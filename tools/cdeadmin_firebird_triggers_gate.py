#!/usr/bin/env python3
"""Native trigger replacement on an owned, disposable Firebird server."""
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
    checks = result['trigger_checks'] = []
    tasks = result['trigger_task_evidence'] = {}

    def sql(source, params=(), handle=None):
        with (handle or connection).cursor() as cursor:
            cursor.execute(source, params)
            return cursor.fetchall() if cursor.description else []

    def declaration(event, value, security='INVOKER'):
        return (event + ' POSITION 4 SQL SECURITY ' + security +
                " AS BEGIN RDB$SET_CONTEXT('USER_SESSION', 'TRIGGER_VALUE', " +
                literal(value) + '); END')

    def apply(operation, name, definition, handle=None):
        request = {'resource_kind': 'trigger', 'operation_id': operation,
                   '_provider_route': route,
                   'draft': {'declaration': definition,
                             'name' if operation == 'create_or_alter' else
                             'confirmation': name},
                   'target_resource': {'resource_kind': 'trigger',
                                       'display_name': name,
                                       'display_path': ['TRG_DATA', name]}}
        assert base.ADMINISTRATION.validate(request) == {'errors': []}
        plan = base.ADMINISTRATION.plan(request)
        receipt = base.ADMINISTRATION.apply(
            client, plan, connection=handle or connection)
        assert receipt['staged_in_provider_session'] is True
        tasks['visual_admin.trigger.' + operation] = {
            'live_execution': 'passed', 'statements': [
                s['source'] for s in plan['command_preview']['statements']]}

    def metadata(name):
        return sql('SELECT RDB$TRIGGER_SOURCE, RDB$SQL_SECURITY, '
                   'RDB$TRIGGER_SEQUENCE, RDB$TRIGGER_INACTIVE, '
                   'RDB$DESCRIPTION FROM RDB$TRIGGERS WHERE '
                   'RDB$TRIGGER_NAME = ?', (name,))

    def fire(kind):
        observer = native.connect(password=password,
                                  **base._route_arguments(route, native))
        try:
            if kind == 'relation':
                sql('INSERT INTO TRG_DATA VALUES (1)', handle=observer)
            elif kind == 'ddl':
                sql('CREATE TABLE TRG_TRANSIENT (ID INTEGER)', handle=observer)
            return sql("SELECT RDB$GET_CONTEXT('USER_SESSION', "
                       "'TRIGGER_VALUE') FROM RDB$DATABASE", handle=observer)
        finally:
            observer.rollback()
            observer.close()

    def privileges(name):
        return sql('SELECT TRIM(RDB$PRIVILEGE) FROM RDB$USER_PRIVILEGES '
                   'WHERE RDB$USER = ? AND RDB$USER_TYPE = 2 '
                   "AND RDB$RELATION_NAME = 'TRG_DATA'", (name,))

    sql('CREATE TABLE TRG_DATA (ID INTEGER)')
    connection.commit()
    for kind, name, event in (
        ('relation', 'T"東京', 'ACTIVE BEFORE INSERT ON TRG_DATA'),
        ('database', 'T_DATABASE', 'ACTIVE ON TRANSACTION START'),
        ('ddl', 'T_DDL', 'ACTIVE BEFORE CREATE TABLE'),
    ):
        apply('create_or_alter', name, declaration(event, 'one'))
        assert metadata(name)
        connection.rollback()
        assert metadata(name) == []
        connection.rollback()
        apply('create_or_alter', name, declaration(event, 'one'))
        connection.commit()
        assert fire(kind) == [('one',)]
        sql('COMMENT ON TRIGGER ' + identifier(name) + " IS 'preserved'")
        sql('GRANT SELECT ON TRG_DATA TO TRIGGER ' + identifier(name))
        connection.commit()
        apply('create_or_alter', name, declaration(event, 'two', 'DEFINER'))
        assert metadata(name)[0][1:4] == (True, 4, 0)
        connection.rollback()
        assert fire(kind) == [('one',)]
        apply('create_or_alter', name, declaration(event, 'two', 'DEFINER'))
        connection.commit()
        assert fire(kind) == [('two',)]
        assert metadata(name)[0][4] == 'preserved'
        assert privileges(name) == [('S',)]
        connection.rollback()
        apply('recreate', name, declaration(event, 'three'))
        assert metadata(name)[0][4] is None
        connection.rollback()
        assert fire(kind) == [('two',)]
        assert metadata(name)[0][4] == 'preserved'
        assert privileges(name) == [('S',)]
        connection.rollback()
        apply('recreate', name, declaration(event, 'three'))
        connection.commit()
        assert fire(kind) == [('three',)]
        assert metadata(name)[0][1:] == (False, 4, 0, None)
        assert privileges(name) == []
        connection.rollback()
        checks.append({'case': 'lifecycle-' + kind,
                       'rollback_commit_verified': True,
                       'execution_and_security_verified': True,
                       'comment_semantics_verified': True,
                       'privilege_semantics_verified': True})
        # Prevent another event class from overwriting the observed marker.
        sql('ALTER TRIGGER ' + identifier(name) + ' INACTIVE')
        connection.commit()
        assert fire(kind) == [(None,)]
        checks[-1]['inactive_verified'] = True
    committed_tasks = dict(tasks)

    sql('CREATE USER TRG_READER PASSWORD ' + literal(password))
    connection.commit()
    reader = native.connect(password=password, **base._route_arguments(
        {**route, 'user': 'TRG_READER'}, native))
    denials = []
    original = metadata('T"東京')
    connection.rollback()
    try:
        for op in ('create_or_alter', 'recreate'):
            try:
                apply(op, 'T"東京', declaration(
                    'ACTIVE BEFORE INSERT ON TRG_DATA', 'denied'), reader)
                reader.commit()
            except Exception as error:
                codes = list(status_codes(error))
                assert 335544352 in codes, codes
                denials.append({'operation': op, 'native_status_codes': codes})
            else:
                raise AssertionError('Unprivileged trigger change admitted')
            finally:
                reader.rollback()
    finally:
        reader.close()
    assert metadata('T"東京') == original
    connection.rollback()
    checks.append({'case': 'permission-denials', 'denials': denials,
                   'trigger_unchanged': True})
    result['trigger_task_evidence'] = committed_tasks


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
                          len(result.get('trigger_checks', [])) == 4)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'trigger_checks': len(result.get('trigger_checks', [])),
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
