#!/usr/bin/env python3
"""Exercise Firebird index variants on a uniquely named disposable table."""

import argparse
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

from cdeadmin_firebird_constraint_editor_gate import (
    ADMINISTRATION, ROOT, _create_client, _route_arguments,
)
from pgadmin.cdeadmin.providers.firebird.provider import _resources
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from cdeadmin_firebird_index_permissions import verify as verify_permissions
from cdeadmin_firebird_system_privileges import verify as verify_privileges


def run(profiles_path):
    from firebird import driver
    document = json.loads(profiles_path.read_text())
    profile = next(dict(p) for p in document['profiles']
                   if p['engine'] == 'firebird')
    profile.setdefault('host', document.get('host', '127.0.0.1'))
    client = _create_client(SimpleNamespace(acquire_secret=None))
    connection = driver.connect(password=profile['password'],
                                **_route_arguments(profile, driver))
    table = 'CDE_IX_' + uuid.uuid4().hex[:12].upper()
    result = {'status': 'failed', 'checks': [], 'table': table}
    created = False

    def execute(sql, parameters=(), fetch=False):
        with connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            return cursor.fetchall() if fetch else None

    def apply(operation, draft, name, commit=True):
        plan = ADMINISTRATION.plan({
            '_provider_route': profile,
            'resource_kind': 'index', 'operation_id': operation,
            'draft': draft, 'target_resource': {
                'display_path': [table, name], 'display_name': name,
                'resource_kind': 'index', 'resource_id': 'index:' + name}})
        ADMINISTRATION.apply(client, plan, connection=connection)
        if commit:
            connection.commit()

    try:
        verify_privileges(connection, client, profile, result)
        execute(f'CREATE TABLE "{table}" (ID INTEGER, V VARCHAR(20))')
        connection.commit()
        created = True
        for kind in ('columns', 'expression'):
            for direction in ('ASCENDING', 'DESCENDING'):
                for partial in (False, True):
                    name = table + '_I'
                    draft = {'name': name, 'table': table, 'unique': True,
                             'index_kind': kind, 'direction': direction}
                    if kind == 'columns':
                        draft['columns'] = ['V']
                    else:
                        draft['expression'] = 'UPPER(V)'
                    if partial:
                        draft['condition'] = 'ID > 0'
                    apply('create', draft, name)
                    metadata = execute(
                        'SELECT RDB$UNIQUE_FLAG, RDB$INDEX_TYPE, '
                        'RDB$EXPRESSION_SOURCE, RDB$CONDITION_SOURCE '
                        'FROM RDB$INDICES WHERE RDB$INDEX_NAME = ?',
                        (name,), True)[0]
                    assert metadata[0] == 1
                    assert bool(metadata[1]) == (direction == 'DESCENDING')
                    assert bool(metadata[2]) == (kind == 'expression')
                    assert bool(metadata[3]) == partial
                    resource = next(item for item in _resources(connection, {
                        'route': profile}) if item['resource_kind'] == 'index'
                        and item['display_name'] == name)
                    ddl = resource['native']['ddl']
                    assert ('WHERE' in ddl.upper()) == partial
                    assert ('COMPUTED BY' in ddl.upper()) == (
                        kind == 'expression')
                    assert bool(resource['native']['condition_source']) == (
                        partial)
                    connection.commit()
                    execute(f'INSERT INTO "{table}" VALUES (1, \'Robin\')')
                    connection.commit()
                    duplicate = 'ROBIN' if kind == 'expression' else 'Robin'
                    try:
                        execute(f'INSERT INTO "{table}" VALUES (2, ?)',
                                (duplicate,))
                    except driver.DatabaseError as error:
                        assert error.sqlstate == '23000', error
                    else:
                        raise AssertionError('Unique index accepted duplicate')
                    finally:
                        connection.rollback()
                    if partial:
                        execute(f'INSERT INTO "{table}" VALUES (0, ?)',
                                (duplicate,))
                        connection.rollback()
                    execute(f'DELETE FROM "{table}"')
                    connection.commit()
                    apply('alter', {'active': False}, name)
                    assert execute(
                        'SELECT RDB$INDEX_INACTIVE FROM RDB$INDICES '
                        'WHERE RDB$INDEX_NAME = ?', (name,), True) == [(1,)]
                    connection.commit()
                    apply('alter', {'active': True}, name)
                    apply('drop', {}, name, commit=False)
                    connection.rollback()
                    assert execute('SELECT COUNT(*) FROM RDB$INDICES '
                                   'WHERE RDB$INDEX_NAME = ?',
                                   (name,), True) == [(1,)]
                    connection.commit()
                    apply('drop', {}, name)
                    apply('create', draft, name, commit=False)
                    connection.rollback()
                    assert execute('SELECT COUNT(*) FROM RDB$INDICES '
                                   'WHERE RDB$INDEX_NAME = ?',
                                   (name,), True) == [(0,)]
                    connection.commit()
                    result['checks'].append(
                        f'{kind}-{direction}-partial-{partial}')
        name = table + '_S'
        apply('create', {'name': name, 'table': table, 'columns': ['ID']},
              name)
        for number in range(10):
            execute(f'INSERT INTO "{table}" VALUES (?, \'sample\')',
                    (number,))
        connection.commit()
        apply('alter', {'refresh_statistics': True}, name)
        statistics, inactive = execute(
            'SELECT RDB$STATISTICS, RDB$INDEX_INACTIVE FROM RDB$INDICES '
            'WHERE RDB$INDEX_NAME = ?', (name,), True)[0]
        assert abs(statistics - 0.1) < 0.00001, statistics
        assert not inactive
        connection.commit()
        result['checks'].append('statistics-refresh-selectivity-active')
        apply('alter', {'active': False}, name)
        apply('alter', {'refresh_statistics': True}, name)
        assert execute('SELECT RDB$INDEX_INACTIVE FROM RDB$INDICES '
                       'WHERE RDB$INDEX_NAME = ?', (name,), True) == [(1,)]
        connection.commit()
        result['checks'].append('statistics-refresh-preserves-inactive')
        apply('alter', {'active': True, 'refresh_statistics': True}, name)
        assert execute('SELECT RDB$INDEX_INACTIVE FROM RDB$INDICES '
                       'WHERE RDB$INDEX_NAME = ?', (name,), True) == [(0,)]
        connection.commit()
        result['checks'].append('activate-and-refresh-statistics')
        apply('alter', {'active': False}, name, commit=False)
        connection.rollback()
        assert execute('SELECT RDB$INDEX_INACTIVE FROM RDB$INDICES '
                       'WHERE RDB$INDEX_NAME = ?', (name,), True) == [(0,)]
        connection.commit()
        result['checks'].append('index-deactivation-rollback')
        other = driver.connect(password=profile['password'],
                               **_route_arguments(profile, driver))
        try:
            plan = ADMINISTRATION.plan({
                '_provider_route': profile, 'resource_kind': 'index',
                'operation_id': 'alter', 'draft': {'active': False},
                'target_resource': {'display_name': name,
                                    'display_path': [table, name]}})
            ADMINISTRATION.apply(client, plan, connection=other)
            assert other.main_transaction.is_active()
        finally:
            other.close()
        assert execute('SELECT RDB$INDEX_INACTIVE FROM RDB$INDICES '
                       'WHERE RDB$INDEX_NAME = ?', (name,), True) == [(0,)]
        connection.commit()
        result['checks'].append('uncommitted-deactivation-attachment-close')
        verify_permissions(connection, client, profile, table, name, result)
        apply('drop', {}, name)
        execute(f'DELETE FROM "{table}"')
        connection.commit()
        # Exercise native expression text through the provider planner.
        cases = [
            ('concat', "UPPER(V) || ';'", 'ID != 0', 'ROBIN;'),
            ('alternative-quote', "V || q'[Robin's ;)]'", 'ID !< 1',
             "RobinRobin's ;)"),
            ('comments', 'UPPER(V) /* ) ; */', 'ID > 0 -- final comment',
             'ROBIN'),
            ('case', 'CASE WHEN ID > 0 THEN UPPER(V) ELSE V END',
             "V SIMILAR TO '[A-Za-z]+'", 'ROBIN'),
            ('quoted', '"V" || \'Robin\'\'s\'', 'ID IS NOT NULL',
             "RobinRobin's"),
        ]
        for label, expression, predicate, expected in cases:
            name = table + '_E'
            apply('create', {'name': name, 'table': table,
                             'index_kind': 'expression',
                             'expression': expression,
                             'condition': predicate}, name)
            execute(f'INSERT INTO "{table}" VALUES (1, \'Robin\')')
            assert execute(f'SELECT COUNT(*) FROM "{table}"',
                           fetch=True) == [(1,)]
            assert execute(f'SELECT ({expression}\n) FROM "{table}" '
                           f'WHERE ({predicate}\n)',
                           fetch=True) == [(expected,)]
            connection.rollback()
            apply('drop', {}, name)
            result['checks'].append('visual-expression-' + label)
        name = table + '_BAD'
        try:
            apply('create', {'name': name, 'table': table,
                             'index_kind': 'expression',
                             'expression': 'NO_SUCH_FUNCTION(V)'}, name)
        except RelationalClientError as error:
            assert 'execution failed (DatabaseError)' in str(error), error
            if connection.main_transaction.is_active():
                connection.rollback()
            assert execute('SELECT COUNT(*) FROM RDB$INDICES '
                           'WHERE RDB$INDEX_NAME = ?',
                           (name,), True) == [(0,)]
            connection.commit()
            result['checks'].append('native-invalid-expression-no-index')
        else:
            raise AssertionError('Native-invalid expression was accepted')
        # Native BLOB source must survive catalog reads and repeated replay.
        # Raw fixture SQL deliberately includes native syntax not yet admitted
        # by the visual expression validator; it is not visual-form evidence.
        for long_source in (False, True):
            name = table + '_R'
            padding = ' ' * 10000 if long_source else ' '
            expression = f'(UPPER(V){padding}|| \';\')'
            predicate = 'WHERE\nID > 0' + padding + 'AND ID < 100'
            execute(f'CREATE DESCENDING INDEX "{name}" ON "{table}" '
                    f'COMPUTED BY {expression} {predicate}')
            comment = "  Robin's index;\n" + 'x' * (
                50000 if long_source else 1)
            execute(f'COMMENT ON INDEX "{name}" IS \''
                    + comment.replace("'", "''") + "'")
            execute(f'ALTER INDEX "{name}" INACTIVE')
            connection.commit()
            for replay in range(2):
                resource = next(item for item in _resources(connection, {
                    'route': profile}) if item['resource_kind'] == 'index'
                    and item['display_name'] == name)
                native = resource['native']
                assert native['description'] == comment
                assert native['state']['active'] is False
                assert native['expression_source'] == expression
                assert native['condition_source'] == predicate
                statements = native['recreation_statements']
                assert len(statements) == 3
                connection.commit()
                apply('drop', {}, name)
                for statement in statements:
                    execute(statement)
                connection.commit()
                execute(f'ALTER INDEX "{name}" ACTIVE')
                connection.commit()
                execute(f'INSERT INTO "{table}" VALUES (1, \'Robin\')')
                assert execute(
                    f'SELECT ID FROM "{table}" WHERE ID > 0 AND ID < 100 '
                    "AND UPPER(V) || ';' = 'ROBIN;'", fetch=True) == [(1,)]
                connection.rollback()
                execute(f'ALTER INDEX "{name}" INACTIVE')
                connection.commit()
                result['checks'].append(
                    f'metadata-replay-long-{long_source}-cycle-{replay}')
            apply('drop', {}, name)
        result.update(status='passed', engine_version=connection.info.version)
        return result
    finally:
        try:
            if connection.main_transaction.is_active():
                connection.rollback()
            if created:
                execute(f'DROP TABLE "{table}"')
                connection.commit()
                result['temporary_table_removed'] = True
        finally:
            connection.close()
            client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, default=ROOT /
                        'tools/reference_engine_demos/runtime/'
                        'connection_profiles.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.profiles)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
