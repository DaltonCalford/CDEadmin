#!/usr/bin/env python3
"""Qualify native gfix flags on healthy disposable Firebird databases."""

import argparse
import itertools
import json
import os
import re
import secrets
import time
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_external_functions_gate import browser_checks
    from .cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _create_client, _configure_client_library, SecretLease,
    )
else:
    from cdeadmin_firebird_external_functions_gate import browser_checks
    from cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _create_client, _configure_client_library, SecretLease,
    )

from pgadmin.cdeadmin.providers.firebird import repair
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def owned_database_open_files(container, database):
    """Read the selected file's descriptor count inside an owned server."""
    if (not re.fullmatch('[0-9a-f]{64}', container) or
            not re.fullmatch(
                r'/var/lib/firebird/data/owned_repair_linger_\d+\.fdb',
                database)):
        raise ValueError('Linger probe target is not an owned fixture')
    if docker('inspect', '--format',
              '{{index .Config.Labels "cdeadmin-owned-gate"}}',
              container).decode().strip() != OWNER:
        raise ValueError('Linger probe ownership differs')
    script = '''
count=0
for candidate in /proc/[0-9]*/fd/*; do
    target=$(readlink "$candidate" 2>/dev/null) || continue
    if [ "$target" = "$1" ]; then count=$((count + 1)); fi
done
printf '%s\\n' "$count"
'''
    return int(docker('exec', container, 'sh', '-c', script,
                      'owned-linger-probe', database).decode().strip())


def run(image, browser_options=None):
    import firebird.driver as native
    _configure_client_library(native)
    password = secrets.token_urlsafe(24)
    operator_password = secrets.token_urlsafe(24)
    container = client = None
    connections = []
    result = {'complete': False, 'checks': [], 'failures': [],
              'fixture_scope': 'healthy disposable databases only',
              'damaged_database_recovery_qualified': False,
              'owned_container_removed': False,
              'credential_values_exported': False}
    bootstrap = '/var/lib/firebird/data/owned_repair_bootstrap.fdb'

    def failure(case, error):
        result['failures'].append({
            'case': case, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error)),
            'frames': [{'file': Path(frame.filename).name,
                        'line': frame.lineno, 'function': frame.name}
                       for frame in traceback.extract_tb(
                           error.__traceback__)]})

    def connect(path, create=False):
        method = native.create_database if create else native.connect
        handle = method(password=password, **_route_arguments(
            {**route, 'database': path}, native))
        connections.append(handle)
        return handle

    phase = 'create-owned-server'
    try:
        container = docker(
            'run', '--detach', '--name',
            'cdeadmin-repair-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--publish', '127.0.0.1::3050', '--env', 'FIREBIRD_ROOT_PASSWORD',
            '--env', 'FIREBIRD_DATABASE', image,
            env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                     FIREBIRD_DATABASE=bootstrap)).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise ValueError('Owned container identity is invalid')
        route = {'host': '127.0.0.1', 'port': published_port(container),
                 'user': 'SYSDBA', 'timeout': 2,
                 'auth_plugin_list': 'Srp256',
                 'credential_reference_id': 'owned-repair',
                 'principal_reference': 'owned-qa'}
        phase = 'readiness'
        deadline = time.monotonic() + 45
        ready = None
        while time.monotonic() < deadline:
            try:
                ready = connect(bootstrap)
                break
            except native.Error:
                time.sleep(0.25)
        if ready is None:
            raise RuntimeError('Owned Firebird did not become ready')
        with ready.cursor() as cursor:
            cursor.execute(
                "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
                'FROM RDB$DATABASE')
            result['engine_version'] = cursor.fetchone()[0]
        assert result['engine_version'] == '5.0.4'
        ready.close()
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))
        modifiers = [list(items) for count in range(4)
                     for items in itertools.combinations(
                         repair.MODIFIERS, count)]
        cases = [(action, selected, None, no_linger)
                 for action, selected, no_linger in itertools.product(
                     repair.ACTIONS, modifiers, (False, True))]
        cases.extend(('ICU', [], count, no_linger)
                     for count, no_linger in itertools.product(
                         (0, 1, 2, 128, 32767), (False, True)))
        for action, selected, workers, no_linger in cases:
            phase = action + '-' + ('-'.join(selected) or 'default')
            phase += '-no-linger-' + str(no_linger)
            if workers is not None:
                phase += '-workers-' + str(workers)
            check = {'case': phase, 'passed': False}
            result['checks'].append(check)
            path = '/var/lib/firebird/data/owned_repair_' + str(
                len(result['checks'])) + '.fdb'
            draft = {'repair_action': action, 'repair_modifiers': selected,
                     'no_linger': no_linger}
            if workers is not None:
                draft['parallel_workers'] = workers
            request = {'resource_kind': 'database',
                       'operation_id': 'repair_database', 'draft': draft,
                       '_provider_route': {**route, 'database': path}}
            try:
                try:
                    flags = repair.flags(draft)
                except RelationalClientError:
                    assert client.config.administration.validate(
                        request)['errors']
                    check.update(incompatible_modifiers_rejected=True,
                                 passed=True)
                    continue
                setup = connect(path, create=True)
                with setup.cursor() as cursor:
                    cursor.execute(
                        'CREATE TABLE REPAIR_MARKER '
                        '(ID INTEGER PRIMARY KEY, NOTE VARCHAR(32))')
                    setup.commit()
                    cursor.execute("INSERT INTO REPAIR_MARKER "
                                   "VALUES (1, 'preserve healthy data')")
                setup.commit()
                setup.close()
                assert not client.config.administration.validate(
                    request)['errors']
                plan = client.plan_admin_operation(request)
                assert plan['command_preview']['repair_selection'] == (
                    repair.selection(path, draft))
                before = {id(handle) for handle in client._connections}
                response = client.apply_admin_operation(plan)
                assert response['driver_observation'][
                    'repair_selection_requested'] == repair.selection(
                        path, draft)
                assert {id(handle) for handle in client._connections} == before
                observer = connect(path)
                with observer.cursor() as cursor:
                    cursor.execute('SELECT ID, NOTE FROM REPAIR_MARKER')
                    assert cursor.fetchall() == [(1, 'preserve healthy data')]
                    cursor.execute('SELECT MON$SHUTDOWN_MODE, '
                                   'MON$BACKUP_STATE FROM MON$DATABASE')
                    assert cursor.fetchone() == (0, 0)
                observer.close()
                check.update(native_flags=flags,
                             parallel_workers_requested=workers,
                             actual_worker_count_observed=False,
                             healthy_rows_preserved=True,
                             passed=True)
            except Exception as error:
                failure(phase, error)
        administrator = connect(bootstrap)
        with administrator.cursor() as cursor:
            cursor.execute("CREATE USER CDE_REPAIR_OPERATOR PASSWORD '" +
                           operator_password.replace("'", "''") + "'")
        administrator.commit()
        administrator.close()
        for action, mask, no_linger in itertools.product(
                repair.ACTIONS, ('inactive', 'default', 0, 1, 2, 3),
                (False, True)):
            phase = 'authorization-' + action + '-' + str(mask)
            phase += '-no-linger-' + str(no_linger)
            check = {'case': phase, 'passed': False}
            result['checks'].append(check)
            path = '/var/lib/firebird/data/owned_repair_auth_' + str(
                len(result['checks'])) + '.fdb'
            limited = None
            try:
                bits = 3 if mask in ('inactive', 'default') else mask
                privileges = [privilege for bit, privilege in enumerate((
                    'USE_GFIX_UTILITY', 'IGNORE_DB_TRIGGERS'))
                    if bits & (1 << bit)]
                administrator = connect(path, create=True)
                with administrator.cursor() as cursor:
                    cursor.execute('CREATE TABLE AUTH_MARKER (ID INTEGER)')
                    cursor.execute('CREATE ROLE CDE_REPAIR_ROLE' + (
                        ' SET SYSTEM PRIVILEGES TO ' + ', '.join(privileges)
                        if privileges else ''))
                    cursor.execute('GRANT CDE_REPAIR_ROLE TO '
                                   'USER CDE_REPAIR_OPERATOR')
                    administrator.commit()
                    cursor.execute('INSERT INTO AUTH_MARKER VALUES (1)')
                administrator.commit()
                administrator.close()
                limited = _create_client(SimpleNamespace(
                    acquire_secret=lambda *_args: SecretLease(
                        operator_password)))
                draft = {'repair_action': action, 'no_linger': no_linger}
                if action == 'ICU':
                    draft['parallel_workers'] = 2
                if mask not in ('inactive', 'default'):
                    draft['role'] = 'CDE_REPAIR_ROLE'
                request = {
                    'resource_kind': 'database',
                    'operation_id': 'repair_database', 'draft': draft,
                    '_provider_route': {
                        **route, 'database': path,
                        'user': 'CDE_REPAIR_OPERATOR',
                        **({'role': 'CDE_REPAIR_ROLE'}
                           if mask == 'default' else {})}}
                assert not limited.config.administration.validate(
                    request)['errors']
                plan = limited.plan_admin_operation(request)
                try:
                    response = limited.apply_admin_operation(plan)
                except RelationalClientError as error:
                    assert mask not in (3, 'default')
                    check['denial_codes'] = list(status_codes(error))
                    assert 335544788 in check['denial_codes']
                    assert 335545112 in check['denial_codes']
                else:
                    assert mask in (3, 'default')
                    assert plan['command_preview']['repair_selection'][
                        'sql_role'] == 'CDE_REPAIR_ROLE'
                    assert response['driver_observation'][
                        'repair_selection_requested']['sql_role'] == (
                            'CDE_REPAIR_ROLE')
                    check['requested_role_receipt_verified'] = True
                    check['privileged_operation_returned'] = True
                observer = connect(path)
                with observer.cursor() as cursor:
                    cursor.execute('SELECT ID FROM AUTH_MARKER')
                    assert cursor.fetchall() == [(1,)]
                observer.close()
                check.update(role_privileges=privileges,
                             role_active=mask != 'inactive',
                             healthy_rows_preserved=True, passed=True)
            except Exception as error:
                failure(phase, error)
            finally:
                if limited is not None:
                    try:
                        limited.close()
                    except Exception as error:
                        failure('close-limited-repair-client', error)
        for action, provider_owned, no_linger in itertools.product(
                repair.ACTIONS, (False, True), (False, True)):
            phase = 'busy-' + action + '-' + str(provider_owned)
            phase += '-no-linger-' + str(no_linger)
            check = {'case': phase, 'passed': False}
            result['checks'].append(check)
            path = '/var/lib/firebird/data/owned_repair_busy_' + str(
                len(result['checks'])) + '.fdb'
            held = None
            try:
                setup = connect(path, create=True)
                with setup.cursor() as cursor:
                    cursor.execute('CREATE TABLE BUSY_MARKER (ID INTEGER)')
                setup.commit()
                setup.close()
                held = (client._connect({'route': {**route, 'database': path}})
                        if provider_owned else connect(path))
                with held.cursor() as cursor:
                    cursor.execute('INSERT INTO BUSY_MARKER VALUES (1)')
                    cursor.execute('SELECT CURRENT_TRANSACTION '
                                   'FROM RDB$DATABASE')
                    transaction = cursor.fetchone()[0]
                plan = client.plan_admin_operation({
                    'resource_kind': 'database',
                    'operation_id': 'repair_database',
                    'draft': {'repair_action': action, 'no_linger': no_linger,
                              **({'parallel_workers': 2}
                                 if action == 'ICU' else {})},
                    '_provider_route': {**route, 'database': path}})
                try:
                    client.apply_admin_operation(plan)
                except RelationalClientError as error:
                    assert action not in ('ICU', 'KILL_SHADOWS')
                    check['exclusive_access_denial_codes'] = list(
                        status_codes(error))
                    expected_code = (
                        335544510 if action == 'UPGRADE_DB' else
                        335545085 if action in ('CORRUPTION_CHECK', 'REPAIR')
                        else 335544461)
                    assert expected_code in check[
                        'exclusive_access_denial_codes']
                else:
                    assert action in ('ICU', 'KILL_SHADOWS')
                    check['nonexclusive_operation_returned'] = True
                with held.cursor() as cursor:
                    cursor.execute('SELECT CURRENT_TRANSACTION '
                                   'FROM RDB$DATABASE')
                    assert cursor.fetchone()[0] == transaction
                    cursor.execute('SELECT ID FROM BUSY_MARKER')
                    assert cursor.fetchall() == [(1,)]
                held.rollback()
                if provider_owned:
                    receipt = client.close_session(held)
                    assert receipt['connection_released'] is True
                    assert held not in client._connections
                    check['provider_session_release'] = receipt
                else:
                    held.close()
                observer = connect(path)
                with observer.cursor() as cursor:
                    cursor.execute('SELECT ID FROM BUSY_MARKER')
                    assert cursor.fetchall() == []
                observer.close()
                check.update(existing_transaction_preserved=True,
                             explicit_rollback_verified=True, passed=True)
            except Exception as error:
                failure(phase, error)
        linger_cases = [
            (action, 'normal', enabled)
            for action, enabled in itertools.product(
                ('ICU', 'KILL_SHADOWS'), (False, True))]
        linger_cases.extend((action, 'active', True)
                            for action in ('ICU', 'KILL_SHADOWS'))
        linger_cases.extend(('ICU', 'denied', enabled)
                            for enabled in (False, True))
        for action, mode, no_linger in linger_cases:
            phase = 'cache-lifetime-' + action + '-' + mode + '-' + str(
                no_linger)
            check = {'case': phase, 'passed': False}
            result['checks'].append(check)
            path = '/var/lib/firebird/data/owned_repair_linger_' + str(
                len(result['checks'])) + '.fdb'
            held = limited = None
            try:
                setup = connect(path, create=True)
                with setup.cursor() as cursor:
                    cursor.execute('ALTER DATABASE SET LINGER TO 600')
                    cursor.execute('CREATE TABLE LINGER_MARKER (ID INTEGER)')
                    setup.commit()
                    cursor.execute('INSERT INTO LINGER_MARKER VALUES (1)')
                setup.commit()
                setup.close()
                assert owned_database_open_files(container, path) > 0
                selected_client = client
                selected_route = route
                if mode == 'active':
                    held = connect(path)
                    with held.cursor() as cursor:
                        cursor.execute('INSERT INTO LINGER_MARKER VALUES (2)')
                        cursor.execute('SELECT CURRENT_TRANSACTION '
                                       'FROM RDB$DATABASE')
                        transaction = cursor.fetchone()[0]
                elif mode == 'denied':
                    limited = _create_client(SimpleNamespace(
                        acquire_secret=lambda *_args: SecretLease(
                            operator_password)))
                    selected_client = limited
                    selected_route = {**route, 'user': 'CDE_REPAIR_OPERATOR'}
                plan = selected_client.plan_admin_operation({
                    'resource_kind': 'database',
                    'operation_id': 'repair_database',
                    'draft': {'repair_action': action, 'no_linger': no_linger},
                    '_provider_route': {**selected_route, 'database': path}})
                try:
                    selected_client.apply_admin_operation(plan)
                except RelationalClientError as error:
                    assert mode == 'denied'
                    assert 335545112 in status_codes(error)
                    check['denial_codes'] = list(status_codes(error))
                else:
                    assert mode != 'denied'
                if held is not None:
                    assert owned_database_open_files(container, path) > 0
                    with held.cursor() as cursor:
                        cursor.execute('SELECT CURRENT_TRANSACTION '
                                       'FROM RDB$DATABASE')
                        assert cursor.fetchone()[0] == transaction
                        cursor.execute('SELECT ID FROM LINGER_MARKER '
                                       'ORDER BY ID')
                        assert cursor.fetchall() == [(1,), (2,)]
                    held.rollback()
                    held.close()
                    held = None
                    check['active_transaction_preserved'] = True
                deadline = time.monotonic() + 5
                count = owned_database_open_files(container, path)
                expect_closed = no_linger or mode == 'denied'
                while expect_closed and count and time.monotonic() < deadline:
                    time.sleep(0.1)
                    count = owned_database_open_files(container, path)
                check['open_database_files_after_task'] = count
                # Native failed-attachment cleanup also closes an otherwise
                # lingering cache. Absence of -nolinger is not a guarantee
                # that a denied maintenance attachment leaves it resident.
                assert (count == 0) is expect_closed
                observer = connect(path)
                with observer.cursor() as cursor:
                    cursor.execute('SELECT RDB$LINGER FROM RDB$DATABASE')
                    assert cursor.fetchone() == (600,)
                    cursor.execute('SELECT ID FROM LINGER_MARKER')
                    assert cursor.fetchall() == [(1,)]
                observer.close()
                check.update(persisted_linger_unchanged=True,
                             committed_rows_preserved=True, passed=True)
            except Exception as error:
                failure(phase, error)
            finally:
                for handle in (held, limited):
                    if handle is not None:
                        try:
                            handle.close()
                        except Exception as error:
                            failure('close-linger-fixture-handle', error)
                try:
                    # Separate explicit fixture cleanup, never a replay of
                    # the tested repair request or a persistent DDL change.
                    client.run_server_operation(
                        {'route': route}, 'remove_linger', path, {})
                except Exception as error:
                    failure('release-owned-linger-cache', error)
        if browser_options is not None:
            result['browser_checks'] = []
            for scale in browser_options.font_scale or (100, 200, 300):
                phase = 'browser-' + str(scale)
                folder = browser_options.build_root / phase
                folder.mkdir(parents=True, exist_ok=False)
                path = ('/var/lib/firebird/data/owned_repair_browser_' +
                        str(scale) + '.fdb')
                try:
                    setup = connect(path, create=True)
                    with setup.cursor() as cursor:
                        cursor.execute('CREATE TABLE REPAIR_MARKER '
                                       '(ID INTEGER, NOTE VARCHAR(32))')
                        setup.commit()
                        cursor.execute("INSERT INTO REPAIR_MARKER "
                                       "VALUES (1, 'preserve healthy data')")
                        if browser_options.browser_role:
                            for name in ('CDE_OWNED_DEFAULT_ROLE',
                                         'CDE_OWNED_TASK_ROLE'):
                                cursor.execute('CREATE ROLE ' + name)
                                cursor.execute('GRANT ' + name +
                                               ' TO USER SYSDBA')
                    setup.commit()
                    setup.close()
                    selected = SimpleNamespace(**{
                        **vars(browser_options), 'font_scale': [scale]})
                    browser_route = {**route, 'database': path}
                    if browser_options.browser_role:
                        browser_route['role'] = 'CDE_OWNED_DEFAULT_ROLE'
                    result['browser_checks'].extend(browser_checks(
                        selected, browser_route, password,
                        container, folder, gate_kind='repair',
                        fixture_kind='firebird-repair-qualification'))
                except Exception as error:
                    failure(phase, error)
            if not result['browser_checks'] or not all(
                    item['passed'] for item in result['browser_checks']):
                result['failures'].append({'case': 'browser-qualification',
                                           'error_type': 'FailedBrowserGate'})
    except Exception as error:
        failure(phase, error)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as error:
                failure('close-owned-client', error)
        for handle in connections:
            try:
                handle.close()
            except Exception as error:
                failure('close-owned-attachment', error)
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                failure('remove-owned-server', error)
    # Both no-linger states across 56 modifier combinations, 5 ICU worker
    # counts, 42 role cases and 14 pending-transaction cases; plus eight
    # nonzero-linger cache-lifetime/active-session/denial observations.
    result['complete'] = (len(result['checks']) == 242 and
                          not result['failures'] and
                          result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--browser-role', action='store_true',
                        help='Qualify an inherited and overridden task role')
    parser.add_argument('--build-root', type=Path)
    parser.add_argument('--source-config-db', type=Path,
                        default=Path('/var/lib/cdeadmin/cdeadmin.db'))
    parser.add_argument('--desktop-user', default='dalton.calford@gmail.com')
    parser.add_argument('--font-scale', type=int, action='append',
                        choices=(100, 200, 300))
    options = parser.parse_args()
    if options.output.exists():
        parser.error('Use a new evidence file')
    if options.browser:
        if options.build_root is None or options.build_root.exists():
            parser.error('Browser tests require a new --build-root directory')
        options.build_root.mkdir(parents=True, exist_ok=False)
    result = run(options.image, options if options.browser else None)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
