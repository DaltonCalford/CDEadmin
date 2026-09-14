#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Mutate only unique column fixtures through real provider forms."""

import json
import os
import sys
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.cdeadmin_firebird_role_ui_gate import (  # noqa: E402
    arguments, forms, firebird, _resources, _route_arguments,
    create_driver, close_workspace, plan_preview, screenshot,
    WebDriverWait, visible_named_control,
)
from tools.cdeadmin_firebird_admin_mapping_gate import (  # noqa: E402
    _create_client,
)
from tools.cdeadmin_firebird_ui_form_gate import (  # noqa: E402
    click_unobscured, fill_form_values,
)
from pgadmin.cdeadmin.providers.firebird import columns  # noqa: E402
from pgadmin.cdeadmin.visual_admin import (  # noqa: E402
    ProviderVisualAdministration,
)
from tools.cdeadmin_ui_evidence import fill_fields  # noqa: E402


def cleanup_column_fixtures(browser, native, tables, domain=None):
    """Collect failures without letting browser shutdown skip owned DDL."""
    errors = []

    def attempt(stage, callback):
        try:
            callback()
            return True
        except Exception as error:
            errors.append({'stage': stage, 'error_type': type(error).__name__})
            return False

    if browser is not None:
        attempt('browser shutdown', browser.quit)
    attempt('rollback before cleanup', lambda: native.rollback()
            if native.main_transaction.is_active() else None)
    removed = True
    for kind, name in [('TABLE', table) for table in reversed(tables)] + (
            [('DOMAIN', domain)] if domain else []):
        def drop(kind=kind, name=name):
            with native.cursor() as cursor:
                catalog, field_name = (
                    ('RDB$RELATIONS', 'RDB$RELATION_NAME') if kind == 'TABLE'
                    else ('RDB$FIELDS', 'RDB$FIELD_NAME'))
                cursor.execute('SELECT COUNT(*) FROM ' + catalog +
                               ' WHERE ' + field_name + ' = ?', (name,))
                row = cursor.fetchone()
                if row is None or row[0] not in (0, 1):
                    raise RuntimeError('Fixture existence could not '
                                       'be verified')
                if row[0] == 0:
                    native.commit()
                    return
                cursor.execute('DROP ' + kind + ' ' + columns.identifier(name))
            native.commit()
        if not attempt('drop ' + kind + ' ' + name, drop):
            removed = False
            attempt('rollback failed drop', lambda: native.rollback()
                    if native.main_transaction.is_active() else None)
    attempt('close native attachment', native.close)
    return {'fixtures_removed': removed, 'errors': errors}


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
    create_cases = [
        ('stored', {}, {'field_type': '8'}),
        ('identity', {'column_mode': 'IDENTITY', 'generation': 'ALWAYS',
                      'start_value': '25', 'increment': '5'},
         {'identity_type': '0', 'identity_initial_value': '25',
          'identity_increment': '5'}),
        ('computed', {'column_mode': 'COMPUTED', 'expression': 'X * 2'},
         {'computed_source': '(X * 2\n)', 'field_type': '8'}),
        ('computed-inferred', {'column_mode': 'COMPUTED INFERRED',
                               'expression': 'X * 1.25'},
         {'computed_source': '(X * 1.25\n)'}),
        ('array', {'dimensions': [{'lower': -2, 'upper': 3},
                                  {'lower': 1, 'upper': 2}]},
         {'field_type': '8'}),
        ('default', {'has_default': True, 'default_kind': 'TEXT',
                     'default_value': "O'Connor", 'data_type': 'VARCHAR',
                     'length': 40}, {'default_source': "DEFAULT 'O''Connor'"}),
        ('not-null', {'constraints': [{'kind': 'NOT NULL', 'name': 'NN'}]},
         {'not_null': '1'}),
        ('check', {'constraints': [{'kind': 'CHECK', 'name': 'CK',
                                    'expression': 'V > 0'}]}, {}),
        ('unique', {'constraints': [{'kind': 'UNIQUE', 'name': 'UQ',
                                     'index_name': 'UI',
                                     'index_direction': 'DESCENDING'}]}, {}),
        ('primary', {'constraints': [{'kind': 'PRIMARY KEY', 'name': 'PK'}]},
         {'not_null': '1'}),
        ('collation', {'data_type': 'VARCHAR', 'length': 20,
                       'character_set': 'UTF8', 'collation': 'UNICODE_CI'},
         {'collation': 'UNICODE_CI'}),
        ('domain', {'data_type': 'DOMAIN', 'domain': prefix + '_D'},
         {'domain': prefix + '_D'}),
        ('blob', {'data_type': 'BLOB', 'blob_subtype': 1,
                  'segment_size': 120, 'character_set': 'UTF8'},
         {'field_type': '261', 'segment_length': '120'}),
    ]
    for label, draft, expected in create_cases:
        cases.append(('create-' + label, None,
                      {'column_mode': 'STORED', 'data_type': 'INTEGER',
                       **draft}, expected))
    cases.extend([
        ('table-create', None, {'columns': [
            {'name': 'X', 'column_mode': 'STORED', 'data_type': 'INTEGER'},
            {'name': 'V', 'column_mode': 'IDENTITY', 'data_type': 'BIGINT',
             'generation': 'ALWAYS', 'start_value': '25', 'increment': '5',
             'constraints': [{'kind': 'PRIMARY KEY'}]},
            {'name': 'C', 'column_mode': 'COMPUTED', 'data_type': 'INTEGER',
             'expression': 'X * 2'},
            {'name': 'A', 'column_mode': 'STORED', 'data_type': 'VARCHAR',
             'length': 10, 'character_set': 'UTF8',
             'dimensions': [{'lower': -2, 'upper': 3}]}]},
         {'column_names': ['X', 'V', 'C', 'A']}),
        ('table-add', 'INTEGER', {'add_columns': [
            {'name': 'ADDED', 'column_mode': 'STORED', 'data_type': 'VARCHAR',
             'length': 20, 'has_default': True, 'default_kind': 'TEXT',
             'default_value': 'test', 'constraints': [{'kind': 'NOT NULL'}]},
            {'name': 'CALC', 'column_mode': 'COMPUTED INFERRED',
             'expression': 'X + 1'}]},
         {'column_names': ['X', 'V', 'ADDED', 'CALC']}),
        ('table-rename', 'INTEGER', {'rename_columns': [
            {'from': 'V', 'to': 'RENAMED'}]},
         {'column_names': ['X', 'RENAMED']}),
        ('table-drop', 'INTEGER', {'drop_columns': ['V']},
         {'column_names': ['X']}),
    ])
    for security, native_security in (
            ('INHERIT', None), ('INVOKER', False), ('DEFINER', True)):
        for publication in ('DEFAULT', 'ENABLE', 'DISABLE'):
            cases.append((
                'table-create-persistent-' + security + '-' + publication,
                None, {'table_type': 'PERSISTENT', 'sql_security': security,
                       'publication': publication, 'columns': [
                           {'name': 'V', 'column_mode': 'STORED',
                            'data_type': 'INTEGER'}]},
                {'relation_type': 0, 'sql_security': native_security,
                 'publication_enabled': publication == 'ENABLE'}))
        for retention in ('DELETE ROWS', 'PRESERVE ROWS'):
            cases.append((
                'table-create-temporary-' + security + '-' + retention,
                None, {'table_type': 'GLOBAL TEMPORARY',
                       'sql_security': security, 'on_commit': retention,
                       'columns': [{'name': 'V', 'column_mode': 'STORED',
                                    'data_type': 'INTEGER'}]},
                {'relation_type': 5 if retention == 'DELETE ROWS' else 4,
                 'sql_security': native_security,
                 'publication_enabled': False}))
        for publication in ('ENABLE', 'DISABLE'):
            cases.append((
                'table-alter-attributes-' + security + '-' + publication,
                'INTEGER', {'sql_security': security,
                            'publication': publication},
                {'sql_security': native_security,
                 'publication_enabled': publication == 'ENABLE'}))
    scope = os.environ.get('CDEADMIN_FIREBIRD_COLUMNS_SCOPE', 'all')
    if scope not in ('all', 'create', 'alter', 'table', 'external'):
        raise ValueError('Unknown column verification scope')
    if scope == 'external':
        cases = []
        for security, native_security in (
                ('INHERIT', None), ('INVOKER', False), ('DEFINER', True)):
            for publication in ('DEFAULT', 'ENABLE', 'DISABLE'):
                cases.append((
                    f'table-create-external-{security}-{publication}', None,
                    {'table_type': 'EXTERNAL', 'sql_security': security,
                     'publication': publication, 'external_file':
                     '/var/lib/firebird/data/' + prefix + '_' +
                     security + '_' + publication,
                     'columns': [{'name': 'V', 'column_mode': 'STORED',
                                  'data_type': 'INTEGER'}]},
                    {'relation_type': 2, 'sql_security': native_security,
                     'publication_enabled': publication == 'ENABLE'}))
    elif scope != 'all':
        cases = [item for item in cases if (
            item[0].startswith('table-') if scope == 'table' else
            item[0].startswith('create-') if scope == 'create' else
            not item[0].startswith(('table-', 'create-')))]
    result['scope'] = scope
    result['expected_mutation_count'] = len(cases)
    try:
        sql('CREATE DOMAIN ' + prefix + '_D AS BIGINT')
        domain_created = True
        for number, (label, definition, draft, expected) in enumerate(cases):
            table = prefix + '_' + str(number)
            tables.append(table)
            if not label.startswith('table-create'):
                sql('CREATE TABLE ' + table + ' (X INTEGER' + (
                    ', V ' + definition if definition is not None else '') +
                    ')')
            if label == 'comment-clear':
                sql('COMMENT ON COLUMN ' + table + ".V IS 'previous comment'")
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        probe = forms._workspace_probe(browser, ['column', 'table'],
                                       collect_context_commands=False)
        result['context_commands_collected'] = False
        for number, (label, definition, draft, expected) in enumerate(cases):
            table = tables[number]
            print(label, flush=True)
            table_operation = label.startswith('table-')
            creating = label.startswith(('create-', 'table-create'))
            kind = 'table' if table_operation else 'column'
            operation = next(iter(forms._enumerate_operations(
                probe['catalog'], [kind], [
                    'create' if creating else 'comment' if
                    label.startswith('comment-') else 'alter'])))
            target = None if creating else next(
                item for item in probe['resources'] if
                item['resource_kind'] == kind and
                item['display_path'] == ([table] if table_operation else
                                         [table, 'V']))
            assert operation['execution_available'] is True
            forms._open_focused_form(browser, operation, target,
                                     probe['database_target_id'])
            forms._wait_for_operation(wait, operation)
            action_context = None
            if not creating and kind == 'column' and \
                    operation['operation_id'] == 'alter':
                action_context = snapshot(table)['alteration']
                choice = wait.until(lambda driver: visible_named_control(
                    driver, 'Alteration'))
                wait.until(lambda driver: choice.is_enabled() and
                           choice.get_attribute('aria-disabled') != 'true')
                result['pending_action_control'] = {
                    'tag': choice.tag_name,
                    'role': choice.get_attribute('role'),
                    'html': choice.get_attribute('outerHTML')}
                if choice.tag_name.lower() == 'select':
                    shown = choice.find_elements('tag name', 'option')
                    values = [item.get_attribute('value') for item in shown]
                else:
                    click_unobscured(browser, wait, choice)
                    shown = wait.until(lambda driver: [
                        item for item in driver.find_elements(
                            'css selector', '[role="option"]')
                        if item.is_displayed()])
                    values = [item.get_attribute('data-value')
                              for item in shown]
                    click_unobscured(browser, wait, shown[0])
                assert values == action_context['allowed_actions']
                result.pop('pending_action_control', None)
                position = wait.until(lambda driver: visible_named_control(
                    driver, 'Position (one-based)'))
                assert position.get_attribute('value') == str(
                    action_context['position'])
            labels = {item['field_id']: item['label']
                      for item in operation['form']['fields']}
            if creating and table_operation:
                draft = {'name': table, **draft}
            elif creating:
                draft = {'table': table, 'name': 'V', **draft}
            active_fields = {item['field_id'] for item in
                             operation['form']['fields'] if
                             ProviderVisualAdministration._field_active(
                                 item, draft)}
            primitives = {labels[key]: value for key, value in draft.items()
                          if key in active_fields and
                          not isinstance(value, list)}
            fill_fields(wait, [f'{key}={value}'
                               for key, value in primitives.items()])
            if table_operation:
                fill_form_values(browser, wait, operation['form']['fields'],
                                 {labels[key]: value for key, value in
                                  draft.items() if isinstance(value, list)})
            for field_name in (() if table_operation else
                               ('dimensions', 'constraints')):
                field = next((item for item in operation['form']['fields']
                              if item['field_id'] == field_name), None)
                records = draft.get(field_name, [])
                for number_in_list, record in enumerate(records):
                    button = visible_named_control(
                        browser, 'Add ' + field['label'] + ' item')
                    click_unobscured(browser, wait, button)
                    group = browser.find_element(
                        'css selector', '[role="group"][aria-label="' +
                        field['label'] + '"]')
                    boxes = group.find_elements('xpath', './div')
                    record_box = boxes[number_in_list]
                    children = {item['field_id']: item['label'] for item in
                                field['array_editor']['fields']}
                    values = dict(record)
                    for key in ('name', 'index_name'):
                        if values.get(key):
                            values[key] = table + '_' + values[key]
                    fill_fields(wait, [f'{children[key]}={value}'
                                       for key, value in values.items()],
                                control_root=record_box)
            plan = plan_preview(browser, wait, operation, {})
            result['pending_case'] = {
                'case': label, 'statements': plan['command_preview'][
                    'statements']}
            geometry = browser.execute_script("""
              const section = [...document.querySelectorAll(
                'section[aria-label="Engine task form"]')].find(
                  element => element.getClientRects().length);
              if (!section) return null;
              return {width: section.clientWidth,
                scrollWidth: section.scrollWidth,
                parentWidth: section.parentElement.clientWidth,
                parentScrollWidth: section.parentElement.scrollWidth,
                helpers: [...section.querySelectorAll(
                  '.MuiFormHelperText-root')]
                  .filter(element => element.getClientRects().length)
                  .map(element => ({width: element.clientWidth,
                    scrollWidth: element.scrollWidth,
                    whiteSpace: getComputedStyle(element).whiteSpace}))};
            """)
            assert geometry and geometry['width'] > 0
            assert geometry['scrollWidth'] <= geometry['width'] + 1, geometry
            assert geometry['width'] <= geometry['parentWidth'] + 1, geometry
            assert (geometry['parentScrollWidth'] <=
                    geometry['parentWidth'] + 1), geometry
            assert all(item['scrollWidth'] <= item['width'] + 1
                       for item in geometry['helpers']), geometry
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
            if table_operation:
                resources = _resources(native, {'route': route})
                native.commit()
                table_metadata = next(item['native'] for item in resources if
                                      item['resource_kind'] == 'table' and
                                      item['display_name'] == table)
                observed = {**table_metadata,
                            'column_names': [item['name'] for item in
                                             table_metadata['columns']],
                            'native': table_metadata}
            else:
                observed = snapshot(table)
            for key, value in expected.items():
                assert observed[key] == value, (key, observed.get(key), value)
            path = options.output_root / f'{number:02d}-{label}.png'
            result['checks'].append({
                'case': label, 'statements': plan['command_preview'][
                    'statements'], 'native_postcondition': observed,
                'form_geometry': geometry,
                'verified_action_context': action_context,
                'screenshot': str(path), 'sha256': screenshot(browser, path)})
            result.pop('pending_case', None)
            close_workspace(browser, wait)
        result['passed'] = len(result['checks']) == len(cases)
    except Exception:
        result['failures'].append({'traceback': traceback.format_exc()})
        if browser is not None:
            try:
                path = options.output_root / 'failure.png'
                result['failure_screenshot'] = str(path)
                screenshot(browser, path)
                result['failure_geometry'] = browser.execute_script("""
                  const button = [...document.querySelectorAll('button')].find(
                    element =>
                      element.textContent === 'Validate and preview' &&
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
            except Exception as error:
                result['diagnostic_error_type'] = type(error).__name__
    finally:
        cleanup = cleanup_column_fixtures(
            browser, native, tables, prefix + '_D' if domain_created else None)
        result['fixtures_removed'] = cleanup['fixtures_removed']
        result['failures'].extend(cleanup['errors'])
        result['passed'] = result['passed'] and not cleanup['errors']
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
