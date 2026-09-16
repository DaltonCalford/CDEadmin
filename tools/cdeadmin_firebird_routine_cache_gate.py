#!/usr/bin/env python3
"""Compare direct native and provider routine replacement observations."""
import argparse
import json
from pathlib import Path
from itertools import product

if __package__:
    from . import cdeadmin_firebird_views_gate as base
else:
    import cdeadmin_firebird_views_gate as base


def compare_records(records):
    expected = set(product(('direct', 'savepoint', 'provider'),
                           ('commit', 'create_rollback', 'alter_rollback'),
                           (False, True)))
    keys = [(r['mode'], r['history'], r['warm']) for r in records]
    if len(keys) != len(expected) or set(keys) != expected or any(
            type(r['warm']) is not bool or type(r['after']) is not int or
            type(r['fresh']) is not int for r in records):
        raise ValueError('Complete typed comparison matrix required')
    direct = {(r['history'], r['warm']): r for r in records
              if r['mode'] == 'direct'}
    divergences = [r for r in records if r['mode'] != 'direct' and any(
        r[field] != direct[r['history'], r['warm']][field]
        for field in ('before', 'after', 'fresh'))]
    return {'complete': not divergences and all(
                r['fresh'] == 3 for r in records),
            'divergences': divergences,
            'stale_cases': [r for r in records if r['after'] != r['fresh']]}


def verify(_connection, client, route, password, result):
    import firebird.driver as native
    from pgadmin.cdeadmin.providers.firebird.error_diagnostics import (
        status_codes,
    )
    records = result['routine_cache_checks'] = []
    failures = result['routine_cache_failures'] = []

    def connect():
        return native.connect(password=password,
                              **base._route_arguments(route, native))

    def sql(connection, source):
        with connection.cursor() as cursor:
            cursor.execute(source)
            return cursor.fetchall() if cursor.description else []

    def run_case(mode, history, warm):
        name = 'CACHE_' + mode.upper() + '_' + history.upper() + str(int(warm))
        connection = connect()

        def apply(value):
            declaration = ('RETURNS (Y INTEGER) AS BEGIN Y = ' + str(value) +
                           '; SUSPEND; END')
            source = 'CREATE OR ALTER PROCEDURE ' + name + ' ' + declaration
            if mode == 'provider':
                plan = base.ADMINISTRATION.plan({
                    'resource_kind': 'procedure',
                    'operation_id': 'create_or_alter',
                    '_provider_route': route,
                    'draft': {'name': name, 'declaration': declaration}})
                receipt = base.ADMINISTRATION.apply(
                    client, plan, connection=connection)
                assert receipt['staged_in_provider_session'] is True
            else:
                if mode == 'savepoint':
                    sql(connection, 'SAVEPOINT OWNED_TASK')
                sql(connection, source)
                if mode == 'savepoint':
                    sql(connection, 'RELEASE SAVEPOINT OWNED_TASK ONLY')

        def execute(handle):
            return sql(handle, 'EXECUTE PROCEDURE ' + name)[0][0]

        try:
            if history == 'create_rollback':
                apply(0)
                if warm:
                    execute(connection)
                connection.rollback()
            apply(1)
            connection.commit()
            before = execute(connection) if warm else None
            if connection.main_transaction.is_active():
                connection.rollback()
            if history == 'alter_rollback':
                apply(2)
                if warm:
                    execute(connection)
                connection.rollback()
            apply(3)
            connection.commit()
            after = execute(connection)
            connection.rollback()
            observer = connect()
            try:
                fresh = execute(observer)
                assert fresh == 3, fresh
            finally:
                if observer.main_transaction.is_active():
                    observer.rollback()
                observer.close()
            return {'mode': mode, 'history': history, 'warm': warm,
                    'before': before, 'after': after, 'fresh': fresh,
                    'stale': after != fresh}
        finally:
            if connection.main_transaction.is_active():
                connection.rollback()
            connection.close()

    for mode in ('direct', 'savepoint', 'provider'):
        for history in ('commit', 'create_rollback', 'alter_rollback'):
            for warm in (False, True):
                try:
                    records.append(run_case(mode, history, warm))
                except Exception as error:
                    failures.append({'mode': mode, 'history': history,
                                     'warm': warm,
                                     'error_type': type(error).__name__,
                                     'native_status_codes': list(
                                         status_codes(error))})
    if not failures:
        result['routine_cache_comparison'] = compare_records(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite evidence')
    result = base.run(extra_checks=verify)
    result['complete'] = (result['complete'] and
                          len(result.get('routine_cache_checks', [])) == 18 and
                          not result.get('routine_cache_failures') and
                          result.get('routine_cache_comparison', {}).get(
                              'complete') is True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'checks': result.get('routine_cache_checks'),
                      'failures': result['failures'] + result.get(
                          'routine_cache_failures', []),
                      'comparison': result.get('routine_cache_comparison')}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
