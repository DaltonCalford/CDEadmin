#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Run the SQLite lifecycle UI gate in an isolated 3.53.0 runtime."""

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
    from .cdeadmin_sqlite_ui_orchestrator import (
        ROOT,
        _free_port,
        _prepare_database,
        _retarget_config,
        _wait_for_server,
        _write_config,
    )
else:
    from cdeadmin_sqlite_ui_orchestrator import (
        ROOT,
        _free_port,
        _prepare_database,
        _retarget_config,
        _wait_for_server,
        _write_config,
    )


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-config-db', type=Path, required=True)
    parser.add_argument('--source-database', type=Path, required=True)
    parser.add_argument('--desktop-user', required=True)
    parser.add_argument('--sqlite-library-dir', type=Path, required=True)
    parser.add_argument('--browser-binary')
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


def run(options):
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
            prefix='cdeadmin-sqlite-lifecycle-') as temporary:
        temporary_root = Path(temporary)
        data_dir = temporary_root / 'runtime'
        data_dir.mkdir()
        config_database = data_dir / 'cdeadmin.db'
        database = temporary_root / 'cdeadmin_sqlite_ui.sqlite'
        attached = temporary_root / 'cdeadmin_sqlite_archive.sqlite'
        shutil.copy2(options.source_config_db, config_database)
        _prepare_database(options.source_database, database, attached)
        _retarget_config(
            config_database, options.desktop_user, database, attached
        )
        port = _free_port()
        config_file = temporary_root / 'qa_config.py'
        _write_config(config_file, data_dir, options.desktop_user, port)
        environment = os.environ.copy()
        environment['CONFIG_DISTRO_FILE_PATH'] = str(config_file)
        current_library_path = environment.get('LD_LIBRARY_PATH', '')
        library_path = str(options.sqlite_library_dir.resolve())
        environment['LD_LIBRARY_PATH'] = ':'.join(
            item for item in (library_path, current_library_path) if item
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
            command = [
                sys.executable,
                str(ROOT / 'tools/'
                    'cdeadmin_sqlite_database_lifecycle_ui_gate.py'),
                '--url', f'http://127.0.0.1:{port}',
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
            if options.browser_binary:
                command.extend(['--browser-binary', options.browser_binary])
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
            if completed.returncode != 0 and result is None:
                raise RuntimeError('browser gate failed before summary output')
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
    return {
        'schema': 'cdeadmin.sqlite-lifecycle-orchestrator.v1',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'engine_id': 'sqlite', 'interface_id': 'sqlite-native',
        'reference_version': '3.53.0',
        'sqlite_library_directory': str(options.sqlite_library_dir),
        'isolated_config_clone': True,
        'isolated_database_clone': True,
        'source_hashes': source_hashes,
        'sources_unchanged': source_unchanged,
        'server_stopped': process is None or process.returncode is not None,
        'browser_summary': str(options.summary_output),
        'browser_complete': bool(result and result.get('complete')),
        'infrastructure_failure': infrastructure_failure,
        'temporary_runtime_removed': True,
        'complete': (
            infrastructure_failure is None and source_unchanged and
            bool(result and result.get('complete')) and
            (process is None or process.returncode is not None)
        ),
    }


def main(argv=None):
    options = arguments(argv)
    result = run(options)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8'
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
