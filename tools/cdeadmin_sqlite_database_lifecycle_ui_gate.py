#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Certify SQLite or DuckDB target lifecycle forms in the real browser UI.

The caller must provide an isolated CDEadmin configuration database and a
disposable filesystem root.  This gate uses the rendered, provider-owned
forms for every SQLite database-target task.  Native SQLite and configuration
database reads independently verify the completed operations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import traceback
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as expected
from selenium.webdriver.support.ui import WebDriverWait

if __package__:
    from .cdeadmin_firebird_ui_form_gate import (
        accessibility_observation,
        apply_presentation,
        assert_form_controls,
        create_driver,
        evidence_variant,
        layout_observation,
        screenshot,
    )
    from .cdeadmin_ui_evidence import (
        complete_endpoint_prompt,
        expand,
        fill_fields,
        invoke_context_action,
        visible_named_control,
        wait_for_tree_item,
    )
else:
    from cdeadmin_firebird_ui_form_gate import (
        accessibility_observation,
        apply_presentation,
        assert_form_controls,
        create_driver,
        evidence_variant,
        layout_observation,
        screenshot,
    )
    from cdeadmin_ui_evidence import (
        complete_endpoint_prompt,
        expand,
        fill_fields,
        invoke_context_action,
        visible_named_control,
        wait_for_tree_item,
    )


MENU_GROUPS = {
    'database': 'Database workspace',
    'database-lifecycle': 'Definition and lifecycle',
}
COMMANDS = {
    'define': 'endpoint.sqlite.register_database',
    'create': 'endpoint.sqlite.create_database',
    'connect': 'database.sqlite.connect',
    'edit': 'database.sqlite.edit',
    'alter': 'database.sqlite.alter',
    'drop': 'database.sqlite.drop',
    'remove': 'database.sqlite.remove',
}
ENGINE_NAME = 'SQLite'
ENGINE_ID = 'sqlite'
PROFILE_ID = 'sqlite-native'
ENDPOINT_PASSWORD = None
COMPLETION_ORDER = (
    'connect', 'edit', 'define', 'remove', 'create', 'alter', 'drop',
)
RENDER_ORDER = (
    'connect', 'edit', 'alter', 'drop', 'remove', 'define', 'create',
)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--config-db', type=Path, required=True)
    parser.add_argument('--database-root', type=Path, required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--engine', choices=('SQLite', 'DuckDB'),
                        default='SQLite')
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--summary-output', type=Path, required=True)
    parser.add_argument('--browser-binary')
    parser.add_argument('--width', type=int, default=1600)
    parser.add_argument('--height', type=int, default=1000)
    parser.add_argument(
        '--theme', choices=('default', 'high-contrast'), default='default',
    )
    parser.add_argument(
        '--font-scale', type=int, choices=(100, 150, 200, 300), default=100,
    )
    parser.add_argument('--timeout', type=int, default=90)
    return parser.parse_args(argv)


def _configure_engine(engine):
    global ENGINE_NAME, ENGINE_ID, PROFILE_ID, COMMANDS
    ENGINE_NAME = engine
    ENGINE_ID = engine.lower()
    PROFILE_ID = f'{ENGINE_ID}-native'
    COMMANDS = {
        'define': f'endpoint.{ENGINE_ID}.register_database',
        'create': f'endpoint.{ENGINE_ID}.create_database',
        **{
            mode: f'database.{ENGINE_ID}.{mode}'
            for mode in ('connect', 'edit', 'alter', 'drop', 'remove')
        },
    }


def _prepare_tree(driver, wait, options, database_label):
    driver.get(driver.current_url.split('/browser/')[0].rstrip('/') +
               '/browser/' if '/browser/' in driver.current_url else
               driver.current_url.rstrip('/') + '/browser/')
    wait.until(lambda value: '/browser/' in value.current_url)
    apply_presentation(driver, wait, options)
    for label, child in (
        ('Connectors', ENGINE_NAME),
        (ENGINE_NAME, 'localhost'),
        ('localhost', database_label),
    ):
        expand(wait, label)
        wait_for_tree_item(wait, child)
    database = wait_for_tree_item(wait, database_label)
    driver.execute_script('arguments[0].click()', database)
    driver.execute_script(
        """
        const tree = window.pgAdmin.Browser.tree;
        let item = tree.selected();
        while (item && tree.itemData(item)?._type !== 'server') {
          item = tree.hasParent(item) ? tree.parent(item) : null;
        }
        const node = window.pgAdmin.Browser.Nodes.server;
        window.__cdeadminSqliteLifecycleVerified = false;
        node.callbacks.verify_cde_endpoint.call(node, {
          item,
          onSuccess: () => {
            window.__cdeadminSqliteLifecycleVerified = true;
          },
        });
        """
    )
    password_env = getattr(options, 'endpoint_password_env', None)
    if password_env:
        complete_endpoint_prompt(
            driver, os.environ.get(password_env), timeout=options.timeout
        )
    wait.until(lambda value: value.execute_script(
        'return window.__cdeadminSqliteLifecycleVerified === true'
    ))


def _tree_context(driver, database_label=None):
    """Select and return the endpoint or named database tree item."""
    if database_label:
        # The tree virtualizes rows while scrolling.  A coordinate-based
        # ActionChains click can therefore land on the row that replaced this
        # element during the scroll. Reacquire the mounted row after every
        # stale-element race and dispatch directly to the exact element.
        def select_exact_database(value):
            for candidate in reversed(value.find_elements(
                    By.CSS_SELECTOR, '.file-name')):
                try:
                    if candidate.text != database_label:
                        continue
                    value.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'}); "
                        "arguments[0].click();",
                        candidate,
                    )
                    return True
                except StaleElementReferenceException:
                    return False
            return False

        WebDriverWait(driver, 10).until(select_exact_database)
        WebDriverWait(driver, 10).until(lambda value: value.execute_script(
            """
            const tree = window.pgAdmin.Browser.tree;
            const item = tree.selected();
            const data = item ? tree.itemData(item) : null;
            return data?._type === 'cde_database_target' &&
              (data?.label || data?._label) === arguments[0];
            """, database_label
        ))
    return driver.execute_script(
        """
        const tree = window.pgAdmin.Browser.tree;
        let item = tree.selected();
        while (item && tree.itemData(item)?._type !== 'server') {
          item = tree.hasParent(item) ? tree.parent(item) : null;
        }
        const endpoint = tree.itemData(item);
        return {
          endpoint_label: endpoint.label || endpoint._label,
          selected_label: tree.itemData(tree.selected()).label ||
            tree.itemData(tree.selected())._label,
          selected_actions:
            tree.itemData(tree.selected()).cde_context_actions || [],
          endpoint_actions: endpoint.cde_context_actions || [],
        };
        """
    )


def _action(driver, mode, database_label=None):
    context = _tree_context(driver, database_label)
    if mode in {'define', 'create'}:
        selected = driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            const tree = window.pgAdmin?.Browser?.tree;
            let item = tree?.selected?.();
            while (item && tree.itemData(item)?._type !== 'server') {
              item = tree.hasParent(item) ? tree.parent(item) : null;
            }
            if (!item) {
              done(false);
              return;
            }
            Promise.resolve(tree.select(item, true, 'center')).then(
              () => done(true), () => done(false)
            );
            """
        )
        if selected is not True:
            raise RuntimeError('provider endpoint tree selection failed')
        WebDriverWait(driver, 10).until(lambda value: value.execute_script(
            """
            const tree = window.pgAdmin?.Browser?.tree;
            const item = tree?.selected?.();
            return item && tree.itemData(item)?._type === 'server';
            """
        ))
        context = _tree_context(driver)
    actions = (context['endpoint_actions'] if mode in {'define', 'create'}
               else context['selected_actions'])
    command_id = COMMANDS[mode]
    action = next((item for item in actions
                   if item.get('command_id') == command_id), None)
    if action is None:
        raise RuntimeError(
            f'context command {command_id} is unavailable in ' +
            json.dumps(context, sort_keys=True)
        )
    if action.get('enabled') is False:
        raise RuntimeError(
            f'context command {command_id} is disabled: ' +
            str(action.get('disabled_reason') or '')
        )
    return action


def _open_form(driver, wait, mode, database_label=None):
    action = _action(driver, mode, database_label)
    selected = wait.until(expected.visibility_of_element_located((
        By.CSS_SELECTOR, '.file-entry[aria-selected="true"]',
    )))
    invoke_context_action(
        wait, driver, selected,
        [MENU_GROUPS[action['menu_group']], action['label']],
        endpoint_password=ENDPOINT_PASSWORD,
        endpoint_prompt_timeout=5 if ENDPOINT_PASSWORD else 1,
    )
    form_id = action.get('arguments', {}).get('form_id')
    if not form_id:
        raise RuntimeError(f'{mode} context command has no form identity')
    wait.until(expected.visibility_of_element_located((
        By.CSS_SELECTOR, '[role="dialog"]',
    )))
    return action, form_id


def _form_contract(driver, mode):
    return driver.execute_script(
        """
        const mode = arguments[0];
        const tree = window.pgAdmin.Browser.tree;
        let item = tree.selected();
        while (item && tree.itemData(item)?._type !== 'server') {
          item = tree.hasParent(item) ? tree.parent(item) : null;
        }
        const data = tree.itemData(item);
        const profiles = window.pgAdmin.Browser.Nodes.server;
        return data?.cde_profile_id || null;
        """, mode
    )


def _visible_form_fields(driver):
    return driver.execute_script(
        """
        const dialog = document.querySelector('[role="dialog"]');
        return [...dialog.querySelectorAll(
          'input, textarea, select, [role="combobox"]'
        )].filter(element => {
          const style = getComputedStyle(element);
          return style.display !== 'none' && style.visibility !== 'hidden' &&
            element.getClientRects().length > 0;
        }).map(element => element.getAttribute('aria-label') ||
          element.getAttribute('name') || element.id || '');
        """
    )


def _catalog_forms(driver):
    return driver.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const app = window.pgAdmin;
        const tree = app.Browser.tree;
        let item = tree.selected();
        while (item && tree.itemData(item)?._type !== 'server') {
          item = tree.hasParent(item) ? tree.parent(item) : null;
        }
        const data = tree.itemData(item);
        const node = app.Browser.Nodes.server;
        const url = node.generate_url(item, 'cde_workspace', data, true);
        const headers = {'Content-type': 'application/json'};
        if (app.csrf_token_header && app.csrf_token) {
          headers[app.csrf_token_header] = app.csrf_token;
        }
        fetch(url, {credentials: 'same-origin', headers})
          .then(response => response.json())
          .then(value => {
            const forms = value.data.database_targets.forms.forms;
            const database = value.data.visual_admin?.objects?.find(
              item => item.resource_kind === 'database'
            );
            for (const mode of ['create', 'alter', 'drop']) {
              const operation = database?.operations?.find(
                item => item.operation_id === mode
              );
              if (operation?.form && forms[mode]) {
                forms[mode] = {...forms[mode],
                  fields: operation.form.fields};
              }
            }
            done(forms);
          })
          .catch(error => done({__error__: String(error)}));
        """
    )


def _target_rows(config_db):
    with sqlite3.connect(config_db) as connection:
        rows = connection.execute(
            """
            SELECT t.id, t.display_name, t.database, t.configuration,
                   t.active
              FROM cde_endpoint_database_target AS t
              JOIN cde_endpoint AS e ON e.id = t.endpoint_id
             WHERE e.profile_id = ?
             ORDER BY t.database
            """,
            (PROFILE_ID,),
        ).fetchall()
    return [{
        'target_id': row[0], 'display_name': row[1],
        'database': row[2], 'configuration': json.loads(row[3] or '{}'),
        'active': bool(row[4]),
    } for row in rows]


def _database_state(path):
    if not path.is_file():
        return {'exists': False}
    if ENGINE_ID == 'duckdb':
        with duckdb.connect(str(path), read_only=True) as connection:
            return {
                'exists': True,
                'runtime_version': connection.execute(
                    'SELECT version()'
                ).fetchone()[0],
                'database_name': connection.execute(
                    'SELECT current_database()'
                ).fetchone()[0],
                'duckdb_header': path.read_bytes()[8:12] == b'DUCK',
            }
    with sqlite3.connect(path) as connection:
        values = {}
        for name in ('page_size', 'encoding', 'auto_vacuum',
                     'application_id', 'user_version', 'journal_mode',
                     'synchronous'):
            values[name] = connection.execute(
                f'PRAGMA {name}'
            ).fetchone()[0]
    return {'exists': True, 'pragmas': values}


def _close_with_escape(driver, wait):
    dialog = wait.until(expected.visibility_of_element_located((
        By.CSS_SELECTOR, '[role="dialog"]',
    )))
    ActionChains(driver).send_keys(Keys.ESCAPE).perform()
    wait.until(expected.staleness_of(dialog))


def _close(driver, wait):
    dialog = wait.until(expected.visibility_of_element_located((
        By.CSS_SELECTOR, '[role="dialog"]',
    )))
    close = wait.until(lambda value: visible_named_control(value, 'Close'))
    close.click()
    wait.until(expected.staleness_of(dialog))


def _validation_observation(driver, wait, form, mode):
    required = next((
        field for field in form['fields']
        if field.get('required') and field.get('control') in {
            'text', 'file', 'code', 'json', 'number',
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
    buttons = {
        'create': 'Validate and preview',
        'alter': 'Validate and preview',
        'drop': 'Validate and preview',
    }
    button = wait.until(lambda value: visible_named_control(
        value, buttons.get(mode, form['title'])
    ))
    if button.is_enabled():
        button.click()
        wait.until(lambda value: any(
            item.is_displayed()
            for item in value.find_elements(By.CSS_SELECTOR, '[role="alert"]')
        ))
    if driver.find_elements(
            By.CSS_SELECTOR, '[aria-label="Provider plan preview"]'):
        raise RuntimeError(f'{mode} planned empty required input')
    return {
        'state': 'observed',
        'field_id': required['field_id'],
        'validation_kind': 'required',
        'plan_present': False,
        'provider_operation_executed': False,
    }


def _render_case(driver, wait, options, forms, mode, database_label=None):
    action, form_id = _open_form(driver, wait, mode, database_label)
    form = forms[mode]
    if form_id != form['form_id']:
        raise RuntimeError(f'{mode} opened a different form contract')
    controls = assert_form_controls(wait, form['fields'])
    operation = {'operation_id': mode}
    layout = layout_observation(driver)
    accessibility = accessibility_observation(
        driver, wait, operation, controls
    )
    path = options.output_root / COMMANDS[mode] / (
        f'initial-{options.width}x{options.height}-'
        f'{evidence_variant(options)}.png'
    )
    digest = screenshot(driver, path)
    before_targets = _target_rows(options.config_db)
    _close_with_escape(driver, wait)
    cancelled_path = options.output_root / COMMANDS[mode] / (
        f'cancelled-{options.width}x{options.height}-'
        f'{evidence_variant(options)}.png'
    )
    cancelled_digest = screenshot(driver, cancelled_path)
    after_targets = _target_rows(options.config_db)
    if before_targets != after_targets:
        raise RuntimeError(f'{mode} cancellation changed retained targets')

    _open_form(driver, wait, mode, database_label)
    assert_form_controls(wait, form['fields'])
    validation = _validation_observation(driver, wait, form, mode)
    validation_layout = None
    validation_path = None
    validation_digest = None
    if validation['state'] == 'observed':
        validation_layout = layout_observation(driver)
        validation_path = options.output_root / COMMANDS[mode] / (
            f'validation-error-{options.width}x{options.height}-'
            f'{evidence_variant(options)}.png'
        )
        validation_digest = screenshot(driver, validation_path)
    _close(driver, wait)
    result = {
        'mode': mode, 'command_id': COMMANDS[mode], 'form_id': form_id,
        'declared_field_count': len(form['fields']),
        'rendered_field_count': len(controls), 'layout': layout,
        'accessibility': accessibility,
        'validation': validation,
        'validation_layout': validation_layout,
        'cancellation': {
            'dialog_dismissed': True,
            'dismissal_input': 'keyboard_escape',
            'provider_operation_executed': False,
        },
        'cancelled_without_target_mutation': True,
        'screenshots': {
            'initial': {'path': str(path), 'sha256': digest},
            'cancelled': {
                'path': str(cancelled_path),
                'sha256': cancelled_digest,
            },
        },
        'values_recorded': False,
    }
    if validation_path is not None:
        result['screenshots']['validation_error'] = {
            'path': str(validation_path),
            'sha256': validation_digest,
        }
    return result


def _submit_target_form(driver, wait, mode, values):
    fill_fields(wait, [f'{label}={value}' for label, value in values.items()])
    title = driver.find_elements(
        By.CSS_SELECTOR, '[role="dialog"] h2'
    )[-1].text
    button = wait.until(lambda value: visible_named_control(value, title))
    wait.until(lambda _driver: button.is_enabled())
    button.click()


def _wait_for_native_state(wait, predicate, message):
    try:
        wait.until(lambda _driver: predicate())
    except Exception as exc:
        raise RuntimeError(message) from exc


def _wait_for_database_state(wait, path, message):
    """Wait until an independently opened native observer can read a file."""
    observed = {}

    def inspect():
        try:
            observed.clear()
            observed.update(_database_state(path))
            return True
        except duckdb.IOException as exc:
            # The CDEadmin process may still be closing the short-lived
            # verification handle after activating a newly created DuckDB
            # target. The independent observer must wait for native lock
            # release instead of treating that scheduling race as product
            # state.
            if ENGINE_ID == 'duckdb' and 'Could not set lock' in str(exc):
                return False
            raise

    _wait_for_native_state(wait, inspect, message)
    return dict(observed)


def _submit_lifecycle(driver, wait, options, mode, values):
    fill_fields(wait, [f'{label}={value}' for label, value in values.items()])
    preview = wait.until(
        lambda value: visible_named_control(value, 'Validate and preview')
    )
    wait.until(lambda _driver: preview.is_enabled())
    preview.click()
    plan = wait.until(expected.visibility_of_element_located((
        By.CSS_SELECTOR, '[role="dialog"] pre',
    )))
    plan_value = json.loads(plan.text)
    if plan_value.get('state') != 'ready' or (
            plan_value.get('execution_available') is not True):
        raise RuntimeError(f'{mode} provider plan is not executable')
    preview_path = options.output_root / COMMANDS[mode] / (
        f'plan-preview-{options.width}x{options.height}-'
        f'{evidence_variant(options)}.png'
    )
    preview_digest = screenshot(driver, preview_path)
    preview_layout = layout_observation(driver)
    confirmation = visible_named_control(
        driver, 'I confirm this provider-planned database operation.'
    )
    if confirmation is not None:
        driver.execute_script('arguments[0].click()', confirmation)
    title = driver.find_elements(
        By.CSS_SELECTOR, '[role="dialog"] h2'
    )[-1].text
    apply_button = wait.until(
        lambda value: visible_named_control(value, title)
    )
    wait.until(lambda _driver: apply_button.is_enabled())
    apply_button.click()
    wait.until(lambda value: (
        'completed the native database operation' in
        value.find_element(By.CSS_SELECTOR, '[role="dialog"]').text
    ))
    completed_layout = layout_observation(driver)
    return {
        'plan_state': 'ready', 'execution_available': True,
        'confirmation_observed': confirmation is not None,
        'provider_completion_observed': True,
        'preview_layout': preview_layout,
        'completed_layout': completed_layout,
        'screenshots': {
            'plan_preview': {
                'path': str(preview_path), 'sha256': preview_digest,
            },
        },
    }


def _capture_completed(driver, options, mode, evidence):
    complete_path = options.output_root / COMMANDS[mode] / (
        f'completed-{options.width}x{options.height}-'
        f'{evidence_variant(options)}.png'
    )
    evidence.setdefault('screenshots', {})['completed'] = {
        'path': str(complete_path),
        'sha256': screenshot(driver, complete_path),
    }
    evidence['completed_layout'] = layout_observation(driver)


def _refresh_tree(driver, wait, options, database_label):
    for attempt in range(2):
        try:
            driver.get(options.url.rstrip('/') + '/browser/')
            wait.until(lambda value: '/browser/' in value.current_url)
            apply_presentation(driver, wait, options)
            for label, child in (
                ('Connectors', ENGINE_NAME),
                (ENGINE_NAME, 'localhost'),
                ('localhost', database_label),
            ):
                expand(wait, label)
                wait_for_tree_item(wait, child)
            return
        except TimeoutException:
            if attempt:
                raise
            driver.get('about:blank')


def _complete_duckdb_cases(driver, wait, options):
    original_path = options.database_root / options.database
    registered_path = options.database_root / 'duckdb-ui-registered.duckdb'
    created_path = options.database_root / 'duckdb-ui-created.duckdb'
    with duckdb.connect(str(registered_path)) as connection:
        connection.execute(
            'CREATE TABLE registered_probe(id INTEGER PRIMARY KEY)'
        )
    evidence = []

    _open_form(driver, wait, 'connect', options.database)
    _submit_target_form(driver, wait, 'connect', {
        'DuckDB configuration': '{"threads":2}',
    })
    _wait_for_native_state(wait, lambda: any(
        item['database'] == str(original_path) and item['active'] and
        item['configuration'].get('config', {}).get('threads') == 2
        for item in _target_rows(options.config_db)
    ), 'connect did not retain active DuckDB options')
    evidence.append({
        'mode': 'connect', 'active_target_observed': True,
        'connection_options_observed': True,
        'preview_state': 'not_applicable',
    })
    _capture_completed(driver, options, 'connect', evidence[-1])
    _close(driver, wait)

    _open_form(driver, wait, 'edit', options.database)
    _submit_target_form(driver, wait, 'edit', {
        'Navigator display name': options.database,
        'DuckDB configuration': '{"threads":3}',
    })
    _wait_for_native_state(wait, lambda: any(
        item['database'] == str(original_path) and
        item['configuration'].get('config', {}).get('threads') == 3
        for item in _target_rows(options.config_db)
    ), 'edit did not persist DuckDB connection options')
    evidence.append({
        'mode': 'edit', 'connection_options_observed': True,
        'preview_state': 'not_applicable',
    })
    _capture_completed(driver, options, 'edit', evidence[-1])
    _close(driver, wait)

    _open_form(driver, wait, 'define')
    _submit_target_form(driver, wait, 'define', {
        'DuckDB database file': str(registered_path),
        'Navigator display name': registered_path.name,
        'DuckDB configuration': '{"threads":2}',
    })
    _wait_for_native_state(wait, lambda: any(
        item['database'] == str(registered_path) and item['active']
        for item in _target_rows(options.config_db)
    ), 'define did not retain and activate the DuckDB file')
    evidence.append({
        'mode': 'define', 'retained_target_observed': True,
        'native_file_preserved': registered_path.is_file(),
        'preview_state': 'not_applicable',
    })
    _capture_completed(driver, options, 'define', evidence[-1])
    _close(driver, wait)

    _refresh_tree(driver, wait, options, registered_path.name)
    _open_form(driver, wait, 'remove', registered_path.name)
    _submit_target_form(driver, wait, 'remove', {
        'Type the native name to confirm': str(registered_path),
    })
    _wait_for_native_state(wait, lambda: not any(
        item['database'] == str(registered_path)
        for item in _target_rows(options.config_db)
    ), 'remove retained the DuckDB registration')
    if not registered_path.is_file():
        raise RuntimeError('remove deleted the native DuckDB file')
    evidence.append({
        'mode': 'remove', 'target_removed': True,
        'native_file_preserved': True, 'preview_state': 'not_applicable',
    })
    _capture_completed(driver, options, 'remove', evidence[-1])
    _close(driver, wait)

    _refresh_tree(driver, wait, options, options.database)
    _open_form(driver, wait, 'create')
    lifecycle = _submit_lifecycle(driver, wait, options, 'create', {
        'Database filename': created_path.name,
        'DuckDB creation configuration': '{"threads":2}',
    })
    state = _wait_for_database_state(
        wait, created_path,
        'created DuckDB file remained locked to native observation',
    )
    if not state['exists'] or not state['duckdb_header'] or (
            state['runtime_version'] != 'v1.5.2'):
        raise RuntimeError('create native state is not a DuckDB 1.5.2 file')
    if not any(item['database'] == str(created_path) and item['active']
               for item in _target_rows(options.config_db)):
        raise RuntimeError('created DuckDB file was not retained and active')
    evidence.append({
        'mode': 'create', **lifecycle,
        'native_state_observed': True, 'retained_target_observed': True,
    })
    _capture_completed(driver, options, 'create', evidence[-1])
    _close(driver, wait)

    _refresh_tree(driver, wait, options, created_path.name)
    _open_form(driver, wait, 'drop', created_path.name)
    lifecycle = _submit_lifecycle(driver, wait, options, 'drop', {
        'Type the exact database path to confirm deletion': str(created_path),
    })
    if created_path.exists():
        raise RuntimeError('drop did not delete the native DuckDB file')
    if any(item['database'] == str(created_path)
           for item in _target_rows(options.config_db)):
        raise RuntimeError('drop retained the deleted DuckDB target')
    evidence.append({
        'mode': 'drop', **lifecycle,
        'native_file_absent': True, 'target_removed': True,
    })
    _capture_completed(driver, options, 'drop', evidence[-1])
    _close(driver, wait)
    return evidence


def _complete_cases(driver, wait, options):
    if ENGINE_ID == 'duckdb':
        return _complete_duckdb_cases(driver, wait, options)
    original_path = options.database_root / options.database
    registered_path = options.database_root / 'sqlite-ui-registered.sqlite'
    created_path = options.database_root / 'sqlite-ui-created.sqlite'
    with sqlite3.connect(registered_path) as connection:
        connection.execute(
            'CREATE TABLE registered_probe(id INTEGER PRIMARY KEY)'
        )
        connection.commit()
    evidence = []

    _open_form(driver, wait, 'connect', options.database)
    _submit_target_form(driver, wait, 'connect', {
        'Busy timeout (milliseconds)': '6700',
    })
    _wait_for_native_state(wait, lambda: any(
        item['database'] == str(original_path) and item['active'] and
        item['configuration'].get('busy_timeout') == 6700
        for item in _target_rows(options.config_db)
    ), 'connect did not retain the active SQLite options')
    row = next(item for item in _target_rows(options.config_db)
               if item['database'] == str(original_path))
    if not row['active'] or row['configuration'].get('busy_timeout') != 6700:
        raise RuntimeError('connect did not retain the active SQLite options')
    evidence.append({'mode': 'connect', 'active_target_observed': True,
                     'connection_options_observed': True,
                     'preview_state': 'not_applicable'})
    _capture_completed(driver, options, 'connect', evidence[-1])
    _close(driver, wait)

    _open_form(driver, wait, 'edit', options.database)
    _submit_target_form(driver, wait, 'edit', {
        'Navigator display name': options.database,
        'Busy timeout (milliseconds)': '6800',
    })
    _wait_for_native_state(wait, lambda: any(
        item['database'] == str(original_path) and
        item['configuration'].get('busy_timeout') == 6800
        for item in _target_rows(options.config_db)
    ), 'edit did not persist SQLite connection options')
    row = next(item for item in _target_rows(options.config_db)
               if item['database'] == str(original_path))
    if row['configuration'].get('busy_timeout') != 6800:
        raise RuntimeError('edit did not persist SQLite connection options')
    evidence.append({'mode': 'edit', 'connection_options_observed': True,
                     'preview_state': 'not_applicable'})
    _capture_completed(driver, options, 'edit', evidence[-1])
    _close(driver, wait)

    _open_form(driver, wait, 'define')
    _submit_target_form(driver, wait, 'define', {
        'SQLite database file': str(registered_path),
        'Navigator display name': registered_path.name,
        'Busy timeout (milliseconds)': '6900',
    })
    _wait_for_native_state(wait, lambda: any(
        item['database'] == str(registered_path) and item['active']
        for item in _target_rows(options.config_db)
    ), 'define did not retain and activate SQLite file')
    targets = _target_rows(options.config_db)
    if not any(item['database'] == str(registered_path) and item['active']
               for item in targets):
        raise RuntimeError('define did not retain and activate SQLite file')
    evidence.append({'mode': 'define', 'retained_target_observed': True,
                     'native_file_preserved': registered_path.is_file(),
                     'preview_state': 'not_applicable'})
    _capture_completed(driver, options, 'define', evidence[-1])
    _close(driver, wait)

    _refresh_tree(driver, wait, options, registered_path.name)
    _open_form(driver, wait, 'remove', registered_path.name)
    _submit_target_form(driver, wait, 'remove', {
        'Type the native name to confirm': str(registered_path),
    })
    _wait_for_native_state(wait, lambda: not any(
        item['database'] == str(registered_path)
        for item in _target_rows(options.config_db)
    ), 'remove retained the SQLite registration')
    if any(item['database'] == str(registered_path)
           for item in _target_rows(options.config_db)):
        raise RuntimeError('remove retained the SQLite registration')
    if not registered_path.is_file():
        raise RuntimeError('remove deleted the native SQLite file')
    evidence.append({'mode': 'remove', 'target_removed': True,
                     'native_file_preserved': True,
                     'preview_state': 'not_applicable'})
    _capture_completed(driver, options, 'remove', evidence[-1])
    _close(driver, wait)

    _refresh_tree(driver, wait, options, options.database)
    _open_form(driver, wait, 'create')
    lifecycle = _submit_lifecycle(driver, wait, options, 'create', {
        'Database filename': created_path.name,
        'Page size': '8192', 'Text encoding': 'UTF-8',
        'Auto-vacuum mode': 'FULL', 'Application ID': '5300',
        'User version': '53',
    })
    state = _database_state(created_path)
    pragmas = state.get('pragmas', {})
    if not state['exists'] or pragmas.get('page_size') != 8192 or (
            pragmas.get('application_id') != 5300 or
            pragmas.get('user_version') != 53):
        raise RuntimeError('create native state does not match the form plan')
    if not any(item['database'] == str(created_path) and item['active']
               for item in _target_rows(options.config_db)):
        raise RuntimeError('created SQLite file was not retained and active')
    evidence.append({'mode': 'create', **lifecycle,
                     'native_state_observed': True,
                     'retained_target_observed': True})
    _capture_completed(driver, options, 'create', evidence[-1])
    _close(driver, wait)

    _refresh_tree(driver, wait, options, created_path.name)
    _open_form(driver, wait, 'alter', created_path.name)
    lifecycle = _submit_lifecycle(driver, wait, options, 'alter', {
        'Journal mode': 'DELETE', 'Synchronous mode': 'FULL',
        'Application ID': '5353', 'User version': '530',
    })
    state = _database_state(created_path)
    pragmas = state.get('pragmas', {})
    if pragmas.get('application_id') != 5353 or (
            pragmas.get('user_version') != 530 or
            str(pragmas.get('journal_mode')).lower() != 'delete'):
        raise RuntimeError('alter native state does not match the form plan')
    evidence.append({'mode': 'alter', **lifecycle,
                     'native_state_observed': True})
    _capture_completed(driver, options, 'alter', evidence[-1])
    _close(driver, wait)

    _refresh_tree(driver, wait, options, created_path.name)
    _open_form(driver, wait, 'drop', created_path.name)
    lifecycle = _submit_lifecycle(driver, wait, options, 'drop', {
        'Type the exact database path to confirm deletion': str(created_path),
    })
    if created_path.exists():
        raise RuntimeError('drop did not delete the native SQLite file')
    if any(item['database'] == str(created_path)
           for item in _target_rows(options.config_db)):
        raise RuntimeError('drop retained the deleted SQLite target')
    evidence.append({'mode': 'drop', **lifecycle,
                     'native_file_absent': True,
                     'target_removed': True})
    _capture_completed(driver, options, 'drop', evidence[-1])
    _close(driver, wait)
    return evidence


def _duckdb_alter_observation(driver, database_label):
    if ENGINE_ID != 'duckdb':
        return None
    context = _tree_context(driver, database_label)
    action = next((
        item for item in context['selected_actions']
        if item.get('command_id') == 'database.duckdb.alter'
    ), None)
    expected = (
        'DuckDB 1.5.2 has no ALTER DATABASE statement. Connection and '
        'session settings are edited through their provider-owned forms.'
    )
    if action is None or action.get('enabled') is not False or (
            action.get('disabled_reason') != expected):
        raise RuntimeError(
            'DuckDB ALTER DATABASE must be visibly disabled with its exact '
            'native reason'
        )
    return {
        'command_id': action['command_id'], 'enabled': False,
        'disabled_reason': action['disabled_reason'],
    }


def run(options):
    _configure_engine(options.engine)
    driver = create_driver(options)
    driver.set_script_timeout(max(120, options.timeout * 10))
    wait = WebDriverWait(driver, options.timeout)
    rendered = []
    completed = []
    failures = []
    alter_observation = None
    expected_modes = (
        tuple(item for item in COMPLETION_ORDER if item != 'alter')
        if ENGINE_ID == 'duckdb' else COMPLETION_ORDER
    )
    render_order = (
        tuple(item for item in RENDER_ORDER if item != 'alter')
        if ENGINE_ID == 'duckdb' else RENDER_ORDER
    )
    try:
        driver.get(options.url.rstrip('/') + '/browser/')
        _prepare_tree(driver, wait, options, options.database)
        forms = _catalog_forms(driver)
        if forms.get('__error__'):
            raise RuntimeError(forms['__error__'])
        alter_observation = _duckdb_alter_observation(
            driver, options.database
        )
        completed = _complete_cases(driver, wait, options)
        _refresh_tree(driver, wait, options, options.database)
        for mode in render_order:
            database_label = options.database if mode not in {
                'define', 'create'
            } else None
            rendered.append(_render_case(
                driver, wait, options, forms, mode, database_label
            ))
    except Exception as exc:
        failure_path = options.output_root / 'failure.png'
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(failure_path))
        failures.append({
            'error_type': type(exc).__name__, 'error': str(exc),
            'traceback': traceback.format_exc(),
            'screenshot': str(failure_path),
        })
    finally:
        driver.quit()
    passed_modes = {item['mode'] for item in rendered}
    completed_modes = {item['mode'] for item in completed}
    return {
        'schema': f'cdeadmin.{ENGINE_ID}-database-lifecycle-ui-gate.v1',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'engine_id': ENGINE_ID, 'interface_id': PROFILE_ID,
        'reference_version': (
            '3.53.0' if ENGINE_ID == 'sqlite' else '1.5.2'
        ),
        'viewport': f'{options.width}x{options.height}',
        'theme': options.theme,
        'font_scale': options.font_scale,
        'evidence_variant': evidence_variant(options),
        'expected_modes': list(expected_modes),
        'unsupported_alter_observation': alter_observation,
        'rendered': rendered, 'completed': completed,
        'cancelled_form_count': sum(
            item['cancellation']['dialog_dismissed'] for item in rendered
        ),
        'validation_observed_form_count': sum(
            item['validation']['state'] == 'observed' for item in rendered
        ),
        'validation_not_applicable_form_count': sum(
            item['validation']['state'] == 'not_applicable'
            for item in rendered
        ),
        'provider_plan_preview_count': sum(
            item.get('plan_state') == 'ready' for item in completed
        ),
        'completed_screenshot_count': sum(
            'completed' in item.get('screenshots', {}) for item in completed
        ),
        'failures': failures, 'credential_values_exported': False,
        'provider_values_recorded': False,
        'complete': (
            passed_modes == set(expected_modes) and
            completed_modes == set(expected_modes) and not failures
        ),
    }


def main(argv=None):
    options = arguments(argv)
    result = run(options)
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8'
    )
    print(json.dumps({
        'complete': result['complete'],
        'rendered_modes': [item['mode'] for item in result['rendered']],
        'completed_modes': [item['mode'] for item in result['completed']],
        'failures': [item['error'] for item in result['failures']],
        'output': str(options.summary_output),
    }, indent=2, sort_keys=True))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
