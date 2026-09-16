#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Rename and rename back through the same focused native column editor."""

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
    close_workspace, plan_preview, screenshot, WebDriverWait,
    visible_named_control,
)
from tools.cdeadmin_firebird_columns_ui_gate import (  # noqa: E402
    cleanup_column_fixtures,
)
from tools.cdeadmin_firebird_admin_mapping_gate import (  # noqa: E402
    _create_client,
)
from pgadmin.cdeadmin.providers.firebird.provider import (  # noqa: E402
    _materialize_catalog_value,
)
from tools.cdeadmin_firebird_ui_form_gate import (  # noqa: E402
    click_unobscured, screenshot_form_pages,
)
from tools.cdeadmin_ui_evidence import fill_fields  # noqa: E402
from pgadmin.cdeadmin.providers.firebird.identity import (  # noqa: E402
    catalog_resource_id,
)
from pgadmin.cdeadmin.providers.firebird.mappings import (  # noqa: E402
    identifier,
)


def array_seed(domain_mode, row):
    """Distinct owned values for INTEGER[1:3] or INTEGER[-2:3,1:2]."""
    if not domain_mode:
        return [-2147483648, row, 2147483647]
    return [[row * 100 + outer * 10 + inner for inner in (1, 2)]
            for outer in range(-2, 4)]


def repreview_confirmation(browser, wait, operation, first_plan):
    """Re-preview without touching draft fields or changing native policy."""
    assert operation['confirmation_required'] is True
    label = 'I confirm this provider-planned operation.'
    confirmation = wait.until(lambda driver: visible_named_control(
        driver, label))
    button = visible_named_control(browser, 'Apply provider plan')
    assert not confirmation.is_selected()
    assert not button.is_enabled()
    click_unobscured(browser, wait, confirmation)
    assert confirmation.is_selected()
    assert button.is_enabled()
    # Passing no field values is essential: clearing/retyping even identical
    # text would reset the old boolean and conceal the confirmation defect.
    second_plan = plan_preview(browser, wait, operation, {})
    confirmation = wait.until(lambda driver: visible_named_control(
        driver, label))
    assert not confirmation.is_selected()
    assert not visible_named_control(
        browser, 'Apply provider plan').is_enabled()
    assert second_plan['command_preview'] == first_plan['command_preview']
    return second_plan, confirmation


def run(options, profiles):
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    assert route['database'] == options.database_path
    _create_client(SimpleNamespace(acquire_secret=None))
    native = firebird.connect(password=route['password'],
                              **_route_arguments(route, firebird))
    prefix = 'CDE_UI_RENAME_' + uuid.uuid4().hex[:10].upper()
    tables, browser = [], None
    domain_mode = os.environ.get('CDEADMIN_FIREBIRD_RENAME_KIND') == 'domain'
    domains = []
    cases = [('stored', 'INTEGER', 'W'),
             ('identity', 'BIGINT GENERATED ALWAYS AS IDENTITY', 'Id New'),
             ('computed', 'COMPUTED BY (X + 1)', 'Calculated'),
             ('blob', 'BLOB SUB_TYPE TEXT', 'épreuve:blob%'),
             ('array', 'INTEGER[1:3]', 'Quote"Name')]
    if domain_mode:
        cases = [('scalar', 'INTEGER DEFAULT 7 CHECK (VALUE > 0)', 'new'),
                 ('blob', 'BLOB SUB_TYPE TEXT', 'épreuve:blob%'),
                 ('array', 'INTEGER[-2:3,1:2]', 'Quote"Name')]
    kind = 'domain' if domain_mode else 'column'
    result = {'passed': False, 'checks': [], 'confirmation_checks': [],
              'failures': [],
              'fixtures_removed': False, 'credential_values_exported': False}

    def names(table):
        with native.cursor() as cursor:
            cursor.execute('SELECT TRIM(TRAILING FROM RDB$FIELD_NAME) '
                           'FROM RDB$RELATION_FIELDS WHERE '
                           'RDB$RELATION_NAME = ? ORDER BY RDB$FIELD_POSITION',
                           (table,))
            value = [row[0] for row in cursor.fetchall()]
        native.commit()
        return value

    def rows(table, column):
        with native.cursor() as cursor:
            cursor.execute('SELECT X, ' + identifier(column) + ' FROM ' +
                           identifier(table) + ' ORDER BY X')
            value = [[_materialize_catalog_value(item) for item in row]
                     for row in cursor.fetchall()]
        native.commit()
        return value

    def domain_source(table):
        with native.cursor() as cursor:
            cursor.execute('SELECT TRIM(TRAILING FROM RDB$FIELD_SOURCE) '
                           'FROM RDB$RELATION_FIELDS WHERE '
                           "RDB$RELATION_NAME = ? AND RDB$FIELD_NAME = 'V'",
                           (table,))
            value = cursor.fetchone()[0]
        native.commit()
        return value

    def table_exists(table):
        with native.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM RDB$RELATIONS '
                           'WHERE RDB$RELATION_NAME = ?', (table,))
            count = cursor.fetchone()[0]
        native.commit()
        assert count in (0, 1)
        return count == 1

    def confirmation_drop(table, case):
        print('confirmation drop: ' + case, flush=True)
        object_editor = case in ('array', 'identity')
        current = forms._workspace_probe(browser, ['table'],
                                         collect_context_commands=False)
        operation = next(iter(forms._enumerate_operations(
            current['catalog'], ['table'], ['drop'])))
        assert operation['confirmation_required'] is True
        target = next(item for item in current['resources'] if
                      item['resource_kind'] == 'table' and
                      item['display_name'] == table)
        forms._open_focused_form(browser, operation, target,
                                 current['database_target_id'],
                                 workspace=('object' if object_editor else
                                            'administration'))
        forms._wait_for_operation(wait, operation)
        if object_editor:
            wait.until(lambda driver: any(
                item.is_displayed() for item in driver.find_elements(
                    'css selector',
                    '[aria-label="Selected object operations"]')))
        label = next(field['label'] for field in operation['form']['fields']
                     if field['field_id'] == 'confirmation')
        first = plan_preview(browser, wait, operation, {label: table})
        expected = 'DROP TABLE ' + identifier(table)
        assert first['command_preview']['statements'][0]['source'] == expected
        assert table_exists(table)
        second, confirmation = repreview_confirmation(
            browser, wait, operation, first)
        assert table_exists(table)
        browser.execute_script(
            'arguments[0].scrollIntoView({block: "center"})', confirmation)
        path = options.output_root / (case + '-repreview-unconfirmed.png')
        digest = screenshot(browser, path, reset_scroll=False)
        click_unobscured(browser, wait, confirmation)
        button = wait.until(lambda driver: visible_named_control(
            driver, 'Apply provider plan'))
        wait.until(lambda _driver: button.is_enabled())
        click_unobscured(browser, wait, button)
        # A dropped target may disappear from selection, so inspect native
        # absence and completed form work, not a transient result panel.
        wait.until(lambda _driver: not table_exists(table))

        def idle(driver):
            if object_editor:
                return any(item.is_displayed() for item in
                           driver.find_elements(
                               'css selector',
                               '[aria-label="Completed object task"]'))
            field = visible_named_control(driver, label)
            return field is not None and field.is_enabled()

        wait.until(idle)
        target_control = visible_named_control(browser, 'Target resource')
        if object_editor:
            assert target_control is None
        else:
            assert target_control is not None
            assert not target_control.text.replace('\u200b', '').strip()
        assert not visible_named_control(
            browser, 'Validate and preview').is_enabled()
        assert not visible_named_control(
            browser, 'Apply provider plan').is_enabled()
        assert not any(item.is_displayed() for item in browser.find_elements(
            'css selector', '[role="alert"][class*="MuiAlert-standardError"]'))
        assert 'Do not repeat the operation' not in browser.find_element(
            'tag name', 'body').text
        output = wait.until(lambda driver: next((
            item for item in driver.find_elements(
                'css selector', '[aria-label="Provider operation result"]')
            if item.is_displayed()), None))
        response = json.loads(output.text)
        assert response['accepted'] is True
        assert response['commit_requested'] is True
        assert response['transaction_finality_interpreted_by_common_code'] is (
            False)
        response_target = browser.find_element(
            'css selector', '[aria-label="Provider response target"]')
        assert table in response_target.text
        assert target['resource_id'] in response_target.text
        after_drop_images = screenshot_form_pages(
            browser, options.output_root / (case + '-drop-result'))
        result['confirmation_checks'].append({
            'case': case, 'resource_kind': 'table', 'operation_id': 'drop',
            'surface': 'object-editor' if object_editor else 'task-form',
            'first_plan_id': first['plan_id'],
            'second_plan_id': second['plan_id'],
            'same_draft_reconfirmed': True,
            'no_automatic_replacement_target': True,
            'drop_receipt_visible_after_refresh': True,
            'response_original_resource_id': target['resource_id'],
            'after_drop_screenshots': after_drop_images,
            'native_object_present_before_explicit_apply': True,
            'native_object_absent_after_explicit_apply': True,
            'screenshot': str(path), 'sha256': digest})
        close_workspace(browser, wait)

    try:
        for index, (_label, definition, _new) in enumerate(cases):
            table = prefix + '_' + str(index)
            with native.cursor() as cursor:
                if domain_mode:
                    domain = table + '_D'
                    cursor.execute('CREATE DOMAIN ' + identifier(domain) +
                                   ' AS ' + definition)
                    domains.append(domain)
                    definition = identifier(domain)
                cursor.execute('CREATE TABLE ' + identifier(table) +
                               ' (X INTEGER, V ' + definition + ')')
            native.commit()
            tables.append(table)
            with native.cursor() as cursor:
                for value in (7, 11):
                    cursor.execute('INSERT INTO ' + identifier(table) +
                                   ' (X) VALUES (?)', (value,))
                if _label == 'stored':
                    cursor.execute('UPDATE ' + identifier(table) +
                                   ' SET V = X * 3')
                elif _label == 'blob':
                    cursor.execute('UPDATE ' + identifier(table) +
                                   ' SET V = ?', ('  Native é text  ',))
                elif _label == 'array':
                    for value in (7, 11):
                        cursor.execute('UPDATE ' + identifier(table) +
                                       ' SET V = ? WHERE X = ?',
                                       (array_seed(domain_mode, value), value))
            native.commit()
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        probe = forms._workspace_probe(browser, [kind],
                                       collect_context_commands=False)
        operation = next(iter(forms._enumerate_operations(
            probe['catalog'], [kind], ['rename'])))
        for index, (label, _definition, new_name) in enumerate(cases):
            table = tables[index]
            try:
                baseline_rows = rows(table, 'V')
                if label == 'array':
                    assert baseline_rows == [
                        [value, array_seed(domain_mode, value)]
                        for value in (7, 11)]
                original = table + '_D' if domain_mode else 'V'
                if domain_mode:
                    new_name = table + '_' + new_name
                    domains.append(new_name)
                path = [original] if domain_mode else [table, original]
                target = next(item for item in probe['resources'] if
                              item['resource_kind'] == kind and
                              item['display_path'] == path)
                forms._open_focused_form(browser, operation, target,
                                         probe['database_target_id'])
                forms._wait_for_operation(wait, operation)
                for old, new in [(original, new_name), (new_name, original)]:
                    label_now = label + (
                        '-forward' if old == original else '-back')
                    print(kind + ' rename: ' + label_now, flush=True)
                    fill_fields(wait, ['New name=' + new])
                    images = screenshot_form_pages(
                        browser, options.output_root / label_now)
                    plan = plan_preview(browser, wait, operation, {})
                    source = plan['command_preview']['statements'][0]['source']
                    expected = (
                        'ALTER DOMAIN ' + identifier(old) if domain_mode else
                        'ALTER TABLE ' + identifier(table) +
                        ' ALTER COLUMN ' + identifier(old))
                    assert source == expected + ' TO ' + identifier(new)
                    confirm = visible_named_control(
                        browser, 'I confirm this provider-planned operation.')
                    if confirm is not None and not confirm.is_selected():
                        click_unobscured(browser, wait, confirm)
                    button = wait.until(lambda driver: visible_named_control(
                        driver, 'Apply provider plan'))
                    wait.until(lambda _driver: button.is_enabled())
                    click_unobscured(browser, wait, button)
                    output = wait.until(lambda driver: next((
                        item for item in driver.find_elements(
                            'css selector',
                            '[aria-label="Provider operation result"]')
                        if item.is_displayed()), None))
                    response = json.loads(output.text)
                    receipt = response['resource_identity_change']
                    assert response['accepted'] is True
                    assert receipt['native_identity_verified'] is True
                    assert receipt['committed_by_provider'] is True
                    assert receipt['resource_id'] == catalog_resource_id(
                        kind, [] if domain_mode else [table], new)
                    if domain_mode:
                        assert domain_source(table) == new
                        assert names(table) == ['X', 'V']
                    else:
                        assert names(table) == ['X', new]
                    assert rows(table, 'V' if domain_mode else new) == (
                        baseline_rows)
                    wait.until(lambda driver: visible_named_control(
                        driver, 'Validate and preview').is_enabled())
                    # A rename changes the resource ID, not the identity of
                    # the completed operation. Its receipt must survive the
                    # automatic catalog/selection refresh, with no old plan
                    # left executable.
                    wait.until(lambda driver: next((
                        json.loads(item.text) == response
                        for item in driver.find_elements(
                            'css selector',
                            '[aria-label="Provider operation result"]')
                        if item.is_displayed()), False))
                    assert not visible_named_control(
                        browser, 'Apply provider plan').is_enabled()
                    assert not any(item.is_displayed() for item in
                                   browser.find_elements(
                                       'css selector',
                                       '[aria-label="Provider plan preview"]'))
                    assert 'Do not repeat the operation' not in (
                        browser.find_element('tag name', 'body').text)
                    path = options.output_root / (label_now + '-result.png')
                    result['checks'].append({
                        'case': label_now, 'receipt': receipt,
                        'receipt_visible_after_refresh': True,
                        'submitted_plan_retired': True,
                        'rows_preserved': baseline_rows,
                        'form_screenshots': images, 'screenshot': str(path),
                        'sha256': screenshot(browser, path)})
                close_workspace(browser, wait)
                confirmation_drop(table, label)
            except Exception:
                result['failures'].append({'case': label,
                                          'traceback': traceback.format_exc()})
                path = options.output_root / (label + '-failure.png')
                screenshot(browser, path)
                close_workspace(browser, wait)
        result['passed'] = (len(result['checks']) == len(cases) * 2 and
                            len(result['confirmation_checks']) == len(cases))
    except Exception:
        result['failures'].append({'traceback': traceback.format_exc()})
    finally:
        cleanup = cleanup_column_fixtures(
            browser, native, tables, domains=domains)
        result['fixtures_removed'] = cleanup['fixtures_removed']
        result['failures'].extend(cleanup['errors'])
        result['passed'] = result['passed'] and not result['failures']
    return result


def main():
    options, profiles = arguments()
    result = run(options, profiles)
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
