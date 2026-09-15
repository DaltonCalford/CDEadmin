#!/usr/bin/env python3
"""Verify native validation findings in real forms on an owned damaged copy."""

import json
import os
import re

from cdeadmin_firebird_role_ui_gate import (
    arguments, forms, firebird, _route_arguments, create_driver,
    close_workspace, plan_preview, WebDriverWait, visible_named_control,
)
from cdeadmin_firebird_query_ui_gate import _load_profile
from cdeadmin_firebird_ui_form_gate import (
    click_unobscured, screenshot_form_pages, screenshot,
    MENU_GROUP_LABELS, invoke_context_action, accessibility_observation,
)
from cdeadmin_firebird_logical_volumes_gate import docker, OWNER


def run(options, profiles):
    route = _load_profile(profiles)
    container = route.get('owned_container_id', '')
    database = ('/var/lib/firebird/data/owned_repair_damage_browser_' +
                str(options.font_scale) + '.fdb')
    if (route.get('fixture_kind') != 'firebird-repair-damage-qualification' or
            route['host'] != '127.0.0.1' or route['database'] != database or
            options.database_path != database or
            not re.fullmatch('[0-9a-f]{64}', container)):
        raise ValueError('Damaged repair tests require an owned server')
    if docker('inspect', '--format',
              '{{index .Config.Labels "cdeadmin-owned-gate"}}',
              container).decode().strip() != OWNER:
        raise ValueError('Owned repair server identity differs')
    if docker('port', container, '3050/tcp').decode().strip() != (
            '127.0.0.1:' + str(route['port'])):
        raise ValueError('Owned repair server port differs')
    firebird.driver_config.fb_client_library.value = os.environ[
        'CDEADMIN_FIREBIRD_CLIENT_LIBRARY']
    browser = None
    result = {'passed': False, 'checks': [], 'failures': [],
              'fixture_scope': 'copied index-page relation mismatch only',
              'credential_values_exported': False}
    try:
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        probe = forms._workspace_probe(browser, ['database'], False)
        descriptor = next(item for item in probe['catalog']['objects']
                          if item['resource_kind'] == 'database')
        operation = {**next(item for item in descriptor['operations']
                            if item['operation_id'] == 'repair_database'),
                     'resource_kind': 'database'}
        browser.execute_script('''
            window.__ownedDamageDispatches = 0;
            const send = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.send = function(body) {
              try {
                if (JSON.parse(body)?.action === 'visual_admin_apply')
                  window.__ownedDamageDispatches++;
              } catch (_) { /* Count actions only, never capture payloads. */ }
              return send.apply(this, arguments);
            };
        ''')
        cases = [(action, modifiers, no_linger)
                 for action, modifiers in (
                     ('VALIDATE_DB', ['FULL', 'CHECK_DB']),
                     ('MEND_DB', ['CHECK_DB']), ('CORRUPTION_CHECK', []))
                 for no_linger in (False, True)]
        for action, modifiers, no_linger in cases:
            prefix = action + '-no-linger-' + str(no_linger)
            item = forms.wait_for_tree_item(wait, options.database)
            forms._context_click_visible_label(browser, wait, item)
            command = wait.until(lambda driver: driver.execute_script('''
                const tree = window.pgAdmin.Browser.tree;
                return (tree.itemData(tree.selected())
                  .cde_context_actions || []).find(item =>
                  item.command_id === 'database.firebird.repair_database' &&
                  item.enabled);
            '''))
            assert command['arguments']['database_target_id'] == (
                probe['database_target_id'])
            forms.ActionChains(browser).send_keys(forms.Keys.ESCAPE).perform()
            item = forms.wait_for_tree_item(wait, options.database)
            invoke_context_action(wait, browser, item, [
                MENU_GROUP_LABELS[command['menu_group']], command['label']],
                route['password'], endpoint_prompt_timeout=1)
            if command['requires_confirmation']:
                button = wait.until(lambda driver: visible_named_control(
                    driver, 'Continue'))
                click_unobscured(browser, wait, button)
            forms._wait_for_operation(wait, operation)
            fields = operation['form']['fields']
            controls = forms.assert_form_controls(wait, fields)
            accessibility = accessibility_observation(
                browser, wait, operation, controls)
            plan_preview(browser, wait, operation, {
                'Repair action': action,
                'Native validation modifiers': modifiers,
                'Do not linger after this maintenance task': no_linger,
                'ICU parallel workers requested': '', 'SQL role': ''})
            assert browser.execute_script(
                'return window.__ownedDamageDispatches') == len(
                    result['checks'])
            pages = screenshot_form_pages(
                browser, options.output_root / (prefix + '-plan'))
            confirmation = visible_named_control(
                browser, 'I confirm this provider-planned operation.')
            assert confirmation is not None
            if not confirmation.is_selected():
                click_unobscured(browser, wait, confirmation)
            button = wait.until(lambda driver: visible_named_control(
                driver, 'Apply provider plan'))
            wait.until(lambda _driver: button.is_enabled())
            click_unobscured(browser, wait, button)
            wait.until(lambda driver: 'outcome is unknown' in
                       driver.find_element('tag name', 'body').text)
            text = browser.find_element('tag name', 'body').text
            assert '335740952' in text and '335740986' in text
            assert 'plan is retired' in text
            assert 'will not be automatically retried' in text
            assert not browser.find_elements(
                'css selector', '[aria-label="Provider operation result"]')
            assert not visible_named_control(
                browser, 'Apply provider plan').is_enabled()
            status = browser.find_element(
                'css selector', '[aria-label="Provider workspace status"]')
            status.send_keys(forms.Keys.END)
            error_pages = screenshot_form_pages(
                browser, options.output_root / (prefix + '-native-findings'))
            connection = firebird.connect(
                password=route['password'],
                **_route_arguments(route, firebird))
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT ID, NOTE FROM OUTPUT_MARKER '
                                   'PLAN (OUTPUT_MARKER NATURAL)')
                    assert cursor.fetchall() == [(1, 'preserve')]
            finally:
                connection.close()
            assert browser.execute_script(
                'return window.__ownedDamageDispatches') == (
                    len(result['checks']) + 1)
            result['checks'].append({
                'action': action,
                'no_linger_requested': no_linger,
                'native_status_codes': [335740952, 335740986],
                'apply_dispatch_count': 1, 'submitted_plan_retired': True,
                'native_findings_visible': True, 'no_false_success': True,
                'natural_scan_rows_preserved': True,
                'accessibility': accessibility, 'plan_screenshots': pages,
                'error_screenshots': error_pages})
            close_workspace(browser, wait)
        result['passed'] = len(result['checks']) == len(cases)
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
