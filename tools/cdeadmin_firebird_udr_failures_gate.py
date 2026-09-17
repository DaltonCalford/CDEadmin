#!/usr/bin/env python3
"""Qualify deferred UDR commit failures and caller transaction recovery."""
import argparse
import json
import traceback
from pathlib import Path

if __package__:
    from . import cdeadmin_firebird_views_gate as base
else:
    import cdeadmin_firebird_views_gate as base


def cases():
    return [
        ('missing-module', 'cdeadmin_missing_module!absent',
         'UDR module not loaded'),
        ('missing-entry', 'udrcpp_example!cdeadmin_absent',
         'Entry point not found'),
        ('invalid-entry', 'no_separator', 'Invalid entry point'),
        ('invalid-module', '../not_allowed!absent', 'Invalid module name'),
    ]


def verify(connection, client, route, password, result):
    handle = client.open_session({'route': route})
    try:
        verify_session(handle, client, route, password, result)
    finally:
        client.close_session(handle)


def verify_session(connection, client, route, password, result):
    import firebird.driver as native
    from pgadmin.cdeadmin.providers.firebird.provider import ADMINISTRATION
    from pgadmin.cdeadmin.providers.firebird.error_diagnostics import (
        status_codes,
    )
    from pgadmin.cdeadmin.sdk.relational import RelationalClientError

    checks = result['udr_failure_checks'] = []

    def sql(source, handle=connection):
        with handle.cursor() as cursor:
            cursor.execute(source)
            return cursor.fetchall() if cursor.description else []

    def rollback():
        if connection.main_transaction.is_active():
            connection.rollback()

    def savepoint_absent():
        try:
            sql('ROLLBACK TO SAVEPOINT UDR_USER_BOUNDARY')
        except native.DatabaseError as exc:
            assert 335544820 in status_codes(exc)
            assert exc.sqlstate == '3B000'
            return list(status_codes(exc))
        raise AssertionError('Commit unexpectedly retained the savepoint')

    rollback()
    sql('CREATE TABLE UDR_PENDING (ID INTEGER)')
    connection.commit()
    for kind in ('function', 'procedure'):
        for index, (case, entry, expected_message) in enumerate(cases()):
            check = {'kind': kind, 'case': case, 'passed': False}
            checks.append(check)
            try:
                rollback()
                name = 'UDR_FAIL_' + kind[0].upper() + str(index)
                declaration = ('(X INTEGER) RETURNS ' + (
                    'INTEGER' if kind == 'function' else '(Y INTEGER)') +
                    f" EXTERNAL NAME '{entry}' ENGINE UDR")
                request = {
                    'resource_kind': kind, 'operation_id': 'create_or_alter',
                    '_provider_route': route,
                    'draft': {'name': name, 'declaration': declaration}}
                plan = ADMINISTRATION.plan(request)
                source = plan['command_preview']['statements'][0]['source']
                sql('INSERT INTO UDR_PENDING VALUES (9)')
                sql('SAVEPOINT UDR_USER_BOUNDARY')
                native_transaction = connection.main_transaction.info.id
                sql(source)
                # Native control establishes the error class/message; raw
                # server text is not published by the provider-facing path.
                try:
                    connection.commit()
                except native.DatabaseError as exc:
                    native_codes = status_codes(exc)
                    assert 335544382 in native_codes, native_codes
                    assert expected_message in str(exc), str(exc)
                    check['native_codes'] = list(native_codes)
                    check['native_sqlstate'] = exc.sqlstate
                else:
                    raise AssertionError('Native commit was not rejected')
                assert connection.main_transaction.info.id == (
                    native_transaction)
                assert sql('SELECT ID FROM UDR_PENDING') == [(9,)]
                check['native_savepoint_failure'] = savepoint_absent()
                rollback()
                sql('INSERT INTO UDR_PENDING VALUES (1)')
                sql('SAVEPOINT UDR_USER_BOUNDARY')
                transaction = connection.main_transaction.info.id
                ADMINISTRATION.apply(client, plan, connection=connection)
                try:
                    client.control_transaction(connection, 'commit')
                except RelationalClientError as exc:
                    assert status_codes(exc) == native_codes
                    assert '335544382' in str(exc)
                    assert entry not in str(exc)
                    assert expected_message not in str(exc)
                    check['provider_error'] = str(exc)
                    check['provider_codes'] = list(status_codes(exc))
                else:
                    raise AssertionError('Provider commit was not rejected')
                assert connection.main_transaction.info.id == transaction
                assert sql('SELECT ID FROM UDR_PENDING') == [(1,)]
                table = ('RDB$FUNCTIONS' if kind == 'function'
                         else 'RDB$PROCEDURES')
                column = ('RDB$FUNCTION_NAME' if kind == 'function'
                          else 'RDB$PROCEDURE_NAME')
                assert sql(f'SELECT COUNT(*) FROM {table} '
                           f"WHERE {column} = '{name}'") == [(1,)]
                check['provider_savepoint_failure'] = savepoint_absent()
                assert check['provider_savepoint_failure'] == (
                    check['native_savepoint_failure'])
                if index % 2 == 0:
                    client.control_transaction(connection, 'rollback')
                    expected_rows = []
                    check['recovery'] = 'explicit-full-rollback'
                else:
                    sql(f'DROP {kind.upper()} {name}')
                    client.control_transaction(connection, 'commit')
                    expected_rows = [(1,)]
                    check['recovery'] = 'remove-invalid-definition-and-commit'
                observer = client.open_session({'route': route})
                try:
                    assert sql('SELECT ID FROM UDR_PENDING', observer) == (
                        expected_rows)
                    assert sql(f'SELECT COUNT(*) FROM {table} '
                               f"WHERE {column} = '{name}'", observer) == [
                                   (0,)]
                finally:
                    client.close_session(observer)
                sql('DELETE FROM UDR_PENDING')
                connection.commit()
                check.update(passed=True, transaction_preserved=True,
                             user_savepoint_preserved=False,
                             native_savepoint_behavior_matched=True,
                             registration_precedes_resolution=True,
                             explicit_recovery=True)
            except Exception as exc:
                result['failures'].append({
                    'case': f'udr-{kind}-{case}',
                    'error_type': type(exc).__name__,
                    'message': str(exc).replace(password, '<redacted>'),
                    'frames': [{'function': frame.name, 'line': frame.lineno}
                               for frame in traceback.extract_tb(
                                   exc.__traceback__)]})
            finally:
                rollback()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite evidence')
    result = base.run(extra_checks=verify)
    checks = result.get('udr_failure_checks', [])
    result['complete'] = (result['complete'] and
                          len(checks) == 2 * len(cases()) and
                          all(check['passed'] for check in checks))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
