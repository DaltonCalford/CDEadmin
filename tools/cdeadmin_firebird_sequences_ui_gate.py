#!/usr/bin/env python3
"""Exercise native sequence forms and exact values on an owned database."""

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
    if (route.get('fixture_kind') != 'firebird-sequences-qualification' or
            route['host'] != '127.0.0.1' or
            route['database'] != (
                '/var/lib/firebird/data/owned_sequences.fdb') or
            route['database'] != options.database_path or
            not re.fullmatch('[0-9a-f]{64}', container)):
        raise ValueError('Sequence browser tests require an owned server')
    label = docker('inspect', '--format',
                   '{{index .Config.Labels "cdeadmin-owned-gate"}}',
                   container).decode().strip()
    if label != OWNER or published_port(container) != route['port']:
        raise ValueError('Owned sequence server identity or port differs')
    firebird.driver_config.fb_client_library.value = os.environ[
        'CDEADMIN_FIREBIRD_CLIENT_LIBRARY']
    native = firebird.connect(password=route['password'],
                              **_route_arguments(route, firebird))
    browser = None
    result = {'passed': False, 'checks': [], 'failures': [],
              'expected_mutation_count': 15,
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
        current = forms._workspace_probe(browser, ['sequence'], False)
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
        return forms._workspace_probe(browser, ['sequence'], False)

    def apply(operation_id, name, values, *, expected=None, current=None,
              next_value=None):
        print(name + ': ' + operation_id, flush=True)
        probe = refresh()
        operation = next(item for item in forms._enumerate_operations(
            probe['catalog'], ['sequence'], None)
            if item['operation_id'] == operation_id)
        assert operation['execution_available'] is True
        selected = next((item for item in probe['resources'] if
                         item['resource_kind'] == 'sequence' and
                         item['display_name'] == name), None)
        if selected is None:
            assert operation_id in {'create', 'create_or_alter'}
            selected = {'resource_id': 'cdeadmin-create-scope:sequence'}
        forms._open_focused_form(browser, operation, selected,
                                 probe['database_target_id'])
        forms._wait_for_operation(wait, operation)
        labels = {field['field_id']: field['label'] for field in
                  operation['form']['fields']}
        plan = plan_preview(browser, wait, operation, {
            labels[key]: value for key, value in values.items()})
        pages = screenshot_form_pages(browser, options.output_root / (
            f'{len(result["checks"]):02d}-sequence-{operation_id}'))
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
                  return label.textContent.startsWith('Confirm sequence name')
                    && input && input.getClientRects().length
                    && !input.disabled;
                });
            }
            return Array.from(document.querySelectorAll('button')).some(
              item => item.textContent.trim() === 'Validate and preview' &&
              item.getClientRects().length && !item.disabled &&
              item.getAttribute('aria-disabled') !== 'true');
            """, operation_id))
        rows = sql('SELECT RDB$INITIAL_VALUE, RDB$GENERATOR_INCREMENT, '
                   'RDB$DESCRIPTION FROM RDB$GENERATORS '
                   'WHERE RDB$GENERATOR_NAME = ?', (name,))
        observed = rows[0] if rows else None
        assert observed == expected
        quoted = '"' + name.replace('"', '""') + '"'
        if current is not None:
            assert sql('SELECT GEN_ID(' + quoted +
                       ', 0) FROM RDB$DATABASE') == [(current,)]
        if next_value is not None:
            assert sql('SELECT NEXT VALUE FOR ' + quoted +
                       ' FROM RDB$DATABASE') == [(next_value,)]
        result['checks'].append({
            'operation': operation_id, 'name': name,
            'statements': plan['command_preview']['statements'],
            'native_postcondition': observed, 'screenshots': pages,
            'observed_current': str(current) if current is not None else None,
            'consumed_next': (str(next_value)
                              if next_value is not None else None),
            'independent_native_postcondition': True})
        close_workspace(browser, wait)

    try:
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        name = 'UI_SEQUENCE'
        apply('create', name, {'name': name, 'start': '10', 'increment': '2'},
              expected=(10, 2, None), next_value=10)
        apply('set_current', name, {'current': '9007199254740993',
                                    'confirmation': name},
              expected=(10, 2, None), current=9007199254740993,
              next_value=9007199254740995)
        apply('alter', name, {'restart': '9223372036854775807',
                              'increment': '-1'},
              expected=(10, -1, None), next_value=9223372036854775807)
        apply('alter', name, {'restart_initial': True},
              expected=(10, -1, None), next_value=10)
        apply('comment', name, {'description': "Owner's 東京 notes"},
              expected=(10, -1, "Owner's 東京 notes"), current=10)
        apply('comment', name, {'description': ''},
              expected=(10, -1, None), current=10)
        apply('create_or_alter', name, {'name': name, 'start': '-100',
                                        'increment': '3'},
              expected=(10, 3, None), next_value=-100)
        apply('recreate', name, {'start': '-9223372036854775808',
                                 'increment': '1', 'confirmation': name},
              expected=(-9223372036854775808, 1, None),
              next_value=-9223372036854775808)
        apply('recreate', name, {'confirmation': name},
              expected=(-9223372036854775808, 1, None),
              next_value=-9223372036854775808)
        apply('drop', name, {'confirmation': name})
        other = 'UI_NEW_SEQUENCE'
        apply('create_or_alter', other, {'name': other, 'start': '1',
                                         'increment': '-2147483647'},
              expected=(1, -2147483647, None), next_value=1)
        apply('alter', other, {'increment': '2147483647'},
              expected=(1, 2147483647, None), next_value=2147483648)
        apply('drop', other, {'confirmation': other})
        quoted = 'UI"東京'
        apply('create', quoted, {'name': quoted},
              expected=(1, 1, None), next_value=1)
        apply('drop', quoted, {'confirmation': quoted})
        result['passed'] = len(result['checks']) == 15
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
