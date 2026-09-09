#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Certify every MariaDB 12.2.2 database-target form in the browser UI."""

from __future__ import annotations

import argparse
import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

import mariadb
from selenium.webdriver.support.ui import WebDriverWait

if __package__:
    from . import cdeadmin_sqlite_database_lifecycle_ui_gate as base
    from .cdeadmin_provider_object_form_gate import (
        _prepare_tree as prepare_provider_tree,
        _quit_driver,
    )
else:
    import cdeadmin_sqlite_database_lifecycle_ui_gate as base
    from cdeadmin_provider_object_form_gate import (
        _prepare_tree as prepare_provider_tree,
        _quit_driver,
    )


REFERENCE_VERSION = '12.2.2'
REGISTERED_DATABASE = 'cdeadmin_ui_registered'
CREATED_DATABASE = 'cdeadmin_ui_created'
CHARACTER_SET = 'utf8mb4'
COLLATION = 'utf8mb4_uca1400_ai_ci'


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--config-db', type=Path, required=True)
    parser.add_argument('--database', default='cdeadmin_demo')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--user', default='root')
    parser.add_argument('--endpoint-password-env', required=True)
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


def _connect(options):
    password = os.environ.get(options.endpoint_password_env)
    if not password:
        raise RuntimeError('MariaDB lifecycle password environment is empty')
    return mariadb.connect(
        host=options.host, port=options.port, user=options.user,
        password=password, connect_timeout=10,
    )


def _execute(options, source):
    connection = _connect(options)
    connection.autocommit = True
    cursor = connection.cursor()
    try:
        cursor.execute(source)
    finally:
        cursor.close()
        connection.close()


def _database_state(options, database):
    connection = _connect(options)
    cursor = connection.cursor()
    try:
        cursor.execute(
            'SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME, '
            'SCHEMA_COMMENT FROM information_schema.SCHEMATA '
            'WHERE SCHEMA_NAME = ?', (database,),
        )
        row = cursor.fetchone()
        if row is None:
            return {'exists': False}
        cursor.execute(f'SHOW CREATE DATABASE `{database}`')
        source = str(cursor.fetchone()[1])
        return {
            'exists': True,
            'character_set': str(row[0]),
            'collation': str(row[1]),
            'comment': str(row[2]),
            'show_create_has_comment': 'COMMENT' in source,
        }
    finally:
        cursor.close()
        connection.close()


def _target(options, database):
    return next((
        item for item in base._target_rows(options.config_db)
        if item['database'] == database
    ), None)


def _refresh_tree(driver, wait, options, database):
    """Refresh the MariaDB tree and reauthenticate the browser session."""
    base._refresh_tree(driver, wait, options, database)
    base.complete_endpoint_prompt(
        driver, base.ENDPOINT_PASSWORD, timeout=min(options.timeout, 15),
    )


def _complete_cases(driver, wait, options):
    _execute(
        options,
        f'CREATE DATABASE IF NOT EXISTS `{REGISTERED_DATABASE}` '
        f'DEFAULT CHARACTER SET {CHARACTER_SET} '
        f'DEFAULT COLLATE {COLLATION} COMMENT = \'pre-existing\'',
    )
    _execute(options, f'DROP DATABASE IF EXISTS `{CREATED_DATABASE}`')
    evidence = []

    base._open_form(driver, wait, 'connect', options.database)
    base._submit_target_form(driver, wait, 'connect', {
        'Default character set': CHARACTER_SET,
        'Default collation': COLLATION,
    })
    base._wait_for_native_state(
        wait,
        lambda: bool(_target(options, options.database) and
                     _target(options, options.database)['active']),
        'connect did not activate the retained MariaDB database',
    )
    evidence.append({
        'mode': 'connect', 'active_target_observed': True,
        'preview_state': 'not_applicable',
    })
    base._capture_completed(driver, options, 'connect', evidence[-1])
    base._close(driver, wait)

    base._open_form(driver, wait, 'edit', options.database)
    base._submit_target_form(driver, wait, 'edit', {
        'Navigator display name': options.database,
        'Default character set': CHARACTER_SET,
        'Default collation': COLLATION,
        'Session SQL mode': 'STRICT_TRANS_TABLES',
    })
    base._wait_for_native_state(
        wait,
        lambda: bool(_target(options, options.database) and _target(
            options, options.database
        )['configuration'].get('sql_mode') == 'STRICT_TRANS_TABLES'),
        'edit did not retain MariaDB target configuration',
    )
    evidence.append({
        'mode': 'edit', 'connection_options_observed': True,
        'preview_state': 'not_applicable',
    })
    base._capture_completed(driver, options, 'edit', evidence[-1])
    base._close(driver, wait)

    base._open_form(driver, wait, 'define')
    base._submit_target_form(driver, wait, 'define', {
        'MariaDB database name': REGISTERED_DATABASE,
        'Navigator display name': REGISTERED_DATABASE,
        'Default character set': CHARACTER_SET,
        'Default collation': COLLATION,
    })
    base._wait_for_native_state(
        wait, lambda: _target(options, REGISTERED_DATABASE) is not None,
        'define did not retain the existing MariaDB database',
    )
    if not _database_state(options, REGISTERED_DATABASE)['exists']:
        raise RuntimeError('define changed or removed the native database')
    evidence.append({
        'mode': 'define', 'retained_target_observed': True,
        'native_database_preserved': True,
        'preview_state': 'not_applicable',
    })
    base._capture_completed(driver, options, 'define', evidence[-1])
    base._close(driver, wait)

    _refresh_tree(driver, wait, options, REGISTERED_DATABASE)
    base._open_form(driver, wait, 'remove', REGISTERED_DATABASE)
    base._submit_target_form(driver, wait, 'remove', {
        'Type the native name to confirm': REGISTERED_DATABASE,
    })
    base._wait_for_native_state(
        wait, lambda: _target(options, REGISTERED_DATABASE) is None,
        'remove retained the MariaDB target registration',
    )
    if not _database_state(options, REGISTERED_DATABASE)['exists']:
        raise RuntimeError('remove dropped the native MariaDB database')
    evidence.append({
        'mode': 'remove', 'target_removed': True,
        'native_database_preserved': True,
        'preview_state': 'not_applicable',
    })
    base._capture_completed(driver, options, 'remove', evidence[-1])
    base._close(driver, wait)

    _refresh_tree(driver, wait, options, options.database)
    base._open_form(driver, wait, 'create')
    lifecycle = base._submit_lifecycle(driver, wait, options, 'create', {
        'MariaDB database name': CREATED_DATABASE,
        'Default character set': CHARACTER_SET,
        'Default collation': COLLATION,
        'Database comment': 'created through CDEadmin lifecycle QA',
    })
    state = _database_state(options, CREATED_DATABASE)
    if not state['exists'] or state['character_set'] != CHARACTER_SET or (
            state['collation'] != COLLATION or
            state['comment'] != 'created through CDEadmin lifecycle QA'):
        raise RuntimeError('created MariaDB database differs from its plan')
    if _target(options, CREATED_DATABASE) is None:
        raise RuntimeError('created MariaDB database was not retained')
    evidence.append({
        'mode': 'create', **lifecycle, 'native_state_observed': True,
        'retained_target_observed': True,
    })
    base._capture_completed(driver, options, 'create', evidence[-1])
    base._close(driver, wait)

    _refresh_tree(driver, wait, options, CREATED_DATABASE)
    base._open_form(driver, wait, 'alter', CREATED_DATABASE)
    lifecycle = base._submit_lifecycle(driver, wait, options, 'alter', {
        'Default character set': CHARACTER_SET,
        'Default collation': COLLATION,
        'Database comment': 'altered through CDEadmin lifecycle QA',
    })
    state = _database_state(options, CREATED_DATABASE)
    if not state['exists'] or (
            state['comment'] != 'altered through CDEadmin lifecycle QA'):
        raise RuntimeError('alter did not change the MariaDB comment')
    evidence.append({
        'mode': 'alter', **lifecycle, 'native_state_observed': True,
    })
    base._capture_completed(driver, options, 'alter', evidence[-1])
    base._close(driver, wait)

    _refresh_tree(driver, wait, options, CREATED_DATABASE)
    base._open_form(driver, wait, 'drop', CREATED_DATABASE)
    lifecycle = base._submit_lifecycle(driver, wait, options, 'drop', {
        'Type the exact MariaDB database name to confirm': CREATED_DATABASE,
    })
    if _database_state(options, CREATED_DATABASE)['exists']:
        raise RuntimeError('drop retained the native MariaDB database')
    if _target(options, CREATED_DATABASE) is not None:
        raise RuntimeError('drop retained the deleted MariaDB target')
    evidence.append({
        'mode': 'drop', **lifecycle, 'native_database_absent': True,
        'target_removed': True,
    })
    base._capture_completed(driver, options, 'drop', evidence[-1])
    base._close(driver, wait)
    return evidence


def run(options):
    base._configure_engine('MariaDB')
    base.ENDPOINT_PASSWORD = os.environ.get(options.endpoint_password_env)
    # Use the public provider workspace path to authenticate the endpoint.
    # The older lifecycle harness called a private verification callback that
    # does not establish the browser-side provider session required by these
    # forms.  These values only describe the real tree path to the generic
    # navigator helper; form ownership remains entirely with MariaDB.
    options.engine = 'MariaDB'
    options.server = 'localhost'
    driver = base.create_driver(options)
    driver.set_script_timeout(max(120, options.timeout * 10))
    wait = WebDriverWait(driver, options.timeout)
    rendered = []
    completed = []
    failures = []
    try:
        driver.get(options.url.rstrip('/') + '/browser/')
        prepare_provider_tree(driver, wait, options)
        forms = base._catalog_forms(driver)
        if forms.get('__error__'):
            raise RuntimeError(forms['__error__'])
        completed = _complete_cases(driver, wait, options)
        _refresh_tree(driver, wait, options, options.database)
        for mode in base.RENDER_ORDER:
            database_label = (
                options.database if mode not in {'define', 'create'} else None
            )
            rendered.append(base._render_case(
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
        try:
            _execute(options, f'DROP DATABASE IF EXISTS `{CREATED_DATABASE}`')
            _execute(
                options,
                f'DROP DATABASE IF EXISTS `{REGISTERED_DATABASE}`',
            )
        except Exception:
            pass
        _quit_driver(driver)
    expected = set(base.COMPLETION_ORDER)
    return {
        'schema': 'cdeadmin.mariadb-database-lifecycle-ui-gate.v1',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'engine_id': 'mariadb', 'interface_id': 'mariadb-native',
        'reference_version': REFERENCE_VERSION,
        'viewport': f'{options.width}x{options.height}',
        'theme': options.theme, 'font_scale': options.font_scale,
        'evidence_variant': base.evidence_variant(options),
        'expected_modes': list(base.COMPLETION_ORDER),
        'rendered': rendered, 'completed': completed,
        'cancelled_form_count': sum(
            item['cancellation']['dialog_dismissed'] for item in rendered
        ),
        'validation_observed_form_count': sum(
            item['validation']['state'] == 'observed' for item in rendered
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
            {item['mode'] for item in rendered} == expected and
            {item['mode'] for item in completed} == expected and
            not failures
        ),
    }


def main(argv=None):
    options = arguments(argv)
    result = run(options)
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
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
