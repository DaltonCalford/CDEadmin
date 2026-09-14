#!/usr/bin/env python3
"""Verify native backup history retention and restored data on owned files."""
import argparse
import json
import subprocess
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_admin_mapping_gate import (
        _create_client, _route_arguments,
    )
else:
    from cdeadmin_firebird_admin_mapping_gate import (
        _create_client, _route_arguments,
    )
from pgadmin.cdeadmin.providers.firebird.provider import (
    ADMINISTRATION, _configure_client_library,
)
from pgadmin.cdeadmin.security.secrets import SecretLease
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def run(profiles, container):
    import firebird.driver as driver
    _configure_client_library(driver)
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    password = route.pop('password')
    root = PurePosixPath(route['database']).parent
    token = uuid.uuid4().hex
    primary = str(root / ('cde_history_' + token + '.fdb'))
    restored = str(root / ('cde_history_restore_' + token + '.fdb'))
    result = {'complete': False, 'cases': [], 'failures': [],
              'owned_paths_removed': [], 'credential_values_exported': False,
              'fixture_database': primary}
    owned = []
    connection = None
    client = None

    def docker(*args):
        completed = subprocess.run(['docker', *args], capture_output=True,
                                   check=False, timeout=45)
        if completed.returncode:
            raise RuntimeError('Owned fixture Docker action failed')
        return completed.stdout

    def claim(path):
        if token not in PurePosixPath(path).name or str(
                PurePosixPath(path).parent) != str(root):
            raise RuntimeError('Owned file identity mismatch')
        docker('exec', container, 'test', '!', '-e', path)
        owned.append(path)

    def connect(path):
        return driver.connect(password=password, **_route_arguments(
            {**route, 'database': path}, driver))

    def history():
        if connection.main_transaction.is_active():
            connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute('SELECT RDB$BACKUP_ID, RDB$GUID, RDB$TIMESTAMP, '
                           'RDB$BACKUP_LEVEL '
                           'FROM RDB$BACKUP_HISTORY ORDER BY RDB$BACKUP_ID')
            rows = cursor.fetchall()
        connection.rollback()
        return rows

    def operation(name, options):
        request = {'engine_id': 'firebird', 'resource_kind': 'database',
                   'operation_id': name, 'draft': options,
                   '_provider_route': service_route}
        checked = ADMINISTRATION.validate(request)
        assert not checked['errors'], checked['errors']
        plan = ADMINISTRATION.plan(request)
        if options.get('database_guid'):
            compiled_guid = plan['provider_payload']['compiled'][
                'options']['database_guid']
            assert compiled_guid.startswith('{')
            assert compiled_guid.endswith('}')
        if options.get('clean_history'):
            assert any('history' in warning for warning in plan['warnings'])
        applied = ADMINISTRATION.apply(client, {
            'provider_payload': plan['provider_payload']})
        observed = applied['driver_observation']
        assert observed['server_completed'] is True
        assert observed['service_release']['service_handle_released'] is True
        return observed

    try:
        # File cleanup is allowed only in the exact container backing this
        # localhost profile. No arbitrary host or user data path is accepted.
        if route['host'] not in {'127.0.0.1', 'localhost'}:
            raise RuntimeError('Owned backup fixture requires localhost')
        ports = docker('port', container, '3050/tcp').decode().splitlines()
        assert '127.0.0.1:' + str(route['port']) in ports
        claim(primary)
        connection = driver.create_database(password=password,
                                            **_route_arguments(
                                                {**route, 'database': primary},
                                                driver))
        assert '5.0.4' in connection.info.firebird_version
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE OWNED_PAYLOAD '
                           '(ID INTEGER PRIMARY KEY, V VARCHAR(60))')
        connection.commit()
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO OWNED_PAYLOAD VALUES (?, ?)',
                           (1, 'Restore proof'))
        connection.commit()
        service_route = {**route, 'database': primary,
                         'credential_reference_id': 'owned-history-secret',
                         'principal_reference': 'owned-history-principal'}
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))
        backups = []
        original_guid = None
        for index, retention in enumerate((None, None, 'ROWS', 'DAYS')):
            path = str(root / f'cde_history_{token}_{index}.nbk')
            claim(path)
            options = {'backup_file': path, 'backup_level': 0,
                       'backup_flags': []}
            if retention:
                options.update(clean_history=True, history_keep_unit=retention,
                               history_keep_value=1)
            observed = operation('backup_physical', options)
            backups.append(path)
            rows = history()
            assert len(rows) == (1, 2, 1, 2)[index]
            if index == 0:
                original_guid = rows[0][1].strip()
            if retention:
                assert observed['history_retention_requested'] == {
                    'unit': retention, 'value': 1,
                    'backup_files_deleted': False}
            for backup in backups:
                docker('exec', container, 'test', '-s', backup)
            result['cases'].append('history-' + str(index))
        before = history()
        assert original_guid not in [row[1].strip() for row in before]
        lost_path = str(root / f'cde_history_{token}_lost_guid.nbk')
        claim(lost_path)
        try:
            operation('backup_physical', {
                'backup_file': lost_path, 'database_guid': original_guid,
                'clean_history': True, 'history_keep_unit': 'ROWS',
                'history_keep_value': 1})
        except RelationalClientError as exc:
            assert 337117261 in exc.gds_codes
            assert history() == before
            result['cases'].append('pruned-guid-native-rejection')
        else:
            raise AssertionError('A removed history GUID was accepted')
        # A bare UUID must still select GUID mode, never atoi's level path.
        latest_guid = before[-1][1].strip()
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO OWNED_PAYLOAD VALUES (?, ?)',
                           (2, 'GUID increment proof'))
        connection.commit()
        increment = str(root / f'cde_history_{token}_guid_increment.nbk')
        claim(increment)
        operation('backup_physical', {
            'backup_file': increment,
            'database_guid': latest_guid.strip('{}').lower(),
            'clean_history': True, 'history_keep_unit': 'DAYS',
            'history_keep_value': 1})
        assert history()[-1][3] is None
        result['cases'].append('bare-guid-increment-native-history')
        claim(restored)
        operation('restore_physical', {
            'backup_files': [backups[-1], increment],
            'restore_database': restored, 'restore_flags': []})
        restored_connection = connect(restored)
        try:
            with restored_connection.cursor() as cursor:
                cursor.execute('SELECT ID, V FROM OWNED_PAYLOAD ORDER BY ID')
                assert cursor.fetchall() == [
                    (1, 'Restore proof'), (2, 'GUID increment proof')]
        finally:
            restored_connection.close()
        result['cases'].append('restored-payload-verified')
    except Exception as exc:
        result['failures'].append({'error_type': type(exc).__name__,
                                   'message': str(exc).replace(
                                       password, '[redacted]')})
    finally:
        released = True
        for handle in (client, connection):
            if handle is not None:
                try:
                    handle.close()
                except Exception as exc:
                    released = False
                    result['failures'].append({
                        'case': 'release', 'error_type': type(exc).__name__})
        # All attachments must be released before deleting owned files.
        if released:
            for path in reversed(owned):
                try:
                    docker('exec', container, 'rm', '-f', path)
                    docker('exec', container, 'test', '!', '-e', path)
                    result['owned_paths_removed'].append(path)
                except Exception as exc:
                    result['failures'].append({
                        'case': 'cleanup', 'path': path,
                        'error_type': type(exc).__name__})
    result['complete'] = (len(result['cases']) == 7 and
                          not result['failures'] and
                          len(result['owned_paths_removed']) == len(owned))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--container', default='cdeadmin-demo-firebird')
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    result = run(options.profiles, options.container)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['complete'] else 1)


if __name__ == '__main__':
    main()
