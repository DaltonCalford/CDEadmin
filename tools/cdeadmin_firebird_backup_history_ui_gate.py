#!/usr/bin/env python3
"""Exercise Firebird backup-history controls on an owned UI database."""
import json
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from cdeadmin_firebird_query_ui_gate import (
    _button, _grid_control_evidence, _load_profile, _write_records,
    arguments, complete_endpoint_prompt, create_driver, evidence_variant,
    invoke_context_action, prepare_tree, screenshot, wait_for_tree_item,
)
from cdeadmin_firebird_ui_form_gate import (
    close_workspace, fill_form_values, firebird_service_forms,
)
from cdeadmin_ui_evidence import visible_named_control
from cdeadmin_firebird_admin_mapping_gate import _route_arguments
from pgadmin.cdeadmin.providers.firebird.provider import (
    _configure_client_library,
)


def run(options, password):
    import firebird.driver as native
    _configure_client_library(native)
    if not re.fullmatch(r'cde_history_ui_[0-9a-f]{32}\.fdb', options.database):
        raise ValueError('Backup-history UI gate requires an owned database')
    route = _load_profile(options.profiles)
    root = PurePosixPath(route['database']).parent
    database_path = str(root / options.database)
    route = {**route, 'database': database_path}
    route.pop('password', None)
    driver = create_driver(options)
    wait = WebDriverWait(driver, options.timeout)
    evidence = {'schema': 'cdeadmin.firebird-backup-history-ui.v1',
                'captured_at': datetime.now(timezone.utc).isoformat(),
                'engine_id': 'firebird', 'interface_id': 'firebird-native',
                'reference_version': '5.0.4', 'database': options.database,
                'passed': False, 'credential_values_exported': False,
                'screenshots': {}, 'controls': {}, 'cases': []}

    def capture(state):
        path = options.output_root / (
            state + '-' + evidence_variant(options) + '.png')
        evidence['screenshots'][state] = {
            'path': str(path), 'sha256': screenshot(
                driver, path, reset_scroll=False)}
        evidence['controls'][state] = _grid_control_evidence(driver)

    def readable_result(result, prefix):
        for index, term in enumerate(result.find_elements(By.TAG_NAME, 'dt')):
            if not term.text.startswith('Requested backup'):
                continue
            geometry = driver.execute_script('''
                const group = arguments[0].parentElement;
                group.scrollIntoView({block: 'center'});
                let visible = true;
                for (const item of group.children) {
                  const r = item.getBoundingClientRect();
                  visible &&= r.top >= 0 && r.left >= 0 &&
                    r.bottom <= innerHeight && r.right <= innerWidth;
                  visible &&= item.scrollWidth <= item.clientWidth + 1;
                  for (let p = item.parentElement; p; p = p.parentElement) {
                    const css = getComputedStyle(p);
                    const b = p.getBoundingClientRect();
                    if (['auto','scroll','hidden'].includes(css.overflowY))
                      visible &&= r.top >= b.top - 1 &&
                        r.bottom <= b.bottom + 1;
                    if (['auto','scroll','hidden'].includes(css.overflowX))
                      visible &&= r.left >= b.left - 1 &&
                        r.right <= b.right + 1;
                  }
                }
                return {label_and_value_visible: visible};
            ''', term)
            state = prefix + '-readable-result-' + str(index)
            capture(state)
            evidence['controls'][state].append(geometry)
            assert geometry['label_and_value_visible']

    try:
        prepare_tree(driver, wait, options, password)
        fields = next(item['form']['fields']
                      for item in firebird_service_forms()
                      if item['operation_id'] == 'backup_physical')
        latest_guid = None
        for index, unit in enumerate(('ROWS', 'DAYS')):
            io_mode = 'ON' if unit == 'ROWS' else 'OFF'
            database = wait_for_tree_item(wait, options.database)
            invoke_context_action(wait, driver, database,
                                  ['Backup', 'Physical backup (nbackup)...'],
                                  password, endpoint_prompt_timeout=1)
            complete_endpoint_prompt(driver, password, timeout=1)
            io_control = visible_named_control(
                driver, 'Backup read I/O policy')
            assert 'Native default' in io_control.text
            backup_path = str(root / (options.database + '.' + unit + '.nbk'))
            values = {
                'Physical backup filename on the Firebird server': backup_path,
                'Clean backup history after backup': 'true',
                'Keep backup history by': unit,
                'History retention count': 0,
                'Backup read I/O policy': io_mode,
            }
            fill_form_values(driver, wait, fields, values)
            _button(wait, 'Validate and preview').click()
            wait.until(lambda value: 'History retention count is below its '
                       'minimum.' in value.find_element(
                           By.CSS_SELECTOR, '[role="dialog"]').text)
            assert not driver.find_element(
                By.XPATH, '//button[normalize-space()="Apply provider plan"]'
            ).is_enabled()
            capture(unit.lower() + '-invalid-count')
            fill_form_values(driver, wait, fields,
                             {**values, 'History retention count': 1})
            for label, state in [
                    ('Clean backup history after backup', 'enabled'),
                    ('Keep backup history by', 'unit'),
                    ('History retention count', 'count'),
                    ('Backup read I/O policy', 'io-policy')]:
                control = visible_named_control(driver, label)
                assert control is not None
                geometry = driver.execute_script('''
                    const control = arguments[0];
                    const group = control.closest('.MuiFormControl-root') ||
                      control.closest('label') || control;
                    group.scrollIntoView({block: 'center'});
                    const label = group.matches('label') ? group :
                      group.querySelector('label');
                    let visible = true;
                    for (const item of [control, label].filter(Boolean)) {
                      const r = item.getBoundingClientRect();
                      for (let p = item.parentElement; p;
                           p = p.parentElement) {
                        const css = getComputedStyle(p);
                        const b = p.getBoundingClientRect();
                        if (['auto','scroll','hidden'].includes(css.overflowY))
                          visible &&= r.top >= b.top - 1 &&
                            r.bottom <= b.bottom + 1;
                        if (['auto','scroll','hidden'].includes(css.overflowX))
                          visible &&= r.left >= b.left - 1 &&
                            r.right <= b.right + 1;
                      }
                    }
                    return {label_and_control_visible: visible,
                      value_not_clipped: control.scrollWidth <=
                        control.clientWidth + 1};
                ''', control)
                capture(unit.lower() + '-control-' + state)
                state_key = unit.lower() + '-control-' + state
                evidence['controls'][state_key].append(geometry)
                assert geometry['label_and_control_visible']
                assert geometry['value_not_clipped']
            _button(wait, 'Validate and preview').click()
            complete_endpoint_prompt(driver, password, timeout=3)
            apply = _button(wait, 'Apply provider plan')
            preview = driver.find_element(
                By.CSS_SELECTOR, '[aria-label="Provider plan preview"]')
            assert 'backup-history records' in preview.text
            assert json.loads(preview.text)['command_preview'][
                'backup_io_requested'] == io_mode
            capture(unit.lower() + '-plan')
            apply.click()
            result = wait.until(lambda value: value.find_element(
                By.CSS_SELECTOR, '[aria-label="Firebird service result"]'))
            assert 'Requested backup selection' in result.text
            assert 'Level: 0' in result.text
            assert 'Direct reads ' + io_mode in result.text
            assert 'Requested backup-history retention' in result.text
            expected_policy = ('Newest rows (timestamp cutoff): 1'
                               if unit == 'ROWS' else
                               'Calendar days including today: 1')
            assert expected_policy in result.text
            readable_result(result, unit.lower())
            details = result.find_element(By.TAG_NAME, 'details')
            details.find_element(By.TAG_NAME, 'summary').click()
            observed = json.loads(result.find_element(
                By.CSS_SELECTOR,
                '[aria-label="Firebird native service receipt"]').text)
            assert observed['server_completed'] is True
            assert observed['backup_io_requested'] == io_mode
            release = observed['service_release']
            assert release['service_handle_released'] is True
            assert observed['history_retention_requested'] == {
                'unit': unit, 'value': 1, 'backup_files_deleted': False}
            connection = native.connect(password=password,
                                        **_route_arguments(route, native))
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT COUNT(*) FROM RDB$BACKUP_HISTORY')
                    assert cursor.fetchone() == (index + 1,)
                    cursor.execute('SELECT FIRST 1 RDB$GUID FROM '
                                   'RDB$BACKUP_HISTORY '
                                   'ORDER BY RDB$BACKUP_ID DESC')
                    latest_guid = cursor.fetchone()[0].strip()
            finally:
                connection.close()
            capture(unit.lower() + '-native-result')
            evidence['cases'].append(unit + '-native-history-verified')
            close_workspace(driver, wait)
        database = wait_for_tree_item(wait, options.database)
        invoke_context_action(wait, driver, database,
                              ['Backup', 'Physical backup (nbackup)...'],
                              password, endpoint_prompt_timeout=1)
        complete_endpoint_prompt(driver, password, timeout=1)
        io_control = visible_named_control(driver, 'Backup read I/O policy')
        assert 'Native default' in io_control.text
        driver.execute_script(
            'arguments[0].scrollIntoView({block:"center"})', io_control)
        capture('native-io-policy-control')
        guid_label = 'Database backup GUID (overrides level)'
        values = {
            'Physical backup filename on the Firebird server': str(
                root / (options.database + '.GUID.nbk')),
            guid_label: 'not-a-guid',
        }
        fill_form_values(driver, wait, fields, values)
        _button(wait, 'Validate and preview').click()
        wait.until(lambda value: 'Firebird backup GUID must be a hyphenated '
                   'UUID' in value.find_element(
                       By.CSS_SELECTOR, '[role="dialog"]').text)
        capture('guid-invalid-format')
        values[guid_label] = latest_guid.strip('{}').lower()
        fill_form_values(driver, wait, fields, values)
        control = visible_named_control(driver, guid_label)
        driver.execute_script(
            'arguments[0].scrollIntoView({block:"center"})', control)
        capture('guid-control')
        _button(wait, 'Validate and preview').click()
        complete_endpoint_prompt(driver, password, timeout=3)
        apply = _button(wait, 'Apply provider plan')
        preview = json.loads(driver.find_element(
            By.CSS_SELECTOR, '[aria-label="Provider plan preview"]').text)
        assert preview['command_preview']['backup_selection'] == {
            'mode': 'guid', 'guid': latest_guid.upper()}
        assert preview['command_preview']['backup_io_requested'] == 'NATIVE'
        capture('guid-native-plan')
        apply.click()
        result = wait.until(lambda value: value.find_element(
            By.CSS_SELECTOR, '[aria-label="Firebird service result"]'))
        assert 'Requested backup selection' in result.text
        assert 'GUID: ' + latest_guid.upper() in result.text
        assert 'Native default' in result.text
        assert 'Requested backup-history retention' not in result.text
        readable_result(result, 'guid')
        result.find_element(By.TAG_NAME, 'summary').click()
        observed = json.loads(result.find_element(
            By.CSS_SELECTOR,
            '[aria-label="Firebird native service receipt"]').text)
        assert observed['server_completed'] is True
        assert observed['backup_io_requested'] == 'NATIVE'
        assert observed['service_release']['service_handle_released'] is True
        assert observed['backup_selection_requested'] == {
            'mode': 'guid', 'guid': latest_guid.upper()}
        assert 'history_retention_requested' not in observed
        connection = native.connect(password=password,
                                    **_route_arguments(route, native))
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT FIRST 1 RDB$BACKUP_LEVEL FROM '
                               'RDB$BACKUP_HISTORY '
                               'ORDER BY RDB$BACKUP_ID DESC')
                assert cursor.fetchone() == (None,)
        finally:
            connection.close()
        capture('guid-native-result')
        evidence['cases'].append('bare-guid-native-increment-verified')
        close_workspace(driver, wait)
        database = wait_for_tree_item(wait, options.database)
        invoke_context_action(wait, driver, database,
                              ['Restore', 'Physical restore (nbackup)...'],
                              password, endpoint_prompt_timeout=1)
        confirmation = wait.until(lambda value: value.find_element(
            By.XPATH, '//*[@role="dialog"]'
            '[contains(., "Confirm provider action")]'))
        assert 'Physical restore (nbackup)' in confirmation.text
        capture('restore-open-confirmation')
        _button(wait, 'Cancel').click()
        wait.until(EC.invisibility_of_element(confirmation))
        assert visible_named_control(driver, (
            'Restored database filename on the Firebird server')) is None
        database = wait_for_tree_item(wait, options.database)
        invoke_context_action(wait, driver, database,
                              ['Restore', 'Physical restore (nbackup)...'],
                              password, endpoint_prompt_timeout=1)
        confirmation = wait.until(lambda value: value.find_element(
            By.XPATH, '//*[@role="dialog"]'
            '[contains(., "Confirm provider action")]'))
        assert 'Physical restore (nbackup)' in confirmation.text
        _button(wait, 'Continue').click()
        complete_endpoint_prompt(driver, password, timeout=1)
        wait.until(lambda _driver: visible_named_control(
            driver, 'Restored database filename on the Firebird server'))
        assert visible_named_control(driver, 'Use direct I/O') is None
        assert visible_named_control(driver, 'Backup read I/O policy') is None
        capture('restore-no-ineffective-io-control')
        evidence['cases'].append('restore-form-no-ineffective-io-control')
        close_workspace(driver, wait)
        evidence['passed'] = len(evidence['cases']) == 4
    except Exception as exc:
        evidence['error_type'] = type(exc).__name__
        capture('failure')
        raise
    finally:
        try:
            options.summary_output.parent.mkdir(parents=True, exist_ok=True)
            options.summary_output.write_text(
                json.dumps(evidence, indent=2) + '\n')
            _write_records(options, evidence,
                           command_id='database.firebird.backup_physical',
                           form_id='firebird_backup_physical',
                           proof_id='firebird-backup-history-ui-gate')
        finally:
            driver.quit()


def main():
    options = arguments()
    password = str(_load_profile(options.profiles).get('password', ''))
    if not password:
        raise SystemExit('Firebird demo credential is unavailable')
    run(options, password)


if __name__ == '__main__':
    main()
