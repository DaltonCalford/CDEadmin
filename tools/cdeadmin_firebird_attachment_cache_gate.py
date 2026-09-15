#!/usr/bin/env python3
"""Native cache precedence in three owned Firebird server modes."""

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
else:
    from cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _configure_client_library,
    )

from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes


SERVER_MODES = ('Super', 'SuperClassic', 'Classic')
CACHE_REQUESTS = (None, 0, 1, 24, 25, 49, 50, 128, 256)


def run(image):
    import firebird.driver as native
    _configure_client_library(native)
    result = {'complete': False, 'checks': [], 'failures': [],
              'removed_server_modes': [], 'provider_forms_qualified': False}

    def failure(case, error):
        result['failures'].append({
            'case': case, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error))})

    for mode in SERVER_MODES:
        container = None
        password = secrets.token_urlsafe(24)
        path = '/var/lib/firebird/data/owned_cache.fdb'
        phase = mode + '-create-owned-server'
        try:
            container = docker(
                'run', '--detach', '--name',
                'cdeadmin-cache-' + uuid.uuid4().hex[:16],
                '--label', 'cdeadmin-owned-gate=' + OWNER,
                '--memory', '512m', '--memory-swap', '512m',
                '--publish', '127.0.0.1::3050',
                '--env', 'FIREBIRD_ROOT_PASSWORD',
                '--env', 'FIREBIRD_DATABASE',
                '--env', 'FIREBIRD_CONF_ServerMode',
                '--env', 'FIREBIRD_CONF_DefaultDbCachePages', image,
                env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                         FIREBIRD_DATABASE=path, FIREBIRD_CONF_ServerMode=mode,
                         FIREBIRD_CONF_DefaultDbCachePages='128')
            ).decode().strip()
            if not re.fullmatch('[0-9a-f]{64}', container):
                raise ValueError('Owned container identity is invalid')
            port = published_port(container)
            dsn = f'127.0.0.1/{port}:{path}'

            def connect(request=None):
                name = 'owned_cache_' + uuid.uuid4().hex
                config = native.driver_config.register_database(name)
                config.dsn.value = dsn
                config.user.value = None
                config.password.value = None
                config.cache_size.value = request
                return native.connect(name, user='SYSDBA', password=password)

            phase = mode + '-readiness'
            deadline = time.monotonic() + 45
            while True:
                try:
                    with connect() as handle:
                        with handle.cursor() as cursor:
                            cursor.execute(
                                "SELECT RDB$GET_CONTEXT('SYSTEM', "
                                "'ENGINE_VERSION') FROM RDB$DATABASE")
                            version = cursor.fetchone()[0]
                    assert version == '5.0.4'
                    break
                except native.Error:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.25)
            for stored in (0, 64):
                phase = f'{mode}-stored-cache-{stored}'
                with native.connect_server(
                        f'127.0.0.1/{port}', user='SYSDBA',
                        password=password) as server:
                    server.database.set_default_cache_size(
                        database=path, size=stored)
                for requested in CACHE_REQUESTS:
                    phase = f'{mode}-stored-{stored}-requested-{requested}'
                    try:
                        denied = (mode != 'Super' and requested is not None
                                  and requested < 25)
                        try:
                            with connect(requested) as handle:
                                observed = handle.info.page_cache_size
                        except native.DatabaseError as error:
                            codes = list(status_codes(error))
                            assert denied
                            assert 335545087 in codes
                            result['checks'].append({
                                'case': phase, 'native_status_codes': codes,
                                'requested': requested, 'stored': stored})
                            continue
                        assert not denied
                        expected = stored or (
                            128 if mode == 'Super' or requested is None
                            else max(50, requested))
                        assert observed == expected
                        result['checks'].append({
                            'case': phase, 'requested': requested,
                            'stored': stored,
                            'observed_cache_pages': observed})
                    except Exception as error:
                        failure(phase, error)
        except Exception as error:
            failure(phase, error)
        finally:
            if container is not None:
                try:
                    remove_owned(container)
                    result['removed_server_modes'].append(mode)
                except Exception as error:
                    failure(mode + '-cleanup', error)
    result['complete'] = (
        len(result['checks']) == 54 and not result['failures'] and
        result['removed_server_modes'] == list(SERVER_MODES))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        parser.error('Use a new evidence file')
    result = run(options.image)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
