#!/usr/bin/env python3
"""Exercise package task forms and owning-package navigation on an owned DB."""

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
)
from cdeadmin_firebird_logical_volumes_gate import (
    docker, OWNER, published_port,
)


def run(options, profiles):
    route = _load_profile(profiles)
    container = route.get('owned_container_id', '')
    if (route.get('fixture_kind') != 'firebird-packages-qualification' or
            route['host'] != '127.0.0.1' or
            route['database'] != '/var/lib/firebird/data/owned_packages.fdb' or
            route['database'] != options.database_path or
            not re.fullmatch('[0-9a-f]{64}', container)):
        raise ValueError('Package browser tests require an owned server')
    label = docker('inspect', '--format',
                   '{{index .Config.Labels "cdeadmin-owned-gate"}}',
                   container).decode().strip()
    if label != OWNER or published_port(container) != route['port']:
        raise ValueError('Owned package server identity or port differs')
    firebird.driver_config.fb_client_library.value = os.environ[
        'CDEADMIN_FIREBIRD_CLIENT_LIBRARY']
    native = firebird.connect(password=route['password'],
                              **_route_arguments(route, firebird))
    browser = None
    result = {'passed': False, 'checks': [], 'failures': [],
              'owner_navigation': [], 'expected_mutation_count': 15,
              'credential_values_exported': False,
              'target_database': options.database_path}

    def sql(statement, parameters=()):
        if native.main_transaction.is_active():
            native.rollback()
        try:
            with native.cursor() as cursor:
                cursor.execute(statement, parameters)
                return cursor.fetchall() if cursor.description else []
        finally:
            native.rollback()

    def refresh():
        current = forms._workspace_probe(
            browser, ['package', 'function'], False)
        response = browser.execute_async_script("""
            const done = arguments[arguments.length - 1];
            const app = window.pgAdmin;
            const headers = {'Content-Type': 'application/json'};
            headers[app.csrf_token_header] = app.csrf_token;
            fetch(arguments[0], {method: 'POST', credentials: 'same-origin',
              headers, body: JSON.stringify({action: 'resource_refresh',
                request: {database_target_id: arguments[1],
                  generation: arguments[2]}})})
              .then(response => done({status: response.status}))
              .catch(() => done({status: 0}));
            """, current['endpoint_url'], current['database_target_id'],
            current['generation'])
        assert response.get('status') == 200
        return forms._workspace_probe(browser, ['package', 'function'], False)

    def idle(driver):
        return driver.execute_script("""
            const titles = new Set([
              'Validate and preview', 'Refresh object properties']);
            return Array.from(document.querySelectorAll('button')).some(
              item => titles.has(item.textContent.trim()) &&
              item.getClientRects().length && !item.disabled &&
              item.getAttribute('aria-disabled') !== 'true');
            """)

    def apply(operation_id, name, values, *, present=True, body=None,
              security=None, invocation=None):
        print(name + ': ' + operation_id, flush=True)
        probe = refresh()
        operation = next(item for item in forms._enumerate_operations(
            probe['catalog'], ['package'], None)
            if item['operation_id'] == operation_id)
        assert operation['execution_available'] is True
        selected = next((item for item in probe['resources'] if
                         item['resource_kind'] == 'package' and
                         item['display_name'] == name), None)
        if selected is None:
            assert operation_id in {'create', 'create_or_alter'}
            selected = {'resource_id': 'cdeadmin-create-scope:package'}
        forms._open_focused_form(browser, operation, selected,
                                 probe['database_target_id'])
        forms._wait_for_operation(wait, operation)
        labels = {field['field_id']: field['label'] for field in
                  operation['form']['fields']}
        plan = plan_preview(browser, wait, operation, {
            labels[key]: value for key, value in values.items()})
        pages = screenshot_form_pages(browser, options.output_root / (
            f'{len(result["checks"]):02d}-package-{operation_id}'))
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
        if present:
            wait.until(idle)
        else:
            # The dropped target is deliberately absent. Preview stays
            # disabled without a target; wait for the confirmation field
            # to leave its applying/disabled state, not a new valid target.
            wait.until(lambda driver: driver.execute_script("""
                return Array.from(document.querySelectorAll('label')).some(
                  label => {
                    const input = document.getElementById(label.htmlFor);
                    return label.textContent.startsWith('Confirm package name')
                      && input && input.getClientRects().length
                      && !input.disabled;
                  });
                """))
        observed = sql('SELECT RDB$PACKAGE_BODY_SOURCE, RDB$SQL_SECURITY, '
                       'RDB$VALID_BODY_FLAG, RDB$DESCRIPTION '
                       'FROM RDB$PACKAGES WHERE RDB$PACKAGE_NAME = ?', (name,))
        assert bool(observed) is present
        if present:
            assert bool(observed[0][0]) is body
            assert observed[0][1] == security
            if operation_id == 'comment':
                assert observed[0][3] == values['description']
        if invocation is not None:
            quoted = '"' + name.replace('"', '""') + '"'
            assert sql('SELECT ' + quoted + '.F() FROM RDB$DATABASE') == [
                (invocation,)]
        result['checks'].append({
            'operation': operation_id, 'name': name,
            'statements': plan['command_preview']['statements'],
            'native_postcondition': observed, 'screenshots': pages,
            'independent_native_postcondition': True})
        close_workspace(browser, wait)

    def owner_navigation(name):
        probe = refresh()
        member = next(item for item in probe['resources'] if
                      item['resource_kind'] == 'function' and
                      item['display_name'] == 'F' and
                      forms._provider_native(item, 'firebird').get(
                          'package') == name)
        browser.execute_script("""
            const node = window.pgAdmin.Browser.Nodes.server;
            node.callbacks.open_cde_workspace.call(node,
              {item: window.__cdeadminQaEndpointItem}, 'object', {
                resource_id: arguments[0], database_target_id: arguments[1],
                resource_kind: 'function', operation_id: 'inspect',
                task_title: 'Package member editor',
              });
            """, member['resource_id'], probe['database_target_id'])
        title = 'Open owning package: ' + name
        button = wait.until(
            lambda driver: visible_named_control(driver, title))
        wait.until(lambda _driver: button.is_enabled())
        selector = '[aria-label="Selected object operations"] [role="tab"]'
        tabs = browser.execute_script("""
            return Array.from(document.querySelectorAll(arguments[0]))
              .map(item => item.textContent.trim().toLowerCase());
            """, selector)
        assert not {'alter', 'drop'} & set(tabs)
        before = screenshot_form_pages(browser, options.output_root /
                                       'member-owning-package')
        click_unobscured(browser, wait, button)
        wait.until(lambda driver: driver.execute_script("""
            return Array.from(document.querySelectorAll(arguments[0])).some(
              item => item.textContent.trim() === 'Alter package header' &&
                item.getClientRects().length);
            """, selector))
        wait.until(idle)
        after = screenshot_form_pages(browser, options.output_root /
                                      'owning-package-editor')
        result['owner_navigation'].append({
            'member_id': member['resource_id'], 'package': name,
            'invalid_member_actions_absent': True,
            'owner_header_editor_reached': True,
            'screenshots': before + after})
        close_workspace(browser, wait)

    try:
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        header = 'BEGIN FUNCTION F RETURNS INTEGER; END'
        body1 = 'BEGIN FUNCTION F RETURNS INTEGER AS BEGIN RETURN 1; END END'
        body2 = body1.replace('RETURN 1', 'RETURN 2')
        name = 'UI_HEADER_' + str(options.font_scale)
        apply('create', name, {'name': name, 'header': header, 'body': ''},
              body=False)
        apply('create_body', name, {'body': body1}, body=True, invocation=1)
        apply('replace_body', name, {'body': body2}, body=True, invocation=2)
        apply('alter', name, {'header': header, 'sql_security': 'DEFINER'},
              body=True, security=True)
        apply('replace_body', name, {'body': body1}, body=True,
              security=True, invocation=1)
        apply('comment', name, {'description': "Owner's 東京 notes"},
              body=True, security=True)
        owner_navigation(name)
        name2 = 'UI_UPSERT_' + str(options.font_scale)
        apply('create_or_alter', name2, {'name': name2, 'header': 'BEGIN END'},
              body=False)
        apply('create_or_alter', name2, {'name': name2, 'header': header},
              body=False)
        apply('recreate', name2, {
            'header': header, 'body': body2, 'confirmation': name2},
            body=True, invocation=2)
        apply('recreate', name2, {'confirmation': name2},
              body=True, invocation=2)
        apply('drop_body', name2, {'confirmation': name2}, body=False)
        apply('drop', name2, {'confirmation': name2}, present=False)
        apply('drop', name, {'confirmation': name}, present=False)
        quoted = 'UI"東京_' + str(options.font_scale)
        apply('create', quoted, {'name': quoted, 'header': header,
                                 'body': body1, 'sql_security': 'INVOKER'},
              body=True, security=False, invocation=1)
        apply('drop', quoted, {'confirmation': quoted}, present=False)
        result['passed'] = (len(result['checks']) == 15 and
                            len(result['owner_navigation']) == 1)
    except Exception as error:
        result['failures'].append({'type': type(error).__name__})
        if browser is not None:
            screenshot(browser, options.output_root / 'failure.png')
        raise
    finally:
        if browser is not None:
            forms._quit_driver(browser)
        native.close()
        options.summary_output.parent.mkdir(parents=True, exist_ok=True)
        options.summary_output.write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    options, profiles = arguments()
    outcome = run(options, profiles)
    print(json.dumps({'passed': outcome['passed'],
                      'checks': len(outcome['checks'])}))
    raise SystemExit(0 if outcome['passed'] else 1)
