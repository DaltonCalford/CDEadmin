#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Mutate only unique column fixtures through real provider forms."""

import json
import traceback
import uuid
from types import SimpleNamespace

from cdeadmin_firebird_role_ui_gate import (
    arguments, forms, firebird, _resources, _route_arguments,
    create_driver, close_workspace, plan_preview, screenshot,
    WebDriverWait, visible_named_control,
)
from cdeadmin_firebird_admin_mapping_gate import _create_client
from cdeadmin_firebird_ui_form_gate import click_unobscured
from pgadmin.cdeadmin.providers.firebird import columns


def run(options, profiles):
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    assert route['database'] == options.database_path
    _create_client(SimpleNamespace(acquire_secret=None))
    native = firebird.connect(password=route['password'],
                              **_route_arguments(route, firebird))
    prefix = 'CDE_UI_COL_' + uuid.uuid4().hex[:10].upper()
    tables = []
    domain_created = False
    browser = None
    result = {'passed': False, 'checks': [], 'failures': [],
              'credential_values_exported': False, 'fixtures_removed': False}

    def sql(source):
        with native.cursor() as cursor:
            cursor.execute(source)
        native.commit()

    def snapshot(table):
        resources = _resources(native, {'route': route})
        native.commit()
        return next(item['native'] for item in resources if
                    item['resource_kind'] == 'column' and
                    item['display_path'] == [table, 'V'])

    cases = [
        ('position', 'INTEGER', {'action': 'POSITION', 'position': 1},
         {'position': '0'}),
        ('not-null', 'INTEGER', {'action': 'SET NOT NULL'}, {'not_null': '1'}),
        ('nullable', 'INTEGER NOT NULL', {'action': 'DROP NOT NULL'},
         {'not_null': None}),
        ('drop-default', 'INTEGER DEFAULT 7', {'action': 'DROP DEFAULT'},
         {'default_source': None}),
        ('computed', 'INTEGER COMPUTED BY (X + 1)',
         {'action': 'COMPUTED', 'expression': 'X + 2'},
         {'computed_source': '(X + 2\n)'}),
        ('computed-type', 'INTEGER COMPUTED BY (X + 1)',
         {'action': 'TYPE COMPUTED', 'data_type': 'BIGINT',
          'expression': 'X + 2'}, {'computed_source': '(X + 2\n)'}),
        ('identity', 'BIGINT GENERATED ALWAYS AS IDENTITY',
         {'action': 'IDENTITY', 'generation': 'BY DEFAULT',
          'restart': 'WITH VALUE', 'restart_value': '-42', 'increment': '-3'},
         {'identity_type': '1'}),
        ('drop-identity', 'BIGINT GENERATED ALWAYS AS IDENTITY',
         {'action': 'DROP IDENTITY'}, {'identity_type': None}),
        ('computed-blob', 'INTEGER COMPUTED BY (X + 1)',
         {'action': 'TYPE COMPUTED', 'data_type': 'BLOB',
          'blob_subtype': 1, 'segment_size': 120, 'character_set': 'UTF8',
          'expression': "CAST('text' AS BLOB SUB_TYPE TEXT)"},
         {'field_type': '261', 'field_sub_type': '1',
          'segment_length': '120'}),
        ('type-domain', 'BIGINT',
         {'action': 'TYPE', 'data_type': 'DOMAIN', 'domain': prefix + '_D'},
         {'domain': prefix + '_D'}),
        ('comment-set', 'INTEGER', {'description': "  'é'  "},
         {'description': "  'é'  "}),
        ('comment-clear', 'INTEGER', {'description': ''},
         {'description': None}),
    ]
    for kind in columns.DEFAULTS:
        values = {'TEXT': "O'Connor é", 'BINARY': '414243',
                  'NUMBER': '-1.25e2',
                  'DATE': '2026-09-13', 'TIME': '12:34:56',
                  'TIMESTAMP': '2026-09-13 12:34:56'}
        draft = {'action': 'SET DEFAULT', 'default_kind': kind}
        if kind in values:
            draft['default_value'] = values[kind]
        cases.append(('default-' + kind, 'VARCHAR(128)', draft,
                      {'default_source': 'DEFAULT ' +
                       columns.default_value(draft)}))
    for kind in columns.TIMED_DEFAULTS:
        for precision in range(4):
            draft = {'action': 'SET DEFAULT', 'default_kind': kind,
                     'time_precision': precision}
            cases.append((f'default-{kind}-{precision}', 'VARCHAR(128)', draft,
                          {'default_source': 'DEFAULT ' +
                           columns.default_value(draft)}))
    for name in columns.TYPES:
        if name in ('DOMAIN', 'BLOB'):
            continue
        draft = {'action': 'TYPE', 'data_type': name}
        if name in ('CHAR', 'VARCHAR', 'NCHAR', 'NCHAR VARYING',
                    'BINARY', 'VARBINARY'):
            draft['length'] = 40
        elif name in ('NUMERIC', 'DECIMAL'):
            draft.update(precision=18, scale=3)
        elif name == 'FLOAT':
            draft['precision'] = 53
        elif name == 'DECFLOAT':
            draft['precision'] = 16
        elif name in ('TIME', 'TIMESTAMP'):
            draft['time_zone'] = 'WITH TIME ZONE'
        cases.append(('type-' + name, columns.data_type(draft), draft, {}))
    result['expected_mutation_count'] = len(cases)
    try:
        sql('CREATE DOMAIN ' + prefix + '_D AS BIGINT')
        domain_created = True
        for number, (label, definition, draft, expected) in enumerate(cases):
            table = prefix + '_' + str(number)
            sql('CREATE TABLE ' + table + ' (X INTEGER, V ' + definition + ')')
            tables.append(table)
            if label == 'comment-clear':
                sql('COMMENT ON COLUMN ' + table + ".V IS 'previous comment'")
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        probe = forms._workspace_probe(browser, ['column'])
        for number, (label, definition, draft, expected) in enumerate(cases):
            table = tables[number]
            print(label, flush=True)
            operation = next(iter(forms._enumerate_operations(
                probe['catalog'], ['column'], [
                    'comment' if label.startswith('comment-') else 'alter'])))
            target = next(item for item in probe['resources'] if
                          item['resource_kind'] == 'column' and
                          item['display_path'] == [table, 'V'])
            assert operation['execution_available'] is True
            forms._open_focused_form(browser, operation, target,
                                     probe['database_target_id'])
            forms._wait_for_operation(wait, operation)
            labels = {item['field_id']: item['label']
                      for item in operation['form']['fields']}
            plan = plan_preview(browser, wait, operation,
                                {labels[key]: value for key, value in
                                 draft.items()})
            confirmation = visible_named_control(
                browser, 'I confirm this provider-planned operation.')
            if confirmation is not None and not confirmation.is_selected():
                click_unobscured(browser, wait, confirmation)
            button = wait.until(lambda driver: visible_named_control(
                driver, 'Apply provider plan'))
            wait.until(lambda driver: button.is_enabled())
            click_unobscured(browser, wait, button)
            output = wait.until(lambda driver: next((
                element for element in driver.find_elements(
                    'css selector', '[aria-label="Provider operation result"]')
                if element.is_displayed()), None))
            assert json.loads(output.text)['accepted'] is True
            observed = snapshot(table)
            for key, value in expected.items():
                assert observed[key] == value, (key, observed.get(key), value)
            path = options.output_root / f'{number:02d}-{label}.png'
            result['checks'].append({
                'case': label, 'statements': plan['command_preview'][
                    'statements'], 'native_postcondition': observed,
                'screenshot': str(path), 'sha256': screenshot(browser, path)})
            close_workspace(browser, wait)
        result['passed'] = len(result['checks']) == len(cases)
    except Exception:
        result['failures'].append({'traceback': traceback.format_exc()})
        if browser is not None:
            path = options.output_root / 'failure.png'
            result['failure_screenshot'] = str(path)
            screenshot(browser, path)
            result['failure_geometry'] = browser.execute_script("""
              const button = [...document.querySelectorAll('button')].find(
                element => element.textContent === 'Validate and preview' &&
                  element.getClientRects().length);
              const nodes = [];
              for (let node = button; node; node = node.parentElement) {
                const style = getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                nodes.push({tag: node.tagName, classes: node.className,
                  top: rect.top, bottom: rect.bottom, height: rect.height,
                  overflow: style.overflow, flex: style.flex,
                  scrollHeight: node.scrollHeight,
                  clientHeight: node.clientHeight});
              }
              return nodes;
            """)
    finally:
        if browser is not None:
            browser.quit()
        if native.main_transaction.is_active():
            native.rollback()
        for table in reversed(tables):
            sql('DROP TABLE ' + table)
        if domain_created:
            sql('DROP DOMAIN ' + prefix + '_D')
        result['fixtures_removed'] = True
        native.close()
    return result


def main():
    options, profiles = arguments()
    result = run(options, profiles)
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] and result['fixtures_removed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
