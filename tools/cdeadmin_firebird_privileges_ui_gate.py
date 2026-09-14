#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Exercise grant/revoke forms using only owned demo-database objects."""

import json
import os
import sys
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.cdeadmin_firebird_role_ui_gate import (  # noqa: E402
    arguments, forms, firebird, _route_arguments, create_driver,
    close_workspace, plan_preview, WebDriverWait, visible_named_control,
)
from tools.cdeadmin_firebird_admin_mapping_gate import (  # noqa: E402
    _create_client,
)
from tools.cdeadmin_firebird_ui_form_gate import (  # noqa: E402
    click_unobscured, fill_form_values, screenshot_form_pages, screenshot,
)
from pgadmin.cdeadmin.providers.firebird.privileges import (  # noqa: E402
    OBJECT_PRIVILEGES, DDL_CLASSES, PRINCIPAL_KINDS, compile_privilege,
)
from pgadmin.cdeadmin.providers.firebird.mappings import identifier  # noqa


def run(options, profiles):
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    assert route['database'] == options.database_path
    _create_client(SimpleNamespace(acquire_secret=None))
    native = firebird.connect(password=route['password'],
                              **_route_arguments(route, firebird))
    prefix = 'CDE_UI_PRIV_' + uuid.uuid4().hex[:10].upper()
    creator_name = prefix + '_CREATOR'
    names = {key: prefix + '_' + key for key in
             ('T', 'B', 'VW', 'P', 'F', 'PK', 'TR', 'S', 'E', 'R')}
    q = {key: identifier(name) for key, name in names.items()}
    created = []
    browser = None
    result = {'passed': False, 'checks': [], 'failures': [],
              'fixtures_removed': False, 'credential_values_exported': False,
              'context_commands_collected': False}

    def execute(source):
        with native.cursor() as cursor:
            cursor.execute(source)
        native.commit()

    def snapshot():
        with native.cursor() as cursor:
            cursor.execute('SELECT RDB$USER, RDB$RELATION_NAME, '
                           'RDB$FIELD_NAME, RDB$PRIVILEGE, RDB$GRANTOR, '
                           'RDB$GRANT_OPTION, RDB$USER_TYPE, RDB$OBJECT_TYPE '
                           'FROM RDB$USER_PRIVILEGES')
            rows = {(*row, 'RDB$USER_PRIVILEGES') for row in cursor.fetchall()}
            cursor.execute('SELECT SEC$USER, SEC$USER_TYPE '
                           'FROM SEC$DB_CREATORS')
            rows.update((user, 'CREATE DATABASE', None, 'C', None, None,
                         kind, 21, 'SEC$DB_CREATORS')
                        for user, kind in cursor.fetchall())
        native.commit()
        return rows

    def mutate(label, operation_id, values):
        print('privilege mutation: ' + label, flush=True)
        operation = next(
            op for item in probe['catalog']['objects'] if
            item['resource_kind'] == 'privilege' for op in item['operations']
            if op['operation_id'] == operation_id)
        operation = {**operation, 'resource_kind': 'privilege'}
        assert operation['target_required'] is False
        forms._open_focused_form(browser, operation,
                                 {'resource_id': 'cdeadmin-create-scope:'
                                  'privilege'}, probe['database_target_id'])
        forms._wait_for_operation(wait, operation)
        fields = operation['form']['fields']
        by_label = {}
        for field in fields:
            if field['field_id'] in values:
                value = values[field['field_id']]
                by_label[field['label']] = (
                    json.dumps(value) if isinstance(value, (list, dict, bool))
                    else str(value))
        fill_form_values(browser, wait, fields, by_label)
        paths = screenshot_form_pages(browser, options.output_root / label)
        plan_preview(browser, wait, operation, {})
        confirmation = visible_named_control(
            browser, 'I confirm this provider-planned operation.')
        if confirmation is not None and not confirmation.is_selected():
            click_unobscured(browser, wait, confirmation)
        button = wait.until(lambda web: visible_named_control(
            web, 'Apply provider plan'))
        wait.until(lambda _web: button.is_enabled())
        click_unobscured(browser, wait, button)
        rendered = wait.until(lambda web: next((
            node for node in web.find_elements(
                'css selector', '[aria-label="Provider operation result"]')
            if node.is_displayed()), None))
        observed = json.loads(rendered.text)
        assert observed.get('accepted') is True
        wait.until(lambda web: visible_named_control(
            web, 'Validate and preview').is_enabled())
        assert 'Do not repeat the operation' not in (
            browser.find_element('tag name', 'body').text)
        path = options.output_root / (label + '-result.png')
        digest = screenshot(browser, path)
        result['checks'].append({'case': label, 'operation': operation_id,
                                 'form_pages': paths,
                                 'result_screenshot': str(path),
                                 'sha256': digest})
        close_workspace(browser, wait)

    try:
        for kind, key, source in [
                ('TABLE', 'T', f'CREATE TABLE {q["T"]} (X INTEGER)'),
                ('TABLE', 'B', f'CREATE TABLE {q["B"]} '
                 '(X INTEGER, V INTEGER)'),
                ('VIEW', 'VW', f'CREATE VIEW {q["VW"]} AS SELECT X '
                 f'FROM {q["T"]}'),
                ('PROCEDURE', 'P', f'CREATE PROCEDURE {q["P"]} AS BEGIN END'),
                ('FUNCTION', 'F', f'CREATE FUNCTION {q["F"]} RETURNS INTEGER '
                 'AS BEGIN RETURN 1; END'),
                ('PACKAGE', 'PK', f'CREATE PACKAGE {q["PK"]} AS '
                 'BEGIN PROCEDURE P; END'),
                ('TRIGGER', 'TR', f'CREATE TRIGGER {q["TR"]} FOR {q["T"]} '
                 'INACTIVE BEFORE INSERT AS BEGIN END'),
                ('SEQUENCE', 'S', f'CREATE SEQUENCE {q["S"]}'),
                ('EXCEPTION', 'E', f'CREATE EXCEPTION {q["E"]} \'test\''),
                ('ROLE', 'R', f'CREATE ROLE {q["R"]}')]:
            execute(source)
            created.append((kind, names[key]))
        execute(f'CREATE PACKAGE BODY {q["PK"]} AS BEGIN PROCEDURE P AS '
                'BEGIN END END')
        base = {'principal_kind': 'ROLE', 'principal': names['R'],
                'object_type': 'TABLE', 'object_name': names['B'],
                'privileges': ['SELECT']}
        targets = {'TABLE': 'B', 'VIEW': 'VW', 'PROCEDURE': 'P',
                   'FUNCTION': 'F', 'PACKAGE': 'PK', 'SEQUENCE': 'S',
                   'GENERATOR': 'S', 'EXCEPTION': 'E'}
        cases = [(kind.lower(), {**base, 'object_type': kind,
                                 'object_name': names[targets[kind]],
                                 'privileges': list(privileges)})
                 for kind, privileges in OBJECT_PRIVILEGES.items()]
        cases.extend(('class-' + kind.lower().replace(' ', '-'), {
            'privilege_scope': 'ddl_class', 'ddl_class': kind,
            'ddl_privileges': ['CREATE', 'ALTER ANY', 'DROP ANY'],
            'principal_kind': 'ROLE', 'principal': names['R']})
            for kind in DDL_CLASSES)
        principals = {'USER': prefix + '_USER', 'ROLE': names['R'],
                      'PUBLIC': '', 'PROCEDURE': names['P'],
                      'FUNCTION': names['F'], 'PACKAGE': names['PK'],
                      'TRIGGER': names['TR'], 'VIEW': names['VW'],
                      'GROUP': prefix + '_GROUP',
                      'SYSTEM PRIVILEGE': 'USER_MANAGEMENT'}
        cases.extend(('grantee-' + kind.lower().replace(' ', '-'), {
            **base, 'principal_kind': kind, 'principal': principals[kind]})
            for kind in PRINCIPAL_KINDS)
        cases.extend([
            ('columns', {**base, 'privileges': ['UPDATE', 'REFERENCES'],
                         'update_columns': [{'name': 'V'}],
                         'reference_columns': [{'name': 'X'}],
                         'grant_option': True, 'grantor': 'SYSDBA'}),
            ('database', {'privilege_scope': 'database',
                          'database_privileges': ['ALTER', 'DROP'],
                          'principal_kind': 'ROLE', 'principal': names['R']}),
            ('database-create', {'privilege_scope': 'database',
                                 'database_privileges': ['CREATE'],
                                 'principal_kind': 'USER',
                                 'principal': creator_name}),
            ('multiple-grantees', {**base, 'additional_grantees': [
                {'kind': 'USER', 'name': prefix + '_EXTRA'},
                {'kind': 'PUBLIC'}]}),
            ('option-only', {**base, 'grant_option': True}, 'option-only'),
            ('all-objects', base, 'all-objects'),
        ])
        scope = os.environ.get('CDEADMIN_FIREBIRD_PRIVILEGES_SCOPE')
        if scope == 'smoke':
            cases = [item for item in cases if item[0] in
                     ('view', 'columns', 'class-table', 'grantee-public')]
        elif scope == 'extra':
            cases = [item for item in cases if item[0] in
                     ('multiple-grantees', 'option-only', 'all-objects',
                      'database-create')]
        elif scope == 'create-only':
            cases = [item for item in cases if item[0] == 'database-create']
        result['expected_mutation_count'] = sum(
            3 if len(item) == 3 and item[2] == 'option-only' else 2
            for item in cases)
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        probe = forms._workspace_probe(browser, ['privilege'],
                                       collect_context_commands=False)
        for entry in cases:
            label, values = entry[:2]
            mode = entry[2] if len(entry) == 3 else None
            before = snapshot()
            revoke = {
                **values, 'grant_option': False,
                'confirmation': ', '.join([
                    values.get('principal') or 'PUBLIC',
                    *(item.get('name') or 'PUBLIC' for item in
                      values.get('additional_grantees', []))])}
            cleanup_revoke = revoke
            try:
                mutate(label + '-grant', 'grant', values)
                granted = snapshot()
                assert granted != before, 'No native grant change'
                expected_revoked = before
                if mode == 'option-only':
                    revoke = {**revoke, 'grant_option_only': True}
                    expected_revoked = before | {
                        (*row[:5], 0, *row[6:]) for row in granted - before}
                elif mode == 'all-objects':
                    revoke = {key: value for key, value in revoke.items() if
                              key in ('principal', 'principal_kind',
                                      'additional_grantees', 'confirmation')}
                    revoke['privilege_scope'] = 'all_objects'
                mutate(label + '-revoke', 'revoke', revoke)
                assert snapshot() == expected_revoked, 'Unexpected revoke'
                if mode == 'option-only':
                    mutate(label + '-full-revoke', 'revoke', cleanup_revoke)
                    assert snapshot() == before, 'Final revoke differs'
                result['checks'][-1]['native_grants_restored'] = True
            except Exception:
                result['failures'].append({
                    'case': label, 'traceback': traceback.format_exc()})
                screenshot(browser, options.output_root /
                           (label + '-fail.png'))
                if native.main_transaction.is_active():
                    native.rollback()
                if snapshot() != before:
                    execute(compile_privilege('revoke', cleanup_revoke))
                assert snapshot() == before, 'Cannot restore failed fixture'
                result.setdefault('failed_cases_restored', []).append(label)
                close_workspace(browser, wait)
        result['passed'] = not result['failures']
    except Exception:
        result['failures'].append({'traceback': traceback.format_exc()})
        if browser is not None:
            try:
                screenshot(browser, options.output_root / 'failure.png')
            except Exception:
                pass
    finally:
        if browser is not None:
            try:
                browser.quit()
            except Exception as error:
                result['failures'].append({'case': 'browser-cleanup',
                                           'error_type': type(error).__name__})
        removed = True
        try:
            if native.main_transaction.is_active():
                native.rollback()
            with native.cursor() as cursor:
                cursor.execute('SELECT COUNT(*) FROM SEC$DB_CREATORS WHERE '
                               'SEC$USER = ? AND SEC$USER_TYPE = 8',
                               (creator_name,))
                creator_count = cursor.fetchone()[0]
            native.commit()
            if creator_count:
                execute('REVOKE CREATE DATABASE FROM USER ' +
                        identifier(creator_name))
            with native.cursor() as cursor:
                cursor.execute('SELECT COUNT(*) FROM SEC$DB_CREATORS WHERE '
                               'SEC$USER = ? AND SEC$USER_TYPE = 8',
                               (creator_name,))
                assert cursor.fetchone()[0] == 0
            native.commit()
            result['database_creator_removed'] = True
        except Exception:
            removed = False
            result['failures'].append({
                'case': 'creator-cleanup', 'owned_principal': creator_name,
                'traceback': traceback.format_exc()})
        for kind, name in reversed(created):
            try:
                if native.main_transaction.is_active():
                    native.rollback()
                execute('DROP ' + kind + ' ' + identifier(name))
            except Exception:
                removed = False
                result['failures'].append({
                    'case': 'fixture-cleanup', 'owned_object': name,
                    'traceback': traceback.format_exc()})
        result['fixtures_removed'] = removed
        try:
            native.close()
        except Exception as error:
            result['failures'].append({'case': 'attachment-cleanup',
                                       'error_type': type(error).__name__})
        result['passed'] = result['passed'] and not result['failures']
    return result


def main():
    options, profiles = arguments()
    result = run(options, profiles)
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'],
                      'checks': len(result['checks']),
                      'fixtures_removed': result['fixtures_removed']}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
