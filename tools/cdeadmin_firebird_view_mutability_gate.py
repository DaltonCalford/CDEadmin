#!/usr/bin/env python3
"""Measure native view mutation boundaries before enabling grid edits."""
import argparse
import json
from pathlib import Path
import secrets
from types import SimpleNamespace
import uuid

if __package__:
    from . import cdeadmin_firebird_views_gate as base
else:
    import cdeadmin_firebird_views_gate as base


def verify_provider_grid(client, route, result, operation='update'):
    from pgadmin.cdeadmin.core import EndpointContext
    from pgadmin.cdeadmin.providers.firebird.provider import (
        FirebirdProvider, PROFILE,
    )
    from pgadmin.cdeadmin.visual_admin.provider import VisualAdminAccessError
    identity = str(uuid.uuid4())
    context = EndpointContext(
        endpoint_id=identity, mode='legacy_native',
        experience_family='firebird', provider_id=PROFILE.provider_id,
        provider_version='0.1.0', profile_id=PROFILE.profile_id,
        profile_version=PROFILE.exact_version,
        runtime_verification_state='verified',
        verified_runtime_family='firebird',
        verified_runtime_version=result['engine_version'],
        target_adapter_id='firebird_wire-client',
        target_adapter_version='owned',
        pool_namespace=str(uuid.uuid4()), session_namespace=str(uuid.uuid4()),
        cache_namespace=str(uuid.uuid4()),
        diagnostic_namespace=str(uuid.uuid4()),
        effective_permissions=frozenset({
            'network', 'secret_read', 'data_read', 'data_write',
            'administer', 'execute'}))
    provider = FirebirdProvider(context, SimpleNamespace(
        require=lambda *_args, **_kwargs: None), client)
    sid = provider.open_session({'route': route})['session_id']
    original = None
    try:
        target = {'resource_kind': 'view', 'resource_id': 'view:VM_SIMPLE',
                  'display_name': 'VM_SIMPLE', 'display_path': ['VM_SIMPLE']}
        request = {'_provider_route': route, 'target_resource': target,
                   'session_id': sid, 'limit': 1}
        page = provider.read_visual_admin_rows(request)
        original = page['rows'][0]['values']
        token = page['rows'][0]['identity_token']
        assert token and page['editable']
        assert operation in page['row_operations']
        plan = provider.plan_visual_admin({
            **request, 'resource_kind': 'view', 'operation_id': operation,
            'draft': {'selector': {'identity_token': token},
                      'concurrency_token': token,
                      **({'changes': {'V': 31}} if operation == 'update' else
                         {'confirmation': 'provider-row-delete'})}})
        assert plan['state'] == 'ready', plan
        if operation == 'delete':
            assert plan['confirmation_required'] is True
            try:
                provider.apply_visual_admin({
                    'session_id': sid, 'plan_id': plan['plan_id'],
                    'plan_digest': plan['plan_digest'], 'confirmed': False})
            except VisualAdminAccessError as exc:
                assert 'requires confirmation' in str(exc)
            else:
                raise AssertionError('Unconfirmed delete executed')
            with provider._sessions[sid].handle.cursor() as cursor:
                cursor.execute('SELECT V FROM VM_SIMPLE WHERE ID = ?',
                               (original['ID'],))
                assert cursor.fetchall() == [(original['V'],)]
            result['unconfirmed_view_delete_denied'] = True
            # An attempted apply consumes the plan even when confirmation
            # is missing. Obtain a new identity and preview, never replay it.
            page = provider.read_visual_admin_rows(request)
            token = page['rows'][0]['identity_token']
            plan = provider.plan_visual_admin({
                **request, 'resource_kind': 'view', 'operation_id': 'delete',
                'draft': {'selector': {'identity_token': token},
                          'concurrency_token': token,
                          'confirmation': 'provider-row-delete'}})
            assert plan['state'] == 'ready'
        receipt = provider.apply_visual_admin({
            'session_id': sid, 'plan_id': plan['plan_id'],
            'plan_digest': plan['plan_digest'], 'confirmed': True})
        assert receipt['provider_result']['staged_in_provider_session']
        result['provider_view_grid_passed'] = True
        if operation == 'delete':
            result['provider_view_delete_passed'] = True
    finally:
        provider.close_session({'session_id': sid})
    observer = client.open_session({'route': route})
    try:
        with observer.cursor() as cursor:
            cursor.execute('SELECT V FROM VM_BASE WHERE ID = ?',
                           (original['ID'],))
            assert cursor.fetchall() == [(original['V'],)]
    finally:
        client.close_session(observer)


def verify_delete_permissions(client, route, result):
    from pgadmin.cdeadmin.providers.firebird.character_metadata import literal
    secret = secrets.token_urlsafe(24)
    reader = base._create_client(SimpleNamespace(
        acquire_secret=lambda *_: base.SecretLease(secret)))
    admin = client.open_session({'route': route})
    checks = result['view_delete_permission_checks'] = []
    try:
        with admin.cursor() as cursor:
            cursor.execute('CREATE USER VM_READER PASSWORD ' + literal(secret))
            cursor.execute('GRANT SELECT ON VM_SIMPLE TO USER VM_READER')
        client.control_transaction(admin, 'commit')
        phases = [
            ('select-only', None, []),
            ('update-only', 'GRANT UPDATE(V) ON VM_SIMPLE TO USER VM_READER',
             ['update']),
            ('both', 'GRANT DELETE ON VM_SIMPLE TO USER VM_READER',
             ['update', 'delete']),
            ('delete-only', 'REVOKE UPDATE(V) ON VM_SIMPLE '
             'FROM USER VM_READER',
             ['delete']),
            ('revoked', 'REVOKE DELETE ON VM_SIMPLE FROM USER VM_READER', []),
        ]
        for phase, command, expected in phases:
            if command:
                with admin.cursor() as cursor:
                    cursor.execute(command)
                client.control_transaction(admin, 'commit')
            user_route = {**route, 'user': 'VM_READER'}
            handle = reader.open_session({'route': user_route})
            try:
                target = {'resource_kind': 'view',
                          'display_path': ['VM_SIMPLE']}
                page = base.ADMINISTRATION.read_rows(reader, {
                    '_provider_route': user_route, 'target_resource': target,
                    'session_id': 'permission-session'}, connection=handle)
                assert page['row_operations'] == expected, (
                    phase, page['row_operations'])
                assert not page['columns'][0]['editable']
                if 'delete' in expected:
                    token = page['rows'][0]['identity_token']
                    plan = base.ADMINISTRATION.plan({
                        '_provider_route': user_route,
                        'resource_kind': 'view', 'operation_id': 'delete',
                        'target_resource': target,
                        'session_id': 'permission-session',
                        'draft': {'selector': {'identity_token': token},
                                  'concurrency_token': token,
                                  'confirmation': 'provider-row-delete'}})
                    receipt = base.ADMINISTRATION.apply(
                        reader, plan, connection=handle)
                    assert receipt['staged_in_provider_session']
                    reader.control_transaction(handle, 'rollback')
                    restored = base.ADMINISTRATION.read_rows(reader, {
                        '_provider_route': user_route,
                        'target_resource': target,
                        'session_id': 'permission-session'}, connection=handle)
                    assert [row['values'] for row in restored['rows']] == [
                        row['values'] for row in page['rows']]
                checks.append({'phase': phase, 'passed': True,
                               'row_operations': expected})
            finally:
                reader.close_session(handle)
    except Exception as exc:
        raise RuntimeError(str(exc).replace(secret, '<redacted>')) from None
    finally:
        client.close_session(admin)


def verify(connection, client, route, password, result):
    import firebird.driver as native
    from pgadmin.cdeadmin.providers.firebird.error_diagnostics import (
        status_codes,
    )
    from pgadmin.cdeadmin.sdk.relational import RelationalClientError
    checks = result['view_mutability_checks'] = []

    def sql(handle, source, parameters=()):
        with handle.cursor() as cursor:
            cursor.execute(source, parameters)
            return cursor.fetchall() if cursor.description else []

    admin = client.open_session({'route': route})
    try:
        for source in (
                'CREATE TABLE VM_BASE (ID INTEGER PRIMARY KEY, V INTEGER)',
                'CREATE TABLE VM_PENDING (ID INTEGER PRIMARY KEY)',
                'CREATE VIEW VM_SIMPLE AS SELECT ID, V FROM VM_BASE',
                'CREATE VIEW VM_CALCULATED AS '
                'SELECT ID, V, V * 2 AS DOUBLED FROM VM_BASE',
                'CREATE VIEW VM_AGGREGATE AS '
                'SELECT ID, SUM(V) AS V FROM VM_BASE GROUP BY ID',
                'CREATE VIEW VM_TRIGGERED AS '
                'SELECT ID, SUM(V) AS V FROM VM_BASE GROUP BY ID',
                'CREATE TRIGGER VM_WRITE FOR VM_TRIGGERED ACTIVE '
                'BEFORE UPDATE AS BEGIN '
                'UPDATE VM_BASE SET V = NEW.V WHERE ID = OLD.ID; END'):
            sql(admin, source)
            client.control_transaction(admin, 'commit')
        cases = (
            ('VM_SIMPLE', 'V', True),
            ('VM_CALCULATED', 'V', True),
            ('VM_CALCULATED', 'DOUBLED', False),
            ('VM_AGGREGATE', 'V', False),
            ('VM_TRIGGERED', 'V', True),
        )
        executions = [(view, column, writable, api)
                      for view, column, writable in cases
                      for api in ('native', 'provider')]
        executions += [(view, column, writable, 'grid')
                       for view, column, writable in cases[:3]]
        for index, (view, column, writable, api) in enumerate(executions):
            for action in ('commit', 'rollback'):
                key = index * 2 + (action == 'commit') + 1
                sql(admin, 'INSERT INTO VM_BASE VALUES (?, 10)', (key,))
                client.control_transaction(admin, 'commit')
                handle = client.open_session({'route': route})
                case = f'{api}:{view}:{column}:{action}'
                try:
                    sql(handle, 'INSERT INTO VM_PENDING VALUES (?)', (key,))
                    transaction = handle.main_transaction.info.id
                    page = base.ADMINISTRATION.read_rows(client, {
                        '_provider_route': route,
                        'target_resource': {'resource_kind': 'view',
                                            'display_path': [view]},
                    }, connection=handle)
                    assert page['editable'] is False
                    assert all(row['identity_token'] is None
                               for row in page['rows'])
                    scoped = base.ADMINISTRATION.read_rows(client, {
                        '_provider_route': route,
                        'target_resource': {'resource_kind': 'view',
                                            'display_path': [view]},
                        'session_id': 'owned-grid',
                    }, connection=handle)
                    assert scoped['editable'] is (
                        view in {'VM_SIMPLE', 'VM_CALCULATED'})
                    if view == 'VM_CALCULATED':
                        assert next(col for col in scoped['columns']
                                    if col['name'] == 'DOUBLED')[
                                        'editable'] is False
                    flags = sql(handle,
                                'SELECT TRIM(RDB$FIELD_NAME), RDB$UPDATE_FLAG '
                                'FROM RDB$RELATION_FIELDS WHERE '
                                'RDB$RELATION_NAME = ? '
                                'ORDER BY RDB$FIELD_POSITION', (view,))
                    codes = []
                    source = (f'UPDATE {view} SET {column} = 30 '
                              'WHERE ID = ?')
                    if api == 'native':
                        try:
                            sql(handle, source, (key,))
                        except native.DatabaseError as exc:
                            codes = list(status_codes(exc))
                            if writable:
                                raise
                    elif api == 'provider':
                        token = client.submit_query(handle, {
                            'source': source, 'parameters': [key]})
                        token.worker.join(20)
                        assert not token.worker.is_alive()
                        observed = client.describe_result(token)
                        assert observed['complete']
                        payload = observed['payload']
                        assert payload['execution_state'] == (
                            'succeeded' if writable else 'failed')
                        if not writable:
                            codes = payload['error']['native_status_codes']
                    else:
                        target = {'resource_kind': 'view',
                                  'display_path': [view]}
                        grid = base.ADMINISTRATION.read_rows(client, {
                            '_provider_route': route,
                            'target_resource': target,
                            'session_id': 'owned-grid',
                        }, connection=handle)
                        assert grid['editable'] is True
                        assert grid['identity_policy'] == (
                            'provider-view-primary-key-and-original-values')
                        row = next(row for row in grid['rows']
                                   if row['values']['ID'] == key)
                        identity = row['identity_token']
                        request = {
                            '_provider_route': route,
                            'resource_kind': 'view', 'operation_id': 'update',
                            'target_resource': target,
                            'session_id': 'owned-grid',
                            'draft': {'selector': {'identity_token': identity},
                                      'concurrency_token': identity,
                                      'changes': {column: 30}}}
                        try:
                            plan = base.ADMINISTRATION.plan(request)
                            assert writable, 'Calculated column admitted'
                            receipt = base.ADMINISTRATION.apply(
                                client, plan, connection=handle)
                            assert receipt['staged_in_provider_session']
                            result['task_evidence'][
                                'visual_admin.view.update'] = {
                                    'live_execution': 'passed',
                                    'statements': [item['source'] for item in
                                                   plan['command_preview'][
                                                       'statements']]}
                        except RelationalClientError as exc:
                            if writable:
                                raise
                            assert 'not admitted for editing' in str(exc)
                    if api != 'grid':
                        assert bool(codes) is not writable
                    if not writable and api != 'grid':
                        expected_code = (335544359 if column == 'DOUBLED'
                                         else 335544362)
                        assert expected_code in codes, codes
                    if view == 'VM_TRIGGERED':
                        # A trigger can admit UPDATE despite zero field flags.
                        assert all(flag == 0 for _name, flag in flags)
                    if column == 'DOUBLED':
                        assert dict(flags)['V'] == 1
                        assert dict(flags)['DOUBLED'] == 0
                    assert handle.main_transaction.info.id == transaction
                    expected = 30 if writable else 10
                    assert sql(handle, 'SELECT V FROM VM_BASE WHERE ID = ?',
                               (key,)) == [(expected,)]
                    observer = client.open_session({'route': route})
                    try:
                        assert sql(observer, 'SELECT V FROM VM_BASE '
                                   'WHERE ID = ?', (key,)) == [(10,)]
                        assert sql(observer, 'SELECT ID FROM VM_PENDING '
                                   'WHERE ID = ?', (key,)) == []
                    finally:
                        client.close_session(observer)
                    client.control_transaction(handle, action)
                    observer = client.open_session({'route': route})
                    try:
                        assert sql(observer, 'SELECT V FROM VM_BASE '
                                   'WHERE ID = ?', (key,)) == [
                                       (expected if action == 'commit'
                                        else 10,)]
                        assert sql(observer, 'SELECT ID FROM VM_PENDING '
                                   'WHERE ID = ?', (key,)) == (
                                       [(key,)] if action == 'commit' else [])
                    finally:
                        client.close_session(observer)
                    checks.append({'case': case, 'passed': True,
                                   'native_writable': writable,
                                   'native_status_codes': codes,
                                   'catalog_update_flags': flags,
                                   'grid_without_session_read_only': True})
                except Exception as exc:
                    checks.append({'case': case, 'passed': False})
                    result['failures'].append({
                        'case': case, 'error_type': type(exc).__name__,
                        'message': str(exc).replace(password, '<redacted>')})
                finally:
                    client.close_session(handle)
        delete_checks = result['view_delete_checks'] = []
        for index, (view, action) in enumerate(
                [(view, action) for view in ('VM_SIMPLE', 'VM_CALCULATED')
                 for action in ('commit', 'rollback')] +
                [('VM_SIMPLE', 'stale'), ('VM_SIMPLE', 'wrong-session')]):
            key = 100 + index
            sql(admin, 'INSERT INTO VM_BASE VALUES (?, 10)', (key,))
            client.control_transaction(admin, 'commit')
            handle = client.open_session({'route': route})
            case = f'{view}:{action}'
            try:
                sql(handle, 'INSERT INTO VM_PENDING VALUES (?)', (key,))
                transaction = handle.main_transaction.info.id
                target = {'resource_kind': 'view', 'display_path': [view]}
                page = base.ADMINISTRATION.read_rows(client, {
                    '_provider_route': route, 'target_resource': target,
                    'session_id': 'owned-delete'}, connection=handle)
                assert 'delete' in page['row_operations']
                token = next(row['identity_token'] for row in page['rows']
                             if row['values']['ID'] == key)
                request = {
                    '_provider_route': route, 'resource_kind': 'view',
                    'target_resource': target, 'operation_id': 'delete',
                    'session_id': ('other' if action == 'wrong-session'
                                   else 'owned-delete'),
                    'draft': {'selector': {'identity_token': token},
                              'concurrency_token': token,
                              'confirmation': 'provider-row-delete'}}
                if action == 'wrong-session':
                    try:
                        base.ADMINISTRATION.plan(request)
                    except RelationalClientError as exc:
                        assert 'another provider session' in str(exc)
                    else:
                        raise AssertionError('Cross-session delete admitted')
                else:
                    plan = base.ADMINISTRATION.plan(request)
                    if action == 'stale':
                        sql(handle, 'UPDATE VM_BASE SET V = 20 WHERE ID = ?',
                            (key,))
                    try:
                        receipt = base.ADMINISTRATION.apply(
                            client, plan, connection=handle)
                        assert action != 'stale', 'Stale delete accepted'
                        assert receipt['staged_in_provider_session']
                        result['task_evidence']['visual_admin.view.delete'] = {
                            'live_execution': 'passed',
                            'statements': [item['source'] for item in
                                           plan['command_preview'][
                                               'statements']]}
                    except RelationalClientError as exc:
                        if action != 'stale':
                            raise
                        assert 'exactly one row' in str(exc)
                assert handle.main_transaction.info.id == transaction
                pending = sql(handle, 'SELECT V FROM VM_BASE WHERE ID = ?',
                              (key,))
                assert pending == ([(20,)] if action == 'stale' else
                                   [(10,)] if action == 'wrong-session'
                                   else [])
                assert sql(handle, 'SELECT ID FROM VM_PENDING WHERE ID = ?',
                           (key,)) == [(key,)]
                observer = client.open_session({'route': route})
                try:
                    assert sql(observer, 'SELECT V FROM VM_BASE WHERE ID = ?',
                               (key,)) == [(10,)]
                finally:
                    client.close_session(observer)
                client.control_transaction(
                    handle, 'commit' if action == 'commit' else 'rollback')
                observer = client.open_session({'route': route})
                try:
                    assert sql(observer, 'SELECT V FROM VM_BASE WHERE ID = ?',
                               (key,)) == ([] if action == 'commit' else
                                           [(10,)])
                    assert sql(observer, 'SELECT ID FROM VM_PENDING '
                               'WHERE ID = ?', (key,)) == (
                                   [(key,)] if action == 'commit' else [])
                finally:
                    client.close_session(observer)
                delete_checks.append({'case': case, 'passed': True})
            except Exception as exc:
                delete_checks.append({'case': case, 'passed': False})
                result['failures'].append({
                    'case': case, 'error_type': type(exc).__name__,
                    'message': str(exc).replace(password, '<redacted>')})
            finally:
                client.close_session(handle)
        verify_delete_permissions(client, route, result)
        if result.get('verify_provider_grid'):
            verify_provider_grid(client, route, result)
            verify_provider_grid(client, route, result, 'delete')
    finally:
        client.close_session(admin)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--bootstrap-contract', action='store_true',
                        help='Run native checks before contract activation')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite evidence')

    def checks(connection, client, route, password, result):
        result['verify_provider_grid'] = not args.bootstrap_contract
        verify(connection, client, route, password, result)

    result = base.run(extra_checks=checks,
                      run_grid_boundaries=not args.bootstrap_contract)
    checks = result.get('view_mutability_checks', [])
    result['complete'] = (result['complete'] and len(checks) == 26 and
                          all(check['passed'] for check in checks) and
                          len(result.get('view_delete_checks', [])) == 6 and
                          all(check['passed'] for check in
                              result['view_delete_checks']) and
                          len(result.get('view_delete_permission_checks', []))
                          == 5 and
                          (args.bootstrap_contract or result.get(
                              'provider_view_grid_passed') is True) and
                          (args.bootstrap_contract or result.get(
                              'provider_view_delete_passed') is True) and
                          (args.bootstrap_contract or result.get(
                              'unconfirmed_view_delete_denied') is True))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
