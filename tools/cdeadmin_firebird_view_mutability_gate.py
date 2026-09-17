#!/usr/bin/env python3
"""Measure native view mutation boundaries before enabling grid edits."""
import argparse
import json
from pathlib import Path

if __package__:
    from . import cdeadmin_firebird_views_gate as base
else:
    import cdeadmin_firebird_views_gate as base


def verify(connection, client, route, password, result):
    import firebird.driver as native
    from pgadmin.cdeadmin.providers.firebird.error_diagnostics import (
        status_codes,
    )
    checks = result['view_mutability_checks'] = []

    def sql(handle, source, parameters=()):
        with handle.cursor() as cursor:
            cursor.execute(source, parameters)
            return cursor.fetchall() if cursor.description else []

    admin = client.open_session({'route': route})
    try:
        for source in (
                'CREATE TABLE VM_BASE (ID INTEGER PRIMARY KEY, V INTEGER)',
                'CREATE TABLE VM_PENDING (ID INTEGER PRIMARY KEY)',
                'CREATE VIEW VM_SIMPLE AS SELECT ID, V FROM VM_BASE',
                'CREATE VIEW VM_CALCULATED AS '
                'SELECT ID, V, V * 2 AS DOUBLED FROM VM_BASE',
                'CREATE VIEW VM_AGGREGATE AS '
                'SELECT ID, SUM(V) AS V FROM VM_BASE GROUP BY ID',
                'CREATE VIEW VM_TRIGGERED AS '
                'SELECT ID, SUM(V) AS V FROM VM_BASE GROUP BY ID',
                'CREATE TRIGGER VM_WRITE FOR VM_TRIGGERED ACTIVE '
                'BEFORE UPDATE AS BEGIN '
                'UPDATE VM_BASE SET V = NEW.V WHERE ID = OLD.ID; END'):
            sql(admin, source)
            client.control_transaction(admin, 'commit')
        cases = (
            ('VM_SIMPLE', 'V', True),
            ('VM_CALCULATED', 'V', True),
            ('VM_CALCULATED', 'DOUBLED', False),
            ('VM_AGGREGATE', 'V', False),
            ('VM_TRIGGERED', 'V', True),
        )
        executions = [(view, column, writable, api)
                      for view, column, writable in cases
                      for api in ('native', 'provider')]
        for index, (view, column, writable, api) in enumerate(executions):
            for action in ('commit', 'rollback'):
                key = index * 2 + (action == 'commit') + 1
                sql(admin, 'INSERT INTO VM_BASE VALUES (?, 10)', (key,))
                client.control_transaction(admin, 'commit')
                handle = client.open_session({'route': route})
                case = f'{api}:{view}:{column}:{action}'
                try:
                    sql(handle, 'INSERT INTO VM_PENDING VALUES (?)', (key,))
                    transaction = handle.main_transaction.info.id
                    page = base.ADMINISTRATION.read_rows(client, {
                        '_provider_route': route,
                        'target_resource': {'resource_kind': 'view',
                                            'display_path': [view]},
                    }, connection=handle)
                    assert page['editable'] is False
                    assert all(row['identity_token'] is None
                               for row in page['rows'])
                    flags = sql(handle,
                                'SELECT TRIM(RDB$FIELD_NAME), RDB$UPDATE_FLAG '
                                'FROM RDB$RELATION_FIELDS WHERE '
                                'RDB$RELATION_NAME = ? '
                                'ORDER BY RDB$FIELD_POSITION', (view,))
                    codes = []
                    source = (f'UPDATE {view} SET {column} = 30 '
                              'WHERE ID = ?')
                    if api == 'native':
                        try:
                            sql(handle, source, (key,))
                        except native.DatabaseError as exc:
                            codes = list(status_codes(exc))
                            if writable:
                                raise
                    else:
                        token = client.submit_query(handle, {
                            'source': source, 'parameters': [key]})
                        token.worker.join(20)
                        assert not token.worker.is_alive()
                        observed = client.describe_result(token)
                        assert observed['complete']
                        payload = observed['payload']
                        assert payload['execution_state'] == (
                            'succeeded' if writable else 'failed')
                        if not writable:
                            codes = payload['error']['native_status_codes']
                    assert bool(codes) is not writable
                    if not writable:
                        expected_code = (335544359 if column == 'DOUBLED'
                                         else 335544362)
                        assert expected_code in codes, codes
                    if view == 'VM_TRIGGERED':
                        # A trigger can admit UPDATE despite zero field flags.
                        assert all(flag == 0 for _name, flag in flags)
                    if column == 'DOUBLED':
                        assert dict(flags)['V'] == 1
                        assert dict(flags)['DOUBLED'] == 0
                    assert handle.main_transaction.info.id == transaction
                    expected = 30 if writable else 10
                    assert sql(handle, 'SELECT V FROM VM_BASE WHERE ID = ?',
                               (key,)) == [(expected,)]
                    observer = client.open_session({'route': route})
                    try:
                        assert sql(observer, 'SELECT V FROM VM_BASE '
                                   'WHERE ID = ?', (key,)) == [(10,)]
                        assert sql(observer, 'SELECT ID FROM VM_PENDING '
                                   'WHERE ID = ?', (key,)) == []
                    finally:
                        client.close_session(observer)
                    client.control_transaction(handle, action)
                    observer = client.open_session({'route': route})
                    try:
                        assert sql(observer, 'SELECT V FROM VM_BASE '
                                   'WHERE ID = ?', (key,)) == [
                                       (expected if action == 'commit'
                                        else 10,)]
                        assert sql(observer, 'SELECT ID FROM VM_PENDING '
                                   'WHERE ID = ?', (key,)) == (
                                       [(key,)] if action == 'commit' else [])
                    finally:
                        client.close_session(observer)
                    checks.append({'case': case, 'passed': True,
                                   'native_writable': writable,
                                   'native_status_codes': codes,
                                   'catalog_update_flags': flags,
                                   'grid_remains_read_only': True})
                except Exception as exc:
                    checks.append({'case': case, 'passed': False})
                    result['failures'].append({
                        'case': case, 'error_type': type(exc).__name__,
                        'message': str(exc).replace(password, '<redacted>')})
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
    checks = result.get('view_mutability_checks', [])
    result['complete'] = (result['complete'] and len(checks) == 20 and
                          all(check['passed'] for check in checks))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
