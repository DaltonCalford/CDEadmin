#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Exercise the rendered Firebird SQL editor against its database target."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cdeadmin_firebird_grid_ui_gate import (  # noqa: E402
    _button,
    _grid_control_evidence,
)
from tools.cdeadmin_firebird_seeded_transaction_gate import (  # noqa: E402
    _load_profile,
)
from tools.cdeadmin_firebird_ui_form_gate import (  # noqa: E402
    MANIFEST_FIELDS,
    _relative_evidence_path,
    create_driver,
    evidence_variant,
    prepare_tree,
    screenshot,
)
from tools.cdeadmin_ui_evidence import (  # noqa: E402
    complete_endpoint_prompt,
    invoke_context_action,
    visible_named_control,
    wait_for_tree_item,
)


QUERY = (
    'SELECT FIRST 2 CUSTOMER_ID, NAME FROM CUSTOMERS '
    'ORDER BY CUSTOMER_ID'
)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:5052')
    parser.add_argument('--engine', default='Firebird')
    parser.add_argument('--server', default='localhost')
    parser.add_argument('--database', default='cdeadmin_demo.fdb')
    parser.add_argument(
        '--profiles', type=Path,
        default=ROOT / 'tools/reference_engine_demos/runtime/'
        'connection_profiles.json',
    )
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--summary-output', type=Path, required=True)
    parser.add_argument('--manifest-output', type=Path, required=True)
    parser.add_argument('--width', type=int, default=1600)
    parser.add_argument('--height', type=int, default=1000)
    parser.add_argument(
        '--theme', choices=('default', 'high-contrast'), default='default',
    )
    parser.add_argument(
        '--font-scale', type=int, choices=(100, 150, 200, 300), default=100,
    )
    parser.add_argument('--timeout', type=int, default=45)
    parser.add_argument('--browser-binary')
    return parser.parse_args()


def _query_editor(driver):
    return next((
        item for item in driver.find_elements(By.CSS_SELECTOR, 'textarea')
        if item.is_displayed() and
        item.get_attribute('aria-label') == 'Query source'
    ), None)


def _visible_text(driver, expected):
    return any(
        item.is_displayed() and item.text.strip() == expected
        for item in driver.find_elements(
            By.XPATH, f'//*[normalize-space(text())={json.dumps(expected)}]'
        )
    )


def _capture(driver, options, state, screenshots, controls):
    viewport = f'{options.width}x{options.height}'
    path = options.output_root / (
        f'{state}-{viewport}-{evidence_variant(options)}.png'
    )
    screenshots[state] = {
        'path': str(path),
        'sha256': screenshot(driver, path),
    }
    controls[state] = _grid_control_evidence(driver)


def run(options, password):
    driver = create_driver(options)
    wait = WebDriverWait(driver, options.timeout)
    screenshots = {}
    controls = {}
    try:
        prepare_tree(driver, wait, options, password)
        database = wait_for_tree_item(wait, options.database)
        invoke_context_action(
            wait, driver, database,
            ['Database workspace', 'Open Firebird SQL editor...'],
            password, endpoint_prompt_timeout=1,
        )
        complete_endpoint_prompt(driver, password, timeout=1)
        editor = wait.until(lambda value: _query_editor(value))
        _capture(driver, options, 'initial', screenshots, controls)
        driver.execute_script(
            """
            const editor = arguments[0];
            const setter = Object.getOwnPropertyDescriptor(
              HTMLTextAreaElement.prototype, 'value'
            ).set;
            setter.call(editor, arguments[1]);
            editor.dispatchEvent(new Event('input', {bubbles: true}));
            """,
            editor, QUERY,
        )
        wait.until(lambda _value: editor.get_attribute('value') == QUERY)
        _capture(driver, options, 'query-ready', screenshots, controls)
        _button(wait, 'Run').click()
        wait.until(lambda value: (
            _visible_text(value, 'Northwind Field Lab') or any(
                item.is_displayed() and
                'did not complete the request' in item.text.lower()
                for item in value.find_elements(
                    By.CSS_SELECTOR, '[role="alert"]'
                )
            )
        ))
        if not _visible_text(driver, 'Northwind Field Lab'):
            _capture(
                driver, options, 'result-error', screenshots, controls
            )
            alerts = [
                item.text.strip()
                for item in driver.find_elements(
                    By.CSS_SELECTOR, '[role="alert"]'
                )
                if item.is_displayed() and item.text.strip()
            ]
            submitted_query = editor.get_attribute('value')
            raise RuntimeError(
                f'Firebird query failed for {submitted_query!r}; '
                f'visible alerts: {alerts}'
            )
        _capture(driver, options, 'result-rendered', screenshots, controls)
        _button(wait, 'Provider transaction state').click()
        wait.until(lambda value: any(
            item.is_displayed() and 'driver_observation_only' in item.text
            for item in value.find_elements(By.TAG_NAME, 'pre')
        ))
        _capture(
            driver, options, 'transaction-state', screenshots, controls
        )
        close_session = _button(wait, 'Close query session')
        close_session.click()
        wait.until(lambda value: (
            (control := visible_named_control(
                value, 'Close query session'
            )) is not None and not control.is_enabled()
        ))
        _capture(driver, options, 'session-closed', screenshots, controls)
        return {
            'schema': 'cdeadmin.firebird-query-ui-gate.v1',
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'engine_id': 'firebird',
            'interface_id': 'firebird-native',
            'reference_version': '5.0.4',
            'database': options.database,
            'query_kind': 'firebird-select-first',
            'database_scoped_session': True,
            'result_observed': 'Northwind Field Lab',
            'transaction_state_observed': True,
            'provider_session_closed': True,
            'common_finality_interpreted': False,
            'credential_values_exported': False,
            'screenshots': screenshots,
            'controls': controls,
            'passed': True,
        }
    finally:
        try:
            close_session = visible_named_control(
                driver, 'Close query session'
            )
            if close_session is not None and close_session.is_enabled():
                close_session.click()
        except Exception:
            pass
        driver.quit()


def _write_records(options, evidence):
    manifest = options.manifest_output
    existing = []
    if manifest.exists():
        with manifest.open(newline='', encoding='utf-8') as source:
            existing = list(csv.DictReader(source))
    keys = {
        (row['interface_id'], row['command_id'], row['state'],
         row['screenshot_path'])
        for row in existing
    }
    rows = []
    viewport = f'{options.width}x{options.height}'
    for state, value in evidence['screenshots'].items():
        screenshot_path = Path(value['path'])
        occurrence_path = screenshot_path.with_suffix('.json')
        occurrence = {
            'schema': 'cdeadmin.ui-form-evidence.v1',
            'captured_at': evidence['captured_at'],
            'engine_id': 'firebird',
            'interface_id': 'firebird-native',
            'reference_version': '5.0.4',
            'server_label': options.server,
            'database_label': options.database,
            'command_id': 'database.firebird.studio',
            'form_id': 'firebird_sql_studio',
            'state': state,
            'viewport': viewport,
            'theme': options.theme,
            'locale': 'en-US',
            'controls': evidence['controls'][state],
            'screenshot': value,
            'control_values_recorded': False,
            'credential_values_exported': False,
        }
        occurrence_path.write_text(
            json.dumps(occurrence, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
        row = {
            'interface_id': 'firebird-native',
            'reference_version': '5.0.4',
            'profile_id': 'firebird-native',
            'server_id': options.server,
            'database_target_id': options.database,
            'command_id': 'database.firebird.studio',
            'form_id': 'firebird_sql_studio',
            'resource_kind': 'database',
            'resource_id': options.database,
            'state': state,
            'viewport': viewport,
            'device_scale': '1',
            'font_scale': f'{options.font_scale}%',
            'theme': options.theme,
            'locale': 'en-US',
            'screenshot_path': _relative_evidence_path(
                screenshot_path, manifest
            ),
            'occurrence_path': _relative_evidence_path(
                occurrence_path, manifest
            ),
            'interaction_result': (
                f'Firebird SQL Studio state {state}; '
                f'sha256={value["sha256"]}'
            ),
            'transaction_proof_id': 'firebird-query-ui-gate',
            'captured_at_utc': evidence['captured_at'],
        }
        key = (
            row['interface_id'], row['command_id'], row['state'],
            row['screenshot_path'],
        )
        if key not in keys:
            rows.append(row)
            keys.add(key)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open('w', newline='', encoding='utf-8') as target:
        writer = csv.DictWriter(target, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(existing + rows)


def main():
    options = arguments()
    profile = _load_profile(options.profiles)
    password = str(profile.get('password', ''))
    if not password:
        raise SystemExit('Firebird demo credential is unavailable')
    evidence = run(options, password)
    password = ''
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    _write_records(options, evidence)
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
