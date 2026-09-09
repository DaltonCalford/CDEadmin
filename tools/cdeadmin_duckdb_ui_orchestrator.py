#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Run DuckDB 1.5.2 browser gates in an isolated CDEadmin process."""

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
from datetime import datetime, timezone
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-config-db', type=Path, required=True)
    parser.add_argument('--source-database', type=Path, required=True)
    parser.add_argument('--desktop-user', required=True)
    parser.add_argument('--browser-binary')
    parser.add_argument(
        '--gate-kind', choices=(
            'object', 'grid', 'query', 'lifecycle', 'maintenance',
            'properties',
        ),
        default='object',
    )
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--summary-output', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--server-log', type=Path, required=True)
    parser.add_argument('--browser-log', type=Path, required=True)
    parser.add_argument('--width', type=int, default=1600)
    parser.add_argument('--height', type=int, default=1000)
    parser.add_argument(
        '--theme', choices=('default', 'high-contrast'), default='default',
    )
    parser.add_argument(
        '--font-scale', type=int, choices=(100, 150, 200, 300), default=100,
    )
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
            raise RuntimeError('isolated CDEadmin server stopped at startup')
        try:
            with socket.create_connection(
                    ('127.0.0.1', port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError('isolated CDEadmin server did not become ready')


def _prepare_database(source, target, attached, import_directory):
    shutil.copy2(source, target)
    secret_directory = target.parent / 'secrets'
    with duckdb.connect(
        str(target), config={'secret_directory': str(secret_directory)}
    ) as connection:
        connection.execute(
            'CREATE TABLE IF NOT EXISTS qualification('
            'id INTEGER PRIMARY KEY, value VARCHAR, event_date DATE)'
        )
        connection.execute(
            "INSERT OR IGNORE INTO qualification VALUES "
            "(1, 'duckdb-ui-gate', DATE '2026-01-15')"
        )
        connection.execute(
            'CREATE INDEX IF NOT EXISTS qualification_value_idx '
            'ON qualification(value)'
        )
        connection.execute(
            'CREATE SEQUENCE IF NOT EXISTS qualification_sequence START 1'
        )
        connection.execute(
            "CREATE TYPE qualification_state AS ENUM ('ready', 'done')"
            if not connection.execute(
                "SELECT count(*) FROM duckdb_types() "
                "WHERE schema_name = 'main' AND "
                "type_name = 'qualification_state'"
            ).fetchone()[0] else 'SELECT 1'
        )
        connection.execute(
            "CREATE PERSISTENT SECRET qualification_http "
            "(TYPE HTTP, BEARER_TOKEN 'fixture-only')"
        )
        connection.execute(
            'CREATE TABLE IF NOT EXISTS pagination_probe('
            'id INTEGER PRIMARY KEY, value VARCHAR NOT NULL)'
        )
        connection.execute('DELETE FROM pagination_probe')
        connection.executemany(
            'INSERT INTO pagination_probe VALUES (?, ?)',
            ((index, f'page-row-{index:03d}') for index in range(1, 251)),
        )
    with duckdb.connect(str(attached)) as connection:
        connection.execute(
            'CREATE TABLE archive_item(id INTEGER PRIMARY KEY, value VARCHAR)'
        )
        connection.execute("INSERT INTO archive_item VALUES (1, 'archive')")
    import_source = target.parent / 'duckdb_import_source.duckdb'
    with duckdb.connect(str(import_source)) as connection:
        connection.execute(
            'CREATE TABLE import_probe(id INTEGER PRIMARY KEY, value VARCHAR)'
        )
        connection.execute(
            "INSERT INTO import_probe VALUES "
            "(1, 'duckdb-import-browser-gate')"
        )
        escaped = str(import_directory).replace("'", "''")
        connection.execute(
            f"EXPORT DATABASE '{escaped}' (FORMAT PARQUET)"
        )


def _retarget_config(database, desktop_user, target_database, attached):
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT s.id, e.id, t.id, r.id, r.configuration
              FROM server AS s
              JOIN user AS u ON u.id = s.user_id
              JOIN cde_endpoint AS e ON e.legacy_server_id = s.id
              JOIN cde_endpoint_database_target AS t
                ON t.endpoint_id = e.id AND t.active = 1
              JOIN cde_endpoint_route AS r
                ON r.endpoint_id = e.id AND r.priority = 0
             WHERE u.email = ? AND e.profile_id = 'duckdb-native'
            """,
            (desktop_user,),
        ).fetchall()
        if len(rows) != 1:
            raise RuntimeError(
                'QA configuration must have one active DuckDB target'
            )
        server_id, endpoint_id, target_id, route_id, route_json = rows[0]
        route = json.loads(route_json)
        route.update({
            'database': str(target_database),
            'filesystem_root': str(target_database.parent),
            'database_create_root': str(target_database.parent),
            'read_only': False,
            'config': {
                'secret_directory': str(target_database.parent / 'secrets'),
            },
            'attached_databases': [{
                'name': 'archive', 'database': str(attached),
                'read_only': False,
            }],
        })
        connection.execute(
            "UPDATE server SET name = 'localhost', host = NULL, port = 1 "
            'WHERE id = ?', (server_id,),
        )
        connection.execute(
            'UPDATE cde_endpoint_route SET configuration = ? WHERE id = ?',
            (json.dumps(route, sort_keys=True, separators=(',', ':')),
             route_id),
        )
        connection.execute(
            """
            UPDATE cde_endpoint_database_target
               SET display_name = ?, database = ?, configuration = ?,
                   active = 1,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = ? AND endpoint_id = ?
            """,
            (
                target_database.name, str(target_database),
                json.dumps({
                    'read_only': False,
                    'config': {
                        'secret_directory': str(
                            target_database.parent / 'secrets'
                        ),
                    },
                }, sort_keys=True, separators=(',', ':')),
                target_id, endpoint_id,
            ),
        )
        connection.execute(
            """
            UPDATE cde_endpoint_runtime_identity
               SET verification_state = 'verified',
                   verified_runtime_family = 'duckdb',
                   verified_runtime_version = '1.5.2'
             WHERE endpoint_id = ?
            """,
            (endpoint_id,),
        )
        connection.commit()


def _write_config(path, data_dir, user, port):
    path.write_text('\n'.join((
        '"""Generated isolated DuckDB UI gate configuration."""',
        f'DATA_DIR = {str(data_dir)!r}',
        'SERVER_MODE = False',
        f'DESKTOP_USER = {user!r}',
        "DEFAULT_SERVER = '127.0.0.1'",
        f'DEFAULT_SERVER_PORT = {port}',
        'MASTER_PASSWORD_REQUIRED = False',
        'CHECK_EMAIL_DELIVERABILITY = False',
        'SEND_FILE_MAX_AGE_DEFAULT = 0',
        "APP_VERSION_PARAM = 'duckdb_object_form_gate'",
        '',
    )), encoding='utf-8')


def run(options):
    if duckdb.__version__ != '1.5.2':
        raise RuntimeError('DuckDB UI gate requires driver version 1.5.2')
    for source in (options.source_config_db, options.source_database):
        if not source.is_file():
            raise RuntimeError(f'required source is missing: {source}')
    source_hashes = {
        str(options.source_config_db): _sha256(options.source_config_db),
        str(options.source_database): _sha256(options.source_database),
    }
    result = None
    infrastructure_failure = None
    process = None
    server_output = None
    with tempfile.TemporaryDirectory(
            prefix='cdeadmin-duckdb-ui-gate-') as temporary:
        temporary_root = Path(temporary)
        data_dir = temporary_root / 'runtime'
        data_dir.mkdir()
        config_database = data_dir / 'cdeadmin.db'
        database = temporary_root / 'cdeadmin_duckdb_ui.duckdb'
        attached = temporary_root / 'cdeadmin_duckdb_archive.duckdb'
        import_directory = temporary_root / 'duckdb-import-fixture'
        export_directory = temporary_root / 'duckdb-browser-export'
        shutil.copy2(options.source_config_db, config_database)
        _prepare_database(
            options.source_database, database, attached, import_directory
        )
        _retarget_config(
            config_database, options.desktop_user, database, attached
        )
        port = _free_port()
        config_file = temporary_root / 'qa_config.py'
        _write_config(config_file, data_dir, options.desktop_user, port)
        environment = os.environ.copy()
        environment['CONFIG_DISTRO_FILE_PATH'] = str(config_file)
        options.server_log.parent.mkdir(parents=True, exist_ok=True)
        server_output = options.server_log.open('wb')
        try:
            process = subprocess.Popen(
                [sys.executable, str(ROOT / 'web/CDEadmin.py')],
                cwd=ROOT, env=environment, stdout=server_output,
                stderr=subprocess.STDOUT,
            )
            _wait_for_server(process, port)
            gate_script = {
                'object': 'cdeadmin_provider_object_form_gate.py',
                'grid': 'cdeadmin_sqlite_grid_ui_gate.py',
                'query': 'cdeadmin_sqlite_query_ui_gate.py',
                'lifecycle': (
                    'cdeadmin_sqlite_database_lifecycle_ui_gate.py'
                ),
                'maintenance': 'cdeadmin_sqlite_maintenance_ui_gate.py',
                'properties': 'cdeadmin_sqlite_properties_ui_gate.py',
            }[options.gate_kind]
            command = [
                sys.executable,
                str(ROOT / 'tools' / gate_script),
                '--url', f'http://127.0.0.1:{port}',
                '--engine', 'DuckDB',
                '--server', 'localhost', '--database', database.name,
                '--output-root', str(options.evidence_root),
                '--summary-output', str(options.summary_output),
                '--width', str(options.width),
                '--height', str(options.height),
                '--theme', options.theme,
                '--font-scale', str(options.font_scale),
                '--timeout', str(options.timeout),
            ]
            if options.gate_kind == 'lifecycle':
                command = [
                    sys.executable, str(ROOT / 'tools' / gate_script),
                    '--url', f'http://127.0.0.1:{port}',
                    '--engine', 'DuckDB',
                    '--config-db', str(config_database),
                    '--database-root', str(temporary_root),
                    '--database', database.name,
                    '--output-root', str(options.evidence_root),
                    '--summary-output', str(options.summary_output),
                    '--width', str(options.width),
                    '--height', str(options.height),
                    '--theme', options.theme,
                    '--font-scale', str(options.font_scale),
                    '--timeout', str(options.timeout),
                ]
            if options.gate_kind == 'object':
                command.extend([
                    '--engine-id', 'duckdb',
                    '--interface-id', 'duckdb-native',
                    '--reference-version', '1.5.2',
                ])
            elif options.gate_kind in {'grid', 'properties'}:
                command.extend(['--database-path', str(database)])
            elif options.gate_kind == 'maintenance':
                command.extend([
                    '--database-path', str(database),
                    '--backup-path', str(
                        temporary_root / 'unused-sqlite-backup'
                    ),
                    '--export-path', str(export_directory),
                    '--import-path', str(import_directory),
                ])
            if options.browser_binary:
                command.extend(['--browser-binary', options.browser_binary])
            options.summary_output.unlink(missing_ok=True)
            completed = subprocess.run(
                command, cwd=ROOT, env=environment,
                capture_output=True, text=True, check=False,
            )
            options.browser_log.parent.mkdir(parents=True, exist_ok=True)
            options.browser_log.write_text(
                completed.stdout + completed.stderr, encoding='utf-8'
            )
            if options.summary_output.is_file():
                result = json.loads(options.summary_output.read_text(
                    encoding='utf-8'
                ))
            if completed.returncode != 0:
                result = None
                raise RuntimeError(
                    'browser gate failed; see the isolated browser log'
                )
        except Exception as exc:
            infrastructure_failure = {
                'error_type': type(exc).__name__, 'message': str(exc),
            }
        finally:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
            if server_output is not None:
                server_output.close()
    source_unchanged = all(
        Path(path).is_file() and _sha256(Path(path)) == digest
        for path, digest in source_hashes.items()
    )
    complete = (
        infrastructure_failure is None and source_unchanged and
        bool(result and (result.get('complete') or result.get('passed'))) and
        (process is None or process.returncode is not None)
    )
    return {
        'schema': 'cdeadmin.duckdb-ui-orchestrator.v1',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'engine_id': 'duckdb', 'interface_id': 'duckdb-native',
        'reference_version': '1.5.2',
        'isolated_config_clone': True, 'isolated_database_clone': True,
        'source_hashes': source_hashes, 'sources_unchanged': source_unchanged,
        'server_stopped': process is None or process.returncode is not None,
        'browser_summary': str(options.summary_output),
        'gate_kind': options.gate_kind,
        'browser_complete': bool(
            result and (result.get('complete') or result.get('passed'))
        ),
        'infrastructure_failure': infrastructure_failure,
        'temporary_runtime_removed': True, 'complete': complete,
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
        'browser_complete': result['browser_complete'],
        'sources_unchanged': result['sources_unchanged'],
        'output': str(options.output),
    }, indent=2, sort_keys=True))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
