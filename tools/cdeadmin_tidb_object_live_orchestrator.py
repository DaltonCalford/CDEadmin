#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify TiDB object editors in an isolated exact-version container."""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cdeadmin_relational_provider_live_verify import verify  # noqa: E402


IMAGE = 'pingcap/tidb:v8.5.6'
PORT = 4000


def _run(arguments, *, check=True):
    return subprocess.run(
        arguments, check=check, capture_output=True, text=True,
    )


def _published_port(value):
    for line in (item.strip() for item in value.splitlines()):
        if not line:
            continue
        try:
            port = int(line.rsplit(':', 1)[1])
        except (IndexError, ValueError):
            continue
        if 0 < port < 65536:
            return port
    raise RuntimeError('container did not publish a usable TiDB SQL port')


def _wait_until_ready(container, timeout):
    import mysql.connector

    deadline = time.monotonic() + timeout
    port = None
    while time.monotonic() < deadline:
        state = _run([
            'docker', 'inspect', '-f', '{{.State.Running}}', container,
        ], check=False)
        if state.returncode or state.stdout.strip() != 'true':
            raise RuntimeError(
                'TiDB qualification container stopped during startup'
            )
        if port is None:
            published = _run(
                ['docker', 'port', container, f'{PORT}/tcp'], check=False,
            )
            if published.returncode == 0:
                try:
                    port = _published_port(published.stdout)
                except RuntimeError:
                    port = None
        if port is not None:
            try:
                connection = mysql.connector.connect(
                    user='root', host='127.0.0.1', port=port,
                    connection_timeout=2,
                )
                connection.close()
                return port
            except Exception:
                pass
        time.sleep(1)
    raise RuntimeError('TiDB qualification container did not become ready')


def _image_identity(image):
    completed = _run([
        'docker', 'image', 'inspect', image, '--format', '{{json .}}',
    ])
    document = json.loads(completed.stdout)
    return {
        'requested_reference': image,
        'image_id': document.get('Id'),
        'repo_digests': sorted(document.get('RepoDigests') or []),
    }


def run(image, server_log, startup_timeout=300):
    identity = _image_identity(image)
    container = f'cdeadmin-tidb-object-{secrets.token_hex(6)}'
    started = False
    result = None
    try:
        _run([
            'docker', 'run', '-d', '--rm', '--name', container,
            '-p', f'127.0.0.1::{PORT}', image,
            '--store=unistore', '--path=/tmp/tidb', '--host=0.0.0.0',
            '--status=10080',
        ])
        started = True
        port = _wait_until_ready(container, startup_timeout)
        result = verify('tidb', '127.0.0.1', port)
    finally:
        server_log.parent.mkdir(parents=True, exist_ok=True)
        if started:
            logs = _run(['docker', 'logs', container], check=False)
            server_log.write_text(
                logs.stdout + logs.stderr, encoding='utf-8'
            )
            _run(['docker', 'stop', container], check=False)
        elif not server_log.exists():
            server_log.write_text('', encoding='utf-8')
    if result is None:
        raise RuntimeError('qualification completed without provider evidence')
    result['isolated_runtime_container'] = True
    result['runtime_image'] = identity
    result['server_stopped'] = True
    result['credential_values_exported'] = False
    result['runtime_scope'] = 'single-node unistore SQL object semantics'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default=IMAGE)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--object-output', type=Path, required=True)
    parser.add_argument('--server-log', type=Path, required=True)
    parser.add_argument('--startup-timeout', type=int, default=300)
    options = parser.parse_args(argv)
    result = run(
        options.image, options.server_log,
        startup_timeout=options.startup_timeout,
    )
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    options.object_output.parent.mkdir(parents=True, exist_ok=True)
    options.object_output.write_text(
        json.dumps(
            result['object_experience_evidence'], indent=2, sort_keys=True,
        ) + '\n', encoding='utf-8',
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
