#!/usr/bin/env python3
"""Native standalone function replacement on a disposable Firebird server."""
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
    checks = result['function_checks'] = []
    tasks = result['function_task_evidence'] = {}

    def sql(source, params=(), handle=None):
        with (handle or connection).cursor() as cursor:
            cursor.execute(source, params)
            return cursor.fetchall() if cursor.description else []

    def declaration(value, deterministic=True, security='INVOKER'):
        return ('(X INTEGER = 2) RETURNS INTEGER ' +
                ('DETERMINISTIC ' if deterministic else 'NOT DETERMINISTIC ') +
                'SQL SECURITY ' + security +
                ' AS BEGIN RETURN X + ' + str(value) + '; END')

    def apply(operation, name, definition, handle=None):
        request = {'resource_kind': 'function', 'operation_id': operation,
                   '_provider_route': route,
                   'draft': {'declaration': definition,
                             'name' if operation == 'create_or_alter' else
                             'confirmation': name},
                   'target_resource': {'resource_kind': 'function',
                                       'display_name': name}}
        assert base.ADMINISTRATION.validate(request) == {'errors': []}
        plan = base.ADMINISTRATION.plan(request)
        receipt = base.ADMINISTRATION.apply(
            client, plan, connection=handle or connection)
        assert receipt['staged_in_provider_session'] is True
        tasks['visual_admin.function.' + operation] = {
            'live_execution': 'passed', 'statements': [
                s['source'] for s in plan['command_preview']['statements']]}

    def values(name, fresh=True):
        if not fresh:
            return sql('SELECT ' + identifier(name) + '() FROM RDB$DATABASE')
        observer = native.connect(password=password,
                                  **base._route_arguments(route, native))
        try:
            return sql('SELECT ' + identifier(name) + '() FROM RDB$DATABASE',
                       handle=observer)
        finally:
            observer.rollback()
            observer.close()

    def body(name):
        return sql('SELECT RDB$FUNCTION_SOURCE FROM RDB$FUNCTIONS WHERE '
                   'RDB$FUNCTION_NAME = ?', (name,))[0][0]

    def grants(name):
        return sql('SELECT TRIM(RDB$PRIVILEGE) FROM RDB$USER_PRIVILEGES '
                   "WHERE RDB$RELATION_NAME = ? AND RDB$USER = 'PUBLIC'",
                   (name,))

    for name, deterministic in [('F_BASE', True), ('F"東京', False)]:
        apply('create_or_alter', name, declaration(1, deterministic))
        assert values(name, fresh=False) == [(3,)]
        connection.rollback()
        assert sql('SELECT 1 FROM RDB$FUNCTIONS WHERE RDB$FUNCTION_NAME = ?',
                   (name,)) == []
        connection.rollback()
        apply('create_or_alter', name, declaration(1, deterministic))
        connection.commit()
        sql('GRANT EXECUTE ON FUNCTION ' + identifier(name) + ' TO PUBLIC')
        connection.commit()
        apply('create_or_alter', name,
              declaration(2, deterministic, 'DEFINER'))
        assert 'RETURN X + 2;' in body(name)
        connection.rollback()
        assert values(name) == [(3,)]
        apply('create_or_alter', name,
              declaration(2, deterministic, 'DEFINER'))
        connection.commit()
        observed = values(name)
        assert observed == [(4,)], {'observed': observed, 'body': body(name)}
        warm_after_alter = values(name, fresh=False)
        assert grants(name) == [('X',)]
        assert sql('SELECT RDB$SQL_SECURITY FROM RDB$FUNCTIONS WHERE '
                   'RDB$FUNCTION_NAME = ?', (name,)) == [(True,)]
        connection.rollback()
        apply('recreate', name, declaration(3, deterministic))
        assert 'RETURN X + 3;' in body(name)
        connection.rollback()
        assert values(name) == [(4,)]
        assert grants(name) == [('X',)]
        connection.rollback()
        apply('recreate', name, declaration(3, deterministic))
        connection.commit()
        assert values(name) == [(5,)]
        assert grants(name) == []
        assert sql('SELECT RDB$SQL_SECURITY, RDB$DETERMINISTIC_FLAG FROM '
                   'RDB$FUNCTIONS WHERE RDB$FUNCTION_NAME = ?', (name,)) == [
                       (False, deterministic)]
        connection.rollback()
        checks.append({'case': 'lifecycle-' + name,
                       'rollback_commit_verified': True,
                       'grant_semantics_verified': True,
                       'warm_execution_after_alter': warm_after_alter,
                       'fresh_execution_after_alter': observed,
                       'deterministic_verified': True,
                       'execution_and_security_verified': True})
    committed_tasks = dict(tasks)

    sql('CREATE VIEW FUNC_DEP AS SELECT F_BASE(1) AS Y FROM RDB$DATABASE')
    connection.commit()
    try:
        apply('recreate', 'F_BASE', declaration(7))
        connection.commit()
    except Exception as error:
        codes = list(status_codes(error))
        assert 335544630 in codes, codes
        connection.rollback()
        assert values('F_BASE') == [(5,)]
        observer = native.connect(password=password,
                                  **base._route_arguments(route, native))
        try:
            assert sql('SELECT Y FROM FUNC_DEP', handle=observer) == [(4,)]
        finally:
            observer.rollback()
            observer.close()
        checks.append({'case': 'dependency-denial',
                       'native_status_codes': codes,
                       'original_and_dependent_preserved': True})
    else:
        raise AssertionError('Dependent function recreation was admitted')

    sql('CREATE USER FUNC_READER PASSWORD ' + literal(password))
    connection.commit()
    reader = native.connect(password=password, **base._route_arguments(
        {**route, 'user': 'FUNC_READER'}, native))
    denials = []
    try:
        for op in ('create_or_alter', 'recreate'):
            try:
                apply(op, 'F"東京', declaration(7, False), reader)
                reader.commit()
            except Exception as error:
                codes = list(status_codes(error))
                assert 335544352 in codes, codes
                denials.append({'operation': op, 'native_status_codes': codes})
            else:
                raise AssertionError('Unprivileged function change admitted')
            finally:
                reader.rollback()
    finally:
        reader.close()
    assert values('F"東京') == [(5,)]
    checks.append({'case': 'permission-denials', 'denials': denials,
                   'function_unchanged': True})
    result['function_task_evidence'] = committed_tasks


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
                          len(result.get('function_checks', [])) == 4)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'function_checks': len(result.get(
                          'function_checks', [])),
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
