#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Run Firebird browser gates through an isolated CDEadmin configuration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from .cdeadmin_firebird_ui_completed_orchestrator import (
        ROOT,
        _free_port,
        _retarget_config,
        _wait_for_server,
        _write_config,
    )
else:
    from cdeadmin_firebird_ui_completed_orchestrator import (
        ROOT,
        _free_port,
        _retarget_config,
        _wait_for_server,
        _write_config,
    )


GATE_SCRIPTS = {
    'inspector-tabs': 'cdeadmin_firebird_inspector_tabs_ui_gate.py',
    'rename': 'cdeadmin_firebird_rename_ui_gate.py',
    'table-metadata': 'cdeadmin_firebird_table_metadata_ui_gate.py',
    'privileges': 'cdeadmin_firebird_privileges_ui_gate.py',
    'columns': 'cdeadmin_firebird_columns_ui_gate.py',
    'mapping': 'cdeadmin_firebird_admin_mapping_ui_gate.py',
    'mappings': 'cdeadmin_firebird_mappings_ui_gate.py',
    'role': 'cdeadmin_firebird_role_ui_gate.py',
    'object': 'cdeadmin_provider_object_form_gate.py',
    'grid': 'cdeadmin_firebird_grid_ui_gate.py',
    'query': 'cdeadmin_firebird_query_ui_gate.py',
    'services': 'cdeadmin_firebird_services_ui_gate.py',
    'backup-history': 'cdeadmin_firebird_backup_history_ui_gate.py',
    'lifecycle': 'cdeadmin_firebird_database_lifecycle_ui_gate.py',
    'properties': 'cdeadmin_firebird_properties_ui_gate.py',
}


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-config-db', type=Path, required=True)
    parser.add_argument('--desktop-user', required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--firebird-port', type=int, default=53050)
    parser.add_argument('--database', required=True)
    parser.add_argument('--user', default='SYSDBA')
    parser.add_argument(
        '--password-env', default='CDEADMIN_FIREBIRD_DEMO_PASSWORD'
    )
    parser.add_argument('--client-library', type=Path, required=True)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--browser-binary')
    parser.add_argument('--gate-kind', choices=tuple(GATE_SCRIPTS),
                        required=True)
    parser.add_argument(
        '--resource-kind', dest='resource_kinds', action='append',
        help='Limit the object-form gate to this Firebird resource kind.',
    )
    parser.add_argument(
        '--operation-id', dest='operation_ids', action='append',
        help='Limit the object-form gate to this operation identifier.',
    )
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--summary-output', type=Path, required=True)
    parser.add_argument('--manifest-output', type=Path, required=True)
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


def gate_command(options, url, database_label, config_database=None):
    command = [
        sys.executable,
        str(ROOT / 'tools' / GATE_SCRIPTS[options.gate_kind]),
        '--url', url,
        '--engine', 'Firebird',
        '--server', 'localhost',
        '--database', database_label,
        '--output-root', str(options.evidence_root),
        '--summary-output', str(options.summary_output),
        '--width', str(options.width),
        '--height', str(options.height),
        '--timeout', str(options.timeout),
        '--theme', options.theme,
        '--font-scale', str(options.font_scale),
    ]
    if options.gate_kind in {
            'object', 'role', 'mapping', 'mappings', 'columns',
            'table-metadata', 'privileges', 'rename'}:
        command.extend([
            '--engine-id', 'firebird',
            '--interface-id', 'firebird-native',
            '--reference-version', '5.0.4',
            '--endpoint-password-env', options.password_env,
        ])
        for resource_kind in options.resource_kinds or ():
            command.extend(['--resource-kind', resource_kind])
        for operation_id in options.operation_ids or ():
            command.extend(['--operation-id', operation_id])
        if options.gate_kind in {'role', 'mapping', 'mappings', 'columns',
                                 'table-metadata', 'privileges', 'rename'}:
            command.extend(['--profiles', str(options.profiles),
                            '--database-path', options.database])
    elif options.gate_kind in {'grid', 'query', 'services', 'backup-history'}:
        command.extend([
            '--profiles', str(options.profiles),
            '--manifest-output', str(options.manifest_output),
        ])
    elif options.gate_kind in {'lifecycle', 'inspector-tabs'}:
        if config_database is None:
            raise RuntimeError(
                'lifecycle gate requires an isolated configuration database'
            )
        command.extend([
            '--config-db', str(config_database),
            '--database-root', str(Path(options.database).parent),
            '--host', options.host,
            '--firebird-port', str(options.firebird_port),
            '--user', options.user,
            '--password-env', options.password_env,
            '--client-library', str(options.client_library),
        ])
    elif options.gate_kind == 'properties':
        command.extend([
            '--database-path', options.database,
            '--host', options.host,
            '--firebird-port', str(options.firebird_port),
            '--user', options.user,
            '--endpoint-password-env', options.password_env,
            '--client-library', str(options.client_library),
        ])
    if options.browser_binary:
        command.extend(['--browser-binary', options.browser_binary])
    return command


def run(options):
    if not options.source_config_db.is_file():
        raise RuntimeError('source QA configuration database is missing')
    if not options.client_library.is_file():
        raise RuntimeError('Firebird client library is missing')
    if not options.profiles.is_file():
        raise RuntimeError('reference connection profiles are missing')
    if not os.environ.get(options.password_env):
        raise RuntimeError(
            f'{options.password_env} must contain the test credential'
        )
    source_hash = _sha256(options.source_config_db)
    result = None
    infrastructure_failure = None
    process = None
    server_output = None
    with tempfile.TemporaryDirectory(
            prefix='cdeadmin-firebird-ui-gate-') as temporary:
        temporary_root = Path(temporary)
        data_dir = temporary_root / 'runtime'
        data_dir.mkdir()
        config_database = data_dir / 'cdeadmin.db'
        shutil.copy2(options.source_config_db, config_database)
        database_label = Path(options.database).name
        _retarget_config(
            config_database, options.desktop_user, options.database,
            database_label, options.firebird_port,
        )
        port = _free_port()
        config_file = temporary_root / 'qa_config.py'
        _write_config(config_file, data_dir, options.desktop_user, port)
        environment = os.environ.copy()
        environment['CONFIG_DISTRO_FILE_PATH'] = str(config_file)
        environment['CDEADMIN_FIREBIRD_CLIENT_LIBRARY'] = str(
            options.client_library.resolve()
        )
        options.server_log.parent.mkdir(parents=True, exist_ok=True)
        server_output = options.server_log.open('wb')
        try:
            process = subprocess.Popen(
                [sys.executable, str(ROOT / 'web/CDEadmin.py')],
                cwd=ROOT, env=environment, stdout=server_output,
                stderr=subprocess.STDOUT,
            )
            _wait_for_server(process, port)
            options.summary_output.unlink(missing_ok=True)
            options.browser_log.parent.mkdir(parents=True, exist_ok=True)
            with options.browser_log.open('w', encoding='utf-8') as log:
                completed = subprocess.run(
                    gate_command(
                        options, f'http://127.0.0.1:{port}', database_label,
                        config_database,
                    ),
                    cwd=ROOT, env=environment, stdout=log,
                    stderr=subprocess.STDOUT, text=True, check=False,
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
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
            if server_output is not None:
                server_output.close()
    source_unchanged = (
        options.source_config_db.is_file() and
        _sha256(options.source_config_db) == source_hash
    )
    browser_complete = bool(
        result and (result.get('complete') or result.get('passed'))
    )
    return {
        'schema': 'cdeadmin.firebird-ui-orchestrator.v1',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'engine_id': 'firebird',
        'interface_id': 'firebird-native',
        'reference_version': '5.0.4',
        'gate_kind': options.gate_kind,
        'isolated_config_clone': True,
        'source_config_sha256': source_hash,
        'source_config_unchanged': source_unchanged,
        'packaged_sample_database_used': True,
        'credential_values_exported': False,
        'server_stopped': process is None or process.returncode is not None,
        'browser_summary': str(options.summary_output),
        'browser_complete': browser_complete,
        'infrastructure_failure': infrastructure_failure,
        'temporary_runtime_removed': True,
        'complete': (
            infrastructure_failure is None and source_unchanged and
            browser_complete and
            (process is None or process.returncode is not None)
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
        'gate_kind': result['gate_kind'],
        'browser_complete': result['browser_complete'],
        'source_config_unchanged': result['source_config_unchanged'],
        'credential_values_exported': False,
        'output': str(options.output),
    }, indent=2, sort_keys=True))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
