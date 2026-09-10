#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Run Firebird database task forms against disposable Firebird 5 files."""

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


def _drop_database(driver, dsn, user, password):
    try:
        connection = driver.connect(dsn, user=user, password=password)
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


def _remove_server_files(container, paths):
    if not container:
        return []
    removed = []
    for path in paths:
        completed = subprocess.run(
            ['docker', 'exec', container, 'rm', '-f', path],
            check=False, capture_output=True, text=True,
        )
        if completed.returncode == 0:
            removed.append(path)
    return removed


def run(options):
    if options.client_library:
        os.environ['CDEADMIN_FIREBIRD_CLIENT_LIBRARY'] = str(
            options.client_library.resolve()
        )
    password = os.environ.get(options.password_env)
    if not password:
        raise RuntimeError(
            f'{options.password_env} must contain the test credential'
        )

    import firebird.driver as driver
    from pgadmin.cdeadmin.providers.firebird.provider import (
        PROFILE,
        _client_library_identity,
        _configure_client_library,
        _firebird_service_operation,
        _server_arguments,
        _server_identity,
    )
    _configure_client_library(driver)

    suffix = uuid.uuid4().hex
    root = options.database_root.rstrip('/')
    primary = f'{root}/cdeadmin_service_qa_{suffix}.fdb'
    logical_restore = f'{root}/cdeadmin_service_gbak_{suffix}.fdb'
    physical_restore = f'{root}/cdeadmin_service_nbak_{suffix}.fdb'
    logical_backup = f'{root}/cdeadmin_service_qa_{suffix}.fbk'
    physical_backup = f'{root}/cdeadmin_service_qa_{suffix}.nbk'

    def dsn(database):
        return f'{options.host}/{options.port}:{database}'

    route = {
        'host': options.host, 'port': options.port, 'user': options.user,
        'trusted_auth': False,
    }
    server_arguments = _server_arguments(route, driver)
    server_arguments['password'] = password
    server = None
    results = []
    cleanup = {'databases_dropped': [], 'files_removed': []}
    created = []
    backup_files = [logical_backup, physical_backup]
    operations = (
        ('set_page_cache_size', primary, {'page_buffers': 4096}),
        ('set_sweep_interval', primary, {'sweep_interval': 20000}),
        ('set_space_reservation', primary, {'mode': 'USE_FULL'}),
        ('set_space_reservation', primary, {'mode': 'RESERVE'}),
        ('set_write_mode', primary, {'mode': 'ASYNC'}),
        ('set_write_mode', primary, {'mode': 'SYNC'}),
        ('set_access_mode', primary, {'mode': 'READ_ONLY'}),
        ('set_access_mode', primary, {'mode': 'READ_WRITE'}),
        ('set_sql_dialect', primary, {'sql_dialect': 3}),
        ('remove_linger', primary, {}),
        ('set_replica_mode', primary, {'mode': 'READ_ONLY'}),
        ('set_replica_mode', primary, {'mode': 'NONE'}),
        ('upgrade_database', primary, {}),
        ('database_statistics', primary, {
            'statistics_flags': ['HDR_PAGES'],
        }),
        ('validate_database', primary, {}),
        ('sweep_database', primary, {'parallel_workers': 2}),
        ('backup_logical', primary, {
            'backup_file': logical_backup, 'backup_flags': [],
            'verbose': True,
        }),
        ('restore_logical', primary, {
            'backup_file': logical_backup,
            'restore_database': logical_restore,
            'restore_flags': [], 'verbose': True,
        }),
        ('backup_physical', primary, {
            'backup_file': physical_backup, 'backup_level': 0,
            'backup_flags': [],
        }),
        ('restore_physical', primary, {
            'backup_files': [physical_backup],
            'restore_database': physical_restore, 'restore_flags': [],
        }),
        ('repair_database', primary, {'repair_action': 'VALIDATE_DB'}),
        ('shutdown_database', primary, {
            'mode': 'FULL', 'method': 'DENY_ATTACHMENTS',
            'shutdown_timeout': 0,
        }),
        ('bring_online', primary, {'mode': 'NORMAL'}),
    )
    try:
        connection = driver.create_database(
            dsn(primary), user=options.user, password=password,
            overwrite=False,
        )
        connection.close()
        created.append(primary)
        server = driver.connect_server(**server_arguments)
        server_identity = _server_identity(server, {}, driver)
        for sequence, (operation, database, values) in enumerate(
                operations, start=1):
            try:
                result = _firebird_service_operation(
                    server, operation, database, values, driver
                )
            except Exception as exc:
                results.append({
                    'sequence': sequence, 'operation': operation,
                    'passed': False, 'error_type': type(exc).__name__,
                    'error_message': str(exc),
                })
                break
            results.append({
                'sequence': sequence, 'operation': operation,
                'passed': result['server_completed'],
                'output_lines': len(result.get('output', [])),
            })
            if operation == 'restore_logical':
                created.append(logical_restore)
            elif operation == 'restore_physical':
                created.append(physical_restore)
    finally:
        if server is not None:
            server.close()
        for database in reversed(created):
            if _drop_database(
                    driver, dsn(database), options.user, password):
                cleanup['databases_dropped'].append(database)
        cleanup['files_removed'] = _remove_server_files(
            options.container, backup_files
        )
        password = ''

    return {
        'schema': 'cdeadmin.firebird-service-form-live-evidence.v1',
        'reference_profile': PROFILE.exact_version,
        'driver_version': driver.__VERSION__,
        **_client_library_identity(driver),
        'server_identity': server_identity,
        'disposable_database': True,
        'credential_values_exported': False,
        'results': results,
        'operation_count': len(results),
        'expected_operation_count': len(operations),
        'cleanup': cleanup,
        'passed': (
            len(results) == len(operations) and
            all(item['passed'] for item in results) and
            len(cleanup['databases_dropped']) == len(created)
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=53050)
    parser.add_argument('--database-root', required=True)
    parser.add_argument('--user', default='SYSDBA')
    parser.add_argument(
        '--password-env', default='CDEADMIN_FIREBIRD_DEMO_PASSWORD'
    )
    parser.add_argument('--client-library', type=Path, required=True)
    parser.add_argument('--container')
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args(argv)
    result = run(options)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps({
        'passed': result['passed'],
        'operation_count': result['operation_count'],
        'output': str(options.output),
    }, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
