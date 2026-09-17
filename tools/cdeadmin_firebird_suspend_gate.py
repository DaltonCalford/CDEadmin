#!/usr/bin/env python3
"""Compare native and provider SUSPEND failure boundaries on owned Firebird."""
import argparse
import json
from pathlib import Path

if __package__:
    from . import cdeadmin_firebird_views_gate as base
else:
    import cdeadmin_firebird_views_gate as base


HEADER = 'BEGIN PROCEDURE P(K INTEGER) RETURNS(N INTEGER); END'
BODY = ('BEGIN PROCEDURE P(K INTEGER) RETURNS(N INTEGER) AS BEGIN '
        'INSERT INTO SUSPEND_DATA VALUES (:K, 1); N = 1; SUSPEND; '
        'INSERT INTO SUSPEND_DATA VALUES (:K, 2); '
        'EXCEPTION SUSPEND_FAILURE; END END')


def verify(connection, client, route, password, result):
    import firebird.driver as native
    from pgadmin.cdeadmin.providers.firebird.provider import ADMINISTRATION
    from pgadmin.cdeadmin.providers.firebird.error_diagnostics import (
        status_codes,
    )
    from pgadmin.cdeadmin.providers.firebird.query_client import (
        QUERY_FAILURE_NOTICE,
    )
    admin = client.open_session({'route': route})
    checks = result['suspend_checks'] = []

    def sql(handle, source, params=()):
        with handle.cursor() as cursor:
            cursor.execute(source, params)
            return cursor.fetchall() if cursor.description else []

    def rows(key):
        observer = client.open_session({'route': route})
        try:
            return sql(observer, 'SELECT N FROM SUSPEND_DATA WHERE K = ? '
                       'ORDER BY N', (key,))
        finally:
            client.close_session(observer)

    try:
        for source in [
                'CREATE TABLE SUSPEND_DATA (K INTEGER, N INTEGER)',
                "CREATE EXCEPTION SUSPEND_FAILURE 'Owned fetch failure'"]:
            sql(admin, source)
            client.control_transaction(admin, 'commit')
        plan = ADMINISTRATION.plan({
            'resource_kind': 'package', 'operation_id': 'create',
            '_provider_route': route,
            'draft': {'name': 'SUSPEND_PACKAGE', 'header': HEADER,
                      'body': BODY}})
        ADMINISTRATION.apply(client, plan, connection=admin)
        client.control_transaction(admin, 'commit')
        key = 0
        for api in ('native', 'provider'):
            for invocation in ('select', 'execute'):
                for action in ('commit', 'rollback'):
                    key += 1
                    handle = client.open_session({'route': route})
                    case = f'{api}:{invocation}:{action}'
                    try:
                        sql(handle, 'INSERT INTO SUSPEND_DATA VALUES (?, 0)',
                            (key,))
                        transaction = handle.main_transaction.info.id
                        source = ('SELECT N FROM SUSPEND_PACKAGE.P(?)'
                                  if invocation == 'select' else
                                  'EXECUTE PROCEDURE SUSPEND_PACKAGE.P(?)')
                        codes = []
                        fetched = []
                        if api == 'native':
                            try:
                                with handle.cursor() as cursor:
                                    cursor.execute(source, (key,))
                                    while True:
                                        row = cursor.fetchone()
                                        if row is None:
                                            break
                                        fetched.append(row)
                            except native.DatabaseError as exc:
                                codes = list(status_codes(exc))
                        else:
                            token = client.submit_query(handle, {
                                'source': source, 'parameters': [key]})
                            token.worker.join(20)
                            assert not token.worker.is_alive()
                            observed = client.describe_result(token)
                            assert observed['complete']
                            payload = observed['payload']
                            fetched = payload['rows']
                            if invocation == 'select':
                                assert payload['execution_state'] == 'failed'
                                codes = payload['error']['native_status_codes']
                                assert QUERY_FAILURE_NOTICE in payload[
                                    'error']['message']
                                assert fetched == []
                            else:
                                assert payload['execution_state'] == (
                                    'succeeded')
                        if invocation == 'select':
                            assert 335544517 in codes, codes
                        else:
                            assert not codes
                            assert fetched == [(1,)], fetched
                        assert handle.main_transaction.info.id == transaction
                        pending = sql(handle, 'SELECT N FROM SUSPEND_DATA '
                                      'WHERE K = ? ORDER BY N', (key,))
                        assert pending == [(0,), (1,)], pending
                        assert rows(key) == []
                        client.control_transaction(handle, action)
                        expected = pending if action == 'commit' else []
                        assert rows(key) == expected
                        checks.append({'case': case, 'passed': True,
                                       'fetched_rows': fetched,
                                       'native_status_codes': codes,
                                       'pending_rows': pending})
                    except Exception as exc:
                        result['failures'].append({
                            'case': case, 'error_type': type(exc).__name__,
                            'message': str(exc).replace(
                                password, '<redacted>')})
                    finally:
                        client.close_session(handle)
    finally:
        client.close_session(admin)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite evidence')
    result = base.run(extra_checks=verify)
    checks = result.get('suspend_checks', [])
    result['complete'] = (result['complete'] and len(checks) == 8 and
                          all(check['passed'] for check in checks))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
