#!/usr/bin/env python3
"""Exercise character metadata forms only on owned matrix databases."""

import json
import os
import re
import uuid
from pathlib import PurePosixPath

from cdeadmin_firebird_role_ui_gate import (
    arguments, forms, firebird, _resources, _route_arguments,
    create_driver, close_workspace, plan_preview, screenshot,
    WebDriverWait, visible_named_control,
)
from cdeadmin_firebird_query_ui_gate import _load_profile
from cdeadmin_firebird_ui_form_gate import (
    screenshot_form_pages, click_unobscured,
)


def run(options, profiles):
    profile = _load_profile(profiles)
    original = PurePosixPath(profile['database'])
    target = PurePosixPath(options.database_path)
    if (target.parent != original.parent or target == original or
            not re.fullmatch(r'cde_history_ui_[0-9a-f]{32}\.fdb', target.name)
            or profile['host'] != '127.0.0.1'):
        raise ValueError('Character metadata UI requires an owned matrix DB')
    profile['database'] = str(target)
    firebird.driver_config.fb_client_library.value = os.environ[
        'CDEADMIN_FIREBIRD_CLIENT_LIBRARY']
    native = firebird.connect(password=profile['password'],
                              **_route_arguments(profile, firebird))
    browser = None
    name = 'CDE_UI_COLL_' + uuid.uuid4().hex[:16].upper()
    result = {'passed': False, 'checks': [], 'failures': [],
              'credential_values_exported': False,
              'target_database': str(target), 'expected_mutation_count': 8}

    def state(kind, object_name):
        resources = _resources(native, {'route': profile})
        native.rollback()
        return next((item['native'] for item in resources
                     if item['resource_kind'] == kind and
                     item['display_name'] == object_name), None)

    def refresh():
        current = forms._workspace_probe(
            browser, ['collation', 'character-set'], False)
        response = browser.execute_async_script(
            """
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
        return forms._workspace_probe(browser, ['collation', 'character-set'],
                                      False)

    def apply(kind, operation_id, object_name, values, expected):
        print(kind + ': ' + operation_id, flush=True)
        probe = refresh()
        operation = next(iter(forms._enumerate_operations(
            probe['catalog'], [kind], [operation_id])))
        assert operation['execution_available'] is True
        selected = next((item for item in probe['resources'] if
                         item['resource_kind'] == kind and
                         item['display_name'] == object_name), None)
        if selected is None:
            assert operation_id == 'create'
            selected = {'resource_id': 'cdeadmin-create-scope:' + kind}
        forms._open_focused_form(browser, operation, selected,
                                 probe['database_target_id'])
        forms._wait_for_operation(wait, operation)
        labels = {field['field_id']: field['label'] for field in
                  operation['form']['fields']}
        plan = plan_preview(browser, wait, operation, {
            labels[key]: value for key, value in values.items()})
        prefix = options.output_root / (
            f'{len(result["checks"]):02d}-{kind}-{operation_id}')
        pages = screenshot_form_pages(browser, prefix)
        confirmation = visible_named_control(
            browser, 'I confirm this provider-planned operation.')
        if confirmation is not None and not confirmation.is_selected():
            click_unobscured(browser, wait, confirmation)
        button = wait.until(lambda driver: visible_named_control(
            driver, 'Apply provider plan'))
        wait.until(lambda driver: button.is_enabled())
        click_unobscured(browser, wait, button)
        output = wait.until(lambda driver: next((
            element for element in driver.find_elements(
                'css selector', '[aria-label="Provider operation result"]')
            if element.is_displayed()), None))
        assert json.loads(output.text)['accepted'] is True
        observed = state(kind, object_name)
        if expected is None:
            assert observed is None
        else:
            assert observed is not None
            for key, value in expected.items():
                assert observed[key] == value, key
        result['checks'].append({
            'kind': kind, 'operation': operation_id,
            'statements': plan['command_preview']['statements'],
            'native_postcondition': observed, 'screenshots': pages,
            'recent_request_timings': browser.execute_script("""
              return performance.getEntriesByType('resource').slice(-25)
                .filter(item => item.initiatorType === 'fetch' ||
                  item.initiatorType === 'xmlhttprequest')
                .map(item => ({path: new URL(item.name).pathname,
                  duration_ms: item.duration}));
            """)})
        # Accepted execution precedes asynchronous catalog refresh. Respect
        # the production close guard instead of racing those follow-up checks.
        field_label = next(field['label'] for field in
                           operation['form']['fields'] if
                           field['control'] in {'text', 'multiline'})
        # After DROP the preview button correctly stays disabled because the
        # target no longer exists. Text inputs still expose the busy state.
        wait.until(lambda driver: (
            visible_named_control(driver, field_label) is not None and
            visible_named_control(driver, field_label).is_enabled()))
        close_workspace(browser, wait)

    try:
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        original_default = state('character-set', 'UTF8')['default_collation']
        apply('collation', 'create', name, {
            'name': name, 'base_collation': 'UNICODE',
            'padding': 'NO_PAD', 'case_sensitivity': 'INSENSITIVE',
            'accent_sensitivity': 'INSENSITIVE',
            'specific_attributes': [{'name': 'NUMERIC-SORT', 'value': '1'}],
            'description': 'Browser creation comment'},
            {'attributes': '6', 'description': 'Browser creation comment'})
        apply('collation', 'comment', name,
              {'description': 'Modified comment'},
              {'description': 'Modified comment'})
        apply('collation', 'comment', name, {'description': ''},
              {'description': None})
        apply('character-set', 'comment', 'UTF8',
              {'description': 'Browser charset comment'},
              {'description': 'Browser charset comment'})
        apply('character-set', 'comment', 'UTF8', {'description': ''},
              {'description': None})
        apply('character-set', 'alter', 'UTF8', {'default_collation': name},
              {'default_collation': name})
        apply('character-set', 'alter', 'UTF8',
              {'default_collation': original_default},
              {'default_collation': original_default})
        apply('collation', 'drop', name, {'confirmation': name}, None)
        result['passed'] = len(result['checks']) == 8
    except Exception as error:
        result['failures'].append({'type': type(error).__name__})
        if browser is not None:
            screenshot(browser, options.output_root / 'failure.png')
        raise
    finally:
        if browser is not None:
            forms._quit_driver(browser)
        native.close()
        # The matrix owner drops the entire uniquely named database, even if
        # a form failed midway. Never mutate or restore a user's database.
        options.summary_output.parent.mkdir(parents=True, exist_ok=True)
        options.summary_output.write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    options, profiles = arguments()
    outcome = run(options, profiles)
    print(json.dumps({'passed': outcome['passed'],
                      'checks': len(outcome['checks'])}))
    raise SystemExit(0 if outcome['passed'] else 1)
