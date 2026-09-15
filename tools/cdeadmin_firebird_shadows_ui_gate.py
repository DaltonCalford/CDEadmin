#!/usr/bin/env python3
"""Exercise shadow forms against an identity-checked disposable server."""

import json
import os
import re
import subprocess

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
    if (route.get('fixture_kind') != 'firebird-shadows-qualification' or
            route['host'] != '127.0.0.1' or
            route['database'] != '/var/lib/firebird/data/owned_shadows.fdb' or
            route['database'] != options.database_path or
            not re.fullmatch('[0-9a-f]{64}', container)):
        raise ValueError('Shadow browser tests require an owned server')
    label = docker('inspect', '--format',
                   '{{index .Config.Labels "cdeadmin-owned-gate"}}',
                   container).decode().strip()
    if label != OWNER or published_port(container) != route['port']:
        raise ValueError('Owned shadow server identity or port differs')
    firebird.driver_config.fb_client_library.value = os.environ[
        'CDEADMIN_FIREBIRD_CLIENT_LIBRARY']
    browser = None
    result = {'passed': False, 'checks': [], 'failures': [],
              'inspector_sections': [],
              'expected_mutation_count': 8,
              'credential_values_exported': False,
              'target_database': options.database_path}

    def rows(number):
        with firebird.connect(password=route['password'],
                              **_route_arguments(route, firebird)) as native:
            with native.cursor() as cursor:
                cursor.execute(
                    'SELECT RDB$FILE_NAME, '
                    'RDB$FILE_SEQUENCE, RDB$FILE_START, RDB$FILE_LENGTH, '
                    'RDB$FILE_FLAGS FROM RDB$FILES '
                    'WHERE RDB$SHADOW_NUMBER = ? ORDER BY RDB$FILE_SEQUENCE',
                    (number,))
                return cursor.fetchall()

    def file_exists(filename):
        if not filename.startswith('/var/lib/firebird/data/owned_shadow_ui_'):
            raise ValueError('Unowned shadow path')
        process = subprocess.run(
            ['docker', 'exec', container, 'test', '-f', filename],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        if process.returncode not in (0, 1):
            raise RuntimeError('Owned file inspection failed')
        return process.returncode == 0

    def refresh():
        probe = forms._workspace_probe(browser, ['shadow'], False)
        response = browser.execute_async_script("""
            const done = arguments[arguments.length - 1];
            const app = window.pgAdmin;
            const headers = {'Content-Type': 'application/json'};
            headers[app.csrf_token_header] = app.csrf_token;
            fetch(arguments[0], {method: 'POST', credentials: 'same-origin',
              headers, body: JSON.stringify({action: 'resource_refresh',
                request: {database_target_id: arguments[1],
                  generation: arguments[2]}})})
              .then(response => done(response.status)).catch(() => done(0));
            """, probe['endpoint_url'], probe['database_target_id'],
            probe['generation'])
        assert response == 200
        return forms._workspace_probe(browser, ['shadow'], False)

    def apply(operation_id, number, values, filenames, preserve=False):
        probe = refresh()
        operation = next(item for item in forms._enumerate_operations(
            probe['catalog'], ['shadow'], None)
            if item['operation_id'] == operation_id)
        assert operation['execution_available'] is True
        selected = next((item for item in probe['resources'] if
                         item['resource_kind'] == 'shadow' and
                         item['display_name'] == str(number)), None)
        if selected is None:
            assert operation_id == 'create'
            selected = {'resource_id': 'cdeadmin-create-scope:shadow'}
        forms._open_focused_form(browser, operation, selected,
                                 probe['database_target_id'])
        forms._wait_for_operation(wait, operation)
        labels = {field['field_id']: field['label'] for field in
                  operation['form']['fields']}
        plan = plan_preview(browser, wait, operation, {
            labels[key]: value for key, value in values.items()})
        pages = screenshot_form_pages(browser, options.output_root / (
            f'{len(result["checks"]):02d}-shadow-{operation_id}'))
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
            if (arguments[0] === 'drop') {
              return Array.from(document.querySelectorAll('label')).some(
                label => {
                  const input = document.getElementById(label.htmlFor);
                  return label.textContent.startsWith('Confirm shadow number')
                    && input && input.getClientRects().length
                    && !input.disabled;
                });
            }
            return Array.from(document.querySelectorAll('button')).some(
              item => item.textContent.trim() === 'Validate and preview' &&
              item.getClientRects().length && !item.disabled &&
              item.getAttribute('aria-disabled') !== 'true');
            """, operation_id))
        observed = rows(number)
        if operation_id == 'create':
            assert [row[0] for row in observed] == filenames
            assert all(file_exists(filename) for filename in filenames)
            assert bool(observed[0][4] & 4) == (values['mode'] == 'MANUAL')
        else:
            assert not observed
            # Provider pool closure is verified separately by the native gate;
            # physical unlink may wait until the last engine attachment closes.
            if preserve:
                assert all(file_exists(filename) for filename in filenames)
        result['checks'].append({
            'operation': operation_id, 'number': number,
            'statements': plan['command_preview']['statements'],
            'native_postcondition': observed, 'screenshots': pages,
            'independent_native_postcondition': True})
        close_workspace(browser, wait)

    def inspect(number, filenames):
        probe = refresh()
        selected = next(item for item in probe['resources'] if
                        item['resource_kind'] == 'shadow' and
                        item['display_name'] == str(number))
        browser.execute_script("""
            const node = window.pgAdmin.Browser.Nodes.server;
            node.callbacks.open_cde_workspace.call(node,
              {item: window.__cdeadminQaEndpointItem}, 'object', {
                resource_id: arguments[0], database_target_id: arguments[1],
                resource_kind: 'shadow', operation_id: 'inspect',
                task_title: 'Shadow object editor',
              });
            """, selected['resource_id'], probe['database_target_id'])
        selector = '[aria-label="Object properties sections"]'
        wait.until(lambda driver: driver.find_elements(
            'css selector', selector))

        def ready(driver):
            button = visible_named_control(driver, 'Refresh object properties')
            return (button if button is not None and button.is_enabled()
                    else None)

        click_unobscured(browser, wait, wait.until(ready))
        wait.until(ready)
        titles = ['Summary', 'Creation statement (DDL)',
                  'Storage files', 'Operations']
        assert browser.execute_script("""
            return Array.from(document.querySelectorAll(
              arguments[0] + ' [role="tab"]')).map(tab => tab.textContent);
            """, selector) == titles
        for section, title in zip(('properties', 'ddl', 'files', 'operations'),
                                  titles):
            for _attempt in range(20):
                tab = next(item for item in browser.find_elements(
                    'css selector', selector + ' [role="tab"]')
                    if item.text == title)
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
                click_unobscured(browser, wait, arrows[-1])
            else:
                raise AssertionError('Shadow inspector tab is unreachable')
            click_unobscured(browser, wait, tab)
            panel_selector = '[aria-label="' + section + ' object section"]'
            panel = wait.until(lambda driver: next((
                item for item in driver.find_elements(
                    'css selector', panel_selector)
                if item.is_displayed()), None))
            if section == 'files':
                assert all(filename in panel.text for filename in filenames)
                assert panel.find_elements('css selector', '[role="table"]')
            elif section == 'ddl':
                assert 'CREATE SHADOW ' + str(number) in panel.text
            dimensions = browser.execute_script("""
                const panel = arguments[0];
                panel.scrollIntoView({block: 'center', inline: 'nearest'});
                panel.scrollTop = 0;
                return {height: panel.clientHeight, total: panel.scrollHeight};
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
                    const rect = panel.getBoundingClientRect();
                    let top = Math.max(0, rect.top);
                    let bottom = Math.min(innerHeight, rect.bottom);
                    for (let parent = panel.parentElement; parent;
                         parent = parent.parentElement) {
                      const style = getComputedStyle(parent);
                      if (/auto|scroll|hidden|clip/.test(style.overflowY)) {
                        const bounds = parent.getBoundingClientRect();
                        top = Math.max(top, bounds.top);
                        bottom = Math.min(bottom, bounds.bottom);
                      }
                    }
                    return {height: rect.height, visible: bottom - top};
                    """, panel, page * dimensions['height'])
                assert visible['visible'] >= visible['height'] - 2
                path = options.output_root / (
                    f'inspector-{section}-{page + 1:02d}.png')
                pages.append({'path': str(path), 'sha256': screenshot(
                    browser, path, reset_scroll=False)})
            result['inspector_sections'].append({
                'section': section, 'screenshots': pages})
        close_workspace(browser, wait)

    try:
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        for offset, (mode, conditional, preserve) in enumerate((
                ('AUTO', False, False), ('MANUAL', False, True),
                ('AUTO', True, True), ('MANUAL', True, False))):
            number = 1000 + options.font_scale + offset
            filename = f"/var/lib/firebird/data/owned_shadow_ui_{number}_影's"
            filenames = [filename, filename + '_second']
            apply('create', number, {
                'number': number, 'mode': mode, 'conditional': conditional,
                'filename': filename, 'length': 256,
                'secondary_files': [{'filename': filenames[1], 'start': 700,
                                     'length': 512}]}, filenames)
            if offset == 0:
                inspect(number, filenames)
            apply('drop', number, {'confirmation': str(number),
                                   'preserve_files': preserve}, filenames,
                  preserve=preserve)
        result['passed'] = (len(result['checks']) == 8 and
                            len(result['inspector_sections']) == 4)
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
