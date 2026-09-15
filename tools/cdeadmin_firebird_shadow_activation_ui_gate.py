#!/usr/bin/env python3
"""Recover an owned shadow through the server popup with no live primary."""

import json
import os
import re
import traceback
from pathlib import Path

from cdeadmin_firebird_role_ui_gate import (
    arguments, forms, firebird, _route_arguments, create_driver,
    close_workspace, WebDriverWait, visible_named_control,
)
from cdeadmin_firebird_query_ui_gate import _load_profile
from cdeadmin_firebird_ui_form_gate import (
    click_unobscured, screenshot_form_pages, screenshot, fill_form_values,
    MENU_GROUP_LABELS, invoke_context_action,
)
from cdeadmin_firebird_logical_volumes_gate import (
    docker, OWNER, published_port,
)


def run(options, profiles):
    route = _load_profile(profiles)
    container = route.get('owned_container_id', '')
    database = ('/var/lib/firebird/data/'
                f'owned_activation_browser_{options.font_scale}.fdb')
    shadow = database + "_影's.shd"
    denied = route.get('expected_privilege_denial') is True
    if (route.get('fixture_kind') !=
            'firebird-shadow-activation-qualification' or
            route['host'] != '127.0.0.1' or route['database'] != database or
            route.get('shadow_filename') != shadow or
            options.database_path != database or
            not re.fullmatch('[0-9a-f]{64}', container)):
        raise ValueError('Shadow recovery requires an owned server fixture')
    label = docker('inspect', '--format',
                   '{{index .Config.Labels "cdeadmin-owned-gate"}}',
                   container).decode().strip()
    if label != OWNER or published_port(container) != route['port']:
        raise ValueError('Owned recovery server identity or port differs')
    assert docker('exec', container, 'sh', '-c',
                  'test ! -e "$1" && test -f "$2"', 'gate',
                  database, database + '.isolated') == b''
    firebird.driver_config.fb_client_library.value = os.environ[
        'CDEADMIN_FIREBIRD_CLIENT_LIBRARY']
    browser = None
    result = {'passed': False, 'invalid_forms': [], 'failures': [],
              'credential_values_exported': False,
              'original_database_unavailable': True}

    def ready_or_credentials(predicate):
        def password_visible(driver):
            return any(item.is_displayed() for item in driver.find_elements(
                'css selector', '[role=dialog] input[type=password]'))
        wait.until(lambda driver:
                   password_visible(driver) or predicate(driver))
        if password_visible(browser):
            forms.complete_endpoint_prompt(browser, route['password'],
                                           timeout=options.timeout)
        return wait.until(predicate)

    try:
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        browser.get(options.url.rstrip('/') + '/browser/')
        wait.until(lambda value: '/browser/' in value.current_url)
        forms.apply_presentation(browser, wait, options)
        forms.ensure_data_explorer(wait)
        for parent, child in (('Connectors', options.engine),
                              (options.engine, options.server)):
            forms.expand(wait, parent)
            forms.wait_for_tree_item(wait, child)
        endpoint = forms.wait_for_tree_item(wait, options.server)
        forms._context_click_visible_label(browser, wait, endpoint)
        wait.until(lambda driver: driver.execute_script('''
            const tree = window.pgAdmin.Browser.tree;
            const item = tree.selected();
            if (tree.itemData(item)?._type !== 'server') return false;
            window.__cdeadminQaEndpointItem = item;
            return true;
        '''))
        forms.ActionChains(browser).send_keys(forms.Keys.ESCAPE).perform()
        browser.execute_script('''
            const app = window.pgAdmin, tree = app.Browser.tree;
            const item = window.__cdeadminQaEndpointItem;
            if (tree.itemData(item)._type !== 'server')
              throw new Error('Expected server selection');
            window.__cdeadminQaEndpointItem = item;
            const node = app.Browser.Nodes.server;
            node.callbacks.open_cde_workspace.call(node, {item},
              'administration', {database_target_id: null,
                resource_kind: 'database', operation_id: 'activate_shadow'});
        ''')
        ready_or_credentials(lambda driver: visible_named_control(
            driver, 'First shadow filename'))
        close_workspace(browser, wait)
        # Reopening the actual menu refreshes its authenticated action state.
        endpoint = forms.wait_for_tree_item(wait, options.server)
        forms._context_click_visible_label(browser, wait, endpoint)
        action = wait.until(lambda driver: driver.execute_script('''
            const tree = window.pgAdmin.Browser.tree;
            return (tree.itemData(tree.selected()).cde_context_actions || [])
              .find(item => item.command_id ===
                'endpoint.firebird.activate_shadow' && item.enabled);
        '''))
        forms.ActionChains(browser).send_keys(forms.Keys.ESCAPE).perform()
        assert action['arguments']['database_target_id'] is None
        endpoint = forms.wait_for_tree_item(wait, options.server)
        invoke_context_action(
            wait, browser, endpoint,
            [MENU_GROUP_LABELS[action['menu_group']], action['label']],
            route['password'], endpoint_prompt_timeout=1)
        if action['requires_confirmation']:
            button = wait.until(lambda driver: visible_named_control(
                driver, 'Continue'))
            click_unobscured(browser, wait, button)
        wait.until(lambda driver: visible_named_control(
            driver, 'First shadow filename'))
        result['server_popup_verified'] = True
        fields = [
            {'field_id': 'shadow_filename', 'label': 'First shadow filename',
             'control': 'text', 'required': True},
            {'field_id': 'confirmation', 'label': 'Confirm shadow filename',
             'control': 'text', 'required': True},
            {'field_id': 'original_isolated',
             'label': 'The original database is stopped or isolated',
             'control': 'boolean', 'required': True, 'default': False},
            {'field_id': 'role', 'label': 'SQL role', 'control': 'text'},
        ]
        result['controls'] = forms.assert_form_controls(wait, fields)
        base = {'First shadow filename': shadow,
                'Confirm shadow filename': shadow,
                'The original database is stopped or isolated': True,
                'SQL role': 'RECOVERY_OBSERVER' if denied else ''}
        for label, changed, message in (
                ('wrong-confirmation', {'Confirm shadow filename': 'wrong'},
                 'Confirm the exact shadow filename'),
                ('missing-isolation', {
                    'The original database is stopped or isolated': False},
                 'Confirm that the original database is stopped or isolated')):
            fill_form_values(browser, wait, fields, {**base, **changed})
            click_unobscured(browser, wait, visible_named_control(
                browser, 'Validate and preview'))
            wait.until(lambda driver: message in driver.find_element(
                'tag name', 'body').text)
            assert not browser.find_elements(
                'css selector', '[aria-label="Provider plan preview"]')
            result['invalid_forms'].append(label)
        fill_form_values(browser, wait, fields, base)
        click_unobscured(browser, wait, visible_named_control(
            browser, 'Validate and preview'))
        plan = ready_or_credentials(lambda driver: driver.find_element(
            'css selector', '[aria-label="Provider plan preview"]'))
        assert 'activate_shadow' in plan.text
        result['form_screenshots'] = screenshot_form_pages(
            browser, options.output_root / 'recovery-plan')
        checkbox = visible_named_control(
            browser, 'I confirm this provider-planned operation.')
        if not checkbox.is_selected():
            click_unobscured(browser, wait, checkbox)
        button = visible_named_control(browser, 'Apply provider plan')
        wait.until(lambda _driver: button.is_enabled())
        # Count only the reviewed action name, never capture request bodies or
        # credentials. This instrumentation lives in the owned browser page.
        browser.execute_script('''
          window.__ownedRecoveryDispatches = 0;
          const send = XMLHttpRequest.prototype.send;
          XMLHttpRequest.prototype.send = function(body) {
            try {
              if (JSON.parse(body)?.action === 'visual_admin_apply')
                window.__ownedRecoveryDispatches++;
            } catch (_) { /* Non-JSON requests are outside this counter. */ }
            return send.apply(this, arguments);
          };
        ''')
        click_unobscured(browser, wait, button)
        if denied:
            ready_or_credentials(lambda driver: 'outcome is unknown' in
                                 driver.find_element('tag name', 'body').text)
            text = browser.find_element('tag name', 'body').text
            assert '335544788' in text and '335545112' in text
            assert 'plan is retired' in text
            assert 'will not be automatically retried' in text
            assert not browser.find_elements(
                'css selector', '[aria-label="Provider operation result"]')
            result['native_denial_visible'] = True
        else:
            receipt = ready_or_credentials(lambda driver: driver.find_element(
                'css selector',
                '[aria-label="Firebird native service receipt"]'))
            observed = json.loads(receipt.get_attribute('textContent'))
            assert observed['server_completed'] is True
            assert observed['shadow_header_verification'] == {
                'first_file_verified': True, 'active_shadow_verified': True}
            assert observed['service_release'][
                'service_handle_released'] is True
        assert not visible_named_control(
            browser, 'Apply provider plan').is_enabled()
        result['submitted_plan_retired'] = True
        oracle_route = {**route, 'database': shadow,
                        'user': 'SYSDBA' if denied else route.get('user')}
        with firebird.connect(password=route.get(
                'oracle_password', route['password']), **_route_arguments(
                    oracle_route, firebird)) as native:
            with native.cursor() as cursor:
                cursor.execute('SELECT ID FROM RECOVERY_MARKER ORDER BY ID')
                assert cursor.fetchall() == [(1,), (2,)]
        result['native_recovered_rows_verified'] = True
        if denied:
            status = browser.find_element(
                'css selector', '[aria-label="Provider workspace status"]')
            geometry = browser.execute_script('''
                const node = arguments[0];
                return {height: node.clientHeight,
                  maximum: node.scrollHeight - node.clientHeight,
                  tabIndex: node.tabIndex};
            ''', status)
            assert geometry['height'] > 0 and geometry['tabIndex'] == 0
            status.send_keys(forms.Keys.END)
            if geometry['maximum'] > 0:
                wait.until(lambda driver: driver.execute_script(
                    'return arguments[0].scrollTop > 0', status))
            result['status_screenshots'] = []
            step = max(1, geometry['height'] - 64)
            offsets = list(dict.fromkeys([
                *range(0, geometry['maximum'] + 1, step),
                geometry['maximum']]))
            assert len(offsets) <= 100
            for index, offset in enumerate(offsets):
                actual = browser.execute_script(
                    'arguments[0].scrollTop = arguments[1]; '
                    'return arguments[0].scrollTop;', status, offset)
                assert abs(actual - offset) <= 1
                path = options.output_root / (
                    f'recovery-error-page-{index + 1:02d}.png')
                result['status_screenshots'].append({
                    'path': str(path), 'scroll_top': actual,
                    'sha256': screenshot(browser, path, reset_scroll=False)})
            result['status_keyboard_scroll_verified'] = True
        result['apply_dispatches'] = browser.execute_script(
            'return window.__ownedRecoveryDispatches')
        assert result['apply_dispatches'] == 1
        result['result_screenshots'] = screenshot_form_pages(
            browser, options.output_root / 'recovery-completed')
        close_workspace(browser, wait)
        result['passed'] = True
    except Exception as error:
        result['failures'].append({
            'type': type(error).__name__,
            'frames': [{'file': Path(frame.filename).name,
                        'line': frame.lineno, 'function': frame.name}
                       for frame in traceback.extract_tb(
                           error.__traceback__)]})
        if browser is not None:
            result['network_observations'] = browser.execute_script('''
                return performance.getEntriesByType('resource').slice(-50)
                  .map(item => ({path: new URL(item.name).pathname,
                    status: item.responseStatus, duration: item.duration}));
            ''')
            result['endpoint_state'] = browser.execute_script('''
                const tree = window.pgAdmin?.Browser?.tree;
                const data = tree?.itemData(window.__cdeadminQaEndpointItem);
                return {type: data?._type, id: data?._id,
                  provider_endpoint: Boolean(data?.cde_endpoint),
                  profile: data?.cde_profile_id,
                  authenticated: data?.cde_session_authenticated,
                  verification: data?.runtime_verification_state};
            ''')
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
    print(json.dumps({'passed': outcome['passed']}))
    raise SystemExit(0 if outcome['passed'] else 1)
