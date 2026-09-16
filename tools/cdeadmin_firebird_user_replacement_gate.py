#!/usr/bin/env python3
"""User replacement and authentication in an owned Firebird security store."""
import argparse
import json
from pathlib import Path

if __package__:
    from . import cdeadmin_firebird_views_gate as base
else:
    import cdeadmin_firebird_views_gate as base


def verify(connection, client, route, password, result):
    import firebird.driver as native
    from pgadmin.cdeadmin.providers.firebird.error_diagnostics import (
        status_codes,
    )
    checks = result['user_replacement_checks'] = []
    tasks = result['user_replacement_task_evidence'] = {}
    account = 'CDE_REPLACEMENT_USER'
    rotated = password + 'next'

    def sql(source, params=(), handle=None):
        with (handle or connection).cursor() as cursor:
            cursor.execute(source, params)
            return cursor.fetchall() if cursor.description else []

    def apply(op, values, handle=None, name=account):
        request = {'resource_kind': 'user', 'operation_id': op,
                   '_provider_route': route,
                   'target_resource': {'resource_kind': 'user',
                                       'display_name': name,
                                       'native': {'plugin': 'Srp'}},
                   'draft': {'name' if op == 'create_or_alter' else
                             'confirmation': name, 'plugin': 'Srp', **values}}
        validation = base.ADMINISTRATION.validate(request)
        assert validation == {'errors': []}, validation
        plan = base.ADMINISTRATION.plan(request)
        preview = json.dumps(plan['command_preview'])
        assert password not in preview
        receipt = base.ADMINISTRATION.apply(
            client, plan, connection=handle or connection)
        assert receipt['staged_in_provider_session'] is True
        tasks['visual_admin.user.' + op] = {
            'live_execution': 'passed', 'credentials_redacted': True,
            'statements': [s['source'] for s in
                           plan['command_preview']['statements']]}

    def login(secret, expected=True, query=None):
        try:
            observer = native.connect(password=secret, **base._route_arguments(
                {**route, 'user': account}, native))
        except Exception as error:
            codes = list(status_codes(error))
            assert not expected and 335544472 in codes, codes
            return
        try:
            assert expected, 'Unexpected authentication success'
            if query:
                return sql(query, handle=observer)
        finally:
            if observer.main_transaction.is_active():
                observer.rollback()
            observer.close()

    def metadata():
        rows = sql('SELECT SEC$FIRST_NAME, SEC$ACTIVE, SEC$ADMIN '
                   'FROM SEC$USERS WHERE SEC$USER_NAME = ? '
                   "AND SEC$PLUGIN = 'Srp'", (account,))
        connection.rollback()
        return rows

    apply('create_or_alter', {'password': password, 'first_name': 'Original'})
    connection.rollback()
    assert metadata() == []
    login(password, False)
    apply('create_or_alter', {
        'password': password, 'first_name': 'Original',
        'active_state': 'ACTIVE', 'admin_role': 'REVOKE'})
    connection.commit()
    login(password)
    assert metadata() == [('Original', True, False)]
    checks.append({'case': 'creation', 'rollback_commit_verified': True,
                   'authentication_verified': True})

    apply('create_or_alter', {'password': rotated, 'first_name': 'Changed'})
    connection.rollback()
    login(password)
    login(rotated, False)
    apply('create_or_alter', {
        'password': rotated, 'first_name': 'Changed', 'admin_role': 'GRANT',
        'tags': [{'name': 'DEPARTMENT', 'value': 'QA'}]})
    connection.commit()
    login(rotated)
    login(password, False)
    assert metadata() == [('Changed', True, True)]
    assert sql('SELECT SEC$VALUE FROM SEC$USER_ATTRIBUTES '
               'WHERE SEC$USER_NAME = ? AND SEC$KEY = ?',
               (account, 'DEPARTMENT')) == [('QA',)]
    connection.rollback()
    apply('create_or_alter', {
        'admin_role': 'REVOKE', 'tags': [{'name': 'DEPARTMENT', 'drop': True}],
        'active_state': 'INACTIVE'})
    connection.commit()
    login(rotated, False)
    assert metadata() == [('Changed', False, False)]
    assert sql('SELECT SEC$VALUE FROM SEC$USER_ATTRIBUTES '
               'WHERE SEC$USER_NAME = ? AND SEC$KEY = ?',
               (account, 'DEPARTMENT')) == []
    connection.rollback()
    apply('create_or_alter', {'active_state': 'ACTIVE'})
    connection.commit()
    login(rotated)
    checks.append({'case': 'alteration', 'rollback_commit_verified': True,
                   'rotation_state_admin_tags_verified': True})

    sql('CREATE TABLE USER_GRANT_FIXTURE (ID INTEGER)')
    connection.commit()
    sql('INSERT INTO USER_GRANT_FIXTURE VALUES (42)')
    sql('GRANT SELECT ON USER_GRANT_FIXTURE TO USER ' + account)
    connection.commit()
    apply('recreate', {'password': password})
    connection.rollback()
    login(rotated)
    login(password, False)
    assert metadata() == [('Changed', True, False)]
    apply('recreate', {'password': password})
    connection.commit()
    login(rotated, False)
    assert login(password, query='SELECT ID FROM USER_GRANT_FIXTURE') == [
        (42,)]
    recreated = metadata()
    assert recreated == [(None, True, False)], recreated
    checks.append({'case': 'recreation', 'rollback_commit_verified': True,
                   'authentication_and_name_grants_verified': True})
    committed_tasks = dict(tasks)

    apply('create_or_alter', {'password': password}, name='USER_VICTIM')
    connection.commit()

    reader = native.connect(password=password, **base._route_arguments(
        {**route, 'user': account}, native))
    denials = []
    try:
        for op in ('create_or_alter', 'recreate'):
            try:
                apply(op, {'password': rotated}, reader, name='USER_VICTIM')
                reader.commit()
            except Exception as error:
                codes = list(status_codes(error))
                assert 335544352 in codes, codes
                denials.append({'operation': op, 'native_status_codes': codes})
            else:
                raise AssertionError('Unprivileged other-user change admitted')
            finally:
                if reader.main_transaction.is_active():
                    reader.rollback()
    finally:
        reader.close()
    observer = native.connect(password=password,
                              **base._route_arguments(
                                  {**route, 'user': 'USER_VICTIM'}, native))
    observer.close()
    checks.append({'case': 'permission-denials', 'denials': denials,
                   'other_user_authentication_preserved': True})
    result['user_replacement_task_evidence'] = committed_tasks


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
                          len(result.get('user_replacement_checks', [])) == 4)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
