#!/usr/bin/env python3
"""Exercise individual database storage forms on an owned Firebird fixture."""

import json
import os
import re

from cdeadmin_firebird_role_ui_gate import (
    arguments, forms, firebird, _route_arguments, create_driver,
    close_workspace, plan_preview, WebDriverWait, visible_named_control,
)
from cdeadmin_firebird_query_ui_gate import _load_profile
from cdeadmin_firebird_ui_form_gate import (
    click_unobscured, screenshot_form_pages, screenshot, fill_form_values,
)
from cdeadmin_firebird_logical_volumes_gate import (
    docker, OWNER, published_port,
)


def run(options, profiles):
    route = _load_profile(profiles)
    container = route.get('owned_container_id', '')
    database = '/var/lib/firebird/data/owned_storage_browser.fdb'
    if (route.get('fixture_kind') != 'firebird-storage-qualification' or
            route['host'] != '127.0.0.1' or
            route['database'] != database or database != options.database_path
            or not re.fullmatch('[0-9a-f]{64}', container)):
        raise ValueError('Storage browser tests require an owned server')
    label = docker('inspect', '--format',
                   '{{index .Config.Labels "cdeadmin-owned-gate"}}',
                   container).decode().strip()
    if label != OWNER or published_port(container) != route['port']:
        raise ValueError('Owned storage server identity or port differs')
    firebird.driver_config.fb_client_library.value = os.environ[
        'CDEADMIN_FIREBIRD_CLIENT_LIBRARY']
    browser = None
    result = {'passed': False, 'checks': [], 'failures': [],
              'invalid_forms': [],
              'expected_mutation_count': 7,
              'credential_values_exported': False,
              'target_database': database}

    def observe():
        with firebird.connect(password=route['password'],
                              **_route_arguments(route, firebird)) as native:
            with native.cursor() as cursor:
                cursor.execute('SELECT RDB$FILE_NAME, RDB$FILE_FLAGS '
                               'FROM RDB$FILES')
                files = cursor.fetchall()
                cursor.execute('SELECT MON$BACKUP_STATE FROM MON$DATABASE')
                state = cursor.fetchone()[0]
                return files, state

    def apply(operation_id, values, expected_state, expected_difference):
        probe = forms._workspace_probe(browser, ['database'], False)
        descriptor = next(item for item in probe['catalog']['objects']
                          if item['resource_kind'] == 'database')
        operation = {**next(item for item in descriptor['operations']
                            if item['operation_id'] == operation_id),
                     'resource_kind': 'database'}
        assert operation['execution_available'] is True
        assert operation['form']['form_id'] == (
            'firebird.database.' + operation_id)
        selected = next(item for item in probe['resources'] if
                        item['resource_kind'] == 'database')
        forms._open_focused_form(browser, operation, selected,
                                 probe['database_target_id'])
        forms._wait_for_operation(wait, operation)
        labels = {field['field_id']: field['label'] for field in
                  operation['form']['fields']}
        invalid = [('wrong_database', {
            'confirmation': database + '.wrong', **values},
            'Confirm the exact database path or alias')]
        if operation_id == 'begin_backup':
            invalid.append(('missing_overwrite_ack', {
                'confirmation': database,
                'confirm_difference_overwrite': False},
                'Confirm that the difference-file path'))
        for case, draft, message in invalid:
            fill_form_values(browser, wait, operation['form']['fields'], {
                labels[key]: value for key, value in draft.items()})
            validate = wait.until(lambda driver: visible_named_control(
                driver, 'Validate and preview'))
            click_unobscured(browser, wait, validate)
            wait.until(lambda driver: message in driver.find_element(
                'tag name', 'body').text)
            assert not browser.find_elements(
                'css selector', '[aria-label="Provider plan preview"]')
            button = visible_named_control(browser, 'Apply provider plan')
            assert button is None or not button.is_enabled()
            result['invalid_forms'].append({'operation': operation_id,
                                            'case': case,
                                            'mutation_not_admitted': True})
        plan = plan_preview(browser, wait, operation, {
            labels[key]: value for key, value in
            {'confirmation': database, **values}.items()})
        pages = screenshot_form_pages(browser, options.output_root / (
            f'{len(result["checks"]):02d}-{operation_id}'))
        confirmation = visible_named_control(
            browser, 'I confirm this provider-planned operation.')
        if confirmation is not None and not confirmation.is_selected():
            click_unobscured(browser, wait, confirmation)
        button = wait.until(lambda driver: visible_named_control(
            driver, 'Apply provider plan'))
        wait.until(lambda _driver: button.is_enabled())
        click_unobscured(browser, wait, button)
        output = wait.until(lambda driver: next((
            item for item in driver.find_elements(
                'css selector', '[aria-label="Provider operation result"]')
            if item.is_displayed()), None))
        assert json.loads(output.text)['accepted'] is True
        wait.until(lambda driver: driver.execute_script("""
            return Array.from(document.querySelectorAll('button')).some(
              item => item.textContent.trim() === 'Validate and preview' &&
              item.getClientRects().length && !item.disabled &&
              item.getAttribute('aria-disabled') !== 'true');
            """))
        files, state = observe()
        assert state == expected_state
        differences = [name for name, flags in files if (flags or 0) & 32]
        assert differences == expected_difference
        if operation_id == 'add_files':
            assert all((item['filename'], 0) in files for item in
                       values['files'])
        result['checks'].append({'operation': operation_id,
                                 'native_postconditions': True,
                                 'plan': plan, 'screenshots': pages})
        close_workspace(browser, wait)

    try:
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        actions = browser.execute_script("""
            const app = window.pgAdmin;
            const item = window.__cdeadminQaDatabaseItem;
            return app.Browser.tree.itemData(item).cde_context_actions || [];
            """)
        for operation in ('add_files', 'add_difference_file',
                          'drop_difference_file', 'begin_backup',
                          'end_backup'):
            action = next(item for item in actions if item['command_id'] ==
                          'database.firebird.' + operation)
            assert action['enabled'] is True
            assert action['arguments']['operation_id'] == operation
            assert action['arguments']['database_target_id']
        result['database_context_actions_verified'] = True
        prefix = database + '_' + str(options.font_scale)
        start = 512
        difference = prefix + "_影's.delta"
        apply('add_difference_file', {'filename': difference}, 0, [difference])
        apply('begin_backup', {'confirm_difference_overwrite': True},
              1, [difference])
        apply('end_backup', {}, 0, [difference])
        apply('drop_difference_file', {}, 0, [])
        apply('begin_backup', {'confirm_difference_overwrite': True},
              1, [None])
        apply('end_backup', {}, 0, [])
        apply('add_files', {'files': [
            {'filename': prefix + "_影's.extra", 'start': start,
             'length': 256},
            {'filename': prefix + '_second.extra', 'start': start + 512,
             'length': 256}]}, 0, [])
        result['passed'] = (len(result['checks']) == 7 and
                            len(result['invalid_forms']) == 9)
    except Exception as error:
        result['failures'].append({'type': type(error).__name__})
        if browser is not None:
            screenshot(browser, options.output_root / 'failure.png')
        raise
    finally:
        if browser is not None:
            forms._quit_driver(browser)
        options.summary_output.parent.mkdir(parents=True, exist_ok=True)
        options.summary_output.write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    options, profiles = arguments()
    outcome = run(options, profiles)
    print(json.dumps({'passed': outcome['passed'],
                      'checks': len(outcome['checks'])}))
    raise SystemExit(0 if outcome['passed'] else 1)
