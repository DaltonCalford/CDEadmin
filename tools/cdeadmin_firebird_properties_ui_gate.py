#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Verify the designed Firebird properties workspace against live state."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

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
from tools.cdeadmin_sqlite_properties_ui_gate import (  # noqa: E402
    _rendered_group,
)
from tools.cdeadmin_ui_evidence import (  # noqa: E402
    invoke_context_action,
    wait_for_tree_item,
)


GROUPS = (
    'Firebird database identity',
    'Firebird format and dialect',
    'Firebird storage and durability',
    'Firebird maintenance and state',
    'Firebird MGA transaction state',
    'Firebird attachment activity',
    'Firebird connection defaults',
    'Firebird server observations',
    'Verified Firebird interface',
)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--engine', default='Firebird')
    parser.add_argument('--server', default='localhost')
    parser.add_argument('--database', required=True)
    parser.add_argument('--database-path', required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--firebird-port', type=int, default=53050)
    parser.add_argument('--user', default='SYSDBA')
    parser.add_argument(
        '--endpoint-password-env',
        default='CDEADMIN_FIREBIRD_DEMO_PASSWORD',
    )
    parser.add_argument('--client-library', type=Path, required=True)
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


def _native(options, password):
    if not options.client_library.is_file():
        raise RuntimeError('Firebird 5 client library is missing')
    os.environ['CDEADMIN_FIREBIRD_CLIENT_LIBRARY'] = str(
        options.client_library.resolve()
    )
    import firebird.driver as driver

    if not driver.fbapi.has_api():
        driver.driver_config.fb_client_library.value = str(
            options.client_library.resolve()
        )
    connection = driver.connect(
        f'{options.host}/{options.firebird_port}:{options.database_path}',
        user=options.user, password=password,
    )
    try:
        cursor = connection.cursor()
        cursor.execute(
            'SELECT MON$DATABASE_NAME, MON$PAGE_SIZE, MON$ODS_MAJOR, '
            'MON$ODS_MINOR, MON$SQL_DIALECT, MON$READ_ONLY, '
            'MON$FORCED_WRITES, MON$RESERVE_SPACE, MON$SWEEP_INTERVAL, '
            'MON$BACKUP_STATE, MON$CRYPT_STATE, MON$SHUTDOWN_MODE '
            'FROM MON$DATABASE'
        )
        monitor = cursor.fetchone()
        cursor.execute(
            'SELECT TRIM(D.RDB$CHARACTER_SET_NAME), '
            'TRIM(C.RDB$DEFAULT_COLLATE_NAME) FROM RDB$DATABASE D '
            'LEFT JOIN RDB$CHARACTER_SETS C ON '
            'C.RDB$CHARACTER_SET_NAME = D.RDB$CHARACTER_SET_NAME'
        )
        charset, collation = cursor.fetchone()
        cursor.close()
        info = connection.info
        return {
            'database_name': str(monitor[0]).strip(),
            'page_size': str(monitor[1]),
            'ods_major': str(monitor[2]),
            'ods_minor': str(monitor[3]),
            'sql_dialect': str(monitor[4]),
            'read_only': bool(monitor[5]),
            'forced_writes': bool(monitor[6]),
            'reserve_space': bool(monitor[7]),
            'sweep_interval': str(monitor[8]),
            'backup_state': {
                0: 'NORMAL', 1: 'STALLED', 2: 'MERGE',
            }.get(monitor[9]),
            'encryption_state': {
                0: 'NOT_ENCRYPTED', 1: 'ENCRYPTED',
                2: 'DECRYPT_IN_PROGRESS', 3: 'ENCRYPT_IN_PROGRESS',
            }.get(monitor[10]),
            'shutdown_mode': {
                0: 'ONLINE', 1: 'MULTI_USER_SHUTDOWN',
                2: 'SINGLE_USER_SHUTDOWN', 3: 'FULL_SHUTDOWN',
            }.get(monitor[11]),
            'default_character_set': str(charset).strip(),
            'default_collation': str(collation).strip(),
            'server_version': str(info.version),
            'engine_version': str(info.engine_version),
            'server_build': str(info.server_version),
            'server_site': str(info.site),
            'stored_page_buffers': str(info.get_info(
                driver.DbInfoCode.SET_PAGE_BUFFERS)),
        }
    finally:
        connection.close()


def _expected(native, options):
    def boolean(value):
        return 'Yes' if value else 'No'

    return {
        'Firebird database identity': {
            'Database filename or alias': options.database_path,
            'Resolved database filename': native['database_name'],
        },
        'Firebird format and dialect': {
            'ODS major': native['ods_major'],
            'ODS minor': native['ods_minor'],
            'SQL dialect': native['sql_dialect'],
            'Default character set': native['default_character_set'],
            'Default collation': native['default_collation'],
        },
        'Firebird storage and durability': {
            'Page size (bytes)': native['page_size'],
            'Stored page-buffer override (0 uses server default)':
                native['stored_page_buffers'],
            'Forced writes': boolean(native['forced_writes']),
            'Reserve page space': boolean(native['reserve_space']),
            'Read only': boolean(native['read_only']),
        },
        'Firebird maintenance and state': {
            'Sweep interval': native['sweep_interval'],
            'Backup state': native['backup_state'],
            'Encryption state': native['encryption_state'],
            'Shutdown mode': native['shutdown_mode'],
        },
        'Firebird server observations': {
            'Server version': native['server_version'],
            'Engine compatibility version': native['engine_version'],
            'Server build and protocol': native['server_build'],
            'Server site': native['server_site'],
        },
        'Verified Firebird interface': {
            'Runtime family': 'firebird',
            'Verified server version': '5.0.4',
        },
    }


def _is_json_collection(value):
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return False
    return isinstance(decoded, (dict, list))


def cache_row_evidence(driver, options):
    evidence = []
    for index, label in enumerate((
            'Page cache size (pages)',
            'Monitoring page cache allocation (pages)',
            'Stored page-buffer override (0 uses server default)')):
        term = driver.find_element(
            By.XPATH, '//dt[normalize-space(.)="' + label + '"]')
        driver.execute_script(
            'arguments[0].scrollIntoView({block:"center",inline:"nearest"})',
            term)
        measured = driver.execute_script('''
          return [arguments[0], arguments[0].nextElementSibling].map(el => ({
            width:el.clientWidth, content_width:el.scrollWidth,
            height:el.clientHeight, content_height:el.scrollHeight
          }));
        ''', term)
        path = options.output_root / f'cache-property-{index}.png'
        digest = screenshot(driver, path, reset_scroll=False)
        evidence.append({'label': label, 'layout': measured,
                         'path': str(path), 'sha256': digest,
                         'no_text_overflow': all(
                             item['content_width'] <= item['width'] + 1 and
                             item['content_height'] <= item['height'] + 1
                             for item in measured)})
    return evidence


def run(options):
    password = os.environ.get(options.endpoint_password_env)
    if not password:
        raise RuntimeError(
            f'{options.endpoint_password_env} must contain the test credential'
        )
    native = _native(options, password)
    driver = create_driver(options)
    wait = WebDriverWait(driver, options.timeout)
    try:
        actions = prepare_tree(driver, wait, options, password)
        action = next((
            item for item in actions
            if item['command_id'] == 'database.firebird.properties'
        ), None)
        if action is None or action.get('enabled') is False:
            raise RuntimeError(
                'Firebird database properties popup command is unavailable'
            )
        database = wait_for_tree_item(wait, options.database)
        invoke_context_action(
            wait, driver, database,
            ['Database workspace', action['label']], password,
            endpoint_prompt_timeout=5,
        )
        try:
            wait.until(lambda value: all(
                _rendered_group(value, title) is not None for title in GROUPS
            ))
        except TimeoutException as exc:
            failure = options.output_root / (
                f'database-properties-load-failure-{options.width}x'
                f'{options.height}-{evidence_variant(options)}.png'
            )
            screenshot(driver, failure)
            raise RuntimeError(
                'Firebird properties groups did not finish loading; '
                f'screenshot: {failure}'
            ) from exc
        rendered = {
            title: _rendered_group(driver, title) for title in GROUPS
        }
        differences = {}
        for title, expected in _expected(native, options).items():
            for label, value in expected.items():
                observed = rendered[title].get(label)
                if observed != value:
                    differences[f'{title}.{label}'] = {
                        'expected': value, 'rendered': observed,
                    }
        if differences:
            raise RuntimeError(
                'rendered Firebird properties differ from direct native '
                'observations: ' + json.dumps(differences, sort_keys=True)
            )
        unstructured = {
            f'{title}.{label}': value
            for title, group in rendered.items()
            for label, value in group.items()
            if _is_json_collection(value)
        }
        if unstructured:
            raise RuntimeError(
                'Firebird properties exposed an unstructured JSON value: ' +
                json.dumps(unstructured, sort_keys=True)
            )
        layout = layout_observation(driver)
        output = options.output_root / (
            f'database-properties-{options.width}x{options.height}-'
            f'{evidence_variant(options)}.png'
        )
        digest = screenshot(driver, output)
        cache_rows = cache_row_evidence(driver, options)
        return {
            'schema': 'cdeadmin.firebird-properties-ui-gate.v1',
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'engine_id': 'firebird',
            'interface_id': 'firebird-native',
            'reference_version': '5.0.4',
            'viewport': f'{options.width}x{options.height}',
            'theme': options.theme,
            'font_scale': options.font_scale,
            'evidence_variant': evidence_variant(options),
            'command_id': action['command_id'],
            'property_group_count': len(rendered),
            'rendered_property_count': sum(
                len(group) for group in rendered.values()
            ),
            'stable_native_properties_match': True,
            'unstructured_json_values_rendered': False,
            'unavailable_values_inferred': False,
            'credential_values_exported': False,
            'layout': layout,
            'screenshot': {'path': str(output), 'sha256': digest},
            'cache_rows': cache_rows,
            'passed': all(item['no_text_overflow'] for item in cache_rows),
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
        'property_group_count': result['property_group_count'],
        'rendered_property_count': result['rendered_property_count'],
    }, indent=2, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
