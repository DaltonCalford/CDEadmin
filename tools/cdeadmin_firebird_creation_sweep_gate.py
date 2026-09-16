#!/usr/bin/env python3
"""Observe creation-time sweep intervals on owned Firebird 5.0.4 servers."""

import argparse
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path

from tools import cdeadmin_firebird_creation_cache_gate as cache


INTERVALS = (None, -1, 0, 1, 20000, 50000, 2147483647)


def observation(handle):
    with handle.cursor() as cursor:
        cursor.execute('SELECT MON$SWEEP_INTERVAL FROM MON$DATABASE')
        monitor = cursor.fetchone()[0]
    return {'info_interval': handle.info.sweep_interval,
            'monitor_interval': monitor}


def creation_case(native, container, port, password, mode, interval):
    path = ('/var/lib/firebird/data/owned_creation_cache_' +
            uuid.uuid4().hex + '.fdb')
    assert not cache.file_present(container, path)
    dsn = f'127.0.0.1/{port}:{path}'
    name = cache.private_configuration(native, dsn)
    config = native.driver_config.get_database(name)
    record = {'mode': mode, 'requested_interval': interval}
    try:
        config.sweep_interval.value = interval
    except ValueError:
        assert interval == -1
        assert not cache.file_present(container, path)
        return dict(record, rejected_before_native_create_by_driver=True,
                    failed_creation_path_absent=True)
    assert interval != -1
    handle = None
    try:
        handle = native.create_database(
            name, user='SYSDBA', password=password, overwrite=False)
        expected = 20000 if interval is None else interval
        first = observation(handle)
        assert first == {'info_interval': expected,
                         'monitor_interval': expected}
        with handle.cursor() as cursor:
            cursor.execute('CREATE TABLE CDE_SWEEP_PROBE (ID INTEGER)')
        handle.commit()
        with handle.cursor() as cursor:
            cursor.execute('INSERT INTO CDE_SWEEP_PROBE VALUES (42)')
        handle.commit()
        handle.close()
        handle = None
        reopen_name = cache.private_configuration(native, dsn)
        handle = native.connect(reopen_name, user='SYSDBA', password=password)
        reopened = observation(handle)
        assert reopened == first
        with handle.cursor() as cursor:
            cursor.execute('SELECT ID FROM CDE_SWEEP_PROBE')
            assert cursor.fetchall() == [(42,)]
        handle.rollback()
        handle.drop_database()
        handle = None
        assert not cache.file_present(container, path)
        return dict(record, created=first, reopened=reopened,
                    committed_rows_preserved=True, dropped=True,
                    owned_path_absent=True)
    finally:
        if handle is not None:
            handle.close()


def run(image):
    import firebird.driver as native
    cache._configure_client_library(native)
    defaults = (native.driver_config.db_defaults.get_config(),
                native.driver_config.server_defaults.get_config())
    result = {'complete': False, 'checks': [], 'failures': [],
              'removed_server_modes': [], 'provider_qualified': False}

    def failure(phase, error):
        result['failures'].append({
            'phase': phase, 'error_type': type(error).__name__,
            'native_status_codes': list(cache.status_codes(error))})

    for mode in cache.SERVER_MODES:
        container = None
        password = secrets.token_urlsafe(24)
        readiness = '/var/lib/firebird/data/owned_sweep_readiness.fdb'
        phase = mode + '-startup'
        try:
            container = cache.docker(
                'run', '--detach', '--name',
                'cdeadmin-creation-sweep-' + uuid.uuid4().hex[:16],
                '--label', 'cdeadmin-owned-gate=' + cache.OWNER,
                '--memory', '512m', '--memory-swap', '512m',
                '--publish', '127.0.0.1::3050',
                '--env', 'FIREBIRD_ROOT_PASSWORD',
                '--env', 'FIREBIRD_DATABASE',
                '--env', 'FIREBIRD_CONF_ServerMode',
                '--env', 'FIREBIRD_CONF_DefaultDbCachePages', image,
                env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                         FIREBIRD_DATABASE=readiness,
                         FIREBIRD_CONF_ServerMode=mode,
                         FIREBIRD_CONF_DefaultDbCachePages='128'),
            ).decode().strip()
            if not re.fullmatch('[0-9a-f]{64}', container):
                raise ValueError('Owned container identity is invalid')
            port = cache.published_port(container)
            name = cache.private_configuration(
                native, f'127.0.0.1/{port}:{readiness}')
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
            for interval in INTERVALS:
                phase = f'{mode}-interval-{interval}'
                try:
                    result['checks'].append(creation_case(
                        native, container, port, password, mode, interval))
                except Exception as error:
                    failure(phase, error)
        except Exception as error:
            failure(phase, error)
        finally:
            if container is not None:
                try:
                    cache.remove_owned(container)
                    result['removed_server_modes'].append(mode)
                except Exception as error:
                    failure(mode + '-cleanup', error)
    result['driver_defaults_unchanged'] = defaults == (
        native.driver_config.db_defaults.get_config(),
        native.driver_config.server_defaults.get_config())
    result['complete'] = (
        len(result['checks']) == len(cache.SERVER_MODES) * len(INTERVALS) and
        not result['failures'] and result['driver_defaults_unchanged'] and
        result['removed_server_modes'] == list(cache.SERVER_MODES))
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
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
