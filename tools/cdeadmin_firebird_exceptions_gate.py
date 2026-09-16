#!/usr/bin/env python3
"""Native exception lifecycle evidence using an owned disposable server."""
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
    checks = result['exception_checks'] = []
    tasks = result['exception_task_evidence'] = {}

    def sql(source, params=(), handle=None):
        with (handle or connection).cursor() as cursor:
            cursor.execute(source, params)
            return cursor.fetchall() if cursor.description else []

    def message(name):
        return sql('SELECT RDB$MESSAGE FROM RDB$EXCEPTIONS WHERE '
                   'RDB$EXCEPTION_NAME = ?', (name,))

    def grants(name):
        return sql("SELECT TRIM(RDB$PRIVILEGE) FROM RDB$USER_PRIVILEGES WHERE "
                   "RDB$RELATION_NAME = ? AND RDB$USER = 'PUBLIC'", (name,))

    def apply(operation, name, value, handle=None):
        draft = {'message': value,
                 'name' if operation == 'create_or_alter' else
                 'confirmation': name}
        request = {'resource_kind': 'exception', 'operation_id': operation,
                   '_provider_route': route, 'draft': draft,
                   'target_resource': {'resource_kind': 'exception',
                                       'display_name': name}}
        assert base.ADMINISTRATION.validate(request) == {'errors': []}
        plan = base.ADMINISTRATION.plan(request)
        receipt = base.ADMINISTRATION.apply(
            client, plan, connection=handle or connection)
        assert receipt['staged_in_provider_session'] is True
        tasks['visual_admin.exception.' + operation] = {
            'live_execution': 'passed', 'statements': [
                s['source'] for s in plan['command_preview']['statements']]}

    for name in ('EX_BASE', 'EX"東京'):
        q = identifier(name)
        original = "O'Brien; -- literal @1"
        apply('create_or_alter', name, original)
        assert message(name) == [(original,)]
        connection.rollback()
        assert message(name) == []
        connection.rollback()
        apply('create_or_alter', name, original)
        connection.commit()
        sql('GRANT USAGE ON EXCEPTION ' + q + ' TO PUBLIC')
        connection.commit()
        apply('create_or_alter', name, 'replacement')
        assert message(name) == [('replacement',)]
        connection.rollback()
        assert message(name) == [(original,)]
        connection.rollback()
        apply('create_or_alter', name, 'replacement')
        connection.commit()
        assert grants(name) == [('G',)], grants(name)
        connection.rollback()
        apply('recreate', name, 'recreated')
        assert message(name) == [('recreated',)]
        connection.rollback()
        assert message(name) == [('replacement',)]
        assert grants(name) == [('G',)]
        connection.rollback()
        apply('recreate', name, 'recreated')
        connection.commit()
        assert message(name) == [('recreated',)]
        assert grants(name) == []
        connection.rollback()
        checks.append({'case': 'lifecycle-' + name,
                       'rollback_commit_verified': True,
                       'grant_semantics_verified': True})

    committed_tasks = dict(tasks)
    sql('CREATE PROCEDURE EX_DEP AS BEGIN EXCEPTION EX_BASE; END')
    connection.commit()
    try:
        apply('recreate', 'EX_BASE', 'blocked')
        connection.commit()
    except Exception as error:
        codes = list(status_codes(error))
        assert 335544630 in codes, codes
        connection.rollback()
        assert message('EX_BASE') == [('recreated',)]
        assert sql("SELECT 1 FROM RDB$PROCEDURES WHERE "
                   "RDB$PROCEDURE_NAME = 'EX_DEP'") == [(1,)]
        connection.rollback()
        checks.append({'case': 'dependency-denial',
                       'native_status_codes': codes,
                       'original_and_dependent_preserved': True})
    else:
        raise AssertionError('Dependent exception recreation was admitted')

    sql('CREATE USER EX_READER PASSWORD ' + literal(password))
    connection.commit()
    reader = native.connect(password=password, **base._route_arguments(
        {**route, 'user': 'EX_READER'}, native))
    denials = []
    try:
        for op in ('create_or_alter', 'recreate'):
            try:
                apply(op, 'EX"東京', 'blocked', reader)
                reader.commit()
            except Exception as error:
                codes = list(status_codes(error))
                assert 335544352 in codes, codes
                denials.append({'operation': op, 'native_status_codes': codes})
            else:
                raise AssertionError('Unprivileged exception change admitted')
            finally:
                reader.rollback()
    finally:
        reader.close()
    assert message('EX"東京') == [('recreated',)]
    connection.rollback()
    checks.append({'case': 'permission-denials', 'denials': denials,
                   'exception_unchanged': True})
    result['exception_task_evidence'] = committed_tasks


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
                          len(result.get('exception_checks', [])) == 4)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'exception_checks': len(result.get(
                          'exception_checks', [])),
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
