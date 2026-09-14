#!/usr/bin/env python3
"""Qualify native gbak include/skip precedence and Unicode table filters."""
import argparse
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_logical_volumes_gate import (
        OWNER, docker, published_port, remove_owned)
    from .cdeadmin_firebird_admin_mapping_gate import (
        _create_client, _route_arguments)
else:
    from cdeadmin_firebird_logical_volumes_gate import (
        OWNER, docker, published_port, remove_owned)
    from cdeadmin_firebird_admin_mapping_gate import (
        _create_client, _route_arguments)
from pgadmin.cdeadmin.providers.firebird.provider import (
    ADMINISTRATION, _configure_client_library)
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.security.secrets import SecretLease


# Native burp.cpp skipRelation truth table. Expected rows are declared, not
# computed using a Python substitute for Firebird SimilarToRegex.
CASES = (
    ('none', None, None, False, True, True),
    ('include', 'OWNED%', None, False, True, False),
    ('skip', None, 'OWNED%', False, False, True),
    ('overlap', 'OWNED%', 'OWNED%', False, False, False),
    ('disjoint', 'OWNED%', 'NEVER%', False, True, False),
    ('no-include-match', 'NEVER%', 'OWNED%', False, False, False),
    ('include-all-skip-ascii', '%', 'OWNED%', False, False, True),
    ('case-insensitive', 'owned%', None, False, True, False),
    ('escaped-underscore', r'OWNED\_FILTER\_DATA', None, False, True, False),
    ('one-character-wildcard', 'OWNED_FILTER_DAT_', None, False, True, False),
    ('literal-dot-not-pcre', 'OWNED.FILTER.DATA', None, False, False, False),
    ('alternation', '(OWNED%|東京%)', None, False, True, True),
    ('unicode-include', '東京%', None, False, False, True),
    ('unicode-skip', None, '東京%', False, True, False),
    ('include-all-skip-unicode', '%', '東京%', False, True, False),
    ('metadata-only', '%', 'NEVER%', True, False, False),
)


def run(image):
    import firebird.driver as native
    _configure_client_library(native)
    name = 'cdeadmin-gbak-filters-' + uuid.uuid4().hex[:16]
    password = secrets.token_urlsafe(24)
    primary = '/var/lib/firebird/data/primary.fdb'
    container = connection = client = None
    phase = 'create-container'
    result = {'complete': False, 'kind': 'logical-filters',
              'cases': [], 'failures': [], 'container_name': name,
              'owned_container_removed': False,
              'credential_values_exported': False}

    def connect(database=primary):
        return native.connect(password=password, **_route_arguments(
            {**route, 'database': database}, native))

    def operation(operation_id, draft):
        request = {'engine_id': 'firebird', 'resource_kind': 'database',
                   'operation_id': operation_id, 'draft': draft,
                   '_provider_route': route}
        assert not ADMINISTRATION.validate(request)['errors']
        plan = ADMINISTRATION.plan(request)
        applied = ADMINISTRATION.apply(client, {
            'provider_payload': plan['provider_payload']})
        observed = applied['driver_observation']
        assert observed['server_completed'] is True
        assert observed['service_release']['service_handle_released'] is True

    def verify(destination, ascii_rows, unicode_rows):
        handle = connect(destination)
        try:
            with handle.cursor() as cursor:
                cursor.execute(
                    'SELECT ID, V FROM OWNED_FILTER_DATA ORDER BY ID')
                assert cursor.fetchall() == (
                    [(1, 'alpha'), (2, 'beta')] if ascii_rows else [])
                cursor.execute('SELECT ID, V FROM "東京資料" ORDER BY ID')
                assert cursor.fetchall() == (
                    [(1, '東京')] if unicode_rows else [])
                cursor.execute('SELECT COUNT(*) FROM RDB$RELATIONS WHERE '
                               'RDB$RELATION_NAME IN (?, ?) AND '
                               'COALESCE(RDB$SYSTEM_FLAG, 0) = 0',
                               ('OWNED_FILTER_DATA', '東京資料'))
                assert cursor.fetchone() == (2,)
            handle.rollback()
        finally:
            handle.close()

    try:
        env = dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                   FIREBIRD_DATABASE=primary)
        # Reuse the immutable-CID/owner-label cleanup contract of gbak gates.
        container = docker(
            'create', '--name', name, '--label',
            'cdeadmin-owned-gate=' + OWNER,
            '--publish', '127.0.0.1::3050', '--env', 'FIREBIRD_ROOT_PASSWORD',
            '--env', 'FIREBIRD_DATABASE', image, env=env).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise RuntimeError('Owned container identity is invalid')
        result['container_id'] = container
        phase = 'start-container'
        docker('start', container)
        route = {'host': '127.0.0.1', 'port': published_port(container),
                 'user': 'SYSDBA', 'database': primary, 'timeout': 2,
                 'auth_plugin_list': 'Srp256',
                 'credential_reference_id': 'owned-filters-secret',
                 'principal_reference': 'owned-filters-principal'}
        phase = 'readiness'
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                connection = connect()
                break
            except native.Error:
                time.sleep(0.25)
        if connection is None:
            raise RuntimeError('Owned Firebird readiness deadline exceeded')
        assert '5.0.4' in connection.info.firebird_version
        phase = 'owned-payload'
        for table in ('OWNED_FILTER_DATA', '東京資料'):
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE TABLE "{table}" '
                               '(ID INTEGER PRIMARY KEY, V VARCHAR(60))')
            connection.commit()
        with connection.cursor() as cursor:
            cursor.executemany('INSERT INTO OWNED_FILTER_DATA VALUES (?, ?)',
                               [(1, 'alpha'), (2, 'beta')])
            cursor.execute('INSERT INTO "東京資料" VALUES (?, ?)', (1, '東京'))
        connection.commit()
        connection.close()
        connection = None
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))
        baseline = primary + '.baseline.fbk'
        operation('backup_logical', {'backup_file': baseline})
        for action in ('backup', 'restore'):
            for (label, include, skip, metadata,
                 keep_ascii, keep_unicode) in CASES:
                phase = action + '-' + label
                backup = primary + '.' + phase + '.fbk'
                destination = primary + '.' + phase + '.fdb'
                filters = {key: value for key, value in (
                    ('include_data', include), ('skip_data', skip))
                    if value is not None}
                flags = ['METADATA_ONLY'] if metadata else []
                try:
                    if action == 'backup':
                        operation('backup_logical', {
                            'backup_file': backup, 'backup_flags': flags,
                            **filters})
                        operation('restore_logical', {
                            'backup_file': backup,
                            'restore_database': destination})
                    else:
                        operation('restore_logical', {
                            'backup_file': baseline,
                            'restore_database': destination,
                            'restore_flags': flags, **filters})
                    verify(destination, keep_ascii, keep_unicode)
                    result['cases'].append({
                        'case': phase, 'ascii_rows': 2 if keep_ascii else 0,
                        'unicode_rows': 1 if keep_unicode else 0,
                        'both_table_definitions_preserved': True})
                except Exception as exc:
                    result['failures'].append({
                        'stage': phase, 'type': type(exc).__name__,
                        'native_status_codes': list(status_codes(exc))})
            phase = action + '-invalid-native-pattern'
            output = primary + '.' + phase
            try:
                try:
                    operation(action + '_logical', {
                        'include_data': '[', **(
                            {'backup_file': output} if action == 'backup' else
                            {'backup_file': baseline,
                             'restore_database': output})})
                except RelationalClientError as exc:
                    codes = status_codes(exc)
                    assert 335544382 in codes  # isc_random, native compiler
                else:
                    raise AssertionError('Invalid native pattern was accepted')
                docker('exec', container, 'test', '!', '-e', output)
                result['cases'].append({'case': phase,
                                        'native_status_codes': list(codes),
                                        'output_not_created': True})
            except Exception as exc:
                result['failures'].append({'stage': phase,
                                           'type': type(exc).__name__,
                                           'native_status_codes': list(
                                               status_codes(exc))})
    except Exception as exc:
        result['failures'].append({'stage': phase, 'type': type(exc).__name__,
                                   'native_status_codes': list(
                                       status_codes(exc))})
    finally:
        for handle in (connection, client):
            if handle is not None:
                try:
                    handle.close()
                except Exception as exc:
                    result['failures'].append({'stage': 'release',
                                               'type': type(exc).__name__})
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as exc:
                result['failures'].append({'stage': 'cleanup',
                                           'type': type(exc).__name__})
    result['complete'] = (not result['failures'] and
                          len(result['cases']) == 2 * len(CASES) + 2
                          and result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise SystemExit('Choose a new evidence output file')
    result = run(options.image)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    raise SystemExit(0 if result['complete'] else 1)


if __name__ == '__main__':
    main()
