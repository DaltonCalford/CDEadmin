#!/usr/bin/env python3
"""UDF visual lifecycle checks on an explicitly owned test server only."""

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


def run(options, profiles):
    profile = _load_profile(profiles)
    container = profile.get('owned_container_id', '')
    if (profile.get('fixture_kind') != 'firebird-udf-qualification' or
            profile['host'] != '127.0.0.1' or
            profile['database'] != '/var/lib/firebird/data/owned_udf.fdb' or
            profile['database'] != options.database_path or
            not re.fullmatch('[0-9a-f]{64}', container)):
        raise ValueError('UDF browser mutations require an owned test server')
    label = docker('inspect', '--format',
                   '{{index .Config.Labels "cdeadmin-owned-gate"}}',
                   container).decode().strip()
    if label != OWNER or published_port(container) != profile['port']:
        raise ValueError('UDF test server ownership or endpoint differs')
    firebird.driver_config.fb_client_library.value = os.environ[
        'CDEADMIN_FIREBIRD_CLIENT_LIBRARY']
    native = firebird.connect(password=profile['password'],
                              **_route_arguments(profile, firebird))
    browser = None
    prefix = 'CDE_UI_UDF_' + uuid.uuid4().hex[:12].upper()
    result = {'passed': False, 'checks': [], 'failures': [],
              'credential_values_exported': False,
              'target_database': options.database_path,
              'expected_mutation_count': 13}

    def state(name):
        resources = _resources(native, {'route': profile})
        native.rollback()
        return next((item['native'] for item in resources
                     if item['resource_kind'] == 'external-function' and
                     item['display_name'] == name), None)

    def refresh():
        current = forms._workspace_probe(browser, ['external-function'], False)
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
        return forms._workspace_probe(browser, ['external-function'], False)

    def apply(action, name, values, expected, invocation=None):
        print(name + ': ' + action, flush=True)
        probe = refresh()
        operation = next(iter(forms._enumerate_operations(
            probe['catalog'], ['external-function'], [action])))
        assert operation['execution_available'] is True
        selected = next((item for item in probe['resources'] if
                         item['resource_kind'] == 'external-function' and
                         item['display_name'] == name), None)
        if selected is None:
            assert action == 'create'
            selected = {
                'resource_id': 'cdeadmin-create-scope:external-function'}
        forms._open_focused_form(browser, operation, selected,
                                 probe['database_target_id'])
        forms._wait_for_operation(wait, operation)
        labels = {field['field_id']: field['label'] for field in
                  operation['form']['fields']}
        plan = plan_preview(browser, wait, operation, {
            labels[key]: value for key, value in values.items()})
        pages = screenshot_form_pages(browser, options.output_root / (
            f'{len(result["checks"]):02d}-external-function-{action}'))
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
        native_value = None
        if invocation:
            expression, expected_value = invocation
            with native.cursor() as cursor:
                cursor.execute('SELECT "' + name + '"(' + expression +
                               ') FROM RDB$DATABASE')
                native_value = cursor.fetchone()[0]
            native.rollback()
            assert native_value == expected_value
        result['checks'].append({
            'operation': action, 'object_name': name,
            'statements': plan['command_preview']['statements'],
            'native_postcondition': observed,
            'native_invocation_result': native_value, 'screenshots': pages})
        field_label = next(field['label'] for field in
                           operation['form']['fields'] if
                           field['control'] in {'text', 'multiline'})
        wait.until(lambda driver: (
            visible_named_control(driver, field_label) is not None and
            visible_named_control(driver, field_label).is_enabled()))
        close_workspace(browser, wait)

    try:
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        apply('create', prefix, {
            'name': prefix, 'arguments': [{'data_type': 'INTEGER',
                                          'mechanism': 'REFERENCE'}],
            'return_mechanism': 'VALUE', 'entrypoint': 'owned_value',
            'module_name': 'cde_owned_udf', 'description': 'Browser comment'},
            {'description': 'Browser comment'}, ('5', 12))
        apply('alter', prefix, {'alter_target': 'ENTRY_POINT',
                                'entrypoint': 'owned_other'},
              {'entrypoint': 'owned_other'}, ('5', 16))
        apply('alter', prefix, {'alter_target': 'MODULE_NAME',
                                'module_name': 'cde_owned_udf_alias'},
              {'module_name': 'cde_owned_udf_alias'}, ('5', 16))
        apply('alter', prefix, {'alter_target': 'BOTH',
                                'module_name': 'cde_owned_udf',
                                'entrypoint': 'owned_value'},
              {'module_name': 'cde_owned_udf', 'entrypoint': 'owned_value'},
              ('5', 12))
        apply('comment', prefix, {'description': 'Changed'},
              {'description': 'Changed'})
        apply('comment', prefix, {'description': ''}, {'description': None})
        apply('drop', prefix, {'confirmation': prefix}, None)
        for suffix, values, invocation in (
                ('ZERO', {'arguments': [], 'return_mechanism': 'VALUE',
                          'entrypoint': 'owned_zero'}, ('', 47)),
                ('PARAM', {'arguments': [{'data_type': 'INTEGER'},
                                         {'data_type': 'INTEGER',
                                          'mechanism': 'DESCRIPTOR'}],
                           'return_mode': 'PARAMETER', 'return_parameter': 2,
                           'entrypoint': 'owned_parameter_descriptor'},
                 ('5', 34)),
                ('STRING', {'arguments': [{'data_type': 'CSTRING',
                                           'length': 128}],
                            'return_data_type': 'CSTRING',
                            'return_length': 128,
                            'entrypoint': 'owned_string'},
                 ("'hello'", 'hello!'))):
            name = prefix + '_' + suffix
            apply('create', name, {'name': name,
                                   'module_name': 'cde_owned_udf', **values},
                  {}, invocation)
            apply('drop', name, {'confirmation': name}, None)
        result['passed'] = len(result['checks']) == 13
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
