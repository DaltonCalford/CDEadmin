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
import time
from datetime import datetime, timezone
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cdeadmin_firebird_grid_ui_gate import (  # noqa: E402
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
    _xpath_literal,
    complete_endpoint_prompt,
    invoke_context_action,
    visible_named_control,
    wait_for_tree_item,
)


QUERY = (
    'SELECT FIRST 2 CUSTOMER_ID, NAME FROM CUSTOMERS '
    'ORDER BY CUSTOMER_ID'
)


def _button(wait, name):
    return wait.until(lambda driver: (
        control if (control := visible_named_control(driver, name)) is not None
        and control.is_enabled() else None))


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
            By.XPATH,
            f'//*[normalize-space(text())={_xpath_literal(expected)}]'
        )
    )


def _set_text(driver, element, value):
    driver.execute_script('''
        const element = arguments[0];
        const prototype = element.tagName === 'TEXTAREA'
          ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(prototype, 'value').set.call(
          element, arguments[1]);
        element.dispatchEvent(new Event('input', {bubbles: true}));
    ''', element, value)


def _binding_editor(driver):
    for label in driver.find_elements(By.TAG_NAME, 'label'):
        if (label.is_displayed() and
                label.text == 'Query parameters (JSON array)'):
            return driver.find_element(By.ID, label.get_attribute('for'))
    return None


def _capture(driver, options, state, screenshots, controls, reset_scroll=True):
    viewport = f'{options.width}x{options.height}'
    path = options.output_root / (
        f'{state}-{viewport}-{evidence_variant(options)}.png'
    )
    screenshots[state] = {
        'path': str(path),
        'sha256': screenshot(driver, path, reset_scroll=reset_scroll),
    }
    controls[state] = _grid_control_evidence(driver)
    layout = driver.execute_script('''
      const body = document.querySelector(
        '[aria-label="Provider query workspace"]');
      const dialog = body?.closest('[role="dialog"]');
      const footer = dialog?.querySelector('.ModalContent-footer');
      if (!body || !dialog || !footer) return null;
      const b = body.getBoundingClientRect();
      const f = footer.getBoundingClientRect();
      const d = dialog.getBoundingClientRect();
      return {bodyBottom: b.bottom, bodyHeight: b.height,
        footerTop: f.top, footerBottom: f.bottom, dialogBottom: d.bottom,
        dialogAriaHidden: Boolean(dialog.closest('[aria-hidden="true"]')),
        overflowY: getComputedStyle(body).overflowY};
    ''')
    controls[state].append({'query_workspace_layout': layout})
    if state == 'failure':
        return
    assert layout is not None, 'Query workspace layout cannot be observed'
    assert layout['overflowY'] == 'auto'
    assert layout['dialogAriaHidden'] is False
    assert layout['bodyHeight'] > 0
    assert layout['bodyBottom'] <= layout['footerTop'] + 1, (
        'Query workspace extends behind its footer')
    assert layout['footerBottom'] <= layout['dialogBottom'] + 1


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
        limit_label = next(label for label in driver.find_elements(
            By.TAG_NAME, 'label') if label.is_displayed() and
            label.text == 'Maximum fetched rows')
        limit_input = driver.find_element(
            By.ID, limit_label.get_attribute('for'))
        assert limit_input.get_attribute('value') == '1000'
        driver.execute_script(
            'arguments[0].scrollIntoView({block: "center"})', limit_input)
        _capture(driver, options, 'fetch-limit-control', screenshots, controls,
                 reset_scroll=False)
        for alignment in ('start', 'end'):
            help_text = driver.find_element(
                By.ID, limit_input.get_attribute('aria-describedby'))
            assert 'does not limit modified rows' in help_text.text
            assert 'selectable procedure may not run to completion' in (
                help_text.text)
            driver.execute_script(
                'arguments[0].scrollIntoView({block: arguments[1]})',
                help_text, alignment)
            _capture(driver, options, 'fetch-limit-help-' + alignment,
                     screenshots, controls, reset_scroll=False)
        _set_text(driver, limit_input, '1000001')
        _button(wait, 'Run').click()
        wait.until(lambda value: _visible_text(value,
                   'Maximum fetched rows must be an integer from '
                                               '0 to 1000000.'))
        _capture(driver, options, 'fetch-limit-invalid', screenshots, controls)
        for bound, reached in (('2', True), ('1000000', False)):
            _set_text(driver, limit_input, bound)
            _set_text(driver, editor, 'SELECT RDB$TYPE AS N FROM RDB$TYPES '
                      'ORDER BY RDB$TYPE')
            _button(wait, 'Run').click()
            observation = wait.until(lambda value: next((
                item for item in value.find_elements(
                    By.CSS_SELECTOR,
                    '[aria-label="Firebird fetch observation"]')
                if item.is_displayed()), None))
            expected_text = ('Fetch limit reached.' if reached else
                             'The end of this result cursor was observed.')
            wait.until(lambda _value: expected_text in observation.text)
            if reached:
                assert 'Rows returned: 2' in observation.text
                assert 'total was not counted' in observation.text
            driver.execute_script(
                'arguments[0].scrollIntoView({block: "center"})', observation)
            _capture(driver, options, 'fetch-limit-result-' + bound,
                     screenshots, controls, reset_scroll=False)
        _set_text(driver, limit_input, '0')
        _set_text(driver, editor, 'SELECT 654321 AS UNBOUNDED_PROOF '
                  'FROM RDB$DATABASE')
        _button(wait, 'Run').click()
        wait.until(lambda value: _visible_text(value, '654321'))
        assert not driver.find_elements(
            By.CSS_SELECTOR,
            '[aria-label="Firebird fetch observation"]')
        _capture(driver, options, 'fetch-limit-explicit-unbounded',
                 screenshots, controls)
        _set_text(driver, limit_input, '1000')
        parameters = wait.until(_binding_editor)
        assert parameters.get_attribute('value') == '[]'
        _set_text(driver, parameters, '{"named": 1}')
        _button(wait, 'Run').click()
        wait.until(lambda value: any(
            item.is_displayed() and 'ordered JSON parameter array' in item.text
            for item in value.find_elements(By.CSS_SELECTOR, '[role="alert"]')
        ))
        _capture(driver, options, 'named-bindings-rejected',
                 screenshots, controls)
        for state, source, values, expected in (
            ('ordered-bindings',
             'SELECT CAST(? AS VARCHAR(80)) AS BINDING_TEXT, '
             'CAST(? AS INTEGER) AS BINDING_NUMBER FROM RDB$DATABASE',
             ['binding-order-proof', 741], 'binding-order-proof'),
            ('unicode-bindings',
             'SELECT CAST(? AS VARCHAR(80) CHARACTER SET UTF8) '
             'AS BINDING_TEXT FROM RDB$DATABASE',
             ["é ' ? ; -- literal-value"], "é ' ? ; -- literal-value"),
            ('exact-int128-result',
             'SELECT CAST(? AS INT128) AS EXACT_INTEGER FROM RDB$DATABASE',
             ['170141183460469231731687303715884105727'],
             '170141183460469231731687303715884105727'),
            ('exact-decfloat-result',
             'SELECT CAST(? AS DECFLOAT(34)) AS EXACT_DECIMAL '
             'FROM RDB$DATABASE',
             ['1.234567890123456789012345678901234'],
             '1.234567890123456789012345678901234'),
            ('timestamp-zone-result',
             'SELECT CAST(? AS TIMESTAMP WITH TIME ZONE) AS NATIVE_TIME '
             'FROM RDB$DATABASE', ['2026-09-14 12:34:56.1234 +02:00'],
             '2026-09-14 12:34:56.123400+02:00'),
        ):
            _set_text(driver, editor, source)
            _set_text(driver, parameters, json.dumps(values))
            _button(wait, 'Run').click()
            wait.until(lambda value: _visible_text(value, expected))
            _capture(driver, options, state, screenshots, controls)
            cell = next(item for item in driver.find_elements(
                By.XPATH,
                f'//*[normalize-space(text())={_xpath_literal(expected)}]'
            ) if item.is_displayed())
            driver.execute_script(
                'arguments[0].scrollIntoView({block: "center"})', cell)
            _capture(driver, options, state + '-result-detail',
                     screenshots, controls, reset_scroll=False)
        _button(wait, 'Provider transaction state').click()
        wait.until(lambda value: any(
            item.is_displayed() and 'Active transaction' in item.text
            for item in value.find_elements(
                By.CSS_SELECTOR,
                '[aria-label="Provider query transaction state"]')
        ))
        transaction_panel = driver.find_element(
            By.CSS_SELECTOR,
            '[aria-label="Provider query transaction state"]')
        driver.execute_script(
            'arguments[0].scrollIntoView({block: "center"})',
            transaction_panel)
        _capture(
            driver, options, 'transaction-state', screenshots, controls,
            reset_scroll=False,
        )
        details = transaction_panel.find_element(By.TAG_NAME, 'details')
        summary = details.find_element(By.TAG_NAME, 'summary')
        assert details.get_attribute('open') is None
        summary.click()
        wait.until(lambda _value: details.get_attribute('open') is not None)
        assert 'driver_observation_only' in details.text
        _capture(driver, options, 'transaction-native-details', screenshots,
                 controls, reset_scroll=False)
        summary.click()
        wait.until(lambda _value: details.get_attribute('open') is None)
        for action in ('commit', 'rollback'):
            if action == 'rollback':
                _button(wait, 'Run').click()
                wait.until(lambda value: _visible_text(value, expected))
                _button(wait, 'Provider transaction state').click()
                transaction_panel = wait.until(
                    lambda value: value.find_element(
                        By.CSS_SELECTOR,
                        '[aria-label="Provider query transaction state"]'))
                wait.until(lambda _value: 'Active transaction' in
                           transaction_panel.text)
            _button(wait, action).click()
            wait.until(lambda _value: 'Idle — no active transaction' in
                       transaction_panel.text)
            driver.execute_script(
                'arguments[0].scrollIntoView({block: "center"})',
                transaction_panel)
            _capture(driver, options, 'transaction-' + action + '-idle',
                     screenshots, controls, reset_scroll=False)
            # A second click has no active native transaction to complete.
            _button(wait, action).click()
            _button(wait, action)
            assert 'Idle — no active transaction' in transaction_panel.text
            errors = driver.find_elements(
                By.CSS_SELECTOR,
                '[role="alert"][class*="MuiAlert-standardError"]')
            assert not any(item.is_displayed() for item in errors)
            _capture(driver, options,
                     'transaction-' + action + '-already-idle',
                     screenshots, controls, reset_scroll=False)
        _set_text(driver, parameters, '[]')
        _set_text(driver, editor, 'SET TRANSACTION READ ONLY NO WAIT SNAPSHOT')
        _button(wait, 'Run').click()
        _button(wait, 'Provider transaction state').click()
        transaction_panel = wait.until(lambda value: value.find_element(
            By.CSS_SELECTOR,
            '[aria-label="Provider query transaction state"]'))
        wait.until(lambda _value: all(
            value in transaction_panel.text for value in (
                'Active transaction', 'Read only', 'No wait', 'SNAPSHOT')))
        driver.execute_script(
            'arguments[0].scrollIntoView({block: "start"})', transaction_panel)
        _capture(driver, options, 'native-set-transaction', screenshots,
                 controls, reset_scroll=False)
        driver.execute_script(
            'arguments[0].scrollIntoView({block: "center"})',
            transaction_panel.find_elements(By.TAG_NAME, 'dd')[-1])
        _capture(driver, options, 'native-set-transaction-snapshot-details',
                 screenshots, controls, reset_scroll=False)
        transaction_fields = transaction_panel.find_elements(
            By.CSS_SELECTOR, 'dl > div')
        assert len(transaction_fields) == 9
        for index, field in enumerate(transaction_fields):
            driver.execute_script(
                'arguments[0].scrollIntoView({block: "center"})', field)
            observation = driver.execute_script('''
              const field = arguments[0];
              const body = document.querySelector(
                '[aria-label="Provider query workspace"]');
              const f = field.getBoundingClientRect();
              const b = body.getBoundingClientRect();
              return {name: field.querySelector('dt').textContent,
                value: field.querySelector('dd').textContent,
                fully_visible: f.top >= b.top - 1 &&
                  f.bottom <= b.bottom + 1 && f.left >= b.left - 1 &&
                  f.right <= b.right + 1};
            ''', field)
            assert observation['fully_visible'], observation['name']
            state = 'native-transaction-field-' + str(index + 1)
            _capture(driver, options, state, screenshots, controls,
                     reset_scroll=False)
            controls[state].append({'transaction_field': observation})
        _button(wait, 'rollback').click()
        wait.until(lambda _value: 'Idle — no active transaction' in
                   transaction_panel.text)
        # Bound the deliberately costly read even if browser cancellation
        # fails. Allow Selenium's running-state and close-guard checks;
        # native timeout must not win before the explicit Cancel click. Timeout
        # behavior has separate native gates. SET affects only this attachment.
        _set_text(driver, parameters, '[]')
        _set_text(driver, editor,
                  'SET STATEMENT TIMEOUT 60000 MILLISECOND')
        _button(wait, 'Run').click()
        _button(wait, 'Run')
        _set_text(driver, editor,
                  "SELECT RDB$GET_CONTEXT('SYSTEM', 'STATEMENT_TIMEOUT') "
                  'AS TIMEOUT_VALUE FROM RDB$DATABASE')
        _button(wait, 'Run').click()
        wait.until(lambda value: _visible_text(value, '60000'))
        _set_text(driver, editor,
                  'SELECT COUNT(*) FROM RDB$TYPES A CROSS JOIN RDB$TYPES B '
                  'CROSS JOIN RDB$TYPES C CROSS JOIN RDB$TYPES D')
        run_button = _button(wait, 'Run')
        driver.execute_script('''
          window.__cdeFirebirdCancelTiming = {};
          arguments[0].addEventListener('click', () => {
            window.__cdeFirebirdCancelTiming.run = performance.now();
          }, {once: true, capture: true});
        ''', run_button)
        run_button.click()
        _button(wait, 'Cancel request')
        for name in ('Run', 'commit', 'rollback', 'Close query session'):
            control = visible_named_control(driver, name)
            assert control is not None and not control.is_enabled()
        capture_started = time.monotonic()
        _capture(driver, options, 'query-running', screenshots, controls)
        running_capture_seconds = time.monotonic() - capture_started
        dialog = editor.find_element(By.XPATH, './ancestor::*[@role="dialog"]')
        close_buttons = dialog.find_elements(By.CSS_SELECTOR,
                                             'button[aria-label="Close"]')
        assert len(close_buttons) == 1
        close_buttons[0].click()
        assert editor.is_displayed(), 'Running workspace was closed'
        cancel_button = _button(wait, 'Cancel request')
        driver.execute_script('''
          arguments[0].addEventListener('click', () => {
            window.__cdeFirebirdCancelTiming.cancel = performance.now();
          }, {once: true, capture: true});
        ''', cancel_button)
        cancel_started = time.monotonic()
        cancel_button.click()
        cancel_timing = driver.execute_script('''
          const trace = window.__cdeFirebirdCancelTiming;
          return {run_to_cancel_seconds: (trace.cancel - trace.run) / 1000};
        ''')
        cancel_timing['running_capture_seconds'] = running_capture_seconds
        controls['query-running'].append({'cancel_timing': cancel_timing})
        print('Firebird cancellation timing ' + json.dumps(cancel_timing),
              flush=True)
        wait.until(lambda value: any(
            item.is_displayed() and '335544794' in item.text
            for item in value.find_elements(By.CSS_SELECTOR, '[role="alert"]')
        ))
        cancellation_text = ' '.join(
            item.text for item in driver.find_elements(
                By.CSS_SELECTOR, '[role="alert"]') if item.is_displayed())
        cancel_timing['cancel_to_observation_seconds'] = (
            time.monotonic() - cancel_started)
        print('Firebird cancellation observation ' + json.dumps(cancel_timing),
              flush=True)
        assert all(code not in cancellation_text for code in (
            '335545127', '335545128', '335545129')), (
                'Statement timeout must not pass explicit cancellation')
        _button(wait, 'Run')
        _capture(driver, options, 'query-cancelled', screenshots, controls)
        _set_text(driver, editor, 'SELECT 98765 AS AFTER_CANCEL '
                  'FROM RDB$DATABASE')
        _button(wait, 'Run').click()
        wait.until(lambda value: _visible_text(value, '98765'))
        _capture(driver, options, 'query-after-cancel', screenshots, controls)
        _button(wait, 'rollback').click()
        transaction_panel = wait.until(lambda value: value.find_element(
            By.CSS_SELECTOR,
            '[aria-label="Provider query transaction state"]'))
        wait.until(lambda _value: 'Idle — no active transaction' in
                   transaction_panel.text)
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
            'fetch_bound_cases': ['default-1000', 'invalid-before-dispatch',
                                  'limit-reached-total-unknown',
                                  'end-observed', 'explicit-unbounded'],
            'database_scoped_session': True,
            'result_observed': 'Northwind Field Lab',
            'positional_binding_cases': [
                'named-bindings-rejected', 'ordered-bindings',
                'unicode-bindings'],
            'transaction_state_observed': True,
            'transaction_actions_observed': ['commit', 'rollback'],
            'idle_transaction_actions_observed': ['commit', 'rollback'],
            'native_set_transaction_observed': True,
            'provider_session_closed': True,
            'running_query_cancelled_and_session_reused': True,
            'running_workspace_close_guarded': True,
            'common_finality_interpreted': False,
            'credential_values_exported': False,
            'screenshots': screenshots,
            'controls': controls,
            'passed': True,
        }
    except Exception as exc:
        _capture(driver, options, 'failure', screenshots, controls)
        options.summary_output.parent.mkdir(parents=True, exist_ok=True)
        options.summary_output.write_text(json.dumps({
            'passed': False, 'error_type': type(exc).__name__,
            'screenshots': screenshots, 'controls': controls,
            'credential_values_exported': False,
        }, indent=2) + '\n')
        raise
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


def _write_records(options, evidence, *,
                   command_id='database.firebird.studio',
                   form_id='firebird_sql_studio',
                   proof_id='firebird-query-ui-gate'):
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
            'command_id': command_id,
            'form_id': form_id,
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
            'command_id': command_id,
            'form_id': form_id,
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
                f'{form_id} state {state}; '
                f'sha256={value["sha256"]}'
            ),
            'transaction_proof_id': proof_id,
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
