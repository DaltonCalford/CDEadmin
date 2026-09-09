#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Apply every provider-owned MariaDB 12.2 database-maintenance form."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import mariadb
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as expected
from selenium.webdriver.support.ui import WebDriverWait


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cdeadmin_firebird_ui_form_gate import (  # noqa: E402
    accessibility_observation,
    assert_form_controls,
    cancellation_observation,
    close_workspace,
    close_workspace_with_keyboard,
    create_driver,
    evidence_variant,
    layout_observation,
    prepare_tree,
    screenshot,
)
from tools.cdeadmin_ui_evidence import (  # noqa: E402
    _control_evidence,
    fill_fields,
    invoke_context_action,
    visible_named_control,
    wait_for_tree_item,
)


CATALOG_PATH = (
    ROOT / 'web/pgadmin/cdeadmin/visual_admin/portfolio_catalog.json'
)
REFERENCE_VERSION = '12.2.2'
CASES = (
    {
        'case_id': 'analyze_persistent_all',
        'operation_id': 'analyze_tables',
        'values': {
            'tables': ['qualification'], 'binlog_mode': 'LOCAL',
            'persistent_for': 'ALL', 'persistent_columns': [],
            'persistent_indexes': [],
        },
        'expected': (
            'ANALYZE LOCAL TABLE `cdeadmin_demo`.`qualification` '
            'PERSISTENT FOR ALL'
        ),
    },
    {
        'case_id': 'analyze_persistent_specified',
        'operation_id': 'analyze_tables',
        'values': {
            'tables': ['qualification'], 'binlog_mode': 'DEFAULT',
            'persistent_for': 'SPECIFIED',
            'persistent_columns': ['value'],
            'persistent_indexes': ['PRIMARY'],
        },
        'expected': (
            'ANALYZE TABLE `cdeadmin_demo`.`qualification` PERSISTENT FOR '
            'COLUMNS (`value`) INDEXES (PRIMARY)'
        ),
    },
    {
        'case_id': 'check_view_for_upgrade',
        'operation_id': 'check_objects',
        'values': {
            'object_type': 'VIEW',
            'objects': ['cde_qualification_view'],
            'check_options': ['FOR UPGRADE'],
        },
        'expected': (
            'CHECK VIEW `cdeadmin_demo`.`cde_qualification_view` '
            'FOR UPGRADE'
        ),
    },
    {
        'case_id': 'optimize_nowait',
        'operation_id': 'optimize_tables',
        'values': {
            'tables': ['qualification'], 'binlog_mode': 'LOCAL',
            'lock_wait_mode': 'NOWAIT', 'lock_wait_seconds': 0,
        },
        'expected': (
            'OPTIMIZE LOCAL TABLE `cdeadmin_demo`.`qualification` NOWAIT'
        ),
    },
    {
        'case_id': 'repair_myisam',
        'operation_id': 'repair_objects',
        'values': {
            'object_type': 'TABLE', 'objects': ['cde_repair_probe'],
            'binlog_mode': 'LOCAL', 'repair_options': ['QUICK'],
        },
        'expected': (
            'REPAIR LOCAL TABLE `cdeadmin_demo`.`cde_repair_probe` QUICK'
        ),
    },
    {
        'case_id': 'checksum_quick',
        'operation_id': 'checksum_tables',
        'values': {
            'tables': ['qualification'], 'checksum_type': 'QUICK',
        },
        'expected': (
            'CHECKSUM TABLE `cdeadmin_demo`.`qualification` QUICK'
        ),
    },
)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.set_defaults(engine='MariaDB')
    parser.add_argument('--server', default='localhost')
    parser.add_argument('--database', required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--endpoint-password-env', required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--summary-output', type=Path, required=True)
    parser.add_argument('--width', type=int, default=1600)
    parser.add_argument('--height', type=int, default=1000)
    parser.add_argument(
        '--theme', choices=('default', 'high-contrast'), default='default',
    )
    parser.add_argument(
        '--font-scale', type=int, choices=(100, 150, 200, 300), default=100,
    )
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--browser-binary')
    return parser.parse_args(argv)


def _operations():
    catalog = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    forms = catalog['forms']
    operations = {
        item['operation_id']: {**item, 'form': forms[item['form_id']]}
        for item in catalog['operation_profiles']['mariadb_database']
    }
    expected_ids = {case['operation_id'] for case in CASES}
    missing = expected_ids.difference(operations)
    if missing:
        raise RuntimeError(
            'MariaDB maintenance catalog is incomplete: ' +
            ', '.join(sorted(missing))
        )
    return operations


def _password(options):
    value = os.environ.get(options.endpoint_password_env)
    if value is None:
        raise RuntimeError('endpoint password environment is unavailable')
    return value


def _connect(options, password):
    return mariadb.connect(
        host=options.host, port=options.port, user=options.user,
        password=password, database=options.database, connect_timeout=10,
    )


def _native_probe(options, password):
    connection = _connect(options, password)
    try:
        cursor = connection.cursor()
        cursor.execute('SELECT VERSION()')
        version = str(cursor.fetchone()[0]).split('-', 1)[0]
        cursor.execute(
            'SELECT COUNT(*) FROM mysql.table_stats '
            'WHERE db_name = ? AND table_name = ?',
            (options.database, 'qualification'),
        )
        table_stats = int(cursor.fetchone()[0])
        cursor.execute(
            'SELECT COUNT(*) FROM mysql.column_stats '
            'WHERE db_name = ? AND table_name = ? AND column_name = ?',
            (options.database, 'qualification', 'value'),
        )
        value_stats = int(cursor.fetchone()[0])
        cursor.execute(
            'CHECKSUM TABLE `cdeadmin_demo`.`qualification` QUICK'
        )
        checksum = cursor.fetchone()[1]
        cursor.close()
    finally:
        connection.close()
    return {
        'runtime_version': version,
        'persistent_table_stats_count': table_stats,
        'persistent_value_column_stats_count': value_stats,
        'qualification_checksum_is_integer': isinstance(checksum, int),
    }


def _open_form(driver, wait, options, action, operation, password):
    database = wait_for_tree_item(wait, options.database)
    invoke_context_action(
        wait, driver, database,
        ['Maintenance', action['label']], password,
    )
    try:
        wait.until(
            lambda current: visible_named_control(
                current, 'Validate and preview'
            )
        )
    except TimeoutException:
        state = driver.execute_script(
            """
            const tree = window.pgAdmin?.Browser?.tree;
            let item = tree?.selected?.();
            const ancestors = [];
            while (item) {
              const data = tree.itemData(item);
              ancestors.push({
                type: data?._type || null,
                label: data?.label || data?._label || null,
              });
              item = tree.hasParent(item) ? tree.parent(item) : null;
            }
            return {
              invocations: window.__cdeadminQaWorkspaceInvocations || [],
              ancestors,
              dialogs: [...document.querySelectorAll('[role="dialog"]')]
                .filter(item => item.offsetParent !== null)
                .map(item => item.innerText.slice(0, 1000)),
              alerts: [...document.querySelectorAll('[role="alert"]')]
                .filter(item => item.offsetParent !== null)
                .map(item => item.innerText.slice(0, 1000)),
            };
            """
        )
        try:
            state['browser_console'] = driver.get_log('browser')
        except Exception:
            state['browser_console'] = []
        debug_path = options.output_root / 'dispatch-failure.png'
        screenshot(driver, debug_path)
        raise RuntimeError(
            'MariaDB workspace action did not render its form: ' +
            json.dumps(state, sort_keys=True)
        ) from None


def _set_boolean(driver, wait, label, selected):
    control = wait.until(lambda current: visible_named_control(
        current, label
    ))
    checked = (
        control.is_selected() or
        control.get_attribute('aria-checked') == 'true'
    )
    if checked != selected:
        driver.execute_script('arguments[0].click()', control)


def _set_multiselect(driver, wait, field, values):
    control = wait.until(
        lambda current: visible_named_control(current, field['label'])
    )
    labels = {
        option['value']: option['label']
        for option in field.get('options', [])
    }
    control.click()
    for value in values:
        label = labels[value]

        def option(current, expected_label=label):
            return next((item for item in current.find_elements(
                By.CSS_SELECTOR, '[role="option"]'
            ) if item.is_displayed() and item.text.strip() == expected_label),
                        None)

        wait.until(option).click()
    ActionChains(driver).send_keys(Keys.ESCAPE).perform()


def _fill_case(driver, wait, operation, values):
    fields = {
        field['field_id']: field
        for field in operation['form']['fields']
    }
    assignments = []
    for field_id, value in values.items():
        field = fields[field_id]
        if field['control'] == 'boolean':
            _set_boolean(driver, wait, field['label'], bool(value))
        elif field['control'] == 'multiselect':
            _set_multiselect(driver, wait, field, value)
        else:
            if field['control'] == 'select':
                value = next(
                    option['label'] for option in field.get('options', [])
                    if option['value'] == value
                )
            rendered = json.dumps(value) if isinstance(
                value, (list, dict)
            ) else str(value)
            assignments.append(f'{field["label"]}={rendered}')
    fill_fields(wait, assignments)


def _preview(driver, wait, case):
    button = wait.until(
        lambda current: visible_named_control(current, 'Validate and preview')
    )
    wait.until(lambda _current: button.is_enabled())
    button.click()

    def outcome(current):
        plans = [item for item in current.find_elements(
            By.CSS_SELECTOR, '[aria-label="Provider plan preview"]'
        ) if item.is_displayed()]
        if plans:
            return ('plan', plans[-1])
        errors = [item.text.strip() for item in current.find_elements(
            By.CSS_SELECTOR, '[role="alert"]'
        ) if item.is_displayed() and item.text.strip() and
            'Provider workspace:' not in item.text]
        return ('error', errors) if errors else None

    outcome_type, payload = wait.until(outcome)
    if outcome_type == 'error':
        raise RuntimeError(
            f'{case["case_id"]} validation failed: ' + ' '.join(payload)
        )
    plan = json.loads(payload.text)
    statements = plan.get('command_preview', {}).get('statements', [])
    observed = statements[0].get('source') if statements else None
    if plan.get('state') != 'ready' or not plan.get('execution_available'):
        raise RuntimeError(f'{case["case_id"]} plan was not executable')
    if observed != case['expected']:
        raise RuntimeError(
            f'{case["case_id"]} compiled {observed!r}; '
            f'expected {case["expected"]!r}'
        )
    return plan


def _apply(driver, wait, case):
    confirmation = visible_named_control(
        driver, 'I confirm this provider-planned operation.'
    )
    if confirmation is not None:
        driver.execute_script('arguments[0].click()', confirmation)
    button = wait.until(
        lambda current: visible_named_control(current, 'Apply provider plan')
    )
    wait.until(lambda _current: button.is_enabled())
    button.click()
    rendered = wait.until(expected.visibility_of_element_located((
        By.CSS_SELECTOR, '[aria-label="Provider operation result"]',
    )))
    result = json.loads(rendered.text)
    rows = (result.get('statement_results') or [{}])[0].get('rows') or []
    if result.get('accepted') is not True or not rows:
        raise RuntimeError(
            f'{case["case_id"]} lacked a native MariaDB result row'
        )
    return result, rows


def _validation_observation(driver, wait, operation):
    field_id = (
        'objects' if operation['operation_id'] in {
            'check_objects', 'repair_objects'} else 'tables'
    )
    field = next(item for item in operation['form']['fields']
                 if item['field_id'] == field_id)
    wait.until(lambda current: visible_named_control(current, field['label']))
    button = wait.until(
        lambda current: visible_named_control(current, 'Validate and preview')
    )
    if button.is_enabled():
        button.click()
        wait.until(lambda current: any(
            item.is_displayed() and
            'MariaDB maintenance targets must be a JSON array' in item.text
            for item in current.find_elements(
                By.CSS_SELECTOR, '[role="alert"]'
            )
        ))
    if driver.find_elements(
            By.CSS_SELECTOR, '[aria-label="Provider plan preview"]'):
        raise RuntimeError('empty MariaDB target list produced a plan')
    return {
        'state': 'observed', 'field_id': field_id,
        'plan_present': False, 'provider_operation_executed': False,
    }


def _run_case(driver, wait, options, actions, operations, case, password):
    operation = operations[case['operation_id']]
    command_id = f'database.mariadb.{case["operation_id"]}'
    action = actions.get(command_id)
    if action is None:
        raise RuntimeError(f'{command_id} is absent from the database popup')
    directory = options.output_root / case['case_id']
    suffix = f'{options.width}x{options.height}-{evidence_variant(options)}'

    _open_form(driver, wait, options, action, operation, password)
    fields = assert_form_controls(wait, operation['form']['fields'])
    initial_layout = layout_observation(driver)
    accessibility = accessibility_observation(
        driver, wait, operation, fields
    )
    initial = directory / f'initial-{suffix}.png'
    initial_hash = screenshot(driver, initial)
    close_workspace_with_keyboard(driver, wait)
    cancellation = cancellation_observation(driver)
    cancelled = directory / f'cancelled-{suffix}.png'
    cancelled_hash = screenshot(driver, cancelled)

    _open_form(driver, wait, options, action, operation, password)
    assert_form_controls(wait, operation['form']['fields'])
    validation = _validation_observation(driver, wait, operation)
    validation_path = directory / f'validation-error-{suffix}.png'
    validation_hash = screenshot(driver, validation_path)
    close_workspace(driver, wait)

    _open_form(driver, wait, options, action, operation, password)
    assert_form_controls(wait, operation['form']['fields'])
    _fill_case(driver, wait, operation, case['values'])
    plan = _preview(driver, wait, case)
    preview_layout = layout_observation(driver)
    preview = directory / f'plan-preview-{suffix}.png'
    preview_hash = screenshot(driver, preview)
    result, rows = _apply(driver, wait, case)
    completed_layout = layout_observation(driver)
    completed = directory / f'completed-{suffix}.png'
    completed_hash = screenshot(driver, completed)
    rendered_controls = _control_evidence(driver)
    close_workspace(driver, wait)
    return {
        'case_id': case['case_id'], 'command_id': command_id,
        'operation_id': case['operation_id'],
        'form_id': operation['form_id'],
        'form_title': operation['form']['title'],
        'observed_fields': fields, 'accessibility': accessibility,
        'cancellation': cancellation, 'validation': validation,
        'rendered_control_count': len(rendered_controls),
        'compiled_source': case['expected'], 'plan_state': plan['state'],
        'completion': {
            'accepted': True, 'native_row_count': len(rows),
            'native_row_width': len(rows[0]) if rows else 0,
            'native_cell_types': sorted({
                type(value).__name__ for row in rows for value in row
            }),
            'provider_finality_authority': (
                result.get('transaction_finality_interpreted_by_common_code')
                is False
            ),
        },
        'layout': {
            'initial': initial_layout, 'plan_preview': preview_layout,
            'completed': completed_layout,
        },
        'screenshots': {
            'initial': {'path': str(initial), 'sha256': initial_hash},
            'cancelled': {'path': str(cancelled), 'sha256': cancelled_hash},
            'validation_error': {
                'path': str(validation_path), 'sha256': validation_hash,
            },
            'plan_preview': {'path': str(preview), 'sha256': preview_hash},
            'completed': {'path': str(completed), 'sha256': completed_hash},
        },
    }


def run(options):
    if mariadb.__version__ != '1.1.14':
        raise RuntimeError(
            'MariaDB maintenance UI gate requires mariadb 1.1.14'
        )
    password = _password(options)
    driver = create_driver(options)
    wait = WebDriverWait(driver, options.timeout)
    operations = _operations()
    completed = []
    native_states = {}
    try:
        action_list = prepare_tree(driver, wait, options, password)
        actions = {item['command_id']: item for item in action_list}
        native_states['initial'] = _native_probe(options, password)
        if native_states['initial']['runtime_version'] != REFERENCE_VERSION:
            raise RuntimeError('maintenance gate requires MariaDB 12.2.2')
        for case in CASES:
            completed.append(_run_case(
                driver, wait, options, actions, operations, case, password
            ))
            if case['case_id'].startswith('analyze_persistent_'):
                native_states[case['case_id']] = _native_probe(
                    options, password
                )
        native_states['final'] = _native_probe(options, password)
        if native_states['analyze_persistent_all'][
                'persistent_table_stats_count'] < 1:
            raise RuntimeError('persistent table statistics were not observed')
        if native_states['analyze_persistent_specified'][
                'persistent_value_column_stats_count'] < 1:
            raise RuntimeError(
                'persistent column statistics were not observed'
            )
        return {
            'schema': 'cdeadmin.mariadb-maintenance-ui-gate.v1',
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'engine_id': 'mariadb', 'interface_id': 'mariadb-native',
            'reference_version': REFERENCE_VERSION,
            'server_label': options.server,
            'database_label': options.database,
            'viewport': f'{options.width}x{options.height}',
            'theme': options.theme, 'font_scale': options.font_scale,
            'evidence_variant': evidence_variant(options),
            'expected_case_ids': [case['case_id'] for case in CASES],
            'completed_case_ids': [case['case_id'] for case in completed],
            'forms': completed, 'native_states': native_states,
            'cancelled_form_count': sum(
                item['cancellation']['dialog_dismissed']
                for item in completed
            ),
            'validation_observed_form_count': len(completed),
            'accessibility_observed_form_count': sum(
                item['accessibility']['state'] == 'observed'
                for item in completed
            ),
            'provider_owned_forms_only': True,
            'common_dialect_inference': False,
            'credential_values_exported': False,
            'passed': True,
        }
    finally:
        driver.quit()


def main(argv=None):
    options = arguments(argv)
    try:
        evidence = run(options)
    except Exception as exc:
        options.output_root.mkdir(parents=True, exist_ok=True)
        options.summary_output.unlink(missing_ok=True)
        print(json.dumps({
            'passed': False, 'error_type': type(exc).__name__,
            'message': str(exc),
        }, indent=2, sort_keys=True))
        traceback.print_exc()
        return 1
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps({
        'engine_id': evidence['engine_id'],
        'case_count': len(evidence['forms']), 'passed': evidence['passed'],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
