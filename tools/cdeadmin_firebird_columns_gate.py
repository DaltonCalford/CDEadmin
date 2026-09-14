#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Native column alteration matrix in a uniquely named disposable database."""

import argparse
import json
import secrets
import traceback
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from cdeadmin_firebird_admin_mapping_gate import (
    ADMINISTRATION, _create_client, _route_arguments, _resources,
)
from pgadmin.cdeadmin.providers.firebird import columns
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def run(profiles):
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    route['database'] = str(PurePosixPath(route['database']).parent /
                            ('cdeadmin_columns_' + uuid.uuid4().hex + '.fdb'))
    client = _create_client(SimpleNamespace(acquire_secret=None))
    import firebird.driver as driver
    connection = driver.create_database(
        password=route['password'], **_route_arguments(route, driver))
    result = {'schema': 'cdeadmin.firebird-columns.v1', 'passed': False,
              'checks': [], 'failures': [], 'task_evidence': {},
              'fixture_database': route['database'], 'fixture_removed': False}
    test_user = 'CDE_COL_' + uuid.uuid4().hex[:16].upper()
    test_password = secrets.token_urlsafe(24)
    user_created = False
    result['temporary_user_removed'] = False

    def execute(sql, parameters=()):
        with connection.cursor() as cursor:
            cursor.execute(sql, parameters)

    def scalar(sql):
        with connection.cursor() as cursor:
            cursor.execute(sql)
            value = cursor.fetchone()[0]
            if callable(getattr(value, 'read', None)):
                reader = value
                value = reader.read()
                reader.close()
            return value

    def apply(draft, operation='alter', session=None):
        plan = ADMINISTRATION.plan({
            '_provider_route': route, 'resource_kind': 'column',
            'operation_id': operation, 'draft': draft,
            'target_resource': {'display_name': 'V',
                                'display_path': ['CDE_COLUMN', 'V']}})
        ADMINISTRATION.apply(client, plan, connection=session or connection)
        sql = [item['source']
               for item in plan['command_preview']['statements']]
        return sql

    def case(label, definition, draft, query, expected, operation='alter'):
        try:
            execute('CREATE TABLE CDE_COLUMN (X INTEGER, V ' +
                    definition + ')')
            connection.commit()
            before = scalar(query)
            connection.commit()
            apply(draft, operation)
            connection.rollback()
            assert scalar(query) == before, 'rollback changed native metadata'
            connection.commit()
            sql = apply(draft, operation)
            connection.commit()
            actual = scalar(query)
            assert actual == expected, f'{actual!r} != {expected!r}'
            result['task_evidence']['visual_admin.column.' + operation] = {
                'statements': sql, 'live_execution': 'passed'}
            result['checks'].append({'case': label, 'statements': sql,
                                     'observed': actual,
                                     'rollback_preserved': True})
        except Exception:
            result['failures'].append({'case': label,
                                       'traceback': traceback.format_exc()})
        finally:
            if connection.main_transaction.is_active():
                connection.rollback()
            execute('DROP TABLE CDE_COLUMN')
            connection.commit()

    field = (" FROM RDB$RELATION_FIELDS RF JOIN RDB$FIELDS F ON "
             "F.RDB$FIELD_NAME = RF.RDB$FIELD_SOURCE "
             "WHERE RF.RDB$RELATION_NAME = 'CDE_COLUMN' "
             "AND RF.RDB$FIELD_NAME = 'V'")
    try:
        result['engine_version'] = scalar(
            "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
            'FROM RDB$DATABASE')
        connection.commit()
        assert result['engine_version'] == '5.0.4'
        case('position', 'INTEGER', {'action': 'POSITION', 'position': 1},
             'SELECT RF.RDB$FIELD_POSITION' + field, 0)
        case('set-not-null', 'INTEGER', {'action': 'SET NOT NULL'},
             'SELECT RF.RDB$NULL_FLAG' + field, 1)
        case('drop-not-null', 'INTEGER NOT NULL', {'action': 'DROP NOT NULL'},
             'SELECT RF.RDB$NULL_FLAG' + field, None)
        case('drop-default', 'INTEGER DEFAULT 7', {'action': 'DROP DEFAULT'},
             'SELECT RF.RDB$DEFAULT_SOURCE' + field, None)
        for kind in columns.DEFAULTS:
            values = {'TEXT': "O'Connor é", 'BINARY': '414243',
                      'NUMBER': '-1.25e2',
                      'DATE': '2026-09-13', 'TIME': '12:34:56',
                      'TIMESTAMP': '2026-09-13 12:34:56'}
            draft = {'action': 'SET DEFAULT', 'default_kind': kind,
                     'default_value': values.get(kind, '')}
            case('default-' + kind, 'VARCHAR(128)', draft,
                 'SELECT RF.RDB$DEFAULT_SOURCE' + field,
                 'DEFAULT ' + columns.default_value(draft))
        for kind in columns.TIMED_DEFAULTS:
            for precision in range(4):
                draft = {'action': 'SET DEFAULT', 'default_kind': kind,
                         'time_precision': precision}
                case(f'default-{kind}-{precision}', 'VARCHAR(128)', draft,
                     'SELECT RF.RDB$DEFAULT_SOURCE' + field,
                     'DEFAULT ' + columns.default_value(draft))
        for name in columns.TYPES:
            if name == 'BLOB':
                case('type-BLOB', 'INTEGER COMPUTED BY (X + 1)',
                     {'action': 'TYPE COMPUTED', 'data_type': 'BLOB',
                      'blob_subtype': 1, 'character_set': 'UTF8',
                      'segment_size': 120,
                      'expression': "CAST('text' AS BLOB SUB_TYPE TEXT)"},
                     'SELECT F.RDB$FIELD_TYPE' + field, 261)
                continue
            if name == 'DOMAIN':
                execute('CREATE DOMAIN CDE_DOMAIN AS BIGINT')
                connection.commit()
            draft = {'action': 'TYPE', 'data_type': name,
                     'length': 30, 'domain': 'CDE_DOMAIN'}
            # Type changes need a compatible source, not an arbitrary INTEGER.
            definition = columns.data_type(draft)
            query = 'SELECT F.RDB$FIELD_TYPE' + field
            execute('CREATE TABLE CDE_COLUMN (X INTEGER, V ' +
                    definition + ')')
            connection.commit()
            expected = scalar(query)
            connection.commit()
            execute('DROP TABLE CDE_COLUMN')
            connection.commit()
            case('type-' + name, definition, draft, query, expected)
        for action in ('COMPUTED', 'TYPE COMPUTED'):
            case(action, 'INTEGER COMPUTED BY (X + 1)',
                 {'action': action, 'data_type': 'BIGINT',
                  'expression': 'X + 2'},
                 'SELECT F.RDB$COMPUTED_SOURCE' + field, '(X + 2\n)')
        case('computed-line-comment', 'INTEGER COMPUTED BY (X + 1)',
             {'action': 'COMPUTED', 'expression': 'X + 2 -- comment'},
             'SELECT F.RDB$COMPUTED_SOURCE' + field, '(X + 2 -- comment\n)')
        case('identity-always', 'BIGINT GENERATED BY DEFAULT AS IDENTITY',
             {'action': 'IDENTITY', 'generation': 'ALWAYS'},
             'SELECT RF.RDB$IDENTITY_TYPE' + field, 0)
        case('identity-by-default', 'BIGINT GENERATED ALWAYS AS IDENTITY',
             {'action': 'IDENTITY', 'generation': 'BY DEFAULT'},
             'SELECT RF.RDB$IDENTITY_TYPE' + field, 1)
        case('drop-identity', 'BIGINT GENERATED ALWAYS AS IDENTITY',
             {'action': 'DROP IDENTITY'},
             'SELECT RF.RDB$IDENTITY_TYPE' + field, None)
        identity_variants = [
            ({'restart': 'ORIGINAL'}, 'RESTART'),
            ({'restart': 'WITH VALUE', 'restart_value': '42'},
             'RESTART WITH 42'),
            ({'increment': '3'}, 'SET INCREMENT BY 3'),
            ({'increment': '-2'}, 'SET INCREMENT BY -2'),
            ({'generation': 'BY DEFAULT', 'restart': 'WITH VALUE',
              'restart_value': '-42', 'increment': '-3'},
             'SET GENERATED BY DEFAULT RESTART WITH -42 SET INCREMENT BY -3'),
        ]
        for number, (draft, native_clause) in enumerate(identity_variants):
            label = 'identity-state-' + str(number)
            try:
                for table in ('CDE_COLUMN', 'NATIVE_COLUMN'):
                    execute(f'CREATE TABLE {table} (V BIGINT GENERATED ALWAYS '
                            'AS IDENTITY (START WITH 10))')
                connection.commit()
                observations = []
                for commit in (False, True):
                    sql = apply({'action': 'IDENTITY', **draft})
                    execute('ALTER TABLE NATIVE_COLUMN ALTER COLUMN V ' +
                            native_clause)
                    if commit:
                        connection.commit()
                    else:
                        connection.rollback()
                    provider_value = scalar(
                        'INSERT INTO CDE_COLUMN DEFAULT VALUES RETURNING V')
                    native_value = scalar(
                        'INSERT INTO NATIVE_COLUMN DEFAULT VALUES RETURNING V')
                    assert provider_value == native_value
                    observations.append({
                        'boundary': 'commit' if commit else 'rollback',
                        'next_value': native_value})
                    connection.commit()
                result['checks'].append({'case': label, 'statements': sql,
                                         'native_comparison': observations})
                result['task_evidence']['visual_admin.column.alter'] = {
                    'statements': sql, 'live_execution': 'passed'}
            except Exception:
                result['failures'].append({
                    'case': label, 'traceback': traceback.format_exc()})
            finally:
                if connection.main_transaction.is_active():
                    connection.rollback()
                for table in ('CDE_COLUMN', 'NATIVE_COLUMN'):
                    execute('DROP TABLE ' + table)
                connection.commit()
        for value in ("  'é'  ", ''):
            case('comment-' + ('set' if value else 'clear'), 'INTEGER',
                 {'description': value}, 'SELECT RF.RDB$DESCRIPTION' + field,
                 value or None, 'comment')
        execute('CREATE USER ' + test_user + ' PASSWORD ' +
                columns.literal(test_password))
        connection.commit()
        user_created = True
        execute('CREATE TABLE CDE_COLUMN (V INTEGER)')
        execute('GRANT SELECT ON CDE_COLUMN TO USER ' + test_user)
        connection.commit()
        other = driver.connect(password=test_password,
                               **_route_arguments({**route, 'user': test_user},
                                                  driver))
        try:
            for operation, draft in (
                    ('alter', {'action': 'SET NOT NULL'}),
                    ('comment', {'description': 'denied'})):
                label = 'permission-denied-' + operation
                try:
                    apply(draft, operation, other)
                except RelationalClientError:
                    assert scalar('SELECT RF.RDB$NULL_FLAG' + field) is None
                    assert scalar('SELECT RF.RDB$DESCRIPTION' + field) is None
                    connection.commit()
                    result['checks'].append({'case': label,
                                             'native_unchanged': True})
                else:
                    result['failures'].append({'case': label,
                                               'error': 'mutation allowed'})
        finally:
            other.close()
        execute('INSERT INTO CDE_COLUMN (V) VALUES (NULL)')
        execute('INSERT INTO CDE_COLUMN (V) VALUES (1)')
        connection.commit()
        try:
            apply({'action': 'SET NOT NULL'})
            connection.commit()
        except (RelationalClientError, driver.DatabaseError):
            if connection.main_transaction.is_active():
                connection.rollback()
            assert scalar('SELECT RF.RDB$NULL_FLAG' + field) is None
            assert scalar('SELECT COUNT(*) FROM CDE_COLUMN') == 2
            assert scalar(
                'SELECT COUNT(*) FROM CDE_COLUMN WHERE V IS NULL') == 1
            connection.commit()
            result['checks'].append({'case': 'existing-null-rejected',
                                     'rows_and_metadata_unchanged': True})
        else:
            result['failures'].append({'case': 'existing-null-rejected',
                                       'error': 'invalid mutation allowed'})
        execute('DROP TABLE CDE_COLUMN')
        connection.commit()
        execute('CREATE TABLE CDE_COLUMN (V VARCHAR(20))')
        connection.commit()
        execute("INSERT INTO CDE_COLUMN VALUES ('preserve this value')")
        connection.commit()
        try:
            apply({'action': 'TYPE', 'data_type': 'VARCHAR', 'length': 5})
            connection.commit()
        except (RelationalClientError, driver.DatabaseError):
            if connection.main_transaction.is_active():
                connection.rollback()
            assert scalar('SELECT F.RDB$CHARACTER_LENGTH' + field) == 20
            assert scalar('SELECT V FROM CDE_COLUMN') == 'preserve this value'
            connection.commit()
            result['checks'].append({'case': 'type-narrowing-rejected',
                                     'rows_and_metadata_unchanged': True})
        else:
            result['failures'].append({'case': 'type-narrowing-rejected',
                                       'error': 'invalid mutation allowed'})
        execute('DROP TABLE CDE_COLUMN')
        connection.commit()
        execute('CREATE TABLE CDE_COLUMN (X INTEGER, '
                'V BIGINT GENERATED ALWAYS AS IDENTITY '
                '(START WITH 25 INCREMENT BY 5), '
                'C NUMERIC(18, 2) COMPUTED BY (X * 1.25), '
                'A INTEGER[-2:3, 1:2], D CDE_DOMAIN DEFAULT 42, '
                'B BLOB SUB_TYPE TEXT SEGMENT SIZE 120 CHARACTER SET UTF8, '
                'C2 VARCHAR(20) CHARACTER SET UTF8 '
                "COMPUTED BY ('abc'), "
                'S VARCHAR(20) CHARACTER SET UTF8 COLLATE UNICODE_CI) '
                'SQL SECURITY INVOKER DISABLE PUBLICATION')
        connection.commit()

        def fingerprint():
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT TRIM(RF.RDB$FIELD_NAME), F.RDB$FIELD_TYPE, '
                    'F.RDB$FIELD_PRECISION, F.RDB$FIELD_SCALE, '
                    'F.RDB$FIELD_SUB_TYPE, F.RDB$CHARACTER_LENGTH, '
                    'F.RDB$CHARACTER_SET_ID, F.RDB$SEGMENT_LENGTH, '
                    'COALESCE(RF.RDB$COLLATION_ID, F.RDB$COLLATION_ID), '
                    'RF.RDB$NULL_FLAG, '
                    'RF.RDB$IDENTITY_TYPE, G.RDB$INITIAL_VALUE, '
                    'G.RDB$GENERATOR_INCREMENT FROM RDB$RELATION_FIELDS RF '
                    'JOIN RDB$FIELDS F ON '
                    'F.RDB$FIELD_NAME = RF.RDB$FIELD_SOURCE '
                    'LEFT JOIN RDB$GENERATORS G ON '
                    'G.RDB$GENERATOR_NAME = RF.RDB$GENERATOR_NAME '
                    "WHERE RF.RDB$RELATION_NAME = 'CDE_COLUMN' ORDER BY 1")
                values = [list(row) for row in cursor.fetchall()]
                cursor.execute('SELECT TRIM(RF.RDB$FIELD_NAME), '
                               'D.RDB$DIMENSION, D.RDB$LOWER_BOUND, '
                               'D.RDB$UPPER_BOUND FROM RDB$RELATION_FIELDS RF '
                               'JOIN RDB$FIELD_DIMENSIONS D ON '
                               'D.RDB$FIELD_NAME = RF.RDB$FIELD_SOURCE '
                               "WHERE RF.RDB$RELATION_NAME = 'CDE_COLUMN' "
                               'ORDER BY 1, 2')
                dimensions = [list(row) for row in cursor.fetchall()]
            security = scalar('SELECT RDB$SQL_SECURITY FROM RDB$RELATIONS '
                              "WHERE RDB$RELATION_NAME = 'CDE_COLUMN'")
            relation_type = scalar('SELECT RDB$RELATION_TYPE '
                                   'FROM RDB$RELATIONS '
                                   "WHERE RDB$RELATION_NAME = 'CDE_COLUMN'")
            publication = scalar('SELECT COUNT(*) FROM RDB$PUBLICATION_TABLES '
                                 "WHERE RDB$TABLE_NAME = 'CDE_COLUMN'")
            connection.commit()
            return {'columns': values, 'sql_security': security,
                    'relation_type': relation_type,
                    'array_dimensions': dimensions,
                    'publication_membership': publication}

        def recreate(label):
            before = fingerprint()
            resources = _resources(connection, {'route': route})
            connection.commit()
            table = next(item for item in resources if
                         item['resource_kind'] == 'table' and
                         item['display_name'] == 'CDE_COLUMN')
            ddl = table['native']['ddl']
            execute('DROP TABLE CDE_COLUMN')
            connection.commit()
            execute(ddl.rstrip().removesuffix(';'))
            connection.commit()
            after = fingerprint()
            if after == before:
                result['checks'].append({'case': label,
                                         'native_fingerprint': after})
            else:
                result['failures'].append({'case': label,
                                           'before': before, 'after': after,
                                           'ddl': ddl})
            execute('DROP TABLE CDE_COLUMN')
            connection.commit()

        recreate('column-ddl-recreation')
        for retention in ('PRESERVE', 'DELETE'):
            for security in ('DEFINER', 'INVOKER'):
                execute('CREATE GLOBAL TEMPORARY TABLE CDE_COLUMN '
                        '(V INTEGER, S VARCHAR(12)) ON COMMIT ' + retention +
                        ' ROWS, SQL SECURITY ' + security)
                connection.commit()
                recreate('gtt-ddl-' + retention + '-' + security)

        def quote(name):
            return '"' + name.replace('"', '""') + '"'

        for table_name, column_name in (
                ('current', 'V'), ('T', 'current'), ('current', 'current'),
                (' leading table', ' leading column'),
                ('table.name', 'column.name'), ('table"name', 'column"name')):
            execute(f'CREATE TABLE {quote(table_name)} '
                    f'({quote(column_name)} INTEGER)')
            connection.commit()
            resources = _resources(connection, {'route': route})
            connection.commit()
            targets = [item for item in resources if
                       item['resource_kind'] == 'column' and
                       item['display_path'] == [table_name, column_name]]
            label = 'quoted-column-' + table_name + '-' + column_name
            if len(targets) != 1:
                result['failures'].append({'case': label,
                                           'error': 'exact path missing'})
            else:
                plan = ADMINISTRATION.plan({
                    '_provider_route': route, 'resource_kind': 'column',
                    'operation_id': 'alter',
                    'draft': {'action': 'SET DEFAULT',
                              'default_kind': 'NUMBER',
                              'default_value': '42'},
                    'target_resource': targets[0]})
                ADMINISTRATION.apply(client, plan, connection=connection)
                connection.commit()
                execute(f'INSERT INTO {quote(table_name)} DEFAULT VALUES')
                assert scalar(f'SELECT {quote(column_name)} '
                              f'FROM {quote(table_name)}') == 42
                connection.rollback()
                result['checks'].append({'case': label})
            execute('DROP TABLE ' + quote(table_name))
            connection.commit()
        result['passed'] = not result['failures']
    except Exception:
        result['failures'].append({'case': 'infrastructure',
                                   'traceback': traceback.format_exc()})
    finally:
        if user_created:
            if connection.main_transaction.is_active():
                connection.rollback()
            execute('DROP USER ' + test_user)
            connection.commit()
        result['temporary_user_removed'] = True
        connection.drop_database()
        result['fixture_removed'] = True
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
    return 0 if result['passed'] and result['fixture_removed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
