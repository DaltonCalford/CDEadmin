#!/usr/bin/env python3
"""Qualify stored-dialect view replacement and metadata recreation."""
import argparse
import json
import traceback
from pathlib import Path

if __package__:
    from . import cdeadmin_firebird_views_gate as base
else:
    import cdeadmin_firebird_views_gate as base


def cases():
    return [
        ('ordered_aliases', 'SELECT 1, 2 FROM RDB$DATABASE', [(1, 2)]),
        ('cte', 'WITH Q AS (SELECT 7 X FROM RDB$DATABASE) '
         'SELECT X, X+1 FROM Q', [(7, 8)]),
        ('literal_comment', 'SELECT CAST(\'A"B\' AS VARCHAR(20)), '
         'CAST(\'tail\' AS VARCHAR(20)) FROM RDB$DATABASE '
         '-- trailing comment', [('A"B', 'tail')]),
        ('check_option', 'SELECT X, Y FROM T WHERE X > 0 WITH CHECK OPTION',
         [(5, 6)]),
    ]


def verify(connection, client, route, password, result):
    import firebird.driver as native
    from firebird.driver import core
    from pgadmin.cdeadmin.providers.firebird.database_creation import (
        create_database,
    )
    from pgadmin.cdeadmin.providers.firebird.provider import (
        _database_create_arguments, _route_arguments, _resources,
    )
    from pgadmin.cdeadmin.providers.firebird.error_diagnostics import (
        status_codes,
    )

    checks = result['legacy_view_checks'] = []
    result['legacy_view_scope'] = {
        'qualified_matrix': 'stable-output-types; explicit VARCHAR text',
        'native_literal_view_qualification': 'OPEN',
        'automatic_source_casting': False,
    }
    for dialect in (1, 3):
        configured = {**route, 'database':
                      f'/var/lib/firebird/data/views_{dialect}.fdb'}
        args = _database_create_arguments(
            configured, _route_arguments(configured)['database'],
            {'sql_dialect': dialect}, native)
        created = create_database(native, core, password=password, **args)
        created.close()
        handle = client.open_session({'route': configured})

        def sql(source, parameters=()):
            with handle.cursor() as cursor:
                cursor.execute(source, parameters)
                return cursor.fetchall() if cursor.description else []

        def rollback():
            if handle.main_transaction.is_active():
                handle.rollback()

        def exists(name):
            return bool(sql('SELECT 1 FROM RDB$RELATIONS WHERE '
                            'RDB$RELATION_NAME = ?', (name,)))

        def apply(operation, source):
            draft = {'definition': source,
                     'columns': [{'name': 'B'}, {'name': 'A'}]}
            draft['name' if operation == 'create_or_alter' else
                  'confirmation'] = 'V'
            request = {'resource_kind': 'view', 'operation_id': operation,
                       '_provider_route': configured, 'draft': draft,
                       'target_resource': {'resource_kind': 'view',
                                           'display_name': 'V'}}
            assert base.ADMINISTRATION.validate(request) == {'errors': []}
            plan = client.plan_admin_operation(request)
            assert plan['provider_payload']['firebird_dialect_binding'] == {
                'database_sql_dialect': dialect, 'client_sql_dialect': 3}
            assert plan['command_preview']['statements'][0]['source'].endswith(
                source)
            receipt = base.ADMINISTRATION.apply(
                client, plan, connection=handle)
            assert receipt['staged_in_provider_session'] is True

        try:
            assert handle.info.sql_dialect == dialect
            assert handle.sql_dialect == 3
            sql('CREATE TABLE T (X INTEGER, Y INTEGER)')
            sql('CREATE TABLE SENTINEL (X INTEGER)')
            handle.commit()
            sql('INSERT INTO T VALUES (5, 6)')
            handle.commit()
            # Independent native literal-view comparisons, without generated
            # DDL. Capture fresh creation as well as type-changing replacement.
            source = 'SELECT \'A"B\', \'tail\' FROM RDB$DATABASE'
            baseline = {'database_dialect': dialect}
            observations = result.setdefault('native_literal_view_checks', [])
            observations.append(baseline)
            baseline['direct_query_rows'] = sql(source)
            rollback()
            sql('CREATE VIEW RAW_LITERAL (B, A) AS ' + source)
            handle.commit()
            try:
                baseline['fresh_view_rows'] = sql('SELECT * FROM RAW_LITERAL')
            except native.DatabaseError as exc:
                baseline['fresh_view_status_codes'] = list(status_codes(exc))
            rollback()
            sql('DROP VIEW RAW_LITERAL')
            handle.commit()
            sql('CREATE VIEW ORACLE (B, A) AS '
                'SELECT 0, 0 FROM RDB$DATABASE')
            handle.commit()
            sql('CREATE OR ALTER VIEW ORACLE (B, A) AS\n' + source)
            rollback()
            assert sql('SELECT * FROM ORACLE') == [(0, 0)]
            rollback()
            sql('CREATE OR ALTER VIEW ORACLE (B, A) AS\n' + source)
            handle.commit()
            try:
                baseline['same_attachment_rows'] = sql('SELECT * FROM ORACLE')
            except native.DatabaseError as exc:
                baseline['same_attachment_status_codes'] = list(
                    status_codes(exc))
            rollback()
            fresh = client.open_session({'route': configured})
            try:
                try:
                    with fresh.cursor() as cursor:
                        cursor.execute('SELECT * FROM ORACLE')
                        baseline['fresh_attachment_rows'] = cursor.fetchall()
                except native.DatabaseError as exc:
                    baseline['fresh_attachment_status_codes'] = list(
                        status_codes(exc))
            finally:
                client.close_session(fresh)
            sql('DROP VIEW ORACLE')
            handle.commit()
            for operation in ('create_or_alter', 'recreate'):
                for label, source, expected in cases():
                    check = {'database_dialect': dialect,
                             'operation': operation, 'case': label,
                             'passed': False}
                    checks.append(check)
                    try:
                        # Keep result types stable in this metadata gate.
                        # Type-changing replacement is independently observed
                        # above and is not qualified by these cases.
                        original = ("SELECT CAST('old' AS VARCHAR(20)), "
                                    "CAST('prev' AS VARCHAR(20)) "
                                    "FROM RDB$DATABASE"
                                    if label == 'literal_comment' else
                                    'SELECT 0, 0 FROM RDB$DATABASE')
                        original_rows = ([('old', 'prev')] if
                                         label == 'literal_comment' else
                                         [(0, 0)])
                        sql('CREATE VIEW V (B, A) AS ' + original)
                        handle.commit()
                        sql('INSERT INTO SENTINEL VALUES (42)')
                        transaction = handle.main_transaction.info.id
                        apply(operation, source)
                        assert handle.main_transaction.info.id == transaction
                        assert sql('SELECT X FROM SENTINEL') == [(42,)]
                        rollback()
                        assert sql('SELECT * FROM V') == original_rows
                        assert sql('SELECT X FROM SENTINEL') == []
                        rollback()
                        apply(operation, source)
                        handle.commit()
                        assert sql('SELECT * FROM V') == expected
                        resources = _resources(handle, {'route': configured})
                        metadata = next(item['native'] for item in resources
                                        if item['resource_kind'] == 'view' and
                                        item['display_name'] == 'V')
                        assert metadata['view_columns'] == [
                            {'name': 'B'}, {'name': 'A'}]
                        ddl = metadata['ddl']
                        prefix = ('CREATE VIEW V (B, A)' if dialect == 1 else
                                  'CREATE VIEW "V" ("B", "A")')
                        assert ddl.startswith(prefix), ddl
                        assert metadata['definition'].strip() == source.strip()
                        replay = ddl.replace(
                            'CREATE VIEW V' if dialect == 1 else
                            'CREATE VIEW "V"',
                            'CREATE VIEW U' if dialect == 1 else
                            'CREATE VIEW "U"', 1)
                        rollback()
                        sql(replay.rstrip().rstrip(';'))
                        rollback()
                        assert not exists('U')
                        rollback()
                        sql(replay.rstrip().rstrip(';'))
                        handle.commit()
                        assert sql('SELECT * FROM U') == expected
                        sql('DROP VIEW U')
                        rollback()
                        assert sql('SELECT * FROM U') == expected
                        sql('DROP VIEW U')
                        handle.commit()
                        assert not exists('U')
                        check.update(passed=True, ddl=ddl,
                                     pending_work_preserved=True,
                                     replacement_rollback_commit=True,
                                     replay_rollback_commit=True,
                                     drop_rollback_commit=True)
                    except Exception as exc:
                        frames = traceback.extract_tb(exc.__traceback__)
                        result['failures'].append({
                            'case': (f'legacy-view-{dialect}-'
                                     f'{operation}-{label}'),
                            'error_type': type(exc).__name__,
                            'frames': [{'function': frame.name,
                                        'line': frame.lineno} for frame in
                                       frames],
                            'message': str(exc).replace(
                                password, '<redacted>')})
                    finally:
                        rollback()
                        for name in ('U', 'V'):
                            if exists(name):
                                sql('DROP VIEW ' + name)
                                handle.commit()
                        rollback()
        finally:
            client.close_session(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite evidence')
    result = base.run(extra_checks=verify)
    checks = result.get('legacy_view_checks', [])
    result['complete'] = (result['complete'] and len(checks) == 16 and
                          all(check['passed'] for check in checks))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
