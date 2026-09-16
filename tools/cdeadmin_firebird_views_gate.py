#!/usr/bin/env python3
"""Qualify native view replacement in a labelled disposable Firebird 5.0.4."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import time
import traceback
from types import SimpleNamespace
import uuid

if __package__:
    from .cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _create_client, _configure_client_library, ADMINISTRATION, SecretLease,
    )
else:
    from cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _create_client, _configure_client_library, ADMINISTRATION, SecretLease,
    )
from pgadmin.cdeadmin.providers.firebird.character_metadata import identifier
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.providers.firebird.provider import _resources
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def run(image='firebirdsql/firebird:5.0.4'):
    import firebird.driver as native
    _configure_client_library(native)
    password = secrets.token_urlsafe(24)
    container = connection = client = None
    result = {'schema': 'cdeadmin.firebird-views.v1', 'complete': False,
              'checks': [], 'failures': [], 'task_evidence': {},
              'check_option_checks': [],
              'view_grid_checks': [],
              'grid_boundary_checks': [],
              'owned_container_removed': False}

    def failure(case, error):
        result['failures'].append({'case': case,
                                   'error_type': type(error).__name__,
                                   'message': str(error).replace(
                                       password, '<redacted>'),
                                   'frames': [{
                                       'function': frame.name,
                                       'line': frame.lineno,
                                   } for frame in traceback.extract_tb(
                                       error.__traceback__)],
                                   'native_status_codes': list(
                                       status_codes(error))})

    def sql(source, parameters=(), handle=None):
        with (handle or connection).cursor() as cursor:
            cursor.execute(source, parameters)
            return cursor.fetchall() if cursor.description else []

    def rollback():
        if connection.main_transaction.is_active():
            connection.rollback()

    def apply(operation, name, value, handle=None, columns=None):
        draft = {'definition': value,
                 'columns': columns if columns is not None else [
                     {'name': 'VALUE'}]}
        draft['name' if operation == 'create_or_alter' else 'confirmation'] = (
            name)
        request = {'resource_kind': 'view', 'operation_id': operation,
                   '_provider_route': route, 'draft': draft,
                   'target_resource': {'resource_kind': 'view',
                                       'display_name': name}}
        assert not ADMINISTRATION.validate(request)['errors']
        plan = ADMINISTRATION.plan(request)
        receipt = ADMINISTRATION.apply(
            client, plan, connection=handle or connection)
        assert receipt['staged_in_provider_session'] is True
        result['task_evidence']['visual_admin.view.' + operation] = {
            'statements': [s['source'] for s in
                           plan['command_preview']['statements']],
            'live_execution': 'passed'}

    def values(name):
        return sql('SELECT * FROM ' + identifier(name))

    def definition(name):
        return sql('SELECT RDB$VIEW_SOURCE FROM RDB$RELATIONS WHERE '
                   'RDB$RELATION_NAME = ?', (name,))[0][0]

    def lifecycle(name):
        q = identifier(name)
        apply('create_or_alter', name, 'SELECT 1 FROM RDB$DATABASE')
        assert 'SELECT 1' in definition(name)
        rollback()
        assert not sql('SELECT 1 FROM RDB$RELATIONS WHERE '
                       'RDB$RELATION_NAME = ?', (name,))
        rollback()
        apply('create_or_alter', name, 'SELECT 1 FROM RDB$DATABASE')
        connection.commit()
        sql('GRANT SELECT ON ' + q + ' TO PUBLIC')
        connection.commit()
        apply('create_or_alter', name, 'SELECT 2 FROM RDB$DATABASE')
        assert 'SELECT 2' in definition(name)
        rollback()
        assert values(name) == [(1,)]
        rollback()
        apply('create_or_alter', name, 'SELECT 2 FROM RDB$DATABASE')
        connection.commit()
        assert sql('SELECT RDB$PRIVILEGE FROM RDB$USER_PRIVILEGES WHERE '
                   "RDB$RELATION_NAME = ? AND RDB$USER = 'PUBLIC'", (name,))
        apply('recreate', name, 'SELECT 3 FROM RDB$DATABASE')
        assert 'SELECT 3' in definition(name)
        rollback()
        assert values(name) == [(2,)]
        rollback()
        apply('recreate', name, 'SELECT 3 FROM RDB$DATABASE')
        connection.commit()
        assert not sql('SELECT RDB$PRIVILEGE FROM RDB$USER_PRIVILEGES WHERE '
                       "RDB$RELATION_NAME = ? AND RDB$USER = 'PUBLIC'",
                       (name,))
        resource = next(item for item in _resources(connection, route)
                        if item['resource_kind'] == 'view' and
                        item['display_name'] == name)
        assert resource['native']['view_columns'] == [{'name': 'VALUE'}]
        assert values(name) == [(3,)]
        rollback()
        result['checks'].append({'case': 'lifecycle-' + name,
                                 'rollback_commit_verified': True,
                                 'grant_semantics_verified': True,
                                 'catalog_columns_verified': True})

    def dependency():
        sql('CREATE VIEW V_DEP AS SELECT * FROM V_BASE')
        connection.commit()
        try:
            apply('recreate', 'V_BASE', 'SELECT 9 FROM RDB$DATABASE')
            connection.commit()
        except Exception as error:
            codes = list(status_codes(error))
            assert 335544630 in codes, codes
            rollback()
            assert values('V_BASE') == values('V_DEP') == [(3,)]
            return {'native_status_codes': codes,
                    'original_and_dependent_preserved': True}
        raise AssertionError('Recreation ignored dependent view')

    def permission():
        from pgadmin.cdeadmin.providers.firebird.character_metadata import (
            literal,
        )
        sql('CREATE USER VIEW_READER PASSWORD ' + literal(password))
        connection.commit()
        reader = native.connect(password=password, **_route_arguments(
            {**route, 'user': 'VIEW_READER'}, native))
        denials = []
        try:
            for operation in ('create_or_alter', 'recreate'):
                try:
                    apply(operation, 'V_BASE', 'SELECT 4 FROM RDB$DATABASE',
                          handle=reader)
                    reader.commit()
                except Exception as error:
                    codes = list(status_codes(error))
                    assert 335544352 in codes, codes
                    denials.append({'operation': operation,
                                    'native_status_codes': codes})
                else:
                    raise AssertionError('Unprivileged mutation admitted')
                finally:
                    if reader.main_transaction.is_active():
                        reader.rollback()
        finally:
            reader.close()
            sql('DROP USER VIEW_READER')
            connection.commit()
        assert values('V_BASE') == [(3,)]
        return {'denials': denials, 'view_unchanged': True}

    def ordered_columns():
        columns = [{'name': 'B'}, {'name': 'A"東京'}] + [
            {'name': 'C' + str(i)} for i in range(2, 12)]
        for operation, value in (('create_or_alter', 1), ('recreate', 2)):
            apply(operation, 'V_ORDER',
                  f'WITH Q AS (SELECT {value} X FROM RDB$DATABASE) '
                  'SELECT ' + ', '.join('X + ' + str(i * 10)
                                        for i in range(12)) + ' FROM Q',
                  columns=columns)
            connection.commit()
            expected = [tuple(value + i * 10 for i in range(12))]
            assert values('V_ORDER') == expected
            resource = next(r for r in _resources(connection, route)
                            if r['resource_kind'] == 'view' and
                            r['display_name'] == 'V_ORDER')
            assert resource['native']['view_columns'] == columns
            ddl = resource['native']['ddl']
            assert '("B", "A""東京", "C2",' in ddl
            rollback()
            sql('DROP VIEW "V_ORDER"')
            connection.commit()
            sql(ddl)
            connection.commit()
            assert values('V_ORDER') == expected
            recreated = next(r for r in _resources(connection, route)
                             if r['resource_kind'] == 'view' and
                             r['display_name'] == 'V_ORDER')
            assert recreated['native']['view_columns'] == columns
            assert recreated['native']['ddl'] == ddl
            rollback()
        return {'ordered_columns_verified': True, 'cte_verified': True,
                'metadata_recreation_roundtrip_verified': True}

    def invalid_query():
        sql('CREATE TABLE PENDING_VIEW_WORK (ID INTEGER)')
        connection.commit()
        sql('INSERT INTO PENDING_VIEW_WORK VALUES (1)')
        try:
            apply('create_or_alter', 'V_BAD', 'SELECT FROM')
        except Exception as error:
            codes = list(status_codes(error))
            assert 335544569 in codes, codes
            assert sql('SELECT ID FROM PENDING_VIEW_WORK') == [(1,)]
            rollback()
            assert sql('SELECT ID FROM PENDING_VIEW_WORK') == []
            return {'native_status_codes': codes,
                    'pending_work_preserved': True, 'rollback_verified': True}
        raise AssertionError('Invalid query admitted')

    def grid_boundary(action):
        from pgadmin.cdeadmin.core import EndpointContext
        from pgadmin.cdeadmin.providers.firebird.provider import (
            FirebirdProvider, PROFILE,
        )
        from pgadmin.cdeadmin.visual_admin.provider import (
            VisualAdminError, VisualAdminExecutionError,
        )
        from pgadmin.cdeadmin.sdk.actual_engine import PilotProviderError
        namespace = str(uuid.uuid4())
        context = EndpointContext(
            endpoint_id=namespace, mode='legacy_native',
            experience_family='firebird', provider_id=PROFILE.provider_id,
            provider_version='0.1.0', profile_id=PROFILE.profile_id,
            profile_version=PROFILE.exact_version,
            runtime_verification_state='verified',
            verified_runtime_family='firebird',
            verified_runtime_version=result['engine_version'],
            target_adapter_id='firebird_wire-client',
            target_adapter_version='owned', pool_namespace=str(uuid.uuid4()),
            session_namespace=str(uuid.uuid4()),
            cache_namespace=str(uuid.uuid4()),
            diagnostic_namespace=str(uuid.uuid4()),
            effective_permissions=frozenset({
                'network', 'secret_read', 'data_read', 'data_write',
                'administer', 'execute'}))
        provider = FirebirdProvider(context, SimpleNamespace(
            require=lambda *_args, **_kwargs: None), client)
        table = 'BOUNDARY_' + action.upper().replace('-', '_')
        sql('CREATE TABLE ' + table +
            ' (ID INTEGER PRIMARY KEY, V INTEGER CHECK (V < 40))')
        connection.commit()
        sql('INSERT INTO ' + table + ' VALUES (1, 10)')
        sql('INSERT INTO ' + table + ' VALUES (2, 10)')
        connection.commit()
        target = {'resource_kind': 'table', 'resource_id': 'table:' + table,
                  'display_name': table, 'display_path': [table]}
        session_id = provider.open_session({'route': route})['session_id']
        request = {'_provider_route': route, 'target_resource': target,
                   'session_id': session_id, 'limit': 1}
        try:
            page = provider.read_visual_admin_rows(request)
            spare = provider.read_visual_admin_rows(request)
            token = page['rows'][0]['identity_token']
            plan = provider.plan_visual_admin({
                **request, 'resource_kind': 'table', 'operation_id': 'update',
                'draft': {'selector': {'identity_token': token},
                          'changes': {'V': 99}, 'concurrency_token': token}})
            assert plan['state'] == 'ready'
            handle = provider._sessions[session_id].handle
            sql('UPDATE ' + table + ' SET V = 20 WHERE ID = 1', handle=handle)
            if action == 'close':
                provider.close_session({'session_id': session_id})
            elif action.startswith('admin-'):
                current = provider.read_visual_admin_rows(request)
                current_token = current['rows'][0]['identity_token']
                selected = provider.plan_visual_admin({
                    **request, 'resource_kind': 'table',
                    'operation_id': 'update', 'draft': {
                        'selector': {'identity_token': current_token},
                        'changes': {'V': 50 if action == 'admin-fail' else 30},
                        'concurrency_token': current_token}})
                assert selected['state'] == 'ready'
                try:
                    applied = provider.apply_visual_admin({
                        'session_id': session_id,
                        'plan_id': selected['plan_id'],
                        'plan_digest': selected['plan_digest'],
                        'confirmed': True})
                except VisualAdminExecutionError as error:
                    if action != 'admin-fail':
                        raise
                    assert 335544558 in error.operation['native_status_codes']
                else:
                    assert action != 'admin-fail', 'Constraint not enforced'
                    assert applied['provider_result'][
                        'staged_in_provider_session'] is True
                assert selected['plan_id'] not in provider._visual_admin._plans
                assert sql('SELECT V FROM ' + table + ' WHERE ID = 1',
                           handle=handle) == [
                               (20 if action == 'admin-fail' else 30,)]
            elif action.startswith('sql-'):
                source = {
                    'sql-select': 'SELECT V FROM ' + table,
                    'sql-update': ('UPDATE ' + table +
                                   ' SET V = 30 WHERE ID = 1'),
                    'sql-commit': 'COMMIT',
                    'sql-rollback': 'ROLLBACK',
                    'sql-invalid': 'SELECT FROM',
                }[action]
                operation = provider.execute({
                    'session_id': session_id, 'source': source})
                deadline = time.monotonic() + 15
                while True:
                    observed = provider.describe_result({
                        'operation_id': operation['operation_id']})
                    if observed['complete']:
                        break
                    if time.monotonic() >= deadline:
                        raise AssertionError('Native query did not finish')
                    time.sleep(0.02)
                observation = next(iter(observed['extensions'].values()))[
                    'payload']
                assert observation['execution_state'] == (
                    'failed' if action == 'sql-invalid' else 'succeeded'), (
                        observation)
                expected_value = {
                    'sql-select': 20, 'sql-update': 30, 'sql-commit': 20,
                    'sql-rollback': 10, 'sql-invalid': 20,
                }[action]
                assert sql('SELECT V FROM ' + table + ' WHERE ID = 1',
                           handle=handle) == [(expected_value,)]
            else:
                provider.control_transaction({
                    'session_id': session_id, 'action': action})
            assert sql('SELECT V FROM ' + table + ' WHERE ID = 1') == [
                (20 if action in {'commit', 'sql-commit'} else 10,)]
            rollback()
            try:
                provider.apply_visual_admin({
                    'session_id': session_id, 'plan_id': plan['plan_id'],
                    'plan_digest': plan['plan_digest'], 'confirmed': True})
            except (VisualAdminError, PilotProviderError):
                pass
            else:
                raise AssertionError('Old plan survived native boundary')
            spare_token = spare['rows'][0]['identity_token']
            assert spare_token not in ADMINISTRATION._row_identities
            assert page['continuation'] not in (
                ADMINISTRATION._row_continuations)
            if action != 'close':
                fresh = provider.read_visual_admin_rows(request)
                assert fresh['rows'][0]['identity_token']
            else:
                old_session = session_id
                session_id = provider.open_session({'route': route})[
                    'session_id']
                assert session_id != old_session
                fresh = provider.read_visual_admin_rows({
                    **request, 'session_id': session_id})
                assert fresh['rows'][0]['identity_token']
                try:
                    provider.apply_visual_admin({
                        'session_id': session_id, 'plan_id': plan['plan_id'],
                        'plan_digest': plan['plan_digest'], 'confirmed': True})
                except VisualAdminError:
                    pass
                else:
                    raise AssertionError('Old plan survived reconnection')
        finally:
            if session_id in provider._sessions:
                provider.close_session({'session_id': session_id})
        return {'action': action, 'old_plan_rejected': True,
                'tokens_and_pages_invalidated': True,
                'native_finality_observed': True}

    def view_grid(kind):
        table = 'GRID_BASE_' + kind.upper()
        view = 'GRID_VIEW_' + kind.upper()
        sql('CREATE TABLE ' + table + ' (ID INTEGER PRIMARY KEY, V INTEGER)')
        connection.commit()
        sql('INSERT INTO ' + table + ' VALUES (1, 10)')
        connection.commit()
        query = ('SELECT ID, V FROM ' + table if kind == 'simple' else
                 'SELECT ID, SUM(V) AS V FROM ' + table + ' GROUP BY ID')
        apply('create_or_alter', view, query,
              columns=[{'name': 'ID'}, {'name': 'V'}])
        connection.commit()
        # A pending change must survive a grid read without being committed.
        sql('UPDATE ' + table + ' SET V = 20 WHERE ID = 1')
        page = ADMINISTRATION.read_rows(client, {
            '_provider_route': route, 'target_resource': {
                'resource_kind': 'view', 'display_name': view,
                'display_path': [view]}, 'limit': 10}, connection=connection)
        assert page['editable'] is False
        assert page['identity_policy'] == 'read-only-view'
        assert page['rows'][0]['values'] == {'ID': 1, 'V': 20}
        assert all(row['identity_token'] is None for row in page['rows'])
        assert all(column['editable'] is False for column in page['columns'])
        table_target = {'resource_kind': 'table', 'display_name': table,
                        'display_path': [table]}
        for planned_session in ('session-b', None, 'session-a'):
            table_page = ADMINISTRATION.read_rows(client, {
                '_provider_route': route, 'target_resource': table_target,
                'session_id': 'session-a'}, connection=connection)
            token = table_page['rows'][0]['identity_token']
            request = {
                '_provider_route': route, 'target_resource': table_target,
                'resource_kind': 'table', 'operation_id': 'update',
                'session_id': planned_session,
                'draft': {'selector': {'identity_token': token},
                          'changes': {'V': 25}, 'concurrency_token': token}}
            if planned_session != 'session-a':
                try:
                    ADMINISTRATION.plan(request)
                except RelationalClientError as error:
                    assert 'another provider session' in str(error)
                else:
                    raise AssertionError('Cross-session row identity admitted')
                assert sql('SELECT V FROM ' + table) == [(20,)]
            else:
                receipt = ADMINISTRATION.apply(
                    client, ADMINISTRATION.plan(request),
                    connection=connection)
                assert receipt['staged_in_provider_session'] is True
                assert sql('SELECT V FROM ' + table) == [(25,)]
        rollback()
        assert sql('SELECT V FROM ' + table) == [(10,)]
        codes = []
        if kind == 'simple':
            sql('UPDATE ' + view + ' SET V = 30 WHERE ID = 1')
            assert sql('SELECT V FROM ' + table) == [(30,)]
        else:
            try:
                sql('UPDATE ' + view + ' SET V = 30 WHERE ID = 1')
            except native.Error as error:
                codes = list(status_codes(error))
                assert 335544362 in codes, codes
            else:
                raise AssertionError('Aggregate view unexpectedly writable')
        rollback()
        assert sql('SELECT V FROM ' + table) == [(10,)]
        return {'kind': kind, 'grid_read_only_verified': True,
                'row_session_boundary_verified': True,
                'no_identity_tokens': True, 'pending_work_preserved': True,
                'native_update_allowed': kind == 'simple',
                'native_denial_codes': codes}

    def check_option(mode):
        suffix = mode.upper().replace('-', '_')
        table = 'CO_BASE_' + suffix
        view = 'CO_VIEW_' + suffix
        sql('CREATE TABLE ' + table + ' (ID INTEGER PRIMARY KEY, V INTEGER)')
        connection.commit()
        query = 'SELECT ID, V FROM ' + table + ' WHERE V > 0'
        names = [{'name': 'ID'}, {'name': 'V'}]
        if mode in {'alter', 'recreate'}:
            apply('create_or_alter', view, query, columns=names)
            connection.commit()
        apply('recreate' if mode == 'recreate' else 'create_or_alter',
              view, query + ' WITH CHECK OPTION', columns=names)
        connection.commit()
        resource = next(r for r in _resources(connection, route)
                        if r['resource_kind'] == 'view' and
                        r['display_name'] == view)
        ddl = resource['native']['ddl']
        assert 'WITH CHECK OPTION' in ddl
        rollback()
        if mode == 'export':
            sql('DROP VIEW ' + view)
            connection.commit()
            sql(ddl)
            connection.commit()
        denials = []
        sql('INSERT INTO ' + view + ' VALUES (1, 10)')
        for mutation in ('INSERT INTO ' + view + ' VALUES (2, -1)',
                         'UPDATE ' + view + ' SET V = -1 WHERE ID = 1'):
            try:
                sql(mutation)
            except native.Error as error:
                codes = list(status_codes(error))
                assert 335544558 in codes, codes
                denials.append(codes)
            else:
                raise AssertionError('Native CHECK OPTION was not enforced')
            assert sql('SELECT ID, V FROM ' + table) == [(1, 10)]
        rollback()
        assert sql('SELECT ID FROM ' + table) == []
        sql('INSERT INTO ' + view + ' VALUES (1, 10)')
        connection.commit()
        sql('UPDATE ' + view + ' SET V = 20 WHERE ID = 1')
        connection.commit()
        assert sql('SELECT ID, V FROM ' + table) == [(1, 20)]
        sql('DELETE FROM ' + view + ' WHERE ID = 1')
        rollback()
        assert sql('SELECT ID, V FROM ' + table) == [(1, 20)]
        sql('DELETE FROM ' + view + ' WHERE ID = 1')
        connection.commit()
        assert sql('SELECT ID FROM ' + table) == []
        rollback()
        # The equivalent unchecked view permits a write outside its filter;
        # the provider must not invent client-side predicate enforcement.
        apply('create_or_alter', view, query, columns=names)
        connection.commit()
        sql('INSERT INTO ' + view + ' VALUES (2, -1)')
        assert sql('SELECT ID, V FROM ' + table) == [(2, -1)]
        assert sql('SELECT ID FROM ' + view) == []
        rollback()
        return {'mode': mode, 'native_denials': denials,
                'pending_work_preserved': True,
                'rollback_commit_verified': True,
                'insert_update_delete_verified': True,
                'check_option_in_export': True,
                'unchecked_control_verified': True}

    try:
        container = docker(
            'create', '--name', 'cdeadmin-views-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER, '--memory', '512m',
            '--publish', '127.0.0.1::3050', '--env', 'FIREBIRD_ROOT_PASSWORD',
            '--env', 'FIREBIRD_DATABASE', image,
            env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                     FIREBIRD_DATABASE='owned_views.fdb')).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise RuntimeError('Invalid owned container identity')
        docker('start', container)
        route = {'host': '127.0.0.1', 'port': published_port(container),
                 'user': 'SYSDBA',
                 'database': '/var/lib/firebird/data/owned_views.fdb',
                 'timeout': 2, 'auth_plugin_list': 'Srp256',
                 'credential_reference_id': 'owned-view-secret',
                 'principal_reference': 'owned-view-principal'}
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                connection = native.connect(
                    password=password, **_route_arguments(route, native))
                break
            except native.Error:
                time.sleep(0.25)
        if connection is None:
            raise RuntimeError('Owned server readiness deadline exceeded')
        result['engine_version'] = sql(
            "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
            'FROM RDB$DATABASE')[0][0]
        assert result['engine_version'] == '5.0.4'
        rollback()
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))
        for name in ('V_BASE', 'V"東京'):
            try:
                lifecycle(name)
            except Exception as error:
                failure('lifecycle-' + name, error)
            finally:
                rollback()
        for label, callback in (
                ('dependency-denial', dependency),
                ('permission-denials', permission),
                ('ordered-columns-cte', ordered_columns),
                ('invalid-query-pending-work', invalid_query)):
            try:
                result['checks'].append({'case': label, **callback()})
            except Exception as error:
                failure(label, error)
            finally:
                rollback()
        for mode in ('create', 'alter', 'recreate', 'export'):
            try:
                result['check_option_checks'].append(check_option(mode))
            except Exception as error:
                failure('check-option-' + mode, error)
            finally:
                rollback()
        for kind in ('simple', 'aggregate'):
            try:
                result['view_grid_checks'].append(view_grid(kind))
            except Exception as error:
                failure('view-grid-' + kind, error)
            finally:
                rollback()
        for action in ('commit', 'rollback', 'close', 'sql-select',
                       'sql-update', 'sql-commit', 'sql-rollback',
                       'sql-invalid', 'admin-update', 'admin-fail'):
            try:
                result['grid_boundary_checks'].append(grid_boundary(action))
            except Exception as error:
                failure('grid-boundary-' + action, error)
            finally:
                rollback()
    except Exception as error:
        failure('gate', error)
    finally:
        if connection is not None:
            try:
                rollback()
                connection.close()
            except Exception as error:
                failure('close-attachment', error)
        if client is not None:
            try:
                client.close()
            except Exception as error:
                failure('close-provider', error)
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                failure('remove-owned-container', error)
    result['complete'] = (len(result['checks']) == 6 and
                          len(result['check_option_checks']) == 4 and
                          len(result['view_grid_checks']) == 2 and
                          len(result['grid_boundary_checks']) == 10 and
                          not result['failures'] and
                          result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise SystemExit('Use a new evidence file')
    result = run()
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'passed': len(result['checks']),
                      'check_option_passed': len(
                          result['check_option_checks']),
                      'view_grid_passed': len(result['view_grid_checks']),
                      'grid_boundaries_passed': len(
                          result['grid_boundary_checks']),
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
