#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Exercise exact-engine query workflows in the browser UI."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
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
from tools.cdeadmin_firebird_query_ui_gate import (  # noqa: E402
    _query_editor,
    _visible_text,
)
from tools.cdeadmin_firebird_ui_form_gate import (  # noqa: E402
    create_driver,
    prepare_tree,
    screenshot,
)
from tools.cdeadmin_ui_evidence import (  # noqa: E402
    complete_endpoint_prompt,
    invoke_context_action,
    visible_named_control,
    wait_for_tree_item,
)


SMALL_QUERY = (
    'SELECT customer_id, name FROM customers '
    'ORDER BY customer_id LIMIT 2'
)
COMPARISON_QUERY = (
    'SELECT customer_id, name FROM customers '
    'ORDER BY customer_id LIMIT 3'
)
PAGED_QUERY = (
    'WITH RECURSIVE sequence(value) AS ('
    'SELECT 1 UNION ALL SELECT value + 1 FROM sequence WHERE value < 650'
    ') SELECT value, printf(\'row-%03d\', value) AS label FROM sequence'
)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--engine', default='SQLite')
    parser.add_argument('--server', default='localhost')
    parser.add_argument('--database', required=True)
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


def _engine_identity(engine):
    if engine == 'SQLite':
        return 'sqlite', 'sqlite-native', '3.53.0', 'SCAN customers'
    if engine == 'DuckDB':
        return 'duckdb', 'duckdb-native', '1.5.2', 'SEQ_SCAN'
    if engine == 'MySQL':
        return 'mysql', 'mysql-native', '9.7.0', 'query_plan'
    raise RuntimeError(f'unsupported native query gate engine: {engine}')


def _engine_queries(engine):
    if engine == 'SQLite':
        return SMALL_QUERY, COMPARISON_QUERY, PAGED_QUERY
    if engine == 'DuckDB':
        return (
            SMALL_QUERY.replace('FROM customers', 'FROM service.customers'),
            COMPARISON_QUERY.replace(
                'FROM customers', 'FROM service.customers'
            ), PAGED_QUERY,
        )
    if engine == 'MySQL':
        return (
            SMALL_QUERY, COMPARISON_QUERY,
            'WITH RECURSIVE sequence(value) AS ('
            'SELECT 1 UNION ALL SELECT value + 1 FROM sequence '
            'WHERE value < 650) SELECT value, '
            "CONCAT('row-', LPAD(value, 3, '0')) AS label FROM sequence",
        )
    raise RuntimeError(f'unsupported native query gate engine: {engine}')


def _set_query(driver, wait, source):
    editor = wait.until(lambda value: _query_editor(value))
    driver.execute_script(
        """
        const editor = arguments[0];
        const setter = Object.getOwnPropertyDescriptor(
          HTMLTextAreaElement.prototype, 'value'
        ).set;
        setter.call(editor, arguments[1]);
        editor.dispatchEvent(new Event('input', {bubbles: true}));
        """,
        editor, source,
    )
    wait.until(lambda _value: editor.get_attribute('value') == source)


def _capture(driver, options, state, screenshots, controls):
    path = options.output_root / (
        f'{state}-{options.width}x{options.height}-{options.theme}.png'
    )
    screenshots[state] = {
        'path': str(path), 'sha256': screenshot(driver, path),
    }
    controls[state] = _grid_control_evidence(driver)


def _run_query(driver, wait, source, expected_text, engine):
    _set_query(driver, wait, source)
    _button(wait, 'Run').click()
    wait.until(lambda value: _visible_text(value, expected_text) or any(
        item.is_displayed() and 'did not complete the request' in
        item.text.lower()
        for item in value.find_elements(By.CSS_SELECTOR, '[role="alert"]')
    ))
    if not _visible_text(driver, expected_text):
        alerts = [
            item.text.strip() for item in driver.find_elements(
                By.CSS_SELECTOR, '[role="alert"]'
            ) if item.is_displayed() and item.text.strip()
        ]
        raise RuntimeError(
            f'{engine} query did not render {expected_text!r}: {alerts}'
        )


def _wait_for_download(directory, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        files = [
            item for item in directory.glob('*.csv')
            if item.is_file() and item.stat().st_size > 0
        ]
        if files:
            return max(files, key=lambda item: item.stat().st_mtime_ns)
        time.sleep(0.1)
    raise RuntimeError('CSV result export was not downloaded')


def run(options):
    engine_id, interface_id, reference_version, plan_token = (
        _engine_identity(options.engine)
    )
    small_query, comparison_query, paged_query = _engine_queries(
        options.engine
    )
    options.download_dir = options.output_root / 'downloads'
    options.download_dir.mkdir(parents=True, exist_ok=True)
    for old in options.download_dir.glob('*'):
        if old.is_file():
            old.unlink()
    driver = create_driver(options)
    wait = WebDriverWait(driver, options.timeout)
    screenshots = {}
    controls = {}
    try:
        password = os.environ.get(options.endpoint_password_env or '', '')
        prepare_tree(driver, wait, options, password)
        database = wait_for_tree_item(wait, options.database)
        invoke_context_action(
            wait, driver, database,
            ['Database workspace', f'Open {options.engine} SQL editor...'],
            password, endpoint_prompt_timeout=5 if password else 1,
        )
        complete_endpoint_prompt(
            driver, password, timeout=5 if password else 1
        )
        wait.until(lambda value: _query_editor(value))
        if visible_named_control(driver, 'Explain / query plan') is None:
            raise RuntimeError(
                f'{options.engine} provider did not contribute its native '
                'plan action'
            )
        _capture(driver, options, 'initial', screenshots, controls)

        _run_query(
            driver, wait, small_query, 'Northwind Field Lab', options.engine
        )
        _capture(driver, options, 'result-rendered', screenshots, controls)
        _button(wait, 'Export CSV').click()
        exported = _wait_for_download(options.download_dir, options.timeout)
        export_text = exported.read_text(encoding='utf-8')
        if 'Northwind Field Lab' not in export_text:
            raise RuntimeError(
                f'{options.engine} CSV export lacks the rendered row'
            )
        _capture(driver, options, 'result-exported', screenshots, controls)

        _run_query(
            driver, wait, comparison_query, 'Adventure Works Mining',
            options.engine,
        )
        compare = _button(wait, 'Compare with previous result')
        if not compare.is_enabled():
            raise RuntimeError(
                f'{options.engine} result comparison is unavailable'
            )
        compare.click()
        wait.until(lambda value: any(
            item.is_displayed() and
            item.get_attribute('aria-label') == 'Result comparison'
            for item in value.find_elements(By.TAG_NAME, 'pre')
        ))
        _capture(driver, options, 'result-compared', screenshots, controls)

        _set_query(driver, wait, small_query)
        _button(wait, 'Explain / query plan').click()
        wait.until(lambda value: any(
            item.is_displayed() and
            item.get_attribute('aria-label') == 'Query plan results' and
            plan_token in item.text
            for item in value.find_elements(By.CSS_SELECTOR, '[role="region"]')
        ) or any(
            item.is_displayed() and
            item.get_attribute('aria-label') == 'Query plan results' and
            plan_token in item.text
            for item in value.find_elements(By.CSS_SELECTOR, '[aria-label]')
        ))
        _capture(driver, options, 'native-query-plan', screenshots, controls)

        _run_query(driver, wait, paged_query, 'row-001', options.engine)
        next_page = _button(wait, 'Next result page')
        if not next_page.is_enabled():
            raise RuntimeError(
                f'{options.engine} large result did not expose paging'
            )
        _capture(driver, options, 'large-result-first-page', screenshots,
                 controls)
        next_page.click()
        wait.until(lambda value: _visible_text(value, 'row-501'))
        _capture(driver, options, 'large-result-second-page', screenshots,
                 controls)

        _button(wait, 'Provider transaction state').click()
        wait.until(lambda value: any(
            item.is_displayed() and 'driver_observation_only' in item.text
            for item in value.find_elements(By.TAG_NAME, 'pre')
        ))
        _capture(driver, options, 'transaction-state', screenshots, controls)
        close_session = _button(wait, 'Close query session')
        close_session.click()
        wait.until(lambda value: (
            (control := visible_named_control(
                value, 'Close query session'
            )) is not None and not control.is_enabled()
        ))
        _capture(driver, options, 'session-closed', screenshots, controls)
        return {
            'schema': f'cdeadmin.{engine_id}-query-ui-gate.v1',
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'engine_id': engine_id, 'interface_id': interface_id,
            'reference_version': reference_version,
            'database': options.database,
            f'{engine_id}_sql_result_observed': True,
            'native_query_plan_observed': True,
            'result_export_observed': True,
            'result_comparison_observed': True,
            'large_result_paging_observed': True,
            'transaction_state_observed': True,
            'provider_session_closed': True,
            'common_dialect_inference': False,
            'credential_values_exported': False,
            'export': {
                'filename': exported.name,
                'bytes': exported.stat().st_size,
            },
            'screenshots': screenshots, 'controls': controls,
            'passed': True,
        }
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
