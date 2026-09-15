#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Prove Firebird service-form completion through an isolated CDEadmin UI.

The orchestrator clones an existing QA configuration database into a
temporary DATA_DIR, retargets only that clone to a uniquely named disposable
Firebird database, launches an isolated desktop-mode CDEadmin process, and
runs every normal-state service form through its rendered Apply button.  It
collects every operation result before cleanup and never changes the packaged
sample database or the source QA configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
for path in (ROOT, WEB):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from tools.cdeadmin_firebird_ui_form_gate import (  # noqa: E402
    NORMAL_COMPLETION_ORDER,
)
from tools.cdeadmin_ui_evidence import browser_binary  # noqa: E402


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-config-db', type=Path, required=True)
    parser.add_argument('--desktop-user', required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--firebird-port', type=int, default=53050)
    parser.add_argument('--database-root', required=True)
    parser.add_argument('--user', default='SYSDBA')
    parser.add_argument(
        '--password-env', default='CDEADMIN_FIREBIRD_DEMO_PASSWORD'
    )
    parser.add_argument('--client-library', type=Path, required=True)
    parser.add_argument('--container', required=True)
    parser.add_argument('--browser-binary')
    parser.add_argument(
        '--operation', action='append', choices=NORMAL_COMPLETION_ORDER,
        help=(
            'Run only this normal-state operation. Repeat to retain the '
            'specified order; omit for the complete certification inventory.'
        ),
    )
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--manifest-output', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--server-log', type=Path, required=True)
    parser.add_argument('--operation-log-root', type=Path, required=True)
    parser.add_argument('--width', type=int, default=1600)
    parser.add_argument('--height', type=int, default=1000)
    parser.add_argument('--timeout', type=int, default=90)
    return parser.parse_args(argv)


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        return int(listener.getsockname()[1])


def _wait_for_server(process, port, timeout=45.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                'isolated CDEadmin server stopped during startup'
            )
        try:
            with socket.create_connection(
                    ('127.0.0.1', port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError('isolated CDEadmin server did not become ready')


def _retarget_config(database, desktop_user, target_database, target_label,
                     firebird_port, endpoint_user=None, endpoint_role=None):
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT s.id, e.id, t.id, r.id, r.configuration
              FROM server AS s
              JOIN user AS u ON u.id = s.user_id
              JOIN cde_endpoint AS e ON e.legacy_server_id = s.id
              JOIN cde_endpoint_database_target AS t
                ON t.endpoint_id = e.id AND t.active = 1
              JOIN cde_endpoint_route AS r
                ON r.endpoint_id = e.id AND r.priority = 0
             WHERE u.email = ? AND e.profile_id = 'firebird-native'
            """,
            (desktop_user,),
        ).fetchall()
        if len(row) != 1:
            raise RuntimeError(
                'QA configuration must contain exactly one active Firebird '
                'database target for the desktop user'
            )
        server_id, endpoint_id, target_id, route_id, route_value = row[0]
        route = json.loads(route_value)
        route.update({
            'host': '127.0.0.1', 'port': firebird_port,
            'database_create_root': str(Path(target_database).parent),
        })
        route.pop('database', None)
        if endpoint_role is not None:
            route['role'] = endpoint_role
            connection.execute('UPDATE server SET role = ? WHERE id = ?',
                               (endpoint_role, server_id))
        if endpoint_user is not None:
            route['user'] = endpoint_user
            connection.execute('UPDATE server SET username = ? WHERE id = ?',
                               (endpoint_user, server_id))
        connection.execute(
            "UPDATE server SET name = 'localhost', host = '127.0.0.1', "
            "port = ? WHERE id = ?",
            (firebird_port, server_id),
        )
        connection.execute(
            'UPDATE cde_endpoint_route SET configuration = ? WHERE id = ?',
            (json.dumps(route, sort_keys=True, separators=(',', ':')),
             route_id),
        )
        connection.execute(
            """
            UPDATE cde_endpoint_database_target
               SET display_name = ?, database = ?, active = 1,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = ? AND endpoint_id = ?
            """,
            (target_label, target_database, target_id, endpoint_id),
        )
        connection.execute(
            """
            UPDATE cde_endpoint_runtime_identity
               SET verification_state = 'verified',
                   verified_runtime_family = 'firebird',
                   verified_runtime_version = '5.0.4'
             WHERE endpoint_id = ?
            """,
            (endpoint_id,),
        )
        connection.commit()


def _write_config(path, data_dir, user, port):
    path.write_text(
        "\n".join((
            '"""Generated isolated Firebird UI completion gate config."""',
            f'DATA_DIR = {str(data_dir)!r}',
            'SERVER_MODE = False',
            f'DESKTOP_USER = {user!r}',
            "DEFAULT_SERVER = '127.0.0.1'",
            f'DEFAULT_SERVER_PORT = {port}',
            'MASTER_PASSWORD_REQUIRED = False',
            'CHECK_EMAIL_DELIVERABILITY = False',
            'SEND_FILE_MAX_AGE_DEFAULT = 0',
            "APP_VERSION_PARAM = 'firebird_ui_completed_gate'",
            '',
        )),
        encoding='utf-8',
    )


def _docker_file_exists(container, path):
    return subprocess.run(
        ['docker', 'exec', container, 'test', '-f', path],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def _remove_server_files(container, paths):
    removed = []
    for path in paths:
        if not _docker_file_exists(container, path):
            continue
        completed = subprocess.run(
            ['docker', 'exec', container, 'rm', '-f', path],
            check=False, capture_output=True, text=True,
        )
        if completed.returncode == 0:
            removed.append(path)
    return removed


def _connect(driver, host, port, database, user, password):
    return driver.connect(
        f'{host}/{port}:{database}', user=user, password=password
    )


def _database_observation(driver, options, password, database):
    connection = _connect(
        driver, options.host, options.firebird_port, database,
        options.user, password,
    )
    try:
        info = connection.info
        return {
            'connectable': True,
            'engine_version': str(info.engine_version),
            'page_cache_size': int(info.page_cache_size),
            'sweep_interval': int(info.sweep_interval),
            'space_reservation': str(info.space_reservation),
            'write_mode': str(info.write_mode),
            'access_mode': str(info.access_mode),
            'sql_dialect': int(info.sql_dialect),
        }
    finally:
        connection.close()


def _post_state(driver, options, password, operation, paths, summary):
    observation = {
        'provider_specific_observation': True,
        'common_finality_inference': False,
    }
    if operation == 'backup_logical':
        observation['backup_file_exists'] = _docker_file_exists(
            options.container, paths['logical_backup']
        )
    elif operation == 'restore_logical':
        observation['restored_database'] = _database_observation(
            driver, options, password, paths['logical_restore']
        )
    elif operation == 'backup_physical':
        observation['backup_file_exists'] = _docker_file_exists(
            options.container, paths['physical_backup']
        )
    elif operation == 'restore_physical':
        observation['restored_database'] = _database_observation(
            driver, options, password, paths['physical_restore']
        )
    elif operation == 'shutdown_database':
        try:
            connection = _connect(
                driver, options.host, options.firebird_port,
                paths['primary'], options.user, password,
            )
        except Exception:
            observation['new_attachment_rejected'] = True
        else:
            connection.close()
            observation['new_attachment_rejected'] = False
    else:
        observation['database'] = _database_observation(
            driver, options, password, paths['primary']
        )
    if operation in {'database_statistics', 'validate_database'}:
        forms = summary.get('forms') or []
        completion = forms[0].get('completion', {}) if forms else {}
        observation['service_output_observed'] = (
            completion.get('output_line_count', 0) > 0
        )
    passed = all(
        value is not False for key, value in observation.items()
        if key not in {
            'common_finality_inference', 'provider_specific_observation'
        }
    )
    observation['passed'] = passed
    return observation


def _drop_database(driver, options, password, database):
    try:
        connection = _connect(
            driver, options.host, options.firebird_port, database,
            options.user, password,
        )
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


def _bring_database_online(driver, options, password, database, suffix):
    """Use only the public driver service API to reopen a QA database."""
    server_name = f'cdeadmin_ui_cleanup_{suffix}'
    server = driver.driver_config.get_server(server_name)
    if server is None:
        server = driver.driver_config.register_server(server_name)
    server.host.value = options.host
    server.port.value = str(options.firebird_port)
    server.user.value = options.user
    service = driver.connect_server(
        server=server_name, user=options.user, password=password
    )
    try:
        service.database.bring_online(database=database)
    finally:
        service.close()


def run(options):
    password = os.environ.get(options.password_env)
    if not password:
        raise RuntimeError(
            f'{options.password_env} must contain the test credential'
        )
    if not options.source_config_db.is_file():
        raise RuntimeError('source QA configuration database is missing')
    source_hash = _sha256(options.source_config_db)
    os.environ['CDEADMIN_FIREBIRD_CLIENT_LIBRARY'] = str(
        options.client_library.resolve()
    )
    driver = None
    import firebird.driver as driver
    from pgadmin.cdeadmin.providers.firebird.provider import (
        _configure_client_library,
    )
    _configure_client_library(driver)

    suffix = uuid.uuid4().hex
    prefix = f'{options.database_root.rstrip("/")}/cdeadmin_ui_{suffix}'
    paths = {
        'primary': f'{prefix}.fdb',
        'logical_backup': f'{prefix}.fbk',
        'logical_restore': f'{prefix}-gbak.fdb',
        'physical_backup': f'{prefix}.nbk',
        'physical_restore': f'{prefix}-nbackup.fdb',
    }
    target_label = Path(paths['primary']).name
    created = []
    cleanup = {
        'databases_dropped': [], 'files_removed': [],
        'cleanup_verified_absent': [],
    }
    results = []
    operations = tuple(options.operation or NORMAL_COMPLETION_ORDER)
    infrastructure_failure = None
    process = None
    selected_browser = browser_binary(options.browser_binary)
    try:
        connection = driver.create_database(
            f'{options.host}/{options.firebird_port}:{paths["primary"]}',
            user=options.user, password=password, overwrite=False,
        )
        connection.close()
        created.append(paths['primary'])
        with tempfile.TemporaryDirectory(
                prefix='cdeadmin-firebird-ui-completed-') as temporary:
            temporary_root = Path(temporary)
            data_dir = temporary_root / 'runtime'
            data_dir.mkdir()
            config_database = data_dir / 'cdeadmin.db'
            shutil.copy2(options.source_config_db, config_database)
            _retarget_config(
                config_database, options.desktop_user, paths['primary'],
                target_label, options.firebird_port,
            )
            port = _free_port()
            config_file = temporary_root / 'qa_config.py'
            _write_config(config_file, data_dir, options.desktop_user, port)
            options.server_log.parent.mkdir(parents=True, exist_ok=True)
            server_output = options.server_log.open('wb')
            environment = os.environ.copy()
            environment['CONFIG_DISTRO_FILE_PATH'] = str(config_file)
            environment['CDEADMIN_FIREBIRD_CLIENT_LIBRARY'] = str(
                options.client_library.resolve()
            )
            process = subprocess.Popen(
                [sys.executable, str(ROOT / 'web/CDEadmin.py')],
                cwd=ROOT, env=environment, stdout=server_output,
                stderr=subprocess.STDOUT,
            )
            try:
                _wait_for_server(process, port)
                options.operation_log_root.mkdir(parents=True, exist_ok=True)
                for sequence, operation in enumerate(
                        operations, start=1):
                    summary_path = (
                        options.operation_log_root /
                        f'{sequence:02d}-{operation}.json'
                    )
                    log_path = (
                        options.operation_log_root /
                        f'{sequence:02d}-{operation}.log'
                    )
                    command = [
                        sys.executable,
                        str(ROOT / 'tools/cdeadmin_firebird_ui_form_gate.py'),
                        '--url', f'http://127.0.0.1:{port}',
                        '--engine', 'Firebird', '--server', 'localhost',
                        '--database', target_label,
                        '--endpoint-password-env', options.password_env,
                        '--output-root', str(options.evidence_root),
                        '--summary-output', str(summary_path),
                        '--manifest-output', str(options.manifest_output),
                        '--operation', operation, '--apply-live',
                        '--server-file-prefix', prefix,
                        '--width', str(options.width),
                        '--height', str(options.height),
                        '--timeout', str(options.timeout),
                    ]
                    command.extend([
                        '--browser-binary', selected_browser
                    ])
                    completed = subprocess.run(
                        command, cwd=ROOT, env=environment,
                        capture_output=True, text=True, check=False,
                    )
                    log_path.write_text(
                        completed.stdout + completed.stderr,
                        encoding='utf-8',
                    )
                    summary = None
                    if summary_path.is_file():
                        summary = json.loads(summary_path.read_text(
                            encoding='utf-8'
                        ))
                    result = {
                        'sequence': sequence, 'operation': operation,
                        'browser_exit_code': completed.returncode,
                        'browser_summary_present': summary is not None,
                        'browser_complete': bool(
                            summary and summary.get('complete')
                        ),
                        'log': str(log_path),
                        'summary': str(summary_path),
                    }
                    if summary and summary.get('complete'):
                        try:
                            result['post_state'] = _post_state(
                                driver, options, password, operation,
                                paths, summary,
                            )
                        except Exception as exc:
                            result['post_state'] = {
                                'passed': False,
                                'error_type': type(exc).__name__,
                            }
                    if operation == 'restore_logical' and (
                            _docker_file_exists(
                                options.container, paths['logical_restore']
                            )):
                        created.append(paths['logical_restore'])
                    if operation == 'restore_physical' and (
                            _docker_file_exists(
                                options.container, paths['physical_restore']
                            )):
                        created.append(paths['physical_restore'])
                    result['passed'] = (
                        result['browser_exit_code'] == 0 and
                        result['browser_complete'] and
                        result.get('post_state', {}).get('passed') is True
                    )
                    results.append(result)
            finally:
                if process is not None:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=15)
                server_output.close()
    except Exception as exc:
        infrastructure_failure = {
            'error_type': type(exc).__name__, 'message': str(exc),
        }
    finally:
        try:
            if driver is None:
                raise RuntimeError('Firebird driver was not loaded')
            _bring_database_online(
                driver, options, password, paths['primary'], suffix
            )
        except Exception:
            pass
        database_paths = [
            paths['physical_restore'], paths['logical_restore'],
            paths['primary'],
        ]
        for database in database_paths:
            if _drop_database(driver, options, password, database):
                cleanup['databases_dropped'].append(database)
        cleanup['files_removed'] = _remove_server_files(
            options.container, list(paths.values()),
        )
        cleanup['cleanup_verified_absent'] = [
            path for path in paths.values()
            if not _docker_file_exists(options.container, path)
        ]
        password = ''

    source_unchanged = (
        options.source_config_db.is_file() and
        _sha256(options.source_config_db) == source_hash
    )
    return {
        'schema': 'cdeadmin.firebird-ui-completed-gate.v1',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'engine_id': 'firebird', 'interface_id': 'firebird-native',
        'reference_version': '5.0.4',
        'normal_operation_count': len(operations),
        'full_normal_inventory': not bool(options.operation),
        'result_count': len(results),
        'results': results,
        'fault_fixture_operations_excluded': [
            'activate_shadow', 'fixup_database',
        ],
        'isolated_config_clone': True,
        'source_config_sha256': source_hash,
        'source_config_unchanged': source_unchanged,
        'packaged_sample_database_used': False,
        'disposable_database': True,
        'credential_values_exported': False,
        'server_stopped': process is None or process.returncode is not None,
        'browser_binary': selected_browser,
        'cleanup': cleanup,
        'infrastructure_failure': infrastructure_failure,
        'complete': (
            infrastructure_failure is None and source_unchanged and
            len(results) == len(operations) and
            all(item['passed'] for item in results) and
            len(cleanup['cleanup_verified_absent']) == len(paths)
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
        'complete': result['complete'],
        'result_count': result['result_count'],
        'failed_operations': [
            item['operation'] for item in result['results']
            if not item['passed']
        ],
        'output': str(options.output),
        'credential_values_exported': False,
    }, indent=2, sort_keys=True))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
