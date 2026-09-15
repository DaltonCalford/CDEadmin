#!/usr/bin/env python3
"""Exercise prepared transaction forms in owned databases."""

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
    MENU_GROUP_LABELS, invoke_context_action,
    accessibility_observation,
)
from cdeadmin_firebird_logical_volumes_gate import docker, OWNER
from pgadmin.cdeadmin.providers.firebird import limbo


def run(options, profiles):
    route = _load_profile(profiles)
    container = route.get('owned_container_id', '')
    database = ('/var/lib/firebird/data/owned_limbo_browser_' +
                str(options.font_scale) + '.fdb')
    if (route.get('fixture_kind') != 'firebird-limbo-qualification' or
            route['host'] != '127.0.0.1' or route['database'] != database or
            options.database_path != database or
            not re.fullmatch('[0-9a-f]{64}', container)):
        raise ValueError('Limbo browser tests require an owned server')
    if docker('inspect', '--format',
              '{{index .Config.Labels "cdeadmin-owned-gate"}}',
              container).decode().strip() != OWNER:
        raise ValueError('Owned limbo server identity differs')
    published = docker('port', container, str(route['port']) + '/tcp')
    if published.decode().strip() != '127.0.0.1:' + str(route['port']):
        raise ValueError('Owned limbo server port differs')
    firebird.driver_config.fb_client_library.value = os.environ[
        'CDEADMIN_FIREBIRD_CLIENT_LIBRARY']
    browser = None
    result = {'passed': False, 'checks': [], 'failures': [],
              'invalid_forms': [], 'credential_values_exported': False,
              'target_database': database}

    def connect():
        return firebird.connect(password=route['password'],
                                **_route_arguments(route, firebird))

    def prepare(value):
        connection = connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO RECOVERY_MARKER VALUES (?)',
                               (value,))
                cursor.execute('SELECT CURRENT_TRANSACTION FROM RDB$DATABASE')
                identifier = str(cursor.fetchone()[0])
            transaction = connection.main_transaction._tra
            transaction.prepare()
            connection._att.detach()
            connection._att = None
            transaction.release()
            connection.main_transaction._tra = None
            return identifier
        finally:
            connection.close()

    def task(operation_id, identifier=None):
        probe = forms._workspace_probe(browser, ['database'], False)
        descriptor = next(item for item in probe['catalog']['objects']
                          if item['resource_kind'] == 'database')
        operation = {**next(item for item in descriptor['operations']
                            if item['operation_id'] == operation_id),
                     'resource_kind': 'database'}
        assert operation['execution_available'] is True
        database_item = forms.wait_for_tree_item(wait, options.database)
        forms._context_click_visible_label(browser, wait, database_item)
        action = wait.until(lambda driver: driver.execute_script('''
            const tree = window.pgAdmin.Browser.tree;
            return (tree.itemData(tree.selected()).cde_context_actions || [])
              .find(item => item.command_id === arguments[0] && item.enabled);
        ''', 'database.firebird.' + operation_id))
        assert action['arguments']['database_target_id'] == (
            probe['database_target_id'])
        forms.ActionChains(browser).send_keys(forms.Keys.ESCAPE).perform()
        # Loading authenticated actions replaces the virtualized tree row.
        database_item = forms.wait_for_tree_item(wait, options.database)
        invoke_context_action(
            wait, browser, database_item,
            [MENU_GROUP_LABELS[action['menu_group']], action['label']],
            route['password'], endpoint_prompt_timeout=1)
        if action['requires_confirmation']:
            button = wait.until(lambda driver: visible_named_control(
                driver, 'Continue'))
            wait.until(lambda _driver: button.is_enabled())
            click_unobscured(browser, wait, button)
        forms._wait_for_operation(wait, operation)
        popup = browser.execute_script(
            'return window.__ownedLimboMenus.at(-1)')
        assert popup['command'] == 'database.firebird.' + operation_id
        assert popup['top'] >= -1 and popup['bottom'] <= popup['viewport'] + 1
        assert popup['left'] >= -1 and popup['right'] <= popup['width'] + 1
        wait.until(lambda driver: (button := visible_named_control(
            driver, 'Validate and preview')) is not None and
            button.is_enabled())
        fields = operation['form']['fields']
        controls = forms.assert_form_controls(wait, fields)
        accessibility = accessibility_observation(
            browser, wait, operation, controls)
        values = {'SQL role for this attachment': ''}
        if identifier is not None:
            values.update({
                'Local transaction ID': identifier,
                'Confirm transaction ID': identifier,
                'Confirm database filename or alias': database,
                'Coordinator and participant states reviewed': True})
            for field, invalid, message in (
                    ('Confirm transaction ID', 'wrong',
                     'Repeat the exact transaction ID'),
                    ('Confirm database filename or alias', database + '.wrong',
                     'Confirm the exact database filename'),
                    ('Coordinator and participant states reviewed', False,
                     'Confirm review of the distributed coordinator')):
                fill_form_values(browser, wait, fields,
                                 {**values, field: invalid})
                click_unobscured(browser, wait, visible_named_control(
                    browser, 'Validate and preview'))
                wait.until(lambda driver: message in driver.find_element(
                    'tag name', 'body').text)
                assert not browser.find_elements(
                    'css selector', '[aria-label="Provider plan preview"]')
                result['invalid_forms'].append({
                    'operation': operation_id, 'field': field})
        plan = plan_preview(browser, wait, operation, values)
        selection = plan['command_preview']['recovery_selection']
        assert selection['database'] == database
        assert selection['transaction_id'] == identifier
        assert selection['scope'] == 'selected_database_only'
        pages = screenshot_form_pages(browser, options.output_root / (
            str(len(result['checks'])) + '-' + operation_id + '-plan'))
        confirmation = visible_named_control(
            browser, 'I confirm this provider-planned operation.')
        if confirmation is not None and not confirmation.is_selected():
            click_unobscured(browser, wait, confirmation)
        button = wait.until(lambda driver: visible_named_control(
            driver, 'Apply provider plan'))
        wait.until(lambda _driver: button.is_enabled())
        click_unobscured(browser, wait, button)
        selector = '[aria-label="Firebird prepared transactions"]'
        output = wait.until(lambda driver: next((
            item for item in driver.find_elements('css selector', selector)
            if item.is_displayed()), None))
        assert ('global distributed outcome have not been verified'
                in output.text)
        if identifier is not None:
            assert 'native decision call returned' in output.text
        else:
            assert 'Local transaction' in output.text
        assert 'Attachment release is unconfirmed' not in output.text
        result_pages = screenshot_form_pages(browser, options.output_root / (
            str(len(result['checks'])) + '-' + operation_id + '-result'))
        result['checks'].append({'operation': operation_id,
                                 'database_popup_verified': True,
                                 'popup_geometry': popup,
                                 'accessibility': accessibility,
                                 'plan_screenshots': pages,
                                 'result_screenshots': result_pages})
        # The receipt can render before the workspace refresh releases its
        # admission lock. A click on the still-disabled Close is ignored.
        wait.until(lambda driver: (button := visible_named_control(
            driver, 'Validate and preview')) is not None and
            button.is_enabled())
        button = visible_named_control(browser, 'Apply provider plan')
        assert button is None or not button.is_enabled()
        assert browser.execute_script(
            'return window.__ownedLimboDispatches') == len(result['checks'])
        close_workspace(browser, wait)

    try:
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        # Count only the action, without retaining request bodies or secrets.
        browser.execute_script('''
            window.__ownedLimboDispatches = 0;
            window.__ownedLimboMenus = [];
            document.addEventListener('click', (event) => {
              const item = event.target.closest('[data-action-id]');
              const command = item?.getAttribute('data-action-id');
              if (!command?.startsWith('database.firebird.')) return;
              const menu = item.closest('[role=menu]');
              if (!menu) return;
              const bounds = menu.getBoundingClientRect();
              window.__ownedLimboMenus.push({command,
                top: bounds.top, bottom: bounds.bottom,
                left: bounds.left, right: bounds.right,
                viewport: window.innerHeight, width: window.innerWidth,
                scrollTop: menu.scrollTop, scrollHeight: menu.scrollHeight,
                clientHeight: menu.clientHeight});
            }, true);
            const send = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.send = function(body) {
              try {
                if (JSON.parse(body)?.action === 'visual_admin_apply')
                  window.__ownedLimboDispatches++;
              } catch (_) { /* Non-JSON requests are outside this counter. */ }
              return send.apply(this, arguments);
            };
        ''')
        for index, decision in enumerate(('commit', 'rollback')):
            identifier = prepare(index)
            task('inspect_limbo')
            task(decision + '_limbo_local', identifier)
            with connect() as connection:
                assert limbo.inventory(connection, firebird) == []
                with connection.cursor() as cursor:
                    cursor.execute(
                        'SELECT ID FROM RECOVERY_MARKER ORDER BY ID')
                    assert cursor.fetchall() == [(0,)]
        result['passed'] = (len(result['checks']) == 4 and
                            len(result['invalid_forms']) == 6)
    except Exception as error:
        result['failures'].append({'type': type(error).__name__})
        if browser is not None:
            result['failure_popup_clicks'] = browser.execute_script(
                'return window.__ownedLimboMenus || []')
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
