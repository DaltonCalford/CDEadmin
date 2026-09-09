#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Apply native SQLite or DuckDB database-maintenance forms in a browser."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from selenium.webdriver.common.by import By
from selenium.webdriver import ActionChains
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
MENU_GROUP_LABELS = {
    'backup': 'Backup',
    'restore': 'Restore',
    'diagnostics': 'Diagnostics and verification',
    'maintenance': 'Maintenance',
}
OPERATION_ORDER = (
    'backup', 'restore', 'integrity_check', 'quick_check',
    'foreign_key_check', 'vacuum', 'incremental_vacuum', 'optimize',
    'analyze', 'reindex', 'wal_checkpoint',
)
DUCKDB_OPERATION_ORDER = (
    'checkpoint', 'force_checkpoint', 'vacuum', 'analyze',
    'export_database', 'import_database',
)
EXPECTED_SQL = {
    'integrity_check': 'PRAGMA integrity_check(100)',
    'quick_check': 'PRAGMA quick_check(100)',
    'foreign_key_check': 'PRAGMA foreign_key_check',
    'vacuum': 'VACUUM',
    'incremental_vacuum': 'PRAGMA incremental_vacuum(0)',
    'optimize': 'PRAGMA optimize',
    'analyze': 'ANALYZE',
    'reindex': 'REINDEX',
    'wal_checkpoint': 'PRAGMA wal_checkpoint(PASSIVE)',
}


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--engine', default='SQLite')
    parser.add_argument('--server', default='localhost')
    parser.add_argument('--database', required=True)
    parser.add_argument('--database-path', type=Path, required=True)
    parser.add_argument('--backup-path', type=Path, required=True)
    parser.add_argument('--export-path', type=Path)
    parser.add_argument('--import-path', type=Path)
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
    parser.add_argument('--timeout', type=int, default=90)
    parser.add_argument('--browser-binary')
    return parser.parse_args(argv)


def maintenance_forms(engine):
    catalog = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    forms = catalog['forms']
    profile = 'sqlite_database' if engine == 'SQLite' else 'duckdb_database'
    operation_order = (
        OPERATION_ORDER if engine == 'SQLite' else DUCKDB_OPERATION_ORDER
    )
    operations = {
        item['operation_id']: {**item, 'form': forms[item['form_id']]}
        for item in catalog['operation_profiles'][profile]
        if item['operation_id'] in operation_order
    }
    missing = set(operation_order).difference(operations)
    if missing:
        raise RuntimeError(
            f'{engine} maintenance catalog is incomplete: ' +
            ', '.join(sorted(missing))
        )
    return [operations[item] for item in operation_order]


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _native_probe(database, engine):
    if engine == 'DuckDB':
        with duckdb.connect(str(database), read_only=True) as connection:
            runtime = connection.execute('SELECT version()').fetchone()[0]
            imported = connection.execute(
                "SELECT count(*) FROM information_schema.tables WHERE "
                "table_schema = 'main' AND table_name = 'import_probe'"
            ).fetchone()[0]
            value = None
            if imported:
                value = connection.execute(
                    'SELECT value FROM import_probe WHERE id = 1'
                ).fetchone()[0]
        return {
            'runtime_version': runtime,
            'import_probe': value,
            'database_file_present': database.is_file(),
        }
    with sqlite3.connect(database) as connection:
        quick_check = connection.execute(
            'PRAGMA quick_check(1)'
        ).fetchone()
        restore_value = connection.execute(
            'SELECT value FROM restore_probe WHERE id = 1'
        ).fetchone()
        statistics = connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name = 'sqlite_stat1'"
        ).fetchone()
    return {
        'quick_check': quick_check[0] if quick_check else None,
        'restore_probe': restore_value[0] if restore_value else None,
        'statistics_catalog_present': bool(statistics and statistics[0]),
    }


def _open_form(driver, wait, options, action, operation):
    database = wait_for_tree_item(wait, options.database)
    invoke_context_action(
        wait, driver, database,
        [MENU_GROUP_LABELS[action['menu_group']], action['label']],
        '', endpoint_prompt_timeout=1,
    )
    if action['requires_confirmation']:
        continue_button = wait.until(
            lambda value: visible_named_control(value, 'Continue')
        )
        wait.until(lambda _value: continue_button.is_enabled())
        continue_button.click()
    wait.until(lambda value: operation['title'] in value.find_element(
        By.TAG_NAME, 'body'
    ).text)


def _fill_operation(wait, options, operation_id):
    values = {}
    if operation_id in {'backup', 'restore'}:
        values['Absolute backup database filename'] = str(
            options.backup_path
        )
    if operation_id == 'restore':
        values[
            'Type the active database path to confirm replacement'
        ] = str(options.database_path)
    if operation_id == 'export_database':
        values['Absolute empty export directory'] = str(
            options.export_path
        )
    if operation_id == 'import_database':
        values['Absolute DuckDB export directory'] = str(
            options.import_path
        )
    fill_fields(wait, [
        f'{label}={value}' for label, value in values.items()
    ])


def _preview(driver, wait, operation, options):
    button = wait.until(
        lambda value: visible_named_control(value, 'Validate and preview')
    )
    wait.until(lambda _value: button.is_enabled())
    button.click()
    preview = wait.until(expected.visibility_of_element_located((
        By.CSS_SELECTOR, '[aria-label="Provider plan preview"]',
    )))
    plan = json.loads(preview.text)
    if plan.get('state') != 'ready' or not plan.get('execution_available'):
        raise RuntimeError(
            f'{operation["operation_id"]} did not produce an executable plan'
        )
    expected_source = EXPECTED_SQL.get(operation['operation_id'])
    if options.engine == 'DuckDB':
        database_name = options.database.rsplit('.', 1)[0]
        expected_source = {
            'checkpoint': f'CHECKPOINT "{database_name}"',
            'force_checkpoint': f'FORCE CHECKPOINT "{database_name}"',
            'vacuum': 'VACUUM',
            'analyze': 'ANALYZE',
            'export_database': (
                f"EXPORT DATABASE '{options.export_path}' (FORMAT PARQUET)"
            ),
            'import_database': f"IMPORT DATABASE '{options.import_path}'",
        }[operation['operation_id']]
    if expected_source is not None:
        statements = plan.get('command_preview', {}).get('statements', [])
        observed = statements[0].get('source') if statements else None
        if observed != expected_source:
            raise RuntimeError(
                f'{operation["operation_id"]} compiled {observed!r}; '
                f'expected {expected_source!r}'
            )
    return plan


def _apply(driver, wait, operation):
    confirmation = visible_named_control(
        driver, 'I confirm this provider-planned operation.'
    )
    if confirmation is not None:
        driver.execute_script('arguments[0].click()', confirmation)
    button = wait.until(
        lambda value: visible_named_control(value, 'Apply provider plan')
    )
    wait.until(lambda _value: button.is_enabled())
    button.click()
    rendered = wait.until(expected.visibility_of_element_located((
        By.CSS_SELECTOR, '[aria-label="Provider operation result"]',
    )))
    result = json.loads(rendered.text)
    if result.get('accepted') is not True:
        raise RuntimeError(
            f'{operation["operation_id"]} was not accepted by the provider'
        )
    operation_id = operation['operation_id']
    if operation_id in {'backup', 'restore'}:
        observation = result.get('driver_observation', {})
        expected_operation = f'sqlite-online-{operation_id}'
        if observation.get('operation') != expected_operation or (
                observation.get('quick_check') != 'ok'):
            raise RuntimeError(
                f'{operation_id} lacked its native SQLite completion proof'
            )
    elif not isinstance(result.get('statement_results'), list) or len(
            result['statement_results']) != 1:
        raise RuntimeError(
            f'{operation_id} lacked its native statement result'
        )
    return result


def _value_free_result(operation_id, result):
    if operation_id in {'backup', 'restore'}:
        observation = result['driver_observation']
        return {
            'accepted': True,
            'native_operation': observation['operation'],
            'quick_check': observation['quick_check'],
            'driver_returned': observation.get('driver_returned') is True,
        }
    statement = result['statement_results'][0]
    rows = statement.get('rows') or []
    return {
        'accepted': True,
        'statement_result_count': 1,
        'returned_row_count': len(rows),
        'commit_requested': result.get('commit_requested') is True,
        'rollback_requested': result.get('rollback_requested') is True,
        'provider_finality_authority': (
            result.get('transaction_finality_interpreted_by_common_code')
            is False
        ),
    }


def _validation_observation(driver, wait, operation):
    required = next((
        field for field in operation['form']['fields']
        if field.get('required') and field.get('control') in {
            'text', 'code', 'json', 'number',
        }
    ), None)
    if required is None:
        return {
            'state': 'not_applicable',
            'reason': 'form has no clearable required field',
        }
    control = wait.until(lambda value: visible_named_control(
        value, required['label']
    ))
    control.click()
    ActionChains(driver).key_down(Keys.CONTROL).send_keys(
        'a'
    ).key_up(Keys.CONTROL).send_keys(Keys.BACKSPACE).perform()
    button = wait.until(lambda value: visible_named_control(
        value, 'Validate and preview'
    ))
    if button.is_enabled():
        button.click()
        wait.until(lambda value: any(
            item.is_displayed()
            for item in value.find_elements(By.CSS_SELECTOR, '[role="alert"]')
        ))
    if driver.find_elements(
            By.CSS_SELECTOR, '[aria-label="Provider plan preview"]'):
        raise RuntimeError(
            f'{operation["operation_id"]} planned empty required input'
        )
    apply_button = visible_named_control(driver, 'Apply provider plan')
    if apply_button is not None and apply_button.is_enabled():
        raise RuntimeError(
            f'{operation["operation_id"]} enabled Apply for invalid input'
        )
    return {
        'state': 'observed',
        'field_id': required['field_id'],
        'validation_kind': 'required',
        'plan_present': False,
        'apply_enabled': False,
        'provider_operation_executed': False,
    }


def _run_case(driver, wait, options, actions, operation):
    operation_id = operation['operation_id']
    engine_id = options.engine.lower()
    command_id = f'database.{engine_id}.{operation_id}'
    action = actions.get(command_id)
    if action is None:
        raise RuntimeError(f'{command_id} is absent from the database popup')
    if action['arguments'].get('operation_id') != operation_id:
        raise RuntimeError(f'{command_id} routes to the wrong provider task')
    _open_form(driver, wait, options, action, operation)
    fields = assert_form_controls(wait, operation['form']['fields'])
    initial_layout = layout_observation(driver)
    accessibility = accessibility_observation(
        driver, wait, operation, fields
    )
    directory = options.output_root / command_id
    suffix = (
        f'{options.width}x{options.height}-{evidence_variant(options)}'
    )
    initial = directory / f'initial-{suffix}.png'
    initial_hash = screenshot(driver, initial)

    close_workspace_with_keyboard(driver, wait)
    cancellation = cancellation_observation(driver)
    cancelled = directory / f'cancelled-{suffix}.png'
    cancelled_hash = screenshot(driver, cancelled)

    _open_form(driver, wait, options, action, operation)
    assert_form_controls(wait, operation['form']['fields'])
    validation = _validation_observation(driver, wait, operation)
    validation_layout = None
    validation_path = None
    validation_hash = None
    if validation['state'] == 'observed':
        validation_layout = layout_observation(driver)
        validation_path = directory / f'validation-error-{suffix}.png'
        validation_hash = screenshot(driver, validation_path)
    close_workspace(driver, wait)

    _open_form(driver, wait, options, action, operation)
    assert_form_controls(wait, operation['form']['fields'])
    _fill_operation(wait, options, operation_id)
    plan = _preview(driver, wait, operation, options)
    preview_layout = layout_observation(driver)
    preview = directory / f'plan-preview-{suffix}.png'
    preview_hash = screenshot(driver, preview)
    result = _apply(driver, wait, operation)
    completed_layout = layout_observation(driver)
    completed = directory / f'completed-{suffix}.png'
    completed_hash = screenshot(driver, completed)
    rendered_controls = _control_evidence(driver)
    close_workspace(driver, wait)
    evidence = {
        'command_id': command_id,
        'operation_id': operation_id,
        'menu_group': action['menu_group'],
        'menu_label': action['label'],
        'form_id': operation['form_id'],
        'form_title': operation['form']['title'],
        'declared_field_count': len(operation['form']['fields']),
        'observed_fields': fields,
        'accessibility': accessibility,
        'cancellation': cancellation,
        'validation': validation,
        'rendered_control_count': len(rendered_controls),
        'plan_state': plan['state'],
        'execution_available': plan['execution_available'],
        'completion': _value_free_result(operation_id, result),
        'layout': {
            'initial': initial_layout,
            'validation_error': validation_layout,
            'plan_preview': preview_layout,
            'completed': completed_layout,
        },
        'screenshots': {
            'initial': {'path': str(initial), 'sha256': initial_hash},
            'cancelled': {
                'path': str(cancelled), 'sha256': cancelled_hash,
            },
            'plan_preview': {
                'path': str(preview), 'sha256': preview_hash,
            },
            'completed': {
                'path': str(completed), 'sha256': completed_hash,
            },
        },
    }
    if validation_path is not None:
        evidence['screenshots']['validation_error'] = {
            'path': str(validation_path), 'sha256': validation_hash,
        }
    return evidence


def run(options):
    if not options.database_path.is_file():
        raise RuntimeError(
            f'isolated {options.engine} database is missing'
        )
    if options.engine == 'DuckDB':
        if duckdb.__version__ != '1.5.2':
            raise RuntimeError('maintenance gate requires DuckDB 1.5.2')
        if options.export_path is None or options.import_path is None:
            raise RuntimeError(
                'DuckDB maintenance requires export and import paths'
            )
        if not (options.import_path / 'schema.sql').is_file() or not (
                options.import_path / 'load.sql').is_file():
            raise RuntimeError('DuckDB import fixture is incomplete')
        if options.export_path.exists():
            raise RuntimeError('DuckDB export destination must not exist')
    else:
        options.backup_path.unlink(missing_ok=True)
    driver = create_driver(options)
    wait = WebDriverWait(driver, options.timeout)
    forms = maintenance_forms(options.engine)
    completed = []
    native_states = {}
    try:
        action_list = prepare_tree(driver, wait, options, '')
        actions = {item['command_id']: item for item in action_list}
        if options.engine == 'SQLite':
            with sqlite3.connect(options.database_path) as connection:
                connection.execute(
                    'CREATE TABLE IF NOT EXISTS restore_probe('
                    'id INTEGER PRIMARY KEY, value TEXT NOT NULL)'
                )
                connection.execute(
                    'INSERT INTO restore_probe(id, value) '
                    "VALUES(1, 'before-backup') "
                    "ON CONFLICT(id) DO UPDATE SET value = 'before-backup'"
                )
                connection.commit()
        for operation in forms:
            operation_id = operation['operation_id']
            completed.append(_run_case(
                driver, wait, options, actions, operation
            ))
            if options.engine == 'SQLite' and operation_id == 'backup':
                if not options.backup_path.is_file() or (
                        options.backup_path.read_bytes()[:16] !=
                        b'SQLite format 3\x00'):
                    raise RuntimeError(
                        'rendered backup did not create a SQLite database'
                    )
                with sqlite3.connect(options.database_path) as connection:
                    connection.execute(
                        "UPDATE restore_probe SET value = 'after-backup' "
                        'WHERE id = 1'
                    )
                    connection.commit()
                native_states['after_backup_mutation'] = _native_probe(
                    options.database_path, options.engine
                )
            if options.engine == 'SQLite' and operation_id == 'restore':
                native_states['after_restore'] = _native_probe(
                    options.database_path, options.engine
                )
                if native_states['after_restore']['restore_probe'] != (
                        'before-backup'):
                    raise RuntimeError(
                        'native SQLite state was not restored from backup'
                    )
            if options.engine == 'SQLite' and operation_id == 'analyze':
                native_states['after_analyze'] = _native_probe(
                    options.database_path, options.engine
                )
                if not native_states['after_analyze'][
                        'statistics_catalog_present']:
                    raise RuntimeError(
                        'rendered ANALYZE did not create SQLite statistics'
                    )
            if options.engine == 'DuckDB' and operation_id == (
                    'export_database'):
                if not (options.export_path / 'schema.sql').is_file() or not (
                        options.export_path / 'load.sql').is_file():
                    raise RuntimeError(
                        'rendered DuckDB export did not create its manifest'
                    )
                native_states['after_export'] = {
                    'schema_sql_sha256': _sha256(
                        options.export_path / 'schema.sql'
                    ),
                    'load_sql_sha256': _sha256(
                        options.export_path / 'load.sql'
                    ),
                }
            if options.engine == 'DuckDB' and operation_id == (
                    'import_database'):
                native_states['after_import'] = _native_probe(
                    options.database_path, options.engine
                )
                if native_states['after_import']['import_probe'] != (
                        'duckdb-import-browser-gate'):
                    raise RuntimeError(
                        'rendered DuckDB import lacked its native '
                        'postcondition'
                    )
        native_states['final'] = _native_probe(
            options.database_path, options.engine
        )
        if options.engine == 'SQLite' and native_states['final'][
                'quick_check'] != 'ok':
            raise RuntimeError('final native SQLite quick_check failed')
        engine_id = options.engine.lower()
        return {
            'schema': f'cdeadmin.{engine_id}-maintenance-ui-gate.v1',
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'engine_id': engine_id,
            'interface_id': f'{engine_id}-native',
            'reference_version': (
                '3.53.0' if options.engine == 'SQLite' else '1.5.2'
            ),
            'server_label': options.server,
            'database_label': options.database,
            'viewport': f'{options.width}x{options.height}',
            'theme': options.theme,
            'font_scale': options.font_scale,
            'evidence_variant': evidence_variant(options),
            'expected_operation_ids': [
                item['operation_id'] for item in forms
            ],
            'completed_operation_ids': [
                item['operation_id'] for item in completed
            ],
            'forms': completed,
            'native_states': native_states,
            'backup_header_observed': options.engine == 'SQLite',
            'backup_restore_state_round_trip_observed': (
                options.engine == 'SQLite'
            ),
            'duckdb_export_manifest_observed': (
                options.engine == 'DuckDB'
            ),
            'duckdb_import_state_observed_natively': (
                options.engine == 'DuckDB'
            ),
            'provider_owned_forms_only': True,
            'cancelled_form_count': sum(
                item['cancellation']['dialog_dismissed']
                for item in completed
            ),
            'validation_observed_form_count': sum(
                item['validation']['state'] == 'observed'
                for item in completed
            ),
            'validation_not_applicable_form_count': sum(
                item['validation']['state'] == 'not_applicable'
                for item in completed
            ),
            'accessibility_observed_form_count': sum(
                item['accessibility']['state'] == 'observed'
                for item in completed
            ),
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
        # The driver is closed by run(); preserve the traceback in stdout for
        # the orchestrator-owned log. A stale successful summary is never kept.
        options.summary_output.unlink(missing_ok=True)
        print(json.dumps({
            'passed': False,
            'error_type': type(exc).__name__,
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
        'operation_count': len(evidence['forms']),
        'passed': evidence['passed'],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
