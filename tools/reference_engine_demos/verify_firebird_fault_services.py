#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Verify Firebird shadow activation and nbackup fixup fixtures.

Both operations require a deliberately abnormal database state and therefore
do not belong in the normal service-form sequence.  Every file created here
uses a unique server-side name and is removed before exit.  The packaged demo
database is never opened for mutation.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=53050)
    parser.add_argument('--database-root', required=True)
    parser.add_argument('--user', default='SYSDBA')
    parser.add_argument(
        '--password-env', default='CDEADMIN_FIREBIRD_DEMO_PASSWORD'
    )
    parser.add_argument('--client-library', type=Path, required=True)
    parser.add_argument('--container', required=True)
    parser.add_argument('--output', type=Path, required=True)
    return parser.parse_args(argv)


def _container_command(container, *arguments):
    completed = subprocess.run(
        ['docker', 'exec', container, *arguments],
        check=False, capture_output=True, text=True,
    )
    if completed.returncode:
        raise RuntimeError(
            f'container command failed ({arguments[0]}): '
            f'{completed.stderr.strip()}'
        )


def _connect(driver, options, password, database):
    return driver.connect(
        f'{options.host}/{options.port}:{database}',
        user=options.user, password=password,
    )


def _create(driver, options, password, database, marker):
    connection = driver.create_database(
        f'{options.host}/{options.port}:{database}',
        user=options.user, password=password, overwrite=False,
    )
    cursor = connection.cursor()
    try:
        cursor.execute(
            'CREATE TABLE CDE_FAULT_FIXTURE '
            '(ID INTEGER NOT NULL PRIMARY KEY, MARKER_VALUE INTEGER NOT NULL)'
        )
        connection.commit()
        cursor.execute(
            'INSERT INTO CDE_FAULT_FIXTURE VALUES (?, ?)', (1, marker)
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def _marker(driver, options, password, database):
    connection = _connect(driver, options, password, database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            'SELECT MARKER_VALUE FROM CDE_FAULT_FIXTURE WHERE ID = 1'
        )
        row = cursor.fetchone()
        return int(row[0]) if row else None
    finally:
        cursor.close()
        connection.close()


def _drop(driver, options, password, database):
    try:
        connection = _connect(driver, options, password, database)
    except Exception:
        return False
    try:
        connection.drop_database()
        return True
    finally:
        try:
            connection.close()
        except Exception:
            pass


def _provider_result(operation, result, marker):
    return {
        'operation': operation,
        'provider_schema': result.get('schema'),
        'provider_operation_id': result.get('operation_id'),
        'server_completed': result.get('server_completed') is True,
        'post_state_marker': marker,
        'provider_finality_authority': True,
        'common_finality_inference': False,
        'passed': (
            result.get('operation_id') == operation and
            result.get('server_completed') is True and marker is not None
        ),
    }


def _expected_failure(operation, callback, database_root, secret):
    try:
        callback()
    except Exception as exc:
        message = str(exc).replace(
            database_root, '[fixture-root]'
        ).replace(secret, '[redacted]')
        return {
            'operation': operation,
            'rejected': True,
            'error_type': type(exc).__name__,
            'error_message': message,
            'automatic_retry': False,
            'credential_values_exported': False,
            'passed': bool(message),
        }
    return {
        'operation': operation,
        'rejected': False,
        'automatic_retry': False,
        'credential_values_exported': False,
        'passed': False,
    }


def run(options):
    password = os.environ.get(options.password_env)
    if not password:
        raise RuntimeError(
            f'{options.password_env} must contain the test credential'
        )
    os.environ['CDEADMIN_FIREBIRD_CLIENT_LIBRARY'] = str(
        options.client_library.resolve()
    )
    import firebird.driver as driver
    from pgadmin.cdeadmin.providers.firebird.provider import (
        PROFILE,
        _client_library_identity,
        _firebird_service_operation,
        _server_arguments,
        _server_identity,
    )

    suffix = uuid.uuid4().hex
    root = options.database_root.rstrip('/')
    paths = {
        'shadow_primary': f'{root}/cdeadmin_shadow_primary_{suffix}.fdb',
        'shadow': f'{root}/cdeadmin_shadow_{suffix}.fdb',
        'nfix_primary': f'{root}/cdeadmin_nfix_primary_{suffix}.fdb',
        'nfix_copy': f'{root}/cdeadmin_nfix_copy_{suffix}.fdb',
    }
    server = None
    created = []
    results = []
    failures = []
    cleanup = {'databases_dropped': [], 'files_removed': []}
    server_identity = None
    try:
        route = {
            'host': options.host, 'port': options.port,
            'user': options.user, 'trusted_auth': False,
        }
        server_options = _server_arguments(route, driver)
        server_options['password'] = password
        server = driver.connect_server(**server_options)
        server_identity = _server_identity(server, {}, driver)

        # A shadow can only be activated after the primary is unavailable.
        # This fixture creates a real manual shadow, removes only its uniquely
        # named primary file, and promotes the shadow through the provider.
        try:
            _create(
                driver, options, password, paths['shadow_primary'], 4101
            )
            created.extend([paths['shadow_primary'], paths['shadow']])
            connection = _connect(
                driver, options, password, paths['shadow_primary']
            )
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"CREATE SHADOW 1 MANUAL '{paths['shadow']}'"
                )
                connection.commit()
            finally:
                cursor.close()
                connection.close()
            _container_command(
                options.container, 'rm', '-f', '--',
                paths['shadow_primary'],
            )
            cleanup['files_removed'].append(paths['shadow_primary'])
            result = _firebird_service_operation(
                server, 'activate_shadow', paths['shadow'], {}, driver
            )
            results.append(_provider_result(
                'activate_shadow', result,
                _marker(driver, options, password, paths['shadow']),
            ))
        except Exception as exc:
            failures.append({
                'fixture': 'activate_shadow',
                'error_type': type(exc).__name__,
                'error_message': str(exc).replace(root, '[fixture-root]'),
            })

        # Filesystem copies made while ALTER DATABASE BEGIN BACKUP is active
        # retain the stalled nbackup header state.  The provider's NFIX call
        # must normalize that exact copy before it can be attached.
        try:
            _create(
                driver, options, password, paths['nfix_primary'], 4202
            )
            created.extend([paths['nfix_primary'], paths['nfix_copy']])
            connection = _connect(
                driver, options, password, paths['nfix_primary']
            )
            cursor = connection.cursor()
            try:
                cursor.execute('ALTER DATABASE BEGIN BACKUP')
                connection.commit()
                _container_command(
                    options.container, 'cp', '--', paths['nfix_primary'],
                    paths['nfix_copy'],
                )
                cursor.execute('ALTER DATABASE END BACKUP')
                connection.commit()
            finally:
                cursor.close()
                connection.close()
            result = _firebird_service_operation(
                server, 'fixup_database', paths['nfix_copy'], {}, driver
            )
            results.append(_provider_result(
                'fixup_database', result,
                _marker(driver, options, password, paths['nfix_copy']),
            ))
        except Exception as exc:
            failures.append({
                'fixture': 'fixup_database',
                'error_type': type(exc).__name__,
                'error_message': str(exc).replace(root, '[fixture-root]'),
            })

        # NFIX must reject a database whose header is already in normal state.
        # Firebird's activate-shadow service is intentionally idempotent on a
        # normal database, so its proof is the promoted-shadow post-state
        # above rather than an invented missing-shadow rejection rule.
        if paths['nfix_primary'] in created:
            results.append(_expected_failure(
                'fixup_database_normal_state',
                lambda: _firebird_service_operation(
                    server, 'fixup_database', paths['nfix_primary'], {},
                    driver,
                ),
                root, password,
            ))
    finally:
        if server is not None:
            server.close()
        for database in reversed(list(paths.values())):
            if _drop(driver, options, password, database):
                cleanup['databases_dropped'].append(database)
        remaining = []
        for database in paths.values():
            try:
                _container_command(
                    options.container, 'test', '!', '-e', database
                )
            except Exception:
                remaining.append(database)
        if remaining:
            for database in remaining:
                try:
                    _container_command(
                        options.container, 'rm', '-f', '--', database
                    )
                    cleanup['files_removed'].append(database)
                except Exception:
                    pass
        password = ''

    expected = {
        'activate_shadow', 'fixup_database',
        'fixup_database_normal_state',
    }
    observed = {item['operation'] for item in results}
    return {
        'schema': 'cdeadmin.firebird-fault-service-live-evidence.v1',
        'reference_profile': PROFILE.exact_version,
        'driver_version': driver.__VERSION__,
        **_client_library_identity(driver),
        'server_identity': server_identity,
        'disposable_database': True,
        'packaged_sample_database_used': False,
        'credential_values_exported': False,
        'results': results,
        'failures': failures,
        'cleanup': cleanup,
        'expected_operations': sorted(expected),
        'passed': (
            observed == expected and not failures and
            all(item['passed'] for item in results) and
            len(cleanup['databases_dropped']) +
            len(cleanup['files_removed']) == len(set(paths.values()))
        ),
    }


def main(argv=None):
    options = arguments(argv)
    result = run(options)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps({
        'passed': result['passed'],
        'operation_count': len(result['results']),
        'failure_count': len(result['failures']),
        'output': str(options.output),
        'credential_values_exported': False,
    }, indent=2, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
