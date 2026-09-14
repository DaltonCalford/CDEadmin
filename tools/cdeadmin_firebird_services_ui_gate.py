#!/usr/bin/env python3
"""Exercise read-only native Firebird statistics through its task form."""

import json
from datetime import datetime, timezone

from selenium.webdriver.common.by import By
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
        apply.click()
        complete_endpoint_prompt(driver, password, timeout=3)
        result = wait.until(lambda value: value.find_element(
            By.CSS_SELECTOR, '[aria-label="Provider operation result"]'))
        observed = json.loads(result.text)['driver_observation']
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
        driver.execute_script('arguments[0].scrollIntoView({block:"center"})',
                              result)
        capture('statistics-native-result')
        evidence.update(passed=True, native_result_observed=True,
                        service_handle_release_observed=True,
                        applied_plan_replay_disabled=True)
    except Exception as exc:
        evidence['error_type'] = type(exc).__name__
        capture('failure')
        raise
    finally:
        try:
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
