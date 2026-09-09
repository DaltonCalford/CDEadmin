#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Run MySQL 9.7.0 browser gates against an isolated exact runtime.

The orchestrator clones the CDEadmin configuration database, starts a
throwaway MySQL 9.7.0 container on an ephemeral host port, seeds native
objects, normalizes the cloned endpoint to the mandatory
server -> database-target hierarchy, and runs the requested browser gate.
Neither the user's CDEadmin process nor its configuration is modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
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

import mysql.connector


ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'mysql:9.7.0'
ENGINE_ID = 'mysql'
PROFILE_ID = 'mysql-native'
REFERENCE_VERSION = '9.7.0'
DATABASE = 'cdeadmin_demo'


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-config-db', type=Path, required=True)
    parser.add_argument('--desktop-user', required=True)
    parser.add_argument('--browser-binary')
    parser.add_argument('--image', default=IMAGE)
    parser.add_argument(
        '--gate-kind',
        choices=(
            'object', 'lifecycle', 'properties', 'grid', 'query',
            'maintenance', 'backup-restore',
        ),
        default='object'
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
    parser.add_argument('--startup-timeout', type=int, default=300)
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


def _run(command, *, check=True):
    return subprocess.run(
        command, check=check, capture_output=True, text=True,
    )


def _wait_for_cdeadmin(process, port, timeout=45.0):
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


def _wait_for_mysql(port, password, timeout):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            connection = mysql.connector.connect(
                host='127.0.0.1', port=port, user='root', password=password,
                connection_timeout=2, use_pure=True,
            )
            cursor = connection.cursor()
            cursor.execute('SELECT VERSION()')
            version = str(cursor.fetchone()[0]).split('-', 1)[0]
            cursor.close()
            connection.close()
            if version != REFERENCE_VERSION:
                raise RuntimeError(
                    f'MySQL runtime must be {REFERENCE_VERSION}, got {version}'
                )
            return
        except Exception as exc:
            last = exc
            time.sleep(1)
    raise RuntimeError(
        f'MySQL {REFERENCE_VERSION} did not become ready: '
        f'{type(last).__name__}: {last}'
    )


def _seed_mysql(port, password):
    connection = mysql.connector.connect(
        host='127.0.0.1', port=port, user='root', password=password,
        connection_timeout=10, use_pure=True,
    )
    connection.autocommit = True
    cursor = connection.cursor()
    statements = (
        f'CREATE DATABASE IF NOT EXISTS `{DATABASE}` '
        'DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci',
        f'USE `{DATABASE}`',
        'CREATE TABLE qualification ('
        'id INTEGER NOT NULL PRIMARY KEY, value VARCHAR(100) NOT NULL, '
        'event_date DATE NOT NULL)',
        "INSERT INTO qualification VALUES (1, 'mysql-ui-gate', '2026-01-15')",
        'CREATE TABLE customers ('
        'customer_id INTEGER NOT NULL PRIMARY KEY, '
        'name VARCHAR(100) NOT NULL, '
        'city VARCHAR(100), region VARCHAR(100))',
        "INSERT INTO customers VALUES "
        "(1, 'Northwind Field Lab', 'Toronto', 'ON'), "
        "(2, 'Adventure Works Mining', 'Sudbury', 'ON'), "
        "(3, 'Contoso Marine', 'Halifax', 'NS')",
        'CREATE TABLE pagination_probe ('
        'id INTEGER NOT NULL PRIMARY KEY, label VARCHAR(100) NOT NULL)',
        'CREATE TABLE cde_repair_probe ('
        'id INTEGER NOT NULL PRIMARY KEY, value VARCHAR(100) NOT NULL) '
        'ENGINE=MyISAM',
        "INSERT INTO cde_repair_probe VALUES (1, 'repair-ui-gate')",
        'CREATE VIEW qualification_view AS '
        'SELECT id, value, event_date FROM qualification',
        'CREATE MATERIALIZED VIEW qualification_materialized_view AS '
        'SELECT id, value, event_date FROM qualification',
        'CREATE INDEX qualification_value_idx ON qualification(value)',
        'CREATE TRIGGER qualification_before_insert BEFORE INSERT '
        'ON qualification FOR EACH ROW '
        "SET NEW.value = COALESCE(NEW.value, 'seeded')",
        'CREATE PROCEDURE qualification_procedure() BEGIN SELECT 1; END',
        'CREATE FUNCTION qualification_function() RETURNS INTEGER '
        'DETERMINISTIC RETURN 1',
        'CREATE EVENT qualification_event ON SCHEDULE EVERY 1 DAY '
        'ON COMPLETION PRESERVE DISABLE DO SELECT 1',
        'CREATE TABLE qualification_partition ('
        'id INTEGER NOT NULL, value VARCHAR(100), PRIMARY KEY (id)) '
        'PARTITION BY HASH(id) PARTITIONS 2',
        "CREATE ROLE IF NOT EXISTS 'cdeadmin_qa_role'@'%'",
        "CREATE USER IF NOT EXISTS 'cdeadmin_qa_user'@'%' "
        "IDENTIFIED BY 'fixture-only-password'",
        f"GRANT SELECT ON `{DATABASE}`.* TO 'cdeadmin_qa_role'@'%'",
        "GRANT 'cdeadmin_qa_role'@'%' TO 'cdeadmin_qa_user'@'%'",
        "CHANGE REPLICATION SOURCE TO SOURCE_HOST='127.0.0.1', "
        "SOURCE_PORT=3306, SOURCE_USER='cdeadmin_replication_probe', "
        "SOURCE_PASSWORD='disposable-fixture-only' "
        "FOR CHANNEL 'cdeadmin_ui_probe'",
    )
    try:
        for statement in statements:
            cursor.execute(statement)
        cursor.executemany(
            'INSERT INTO pagination_probe (id, label) VALUES (%s, %s)',
            [(value, f'row-{value:03d}') for value in range(1, 651)],
        )
    finally:
        cursor.close()
        connection.close()


def _retarget_config(database, desktop_user, mysql_port, tool_workspace):
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT s.id, e.id, r.id, r.configuration
              FROM server AS s
              JOIN user AS u ON u.id = s.user_id
              JOIN cde_endpoint AS e ON e.legacy_server_id = s.id
              JOIN cde_endpoint_route AS r
                ON r.endpoint_id = e.id AND r.priority = 0
             WHERE u.email = ? AND e.profile_id = ?
            """,
            (desktop_user, PROFILE_ID),
        ).fetchall()
        if len(row) != 1:
            raise RuntimeError(
                'QA configuration must contain one MySQL 9.7 endpoint'
            )
        server_id, endpoint_id, route_id, route_json = row[0]
        route = json.loads(route_json)
        route.update({
            'host': '127.0.0.1', 'port': mysql_port, 'user': 'root',
            'connection_timeout': 10, 'autocommit': False,
            'charset': 'utf8mb4', 'ssl_disabled': True,
            'ssl_verify_cert': False, 'ssl_verify_identity': False,
            'credential_kinds': ['database_password'],
            'tool_workspace': str(tool_workspace), 'tool_timeout': 3600,
        })
        route.pop('database', None)
        route.pop('password', None)
        connection.execute(
            "UPDATE server SET name = 'localhost', host = '127.0.0.1', "
            'port = ?, username = \'root\', maintenance_db = NULL, '
            'password = NULL, save_password = 0 WHERE id = ?',
            (mysql_port, server_id),
        )
        connection.execute(
            'UPDATE cde_endpoint_route SET configuration = ? WHERE id = ?',
            (json.dumps(route, sort_keys=True, separators=(',', ':')),
             route_id),
        )
        # Keep the endpoint's provider-owned secret reference records.  Their
        # protected columns are blank in this isolated clone, but the records
        # are the capability locators through which verify_server installs the
        # one-run password in the process-local secret resolver.
        connection.execute(
            'UPDATE cde_endpoint_database_target SET active = 0 '
            'WHERE endpoint_id = ?', (endpoint_id,),
        )
        target = connection.execute(
            'SELECT id FROM cde_endpoint_database_target '
            'WHERE endpoint_id = ? AND database = ?',
            (endpoint_id, DATABASE),
        ).fetchone()
        target_id = target[0] if target else str(uuid.uuid4())
        configuration = json.dumps({
            'charset': 'utf8mb4',
            'collation': 'utf8mb4_0900_ai_ci',
            'read_only': False,
        }, sort_keys=True, separators=(',', ':'))
        if target:
            connection.execute(
                'UPDATE cde_endpoint_database_target SET display_name = ?, '
                'configuration = ?, active = 1, '
                'updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (DATABASE, configuration, target_id),
            )
        else:
            connection.execute(
                'INSERT INTO cde_endpoint_database_target '
                '(id, endpoint_id, display_name, database, configuration, '
                'active) VALUES (?, ?, ?, ?, ?, 1)',
                (target_id, endpoint_id, DATABASE, DATABASE, configuration),
            )
        connection.execute(
            "UPDATE cde_endpoint_runtime_identity SET verification_state = "
            "'verified', verified_runtime_family = 'mysql', "
            "verified_runtime_version = '9.7.0', "
            "verified_at = CURRENT_TIMESTAMP, "
            "verification_evidence_reference = 'isolated-ui-gate:mysql-9.7.0' "
            'WHERE endpoint_id = ?', (endpoint_id,),
        )
        connection.execute(
            'UPDATE cde_endpoint SET profile_generation = ? WHERE id = ?',
            (str(uuid.uuid4()), endpoint_id),
        )
        connection.commit()


def _write_config(path, data_dir, user, port):
    path.write_text('\n'.join((
        '"""Generated isolated MySQL 9.7.0 UI gate configuration."""',
        f'DATA_DIR = {str(data_dir)!r}',
        'SERVER_MODE = False',
        f'DESKTOP_USER = {user!r}',
        "DEFAULT_SERVER = '127.0.0.1'",
        f'DEFAULT_SERVER_PORT = {port}',
        'MASTER_PASSWORD_REQUIRED = False',
        'CHECK_EMAIL_DELIVERABILITY = False',
        'SEND_FILE_MAX_AGE_DEFAULT = 0',
        "APP_VERSION_PARAM = 'mysql_970_object_form_gate'",
        '',
    )), encoding='utf-8')


def _image_identity(image):
    document = json.loads(_run([
        'docker', 'image', 'inspect', image, '--format', '{{json .}}',
    ]).stdout)
    return {
        'requested_reference': image,
        'image_id': document.get('Id'),
        'repo_digests': sorted(document.get('RepoDigests') or []),
    }


def run(options):
    if not options.source_config_db.is_file():
        raise RuntimeError(
            f'required source is missing: {options.source_config_db}'
        )
    if mysql.connector.__version__ != '26.7.0':
        raise RuntimeError(
            'MySQL UI gate requires mysql-connector-python 26.7.0'
        )
    source_hash = _sha256(options.source_config_db)
    image_identity = _image_identity(options.image)
    result = None
    infrastructure_failure = None
    cdeadmin_process = None
    cdeadmin_output = None
    container = f'cdeadmin-mysql-ui-{uuid.uuid4().hex[:12]}'
    container_started = False
    mysql_port = _free_port()
    mysql_password = secrets.token_urlsafe(24)
    with tempfile.TemporaryDirectory(
            prefix='cdeadmin-mysql-ui-gate-') as temporary:
        temporary_root = Path(temporary)
        tool_workspace = (
            options.evidence_root / f'_mysql-shell-{container}'
        ).resolve(strict=False)
        tool_workspace.mkdir(parents=True, exist_ok=False)
        data_dir = temporary_root / 'runtime'
        data_dir.mkdir()
        config_database = data_dir / 'cdeadmin.db'
        shutil.copy2(options.source_config_db, config_database)
        try:
            _run([
                'docker', 'run', '-d', '--rm', '--name', container,
                '-e', f'MYSQL_ROOT_PASSWORD={mysql_password}',
                '-p', f'127.0.0.1:{mysql_port}:3306', options.image,
            ])
            container_started = True
            _wait_for_mysql(
                mysql_port, mysql_password, options.startup_timeout
            )
            _seed_mysql(mysql_port, mysql_password)
            _retarget_config(
                config_database, options.desktop_user, mysql_port,
                tool_workspace,
            )
            web_port = _free_port()
            config_file = temporary_root / 'qa_config.py'
            _write_config(
                config_file, data_dir, options.desktop_user, web_port
            )
            environment = os.environ.copy()
            environment['CONFIG_DISTRO_FILE_PATH'] = str(config_file)
            environment['CDEADMIN_MYSQL_UI_GATE_PASSWORD'] = mysql_password
            environment['CDEADMIN_MYSQLSH_BINARY'] = str(
                ROOT / 'tools/reference_engine_demos/runtime/bin/'
                'cdeadmin-mysqlsh-9.7'
            )
            options.server_log.parent.mkdir(parents=True, exist_ok=True)
            cdeadmin_output = options.server_log.open('wb')
            cdeadmin_process = subprocess.Popen(
                [sys.executable, str(ROOT / 'web/CDEadmin.py')],
                cwd=ROOT, env=environment, stdout=cdeadmin_output,
                stderr=subprocess.STDOUT,
            )
            _wait_for_cdeadmin(cdeadmin_process, web_port)
            if options.gate_kind == 'object':
                command = [
                    sys.executable,
                    str(ROOT / 'tools/cdeadmin_provider_object_form_gate.py'),
                    '--url', f'http://127.0.0.1:{web_port}',
                    '--engine', 'MySQL', '--engine-id', ENGINE_ID,
                    '--interface-id', PROFILE_ID,
                    '--reference-version', REFERENCE_VERSION,
                    '--server', 'localhost', '--database', DATABASE,
                    '--output-root', str(options.evidence_root),
                    '--summary-output', str(options.summary_output),
                    '--width', str(options.width),
                    '--height', str(options.height),
                    '--theme', options.theme,
                    '--font-scale', str(options.font_scale),
                    '--timeout', str(options.timeout),
                    '--endpoint-password-env',
                    'CDEADMIN_MYSQL_UI_GATE_PASSWORD',
                ]
            elif options.gate_kind == 'lifecycle':
                command = [
                    sys.executable,
                    str(ROOT /
                        'tools/cdeadmin_mysql_database_lifecycle_ui_gate.py'),
                    '--url', f'http://127.0.0.1:{web_port}',
                    '--config-db', str(config_database),
                    '--database', DATABASE, '--host', '127.0.0.1',
                    '--port', str(mysql_port), '--user', 'root',
                    '--endpoint-password-env',
                    'CDEADMIN_MYSQL_UI_GATE_PASSWORD',
                    '--output-root', str(options.evidence_root),
                    '--summary-output', str(options.summary_output),
                    '--width', str(options.width),
                    '--height', str(options.height),
                    '--theme', options.theme,
                    '--font-scale', str(options.font_scale),
                    '--timeout', str(options.timeout),
                ]
            elif options.gate_kind == 'properties':
                command = [
                    sys.executable,
                    str(ROOT / 'tools/cdeadmin_mysql_properties_ui_gate.py'),
                    '--url', f'http://127.0.0.1:{web_port}',
                    '--database', DATABASE, '--host', '127.0.0.1',
                    '--port', str(mysql_port), '--user', 'root',
                    '--endpoint-password-env',
                    'CDEADMIN_MYSQL_UI_GATE_PASSWORD',
                    '--output-root', str(options.evidence_root),
                    '--summary-output', str(options.summary_output),
                    '--width', str(options.width),
                    '--height', str(options.height),
                    '--theme', options.theme,
                    '--font-scale', str(options.font_scale),
                    '--timeout', str(options.timeout),
                ]
            elif options.gate_kind == 'grid':
                command = [
                    sys.executable,
                    str(ROOT / 'tools/cdeadmin_sqlite_grid_ui_gate.py'),
                    '--url', f'http://127.0.0.1:{web_port}',
                    '--engine', 'MySQL', '--server', 'localhost',
                    '--database', DATABASE, '--host', '127.0.0.1',
                    '--port', str(mysql_port), '--user', 'root',
                    '--endpoint-password-env',
                    'CDEADMIN_MYSQL_UI_GATE_PASSWORD',
                    '--output-root', str(options.evidence_root),
                    '--summary-output', str(options.summary_output),
                    '--width', str(options.width),
                    '--height', str(options.height),
                    '--theme', options.theme,
                    '--font-scale', str(options.font_scale),
                    '--timeout', str(options.timeout),
                ]
            elif options.gate_kind == 'query':
                command = [
                    sys.executable,
                    str(ROOT / 'tools/cdeadmin_sqlite_query_ui_gate.py'),
                    '--url', f'http://127.0.0.1:{web_port}',
                    '--engine', 'MySQL', '--server', 'localhost',
                    '--database', DATABASE, '--host', '127.0.0.1',
                    '--port', str(mysql_port), '--user', 'root',
                    '--endpoint-password-env',
                    'CDEADMIN_MYSQL_UI_GATE_PASSWORD',
                    '--output-root', str(options.evidence_root),
                    '--summary-output', str(options.summary_output),
                    '--width', str(options.width),
                    '--height', str(options.height),
                    '--theme', options.theme,
                    '--font-scale', str(options.font_scale),
                    '--timeout', str(options.timeout),
                ]
            elif options.gate_kind == 'maintenance':
                command = [
                    sys.executable,
                    str(ROOT /
                        'tools/cdeadmin_mysql_maintenance_ui_gate.py'),
                    '--url', f'http://127.0.0.1:{web_port}',
                    '--server', 'localhost', '--database', DATABASE,
                    '--host', '127.0.0.1', '--port', str(mysql_port),
                    '--user', 'root', '--endpoint-password-env',
                    'CDEADMIN_MYSQL_UI_GATE_PASSWORD',
                    '--output-root', str(options.evidence_root),
                    '--summary-output', str(options.summary_output),
                    '--width', str(options.width),
                    '--height', str(options.height),
                    '--theme', options.theme,
                    '--font-scale', str(options.font_scale),
                    '--timeout', str(options.timeout),
                ]
            else:
                command = [
                    sys.executable,
                    str(ROOT /
                        'tools/cdeadmin_mysql_backup_restore_ui_gate.py'),
                    '--url', f'http://127.0.0.1:{web_port}',
                    '--server', 'localhost', '--database', DATABASE,
                    '--host', '127.0.0.1', '--port', str(mysql_port),
                    '--user', 'root', '--endpoint-password-env',
                    'CDEADMIN_MYSQL_UI_GATE_PASSWORD',
                    '--tool-workspace', str(tool_workspace),
                    '--output-root', str(options.evidence_root),
                    '--summary-output', str(options.summary_output),
                    '--width', str(options.width),
                    '--height', str(options.height),
                    '--theme', options.theme,
                    '--font-scale', str(options.font_scale),
                    '--timeout', str(options.timeout),
                ]
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
                raise RuntimeError(
                    'browser gate failed; see the isolated browser log'
                )
        except Exception as exc:
            infrastructure_failure = {
                'error_type': type(exc).__name__, 'message': str(exc),
            }
        finally:
            if cdeadmin_process is not None:
                cdeadmin_process.terminate()
                try:
                    cdeadmin_process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    cdeadmin_process.kill()
                    cdeadmin_process.wait(timeout=15)
            if cdeadmin_output is not None:
                cdeadmin_output.close()
            if container_started:
                logs = _run(['docker', 'logs', container], check=False)
                mysql_log = options.server_log.with_name(
                    options.server_log.stem + '-mysql.log'
                )
                mysql_log.write_text(
                    logs.stdout + logs.stderr, encoding='utf-8'
                )
                _run(['docker', 'stop', container], check=False)
    source_unchanged = (
        options.source_config_db.is_file() and
        _sha256(options.source_config_db) == source_hash
    )
    browser_complete = bool(result and (
        result.get('complete') is True or result.get('passed') is True
    ))
    complete = (
        infrastructure_failure is None and source_unchanged and
        browser_complete and
        (cdeadmin_process is None or cdeadmin_process.returncode is not None)
    )
    return {
        'schema': 'cdeadmin.mysql-ui-orchestrator.v1',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'engine_id': ENGINE_ID, 'interface_id': PROFILE_ID,
        'reference_version': REFERENCE_VERSION,
        'runtime_image': image_identity,
        'isolated_config_clone': True,
        'source_config_sha256': source_hash,
        'source_config_unchanged': source_unchanged,
        'isolated_runtime_container': True,
        'server_stopped': (
            cdeadmin_process is None or cdeadmin_process.returncode is not None
        ),
        'container_stopped_and_removed': container_started,
        'temporary_runtime_removed': True,
        'browser_summary': str(options.summary_output),
        'browser_complete': browser_complete,
        'infrastructure_failure': infrastructure_failure,
        'credential_values_exported': False,
        'complete': complete,
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
        'source_config_unchanged': result['source_config_unchanged'],
        'server_stopped': result['server_stopped'],
        'output': str(options.output),
    }, indent=2, sort_keys=True))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
