#!/usr/bin/env python3
"""Qualify first-file shadow recovery in an isolated, owned Firebird server."""

import argparse
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
    from .cdeadmin_firebird_external_functions_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _configure_client_library, browser_checks,
    )
else:
    from cdeadmin_firebird_external_functions_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _configure_client_library, browser_checks,
    )
from pgadmin.cdeadmin.providers.firebird import shadow_activation, shadows
from pgadmin.cdeadmin.providers.firebird.provider import (
    _server_arguments, _service_lines, connect_service,
    _create_client,
)
from pgadmin.cdeadmin.security.secrets import SecretLease
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def run(image, build_root, browser_options=None):
    import firebird.driver as native
    import firebird.driver.core as core
    _configure_client_library(native)
    build_root.mkdir(parents=True, exist_ok=False)
    password = secrets.token_urlsafe(24)
    container = connection = server = None
    result = {'schema': 'cdeadmin.firebird-shadow-activation.v1',
              'complete': False, 'checks': [], 'failures': [],
              'credential_values_exported': False,
              'owned_container_removed': False}

    def failure(label, error):
        result['failures'].append({
            'case': label, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error)),
            'frames': [{'file': Path(frame.filename).name,
                        'line': frame.lineno, 'function': frame.name}
                       for frame in traceback.extract_tb(
                           error.__traceback__)]})

    def sql(statement):
        with connection.cursor() as cursor:
            cursor.execute(statement)
            rows = cursor.fetchall() if cursor.description else []
        connection.commit()
        return rows

    def header(filename):
        lines, truncated = _service_lines(
            lambda output: server.database.get_statistics(
                database=filename, flags=native.SrvStatFlag.HDR_PAGES,
                callback=output))
        assert truncated is False
        return lines

    def service_operation(operation, filename, options, user='SYSDBA',
                          secret=None):
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(secret or password)))
        try:
            return client.run_server_operation({'route': {
                **route, 'user': user,
                'credential_reference_id': 'owned-recovery-secret',
                'principal_reference': 'owned-recovery-principal'}},
                operation, filename, options)
        finally:
            client.close()

    def activate(filename, user='SYSDBA', secret=None, role=None):
        return service_operation('activate_shadow', filename, {
            'shadow_filename': filename, 'confirmation': filename,
            'original_isolated': True, 'role': role}, user, secret)

    def reject_target(filename):
        try:
            activate(filename)
        except RelationalClientError:
            return
        raise AssertionError('A non-shadow header was admitted for recovery')

    phase = 'create-owned-server'
    try:
        bootstrap = '/var/lib/firebird/data/owned_activation_bootstrap.fdb'
        container = docker(
            'create', '--name', 'cdeadmin-activation-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--publish', '127.0.0.1::3050', '--env', 'FIREBIRD_ROOT_PASSWORD',
            '--env', 'FIREBIRD_DATABASE', image,
            env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                     FIREBIRD_DATABASE=bootstrap)).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise RuntimeError('Invalid owned container identity')
        result['container_id'] = container
        docker('start', container)
        route = {'host': '127.0.0.1', 'port': published_port(container),
                 'user': 'SYSDBA', 'database': bootstrap, 'timeout': 2,
                 'auth_plugin_list': 'Srp256'}
        phase = 'readiness'
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                connection = native.connect(
                    password=password, **_route_arguments(route, native))
                break
            except native.Error:
                time.sleep(0.25)
        if connection is None:
            raise RuntimeError('Owned Firebird did not become ready')
        result['engine_version'] = sql(
            "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
            'FROM RDB$DATABASE')[0][0]
        assert result['engine_version'] == '5.0.4'
        connection.close()
        connection = None
        server = connect_service(
            native, core, password=password,
            **_server_arguments(route, native))
        for index, (mode, multiple, missing) in enumerate(
                (mode, multiple, missing) for mode in ('AUTO', 'MANUAL')
                for multiple in (False, True) for missing in (False, True)):
            label = f'{mode}-{multiple}-{missing}'
            database = f'/var/lib/firebird/data/owned_activation_{index}.fdb'
            shadow = database + "_影's.shd"
            try:
                connection = native.create_database(
                    password=password, **_route_arguments(
                        {**route, 'database': database}, native))
                sql('CREATE TABLE RECOVERY_MARKER (ID INTEGER)')
                sql('INSERT INTO RECOVERY_MARKER VALUES (1)')
                for statement in shadows.compile_operation('create', {
                    'number': 1, 'mode': mode, 'filename': shadow,
                    **({'length': 256, 'secondary_files': [
                        {'filename': shadow + '.second', 'start': 256}]}
                       if multiple else {}),
                }):
                    sql(statement)
                sql('INSERT INTO RECOVERY_MARKER VALUES (2)')
                connection.close()
                connection = None
                reject_target(database)
                before = header(shadow)
                classification = shadow_activation.verify_header(before)
                fingerprint = docker('exec', container, 'sha256sum',
                                     database).split()[0]
                retained = database
                if missing:
                    retained = database + '.isolated'
                    docker('exec', container, 'mv', '--', database, retained)
                receipt = activate(shadow)
                assert receipt['server_completed'] is True
                assert receipt['shadow_header_verification'] == classification
                reject_target(shadow)
                connection = native.connect(
                    password=password, **_route_arguments(
                        {**route, 'database': shadow}, native))
                assert sql('SELECT ID FROM RECOVERY_MARKER ORDER BY ID') == (
                    [(1,), (2,)])
                sql('INSERT INTO RECOVERY_MARKER VALUES (3)')
                assert sql('SELECT COUNT(*) FROM RECOVERY_MARKER') == [(3,)]
                connection.close()
                connection = None
                assert docker('exec', container, 'sha256sum',
                              retained).split()[0] == fingerprint
                result['checks'].append({
                    'case': label, 'native_header': classification,
                    'primary_target_rejected': True,
                    'already_activated_target_rejected': True,
                    'recovered_rows_verified': True,
                    'new_independent_write_verified': True,
                    'original_file_unchanged': True,
                    'original_path_unavailable': missing})
            except Exception as error:
                failure(label, error)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception as error:
                        failure(label + '-close', error)
                    connection = None
        result['authorization_checks'] = []
        for index, privileges in enumerate((
                (), ('USE_GSTAT_UTILITY',), ('USE_GFIX_UTILITY',),
                ('USE_GFIX_UTILITY', 'IGNORE_DB_TRIGGERS'))):
            label = 'authorization-' + str(index)
            operator = None
            try:
                database = ('/var/lib/firebird/data/'
                            f'owned_activation_auth_{index}.fdb')
                shadow = database + '.shd'
                username = 'OWNED_RECOVERY_' + str(index)
                user_password = secrets.token_urlsafe(24)
                connection = native.create_database(
                    password=password, **_route_arguments(
                        {**route, 'database': database}, native))
                sql('CREATE USER ' + username + " PASSWORD '" +
                    user_password.replace("'", "''") + "' USING PLUGIN Srp")
                sql('CREATE ROLE RECOVERY_OPERATOR' + (
                    ' SET SYSTEM PRIVILEGES TO ' + ', '.join(privileges)
                    if privileges else ''))
                sql('GRANT RECOVERY_OPERATOR TO USER ' + username)
                sql('CREATE TABLE RECOVERY_MARKER (ID INTEGER)')
                sql('INSERT INTO RECOVERY_MARKER VALUES (1)')
                for statement in shadows.compile_operation('create', {
                        'number': 1, 'mode': 'AUTO', 'filename': shadow}):
                    sql(statement)
                connection.close()
                connection = None
                retained = database + '.isolated'
                docker('exec', container, 'mv', '--', database, retained)
                fingerprint = docker('exec', container, 'sha256sum',
                                     retained).split()[0]
                admitted = len(privileges) == 2
                denied_codes = []
                denied_activation_observed = False
                try:
                    receipt = activate(shadow, username, user_password,
                                       'RECOVERY_OPERATOR')
                except (native.Error, RelationalClientError) as error:
                    if admitted:
                        diagnostic = {
                            'case': label,
                            'native_status_codes': list(status_codes(error)),
                            'message': str(error).replace(
                                password, '[redacted]').replace(
                                    user_password, '[redacted]')}
                        result.setdefault('authorization_diagnostics',
                                          []).append(diagnostic)
                    assert not admitted
                    denied_codes = list(status_codes(error))
                    assert denied_codes
                    # Native 5.0.4 initializes/activates shadows before its
                    # utility-privilege checks. Qualify that dangerous native
                    # outcome explicitly; a rejection does not mean rollback.
                    try:
                        shadow_activation.verify_header(header(shadow))
                    except RelationalClientError as header_error:
                        assert str(header_error) == (
                            'The native header does not identify an active '
                            'shadow')
                        denied_activation_observed = True
                    assert denied_activation_observed
                    assert {335544788, 335545112}.issubset(denied_codes)
                    connection = native.connect(
                        password=password, **_route_arguments(
                            {**route, 'database': shadow}, native))
                    assert sql('SELECT ID FROM RECOVERY_MARKER') == [(1,)]
                else:
                    assert admitted and receipt['server_completed'] is True
                    reject_target(shadow)
                    connection = native.connect(
                        password=password, **_route_arguments(
                            {**route, 'database': shadow}, native))
                    assert sql('SELECT ID FROM RECOVERY_MARKER') == [(1,)]
                    # Qualify the shared service-role correction against
                    # three native utility families, not only activation.
                    sql('ALTER ROLE RECOVERY_OPERATOR SET SYSTEM PRIVILEGES '
                        'TO USE_GFIX_UTILITY, IGNORE_DB_TRIGGERS, '
                        'USE_GSTAT_UTILITY, USE_GBAK_UTILITY, '
                        'CHANGE_HEADER_SETTINGS')
                    sql('GRANT SELECT ON RECOVERY_MARKER '
                        'TO ROLE RECOVERY_OPERATOR')
                    backup = shadow + '.fbk'
                    service_checks = result['role_service_families'] = []
                    for task, task_options in (
                            ('database_statistics', {
                                'statistics_flags': ['DATA_PAGES']}),
                            ('set_sweep_interval', {'sweep_interval': 12345}),
                            ('backup_logical', {'backup_file': backup})):
                        receipt = service_operation(task, shadow, {
                            **task_options, 'role': 'RECOVERY_OPERATOR'},
                            username, user_password)
                        assert receipt['server_completed'] is True
                        assert receipt['service_release'][
                            'service_handle_released'] is True
                        if task == 'database_statistics':
                            assert any('RECOVERY_MARKER' in line for line
                                       in receipt['output'])
                        elif task == 'set_sweep_interval':
                            assert connection.info.sweep_interval == 12345
                        else:
                            assert docker('exec', container, 'test', '-s',
                                          backup) == b''
                        service_checks.append(task)
                assert docker('exec', container, 'sha256sum',
                              retained).split()[0] == fingerprint
                result['authorization_checks'].append({
                    'case': label, 'system_privileges': list(privileges),
                    'admitted': admitted, 'denial_codes': denied_codes,
                    'original_file_unchanged': True,
                    'native_denial_activated_shadow':
                        denied_activation_observed})
            except Exception as error:
                failure(label, error)
            finally:
                for resource in (connection, operator):
                    if resource is not None:
                        try:
                            resource.close()
                        except Exception as error:
                            failure(label + '-close', error)
                connection = None
        if browser_options is not None:
            phase = 'browser-recovery'
            result['browser_checks'] = []
            for scale in browser_options.font_scale or (100, 200, 300):
                database = ('/var/lib/firebird/data/'
                            f'owned_activation_browser_{scale}.fdb')
                shadow = database + "_影's.shd"
                browser_route = {**route, 'database': database,
                                 'shadow_filename': shadow}
                connection = native.create_database(
                    password=password,
                    **_route_arguments(browser_route, native))
                sql('CREATE TABLE RECOVERY_MARKER (ID INTEGER)')
                sql('INSERT INTO RECOVERY_MARKER VALUES (1)')
                for statement in shadows.compile_operation('create', {
                        'number': 1, 'mode': 'AUTO', 'filename': shadow}):
                    sql(statement)
                sql('INSERT INTO RECOVERY_MARKER VALUES (2)')
                connection.close()
                connection = None
                retained = database + '.isolated'
                docker('exec', container, 'mv', '--', database, retained)
                fingerprint = docker('exec', container, 'sha256sum',
                                     retained).split()[0]
                selected_options = SimpleNamespace(
                    **{**vars(browser_options), 'font_scale': [scale]})
                checks = browser_checks(
                    selected_options, browser_route, password, container,
                    build_root, gate_kind='shadow-activation',
                    fixture_kind='firebird-shadow-activation-qualification')
                unchanged = docker('exec', container, 'sha256sum',
                                   retained).split()[0] == fingerprint
                for check in checks:
                    check['isolated_original_unchanged'] = unchanged
                    check['passed'] = check['passed'] and unchanged
                result['browser_checks'].extend(checks)
            if not all(item['passed'] for item in result['browser_checks']):
                result['failures'].append({
                    'case': 'browser-recovery',
                    'error_type': 'BrowserGateFailure'})
    except Exception as error:
        failure(phase, error)
    finally:
        for label, resource in (('connection', connection),
                                ('server', server)):
            if resource is not None:
                try:
                    resource.close()
                except Exception as error:
                    failure('close-' + label, error)
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                failure('remove-owned-container', error)
    result['complete'] = (len(result['checks']) == 8 and
                          len(result.get('authorization_checks', [])) == 4 and
                          not result['failures'] and
                          result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--build-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--source-config-db', type=Path,
                        default=Path('/var/lib/cdeadmin/cdeadmin.db'))
    parser.add_argument('--desktop-user', default='dalton.calford@gmail.com')
    parser.add_argument('--font-scale', type=int, action='append',
                        choices=(100, 200, 300))
    options = parser.parse_args()
    if options.output.exists():
        raise SystemExit('Use a new evidence file')
    result = run(options.image, options.build_root,
                 options if options.browser else None)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    raise SystemExit(0 if result['complete'] else 1)


if __name__ == '__main__':
    main()
