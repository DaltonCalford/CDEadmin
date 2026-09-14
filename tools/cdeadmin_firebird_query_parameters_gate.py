#!/usr/bin/env python3
"""Verify Firebird query bindings and native transaction outcomes."""

import argparse
import importlib.metadata
import json
import subprocess
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from cdeadmin_firebird_admin_mapping_gate import (
    ADMINISTRATION, RelationalClientError, _create_client, _route_arguments,
)
from pgadmin.cdeadmin.security.secrets import SecretLease


def run(profiles, container):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    password = route.pop('password')
    route.update(credential_reference_id='owned-bindings-secret',
                 principal_reference='owned-bindings-principal')
    path = str(PurePosixPath(route['database']).parent /
               ('cde_bindings_' + uuid.uuid4().hex + '.fdb'))
    result = {'complete': False, 'cases': [], 'failures': [],
              'fixture_database': path, 'fixture_removed': False,
              'driver_version': importlib.metadata.version('firebird-driver'),
              'credential_values_exported': False}
    client = _create_client(SimpleNamespace(
        acquire_secret=lambda *_args: SecretLease(password)))
    handle = None
    requested = False

    def exists():
        check = subprocess.run(
            ['docker', 'exec', container, 'test', '-e', path],
            capture_output=True, check=False)
        if check.returncode not in (0, 1):
            raise RuntimeError('Cannot observe owned fixture existence')
        return check.returncode == 0

    def execute(source, parameters=()):
        token = client.execute(handle, {'source': source,
                                        'parameters': parameters})
        return client.describe_result(token)['payload']['rows']

    def external_rows():
        other = driver.connect(password=password, **_route_arguments(
            {**route, 'database': path}, driver))
        try:
            with other.cursor() as cursor:
                cursor.execute('SELECT I, V FROM DATA ORDER BY I')
                return cursor.fetchall()
        finally:
            other.close()

    try:
        assert not exists()
        plan = ADMINISTRATION.plan({
            '_provider_route': route, 'resource_kind': 'database',
            'operation_id': 'create', 'target_resource': None,
            'draft': {'database_path': path}})
        requested = True
        ADMINISTRATION.apply(client, plan)
        handle = client.open_session({'route': {**route, 'database': path}})
        scalar_cases = [
            ('integer', 'INTEGER', 2147483647, '2147483647'),
            ('bigint', 'BIGINT', -9223372036854775808, '-9223372036854775808'),
            ('unicode', 'VARCHAR(80) CHARACTER SET UTF8',
             "é ' ? ; -- text", "é ' ? ; -- text"),
            ('null', 'INTEGER', None, 'None'),
            ('decimal', 'NUMERIC(18,4)', '1234567.0123', '1234567.0123'),
            ('boolean', 'BOOLEAN', True, 'True'),
            ('date', 'DATE', '2026-09-14', '2026-09-14'),
            ('timestamp', 'TIMESTAMP', '2026-09-14 12:34:56.1234',
             '2026-09-14 12:34:56.123400'),
        ]
        for name, cast, value, expected in scalar_cases:
            try:
                rows = execute('SELECT CAST(? AS ' + cast +
                               ') FROM RDB$DATABASE', [value])
                assert str(rows[0][0]) == expected
                result['cases'].append(name)
            except Exception as exc:
                result['failures'].append({'case': name,
                                           'error_type': type(exc).__name__})
            finally:
                client.control_transaction(handle, 'rollback')
        execute('CREATE TABLE DATA (I INTEGER PRIMARY KEY, '
                'V VARCHAR(80) CHARACTER SET UTF8)')
        client.control_transaction(handle, 'commit')
        source = 'INSERT INTO DATA (I, V) VALUES (?, ?)'
        execute(source, [1, "first ' ? ; value"])
        assert external_rows() == []
        client.control_transaction(handle, 'rollback')
        assert external_rows() == []
        result['cases'].append('insert-rollback')
        execute(source, [1, "first ' ? ; value"])
        client.control_transaction(handle, 'commit')
        assert external_rows() == [(1, "first ' ? ; value")]
        result['cases'].append('insert-commit')
        execute('UPDATE DATA SET V = ? WHERE I = ?', ['changed', 1])
        assert execute('SELECT V FROM DATA WHERE I = ?', [1]) == [('changed',)]
        client.control_transaction(handle, 'rollback')
        assert external_rows() == [(1, "first ' ? ; value")]
        result['cases'].append('update-rollback-and-binding-order')
        execute(source, [2, 'prior pending row'])
        for invalid in ({'first': 'private-canary', 'second': 3}, [3]):
            try:
                execute(source, invalid)
            except RelationalClientError as exc:
                assert 'private-canary' not in str(exc)
            else:
                raise AssertionError('Invalid binding unexpectedly executed')
            assert execute('SELECT COUNT(*) FROM DATA') == [(2,)]
            assert external_rows() == [(1, "first ' ? ; value")]
        client.control_transaction(handle, 'rollback')
        result['cases'].append('rejected-bindings-preserve-caller-transaction')
        execute(source, [2, 'pending before duplicate key'])
        try:
            execute(source, [1, 'private-diagnostic-canary'])
        except RelationalClientError as exc:
            assert 335544665 in exc.gds_codes
            assert 'private-diagnostic-canary' not in str(exc)
            assert '335544665' in str(exc)
        else:
            raise AssertionError('Duplicate primary key unexpectedly accepted')
        assert execute('SELECT COUNT(*) FROM DATA') == [(2,)]
        assert external_rows() == [(1, "first ' ? ; value")]
        client.control_transaction(handle, 'rollback')
        result['cases'].append('native-status-and-statement-atomicity')
        execute('DELETE FROM DATA WHERE I = ?', [1])
        client.control_transaction(handle, 'rollback')
        assert len(external_rows()) == 1
        result['cases'].append('delete-rollback')
        execute('DELETE FROM DATA WHERE I = ?', [1])
        client.control_transaction(handle, 'commit')
        assert external_rows() == []
        result['cases'].append('delete-commit')
        closed = client.close_session(handle)
        handle = None
        assert closed['rollback_requested'] is False
        result['cases'].append('close-idle-session')
        handle = client.open_session({'route': {**route, 'database': path}})
        execute(source, [9, 'close must roll back'])
        assert external_rows() == []
        closed = client.close_session(handle)
        handle = None
        assert closed['rollback_requested'] is True
        assert external_rows() == []
        result['cases'].append('close-active-session-rolls-back')
    except Exception as exc:
        result['failures'].append({'case': 'gate',
                                   'error_type': type(exc).__name__,
                                   'message': str(exc).replace(password,
                                                               '[redacted]')})
    finally:
        try:
            if handle is not None:
                client.close_session(handle)
        except Exception as exc:
            result['failures'].append({'case': 'close-session',
                                       'error_type': type(exc).__name__})
        try:
            client.close()
        except Exception as exc:
            result['failures'].append({'case': 'close-client',
                                       'error_type': type(exc).__name__})
        if requested:
            try:
                if exists():
                    connection = driver.connect(
                        password=password, **_route_arguments(
                            {**route, 'database': path}, driver))
                    connection.drop_database()
                result['fixture_removed'] = not exists()
            except Exception as exc:
                result['failures'].append({'case': 'cleanup',
                                           'error_type': type(exc).__name__})
    result['complete'] = (len(result['cases']) == 17 and
                          result['fixture_removed'] and not result['failures'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--container', default='cdeadmin-demo-firebird')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.profiles, args.container)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
