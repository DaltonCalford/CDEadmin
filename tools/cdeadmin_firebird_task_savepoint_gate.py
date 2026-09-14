#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Verify native task rollback preserves caller work and counter semantics."""

import argparse
import json
import traceback
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from cdeadmin_firebird_admin_mapping_gate import (
    ADMINISTRATION, _create_client, _route_arguments,
)
from pgadmin.cdeadmin.providers.firebird.mappings import identifier
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def run(profiles):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    route['database'] = str(PurePosixPath(route['database']).parent /
                            ('cde_task_scope_' + uuid.uuid4().hex + '.fdb'))
    password = route.pop('password')
    client = _create_client(SimpleNamespace(acquire_secret=None))
    connection = driver.create_database(password=password,
                                        **_route_arguments(route, driver))
    observer = None
    result = {'complete': False, 'cases': [], 'failures': [],
              'fixture_database': route['database'], 'fixture_removed': False}

    def execute(source):
        with connection.cursor() as cursor:
            cursor.execute(source)

    def query(source, current=None):
        with (current or connection).cursor() as cursor:
            cursor.execute(source)
            return cursor.fetchall()

    def rows(current=None):
        return query('SELECT ID, V FROM T ORDER BY ID', current)

    def external_rows():
        if observer.main_transaction.is_active():
            observer.rollback()
        return rows(observer)

    def transaction_id():
        return query('SELECT CURRENT_TRANSACTION FROM RDB$DATABASE')[0][0]

    def apply(statements):
        return ADMINISTRATION.apply(client, {'provider_payload': {
            'route': route, 'compiled': {'statements': statements}}},
            connection=connection)

    def statement(source, expected=None):
        return {'source': source, 'parameters': (), **(
            {'expected_rowcount': expected} if expected is not None else {})}

    def counter(name):
        return query('SELECT GEN_ID(' + identifier(name) +
                     ', 0) FROM RDB$DATABASE')[0][0]

    try:
        result['engine_version'] = query(
            "SELECT RDB$GET_CONTEXT('SYSTEM','ENGINE_VERSION') "
            'FROM RDB$DATABASE')[0][0]
        assert result['engine_version'] == '5.0.4'
        execute('CREATE TABLE T (ID INTEGER PRIMARY KEY, V INTEGER NOT NULL)')
        execute('CREATE TABLE I (ID BIGINT GENERATED ALWAYS AS IDENTITY '
                'PRIMARY KEY, V INTEGER NOT NULL)')
        execute('CREATE SEQUENCE S')
        connection.commit()
        identity = query('SELECT TRIM(TRAILING FROM RDB$GENERATOR_NAME) '
                         "FROM RDB$RELATION_FIELDS WHERE RDB$RELATION_NAME = "
                         "'I' AND RDB$FIELD_NAME = 'ID'")[0][0]
        connection.commit()
        observer = driver.connect(password=password,
                                  **_route_arguments(route, driver))
        for case in ('failed-dml', 'verification-failure', 'protected-ddl',
                     'successful-task-rollback', 'successful-task-commit',
                     'prior-savepoint', 'sequence-effects',
                     'identity-effects'):
            try:
                if connection.main_transaction.is_active():
                    connection.rollback()
                execute('DELETE FROM T')
                execute('DELETE FROM I')
                connection.commit()
                execute('INSERT INTO T VALUES (1, 10)')
                original_id = transaction_id()
                assert external_rows() == []
                observed = {'case': case, 'transaction_id': original_id}
                if case in ('successful-task-rollback',
                            'successful-task-commit', 'prior-savepoint'):
                    if case == 'prior-savepoint':
                        execute('SAVEPOINT USER_BOUNDARY')
                        execute('UPDATE T SET V = 11 WHERE ID = 1')
                    applied = apply([
                        statement('INSERT INTO T VALUES (2, 20)')])
                    assert applied['commit_requested'] is False
                    assert applied['rollback_requested'] is False
                    assert applied['native_task_scope']['released'] is True
                    assert applied['staged_in_provider_session'] is True
                    assert transaction_id() == original_id
                    assert external_rows() == []
                    if case == 'prior-savepoint':
                        execute('ROLLBACK TO SAVEPOINT USER_BOUNDARY')
                        assert rows() == [(1, 10)]
                        connection.rollback()
                    elif case == 'successful-task-rollback':
                        connection.rollback()
                        assert rows() == [] and external_rows() == []
                    else:
                        connection.commit()
                        assert external_rows() == [(1, 10), (2, 20)]
                    observed['caller_finality_verified'] = True
                else:
                    native_counter = identity if case == 'identity-effects' \
                        else 'S'
                    before_counter = counter(native_counter)
                    expected_error = '335544347'
                    statements = [statement('INSERT INTO T VALUES (2, 20)'),
                                  statement('INSERT INTO T VALUES (3, NULL)')]
                    if case == 'verification-failure':
                        statements = [statement(
                            'UPDATE T SET V = 30 WHERE ID = 1', 2)]
                        expected_error = 'row identity'
                    elif case == 'sequence-effects':
                        statements[0] = statement(
                            'INSERT INTO T VALUES (2, NEXT VALUE FOR S)')
                    elif case == 'identity-effects':
                        statements[0] = statement(
                            'INSERT INTO I(V) VALUES(20)')
                    try:
                        if case == 'protected-ddl':
                            expected_error = '335544342'
                            plan = ADMINISTRATION.plan({
                                '_provider_route': route,
                                'resource_kind': 'column',
                                'operation_id': 'rename', 'target_resource': {
                                    'display_path': ['T', 'ID']},
                                'draft': {'new_name': 'NEW_ID'},
                            })
                            ADMINISTRATION.apply(client, plan,
                                                 connection=connection)
                        else:
                            apply(statements)
                    except RelationalClientError as error:
                        assert expected_error in str(error), str(error)
                        observed['native_error'] = str(error)
                    else:
                        raise AssertionError('Expected failure was not raised')
                    assert transaction_id() == original_id
                    assert rows() == [(1, 10)]
                    assert external_rows() == []
                    if case in ('sequence-effects', 'identity-effects'):
                        after_counter = counter(native_counter)
                        assert after_counter == before_counter + 1
                        assert query('SELECT ID, V FROM I') == []
                        observed['nontransactional_counter_preserved'] = {
                            'before': before_counter, 'after': after_counter}
                    connection.commit()
                    assert external_rows() == [(1, 10)]
                    observed['earlier_caller_work_preserved'] = True
                result['cases'].append(observed)
            except Exception:
                result['failures'].append({'case': case,
                                          'traceback': traceback.format_exc()})
        legacy = None
        try:
            if connection.main_transaction.is_active():
                connection.rollback()
            execute('DELETE FROM T')
            connection.commit()
            alias = 'owned_dialect_one_' + uuid.uuid4().hex
            configuration = driver.driver_config.register_database(alias)
            configuration.database.value = (
                f'{route["host"]}/{route["port"]}:' + route['database'])
            configuration.sql_dialect.value = 1
            legacy = driver.connect(alias, password=password,
                                    user=route.get('user', 'SYSDBA'))
            assert legacy.sql_dialect == 1
            with legacy.cursor() as cursor:
                cursor.execute('INSERT INTO T VALUES (500, 10)')
            before = query('SELECT CURRENT_TRANSACTION FROM RDB$DATABASE',
                           legacy)[0][0]
            try:
                ADMINISTRATION.apply(client, {'provider_payload': {
                    'route': route, 'compiled': {'statements': [
                        statement('INSERT INTO T VALUES (501, 20)'),
                        statement('INSERT INTO T VALUES (502, NULL)'),
                    ]}}}, connection=legacy)
            except RelationalClientError as error:
                assert '335544347' in str(error), str(error)
            else:
                raise AssertionError('Expected SQL dialect 1 native failure')
            assert rows(legacy) == [(500, 10)]
            assert query('SELECT CURRENT_TRANSACTION FROM RDB$DATABASE',
                         legacy)[0][0] == before
            legacy.commit()
            assert external_rows() == [(500, 10)]
            result['cases'].append({'case': 'sql-dialect-one',
                                    'earlier_caller_work_preserved': True})
        except Exception:
            result['failures'].append({'case': 'sql-dialect-one',
                                       'traceback': traceback.format_exc()})
        finally:
            if legacy is not None:
                legacy.close()
    except Exception:
        result['failures'].append({'case': 'fixture-setup',
                                   'traceback': traceback.format_exc()})
    finally:
        if observer is not None:
            try:
                observer.close()
            except Exception:
                result['failures'].append({'case': 'observer-cleanup',
                                          'traceback': traceback.format_exc()})
        try:
            if connection.main_transaction.is_active():
                connection.rollback()
            connection.drop_database()
            result['fixture_removed'] = True
        except Exception:
            result['failures'].append({'case': 'database-cleanup',
                                       'traceback': traceback.format_exc()})
    result['complete'] = (not result['failures'] and result['fixture_removed']
                          and len(result['cases']) == 9)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.profiles)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'cases': len(result['cases']),
                      'fixture_removed': result['fixture_removed'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
