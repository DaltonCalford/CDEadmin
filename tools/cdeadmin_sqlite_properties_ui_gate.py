#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Verify rendered SQLite or DuckDB properties against native state."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cdeadmin_firebird_ui_form_gate import (  # noqa: E402
    create_driver,
    evidence_variant,
    layout_observation,
    prepare_tree,
    screenshot,
)
from tools.cdeadmin_ui_evidence import (  # noqa: E402
    invoke_context_action,
    wait_for_tree_item,
)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--engine', default='SQLite')
    parser.add_argument('--server', default='localhost')
    parser.add_argument('--database', required=True)
    parser.add_argument('--database-path', type=Path, required=True)
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


def _sqlite_native_properties(database):
    names = (
        'encoding', 'page_size', 'page_count', 'freelist_count',
        'max_page_count', 'auto_vacuum', 'journal_mode', 'synchronous',
        'locking_mode', 'secure_delete', 'application_id', 'user_version',
        'schema_version',
    )
    with sqlite3.connect(database) as connection:
        observed = {
            name: connection.execute(
                f'PRAGMA "main".{name}'
            ).fetchone()[0]
            for name in names
        }
        runtime = connection.execute(
            'SELECT sqlite_version(), sqlite_source_id()'
        ).fetchone()
    return {
        'database_name': str(database),
        'schema_name': 'main',
        'attachment_sequence': 0,
        'encoding': observed['encoding'],
        'page_size_bytes': observed['page_size'],
        'page_count': observed['page_count'],
        'logical_size_bytes': (
            observed['page_size'] * observed['page_count']
        ),
        'physical_file_bytes': database.stat().st_size,
        'free_list_pages': observed['freelist_count'],
        'maximum_page_count': observed['max_page_count'],
        'auto_vacuum_code': observed['auto_vacuum'],
        'journal_mode': observed['journal_mode'],
        'synchronous_code': observed['synchronous'],
        'locking_mode': observed['locking_mode'],
        'secure_delete_code': observed['secure_delete'],
        'application_id': observed['application_id'],
        'user_version': observed['user_version'],
        'schema_version': observed['schema_version'],
        'sqlite_runtime_version': runtime[0],
        'sqlite_source_id': runtime[1],
    }


def _duckdb_native_properties(database):
    with duckdb.connect(str(database)) as connection:
        current_database = connection.execute(
            'SELECT current_database()'
        ).fetchone()[0]
        size = connection.execute(
            'SELECT database_size, block_size, total_blocks, used_blocks, '
            'free_blocks, wal_size, memory_usage, memory_limit '
            'FROM pragma_database_size() WHERE database_name = ?',
            [current_database],
        ).fetchone()
        row = connection.execute(
            'SELECT database_oid, path, comment, tags, type, readonly, '
            'encrypted, cipher, options FROM duckdb_databases() '
            'WHERE database_name = ?', [current_database],
        ).fetchone()
        runtime_version = connection.execute(
            'SELECT version()'
        ).fetchone()[0]
        platform = connection.execute('PRAGMA platform').fetchone()[0]
    return {
        'database_name': current_database,
        'path': row[1],
        'database_oid': row[0],
        'comment': row[2],
        'tags': row[3],
        'database_type': row[4],
        'read_only': bool(row[5]),
        'encrypted': bool(row[6]),
        'cipher': row[7],
        'options': row[8],
        'runtime_version': runtime_version,
        'platform': platform,
        'database_size': size[0],
        'block_size': size[1],
        'total_blocks': size[2],
        'used_blocks': size[3],
        'free_blocks': size[4],
        'wal_size': size[5],
        'memory_usage': size[6],
        'memory_limit': size[7],
        'file_bytes': database.stat().st_size,
    }


def _engine_identity(engine):
    if engine == 'SQLite':
        return 'sqlite', 'sqlite-native', '3.53.0'
    if engine == 'DuckDB':
        return 'duckdb', 'duckdb-native', '1.5.2'
    raise RuntimeError(f'unsupported properties gate engine: {engine}')


def _rendered_value(value):
    if isinstance(value, (dict, list)) or value is None:
        return json.dumps(value, separators=(',', ':'))
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _rendered_group(driver, title):
    return driver.execute_script(
        """
        const title = arguments[0];
        const section = [...document.querySelectorAll('section')].find(
          item => item.querySelector('h3')?.textContent.trim() === title
        );
        if (!section) return null;
        const terms = [...section.querySelectorAll('dt')];
        return Object.fromEntries(terms.map(term => [
          term.textContent.trim(),
          term.nextElementSibling?.textContent.trim() ?? null,
        ]));
        """,
        title,
    )


def run(options):
    if not options.database_path.is_file():
        raise RuntimeError(
            f'isolated {options.engine} database is missing'
        )
    engine_id, interface_id, reference_version = _engine_identity(
        options.engine
    )
    if options.engine == 'SQLite' and sqlite3.sqlite_version != '3.53.0':
        raise RuntimeError('properties gate is not linked to SQLite 3.53.0')
    if options.engine == 'DuckDB' and duckdb.__version__ != '1.5.2':
        raise RuntimeError('properties gate is not linked to DuckDB 1.5.2')
    driver = create_driver(options)
    wait = WebDriverWait(driver, options.timeout)
    try:
        actions = prepare_tree(driver, wait, options, '')
        action = next((
            item for item in actions
            if item['command_id'] == f'database.{engine_id}.properties'
        ), None)
        if action is None or action.get('enabled') is False:
            raise RuntimeError(
                f'{options.engine} database properties popup command is '
                'unavailable'
            )
        database = wait_for_tree_item(wait, options.database)
        invoke_context_action(
            wait, driver, database,
            ['Database workspace', action['label']], '',
            endpoint_prompt_timeout=1,
        )
        try:
            wait.until(lambda value: any(
                item.is_displayed() and item.text == 'Database observations'
                for item in value.find_elements(By.TAG_NAME, 'h3')
            ))
        except TimeoutException as exc:
            failure = options.output_root / (
                f'database-properties-load-failure-{options.width}x'
                f'{options.height}-{evidence_variant(options)}.png'
            )
            screenshot(driver, failure)
            body = driver.find_element(By.TAG_NAME, 'body').text
            try:
                console = driver.get_log('browser')
            except Exception:
                console = []
            raise RuntimeError(
                'database properties did not finish loading: ' +
                json.dumps({
                    'url': driver.current_url,
                    'visible_text': body[-4000:],
                    'console': console[-20:],
                    'screenshot': str(failure),
                }, sort_keys=True)
            ) from exc
        layout = layout_observation(driver)
        rendered = wait.until(
            lambda value: _rendered_group(value, 'Database observations')
        )
        native = (
            _sqlite_native_properties(options.database_path)
            if options.engine == 'SQLite'
            else _duckdb_native_properties(options.database_path)
        )
        expected = {
            key.replace('_', ' '): _rendered_value(value)
            for key, value in native.items()
        }
        volatile_names = (
            {'memory usage'} if options.engine == 'DuckDB' else set()
        )
        volatile = {
            key: {'rendered': rendered.get(key), 'native': expected.get(key)}
            for key in volatile_names
        }
        stable_rendered = {
            key: value for key, value in rendered.items()
            if key not in volatile_names
        }
        stable_expected = {
            key: value for key, value in expected.items()
            if key not in volatile_names
        }
        if stable_rendered != stable_expected:
            differing = {
                key: {
                    'rendered': stable_rendered.get(key),
                    'native': stable_expected.get(key),
                }
                for key in sorted(
                    set(stable_rendered) | set(stable_expected)
                )
                if stable_rendered.get(key) != stable_expected.get(key)
            }
            raise RuntimeError(
                f'rendered {options.engine} database properties differ from '
                'the direct native observations: ' +
                json.dumps(differing, sort_keys=True)
            )
        if any(not values['rendered'] or not values['native']
               for values in volatile.values()):
            raise RuntimeError(
                'volatile DuckDB properties were not observed by both probes'
            )
        connection_target = _rendered_group(driver, 'Connection target')
        engine_interface = _rendered_group(
            driver, 'Verified engine interface'
        )
        if not connection_target or not engine_interface:
            raise RuntimeError(
                'database properties omitted connection or interface identity'
            )
        output = options.output_root / (
            f'database-properties-{options.width}x{options.height}-'
            f'{evidence_variant(options)}.png'
        )
        digest = screenshot(driver, output)
        return {
            'schema': f'cdeadmin.{engine_id}-properties-ui-gate.v1',
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'engine_id': engine_id,
            'interface_id': interface_id,
            'reference_version': reference_version,
            'viewport': f'{options.width}x{options.height}',
            'theme': options.theme,
            'font_scale': options.font_scale,
            'evidence_variant': evidence_variant(options),
            'command_id': action['command_id'],
            'database_property_count': len(expected),
            'stable_database_property_count': len(stable_expected),
            'all_properties_match_native_state': not volatile,
            'all_stable_properties_match_native_state': True,
            'volatile_observations': volatile,
            'connection_target_rendered': True,
            'verified_interface_rendered': True,
            'credential_values_exported': False,
            'layout': layout,
            'screenshot': {
                'path': str(output), 'sha256': digest,
            },
            'passed': True,
        }
    finally:
        driver.quit()


def main(argv=None):
    options = arguments(argv)
    options.summary_output.unlink(missing_ok=True)
    result = run(options)
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps({
        'passed': result['passed'],
        'database_property_count': result['database_property_count'],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
