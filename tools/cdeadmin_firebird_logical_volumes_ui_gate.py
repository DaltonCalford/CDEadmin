#!/usr/bin/env python3
"""Exercise paired gbak volume controls and native restore through the UI."""
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import PurePosixPath

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from cdeadmin_firebird_query_ui_gate import (
    _button, _grid_control_evidence, _load_profile, _write_records,
    arguments, complete_endpoint_prompt, create_driver, evidence_variant,
    invoke_context_action, prepare_tree, screenshot, wait_for_tree_item)
from cdeadmin_firebird_ui_form_gate import (
    click_unobscured, close_workspace, fill_form_values,
    firebird_service_forms, assert_field_label_geometry)
from cdeadmin_ui_evidence import visible_named_control
from cdeadmin_firebird_admin_mapping_gate import _route_arguments
from pgadmin.cdeadmin.providers.firebird.provider import (
    _configure_client_library)


def run(options, password):
    import firebird.driver as native
    _configure_client_library(native)
    if not re.fullmatch(r'cde_history_ui_[0-9a-f]{32}\.fdb', options.database):
        raise ValueError('Logical volumes UI gate requires an owned database')
    route = _load_profile(options.profiles)
    path = str(PurePosixPath(route['database']).parent / options.database)
    route = {**route, 'database': path}
    route.pop('password', None)
    driver = create_driver(options)
    wait = WebDriverWait(driver, options.timeout)
    evidence = {'schema': 'cdeadmin.firebird-logical-volumes-ui.v1',
                'captured_at': datetime.now(timezone.utc).isoformat(),
                'engine_id': 'firebird', 'interface_id': 'firebird-native',
                'reference_version': '5.0.4', 'database': options.database,
                'passed': False, 'credential_values_exported': False,
                'screenshots': {}, 'controls': {}, 'cases': [],
                'owned_plan_drafts': []}
    operation = 'backup'
    current_plan = None

    def press(name):
        click_unobscured(driver, wait, _button(wait, name))

    def capture(state):
        target = options.output_root / (
            state + '-' + evidence_variant(options) + '.png')
        evidence['screenshots'][state] = {
            'path': str(target), 'sha256': screenshot(
                driver, target, reset_scroll=False)}
        evidence['controls'][state] = _grid_control_evidence(driver)

    def preview():
        nonlocal current_plan
        press('Validate and preview')
        complete_endpoint_prompt(driver, password, timeout=3)
        planned = json.loads(wait.until(lambda value: value.find_element(
            By.CSS_SELECTOR, '[aria-label="Provider plan preview"]')).text)
        # These fixtures supply only owned filenames and public service flags.
        evidence['owned_plan_drafts'].append(planned['draft'])
        current_plan = planned
        return planned

    def apply(prefix):
        checkbox = visible_named_control(
            driver, 'I confirm this provider-planned operation.')
        required = current_plan['confirmation_required']
        assert isinstance(required, bool)
        if required:
            assert checkbox is not None
            click_unobscured(driver, wait, checkbox)
        else:
            assert checkbox is None
        press('Apply provider plan')
        result = wait.until(lambda value: value.find_element(
            By.CSS_SELECTOR, '[aria-label="Firebird service result"]'))
        capture(prefix + '-readable-result')
        click_unobscured(driver, wait, result.find_element(
            By.TAG_NAME, 'summary'))
        observed = json.loads(result.find_element(
            By.CSS_SELECTOR,
            '[aria-label="Firebird native service receipt"]').text)
        assert observed['server_completed'] is True
        assert observed['service_release']['service_handle_released'] is True
        capture(prefix + '-native-receipt')
        return observed

    def capture_list(label, prefix, controls=None, alignment='center'):
        if controls is None:
            group = driver.find_element(
                By.CSS_SELECTOR, '[role="group"][aria-label=' +
                json.dumps(label) + ']')
            controls = group.find_elements(By.CSS_SELECTOR, 'input')
        assert controls
        for index, control in enumerate(controls):
            visibility = driver.execute_script('''
                const input = arguments[0];
                const field = input.closest('.MuiFormControl-root');
                if (!field) return {label_and_input_visible: false};
                field.scrollIntoView({block: arguments[1]});
                const label = field.querySelector('label');
                let visible = !!label;
                for (const item of [label, input].filter(Boolean)) {
                  const r = item.getBoundingClientRect();
                  visible &&= r.width > 0 && r.height > 0 &&
                    r.top >= 0 && r.left >= 0 &&
                    r.bottom <= innerHeight && r.right <= innerWidth;
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
                return {label_and_input_visible: visible};
            ''', control, alignment)
            geometry = assert_field_label_geometry(driver, control)
            state = prefix + '-control-' + str(index)
            capture(state)
            evidence['controls'][state].append({
                'label_geometry': geometry, **visibility})
            assert visibility['label_and_input_visible']

    def capture_filters(prefix):
        controls = []
        for label, help_text in (
            ('Skip data for tables matching',
             'Skip takes precedence over include'),
            ('Include data for tables matching',
             'not a Python regular expression'),
        ):
            control = visible_named_control(driver, label)
            assert control is not None
            description_ids = control.get_attribute('aria-describedby').split()
            assert help_text in ' '.join(driver.find_element(
                By.ID, item).text for item in description_ids)
            controls.append(control)
        # Long enlarged help paragraphs may exceed the scroll viewport.
        # Align the label/input at the top instead of centering the entire
        # paragraph (which can put the label above the viewport).
        capture_list('', prefix + '-filters', controls=controls,
                     alignment='start')
        for index, control in enumerate(controls):
            description = driver.find_element(
                By.ID, control.get_attribute('aria-describedby').split()[-1])
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'end'});", description)
            capture(prefix + '-filter-help-end-' + str(index))

    try:
        rows = [(index, secrets.token_hex(1000)) for index in range(100)]
        connection = native.connect(password=password,
                                    **_route_arguments(route, native))
        try:
            with connection.cursor() as cursor:
                cursor.execute('CREATE TABLE OWNED_UI_PAYLOAD '
                               '(ID INTEGER PRIMARY KEY, V VARCHAR(2000))')
                cursor.execute('CREATE TABLE "東京資料" (ID INTEGER)')
            connection.commit()
            with connection.cursor() as cursor:
                cursor.executemany(
                    'INSERT INTO OWNED_UI_PAYLOAD VALUES (?, ?)', rows)
                cursor.execute('INSERT INTO "東京資料" VALUES (1)')
            connection.commit()
        finally:
            connection.close()
        prepare_tree(driver, wait, options, password)
        database = wait_for_tree_item(wait, options.database)
        invoke_context_action(wait, driver, database,
                              ['Backup', 'Logical backup (gbak)...'],
                              password, endpoint_prompt_timeout=1)
        complete_endpoint_prompt(driver, password, timeout=1)
        fields = next(item['form']['fields']
                      for item in firebird_service_forms()
                      if item['operation_id'] == 'backup_logical')
        assert visible_named_control(
            driver, 'Backup filename on the Firebird server') is not None
        fill_form_values(driver, wait, fields, {
            'Backup filename on the Firebird server': path + '.single.fbk',
            'Include data for tables matching': '(OWNED%|東京%)',
            'Skip data for tables matching': 'NEVER%'})
        capture_filters('backup-single')
        planned = preview()
        assert planned['draft']['include_data'] == '(OWNED%|東京%)'
        assert planned['draft']['skip_data'] == 'NEVER%'
        capture('backup-single-unicode-filters-plan')
        apply('backup-single-unicode-filters')
        evidence['cases'].append('single-backup-unicode-combined-filters')
        close_workspace(driver, wait)
        database = wait_for_tree_item(wait, options.database)
        invoke_context_action(wait, driver, database,
                              ['Backup', 'Logical backup (gbak)...'],
                              password, endpoint_prompt_timeout=1)
        files = [path + f'.part-{part}.fbk' for part in range(1, 4)]
        volumes = [{'filename': files[0], 'size_bytes': 2048},
                   {'filename': files[1], 'size_bytes': 4096},
                   {'filename': files[2]}]
        fill_form_values(driver, wait, fields, {
            'Split backup across volumes': 'true',
            'Ordered backup volumes': volumes,
            'Backup options': ['ZIP'],
            'Include data for tables matching': '(OWNED%|東京%)',
            'Skip data for tables matching': 'NEVER%'})
        capture_filters('backup-split')
        assert visible_named_control(
            driver, 'Backup filename on the Firebird server') is None
        capture_list('Ordered backup volumes', 'backup-volume')
        capture('backup-visual-volumes')
        planned = preview()
        assert planned['draft']['backup_volumes'] == volumes
        assert 'backup_file' not in planned['draft']
        capture('backup-ordered-plan')
        press('Ordered backup volumes 2: Move up')
        assert not visible_named_control(
            driver, 'Apply provider plan').is_enabled()
        capture('backup-reorder-invalidates-plan')
        changed = preview()['draft']['backup_volumes']
        assert changed[:2] == [volumes[1], volumes[0]]
        capture('backup-capacities-follow-filenames')
        press('Ordered backup volumes 1: Move down')
        invalid = [{**volumes[0], 'size_bytes': 2047}, *volumes[1:]]
        fill_form_values(driver, wait, fields, {
            'Split backup across volumes': 'true',
            'Ordered backup volumes': invalid})
        press('Validate and preview')
        wait.until(lambda value: 'Capacity in bytes (empty for last volume) '
                   'is below its minimum.' in value.find_element(
                       By.CSS_SELECTOR, '[role="dialog"]').text and
                   not visible_named_control(
                       value, 'Apply provider plan').is_enabled())
        capture('backup-invalid-capacity-denied')
        fill_form_values(driver, wait, fields, {
            'Split backup across volumes': 'true',
            'Ordered backup volumes': volumes})
        assert preview()['draft']['backup_volumes'] == volumes
        apply('backup')
        evidence['cases'].append(
            'paired-volume-edit-reorder-validation-native-backup')
        close_workspace(driver, wait)
        operation = 'restore'
        fields = next(item['form']['fields']
                      for item in firebird_service_forms()
                      if item['operation_id'] == 'restore_logical')
        for index, mode in enumerate(('READ_WRITE', 'READ_ONLY')):
            database = wait_for_tree_item(wait, options.database)
            invoke_context_action(wait, driver, database,
                                  ['Restore', 'Logical restore (gbak)...'],
                                  password, endpoint_prompt_timeout=1)
            press('Continue')
            destination = path + ('.RESTORED.fdb' if index == 0 else
                                  '.RESTORED.PRESERVE.fdb')
            fill_form_values(driver, wait, fields, {
                'Backup filename on the Firebird server': files[0],
                'Additional backup volumes in order': files[1:],
                'Restored database filename on the Firebird server':
                destination,
                'Database access mode': mode,
                'Include data for tables matching': '%',
                'Skip data for tables matching': (
                    '東京%' if index == 0 else 'OWNED%')})
            capture_filters('restore-' + mode)
            capture_list('Additional backup volumes in order',
                         'restore-' + mode + '-volume')
            capture('restore-' + mode + '-visual-list')
            planned = preview()
            assert planned['draft']['additional_backup_files'] == files[1:]
            assert planned['draft']['access_mode'] == mode
            capture('restore-' + mode + '-plan')
            if index == 0:
                press('Additional backup volumes in order 2: Move up')
                assert not visible_named_control(
                    driver, 'Apply provider plan').is_enabled()
                capture('restore-reorder-invalidates-plan')
                press('Additional backup volumes in order 1: Move down')
                preview()
            observed = apply('restore-' + mode)
            assert observed['database'] == destination
            connection = native.connect(password=password, **_route_arguments(
                {**route, 'database': destination}, native))
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        'SELECT ID, V FROM OWNED_UI_PAYLOAD ORDER BY ID')
                    assert cursor.fetchall() == (rows if index == 0 else [])
                    cursor.execute('SELECT ID FROM "東京資料" ORDER BY ID')
                    assert cursor.fetchall() == ([] if index == 0 else [(1,)])
                    cursor.execute('SELECT MON$READ_ONLY FROM MON$DATABASE')
                    assert cursor.fetchone() == (index,)
                connection.rollback()
            finally:
                connection.close()
            capture('restore-' + mode + '-native-verified')
            evidence['cases'].append('ordered-restore-payload-' + mode)
            close_workspace(driver, wait)
        evidence['passed'] = len(evidence['cases']) == 4
    except Exception as exc:
        evidence['error_type'] = type(exc).__name__
        capture(operation + '-failure')
        raise
    finally:
        try:
            options.summary_output.parent.mkdir(parents=True, exist_ok=True)
            options.summary_output.write_text(
                json.dumps(evidence, indent=2) + '\n')
            for action in ('backup', 'restore'):
                screenshots = {key: value for key, value in
                               evidence['screenshots'].items()
                               if key.startswith(action + '-')}
                _write_records(
                    options, {**evidence, 'screenshots': screenshots},
                    command_id=f'database.firebird.{action}_logical',
                    form_id=f'firebird_{action}_logical',
                    proof_id='firebird-logical-volumes-ui-gate')
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
