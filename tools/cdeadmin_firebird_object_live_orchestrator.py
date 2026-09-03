#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify Firebird object editors with an isolated exact runtime clone."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cdeadmin_relational_provider_live_verify import (  # noqa: E402
    _FirebirdAccount,
    verify,
)


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        return int(listener.getsockname()[1])


def _runtime_fingerprint(root):
    """Hash runtime paths and content without following staged links."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix().encode('utf-8')
        digest.update(len(relative).to_bytes(8, 'big'))
        digest.update(relative)
        if path.is_symlink():
            digest.update(b'L')
            target = os.readlink(path).encode('utf-8')
            digest.update(len(target).to_bytes(8, 'big'))
            digest.update(target)
        elif path.is_file():
            digest.update(b'F')
            with path.open('rb') as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
        elif path.is_dir():
            digest.update(b'D')
    return digest.hexdigest()


def _stage_runtime(source, target):
    """Copy writable identity/config state and link immutable runtime data."""
    target.mkdir(parents=True)
    for name in ('firebird.conf', 'databases.conf', 'plugins.conf'):
        shutil.copy2(source / name, target / name)
    shutil.copy2(source / 'security5.fdb', target / 'security5.fdb')
    for name in ('bin', 'intl', 'lib', 'plugins', 'tzdata'):
        (target / name).symlink_to(source / name, target_is_directory=True)
    for name in ('firebird.msg', 'replication.conf'):
        (target / name).symlink_to(source / name)
    (target / 'lock').mkdir()


def _initialize_security(runtime, password, environment):
    bootstrap = runtime / 'bootstrap.fdb'
    source = (
        f"CREATE DATABASE '{bootstrap}' USER 'SYSDBA';\n"
        f"CREATE USER SYSDBA PASSWORD '{password}';\n"
        'COMMIT;\nDROP DATABASE;\nQUIT;\n'
    )
    completed = subprocess.run(
        [str(runtime / 'bin/isql'), '-user', 'SYSDBA'],
        input=source,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError('Firebird security initialization failed')


def _wait_for_server(process, port, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Firebird server stopped during startup')
        try:
            with socket.create_connection(
                    ('127.0.0.1', port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError('Firebird server did not become ready')


def run(runtime_source, server_log):
    runtime_source = runtime_source.resolve()
    required = (
        runtime_source / 'bin/firebird',
        runtime_source / 'bin/isql',
        runtime_source / 'security5.fdb',
    )
    if not all(path.is_file() for path in required):
        raise RuntimeError('Firebird runtime source is incomplete')
    source_fingerprint = _runtime_fingerprint(runtime_source)
    with tempfile.TemporaryDirectory(
            prefix='cdeadmin-firebird-object-live-') as temporary:
        root = Path(temporary)
        runtime = root / 'runtime'
        _stage_runtime(runtime_source, runtime)
        password = secrets.token_urlsafe(24)
        environment = os.environ.copy()
        environment['FIREBIRD'] = str(runtime)
        environment['FIREBIRD_LOCK'] = str(runtime / 'lock')
        _initialize_security(runtime, password, environment)
        port = _free_port()
        database = root / 'qualification.fdb'
        server_log.parent.mkdir(parents=True, exist_ok=True)
        with server_log.open('wb') as output:
            process = subprocess.Popen(
                [
                    str(runtime / 'bin/firebird'), '-d', '-p', str(port),
                    '-e', str(runtime), '-el', str(runtime / 'lock'),
                    '-em', str(runtime),
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                env=environment,
            )
            try:
                _wait_for_server(process, port)
                account = _FirebirdAccount(
                    '127.0.0.1', port, database, 'SYSDBA', password
                )
                result = verify(
                    'firebird', '127.0.0.1', port, account=account
                )
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        password = ''
        final_source_fingerprint = _runtime_fingerprint(runtime_source)
        source_modified = source_fingerprint != final_source_fingerprint
        result['isolated_runtime_clone'] = True
        result['source_runtime_sha256'] = source_fingerprint
        result['source_runtime_modified'] = source_modified
        result['server_stopped'] = process.returncode is not None
        result['credential_values_exported'] = False
        if source_modified:
            result['activation_ready'] = False
            result['error_type'] = 'SourceRuntimeModified'
            result['error_message'] = (
                'preserved Firebird runtime changed during qualification'
            )
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--object-output', type=Path, required=True)
    parser.add_argument('--server-log', type=Path, required=True)
    options = parser.parse_args(argv)
    result = run(options.runtime_source, options.server_log)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    options.object_output.parent.mkdir(parents=True, exist_ok=True)
    options.object_output.write_text(
        json.dumps(
            result['object_experience_evidence'],
            indent=2,
            sort_keys=True,
        ) + '\n',
        encoding='utf-8',
    )
    print(json.dumps({
        'engine_id': result['engine_id'],
        'exact_profile': result['exact_profile'],
        'activation_ready': result['activation_ready'],
        'object_operation_failures': result[
            'object_experience_evidence']['operation_failures'],
        'credential_values_exported': False,
        'server_stopped': result['server_stopped'],
    }, indent=2, sort_keys=True))
    return 0 if result['activation_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
