#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Exercise exact-engine row editing in the rendered provider grid.

Each engine path retains an exact native observer.  DuckDB qualification does
not pass through SQLite or infer SQL behavior from the common grid contract.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import mariadb
import mysql.connector

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as expected
from selenium.webdriver.support.ui import WebDriverWait


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cdeadmin_firebird_grid_ui_gate import (  # noqa: E402
    _button,
    _capture,
    _choose_table,
    _grid_control_evidence,
    _row_button,
    _row_for_input,
)
from tools.cdeadmin_firebird_ui_form_gate import (  # noqa: E402
    close_workspace,
    create_driver,
    prepare_tree,
)
from tools.cdeadmin_ui_evidence import (  # noqa: E402
    complete_endpoint_prompt,
    invoke_context_action,
    wait_for_tree_item,
)


MARKER = 2147483002


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--engine', default='SQLite')
    parser.add_argument('--server', default='localhost')
    parser.add_argument('--database', required=True)
    parser.add_argument('--database-path', type=Path)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int)
    parser.add_argument('--user')
    parser.add_argument('--endpoint-password-env')
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
    parser.add_argument('--timeout', type=int, default=60)
    parser.add_argument('--browser-binary')
    return parser.parse_args(argv)


def _visible_inputs(driver, accessible_name):
    try:
        return [
            item for item in driver.find_elements(By.CSS_SELECTOR, 'input')
            if item.is_displayed() and (
                item.accessible_name == accessible_name or
                item.get_attribute('aria-label') == accessible_name
            )
        ]
    except StaleElementReferenceException:
        return []


def _engine_identity(engine):
    if engine == 'SQLite':
        return 'sqlite', 'sqlite-native', '3.53.0'
    if engine == 'DuckDB':
        return 'duckdb', 'duckdb-native', '1.5.2'
    if engine == 'MySQL':
        return 'mysql', 'mysql-native', '9.7.0'
    if engine == 'MariaDB':
        return 'mariadb', 'mariadb-native', '12.2.2'
    raise RuntimeError(f'unsupported native grid gate engine: {engine}')


def _mysql_connection(options):
    password = os.environ.get(options.endpoint_password_env or '')
    if not password:
        raise RuntimeError('MySQL grid password environment is empty')
    return mysql.connector.connect(
        host=options.host, port=options.port, user=options.user,
        password=password, database=options.database,
        connection_timeout=10, use_pure=True,
    )


def _mariadb_connection(options):
    password = os.environ.get(options.endpoint_password_env or '')
    if not password:
        raise RuntimeError('MariaDB grid password environment is empty')
    return mariadb.connect(
        host=options.host, port=options.port, user=options.user,
        password=password, database=options.database,
        connect_timeout=10,
    )


def _network_connection(options):
    return (
        _mariadb_connection(options)
        if options.engine == 'MariaDB' else _mysql_connection(options)
    )


def _native_row(options, marker):
    if options.engine in {'MySQL', 'MariaDB'}:
        connection = _network_connection(options)
        cursor = connection.cursor()
        try:
            cursor.execute(
                'SELECT customer_id, name, city, region FROM customers '
                'WHERE customer_id = %s', (marker,),
            )
            return cursor.fetchone()
        finally:
            cursor.close()
            connection.close()
    connector = (
        sqlite3.connect if options.engine == 'SQLite' else duckdb.connect
    )
    table = (
        'customers' if options.engine == 'SQLite' else 'service.customers'
    )
    with connector(str(options.database_path)) as connection:
        return connection.execute(
            f'SELECT customer_id, name, city, region FROM {table} '
            'WHERE customer_id = ?', (marker,),
        ).fetchone()


def _has_input_value(driver, accessible_name, expected_value):
    try:
        return any(
            item.get_attribute('value') == expected_value
            for item in _visible_inputs(driver, accessible_name)
        )
    except StaleElementReferenceException:
        return False


def _visible_button_exists(driver, text):
    try:
        return any(
            item.is_displayed() and item.text.strip() == text
            for item in driver.find_elements(By.TAG_NAME, 'button')
        )
    except StaleElementReferenceException:
        return True


def _open_grid_workspace(driver, wait, options):
    password = os.environ.get(options.endpoint_password_env or '', '')
    prepare_tree(driver, wait, options, password)
    database = wait_for_tree_item(wait, options.database)
    invoke_context_action(
        wait, driver, database,
        ['Database workspace',
         ('Browse and edit MySQL table data...'
          if options.engine == 'MySQL' else
          'Browse and edit MariaDB table data...'
          if options.engine == 'MariaDB' else
          f'Browse and edit {options.engine} rows...')],
        password, endpoint_prompt_timeout=5 if password else 1,
    )
    complete_endpoint_prompt(
        driver, password, timeout=5 if password else 1
    )
    _button(wait, 'Load rows')
    _choose_table(driver, wait, 'CUSTOMERS')


def _close_data_session(driver, wait):
    close_session = _button(wait, 'Close data session')
    close_session.click()
    wait.until(lambda value: (
        (control := next((
            item for item in value.find_elements(By.TAG_NAME, 'button')
            if item.is_displayed() and
            item.text.strip() == 'Close data session'
        ), None)) is not None and not control.is_enabled()
    ))


def _reopen_duckdb_grid(driver, wait, options):
    close_workspace(driver, wait)
    _open_grid_workspace(driver, wait, options)
    _button(wait, 'Load rows').click()
    wait.until(lambda value: bool(_visible_inputs(value, 'name value')))


def run(options):
    engine_id, interface_id, reference_version = _engine_identity(
        options.engine
    )
    marker_name = f'CDEadmin {options.engine} browser row'
    driver = create_driver(options)
    wait = WebDriverWait(driver, options.timeout)
    screenshots = {}
    controls = {}

    def capture(state):
        _capture(driver, options, state, screenshots)
        controls[state] = _grid_control_evidence(driver)

    try:
        _open_grid_workspace(driver, wait, options)
        capture('initial')

        load_button = _button(wait, 'Load rows')
        load_button.click()
        wait.until(lambda _value: load_button.is_enabled())
        if not _visible_inputs(driver, 'name value'):
            alerts = [
                item.text.strip() for item in driver.find_elements(
                    By.CSS_SELECTOR, '[role="alert"]'
                ) if item.is_displayed() and item.text.strip()
            ]
            capture('load-response-error')
            raise RuntimeError(
                f'{options.engine} row controls were not rendered after '
                'Load rows; '
                f'visible alerts: {alerts or ["none"]}'
            )
        capture('loaded')

        name = _visible_inputs(driver, 'name value')[0]
        original_name = name.get_attribute('value')
        name.send_keys(Keys.CONTROL, 'a')
        name.send_keys(f'CDEadmin {options.engine} rollback probe')
        save = _row_button(_row_for_input(name), 'Save')
        if save is None:
            raise RuntimeError(
                f'{options.engine} row Save control is unavailable'
            )
        save.click()
        wait.until(expected.visibility_of_element_located((
            By.CSS_SELECTOR, '[aria-label="Staged provider grid changes"]',
        )))
        capture('update-staged')
        _button(wait, 'Rollback changes').click()
        wait.until(expected.invisibility_of_element_located((
            By.CSS_SELECTOR, '[aria-label="Staged provider grid changes"]',
        )))
        wait.until(lambda value: _has_input_value(
            value, 'name value', original_name
        ))
        capture('update-rolled-back')
        if options.engine == 'DuckDB':
            _close_data_session(driver, wait)
            _reopen_duckdb_grid(driver, wait, options)

        values = {
            'customer_id new value': str(MARKER),
            'name new value': marker_name,
            'city new value': 'Toronto',
            'region new value': 'QA',
        }
        new_control = None
        for label, value in values.items():
            candidates = _visible_inputs(driver, label)
            if len(candidates) != 1:
                raise RuntimeError(
                    f'{options.engine} grid field {label} is missing'
                )
            candidates[0].send_keys(value)
            new_control = candidates[0]
        insert = _row_button(_row_for_input(new_control), 'Insert row')
        if insert is None:
            raise RuntimeError(
                f'{options.engine} Insert row control is unavailable'
            )
        insert.click()
        wait.until(expected.visibility_of_element_located((
            By.CSS_SELECTOR, '[aria-label="Staged provider grid changes"]',
        )))
        capture('insert-staged')
        _button(wait, 'Commit changes').click()
        wait.until(expected.invisibility_of_element_located((
            By.CSS_SELECTOR, '[aria-label="Staged provider grid changes"]',
        )))
        wait.until(lambda value: _has_input_value(
            value, 'customer_id value', str(MARKER)
        ))
        if options.engine == 'DuckDB':
            _close_data_session(driver, wait)
        committed_row = _native_row(options, MARKER)
        if committed_row != (
                MARKER, marker_name, 'Toronto', 'QA'):
            raise RuntimeError(
                f'{options.engine} native observer did not see commit; '
                f'observed {committed_row!r}'
            )
        capture('insert-committed')
        if options.engine == 'DuckDB':
            _reopen_duckdb_grid(driver, wait, options)

        marker_control = next(
            item for item in _visible_inputs(driver, 'customer_id value')
            if item.get_attribute('value') == str(MARKER)
        )
        marker_row = _row_for_input(marker_control)
        delete = _row_button(marker_row, 'Delete')
        if delete is None:
            raise RuntimeError(
                f'{options.engine} Delete control is unavailable'
            )
        delete.click()
        wait.until(lambda _value: _row_button(
            marker_row, 'Confirm delete'
        ) is not None)
        capture('safe-delete-confirmation')
        _row_button(marker_row, 'Confirm delete').click()
        wait.until(expected.visibility_of_element_located((
            By.CSS_SELECTOR, '[aria-label="Staged provider grid changes"]',
        )))
        capture('delete-staged')
        _button(wait, 'Commit changes').click()
        wait.until(expected.invisibility_of_element_located((
            By.CSS_SELECTOR, '[aria-label="Staged provider grid changes"]',
        )))
        wait.until(lambda value: not _has_input_value(
            value, 'customer_id value', str(MARKER)
        ))
        if options.engine == 'DuckDB':
            _close_data_session(driver, wait)
        if _native_row(options, MARKER) is not None:
            raise RuntimeError(
                f'{options.engine} native observer still sees deleted row'
            )
        capture('delete-committed-clean')
        if options.engine == 'DuckDB':
            _reopen_duckdb_grid(driver, wait, options)

        _button(wait, 'Provider transaction state').click()
        wait.until(expected.visibility_of_element_located((
            By.CSS_SELECTOR, '[aria-label="Provider grid transaction state"]',
        )))
        capture('transaction-state')

        _choose_table(driver, wait, 'PAGINATION_PROBE')
        _button(wait, 'Load rows').click()
        next_page = _button(wait, 'Next row page')
        cancel_cursor = _button(wait, 'Cancel row cursor')
        if not next_page.is_enabled() or not cancel_cursor.is_enabled():
            raise RuntimeError(
                f'{options.engine} row pagination controls are disabled'
            )
        capture('pagination-first-page')
        cancel_cursor.click()
        wait.until(lambda value: not _visible_button_exists(
            value, 'Cancel row cursor'
        ))
        capture('pagination-cursor-cancelled')

        _button(wait, 'Load rows').click()
        _button(wait, 'Next row page').click()
        wait.until(lambda value: _has_input_value(
            value, 'id value', '201'
        ))
        capture('pagination-second-page')

        _close_data_session(driver, wait)
        capture('session-closed')
        return {
            'schema': f'cdeadmin.{engine_id}-grid-ui-gate.v1',
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'engine_id': engine_id, 'interface_id': interface_id,
            'reference_version': reference_version,
            'database': options.database, 'table': 'customers',
            'rollback_restored_original': True,
            'commit_observed_natively': True,
            'safe_delete_confirmation_observed': True,
            'committed_cleanup_observed_natively': True,
            'provider_session_state_observed': True,
            'pagination_first_and_second_page_observed': True,
            'provider_cursor_cancellation_observed': True,
            'provider_session_closed': True,
            'provider_finality_authority': True,
            'common_finality_interpreted': False,
            'credential_values_exported': False,
            'screenshots': screenshots, 'controls': controls,
            'passed': True,
        }
    finally:
        try:
            if options.engine in {'SQLite', 'MySQL', 'MariaDB'}:
                if options.engine in {'MySQL', 'MariaDB'}:
                    connection = _network_connection(options)
                    cursor = connection.cursor()
                    cursor.execute(
                        'DELETE FROM customers WHERE customer_id = %s',
                        (MARKER,),
                    )
                    cursor.close()
                else:
                    connection = sqlite3.connect(str(options.database_path))
                    connection.execute(
                        'DELETE FROM customers WHERE customer_id = ?',
                        (MARKER,),
                    )
                connection.commit()
                connection.close()
        finally:
            driver.quit()


def main(argv=None):
    options = arguments(argv)
    evidence = run(options)
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps({
        'engine_id': evidence['engine_id'],
        'reference_version': evidence['reference_version'],
        'screenshot_count': len(evidence['screenshots']),
        'passed': evidence['passed'],
        'credential_values_exported': False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
