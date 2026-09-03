#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify MySQL-family object editors in isolated exact containers."""

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


IMAGES = {
    'mysql': 'mysql:9.7.0',
    'mariadb': 'mariadb:12.2.2',
}
PORT = 3306


def _run(arguments, *, check=True):
    return subprocess.run(
        arguments, check=check, capture_output=True, text=True,
    )


def _published_port(value):
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    for line in lines:
        try:
            port = int(line.rsplit(':', 1)[1])
        except (IndexError, ValueError):
            continue
        if 0 < port < 65536:
            return port
    raise RuntimeError('container did not publish a usable database port')


def _root_connection(engine, port):
    if engine == 'mysql':
        import mysql.connector
        return mysql.connector.connect(
            user='root', host='127.0.0.1', port=port,
            connection_timeout=2,
        )
    import mariadb
    return mariadb.connect(
        user='root', host='127.0.0.1', port=port,
        connect_timeout=2,
    )


def _wait_until_ready(engine, container, timeout):
    deadline = time.monotonic() + timeout
    port = None
    while time.monotonic() < deadline:
        state = _run(
            ['docker', 'inspect', '-f', '{{.State.Running}}', container],
            check=False,
        )
        if state.returncode or state.stdout.strip() != 'true':
            raise RuntimeError(
                f'{engine} qualification container stopped during startup'
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
                connection = _root_connection(engine, port)
                connection.close()
                return port
            except Exception:
                pass
        time.sleep(1)
    raise RuntimeError(
        f'{engine} qualification container did not become ready'
    )


def _image_identity(image):
    completed = _run([
        'docker', 'image', 'inspect', image, '--format',
        '{{json .}}',
    ])
    document = json.loads(completed.stdout)
    return {
        'requested_reference': image,
        'image_id': document.get('Id'),
        'repo_digests': sorted(document.get('RepoDigests') or []),
    }


def run(engine, image, server_log, startup_timeout=300):
    if engine not in IMAGES:
        raise ValueError('unsupported MySQL-family qualification engine')
    identity = _image_identity(image)
    container = (
        f'cdeadmin-{engine}-object-{secrets.token_hex(6)}'
    )
    environment = (
        'MYSQL_ALLOW_EMPTY_PASSWORD=yes'
        if engine == 'mysql' else
        'MARIADB_ALLOW_EMPTY_ROOT_PASSWORD=1'
    )
    started = False
    result = None
    try:
        _run([
            'docker', 'run', '-d', '--rm', '--name', container,
            '-e', environment, '-p', f'127.0.0.1::{PORT}', image,
        ])
        started = True
        port = _wait_until_ready(engine, container, startup_timeout)
        result = verify(engine, '127.0.0.1', port)
    finally:
        server_log.parent.mkdir(parents=True, exist_ok=True)
        if started:
            logs = _run(
                ['docker', 'logs', container], check=False,
            )
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
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', choices=sorted(IMAGES), required=True)
    parser.add_argument('--image')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--object-output', type=Path, required=True)
    parser.add_argument('--server-log', type=Path, required=True)
    parser.add_argument('--startup-timeout', type=int, default=300)
    options = parser.parse_args(argv)
    result = run(
        options.engine, options.image or IMAGES[options.engine],
        options.server_log, startup_timeout=options.startup_timeout,
    )
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
