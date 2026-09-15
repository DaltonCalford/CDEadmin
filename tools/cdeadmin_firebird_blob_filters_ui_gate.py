#!/usr/bin/env python3
"""BLOB-filter visual lifecycle and native checks on an owned server."""

import json
import os
import re
import uuid

from cdeadmin_firebird_role_ui_gate import (
    arguments, forms, firebird, _resources, _route_arguments,
    create_driver, close_workspace, plan_preview, screenshot,
    WebDriverWait, visible_named_control,
)
from cdeadmin_firebird_query_ui_gate import _load_profile
from cdeadmin_firebird_ui_form_gate import (
    screenshot_form_pages, click_unobscured,
)
from cdeadmin_firebird_logical_volumes_gate import (
    docker, OWNER, published_port,
)
from cdeadmin_firebird_blob_filters_gate import filtered_blob


def run(options, profiles):
    profile = _load_profile(profiles)
    container = profile.get('owned_container_id', '')
    if (profile.get('fixture_kind') != 'firebird-blob-filter-qualification' or
            profile['host'] != '127.0.0.1' or
            profile['database'] != '/var/lib/firebird/data/owned_filter.fdb' or
            profile['database'] != options.database_path or
            not re.fullmatch('[0-9a-f]{64}', container)):
        raise ValueError('BLOB filter browser tasks require an owned server')
    label = docker('inspect', '--format',
                   '{{index .Config.Labels "cdeadmin-owned-gate"}}',
                   container).decode().strip()
    if label != OWNER or published_port(container) != profile['port']:
        raise ValueError(
            'BLOB filter test server ownership or endpoint differs')
    firebird.driver_config.fb_client_library.value = os.environ[
        'CDEADMIN_FIREBIRD_CLIENT_LIBRARY']
    native = firebird.connect(password=profile['password'],
                              **_route_arguments(profile, firebird))
    browser = None
    prefix = 'CDE_UI_FILTER_' + uuid.uuid4().hex[:12].upper()
    result = {'passed': False, 'checks': [], 'failures': [],
              'inspector_sections': [],
              'credential_values_exported': False,
              'target_database': options.database_path,
              'expected_mutation_count': 8}

    def state(name):
        resources = _resources(native, {'route': profile})
        native.rollback()
        return next((item['native'] for item in resources
                     if item['resource_kind'] == 'blob-filter' and
                     item['display_name'] == name), None)

    def refresh():
        current = forms._workspace_probe(browser, ['blob-filter'], False)
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
        return forms._workspace_probe(browser, ['blob-filter'], False)

    def apply(action, name, values, expected, invocation=None):
        print(name + ': ' + action, flush=True)
        probe = refresh()
        operations = list(forms._enumerate_operations(
            probe['catalog'], ['blob-filter'], None))
        assert {item['operation_id'] for item in operations} <= {
            'inspect', 'create', 'comment', 'drop'}
        operation = next(item for item in operations
                         if item['operation_id'] == action)
        assert operation['execution_available'] is True
        assert all(field['field_id'] not in {
            'definition', 'options', 'arguments'}
            for field in operation['form']['fields'])
        selected = next((item for item in probe['resources'] if
                         item['resource_kind'] == 'blob-filter' and
                         item['display_name'] == name), None)
        if selected is None:
            assert action == 'create'
            selected = {'resource_id': 'cdeadmin-create-scope:blob-filter'}
        forms._open_focused_form(browser, operation, selected,
                                 probe['database_target_id'])
        forms._wait_for_operation(wait, operation)
        labels = {field['field_id']: field['label'] for field in
                  operation['form']['fields']}
        plan = plan_preview(browser, wait, operation, {
            labels[key]: value for key, value in values.items()})
        pages = screenshot_form_pages(browser, options.output_root / (
            f'{len(result["checks"]):02d}-blob-filter-{action}'))
        confirmation = visible_named_control(
            browser, 'I confirm this provider-planned operation.')
        if confirmation is not None and not confirmation.is_selected():
            click_unobscured(browser, wait, confirmation)
        button = wait.until(lambda driver: visible_named_control(
            driver, 'Apply provider plan'))
        wait.until(lambda _driver: button.is_enabled())
        click_unobscured(browser, wait, button)
        output = wait.until(lambda driver: next((
            element for element in driver.find_elements(
                'css selector', '[aria-label="Provider operation result"]')
            if element.is_displayed()), None))
        assert json.loads(output.text)['accepted'] is True
        observed = state(name)
        if expected is None:
            assert observed is None
        else:
            assert observed is not None
            for key, value in expected.items():
                assert observed[key] == value, key
            assert observed['ddl'].startswith('DECLARE FILTER ')
            assert 'parameters' not in observed['property_sections']
        native_value = None
        if invocation:
            source, target = invocation
            native_value = filtered_blob(native, [b'hello\x00 world'],
                                         source=source, target=target)
            native.rollback()
            assert native_value == b'HELLO\x00 WORLD'
        result['checks'].append({
            'operation': action, 'object_name': name,
            'statements': plan['command_preview']['statements'],
            'native_postcondition': observed,
            'native_invocation_hex': (native_value.hex()
                                      if native_value else None),
            'screenshots': pages})
        field_label = next(field['label'] for field in
                           operation['form']['fields'] if
                           field['control'] in {'text', 'multiline'})
        wait.until(lambda driver: (
            visible_named_control(driver, field_label) is not None and
            visible_named_control(driver, field_label).is_enabled()))
        close_workspace(browser, wait)

    def inspect(name):
        probe = refresh()
        selected = next(item for item in probe['resources'] if
                        item['resource_kind'] == 'blob-filter' and
                        item['display_name'] == name)
        browser.execute_script("""
            const app = window.pgAdmin;
            const node = app.Browser.Nodes.server;
            node.callbacks.open_cde_workspace.call(node,
              {item: window.__cdeadminQaEndpointItem}, 'object', {
                resource_id: arguments[0], database_target_id: arguments[1],
                resource_kind: 'blob-filter', operation_id: 'inspect',
                task_title: 'BLOB filter object editor',
              });
            """, selected['resource_id'], probe['database_target_id'])
        selector = '[aria-label="Object properties sections"]'
        wait.until(lambda driver: driver.find_elements(
            'css selector', selector))

        def ready_refresh(driver):
            button = visible_named_control(driver, 'Refresh object properties')
            return (button if button is not None and button.is_enabled()
                    else None)

        # Do not qualify only the resource-list fallback while the dedicated
        # inspection request is still running. Exercise a second real refresh.
        button = wait.until(ready_refresh)
        click_unobscured(browser, wait, button)
        wait.until(ready_refresh)
        result['inspector_refresh_completed'] = True
        tabs = browser.find_elements(
            'css selector', selector + ' [role="tab"]')
        expected_sections = [
            ('properties', 'Summary'), ('ddl', 'Creation statement (DDL)'),
            ('dependencies', 'Depends on'), ('dependents', 'Depended on by'),
            ('privileges', 'Privileges and grants'),
            ('operations', 'Operations')]
        assert [tab.text for tab in tabs] == [title for _, title in
                                              expected_sections]
        for section, title in expected_sections:
            tabs = browser.find_elements('css selector',
                                         selector + ' [role="tab"]')
            tab = next(item for item in tabs if item.text == title)
            # MUI tab strips expose real scroll arrows. At 300% a later tab
            # may be beyond the clip edge; scrolling the outer task alone
            # cannot bring that tab's centre into the clickable strip.
            for _attempt in range(20):
                visible = browser.execute_script("""
                    const tab = arguments[0];
                    tab.scrollIntoView({block: 'center', inline: 'nearest'});
                    const rect = tab.getBoundingClientRect();
                    const hit = document.elementFromPoint(
                      rect.left + rect.width / 2, rect.top + rect.height / 2);
                    return hit === tab || tab.contains(hit);
                    """, tab)
                if visible:
                    break
                arrows = browser.find_elements(
                    'css selector', selector + ' .MuiTabs-scrollButtons')
                assert arrows
                assert 'Mui-disabled' not in arrows[-1].get_attribute('class')
                click_unobscured(browser, wait, arrows[-1])
            else:
                raise AssertionError('Object inspector tab is unreachable')
            click_unobscured(browser, wait, tab)
            panel_selector = '[aria-label="' + section + ' object section"]'

            def visible_panel(driver):
                return next((item for item in driver.find_elements(
                    'css selector', panel_selector) if item.is_displayed()),
                    None)
            panel = wait.until(visible_panel)
            text = panel.text
            if section == 'properties':
                assert 'input subtype' in text and 'output subtype' in text
                assert 'owned_uppercase' in text and 'owned_filter' in text
            elif section == 'ddl':
                assert 'DECLARE FILTER "' + name + '"' in text
                assert 'INPUT_TYPE -81 OUTPUT_TYPE 1' in text
            dimensions = browser.execute_script("""
                arguments[0].scrollTop = 0;
                return {height: arguments[0].clientHeight,
                  total: arguments[0].scrollHeight};
                """, panel)
            assert dimensions['height'] > 0
            count = max(1, (dimensions['total'] + dimensions['height'] - 1) //
                        dimensions['height'])
            assert count <= 100
            pages = []
            for page in range(count):
                visible = browser.execute_script("""
                    const panel = arguments[0];
                    panel.scrollTop = arguments[1];
                    panel.scrollIntoView({block: 'end', inline: 'nearest'});
                    const rect = panel.getBoundingClientRect();
                    let top = Math.max(0, rect.top);
                    let bottom = Math.min(innerHeight, rect.bottom);
                    for (let parent = panel.parentElement; parent;
                      parent = parent.parentElement) {
                      if (/(auto|scroll|hidden|clip)/.test(
                        getComputedStyle(parent).overflowY)) {
                        const bounds = parent.getBoundingClientRect();
                        top = Math.max(top, bounds.top);
                        bottom = Math.min(bottom, bounds.bottom);
                      }
                    }
                    return {height: rect.height, visible: bottom - top};
                    """, panel, page * dimensions['height'])
                assert visible['visible'] >= visible['height'] - 2
                path = options.output_root / (
                    f'inspect-{section}-{page + 1:02d}.png')
                digest = screenshot(browser, path, reset_scroll=False)
                pages.append({'path': str(path), 'sha256': digest})
            result['inspector_sections'].append({
                'section': section, 'screenshots': pages,
                'scrolled_content_height': dimensions['total']})
        close_workspace(browser, wait)

    try:
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        apply('create', prefix, {
            'name': prefix, 'input_subtype': -81, 'output_subtype': 1,
            'entrypoint': 'owned_uppercase', 'module_name': 'owned_filter',
            'description': 'Browser comment'},
            {'description': 'Browser comment', 'input_subtype': '-81',
             'output_subtype': '1'}, (-81, 1))
        inspect(prefix)
        apply('comment', prefix, {'description': 'Changed'},
              {'description': 'Changed'})
        apply('comment', prefix, {'description': ''}, {'description': None})
        apply('drop', prefix, {'confirmation': prefix}, None)
        for suffix, values, expected, invocation in (
                ('INPUT_NAME', {'input_mode': 'MNEMONIC',
                                'input_mnemonic': 'TEXT',
                                'output_subtype': -82},
                 {'input_subtype': '1', 'output_subtype': '-82'}, (1, -82)),
                ('OUTPUT_NAME', {'input_subtype': -83,
                                 'output_mode': 'MNEMONIC',
                                 'output_mnemonic': 'TEXT'},
                 {'input_subtype': '-83', 'output_subtype': '1'}, (-83, 1))):
            name = prefix + '_' + suffix
            apply('create', name, {'name': name,
                                   'entrypoint': 'owned_uppercase',
                                   'module_name': 'owned_filter', **values},
                  expected, invocation)
            apply('drop', name, {'confirmation': name}, None)
        result['passed'] = (len(result['checks']) == 8 and
                            len(result['inspector_sections']) == 6 and
                            result.get('inspector_refresh_completed') is True)
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
