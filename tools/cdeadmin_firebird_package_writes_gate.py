#!/usr/bin/env python3
"""Qualify packaged DML permissions and transactions on owned Firebird."""
import argparse
import json
import secrets
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from . import cdeadmin_firebird_views_gate as base
else:
    import cdeadmin_firebird_views_gate as base


HEADER = ('BEGIN FUNCTION F(OP INTEGER, K INTEGER) RETURNS INTEGER; '
          'PROCEDURE P(OP INTEGER, K INTEGER); END')
BODY = (
    'BEGIN FUNCTION F(OP INTEGER, K INTEGER) RETURNS INTEGER AS BEGIN '
    'IF (OP = 1) THEN INSERT INTO WRITE_DATA VALUES (:K, 9); '
    'IF (OP = 2) THEN UPDATE WRITE_DATA SET V = 9 WHERE ID = :K; '
    'IF (OP = 3) THEN DELETE FROM WRITE_DATA WHERE ID = :K; '
    'IF (OP = 4) THEN BEGIN INSERT INTO WRITE_DATA VALUES (:K, 9); '
    'INSERT INTO WRITE_DATA VALUES (:K, 10); END '
    'IF (OP = 5) THEN BEGIN INSERT INTO WRITE_DATA VALUES (:K, 9); '
    'EXCEPTION WRITE_FAILURE; END '
    'IF (OP = 6) THEN BEGIN INSERT INTO WRITE_DATA VALUES (:K, 9); '
    'INSERT INTO WRITE_DATA VALUES (:K, 10); '
    'WHEN GDSCODE unique_key_violation DO '
    'BEGIN UPDATE WRITE_DATA SET V = 11 WHERE ID = :K; END END '
    'IF (OP = 7) THEN BEGIN INSERT INTO WRITE_DATA VALUES (:K, 9); '
    'EXCEPTION WRITE_FAILURE; WHEN EXCEPTION WRITE_FAILURE DO '
    'BEGIN UPDATE WRITE_DATA SET V = 12 WHERE ID = :K; END END '
    'IF (OP = 8) THEN BEGIN INSERT INTO WRITE_DATA VALUES (:K, 9); '
    'INSERT INTO WRITE_DATA VALUES (:K, 10); '
    'WHEN GDSCODE unique_key_violation DO BEGIN '
    'UPDATE WRITE_DATA SET V = 13 WHERE ID = :K; EXCEPTION; END END '
    'IF (OP = 9) THEN BEGIN INSERT INTO WRITE_DATA VALUES (:K, 9); '
    'EXCEPTION WRITE_FAILURE; WHEN EXCEPTION WRITE_FAILURE DO BEGIN '
    'UPDATE WRITE_DATA SET V = 14 WHERE ID = :K; EXCEPTION; END END '
    'RETURN K; END '
    'PROCEDURE P(OP INTEGER, K INTEGER) AS DECLARE R INTEGER; BEGIN '
    'R = F(OP, K); END END')
MODES = ('INHERIT', 'INVOKER', 'DEFINER')
FAILING_OPS = (4, 5, 8, 9)


def verify(connection, client, route, password, result):
    from pgadmin.cdeadmin.providers.firebird.provider import ADMINISTRATION
    from pgadmin.cdeadmin.providers.firebird.character_metadata import literal

    secret = secrets.token_urlsafe(24)
    writer = base._create_client(SimpleNamespace(
        acquire_secret=lambda *_args: base.SecretLease(secret)))
    admin = client.open_session({'route': route})
    checks = result['package_write_checks'] = []

    def sql(handle, source, params=()):
        with handle.cursor() as cursor:
            cursor.execute(source, params)
            return cursor.fetchall() if cursor.description else []

    def apply(request):
        request = {**request, '_provider_route': route}
        assert ADMINISTRATION.validate(request) == {'errors': []}
        receipt = ADMINISTRATION.apply(
            client, ADMINISTRATION.plan(request), connection=admin)
        assert receipt['staged_in_provider_session']
        client.control_transaction(admin, 'commit')

    def observe(key):
        other = client.open_session({'route': route})
        try:
            return (sql(other, 'SELECT V FROM WRITE_DATA WHERE ID = ?',
                        (key,)),
                    sql(other, 'SELECT ID FROM WRITE_PENDING WHERE ID = ?',
                        (key,)))
        finally:
            client.close_session(other)

    try:
        for source in [
                'CREATE USER WRITE_READER PASSWORD ' + literal(secret),
                'CREATE TABLE WRITE_DATA (ID INTEGER PRIMARY KEY, V INTEGER)',
                'CREATE TABLE WRITE_PENDING (ID INTEGER PRIMARY KEY)',
                "CREATE EXCEPTION WRITE_FAILURE 'Owned package failure'",
                'ALTER DATABASE SET DEFAULT SQL SECURITY INVOKER',
                'GRANT SELECT ON WRITE_DATA TO USER WRITE_READER',
                'GRANT USAGE ON EXCEPTION WRITE_FAILURE TO USER WRITE_READER',
                'GRANT SELECT, INSERT ON WRITE_PENDING TO USER WRITE_READER']:
            sql(admin, source)
            client.control_transaction(admin, 'commit')
        for mode in MODES:
            apply({'resource_kind': 'package', 'operation_id': 'create',
                   'draft': {'name': 'WRITE_' + mode, 'header': HEADER,
                             'body': BODY, 'sql_security': mode}})
            sql(admin, 'GRANT EXECUTE ON PACKAGE WRITE_' + mode +
                ' TO USER WRITE_READER')
            client.control_transaction(admin, 'commit')
        key = 0
        for phase in ('execute-only', 'dml-granted', 'dml-revoked'):
            if phase != 'execute-only':
                operation = 'grant' if phase == 'dml-granted' else 'revoke'
                apply({'resource_kind': 'privilege', 'operation_id': operation,
                       'draft': {'object_type': 'TABLE',
                                 'object_name': 'WRITE_DATA',
                                 'privileges': ['INSERT', 'UPDATE', 'DELETE'],
                                 'principal_kind': 'USER',
                                 'principal': 'WRITE_READER', **(
                                     {'confirmation': 'WRITE_READER'}
                                     if operation == 'revoke' else {})}})
            for mode in MODES:
                allowed = mode == 'DEFINER' or phase == 'dml-granted'
                for op in range(1, 10):
                    for entry in ('function', 'procedure'):
                        for action in ('commit', 'rollback'):
                            key += 1
                            handle = None
                            label = f'{phase}:{mode}:{op}:{entry}:{action}'
                            try:
                                before = [(3,)] if op in (2, 3) else []
                                if before:
                                    sql(admin, 'INSERT INTO WRITE_DATA VALUES '
                                        '(?, 3)', (key,))
                                    client.control_transaction(admin, 'commit')
                                handle = writer.open_session({'route': {
                                    **route, 'user': 'WRITE_READER'}})
                                sql(handle, 'INSERT INTO WRITE_PENDING '
                                    'VALUES (?)', (key,))
                                transaction = handle.main_transaction.info.id
                                if op in FAILING_OPS:
                                    writer.execute(handle, {
                                        'source': 'SAVEPOINT BEFORE_CALL'})
                                name = 'WRITE_' + mode
                                source = (
                                    f'SELECT {name}.F(?, ?) FROM RDB$DATABASE'
                                    if entry == 'function' else
                                    f'EXECUTE PROCEDURE {name}.P(?, ?)')
                                token = writer.submit_query(handle, {
                                    'source': source, 'parameters': [op, key]})
                                token.worker.join(20)
                                assert not token.worker.is_alive()
                                observed = writer.describe_result(token)
                                assert observed['complete']
                                payload = observed['payload']
                                succeeds = allowed and op not in FAILING_OPS
                                if succeeds:
                                    assert payload['execution_state'] == (
                                        'succeeded'), payload
                                    if entry == 'function':
                                        assert payload['rows'] == [(key,)]
                                else:
                                    assert payload['execution_state'] == (
                                        'failed'), payload
                                    code = (335544352 if not allowed else
                                            335544665 if op in (4, 8) else
                                            335544517)
                                    assert code in payload['error'][
                                        'native_status_codes'], payload
                                changed = {1: [(9,)], 2: [(9,)], 3: [],
                                           6: [(11,)], 7: [(12,)]}
                                after = changed[op] if succeeds else before
                                assert handle.main_transaction.info.id == (
                                    transaction)
                                assert sql(handle, 'SELECT V FROM WRITE_DATA '
                                           'WHERE ID = ?', (key,)) == after
                                assert sql(handle, 'SELECT ID FROM '
                                           'WRITE_PENDING WHERE ID = ?',
                                           (key,)) == [(key,)]
                                assert observe(key) == (before, [])
                                if op in FAILING_OPS:
                                    assert writer.cancel(token) is False
                                    writer.execute(handle, {
                                        'source': 'ROLLBACK TO SAVEPOINT '
                                        'BEFORE_CALL'})
                                    assert sql(handle, 'SELECT V FROM '
                                               'WRITE_DATA WHERE ID = ?',
                                               (key,)) == before
                                    assert sql(handle, 'SELECT ID FROM '
                                               'WRITE_PENDING WHERE ID = ?',
                                               (key,)) == [(key,)]
                                    assert handle.main_transaction.info.id == (
                                        transaction)
                                writer.control_transaction(handle, action)
                                expected = (after, [(key,)]) if (
                                    action == 'commit') else (before, [])
                                assert observe(key) == expected
                                checks.append({'case': label, 'passed': True,
                                               'allowed': allowed,
                                               'succeeded': succeeds,
                                               'native_error_codes': (
                                                   payload['error'][
                                                       'native_status_codes']
                                                   if not succeeds else [])})
                            except Exception as exc:
                                result['failures'].append({
                                    'case': label,
                                    'error_type': type(exc).__name__,
                                    'message': str(exc).replace(
                                        secret, '<redacted>').replace(
                                            password, '<redacted>')})
                            finally:
                                if handle is not None:
                                    writer.close_session(handle)
    finally:
        client.close_session(admin)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite evidence')
    result = base.run(extra_checks=verify)
    checks = result.get('package_write_checks', [])
    result['complete'] = (result['complete'] and len(checks) == 324 and
                          all(check['passed'] for check in checks))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
