#!/usr/bin/env python3
"""Object-editor permission tasks on an explicitly owned Firebird server."""

import json
import os
import re

from selenium.webdriver.common.keys import Keys

from cdeadmin_firebird_role_ui_gate import (
    arguments, forms, firebird, _route_arguments, create_driver,
    close_workspace, plan_preview, WebDriverWait, visible_named_control,
)
from cdeadmin_firebird_query_ui_gate import _load_profile
from cdeadmin_firebird_ui_form_gate import (
    click_unobscured, fill_form_values, screenshot_form_pages, screenshot,
)
from cdeadmin_firebird_logical_volumes_gate import (
    docker, OWNER, published_port,
)
from pgadmin.cdeadmin.providers.firebird import object_privileges as bound


def select_tab(browser, wait, selector, title):
    """Use the actual tab strip's keyboard focus/activation behavior."""
    tabs = wait.until(lambda driver: driver.find_elements(
        'css selector', selector + ' [role="tab"]'))
    selected = next(tab for tab in tabs if
                    tab.get_attribute('aria-selected') == 'true')
    selected.send_keys(Keys.HOME)
    for _index in range(len(tabs)):
        focused = browser.switch_to.active_element
        if focused.text == title:
            focused.send_keys(Keys.ENTER)
            wait.until(lambda _driver: focused.get_attribute(
                'aria-selected') == 'true')
            return
        focused.send_keys(Keys.ARROW_RIGHT)
    raise AssertionError('Object editor tab is unreachable: ' + title)


def run(options, profiles):
    route = _load_profile(profiles)
    container = route.get('owned_container_id', '')
    if (route.get('fixture_kind') !=
            'firebird-object-privileges-qualification' or
            route['host'] != '127.0.0.1' or
            route['database'] !=
            '/var/lib/firebird/data/owned_object_privileges.fdb' or
            route['database'] != options.database_path or
            not re.fullmatch('[0-9a-f]{64}', container)):
        raise ValueError('Object privilege browser tests need an owned server')
    label = docker('inspect', '--format',
                   '{{index .Config.Labels "cdeadmin-owned-gate"}}',
                   container).decode().strip()
    if label != OWNER or published_port(container) != route['port']:
        raise ValueError('Owned server identity or port differs')
    firebird.driver_config.fb_client_library.value = os.environ[
        'CDEADMIN_FIREBIRD_CLIENT_LIBRARY']
    native = firebird.connect(password=route['password'],
                              **_route_arguments(route, firebird))
    browser = None
    grantee = 'CDE_OBJECT_READER'
    cases = [(kind, name, None) for kind, name in (
        ('table', 'T'), ('view', 'VW'), ('procedure', 'P'), ('function', 'F'),
        ('package', 'PK'), ('sequence', 'S'), ('exception', 'E'),
        ('external-function', 'EF'), ('column', 'C'))]
    cases += [(kind, name, package) for kind, name in (
        ('function', 'F'), ('procedure', 'P')) for package in ('PK', 'PK2')]
    result = {'passed': False, 'checks': [], 'failures': [],
              'credential_values_exported': False,
              'target_database': options.database_path,
              'expected_mutation_count': 2 * len(cases),
              'entry_path': 'Object browser / Privileges and grants / task'}

    def snapshot():
        if native.main_transaction.is_active():
            native.rollback()
        with native.cursor() as cursor:
            cursor.execute('SELECT RDB$RELATION_NAME, RDB$FIELD_NAME, '
                           'RDB$PRIVILEGE, RDB$OBJECT_TYPE '
                           'FROM RDB$USER_PRIVILEGES WHERE RDB$USER = ?',
                           (grantee,))
            rows = {tuple(value.rstrip(' ') if isinstance(value, str)
                          else value for value in row)
                    for row in cursor.fetchall()}
        native.rollback()
        return rows

    def mutation(kind, name, package, operation, values):
        label = '-'.join(str(item) for item in (kind, package or 'global',
                                                name, operation))
        print(label, flush=True)
        probe = forms._workspace_probe(browser, [kind], False)
        assert probe.get('status') == 200
        selected = next(item for item in probe['resources'] if
                        item['resource_kind'] == kind and
                        item['display_name'] == name and
                        (forms._provider_native(item, 'firebird').get(
                            'package') or None) == package and
                        (kind != 'column' or
                         item['display_path'] == ['T', name]))
        descriptor = next(item for item in probe['catalog']['objects'] if
                          item['resource_kind'] == kind)
        task = next(item for item in descriptor['operations'] if
                    item['operation_id'] == operation)
        assert task['target_required'] is True
        assert task['execution_available'] is True
        assert task['form']['form_id'] == f'firebird.{kind}.{operation}'
        browser.execute_script("""
            const node = window.pgAdmin.Browser.Nodes.server;
            node.callbacks.open_cde_workspace.call(node,
              {item: window.__cdeadminQaEndpointItem}, 'object', {
                resource_id: arguments[0], database_target_id: arguments[1],
                resource_kind: arguments[2], operation_id: 'inspect',
                task_title: 'Object permission editor',
              });
            """, selected['resource_id'], probe['database_target_id'], kind)

        def refresh_ready(driver):
            control = visible_named_control(
                driver, 'Refresh object properties')
            return control is not None and control.is_enabled()
        wait.until(refresh_ready)
        select_tab(browser, wait, '[aria-label="Object properties sections"]',
                   'Privileges and grants')

        def permission_button(driver):
            buttons = driver.find_elements(
                'css selector',
                '[aria-label="Object permission tasks"] button')
            return next((item for item in buttons if item.is_displayed() and
                         item.text == task['title']), None)
        button = wait.until(permission_button)
        assert button.is_enabled()
        click_unobscured(browser, wait, button)
        wait.until(lambda driver: visible_named_control(
            driver, 'Grantee kind'))
        fields = task['form']['fields']
        field_ids = {field['field_id'] for field in fields}
        assert not field_ids & {
            'object_type', 'object_name', 'privilege_scope', 'ddl_class',
            'definition', 'options'}
        by_label = {}
        for field in fields:
            if field['field_id'] in values:
                value = values[field['field_id']]
                by_label[field['label']] = json.dumps(value) if isinstance(
                    value, (dict, list, bool)) else str(value)
        fill_form_values(browser, wait, fields, by_label)
        preview = plan_preview(browser, wait, task, {})
        statements = preview['command_preview']['statements']
        assert [item['source'] for item in statements] == [
            bound.compile_operation(kind, operation, values, selected)]
        pages = screenshot_form_pages(browser, options.output_root / label)
        confirmation = visible_named_control(
            browser, 'I confirm this provider-planned operation.')
        if confirmation is not None and not confirmation.is_selected():
            click_unobscured(browser, wait, confirmation)
        apply = wait.until(lambda driver: visible_named_control(
            driver, 'Apply provider plan'))
        wait.until(lambda _driver: apply.is_enabled())
        click_unobscured(browser, wait, apply)
        output = wait.until(lambda driver: next((
            item for item in driver.find_elements(
                'css selector', '[aria-label="Provider operation result"]')
            if item.is_displayed()), None))
        assert json.loads(output.text)['accepted'] is True

        def idle_after_refresh(driver):
            # A committed object edit refreshes the catalog and may return to
            # Inspect. Both states must finish their native follow-up first.
            # Read the rendered state atomically: React may replace controls
            # between separate WebDriver lookup/is_enabled calls. This never
            # retries a mutation or invokes a click from a polling predicate.
            return driver.execute_script("""
                const titles = new Set([
                  'Validate and preview', 'Refresh object properties']);
                return Array.from(document.querySelectorAll('button')).some(
                  item => titles.has(item.textContent.trim()) &&
                  item.getClientRects().length && !item.disabled &&
                  item.getAttribute('aria-disabled') !== 'true');
                """)
        wait.until(idle_after_refresh)
        observed = snapshot()
        if operation == 'grant':
            assert observed
            _type, target_name, column = bound.target_identity(kind, selected)
            assert all(row[0] == target_name for row in observed)
            if column:
                assert all(row[1] == column for row in observed)
        else:
            assert not observed
        result['checks'].append({
            'case': label, 'kind': kind, 'operation': operation,
            'package': package, 'resource_id': selected['resource_id'],
            'statements': statements, 'screenshots': pages,
            'native_grants': sorted(observed, key=repr),
            'independent_native_postcondition': True})
        close_workspace(browser, wait)

    try:
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        assert not snapshot()
        for kind, name, package in cases:
            values = {'principal_kind': 'USER', 'principal': grantee,
                      'privileges': [item for item in
                                     bound.allowed_privileges(kind)
                                     if item != 'ALL']}
            mutation(kind, name, package, 'grant', values)
            mutation(kind, name, package, 'revoke', dict(
                values, confirmation=grantee))
        result['passed'] = len(result['checks']) == 2 * len(cases)
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
