#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Exercise MySQL Shell 9.7 backup and restore through the actual UI."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import mysql.connector
from selenium.webdriver.common.by import By
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
REFERENCE_VERSION = '9.7.0'
CASES = (
    {
        'case_id': 'backup_logical', 'operation_id': 'backup_logical',
        'values': {
            'path': 'qualification-dump', 'consistent': True,
            'checksum': True, 'threads': 2, 'compression': 'zstd',
            'show_progress': False,
        },
    },
    {
        'case_id': 'restore_logical', 'operation_id': 'restore_logical',
        'values': {
            'path': 'qualification-dump',
            'drop_existing_objects': True, 'reset_progress': True,
            'enable_local_infile': True,
            'threads': 2, 'background_threads': 2,
            'show_progress': False,
        },
    },
)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.set_defaults(engine='MySQL')
    parser.add_argument('--url', required=True)
    parser.add_argument('--server', default='localhost')
    parser.add_argument('--database', required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--endpoint-password-env', required=True)
    parser.add_argument('--tool-workspace', type=Path, required=True)
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
        for item in catalog['operation_profiles']['mysql_database']
        if item['operation_id'] in {'backup_logical', 'restore_logical'}
    }
    if set(operations) != {'backup_logical', 'restore_logical'}:
        raise RuntimeError('MySQL Shell form catalog is incomplete')
    return operations


def _password(options):
    value = os.environ.get(options.endpoint_password_env)
    if value is None:
        raise RuntimeError('endpoint password environment is unavailable')
    return value


def _native_value(options, password):
    connection = mysql.connector.connect(
        host=options.host, port=options.port, user=options.user,
        password=password, database=options.database,
        connection_timeout=10, use_pure=True,
    )
    try:
        cursor = connection.cursor()
        cursor.execute('SELECT VERSION()')
        version = str(cursor.fetchone()[0]).split('-', 1)[0]
        cursor.execute('SELECT @@GLOBAL.local_infile')
        local_infile = bool(cursor.fetchone()[0])
        cursor.execute('SELECT value FROM qualification WHERE id = 1')
        value = str(cursor.fetchone()[0])
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.views "
            "WHERE table_schema = %s AND table_name = "
            "'qualification_view'", (options.database,),
        )
        view_count = int(cursor.fetchone()[0])
        cursor.close()
    finally:
        connection.close()
    return {
        'runtime_version': version, 'qualification_value': value,
        'qualification_view_count': view_count,
        'global_local_infile': local_infile,
    }


def _mutate_after_backup(options, password):
    connection = mysql.connector.connect(
        host=options.host, port=options.port, user=options.user,
        password=password, database=options.database,
        connection_timeout=10, use_pure=True,
    )
    try:
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE qualification SET value = "
            "'mutated-after-shell-backup' WHERE id = 1"
        )
        connection.commit()
        cursor.close()
    finally:
        connection.close()


def _open_form(driver, wait, options, action, operation, password):
    database = wait_for_tree_item(wait, options.database)
    group = (
        'Backup' if operation['operation_id'] == 'backup_logical'
        else 'Restore'
    )
    invoke_context_action(
        wait, driver, database,
        [group, action['label']],
        password,
    )
    if operation['operation_id'] == 'restore_logical':
        continue_button = wait.until(
            lambda current: visible_named_control(current, 'Continue')
        )
        continue_button.click()
    wait.until(
        lambda current: visible_named_control(current, 'Validate and preview')
    )


def _fill(driver, wait, operation, values, database):
    fields = {item['field_id']: item for item in operation['form']['fields']}
    assignments = []
    for field_id, value in values.items():
        field = fields[field_id]
        if field['control'] == 'boolean':
            control = wait.until(
                lambda current, label=field['label']:
                visible_named_control(current, label)
            )
            checked = control.is_selected() or (
                control.get_attribute('aria-checked') == 'true'
            )
            if checked != value:
                driver.execute_script('arguments[0].click()', control)
            continue
        if field['control'] == 'select':
            value = next(
                option['label'] for option in field['options']
                if option['value'] == value
            )
        assignments.append(f'{field["label"]}={value}')
    if operation['operation_id'] == 'restore_logical':
        assignments.append(
            'Type the exact target database name to confirm restore=' +
            database
        )
    fill_fields(wait, assignments)


def _preview(wait, case):
    button = wait.until(
        lambda current: visible_named_control(current, 'Validate and preview')
    )
    wait.until(lambda _current: button.is_enabled())
    button.click()
    rendered = wait.until(expected.visibility_of_element_located((
        By.CSS_SELECTOR, '[aria-label="Provider plan preview"]',
    )))
    plan = json.loads(rendered.text)
    preview = plan.get('command_preview', {})
    if plan.get('state') != 'ready' or not plan.get('execution_available'):
        raise RuntimeError(f'{case["case_id"]} plan is unavailable')
    if preview.get('driver_operation') != 'mysql-shell' or preview.get(
            'statements'):
        raise RuntimeError('MySQL Shell plan crossed the SQL execution path')
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
    observation = result.get('driver_observation', {})
    if result.get('accepted') is not True or not observation.get(
            'mysql_shell_completed'):
        raise RuntimeError(f'{case["case_id"]} did not complete')
    if observation.get('return_code') != 0 or not observation.get(
            'artifact_manifest'):
        raise RuntimeError(f'{case["case_id"]} has no artifact proof')
    if case['operation_id'] == 'restore_logical' and not observation.get(
            'restore_postcondition', {}).get('schema_postcondition_passed'):
        raise RuntimeError('restore lacked its provider schema postcondition')
    return result, observation


def _validation(driver, wait, operation, database):
    if operation['operation_id'] == 'restore_logical':
        fill_fields(wait, [
            'Completed dump directory below the endpoint tool workspace='
            'qualification-dump',
            'Type the exact target database name to confirm restore=wrong',
        ])
    button = wait.until(
        lambda current: visible_named_control(current, 'Validate and preview')
    )
    if button.is_enabled():
        button.click()
        wait.until(lambda current: any(
            item.is_displayed() and item.text.strip()
            for item in current.find_elements(
                By.CSS_SELECTOR, '[role="alert"]'
            ) if 'Provider workspace:' not in item.text
        ))
    if driver.find_elements(
            By.CSS_SELECTOR, '[aria-label="Provider plan preview"]'):
        raise RuntimeError('invalid MySQL Shell form produced a plan')
    return {
        'state': 'observed',
        'field_id': (
            'path' if operation['operation_id'] == 'backup_logical'
            else 'confirmation'
        ),
        'plan_present': False, 'provider_operation_executed': False,
        'target_database': database,
    }


def _run_case(driver, wait, options, actions, operation, case, password):
    command_id = f'database.mysql.{case["operation_id"]}'
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
    wait.until(
        lambda current: visible_named_control(current, 'Validate and preview')
    )
    validation = _validation(driver, wait, operation, options.database)
    validation_path = directory / f'validation-error-{suffix}.png'
    validation_hash = screenshot(driver, validation_path)
    close_workspace(driver, wait)

    _open_form(driver, wait, options, action, operation, password)
    wait.until(
        lambda current: visible_named_control(current, 'Validate and preview')
    )
    _fill(driver, wait, operation, case['values'], options.database)
    plan = _preview(wait, case)
    preview_layout = layout_observation(driver)
    preview = directory / f'plan-preview-{suffix}.png'
    preview_hash = screenshot(driver, preview)
    result, observation = _apply(driver, wait, case)
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
        'plan_state': plan['state'],
        'completion': {
            'accepted': result['accepted'],
            'mysql_shell_completed': True,
            'artifact_file_count': observation['artifact_file_count'],
            'artifact_total_bytes': observation['artifact_total_bytes'],
            'executable_sha256': observation['executable_sha256'],
            'schema_postcondition_passed': observation.get(
                'restore_postcondition', {}
            ).get('schema_postcondition_passed'),
            'provider_finality_authority': result.get(
                'transaction_finality_interpreted_by_common_code'
            ) is False,
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
    if mysql.connector.__version__ != '26.7.0':
        raise RuntimeError(
            'MySQL backup UI gate requires mysql-connector-python 26.7.0'
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
        native_states['before_backup'] = _native_value(options, password)
        if native_states['before_backup']['runtime_version'] != (
                REFERENCE_VERSION):
            raise RuntimeError('backup gate requires MySQL 9.7.0')
        backup = CASES[0]
        completed.append(_run_case(
            driver, wait, options, actions,
            operations[backup['operation_id']], backup, password,
        ))
        _mutate_after_backup(options, password)
        native_states['after_mutation'] = _native_value(options, password)
        restore = CASES[1]
        completed.append(_run_case(
            driver, wait, options, actions,
            operations[restore['operation_id']], restore, password,
        ))
        native_states['after_restore'] = _native_value(options, password)
        if native_states['after_mutation']['qualification_value'] != (
                'mutated-after-shell-backup'):
            raise RuntimeError('post-backup mutation was not observed')
        if native_states['after_restore']['qualification_value'] != (
                native_states['before_backup']['qualification_value']):
            raise RuntimeError('restore did not recover the backed-up row')
        if native_states['after_restore']['qualification_view_count'] != 1:
            raise RuntimeError('restore did not recover the backed-up view')
        if native_states['after_restore']['global_local_infile'] is not False:
            raise RuntimeError('restore did not reinstate local_infile state')
        return {
            'schema': 'cdeadmin.mysql-backup-restore-ui-gate.v1',
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'engine_id': 'mysql', 'interface_id': 'mysql-native',
            'reference_version': REFERENCE_VERSION,
            'server_label': options.server,
            'database_label': options.database,
            'tool_workspace': str(options.tool_workspace),
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
