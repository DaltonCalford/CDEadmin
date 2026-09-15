#!/usr/bin/env python3
"""Native client/stored dialect separation in a disposable Firebird fixture.

This qualifies native behavior only, not provider-generated SQL or UI controls.
"""

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


PROBES = (
    ('unquoted-column', 'SELECT ID FROM DIALECT_PROBE'),
    ('quoted-column', 'SELECT "ID" FROM DIALECT_PROBE'),
    ('quoted-relation', 'SELECT ID FROM "DIALECT_PROBE"'),
    ('quoted-system-column', 'SELECT "RDB$RELATION_ID" FROM RDB$RELATIONS '
     "WHERE RDB$RELATION_NAME = 'DIALECT_PROBE'"),
)


def expected_probe(name, client_dialect, relation_id):
    if client_dialect not in (1, 2, 3) or name not in dict(PROBES):
        raise ValueError('Unknown native dialect probe')
    if name == 'unquoted-column':
        return [(73,)], None
    if client_dialect == 2:
        return None, 335544763  # invalid_string_constant; Parser.cpp
    if name == 'quoted-relation' and client_dialect == 1:
        # Parser::yyerror reports the offending token with its position.
        return None, 335544634  # dsql_token_unk_err
    if client_dialect == 1:
        value = 'ID' if name == 'quoted-column' else 'RDB$RELATION_ID'
        return [(value,)], None
    return [(relation_id if name == 'quoted-system-column' else 73,)], None


def run(image):
    import firebird.driver as native
    _configure_client_library(native)
    result = {'complete': False, 'checks': [], 'failures': [],
              'owned_container_removed': False,
              'provider_forms_qualified': False,
              'fixture_scope': 'client dialect versus stored dialect'}
    container = None
    password = secrets.token_urlsafe(24)
    bootstrap = '/var/lib/firebird/data/owned_dialect_bootstrap.fdb'

    def failure(case, error):
        result['failures'].append({
            'case': case, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error))})

    phase = 'create-owned-server'
    try:
        container = docker(
            'run', '--detach', '--name',
            'cdeadmin-client-dialect-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--memory', '512m', '--memory-swap', '512m',
            '--publish', '127.0.0.1::3050',
            '--env', 'FIREBIRD_ROOT_PASSWORD', '--env', 'FIREBIRD_DATABASE',
            image, env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                            FIREBIRD_DATABASE=bootstrap)).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise ValueError('Owned container identity is invalid')
        port = published_port(container)

        def connect(path, client=3, *, stored=None):
            config = native.driver_config.register_database(
                'owned_client_dialect_' + uuid.uuid4().hex)
            config.dsn.value = f'127.0.0.1/{port}:{path}'
            config.user.value = None
            config.password.value = None
            config.sql_dialect.value = client
            if stored is not None:
                config.db_sql_dialect.value = stored
            method = native.create_database if stored else native.connect
            return method(config.name, user='SYSDBA', password=password)

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
        for stored in (1, 3):
            phase = f'setup-stored-{stored}'
            path = f'/var/lib/firebird/data/owned_dialect_{stored}.fdb'
            try:
                with connect(path, stored=stored) as setup:
                    with setup.cursor() as cursor:
                        cursor.execute('CREATE TABLE DIALECT_PROBE '
                                       '(ID INTEGER)')
                    setup.commit()
                    with setup.cursor() as cursor:
                        cursor.execute('INSERT INTO DIALECT_PROBE VALUES (73)')
                        cursor.execute('SELECT RDB$RELATION_ID '
                                       'FROM RDB$RELATIONS WHERE '
                                       "RDB$RELATION_NAME = 'DIALECT_PROBE'")
                        relation_id = cursor.fetchone()[0]
                    setup.commit()
            except Exception as error:
                failure(phase, error)
                continue
            for client in (1, 2, 3):
                for reset in (False, True):
                    for name, sql in PROBES:
                        phase = f'db-{stored}-client-{client}-{reset}-{name}'
                        record = {'case': phase, 'passed': False,
                                  'stored_dialect': stored,
                                  'client_dialect': client, 'reset': reset}
                        result['checks'].append(record)
                        try:
                            with connect(path, client) as handle:
                                assert handle.sql_dialect == client
                                assert handle.info.sql_dialect == stored
                                if reset:
                                    with handle.cursor() as cursor:
                                        cursor.execute('ALTER SESSION RESET')
                                    handle.rollback()
                                expected, expected_error = expected_probe(
                                    name, client, relation_id)
                                observed_error = None
                                try:
                                    with handle.cursor() as cursor:
                                        cursor.execute(sql)
                                        rows = cursor.fetchall()
                                except native.Error as error:
                                    observed_error = list(status_codes(error))
                                    if expected_error not in observed_error:
                                        raise
                                if expected_error is None:
                                    assert observed_error is None
                                    assert rows == expected
                                else:
                                    assert observed_error is not None
                                handle.rollback()
                                assert handle.sql_dialect == client
                                assert handle.info.sql_dialect == stored
                                record.update(passed=True,
                                              expected_status=expected_error,
                                              stored_dialect_unchanged=True)
                        except Exception as error:
                            failure(phase, error)
    except Exception as error:
        failure(phase, error)
    finally:
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                failure('cleanup', error)
    result['complete'] = (len(result['checks']) == 48 and
                          all(item['passed'] for item in result['checks']) and
                          not result['failures'] and
                          result['owned_container_removed'])
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
