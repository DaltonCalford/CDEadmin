#!/usr/bin/env python3
"""Native standalone procedure replacement on a disposable Firebird server."""
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
    checks = result['procedure_checks'] = []
    tasks = result['procedure_task_evidence'] = {}

    def sql(source, params=(), handle=None):
        with (handle or connection).cursor() as cursor:
            cursor.execute(source, params)
            return cursor.fetchall() if cursor.description else []

    def declaration(value, selectable=True, security='INVOKER'):
        return ('(X INTEGER = 2) RETURNS (Y INTEGER) SQL SECURITY ' +
                security +
                ' AS BEGIN Y = X + ' + str(value) + '; ' +
                ('SUSPEND; ' if selectable else '') + 'END')

    def apply(operation, name, definition, handle=None):
        request = {'resource_kind': 'procedure', 'operation_id': operation,
                   '_provider_route': route,
                   'draft': {'declaration': definition,
                             'name' if operation == 'create_or_alter' else
                             'confirmation': name},
                   'target_resource': {'resource_kind': 'procedure',
                                       'display_name': name}}
        assert base.ADMINISTRATION.validate(request) == {'errors': []}
        plan = base.ADMINISTRATION.plan(request)
        receipt = base.ADMINISTRATION.apply(
            client, plan, connection=handle or connection)
        assert receipt['staged_in_provider_session'] is True
        tasks['visual_admin.procedure.' + operation] = {
            'live_execution': 'passed', 'statements': [
                s['source'] for s in plan['command_preview']['statements']]}

    def values(name, fresh=True):
        if not fresh:
            return sql('EXECUTE PROCEDURE ' + identifier(name))
        observer = native.connect(password=password,
                                  **base._route_arguments(route, native))
        try:
            return sql('EXECUTE PROCEDURE ' + identifier(name),
                       handle=observer)
        finally:
            observer.rollback()
            observer.close()

    def body(name):
        return sql('SELECT RDB$PROCEDURE_SOURCE FROM RDB$PROCEDURES WHERE '
                   'RDB$PROCEDURE_NAME = ?', (name,))[0][0]

    def grants(name):
        return sql('SELECT TRIM(RDB$PRIVILEGE) FROM RDB$USER_PRIVILEGES '
                   "WHERE RDB$RELATION_NAME = ? AND RDB$USER = 'PUBLIC'",
                   (name,))

    for name, selectable in [('P_BASE', True), ('P"東京', False)]:
        apply('create_or_alter', name, declaration(1, selectable))
        assert values(name, fresh=False) == [(3,)]
        connection.rollback()
        assert sql('SELECT 1 FROM RDB$PROCEDURES WHERE RDB$PROCEDURE_NAME = ?',
                   (name,)) == []
        connection.rollback()
        apply('create_or_alter', name, declaration(1, selectable))
        connection.commit()
        sql('GRANT EXECUTE ON PROCEDURE ' + identifier(name) + ' TO PUBLIC')
        connection.commit()
        apply('create_or_alter', name,
              declaration(2, selectable, 'DEFINER'))
        assert 'Y = X + 2;' in body(name)
        connection.rollback()
        assert values(name) == [(3,)]
        apply('create_or_alter', name,
              declaration(2, selectable, 'DEFINER'))
        connection.commit()
        observed = values(name)
        assert observed == [(4,)], {'observed': observed, 'body': body(name)}
        warm_after_alter = values(name, fresh=False)
        assert grants(name) == [('X',)]
        assert sql('SELECT RDB$SQL_SECURITY FROM RDB$PROCEDURES WHERE '
                   'RDB$PROCEDURE_NAME = ?', (name,)) == [(True,)]
        connection.rollback()
        apply('recreate', name, declaration(3, selectable))
        assert 'Y = X + 3;' in body(name)
        connection.rollback()
        assert values(name) == [(4,)]
        assert grants(name) == [('X',)]
        connection.rollback()
        apply('recreate', name, declaration(3, selectable))
        connection.commit()
        assert values(name) == [(5,)]
        assert grants(name) == []
        assert sql('SELECT RDB$SQL_SECURITY, RDB$PROCEDURE_TYPE FROM '
                   'RDB$PROCEDURES WHERE RDB$PROCEDURE_NAME = ?', (name,)) == [
                       (False, 1 if selectable else 2)]
        connection.rollback()
        checks.append({'case': 'lifecycle-' + name,
                       'rollback_commit_verified': True,
                       'grant_semantics_verified': True,
                       'warm_execution_after_alter': warm_after_alter,
                       'fresh_execution_after_alter': observed,
                       'execution_and_security_verified': True})
    committed_tasks = dict(tasks)

    sql('CREATE VIEW PROC_DEP AS SELECT Y FROM P_BASE(1)')
    connection.commit()
    try:
        apply('recreate', 'P_BASE', declaration(7))
        connection.commit()
    except Exception as error:
        codes = list(status_codes(error))
        assert 335544630 in codes, codes
        connection.rollback()
        assert values('P_BASE') == [(5,)]
        observer = native.connect(password=password,
                                  **base._route_arguments(route, native))
        try:
            assert sql('SELECT Y FROM PROC_DEP', handle=observer) == [(4,)]
        finally:
            observer.rollback()
            observer.close()
        checks.append({'case': 'dependency-denial',
                       'native_status_codes': codes,
                       'original_and_dependent_preserved': True})
    else:
        raise AssertionError('Dependent procedure recreation was admitted')

    sql('CREATE USER PROC_READER PASSWORD ' + literal(password))
    connection.commit()
    reader = native.connect(password=password, **base._route_arguments(
        {**route, 'user': 'PROC_READER'}, native))
    denials = []
    try:
        for op in ('create_or_alter', 'recreate'):
            try:
                apply(op, 'P"東京', declaration(7, False), reader)
                reader.commit()
            except Exception as error:
                codes = list(status_codes(error))
                assert 335544352 in codes, codes
                denials.append({'operation': op, 'native_status_codes': codes})
            else:
                raise AssertionError('Unprivileged procedure change admitted')
            finally:
                reader.rollback()
    finally:
        reader.close()
    assert values('P"東京') == [(5,)]
    checks.append({'case': 'permission-denials', 'denials': denials,
                   'procedure_unchanged': True})
    result['procedure_task_evidence'] = committed_tasks


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
                          len(result.get('procedure_checks', [])) == 4)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'procedure_checks': len(result.get(
                          'procedure_checks', [])),
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
