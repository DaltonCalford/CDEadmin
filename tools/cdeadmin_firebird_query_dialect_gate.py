#!/usr/bin/env python3
"""Qualify per-statement dialects without changing attachment/transaction."""
import argparse
import json
from pathlib import Path
import time

if __package__:
    from . import cdeadmin_firebird_dialect_observation_gate as previous
else:
    import cdeadmin_firebird_dialect_observation_gate as previous


def verify(connection, client, route, password, result):
    previous.verify(connection, client, route, password, result)
    checks = result['query_dialect_checks'] = []

    def execute(handle, source, dialect=None, parameters=(), success=True):
        token = client.submit_query(handle, {
            'source': source, 'parameters': list(parameters),
            'output_policy': {'client_sql_dialect': dialect, 'max_rows': 10}})
        deadline = time.monotonic() + 20
        while True:
            value = client.describe_result(token)
            if value['complete']:
                payload = value['payload']
                assert payload['execution_state'] == (
                    'succeeded' if success else 'failed'), value
                if success and dialect is not None:
                    assert payload['statement_sql_dialect'] == dialect
                return payload
            if time.monotonic() > deadline:
                raise AssertionError('Query did not finish')
            time.sleep(0.01)

    for stored in (1, 3):
        configured = {**route, 'database':
                      f'/var/lib/firebird/data/dialect_{stored}.fdb'}
        handle = client.open_session({'route': configured})
        try:
            execute(handle, 'CREATE TABLE DIALECT_WORK (ID INTEGER)')
            client.control_transaction(handle, 'commit')
            for dialect in (1, 2, 3):
                try:
                    execute(handle, 'SET TRANSACTION READ WRITE WAIT '
                            'ISOLATION LEVEL READ COMMITTED RECORD_VERSION',
                            dialect)
                    execute(handle, 'INSERT INTO DIALECT_WORK VALUES (?)',
                            dialect, [dialect])
                    before = handle.main_transaction.info.id
                    payload = execute(handle,
                                      'SELECT CAST(? AS INTEGER) '
                                      'FROM RDB$DATABASE', dialect, [7])
                    assert payload['rows'] == [(7,)], payload
                    if dialect == 1:
                        payload = execute(handle,
                                          'SELECT "literal" FROM '
                                          'RDB$DATABASE', dialect)
                        assert payload['rows'] == [('literal',)], payload
                    elif dialect == 2:
                        execute(handle, 'SELECT "ID" FROM DIALECT_WORK',
                                dialect, success=False)
                    else:
                        payload = execute(handle,
                                          'SELECT "ID" FROM DIALECT_WORK',
                                          dialect)
                        assert payload['rows'] == [(dialect,)], payload
                    # A generated/default query still uses dialect 3 on the
                    # exact same attachment and pending transaction.
                    payload = execute(handle, 'SELECT "ID" FROM DIALECT_WORK')
                    assert payload['rows'] == [(dialect,)], payload
                    assert handle.sql_dialect == 3
                    assert handle.main_transaction.info.id == before
                    client.control_transaction(handle, 'rollback')
                    assert execute(handle, 'SELECT ID FROM DIALECT_WORK')[
                        'rows'] == []
                    client.control_transaction(handle, 'rollback')
                    execute(handle, 'INSERT INTO DIALECT_WORK VALUES (?)',
                            dialect, [dialect])
                    execute(handle, 'COMMIT', dialect)
                    observer = client.open_session({'route': configured})
                    try:
                        observed = execute(
                            observer, 'SELECT ID FROM DIALECT_WORK')
                        assert observed['rows'] == [(dialect,)]
                    finally:
                        client.close_session(observer)
                    execute(handle, 'DELETE FROM DIALECT_WORK', dialect)
                    execute(handle, 'COMMIT', dialect)
                    checks.append({'stored_database_dialect': stored,
                                   'statement_dialect': dialect,
                                   'attachment_default_unchanged': True,
                                   'transaction_preserved': True,
                                   'parameters_and_rollback_verified': True,
                                   'native_begin_and_commit_verified': True})
                except Exception as exc:
                    result['failures'].append({
                        'case': f'query-{stored}-{dialect}',
                        'error_type': type(exc).__name__,
                        'message': str(exc).replace(password, '<redacted>')})
                    client.control_transaction(handle, 'rollback')
        finally:
            client.close_session(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite evidence')
    result = previous.base.run(extra_checks=verify)
    result['complete'] = (result['complete'] and
                          len(result.get('query_dialect_checks', [])) == 6)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
