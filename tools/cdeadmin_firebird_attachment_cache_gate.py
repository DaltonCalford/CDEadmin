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
from pgadmin.cdeadmin.providers.firebird.provider import _route_arguments
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


SERVER_MODES = ('Super', 'SuperClassic', 'Classic')
CACHE_REQUESTS = (None, 0, 1, 24, 25, 49, 50, 128, 256)


def run(image, *, provider=False, browser_options=None):
    browser_gate = getattr(
        browser_options, 'browser_gate', 'cache-preferences')
    if browser_gate not in ('cache-preferences', 'properties', 'lifecycle',
                            'creation-buffers-form', 'creation-sweep-form',
                            'creation-lifecycle', 'trap-preferences',
                            'parallel-preferences'):
        raise ValueError('Unknown cache browser gate')
    if browser_options is not None:
        if not provider or not browser_options.build_root:
            raise ValueError(
                'Browser requires provider mapping and build root')
        browser_options.build_root.mkdir(parents=True, exist_ok=False)
    import firebird.driver as native
    _configure_client_library(native)
    result = {'complete': False, 'checks': [], 'failures': [],
              'removed_server_modes': [], 'provider_forms_qualified': False,
              'provider_mapping_requested': provider, 'browser_checks': [],
              'browser_gate': browser_gate if browser_options else None}

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
                if provider:
                    arguments = _route_arguments({
                        'host': '127.0.0.1', 'port': port, 'database': path,
                        'user': 'SYSDBA',
                        'attachment_cache_policy': (
                            'NATIVE_DEFAULT' if request is None else 'CUSTOM'),
                        'attachment_cache_pages': request,
                    }, native)
                    return native.connect(**arguments, password=password)
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
                        except RelationalClientError:
                            assert provider and requested is not None
                            assert requested < 25
                            result['checks'].append({
                                'case': phase, 'requested': requested,
                                'stored': stored,
                                'rejected_before_native_connect': True})
                            continue
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
            if browser_options is not None and mode == 'SuperClassic':
                from tools.cdeadmin_firebird_external_functions_gate import (
                    browser_checks,
                )
                phase = 'browser-qualification'
                with native.connect_server(
                        f'127.0.0.1/{port}', user='SYSDBA',
                        password=password) as server:
                    server.database.set_default_cache_size(
                        database=path, size=0)
                result['browser_checks'] = browser_checks(
                    browser_options,
                    {'host': '127.0.0.1', 'port': port, 'database': path,
                     'user': 'SYSDBA'}, password, container,
                    browser_options.build_root, gate_kind=browser_gate,
                    fixture_kind='firebird-cache-qualification')
                if not result['browser_checks'] or not all(
                        item['passed'] for item in result['browser_checks']):
                    raise RuntimeError('Browser cache qualification failed')
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
    result['provider_mapping_qualified'] = provider and result['complete']
    result['provider_forms_qualified'] = (
        browser_options is not None and browser_gate == 'cache-preferences'
        and result['complete'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--provider', action='store_true')
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--browser-gate',
                        choices=('cache-preferences', 'properties',
                                 'lifecycle', 'creation-buffers-form',
                                 'creation-sweep-form', 'creation-lifecycle',
                                 'trap-preferences', 'parallel-preferences'),
                        default='cache-preferences')
    parser.add_argument('--build-root', type=Path)
    parser.add_argument('--source-config-db', type=Path,
                        default=Path('/var/lib/cdeadmin/cdeadmin.db'))
    parser.add_argument('--desktop-user')
    parser.add_argument('--font-scale', type=int, action='append',
                        choices=(100, 200, 300))
    options = parser.parse_args()
    if options.output.exists():
        parser.error('Use a new evidence file')
    if options.browser and (not options.provider or not options.build_root or
                            not options.desktop_user):
        parser.error('Browser requires provider, build root and desktop user')
    result = run(options.image, provider=options.provider,
                 browser_options=options if options.browser else None)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
