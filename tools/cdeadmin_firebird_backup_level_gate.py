#!/usr/bin/env python3
"""Prove native level 256 backup and ordered restore on disposable files."""
import argparse
import json
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_backup_restore_ui_matrix import (
        _configure_client_library, _load_profile, database_connection,
        docker, drop_owned_database, resolve_container, validate_profile)
    from .cdeadmin_firebird_admin_mapping_gate import _create_client
else:
    from cdeadmin_firebird_backup_restore_ui_matrix import (
        _configure_client_library, _load_profile, database_connection,
        docker, drop_owned_database, resolve_container, validate_profile)
    from cdeadmin_firebird_admin_mapping_gate import _create_client
from pgadmin.cdeadmin.security.secrets import SecretLease
from pgadmin.cdeadmin.providers.firebird.provider import ADMINISTRATION


def run(profiles, container, *, through_plan=False):
    import firebird.driver as native
    _configure_client_library(native)
    profile = _load_profile(profiles)
    password = profile.pop('password')
    container = resolve_container(container)
    validate_profile(profile, container)
    root = PurePosixPath(profile['database']).parent
    stem = 'cde_level_' + uuid.uuid4().hex
    primary = str(root / (stem + '.fdb'))
    restored = str(root / (stem + '.restored.fdb'))
    backups = [str(root / (stem + f'.{level}.nbk')) for level in range(257)]
    result = {'complete': False, 'levels_completed': [], 'failures': [],
              'through_visual_plan': through_plan,
              'restored_payload_verified': False, 'history_verified': False,
              'owned_database_paths': [primary, restored],
              'owned_files_removed': [], 'credential_values_exported': False}
    claimed = False
    connection = client = None
    release_ok = True

    def operation(name, options):
        if through_plan:
            request = {'engine_id': 'firebird', 'resource_kind': 'database',
                       'operation_id': name, 'draft': options,
                       '_provider_route': route}
            assert not ADMINISTRATION.validate(request)['errors']
            plan = ADMINISTRATION.plan(request)
            return ADMINISTRATION.apply(client, {
                'provider_payload': plan['provider_payload']
            })['driver_observation']
        return client.run_server_operation(
            {'route': route}, name, primary, options)

    try:
        for path in (primary, restored, *backups):
            docker(container, 'exec', 'test', '!', '-e', path)
        claimed = True
        connection = database_connection(native, profile, password, primary,
                                         create=True)
        assert '5.0.4' in connection.info.firebird_version
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE OWNED_LEVEL_PAYLOAD '
                           '(ID INTEGER PRIMARY KEY, V INTEGER)')
        connection.commit()
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))
        route = {**profile, 'database': primary,
                 'credential_reference_id': 'owned-level-secret',
                 'principal_reference': 'owned-level-principal'}
        for level, backup in enumerate(backups):
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO OWNED_LEVEL_PAYLOAD VALUES (?, ?)',
                               (level, level * 7))
            connection.commit()
            # Native dispatch establishes the engine capability; the optional
            # full visual-plan path also qualifies form admission/compilation.
            observed = operation(
                'backup_physical',
                {'backup_file': backup, 'backup_level': level})
            assert observed['server_completed'] is True
            assert observed['service_release'][
                'service_handle_released'] is True
            result['levels_completed'].append(level)
            if level % 32 == 0:
                print(f'Native backup level {level} completed', flush=True)
        with connection.cursor() as cursor:
            cursor.execute('SELECT RDB$BACKUP_LEVEL FROM RDB$BACKUP_HISTORY '
                           'ORDER BY RDB$BACKUP_ID')
            assert [row[0] for row in cursor.fetchall()] == list(range(257))
        connection.rollback()
        result['history_verified'] = True
        observed = operation(
            'restore_physical',
            {'backup_files': backups, 'restore_database': restored})
        assert observed['server_completed'] is True
        assert observed['service_release']['service_handle_released'] is True
        connection.close()
        connection = database_connection(native, profile, password, restored)
        with connection.cursor() as cursor:
            cursor.execute('SELECT ID, V FROM OWNED_LEVEL_PAYLOAD ORDER BY ID')
            assert cursor.fetchall() == [(i, i * 7) for i in range(257)]
        connection.rollback()
        result['restored_payload_verified'] = True
    except Exception as exc:
        result['failures'].append({'stage': 'native',
                                   'type': type(exc).__name__})
    finally:
        for handle in (connection, client):
            if handle is not None:
                try:
                    handle.close()
                except Exception as exc:
                    release_ok = False
                    result['failures'].append({'stage': 'release',
                                               'type': type(exc).__name__})
        if claimed and release_ok:
            try:
                for path in (restored, primary):
                    found = docker(container, 'exec', 'test', '-e', path,
                                   absent_ok=True)
                    if found.returncode == 0:
                        drop_owned_database(native, profile, password, path)
                        docker(container, 'exec', 'test', '!', '-e', path)
                        result['owned_files_removed'].append(path)
                for path in backups:
                    docker(container, 'exec', 'rm', '-f', path)
                    docker(container, 'exec', 'test', '!', '-e', path)
                    result['owned_files_removed'].append(path)
            except Exception as exc:
                result['failures'].append({'stage': 'cleanup',
                                           'type': type(exc).__name__})
    result['complete'] = (not result['failures'] and
                          result['restored_payload_verified'] and
                          result['history_verified'] and
                          len(result['owned_files_removed']) == 259)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--container', default='cdeadmin-demo-firebird')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--through-plan', action='store_true')
    options = parser.parse_args()
    if options.output.exists():
        raise SystemExit('Choose a new evidence output file')
    result = run(options.profiles, options.container,
                 through_plan=options.through_plan)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    raise SystemExit(0 if result['complete'] else 1)


if __name__ == '__main__':
    main()
