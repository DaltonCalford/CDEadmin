#!/usr/bin/env python3
"""Exercise availability task forms including recovery from FULL shutdown."""

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
    MENU_GROUP_LABELS, invoke_context_action, accessibility_observation,
)
from cdeadmin_firebird_logical_volumes_gate import docker, OWNER
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes


def run(options, profiles):
    route = _load_profile(profiles)
    container = route.get('owned_container_id', '')
    database = ('/var/lib/firebird/data/owned_availability_browser_' +
                str(options.font_scale) + '.fdb')
    if (route.get('fixture_kind') != 'firebird-availability-qualification' or
            route['host'] != '127.0.0.1' or route['database'] != database or
            options.database_path != database or
            not re.fullmatch('[0-9a-f]{64}', container)):
        raise ValueError('Availability tests require an owned server')
    if docker('inspect', '--format',
              '{{index .Config.Labels "cdeadmin-owned-gate"}}',
              container).decode().strip() != OWNER:
        raise ValueError('Owned availability server identity differs')
    if docker('port', container, '3050/tcp').decode().strip() != (
            '127.0.0.1:' + str(route['port'])):
        raise ValueError('Owned availability server port differs')
    firebird.driver_config.fb_client_library.value = os.environ[
        'CDEADMIN_FIREBIRD_CLIENT_LIBRARY']
    browser = None
    result = {'passed': False, 'checks': [], 'failures': [],
              'credential_values_exported': False,
              'target_database': database}

    def native_state(expected):
        try:
            connection = firebird.connect(
                password=route['password'],
                **_route_arguments(route, firebird))
        except firebird.Error as error:
            assert expected == 'FULL' and 335544528 in status_codes(error)
            return {'ordinary_attach_denied_codes': list(status_codes(error))}
        try:
            assert expected != 'FULL'
            with connection.cursor() as cursor:
                cursor.execute('SELECT MON$SHUTDOWN_MODE FROM MON$DATABASE')
                observed = cursor.fetchone()[0]
            assert observed == {'NORMAL': 0, 'MULTI': 1, 'SINGLE': 2}[expected]
            return {'shutdown_mode': observed}
        finally:
            connection.close()

    try:
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        probe = forms._workspace_probe(browser, ['database'], False)
        descriptor = next(item for item in probe['catalog']['objects']
                          if item['resource_kind'] == 'database')
        for operation_id, values, expected in (
                ('shutdown_database', {'Shutdown mode': 'FULL',
                                       'Shutdown method': 'FORCED',
                                       'Timeout in seconds': 0}, 'FULL'),
                ('bring_online', {'Online mode': 'SINGLE'}, 'SINGLE'),
                ('bring_online', {'Online mode': 'MULTI'}, 'MULTI'),
                ('bring_online', {'Online mode': 'NORMAL'}, 'NORMAL'),
                ('shutdown_database', {'Shutdown mode': 'SINGLE',
                                       'Shutdown method': 'DENY_TRANSACTIONS',
                                       'Timeout in seconds': 0}, 'SINGLE'),
                ('bring_online', {'Online mode': 'NORMAL'}, 'NORMAL'),
                ('shutdown_database', {'Shutdown mode': 'MULTI',
                                       'Shutdown method': 'DENY_ATTACHMENTS',
                                       'Timeout in seconds': 0}, 'MULTI'),
                ('bring_online', {'Online mode': 'NORMAL'}, 'NORMAL')):
            operation = {**next(item for item in descriptor['operations']
                                if item['operation_id'] == operation_id),
                         'resource_kind': 'database'}
            item = forms.wait_for_tree_item(wait, options.database)
            forms._context_click_visible_label(browser, wait, item)
            action = wait.until(lambda driver: driver.execute_script('''
                const tree = window.pgAdmin.Browser.tree;
                const actions = tree.itemData(tree.selected())
                  .cde_context_actions || [];
                return actions.find(item =>
                  item.command_id === arguments[0] && item.enabled);
            ''', 'database.firebird.' + operation_id))
            assert action['arguments']['database_target_id'] == (
                probe['database_target_id'])
            forms.ActionChains(browser).send_keys(forms.Keys.ESCAPE).perform()
            item = forms.wait_for_tree_item(wait, options.database)
            invoke_context_action(wait, browser, item, [
                MENU_GROUP_LABELS[action['menu_group']], action['label']],
                route['password'], endpoint_prompt_timeout=1)
            if action['requires_confirmation']:
                button = wait.until(lambda driver: visible_named_control(
                    driver, 'Continue'))
                click_unobscured(browser, wait, button)
            forms._wait_for_operation(wait, operation)
            wait.until(lambda driver: (button := visible_named_control(
                driver, 'Validate and preview')) is not None and
                button.is_enabled())
            fields = operation['form']['fields']
            controls = forms.assert_form_controls(wait, fields)
            accessibility = accessibility_observation(
                browser, wait, operation, controls)
            if operation_id == 'shutdown_database':
                fill_form_values(browser, wait, fields, {
                    **values, 'Timeout in seconds': 32768})
                click_unobscured(browser, wait, visible_named_control(
                    browser, 'Validate and preview'))
                rejection = wait.until(lambda driver: next((
                    alert for alert in driver.find_elements(
                        'css selector', '[role="alert"]')
                    if 'Timeout in seconds exceeds its maximum.' in
                    alert.text), None))
                browser.execute_script(
                    'arguments[0].scrollIntoView({block: "center"});',
                    rejection)
                assert rejection.is_displayed()
                screenshot_form_pages(browser, options.output_root / (
                    str(len(result['checks'])) + '-invalid-timeout'))
                wait.until(lambda driver: visible_named_control(
                    driver, 'Validate and preview').is_enabled())
                assert not browser.find_elements(
                    'css selector', '[aria-label="Provider plan preview"]')
            plan = plan_preview(browser, wait, operation, values)
            assert 'before returning a later access error' in str(
                plan['warnings'])
            prefix = str(len(result['checks'])) + '-' + operation_id
            pages = screenshot_form_pages(browser, options.output_root / (
                prefix + '-plan'))
            confirmation = visible_named_control(
                browser, 'I confirm this provider-planned operation.')
            if confirmation is not None and not confirmation.is_selected():
                click_unobscured(browser, wait, confirmation)
            button = wait.until(lambda driver: visible_named_control(
                driver, 'Apply provider plan'))
            wait.until(lambda _driver: button.is_enabled())
            click_unobscured(browser, wait, button)
            result_selector = '[aria-label="Firebird service result"]'
            output = wait.until(lambda driver: next((
                item for item in driver.find_elements(
                    'css selector', result_selector)
                if item.is_displayed()), None))
            assert 'Firebird' in output.text
            observation = native_state(expected)
            result_pages = screenshot_form_pages(
                browser, options.output_root / (prefix + '-result'))
            result['checks'].append({
                'operation': operation_id, 'expected_mode': expected,
                'native_post_state': observation,
                'database_popup_verified': True,
                'accessibility': accessibility, 'plan_screenshots': pages,
                'result_screenshots': result_pages})
            wait.until(lambda driver: (button := visible_named_control(
                driver, 'Validate and preview')) is not None and
                button.is_enabled())
            close_workspace(browser, wait)
        result['passed'] = len(result['checks']) == 8
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
    print(json.dumps(outcome))
    raise SystemExit(0 if outcome['passed'] else 1)
