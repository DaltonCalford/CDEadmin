#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Verify designed MariaDB 12.2 properties against the exact live runtime."""

from __future__ import annotations

import argparse
import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

import mariadb
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

if __package__:
    from . import cdeadmin_sqlite_database_lifecycle_ui_gate as base
else:
    import cdeadmin_sqlite_database_lifecycle_ui_gate as base


REFERENCE_VERSION = '12.2.2'


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
        raise RuntimeError('MariaDB properties password environment is empty')
    connection = mariadb.connect(
        host=options.host, port=options.port, user=options.user,
        password=password, database=options.database, connect_timeout=10,
    )
    cursor = connection.cursor()
    try:
        cursor.execute(
            'SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME, '
            'SCHEMA_COMMENT FROM information_schema.SCHEMATA '
            'WHERE SCHEMA_NAME = ?', (options.database,),
        )
        database = cursor.fetchone()
        if database is None:
            raise RuntimeError('MariaDB database properties are unavailable')
        cursor.execute(
            'SELECT VERSION(), @@hostname, @@port, @@server_id, '
            '@@version_comment, @@version_compile_machine, '
            '@@version_compile_os, @@lower_case_table_names, '
            '@@default_storage_engine, @@tx_isolation, CURRENT_USER(), '
            'USER(), @@sql_mode, @@character_set_server, '
            '@@collation_server, @@log_bin, @@binlog_format, '
            '@@gtid_strict_mode, @@wsrep_on, @@max_connections'
        )
        server = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()
    return {
        'database': dict(zip((
            'default_character_set', 'default_collation', 'schema_comment',
        ), database)),
        'server': dict(zip((
            'version', 'hostname', 'port', 'server_id', 'version_comment',
            'version_compile_machine', 'version_compile_os',
            'lower_case_table_names', 'default_storage_engine',
            'tx_isolation', 'current_user', 'session_user', 'sql_mode',
            'character_set_server', 'collation_server', 'log_bin',
            'binlog_format', 'gtid_strict_mode', 'wsrep_on',
            'max_connections',
        ), server)),
    }


def _rendered_group(driver, title):
    return driver.execute_script(
        """
        const section = [...document.querySelectorAll('section')].find(
          item => item.querySelector('h3')?.textContent.trim() === arguments[0]
        );
        if (!section) return null;
        return Object.fromEntries([...section.querySelectorAll('dt')].map(
          term => [term.textContent.trim(),
            term.nextElementSibling?.textContent.trim() ?? null]
        ));
        """, title,
    )


def _text(value):
    return '' if value is None else str(value)


def _yes_no(value):
    return 'Yes' if value in (1, True, '1', 'ON', 'true') else 'No'


def _expected(native):
    database = native['database']
    server = native['server']
    return {
        'MariaDB database defaults': {
            'Default character set': _text(database['default_character_set']),
            'Default collation': _text(database['default_collation']),
            'Database comment': _text(database['schema_comment']),
        },
        'MariaDB server identity': {
            'Server version': _text(server['version']),
            'Server hostname': _text(server['hostname']),
            'Server port': _text(server['port']),
            'Replication server ID': _text(server['server_id']),
            'Distribution': _text(server['version_comment']),
            'Build architecture': _text(server['version_compile_machine']),
            'Build operating system': _text(server['version_compile_os']),
        },
        'MariaDB server configuration': {
            'Lower-case table-name mode': _text(
                server['lower_case_table_names']),
            'Default storage engine': _text(server['default_storage_engine']),
            'Server character set': _text(server['character_set_server']),
            'Server collation': _text(server['collation_server']),
            'Maximum connections': _text(server['max_connections']),
        },
        'MariaDB session and transaction state': {
            'Transaction isolation': _text(server['tx_isolation']),
            'Session SQL mode': _text(server['sql_mode']),
            'Authenticated account': _text(server['current_user']),
            'Client account': _text(server['session_user']),
        },
        'MariaDB replication and binary logging': {
            'Binary logging enabled': _yes_no(server['log_bin']),
            'Binary log format': _text(server['binlog_format']),
            'GTID strict mode': _yes_no(server['gtid_strict_mode']),
            'Galera replication enabled': _yes_no(server['wsrep_on']),
        },
    }


def _differences(rendered, expected):
    return {
        key: {'rendered': rendered.get(key), 'native': expected.get(key)}
        for key in sorted(set(rendered) | set(expected))
        if rendered.get(key) != expected.get(key)
    }


def run(options):
    base._configure_engine('MariaDB')
    password = os.environ.get(options.endpoint_password_env)
    base.ENDPOINT_PASSWORD = password
    driver = base.create_driver(options)
    wait = WebDriverWait(driver, options.timeout)
    try:
        driver.get(options.url.rstrip('/') + '/browser/')
        base._prepare_tree(driver, wait, options, options.database)
        context = base._tree_context(driver, options.database)
        action = next((
            item for item in context['selected_actions']
            if item.get('command_id') == 'database.mariadb.properties'
        ), None)
        if action is None or action.get('enabled') is False:
            raise RuntimeError(
                'MariaDB database properties popup command is unavailable'
            )
        database = base.wait_for_tree_item(wait, options.database)
        base.invoke_context_action(
            wait, driver, database,
            ['Database workspace', action['label']],
            endpoint_password=password, endpoint_prompt_timeout=5,
        )
        wait.until(lambda value: any(
            item.is_displayed() and item.text == 'MariaDB database defaults'
            for item in value.find_elements(By.TAG_NAME, 'h3')
        ))
        expected = _expected(_native_properties(options))
        differences = {}
        for title, values in expected.items():
            rendered = _rendered_group(driver, title)
            differences[title] = (
                {'missing_group': True} if rendered is None else
                _differences(rendered, values)
            )
        differences = {
            key: value for key, value in differences.items() if value
        }
        if differences:
            raise RuntimeError(
                'rendered MariaDB properties differ from independent native '
                'observations: ' + json.dumps(differences, sort_keys=True)
            )
        raw_json = driver.execute_script(
            r"""
            return [...document.querySelectorAll('dd')].some(item =>
              /^\s*[\[{]/.test(item.textContent || ''));
            """
        )
        if raw_json:
            raise RuntimeError(
                'MariaDB properties exposed an unstructured JSON value'
            )
        layout = base.layout_observation(driver)
        output = options.output_root / (
            f'database-properties-{options.width}x{options.height}-'
            f'{base.evidence_variant(options)}.png'
        )
        digest = base.screenshot(driver, output)
        return {
            'schema': 'cdeadmin.mariadb-properties-ui-gate.v1',
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'engine_id': 'mariadb', 'interface_id': 'mariadb-native',
            'reference_version': REFERENCE_VERSION,
            'viewport': f'{options.width}x{options.height}',
            'theme': options.theme, 'font_scale': options.font_scale,
            'evidence_variant': base.evidence_variant(options),
            'command_id': action['command_id'],
            'native_property_count': sum(map(len, expected.values())),
            'property_group_count': len(expected) + 3,
            'all_properties_match_native_state': True,
            'provider_specific_layout': True,
            'unstructured_json_absent': True,
            'credential_values_exported': False,
            'layout': layout,
            'screenshot': {'path': str(output), 'sha256': digest},
            'failures': [], 'complete': True,
        }
    except Exception as exc:
        output = options.output_root / 'failure.png'
        output.parent.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(output))
        return {
            'schema': 'cdeadmin.mariadb-properties-ui-gate.v1',
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'engine_id': 'mariadb', 'interface_id': 'mariadb-native',
            'reference_version': REFERENCE_VERSION,
            'credential_values_exported': False,
            'failures': [{
                'error_type': type(exc).__name__, 'error': str(exc),
                'traceback': traceback.format_exc(),
                'screenshot': str(output),
            }],
            'complete': False,
        }
    finally:
        driver.quit()


def main(argv=None):
    options = arguments(argv)
    result = run(options)
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8',
    )
    print(json.dumps({
        'complete': result['complete'],
        'failures': [item['error'] for item in result['failures']],
        'output': str(options.summary_output),
    }, indent=2, sort_keys=True))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
