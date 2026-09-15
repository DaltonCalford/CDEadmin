#!/usr/bin/env python3
"""Run real Firebird rename editors in a separately owned native server."""

import argparse
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path

if __package__:
    from .cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _configure_client_library,
    )
    from .cdeadmin_firebird_external_functions_gate import browser_checks
else:
    from cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _configure_client_library,
    )
    from cdeadmin_firebird_external_functions_gate import browser_checks

from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes


def run(options):
    if options.kind not in ('domain', 'column'):
        raise ValueError('Choose a native domain or column editor')
    options.build_root.mkdir(parents=True, exist_ok=False)
    import firebird.driver as native
    _configure_client_library(native)
    container = None
    password = secrets.token_urlsafe(24)
    path = '/var/lib/firebird/data/owned_rename.fdb'
    result = {'complete': False, 'kind': options.kind, 'browser_checks': [],
              'failures': [], 'owned_container_removed': False}
    previous_kind = os.environ.get('CDEADMIN_FIREBIRD_RENAME_KIND')

    def failure(phase, error):
        result['failures'].append({
            'phase': phase, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error))})

    phase = 'create-owned-server'
    try:
        container = docker(
            'run', '--detach', '--name',
            'cdeadmin-rename-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--memory', '512m', '--memory-swap', '512m',
            '--publish', '127.0.0.1::3050',
            '--env', 'FIREBIRD_ROOT_PASSWORD', '--env', 'FIREBIRD_DATABASE',
            options.image, env=dict(
                os.environ, FIREBIRD_ROOT_PASSWORD=password,
                FIREBIRD_DATABASE=path)).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise ValueError('Owned container identity is invalid')
        port = published_port(container)
        phase = 'native-readiness'
        deadline = time.monotonic() + 45
        while True:
            try:
                with native.connect(f'127.0.0.1/{port}:{path}', user='SYSDBA',
                                    password=password) as handle:
                    with handle.cursor() as cursor:
                        cursor.execute("SELECT RDB$GET_CONTEXT('SYSTEM', "
                                       "'ENGINE_VERSION') FROM RDB$DATABASE")
                        if cursor.fetchone()[0] != '5.0.4':
                            raise RuntimeError('Owned engine version differs')
                        result['native_version_verified'] = '5.0.4'
                break
            except native.Error:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        phase = 'browser-rename-editors'
        os.environ['CDEADMIN_FIREBIRD_RENAME_KIND'] = options.kind
        result['browser_checks'] = browser_checks(
            options, {'host': '127.0.0.1', 'port': port,
                      'database': path, 'user': 'SYSDBA'},
            password, container, options.build_root, gate_kind='rename',
            fixture_kind='firebird-rename-qualification')
        if not result['browser_checks'] or not all(
                item['passed'] for item in result['browser_checks']):
            raise RuntimeError('Native rename editor qualification failed')
    except Exception as error:
        failure(phase, error)
    finally:
        if previous_kind is None:
            os.environ.pop('CDEADMIN_FIREBIRD_RENAME_KIND', None)
        else:
            os.environ['CDEADMIN_FIREBIRD_RENAME_KIND'] = previous_kind
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                failure('cleanup', error)
    result['complete'] = (bool(result['browser_checks']) and
                          all(item['passed'] for item in
                              result['browser_checks']) and
                          not result['failures'] and
                          result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--kind', choices=('domain', 'column'), required=True)
    parser.add_argument('--build-root', type=Path, required=True)
    parser.add_argument('--source-config-db', type=Path,
                        default=Path('/var/lib/cdeadmin/cdeadmin.db'))
    parser.add_argument('--desktop-user', required=True)
    parser.add_argument('--font-scale', type=int, action='append',
                        choices=(100, 200, 300))
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        parser.error('Use a new evidence file')
    result = run(options)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
