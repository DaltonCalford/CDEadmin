#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Verify MySQL 9.7 database properties rendered by the real browser UI."""

from __future__ import annotations

import argparse
import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

import mysql.connector
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

if __package__:
    from . import cdeadmin_sqlite_database_lifecycle_ui_gate as base
else:
    import cdeadmin_sqlite_database_lifecycle_ui_gate as base


REFERENCE_VERSION = '9.7.0'


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
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


def _native_properties(options):
    password = os.environ.get(options.endpoint_password_env)
    if not password:
        raise RuntimeError('MySQL properties password environment is empty')
    connection = mysql.connector.connect(
        host=options.host, port=options.port, user=options.user,
        password=password, database=options.database,
        connection_timeout=10, use_pure=True,
    )
    cursor = connection.cursor()
    try:
        cursor.execute(
            'SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME, '
            'DEFAULT_ENCRYPTION FROM information_schema.SCHEMATA '
            'WHERE SCHEMA_NAME = %s', (options.database,),
        )
        database = cursor.fetchone()
        if database is None:
            raise RuntimeError('MySQL database properties are unavailable')
        cursor.execute(
            'SELECT VERSION(), @@hostname, @@port, @@server_uuid, '
            '@@version_comment, @@version_compile_machine, '
            '@@version_compile_os, @@lower_case_table_names, '
            '@@default_storage_engine, @@transaction_isolation, '
            'CURRENT_USER(), USER()'
        )
        server = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()
    return {
        'database': dict(zip((
            'default_character_set', 'default_collation',
            'default_encryption',
        ), database)),
        'server': dict(zip((
            'version', 'hostname', 'port', 'server_uuid', 'version_comment',
            'version_compile_machine', 'version_compile_os',
            'lower_case_table_names', 'default_storage_engine',
            'transaction_isolation', 'current_user', 'session_user',
        ), server)),
    }


def _rendered_group(driver, title):
    return driver.execute_script(
        """
        const title = arguments[0];
        const section = [...document.querySelectorAll('section')].find(
          item => item.querySelector('h3')?.textContent.trim() === title
        );
        if (!section) return null;
        return Object.fromEntries([...section.querySelectorAll('dt')].map(
          term => [term.textContent.trim(),
            term.nextElementSibling?.textContent.trim() ?? null]
        ));
        """, title,
    )


def _rendered_value(value):
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (dict, list)) or value is None:
        return json.dumps(value, separators=(',', ':'))
    return str(value)


def _expected(values):
    return {
        key.replace('_', ' '): _rendered_value(value)
        for key, value in values.items()
    }


def _differences(rendered, native):
    return {
        key: {'rendered': rendered.get(key), 'native': native.get(key)}
        for key in sorted(set(rendered) | set(native))
        if rendered.get(key) != native.get(key)
    }


def run(options):
    base._configure_engine('MySQL')
    password = os.environ.get(options.endpoint_password_env)
    base.ENDPOINT_PASSWORD = password
    driver = base.create_driver(options)
    wait = WebDriverWait(driver, options.timeout)
    failure = None
    try:
        driver.get(options.url.rstrip('/') + '/browser/')
        base._prepare_tree(driver, wait, options, options.database)
        context = base._tree_context(driver, options.database)
        action = next((
            item for item in context['selected_actions']
            if item.get('command_id') == 'database.mysql.properties'
        ), None)
        if action is None or action.get('enabled') is False:
            raise RuntimeError(
                'MySQL database properties popup command is unavailable'
            )
        database = base.wait_for_tree_item(wait, options.database)
        base.invoke_context_action(
            wait, driver, database,
            ['Database workspace', action['label']],
            endpoint_password=password, endpoint_prompt_timeout=5,
        )
        wait.until(lambda value: any(
            item.is_displayed() and item.text == 'Database observations'
            for item in value.find_elements(By.TAG_NAME, 'h3')
        ))
        native = _native_properties(options)
        rendered_database = _rendered_group(
            driver, 'Database observations'
        )
        rendered_server = _rendered_group(driver, 'Server observations')
        expected_database = _expected(native['database'])
        expected_server = _expected(native['server'])
        differences = {
            'database': _differences(
                rendered_database or {}, expected_database
            ),
            'server': _differences(rendered_server or {}, expected_server),
        }
        if differences['database'] or differences['server']:
            raise RuntimeError(
                'rendered MySQL properties differ from independent native '
                'observations: ' + json.dumps(differences, sort_keys=True)
            )
        layout = base.layout_observation(driver)
        output = options.output_root / (
            f'database-properties-{options.width}x{options.height}-'
            f'{base.evidence_variant(options)}.png'
        )
        digest = base.screenshot(driver, output)
        return {
            'schema': 'cdeadmin.mysql-properties-ui-gate.v1',
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'engine_id': 'mysql', 'interface_id': 'mysql-native',
            'reference_version': REFERENCE_VERSION,
            'viewport': f'{options.width}x{options.height}',
            'theme': options.theme, 'font_scale': options.font_scale,
            'evidence_variant': base.evidence_variant(options),
            'command_id': action['command_id'],
            'database_property_count': len(expected_database),
            'server_property_count': len(expected_server),
            'all_properties_match_native_state': True,
            'connection_target_rendered': bool(_rendered_group(
                driver, 'Connection target'
            )),
            'verified_interface_rendered': bool(_rendered_group(
                driver, 'Verified engine interface'
            )),
            'credential_values_exported': False,
            'layout': layout,
            'screenshot': {'path': str(output), 'sha256': digest},
            'failures': [], 'complete': True,
        }
    except Exception as exc:
        output = options.output_root / 'failure.png'
        output.parent.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(output))
        failure = {
            'error_type': type(exc).__name__, 'error': str(exc),
            'traceback': traceback.format_exc(), 'screenshot': str(output),
        }
        return {
            'schema': 'cdeadmin.mysql-properties-ui-gate.v1',
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'engine_id': 'mysql', 'interface_id': 'mysql-native',
            'reference_version': REFERENCE_VERSION,
            'credential_values_exported': False,
            'failures': [failure], 'complete': False,
        }
    finally:
        driver.quit()


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
        'failures': [item['error'] for item in result['failures']],
        'output': str(options.summary_output),
    }, indent=2, sort_keys=True))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
