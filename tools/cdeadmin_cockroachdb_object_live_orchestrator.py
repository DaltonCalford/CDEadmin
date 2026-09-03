#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify CockroachDB object editors in an exact secure container."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cdeadmin_relational_provider_live_verify import (  # noqa: E402
    _CockroachDBAccount,
    verify,
)


IMAGE = 'cockroachdb/cockroach:v26.1.3'
PORT = 26257


def _run(arguments, *, check=True):
    return subprocess.run(
        arguments, check=check, capture_output=True, text=True,
    )


def _published_port(value):
    for line in value.splitlines():
        try:
            port = int(line.strip().rsplit(':', 1)[1])
        except (IndexError, ValueError):
            continue
        if 0 < port < 65536:
            return port
    raise RuntimeError('container did not publish a usable SQL port')


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


def _certificate_command(image, certificate_root, arguments):
    completed = _run([
        'docker', 'run', '--rm',
        '--user', f'{os.getuid()}:{os.getgid()}',
        '--entrypoint', '/cockroach/cockroach',
        '-v', f'{certificate_root}:/certs', image,
        'cert', *arguments, '--certs-dir=/certs',
        '--ca-key=/certs/ca.key',
    ])
    if completed.stdout or completed.stderr:
        # Certificate commands should not disclose key material, but their
        # routine output is unnecessary qualification evidence.
        return


def _create_certificates(image, certificate_root):
    _certificate_command(image, certificate_root, ['create-ca'])
    _certificate_command(
        image, certificate_root,
        ['create-node', 'localhost', '127.0.0.1'],
    )
    _certificate_command(image, certificate_root, ['create-client', 'root'])


def _wait_until_ready(container, certificate_root, timeout):
    import psycopg

    deadline = time.monotonic() + timeout
    port = None
    while time.monotonic() < deadline:
        state = _run([
            'docker', 'inspect', '-f', '{{.State.Running}}', container,
        ], check=False)
        if state.returncode or state.stdout.strip() != 'true':
            raise RuntimeError(
                'CockroachDB qualification container stopped during startup'
            )
        if port is None:
            published = _run([
                'docker', 'port', container, f'{PORT}/tcp',
            ], check=False)
            if published.returncode == 0:
                try:
                    port = _published_port(published.stdout)
                except RuntimeError:
                    port = None
        if port is not None:
            try:
                connection = psycopg.connect(
                    host='127.0.0.1', port=port, user='root',
                    dbname='defaultdb', sslmode='verify-full',
                    sslrootcert=str(certificate_root / 'ca.crt'),
                    sslcert=str(certificate_root / 'client.root.crt'),
                    sslkey=str(certificate_root / 'client.root.key'),
                    connect_timeout=3,
                )
                connection.close()
                return port
            except Exception:
                pass
        time.sleep(1)
    raise RuntimeError(
        'CockroachDB qualification container did not become ready'
    )


def run(image, server_log, work_root, startup_timeout=300):
    identity = _image_identity(image)
    container = f'cdeadmin-cockroachdb-object-{secrets.token_hex(6)}'
    started = False
    result = None
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
            prefix='cockroachdb-secure-', dir=str(work_root.resolve())) as tmp:
        certificate_root = Path(tmp)
        _create_certificates(image, certificate_root)
        try:
            _run([
                'docker', 'run', '-d', '--rm', '--name', container,
                '--entrypoint', '/cockroach/cockroach',
                '-p', f'127.0.0.1::{PORT}',
                '-v', f'{certificate_root}:/certs', image,
                'start-single-node', '--certs-dir=/certs',
                '--listen-addr=0.0.0.0:26257',
                '--advertise-addr=localhost:26257',
                '--http-addr=0.0.0.0:8080',
                '--store=/cockroach/cockroach-data',
            ])
            started = True
            port = _wait_until_ready(
                container, certificate_root, startup_timeout
            )
            account = _CockroachDBAccount(
                '127.0.0.1', port,
                certificate_root / 'ca.crt',
                certificate_root / 'client.root.crt',
                certificate_root / 'client.root.key',
            )
            result = verify(
                'cockroachdb', '127.0.0.1', port, account=account
            )
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
    result['tls_bootstrap'] = 'ephemeral-client-certificate'
    result['tls_private_material_exported'] = False
    result['server_stopped'] = True
    result['credential_values_exported'] = False
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default=IMAGE)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--object-output', type=Path, required=True)
    parser.add_argument('--server-log', type=Path, required=True)
    parser.add_argument('--work-root', type=Path)
    parser.add_argument('--startup-timeout', type=int, default=300)
    options = parser.parse_args(argv)
    work_root = options.work_root or options.output.parent
    result = run(
        options.image, options.server_log, work_root,
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
        'tls_private_material_exported': False,
        'server_stopped': result['server_stopped'],
    }, indent=2, sort_keys=True))
    return 0 if result['activation_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
