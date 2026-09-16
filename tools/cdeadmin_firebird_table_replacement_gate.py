#!/usr/bin/env python3
"""Native structured table recreation on a disposable Firebird 5.0.4 server."""
import argparse
import json
from pathlib import Path

if __package__:
    from . import cdeadmin_firebird_views_gate as base
else:
    import cdeadmin_firebird_views_gate as base


def verify(connection, client, route, password, result):
    import firebird.driver as native
    from pgadmin.cdeadmin.providers.firebird.character_metadata import (
        identifier, literal,
    )
    from pgadmin.cdeadmin.providers.firebird.error_diagnostics import (
        status_codes,
    )

    checks = result['table_replacement_checks'] = []
    tasks = result['table_replacement_task_evidence'] = {}

    def sql(source, params=(), handle=None):
        with (handle or connection).cursor() as cursor:
            cursor.execute(source, params)
            return cursor.fetchall() if cursor.description else []

    def apply(name, kind='PERSISTENT', retention=None, handle=None):
        draft = {'confirmation': name, 'table_type': kind,
                 'sql_security': 'DEFINER',
                 'columns': [{'name': 'ID', 'column_mode': 'STORED',
                              'data_type': 'INTEGER',
                              'constraints': [{'kind': 'PRIMARY KEY'}]},
                             {'name': 'VALUE', 'column_mode': 'STORED',
                              'data_type': 'INTEGER',
                              'has_default': True, 'default_kind': 'NUMBER',
                              'default_value': '7'}]}
        if retention:
            draft['on_commit'] = retention
        request = {'resource_kind': 'table', 'operation_id': 'recreate',
                   'draft': draft, '_provider_route': route,
                   'target_resource': {'resource_kind': 'table',
                                       'display_name': name}}
        assert base.ADMINISTRATION.validate(request) == {'errors': []}
        plan = base.ADMINISTRATION.plan(request)
        receipt = base.ADMINISTRATION.apply(
            client, plan, connection=handle or connection)
        assert receipt['staged_in_provider_session'] is True
        tasks['visual_admin.table.recreate'] = {
            'live_execution': 'passed', 'statements': [
                s['source'] for s in plan['command_preview']['statements']]}

    def grants(name):
        return sql('SELECT TRIM(RDB$PRIVILEGE) FROM RDB$USER_PRIVILEGES '
                   "WHERE RDB$RELATION_NAME = ? AND RDB$USER = 'PUBLIC'",
                   (name,))

    def metadata(name):
        return sql('SELECT RDB$RELATION_TYPE, RDB$SQL_SECURITY, '
                   'RDB$DESCRIPTION FROM RDB$RELATIONS '
                   'WHERE RDB$RELATION_NAME = ?', (name,))

    for name, kind, retention, relation_type in (
        ('T"東京', 'PERSISTENT', None, 0),
        ('T_DELETE', 'GLOBAL TEMPORARY', 'DELETE ROWS', 5),
        ('T_PRESERVE', 'GLOBAL TEMPORARY', 'PRESERVE ROWS', 4),
    ):
        prefix = ('CREATE TABLE ' if kind == 'PERSISTENT' else
                  'CREATE GLOBAL TEMPORARY TABLE ')
        sql(prefix + identifier(name) + ' (ID INTEGER)' + (
            ' ON COMMIT ' + retention if retention else ''))
        sql('COMMENT ON TABLE ' + identifier(name) + " IS 'original'")
        sql('GRANT SELECT ON ' + identifier(name) + ' TO PUBLIC')
        connection.commit()
        if not retention:
            sql('INSERT INTO ' + identifier(name) + ' VALUES (42)')
            connection.commit()
        apply(name, kind, retention)
        assert metadata(name) == [(relation_type, True, None)]
        connection.rollback()
        assert metadata(name) == [(relation_type, None, 'original')]
        assert grants(name) == [('S',)]
        if not retention:
            assert sql('SELECT ID FROM ' + identifier(name)) == [(42,)]
        connection.rollback()
        apply(name, kind, retention)
        connection.commit()
        assert metadata(name) == [(relation_type, True, None)]
        assert grants(name) == [], grants(name)
        connection.rollback()
        observer = native.connect(password=password,
                                  **base._route_arguments(route, native))
        try:
            assert sql('SELECT ID FROM ' + identifier(name),
                       handle=observer) == []
            sql('INSERT INTO ' + identifier(name) + ' (ID) VALUES (1)',
                handle=observer)
            assert sql('SELECT "VALUE" FROM ' + identifier(name),
                       handle=observer) == [(7,)]
            observer.commit()
            assert sql('SELECT COUNT(*) FROM ' + identifier(name),
                       handle=observer) == [(0 if retention == 'DELETE ROWS'
                                             else 1,)]
        finally:
            observer.rollback()
            observer.close()
        checks.append({'case': 'lifecycle-' + kind + '-' + str(retention),
                       'rollback_commit_verified': True,
                       'data_and_retention_verified': True,
                       'metadata_and_grants_verified': True})
    committed_tasks = dict(tasks)

    sql('CREATE VIEW TABLE_DEP AS SELECT ID FROM "T""東京"')
    connection.commit()
    try:
        apply('T"東京')
        connection.commit()
    except Exception as error:
        codes = list(status_codes(error))
        assert 335544630 in codes, codes
        connection.rollback()
        assert sql('SELECT ID FROM TABLE_DEP') == [(1,)]
        connection.rollback()
        checks.append({'case': 'dependency-denial',
                       'native_status_codes': codes,
                       'original_and_dependent_preserved': True})
    else:
        raise AssertionError('Dependent table recreation was admitted')

    sql('CREATE USER TABLE_READER PASSWORD ' + literal(password))
    connection.commit()
    reader = native.connect(password=password, **base._route_arguments(
        {**route, 'user': 'TABLE_READER'}, native))
    try:
        try:
            apply('T_DELETE', 'GLOBAL TEMPORARY', 'DELETE ROWS', reader)
            reader.commit()
        except Exception as error:
            codes = list(status_codes(error))
            assert 335544352 in codes, codes
        else:
            raise AssertionError('Unprivileged table recreation was admitted')
        finally:
            reader.rollback()
    finally:
        reader.close()
    assert metadata('T_DELETE') == [(5, True, None)]
    connection.rollback()
    checks.append({'case': 'permission-denial', 'native_status_codes': codes,
                   'table_unchanged': True})
    result['table_replacement_task_evidence'] = committed_tasks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--include-grid-boundaries', action='store_true')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite evidence')
    result = base.run(extra_checks=verify,
                      run_grid_boundaries=args.include_grid_boundaries)
    result['complete'] = (result['complete'] and
                          len(result.get('table_replacement_checks', [])) == 5)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
