#!/usr/bin/env python3
"""Observe client/database dialects independently in disposable Firebird."""
import argparse
import json
from pathlib import Path
import uuid

if __package__:
    from . import cdeadmin_firebird_views_gate as base
else:
    import cdeadmin_firebird_views_gate as base


def verify(connection, client, route, password, result):
    import firebird.driver as native
    from firebird.driver import core
    from pgadmin.cdeadmin.providers.firebird.database_creation import (
        create_database,
    )
    from pgadmin.cdeadmin.providers.firebird.provider import (
        _database_create_arguments, _route_arguments,
    )
    from pgadmin.cdeadmin.providers.firebird.transaction_state import (
        observe_transaction,
    )
    from pgadmin.cdeadmin.providers.firebird.error_diagnostics import (
        status_codes,
    )

    checks = result['dialect_checks'] = []
    for database_dialect in (1, 3):
        configured = {**route, 'database':
                      '/var/lib/firebird/data/dialect_' +
                      str(database_dialect) + '.fdb'}
        dsn = _route_arguments(configured)['database']
        args = _database_create_arguments(
            configured, dsn, {'sql_dialect': database_dialect}, native)
        created = create_database(native, core, password=password, **args)
        created.close()
        for client_dialect in (1, 2, 3):
            handle = None
            try:
                if client_dialect == 3:
                    # The application's supported preparation mode, through
                    # its actual retained-session path, not a test substitute.
                    handle = client.open_session({'route': configured})
                else:
                    # Native comparison only: never mutate the provider's
                    # cached configuration to pretend this mode is supported.
                    config = native.driver_config.register_database(
                        'owned_dialect_' + uuid.uuid4().hex)
                    config.dsn.value = dsn
                    config.sql_dialect.value = client_dialect
                    handle = native.connect(database=config.name,
                                            user='SYSDBA', password=password,
                                            charset='UTF8')
                idle = observe_transaction(handle)
                assert idle['state'] == 'idle', idle
                assert not handle.main_transaction.is_active()
                expected = {
                    'client_sql_dialect': {'available': True,
                                           'value': client_dialect},
                    'database_sql_dialect': {'available': True,
                                             'value': database_dialect},
                }
                assert idle['attachment_fields'] == expected, idle
                with handle.cursor() as cursor:
                    cursor.execute('SELECT 1 FROM RDB$DATABASE')
                    assert cursor.fetchone() == (1,)
                    before = handle.main_transaction.info.id
                    active = observe_transaction(handle)
                    assert active['attachment_fields'] == expected, active
                    assert (active['fields']['transaction_id']['value'] ==
                            before)
                    assert handle.main_transaction.info.id == before
                    if client_dialect == 1:
                        cursor.execute('SELECT "literal" FROM RDB$DATABASE')
                        assert cursor.fetchone() == ('literal',)
                        quote_result = 'string literal'
                    elif client_dialect == 2:
                        try:
                            cursor.execute(
                                'SELECT "RDB$RELATION_ID" FROM RDB$RELATIONS')
                        except native.DatabaseError as exc:
                            assert status_codes(exc)
                            quote_result = 'native ambiguous quote rejection'
                        else:
                            raise AssertionError('Dialect 2 accepted quotes')
                    else:
                        cursor.execute(
                            'SELECT "RDB$RELATION_ID" FROM RDB$RELATIONS '
                            "WHERE RDB$RELATION_NAME = 'RDB$DATABASE'")
                        assert type(cursor.fetchone()[0]) is int
                        quote_result = 'identifier'
                handle.rollback()
                assert observe_transaction(handle)['state'] == 'idle'
                assert not handle.main_transaction.is_active()
                checks.append({'database_dialect': database_dialect,
                               'client_dialect': client_dialect,
                               'provider_session': client_dialect == 3,
                               'double_quote_semantics': quote_result,
                               'observer_preserves_idle_and_active': True})
            except Exception as exc:
                result['failures'].append({
                    'case': (f'database-{database_dialect}-'
                             f'client-{client_dialect}'),
                    'error_type': type(exc).__name__,
                    'message': str(exc).replace(password, '<redacted>'),
                    'native_status_codes': list(status_codes(exc)),
                })
            finally:
                if handle is not None:
                    if client_dialect == 3:
                        client.close_session(handle)
                    else:
                        handle.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite evidence')
    result = base.run(extra_checks=verify)
    result['complete'] = (result['complete'] and
                          len(result.get('dialect_checks', [])) == 6)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
