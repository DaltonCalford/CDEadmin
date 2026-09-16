#!/usr/bin/env python3
"""Observe creation/attachment cache separation in owned Firebird 5.0.4."""

import argparse
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path

if __package__:
    from .cdeadmin_firebird_attachment_cache_gate import (
        SERVER_MODES, CACHE_REQUESTS, docker, published_port, remove_owned,
        OWNER, _configure_client_library,
    )
else:
    from cdeadmin_firebird_attachment_cache_gate import (
        SERVER_MODES, CACHE_REQUESTS, docker, published_port, remove_owned,
        OWNER, _configure_client_library,
    )

from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.providers.firebird.provider import (
    _route_arguments, _database_create_arguments,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


STORED_REQUESTS = (None, 0, 64)
STORED_BOUNDARIES = (None, 0, -1, 1, 49, 50, 64, 2147483647)


def private_configuration(native, dsn, requested=None, stored=None):
    """Never pass an unregistered creation name or alter driver defaults."""
    name = 'owned_creation_cache_' + uuid.uuid4().hex
    server_name = name + '_server'
    server = native.driver_config.register_server(server_name)
    server.host.value = None
    server.port.value = None
    server.user.value = None
    server.password.value = None
    config = native.driver_config.register_database(name)
    config.server.value = server_name
    config.dsn.value = dsn
    config.database.value = None
    config.user.value = None
    config.password.value = None
    config.cache_size.value = requested
    config.db_cache_size.value = stored
    config.sql_dialect.value = 3
    config.db_sql_dialect.value = 3
    config.page_size.value = 8192
    config.db_charset.value = 'UTF8'
    config.forced_writes.value = True
    config.reserve_space.value = True
    return name


def file_present(container, path):
    if not re.fullmatch('[0-9a-f]{64}', container) or not re.fullmatch(
            r'/var/lib/firebird/data/owned_creation_cache_[0-9a-f]{32}\.fdb',
            path):
        raise ValueError('Only exact owned creation paths can be inspected')
    found = docker('exec', container, 'find', '/var/lib/firebird/data',
                   '-maxdepth', '1', '-name', Path(path).name).decode().strip()
    if found not in ('', path):
        raise ValueError('Unexpected owned path observation')
    return bool(found)


def observe(handle, native):
    with handle.cursor() as cursor:
        cursor.execute('SELECT MON$PAGE_BUFFERS FROM MON$DATABASE')
        monitor_pages = cursor.fetchone()[0]
    return {'allocated_pages': handle.info.page_cache_size,
            'monitor_pages': monitor_pages,
            'stored_pages': handle.info.get_info(
                native.DbInfoCode.SET_PAGE_BUFFERS)}


def creation_case(native, container, port, password, mode, requested, stored,
                  *, provider=False):
    path = ('/var/lib/firebird/data/owned_creation_cache_' +
            uuid.uuid4().hex + '.fdb')
    assert not file_present(container, path)
    dsn = f'127.0.0.1/{port}:{path}'
    record = {'mode': mode, 'requested': requested, 'stored_request': stored}
    try:
        name = None if provider else private_configuration(
            native, dsn, requested, stored)
    except ValueError:
        if provider or stored is None or stored >= 0:
            raise
        assert not file_present(container, path)
        return dict(record, rejected=True,
                    rejected_before_native_create_by_driver=True,
                    failed_creation_path_absent=True)
    denied = mode != 'Super' and requested is not None and requested < 25
    stored_denied = (stored is not None and stored != 0 and
                     not 50 <= stored <= 2147483646)
    handle = None
    try:
        try:
            if provider:
                route = {
                    'host': '127.0.0.1', 'port': port, 'database': path,
                    'user': 'SYSDBA',
                    'attachment_cache_policy': (
                        'NATIVE_DEFAULT' if requested is None else 'CUSTOM'),
                    'attachment_cache_pages': requested,
                }
                options = ({'stored_page_buffers': stored}
                           if stored is not None else {})
                arguments = _database_create_arguments(
                    route, dsn, options, native)
                handle = native.create_database(**arguments, password=password)
            else:
                handle = native.create_database(
                    name, user='SYSDBA', password=password, overwrite=False)
        except RelationalClientError:
            assert provider and (stored_denied or (
                requested is not None and requested < 25))
            assert not file_present(container, path)
            return dict(record, rejected=True,
                        rejected_before_native_create=True,
                        failed_creation_path_absent=True)
        except native.DatabaseError as error:
            codes = list(status_codes(error))
            assert (stored_denied and 335545086 in codes) or (
                denied and 335545087 in codes)
            assert not file_present(container, path)
            return dict(record, rejected=True, native_status_codes=codes,
                        failed_creation_path_absent=True)
        assert not denied and not stored_denied
        initial = observe(handle, native)
        expected = stored or (128 if mode == 'Super' or requested is None
                              else max(50, requested))
        assert initial == {'allocated_pages': expected,
                           'monitor_pages': expected,
                           'stored_pages': stored or 0}
        with handle.cursor() as cursor:
            cursor.execute('CREATE TABLE CACHE_PROBE (ID INTEGER NOT NULL '
                           'PRIMARY KEY, PAYLOAD VARCHAR(40))')
        handle.commit()
        rows = [(7, 'creation-cache-seven'), (11, 'creation-cache-eleven')]
        with handle.cursor() as cursor:
            for row in rows:
                cursor.execute('INSERT INTO CACHE_PROBE (ID, PAYLOAD) '
                               'VALUES (?, ?)', row)
        handle.commit()
        handle.close()
        handle = None
        if provider:
            arguments = _route_arguments({
                **route, 'attachment_cache_policy': 'NATIVE_DEFAULT'}, native)
            handle = native.connect(**arguments, password=password)
        else:
            reopened_name = private_configuration(native, dsn)
            handle = native.connect(
                reopened_name, user='SYSDBA', password=password)
        reopened = observe(handle, native)
        assert reopened == {'allocated_pages': stored or 128,
                            'monitor_pages': stored or 128,
                            'stored_pages': stored or 0}
        with handle.cursor() as cursor:
            cursor.execute('SELECT ID, PAYLOAD FROM CACHE_PROBE ORDER BY ID')
            assert cursor.fetchall() == rows
        handle.commit()
        handle.drop_database()
        handle = None
        assert not file_present(container, path)
        return dict(record, rejected=False, initial=initial, reopened=reopened,
                    committed_rows_preserved=True, owned_database_removed=True)
    finally:
        if handle is not None:
            handle.close()


def run(image, *, provider=False, stored_boundaries=False):
    import firebird.driver as native
    _configure_client_library(native)
    defaults = (native.driver_config.db_defaults.get_config(),
                native.driver_config.server_defaults.get_config())
    result = {'complete': False, 'checks': [], 'failures': [],
              'removed_server_modes': [], 'provider_forms_qualified': False,
              'provider_mapping_requested': provider,
              'stored_boundary_baseline': stored_boundaries}
    stored_requests = (STORED_BOUNDARIES if stored_boundaries else
                       STORED_REQUESTS)
    cache_requests = (None,) if stored_boundaries else CACHE_REQUESTS

    def failure(case, error):
        result['failures'].append({
            'case': case, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error))})

    for mode in SERVER_MODES:
        container = None
        password = secrets.token_urlsafe(24)
        readiness_path = '/var/lib/firebird/data/owned_creation_readiness.fdb'
        phase = mode + '-create-owned-server'
        try:
            container = docker(
                'run', '--detach', '--name',
                'cdeadmin-creation-cache-' + uuid.uuid4().hex[:16],
                '--label', 'cdeadmin-owned-gate=' + OWNER,
                '--memory', '512m', '--memory-swap', '512m',
                '--publish', '127.0.0.1::3050',
                '--env', 'FIREBIRD_ROOT_PASSWORD',
                '--env', 'FIREBIRD_DATABASE',
                '--env', 'FIREBIRD_CONF_ServerMode',
                '--env', 'FIREBIRD_CONF_DefaultDbCachePages', image,
                env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                         FIREBIRD_DATABASE=readiness_path,
                         FIREBIRD_CONF_ServerMode=mode,
                         FIREBIRD_CONF_DefaultDbCachePages='128')
            ).decode().strip()
            if not re.fullmatch('[0-9a-f]{64}', container):
                raise ValueError('Owned container identity is invalid')
            port = published_port(container)
            name = private_configuration(
                native, f'127.0.0.1/{port}:{readiness_path}')
            phase = mode + '-readiness'
            deadline = time.monotonic() + 45
            while True:
                try:
                    with native.connect(name, user='SYSDBA',
                                        password=password) as handle:
                        with handle.cursor() as cursor:
                            cursor.execute(
                                "SELECT RDB$GET_CONTEXT('SYSTEM', "
                                "'ENGINE_VERSION') FROM RDB$DATABASE")
                            assert cursor.fetchone()[0] == '5.0.4'
                    break
                except native.Error:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.25)
            for stored in stored_requests:
                for requested in cache_requests:
                    phase = f'{mode}-stored-{stored}-requested-{requested}'
                    try:
                        arguments = {'provider': True} if provider else {}
                        result['checks'].append(creation_case(
                            native, container, port, password, mode,
                            requested, stored, **arguments))
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
    result['driver_defaults_unchanged'] = defaults == (
        native.driver_config.db_defaults.get_config(),
        native.driver_config.server_defaults.get_config())
    result['complete'] = (
        len(result['checks']) == len(SERVER_MODES) * len(stored_requests) *
        len(cache_requests) and not result['failures'] and
        result['driver_defaults_unchanged'] and
        result['removed_server_modes'] == list(SERVER_MODES))
    result['provider_mapping_qualified'] = provider and result['complete']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--provider', action='store_true')
    parser.add_argument('--stored-boundaries', action='store_true')
    options = parser.parse_args()
    if options.output.exists():
        parser.error('Use a new evidence file')
    result = run(options.image, provider=options.provider,
                 stored_boundaries=options.stored_boundaries)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
