#!/usr/bin/env python3
"""Qualify ordinary attachment no-linger using an owned Firebird server."""

import argparse
import itertools
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
    from .cdeadmin_firebird_repair_gate import owned_database_open_files
else:
    from cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _configure_client_library,
    )
    from cdeadmin_firebird_repair_gate import owned_database_open_files

from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes


SERVER_MODES = ('Super', 'SuperClassic', 'Classic')


def run(image, server_mode='Super'):
    if server_mode not in SERVER_MODES:
        raise ValueError('Choose an exact Firebird server mode')
    import firebird.driver as native
    _configure_client_library(native)
    result = {'complete': False, 'checks': [], 'failures': [],
              'owned_container_removed': False,
              'provider_forms_qualified': False,
              'fixture_scope': 'ordinary attachments',
              'server_mode': server_mode}
    container = None
    password = secrets.token_urlsafe(24)
    limited_password = secrets.token_urlsafe(24)
    bootstrap = '/var/lib/firebird/data/owned_linger_bootstrap.fdb'

    def failure(case, error):
        result['failures'].append({
            'case': case, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error))})

    phase = 'create-owned-server'
    try:
        container = docker(
            'run', '--detach', '--name',
            'cdeadmin-attachment-linger-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--memory', '512m', '--memory-swap', '512m',
            '--publish', '127.0.0.1::3050',
            '--env', 'FIREBIRD_ROOT_PASSWORD', '--env', 'FIREBIRD_DATABASE',
            '--env', 'FIREBIRD_CONF_ServerMode',
            '--env', 'FIREBIRD_CONF_DefaultDbCachePages', image,
            env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                     FIREBIRD_DATABASE=bootstrap,
                     FIREBIRD_CONF_ServerMode=server_mode,
                     FIREBIRD_CONF_DefaultDbCachePages='128')
        ).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise ValueError('Owned container identity is invalid')
        port = published_port(container)

        def connect(path, *, requested=None, limited=False, create=False):
            name = 'owned_linger_' + uuid.uuid4().hex
            config = native.driver_config.register_database(name)
            config.dsn.value = f'127.0.0.1/{port}:{path}'
            config.user.value = None
            config.password.value = None
            config.no_linger.value = requested
            method = native.create_database if create else native.connect
            return method(name,
                          user='CDE_LINGER_USER' if limited else 'SYSDBA',
                          password=limited_password if limited else password)

        phase = 'readiness'
        deadline = time.monotonic() + 45
        while True:
            try:
                with connect(bootstrap) as handle:
                    with handle.cursor() as cursor:
                        cursor.execute("SELECT RDB$GET_CONTEXT('SYSTEM', "
                                       "'ENGINE_VERSION') FROM RDB$DATABASE")
                        assert cursor.fetchone()[0] == '5.0.4'
                break
            except native.Error:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        phase = 'create-owned-principal'
        with connect(bootstrap) as handle:
            with handle.cursor() as cursor:
                cursor.execute("CREATE USER CDE_LINGER_USER PASSWORD '" +
                               limited_password + "'")
            handle.commit()
        for index, (requested, limited, peer) in enumerate(itertools.product(
                (None, False, True), (False, True), (False, True)), 1):
            phase = f'request-{requested}-limited-{limited}-peer-{peer}'
            record = {'case': phase, 'passed': False}
            result['checks'].append(record)
            # Reuse the descriptor observer's exact, allowlisted fixture paths.
            path = f'/var/lib/firebird/data/owned_repair_linger_{index}.fdb'
            held = None
            try:
                with connect(path, create=True) as setup:
                    with setup.cursor() as cursor:
                        cursor.execute('ALTER DATABASE SET LINGER TO 600')
                        cursor.execute('CREATE TABLE LINGER_MARKER '
                                       '(ID INTEGER)')
                        cursor.execute('GRANT SELECT ON LINGER_MARKER '
                                       'TO USER CDE_LINGER_USER')
                    setup.commit()
                    with setup.cursor() as cursor:
                        cursor.execute('INSERT INTO LINGER_MARKER VALUES (1)')
                    setup.commit()
                assert (owned_database_open_files(container, path) > 0) is (
                    server_mode == 'Super')
                if peer:
                    held = connect(path)
                    with held.cursor() as cursor:
                        cursor.execute('INSERT INTO LINGER_MARKER VALUES (2)')
                        cursor.execute('SELECT CURRENT_TRANSACTION '
                                       'FROM RDB$DATABASE')
                        transaction = cursor.fetchone()[0]
                with connect(path, requested=requested,
                             limited=limited) as one:
                    with one.cursor() as cursor:
                        cursor.execute('SELECT ID FROM LINGER_MARKER')
                        assert cursor.fetchall() == [(1,)]
                if held is not None:
                    assert owned_database_open_files(container, path) > 0
                    if requested is True:
                        # False omits the DPB; it must not be described as
                        # restoring the live cache's previous linger value.
                        with connect(path, requested=False) as later:
                            with later.cursor() as cursor:
                                cursor.execute('SELECT ID FROM LINGER_MARKER')
                                assert cursor.fetchall() == [(1,)]
                        record['false_reattachment_before_peer_detach'] = True
                    with held.cursor() as cursor:
                        cursor.execute('SELECT CURRENT_TRANSACTION '
                                       'FROM RDB$DATABASE')
                        assert cursor.fetchone()[0] == transaction
                        cursor.execute('SELECT ID FROM LINGER_MARKER '
                                       'ORDER BY ID')
                        assert cursor.fetchall() == [(1,), (2,)]
                    held.rollback()
                    held.close()
                    held = None
                    record['peer_transaction_preserved'] = True
                deadline = time.monotonic() + 5
                count = owned_database_open_files(container, path)
                expect_closed = requested is True or server_mode != 'Super'
                while (expect_closed and count and
                       time.monotonic() < deadline):
                    time.sleep(0.1)
                    count = owned_database_open_files(container, path)
                assert (count == 0) is expect_closed
                record['open_files_after_final_detach'] = count
                with connect(path) as observer:
                    with observer.cursor() as cursor:
                        cursor.execute('SELECT RDB$LINGER FROM RDB$DATABASE')
                        assert cursor.fetchone() == (600,)
                        cursor.execute('SELECT ID FROM LINGER_MARKER')
                        assert cursor.fetchall() == [(1,)]
                assert (owned_database_open_files(container, path) > 0) is (
                    server_mode == 'Super')
                record.update(stored_linger_unchanged=True,
                              reopening_cache_lingers=server_mode == 'Super',
                              rolled_back_peer_row_absent=True, passed=True)
            except Exception as error:
                failure(phase, error)
            finally:
                if held is not None:
                    try:
                        held.close()
                    except Exception as error:
                        failure(phase + '-peer-cleanup', error)
    except Exception as error:
        failure(phase, error)
    finally:
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                failure('cleanup', error)
    result['complete'] = (len(result['checks']) == 12 and
                          all(item['passed'] for item in result['checks']) and
                          not result['failures'] and
                          result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--server-mode', choices=SERVER_MODES, default='Super')
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        parser.error('Use a new evidence file')
    result = run(options.image, options.server_mode)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
