#!/usr/bin/env python3
"""Verify lossless JSON query values with a read-only Firebird attachment."""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from cdeadmin_firebird_admin_mapping_gate import _create_client
from pgadmin.cdeadmin.security.secrets import SecretLease


def run(profiles):
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    password = route.pop('password')
    route.update(credential_reference_id='owned-query-value-secret',
                 principal_reference='owned-query-value-principal',
                 transaction_access='READ')
    client = _create_client(SimpleNamespace(
        acquire_secret=lambda *_args: SecretLease(password)))
    result = {'complete': False, 'cases': [], 'failures': [],
              'credential_values_exported': False,
              'existing_database_mutated': False}
    binary = {'encoding': 'base64', 'data': 'AP9/', 'byte_length': 3}
    cases = [
        ('integer', 'INTEGER', 2147483647, 2147483647),
        ('bigint', 'BIGINT', '-9223372036854775808', '-9223372036854775808'),
        ('int128', 'INT128', '170141183460469231731687303715884105727',
         '170141183460469231731687303715884105727'),
        ('decimal', 'NUMERIC(18,4)', '1234567.0123', '1234567.0123'),
        ('decfloat', 'DECFLOAT(34)', '1.234567890123456789012345678901234',
         '1.234567890123456789012345678901234'),
        ('boolean', 'BOOLEAN', True, True),
        ('null', 'INTEGER', None, None),
        ('date', 'DATE', '2026-09-14', '2026-09-14'),
        ('time', 'TIME', '12:34:56.1234', '12:34:56.123400'),
        ('timestamp', 'TIMESTAMP', '2026-09-14 12:34:56.1234',
         '2026-09-14 12:34:56.123400'),
        ('time-zone', 'TIME WITH TIME ZONE', '12:34:56.1234 +02:00',
         '12:34:56.123400+02:00'),
        ('timestamp-zone', 'TIMESTAMP WITH TIME ZONE',
         '2026-09-14 12:34:56.1234 +02:00',
         '2026-09-14 12:34:56.123400+02:00'),
        ('text-blob', 'BLOB SUB_TYPE TEXT CHARACTER SET UTF8',
         'é λ text', 'é λ text'),
        ('streamed-text-blob', 'BLOB SUB_TYPE TEXT CHARACTER SET UTF8',
         'λ' * 100000, 'λ' * 100000),
        ('decfloat-nan', 'DECFLOAT(16)', 'NaN', 'NaN'),
    ]
    handle = worker = None
    try:
        handle = client.open_session({'route': route})
        for mode in ('sync', 'async'):
            queries = [
                (name, 'SELECT CAST(? AS ' + cast +
                 ') AS RESULT_VALUE FROM RDB$DATABASE', [value], expected)
                for name, cast, value, expected in cases
            ] + [
                ('binary', "SELECT CAST(x'00FF7F' AS VARBINARY(3)) "
                 'AS RESULT_VALUE FROM RDB$DATABASE', [], binary),
                ('binary-blob', "SELECT CAST(x'00FF7F' AS BLOB SUB_TYPE "
                 'BINARY) AS RESULT_VALUE FROM RDB$DATABASE', [], binary),
            ]
            for name, source, parameters, expected in queries:
                try:
                    request = {'source': source, 'parameters': parameters}
                    if mode == 'async':
                        token = client.submit_query(handle, request)
                        worker = token.worker
                        worker.join(15)
                        assert not worker.is_alive()
                    else:
                        token = client.execute(handle, request)
                    native = client.describe_result(token)
                    assert handle.main_transaction.info.is_read_only()
                    assert native['complete']
                    assert not native['payload'].get('error')
                    column = native['schema']['columns'][0]
                    assert column['metadata_source'] == (
                        'firebird-driver.IMessageMetadata')
                    assert column['native_name'] == 'RESULT_VALUE'
                    if name == 'int128':
                        assert column['native_type'] == 'INT128'
                        assert column['native_type_code'] == 32752
                    if name in {'decfloat', 'decfloat-nan'}:
                        assert column['native_type'].startswith('DECFLOAT(')
                    if name in {'time-zone', 'timestamp-zone'}:
                        assert column['native_type'].endswith('WITH TIME ZONE')
                    rows = native['payload']['rows']
                    assert len(rows) == 1 and len(rows[0]) == 1
                    value = rows[0][0]
                    assert value == expected
                    encoded = json.dumps(native, allow_nan=False)
                    decoded = json.loads(encoded)
                    assert decoded['payload']['rows'][0][0] == expected
                    result['cases'].append(mode + ':' + name)
                except Exception as exc:
                    result['failures'].append({
                        'case': mode + ':' + name,
                        'error_type': type(exc).__name__,
                        'native_codes': list(getattr(exc, 'gds_codes', ()))})
                finally:
                    if worker is None or not worker.is_alive():
                        client.control_transaction(handle, 'rollback')
            try:
                request = {
                    'source': 'SELECT 11 AS "DUP", 22 AS "DUP", '
                    '33 AS "DUP#2" FROM RDB$DATABASE', 'parameters': []}
                if mode == 'async':
                    token = client.submit_query(handle, request)
                    worker = token.worker
                    worker.join(15)
                    assert not worker.is_alive()
                else:
                    token = client.execute(handle, request)
                native = client.describe_result(token)
                columns = native['schema']['columns']
                assert [item['name'] for item in columns] == [
                    'DUP', 'DUP#3', 'DUP#2']
                assert [item['native_name'] for item in columns] == [
                    'DUP', 'DUP', 'DUP#2']
                record = dict(zip([item['name'] for item in columns],
                                  native['payload']['rows'][0]))
                assert record == {'DUP': 11, 'DUP#3': 22, 'DUP#2': 33}
                result['cases'].append(mode + ':duplicate-labels')
            except Exception as exc:
                result['failures'].append({
                    'case': mode + ':duplicate-labels',
                    'error_type': type(exc).__name__})
            finally:
                if worker is None or not worker.is_alive():
                    client.control_transaction(handle, 'rollback')
    finally:
        if worker is not None and worker.is_alive():
            worker.join(20)
        client.close()
    result['complete'] = len(result['cases']) == 36 and not result['failures']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.profiles)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
