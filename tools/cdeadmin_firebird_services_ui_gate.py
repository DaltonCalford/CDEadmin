#!/usr/bin/env python3
"""Exercise read-only native Firebird statistics through its task form."""

import json
from datetime import datetime, timezone

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from cdeadmin_firebird_query_ui_gate import (
    _button, _grid_control_evidence, _load_profile, _write_records,
    arguments, complete_endpoint_prompt, create_driver, evidence_variant,
    invoke_context_action, prepare_tree, screenshot, wait_for_tree_item,
)


def run(options, password):
    driver = create_driver(options)
    wait = WebDriverWait(driver, options.timeout)
    evidence = {
        'schema': 'cdeadmin.firebird-services-ui-gate.v1',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'engine_id': 'firebird', 'interface_id': 'firebird-native',
        'reference_version': '5.0.4', 'database': options.database,
        'credential_values_exported': False, 'passed': False,
        'screenshots': {}, 'controls': {},
    }

    def capture(state):
        path = options.output_root / (
            f'{state}-{options.width}x{options.height}-'
            f'{evidence_variant(options)}.png')
        evidence['screenshots'][state] = {
            'path': str(path), 'sha256': screenshot(
                driver, path, reset_scroll=False)}
        evidence['controls'][state] = _grid_control_evidence(driver)

    try:
        prepare_tree(driver, wait, options, password)
        database = wait_for_tree_item(wait, options.database)
        invoke_context_action(
            wait, driver, database,
            ['Diagnostics and verification', 'Database statistics (gstat)...'],
            password, endpoint_prompt_timeout=1)
        complete_endpoint_prompt(driver, password, timeout=1)
        preview = _button(wait, 'Validate and preview')
        capture('statistics-form')
        preview.click()
        complete_endpoint_prompt(driver, password, timeout=3)
        apply = _button(wait, 'Apply provider plan')
        plan = wait.until(lambda value: value.find_element(
            By.CSS_SELECTOR, '[aria-label="Provider plan preview"]'))
        assert 'database_statistics' in plan.text
        capture('statistics-plan')
        # Hold only this read-only Apply dispatch in the isolated browser.
        # This proves pending-request close safety, not native cancellation.
        driver.execute_script('''
            const original = XMLHttpRequest.prototype.send;
            const gate = {original, held: null, admitted: 0};
            XMLHttpRequest.prototype.send = function(body) {
              let request;
              try { request = JSON.parse(body); } catch { /* non-JSON */ }
              if (request?.action === 'visual_admin_apply') {
                gate.admitted++;
                if (gate.held) throw new Error('Duplicate gate dispatch');
                gate.held = {xhr: this, body};
                return;
              }
              return original.call(this, body);
            };
            window.__cdeServiceCloseGate = gate;
        ''')
        apply.click()
        wait.until(lambda value: value.execute_script(
            'return !!window.__cdeServiceCloseGate.held'))
        dialog = apply.find_element(
            By.XPATH, './ancestor::*[@role="dialog"][1]')
        closes = dialog.find_elements(
            By.XPATH, './/button[@aria-label="Close" or normalize-space()='
            '"Close"]')
        assert len(closes) >= 2, 'Title and footer close controls are required'
        for route, control in [('title', closes[0]), ('footer', closes[-1]),
                               ('escape', None)]:
            if control is None:
                # The Dialog paper is not an editable/focusable element.
                # Send an actual key to its enabled footer button instead.
                closes[-1].send_keys(Keys.ESCAPE)
            else:
                driver.execute_script(
                    'arguments[0].scrollIntoView({block:"center"})', control)
                control.click()
            wait.until(lambda value: 'Closing a task does not cancel or '
                       'roll back a native operation.' in dialog.text)
            assert dialog.is_displayed()
            assert not apply.is_enabled()
            capture('pending-service-close-' + route)
        assert driver.execute_script(
            'return window.__cdeServiceCloseGate.admitted') == 1
        driver.execute_script('''
            const gate = window.__cdeServiceCloseGate;
            XMLHttpRequest.prototype.send = gate.original;
            delete window.__cdeServiceCloseGate;
            gate.original.call(gate.held.xhr, gate.held.body);
        ''')
        complete_endpoint_prompt(driver, password, timeout=3)
        result = wait.until(lambda value: value.find_element(
            By.CSS_SELECTOR, '[aria-label="Firebird service result"]'))
        fields = result.find_elements(By.CSS_SELECTOR, 'dl > div')
        assert len(fields) == 4
        for index, field in enumerate(fields):
            driver.execute_script(
                'arguments[0].scrollIntoView({block:"center"})', field)
            geometry = driver.execute_script('''
                const field = arguments[0];
                let viewport = field.parentElement;
                while (viewport && !['auto', 'scroll'].includes(
                    getComputedStyle(viewport).overflowY)) {
                  viewport = viewport.parentElement;
                }
                if (!viewport) return null;
                const f = field.getBoundingClientRect();
                const v = viewport.getBoundingClientRect();
                return {fully_visible: f.top >= v.top - 1 &&
                    f.bottom <= v.bottom + 1 && f.left >= v.left - 1 &&
                    f.right <= v.right + 1};
            ''', field)
            assert geometry and geometry['fully_visible']
            state = f'service-field-{index + 1}'
            capture(state)
            evidence['controls'][state].append(geometry)
        output = result.find_element(
            By.CSS_SELECTOR, '[aria-label="Firebird native service output"]')
        assert 'Database header page information' in output.text
        lines = [line for line in output.find_elements(
            By.CSS_SELECTOR, '[data-firebird-output-line]')
                 if line.get_attribute('textContent').strip()]
        assert lines
        for state, line in [('statistics-native-output-first', lines[0]),
                            ('statistics-native-output-last', lines[-1])]:
            driver.execute_script(
                'arguments[0].scrollIntoView({block:"center"})', line)
            geometry = driver.execute_script('''
                const line = arguments[0], r = line.getBoundingClientRect();
                let parent = line.parentElement, visible = r.height > 0;
                while (parent) {
                  const p = parent.getBoundingClientRect();
                  const css = getComputedStyle(parent);
                  if (['auto', 'scroll', 'hidden'].includes(css.overflowY))
                    visible &&= r.top >= p.top - 1 && r.bottom <= p.bottom + 1;
                  if (['auto', 'scroll', 'hidden'].includes(css.overflowX))
                    visible &&= r.left >= p.left - 1 && r.right <= p.right + 1;
                  parent = parent.parentElement;
                }
                return {native_line_fully_visible: visible};
            ''', line)
            assert geometry['native_line_fully_visible']
            capture(state)
            evidence['controls'][state].append(geometry)
        details = result.find_element(By.TAG_NAME, 'details')
        assert not details.get_property('open')
        details.find_element(By.TAG_NAME, 'summary').click()
        receipt = result.find_element(
            By.CSS_SELECTOR, '[aria-label="Firebird native service receipt"]')
        observed = json.loads(receipt.text)
        assert output.get_attribute('textContent') == ''.join(
            observed['output'])
        assert observed['schema'] == 'cdeadmin.firebird-service-result.v1'
        assert observed['operation_id'] == 'database_statistics'
        assert observed['server_completed'] is True
        assert observed['output']
        assert observed['service_release']['service_handle_released'] is True
        assert observed['service_release']['rollback_requested'] is False
        assert not driver.find_elements(By.CSS_SELECTOR,
                                        '[aria-label="Firebird service '
                                        'cleanup required"]')
        assert not apply.is_enabled(), 'Applied service plan is replayable'
        capture('statistics-native-receipt')
        details.find_element(By.TAG_NAME, 'summary').click()
        assert not details.get_property('open')
        evidence.update(passed=True, native_result_observed=True,
                        service_handle_release_observed=True,
                        pending_dispatch_close_veto=['title', 'footer',
                                                     'escape'],
                        applied_plan_replay_disabled=True)
    except Exception as exc:
        evidence['error_type'] = type(exc).__name__
        capture('failure')
        raise
    finally:
        try:
            driver.execute_script('''
                const gate = window.__cdeServiceCloseGate;
                if (gate) {
                  XMLHttpRequest.prototype.send = gate.original;
                  delete window.__cdeServiceCloseGate;
                }
            ''')
            options.summary_output.parent.mkdir(parents=True, exist_ok=True)
            options.summary_output.write_text(
                json.dumps(evidence, indent=2) + '\n')
            _write_records(
                options, evidence,
                command_id='database.firebird.database_statistics',
                form_id='firebird_database_statistics',
                proof_id='firebird-services-ui-gate')
        finally:
            driver.quit()
    return evidence


def main():
    options = arguments()
    password = str(_load_profile(options.profiles).get('password', ''))
    if not password:
        raise SystemExit('Firebird demo credential is unavailable')
    evidence = run(options, password)
    print(json.dumps({'passed': evidence['passed'],
                      'screenshot_count': len(evidence['screenshots']),
                      'credential_values_exported': False}))


if __name__ == '__main__':
    main()
