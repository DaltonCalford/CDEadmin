#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Execute local/global mapping forms on the matrix gate's isolated server."""

import json
import subprocess
import uuid

from cdeadmin_firebird_role_ui_gate import (
    arguments, forms, firebird, _resources, _route_arguments,
    create_driver, close_workspace, plan_preview, screenshot,
    WebDriverWait, visible_named_control,
)
from cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION, _create_client
from cdeadmin_firebird_ui_form_gate import fill_fields
from pgadmin.cdeadmin.providers.firebird import mappings
from types import SimpleNamespace


def run(options, profiles):
    document = json.loads(profiles.read_text())
    route = document['profiles'][0]
    container = document['fixture_container']
    if (not container.startswith('cdeadmin-mapping-qa-') or
            route['database'] != '/var/lib/firebird/data/matrix.fdb' or
            route['host'] != '127.0.0.1' or
            options.database_path != route['database']):
        raise ValueError('Mapping mutations require the isolated '
                         'matrix server')
    inspected = json.loads(subprocess.check_output(
        ['docker', 'inspect', container], text=True))[0]
    assert inspected['Config']['Labels']['org.cdeadmin.fixture'] == (
        'firebird-mapping-matrix')
    assert inspected['NetworkSettings']['Ports']['3050/tcp'] == [{
        'HostIp': '127.0.0.1', 'HostPort': str(route['port'])}]
    client = _create_client(SimpleNamespace(acquire_secret=None))
    native = firebird.connect(password=route['password'],
                              **_route_arguments(route, firebird))
    browser = None
    name = 'CDE_UI_MAP_' + uuid.uuid4().hex[:16].upper()
    result = {'passed': False, 'checks': [], 'failures': [],
              'expected_mutation_count': 22, 'negative_checks': [],
              'credential_values_exported': False, 'fixtures_removed': False}

    def state(kind):
        items = _resources(native, {'route': route})
        native.commit()
        return next((item['native'] for item in items if
                     item['resource_kind'] == kind and
                     item['display_name'] == name), None)

    def refresh():
        probe = forms._workspace_probe(browser, list(mappings.KINDS))
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
              .then(async response => done({status: response.status,
                body: await response.json()}))
              .catch(error => done({error: String(error)}));
            """, probe['endpoint_url'], probe['database_target_id'],
            probe['generation'])
        assert response.get('status') == 200, response
        return forms._workspace_probe(browser, list(mappings.KINDS))

    def apply(kind, operation_id, values, check):
        print(kind + ': ' + operation_id, flush=True)
        probe = refresh()
        operation = next(op for op in forms._enumerate_operations(
            probe['catalog'], [kind], [operation_id]))
        assert operation['execution_available'] is True
        target = next((item for item in probe['resources'] if
                       item['resource_kind'] == kind and
                       item['display_name'] == name), None)
        if target is None:
            assert operation_id == 'create'
            target = {'resource_id': 'cdeadmin-create-scope:' + kind}
        forms._open_focused_form(browser, operation, target,
                                 probe['database_target_id'])
        forms._wait_for_operation(wait, operation)
        labels = {f['field_id']: f['label']
                  for f in operation['form']['fields']}
        plan = plan_preview(browser, wait, operation,
                            {labels[k]: v for k, v in values.items()})
        confirmation = visible_named_control(
            browser, 'I confirm this provider-planned operation.')
        if confirmation is not None and not confirmation.is_selected():
            confirmation.click()
        button = wait.until(lambda driver: visible_named_control(
            driver, 'Apply provider plan'))
        wait.until(lambda driver: button.is_enabled())
        button.click()
        output = wait.until(lambda driver: next((
            element for element in driver.find_elements(
                'css selector', '[aria-label="Provider operation result"]')
            if element.is_displayed()), None))
        assert json.loads(output.text)['accepted'] is True
        observed = state(kind)
        check(observed)
        path = options.output_root / (
            f'{len(result["checks"]):02d}-{kind}-{operation_id}.png')
        digest = screenshot(browser, path)
        result['checks'].append({
            'kind': kind, 'operation': operation_id,
            'statements': plan['command_preview']['statements'],
            'native_postcondition': observed, 'screenshot': str(path),
            'sha256': digest})
        close_workspace(browser, wait)

    def expect(actual, **values):
        for key, value in values.items():
            assert actual[key] == value, (key, actual[key], value)

    try:
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        for kind in mappings.KINDS:
            apply(kind, 'create', {
                'name': name, 'plugin': 'Srp256',
                'from_name': 'CDE_UI_UNMATCHED'}, lambda row: expect(
                    row, plugin='Srp256', **{'from': 'CDE_UI_UNMATCHED'}))
            # Change one field only: unchanged native definition must survive.
            apply(kind, 'alter', {'to_name': 'CDE_UI_TARGET'},
                  lambda row: expect(row, plugin='Srp256',
                                     to='CDE_UI_TARGET',
                                     **{'from': 'CDE_UI_UNMATCHED'}))
            for mode, source_type, any_name, to_type in (
                    ('ANY_PLUGIN', 'GROUP', False, 'ROLE'),
                    ('SERVERWIDE', 'Predefined_Group', True, 'USER'),
                    ('MAPPING', 'ROLE', False, 'ROLE'),
                    ('ANY', 'USER', True, 'USER'),
                    ('PLUGIN', 'USER', False, 'ROLE')):
                changes = {
                    'using_mode': mode,
                    **({'plugin': 'Srp256'} if mode == 'PLUGIN' else {}),
                    **({'source_database': 'Case sensitive db'}
                       if mode != 'SERVERWIDE' else {}),
                    'from_type': source_type, 'from_any': any_name,
                    **({'from_name': 'CDE_UI_IDENTITY é'}
                       if not any_name else {}),
                    'to_type': to_type,
                    'to_name': 'CDE_UI_ROLE' if to_type == 'ROLE' else '',
                }

                def check_variant(row):
                    expected = {
                        'using_mode': mode, 'from_type': source_type,
                        'from_any': any_name, 'to_type': to_type,
                        'source_database': ('' if mode == 'SERVERWIDE'
                                            else 'Case sensitive db'),
                        'plugin': 'Srp256' if mode == 'PLUGIN' else '',
                        'to_name': 'CDE_UI_ROLE' if to_type == 'ROLE' else '',
                    }
                    expect(row['mapping_draft'], **expected)
                apply(kind, 'alter', changes, check_variant)
            apply(kind, 'comment', {'description': 'Browser mapping comment'},
                  lambda row: expect(row,
                                     description='Browser mapping comment'))
            apply(kind, 'comment', {'description': ''},
                  lambda row: expect(row, description=None))
            if kind == mappings.KINDS[1]:
                probe = refresh()
                operation = next(op for op in forms._enumerate_operations(
                    probe['catalog'], [kind], ['comment']))
                target = next(item for item in probe['resources'] if
                              item['resource_kind'] == kind and
                              item['display_name'] == name)
                before = state(kind)
                forms._open_focused_form(browser, operation, target,
                                         probe['database_target_id'])
                forms._wait_for_operation(wait, operation)
                fill_fields(wait, ['Comment=Unicode é must not be corrupted'])
                button = visible_named_control(browser, 'Validate and preview')
                wait.until(lambda driver: button.is_enabled())
                button.click()
                wait.until(lambda driver: 'non-ASCII' in
                           driver.find_element('tag name', 'body').text)
                assert not visible_named_control(
                    browser, 'Apply provider plan').is_enabled()
                assert state(kind) == before
                path = options.output_root / 'global-comment-rejected.png'
                result['negative_checks'].append({
                    'case': 'global-non-ascii-comment-no-mutation',
                    'screenshot': str(path),
                    'sha256': screenshot(browser, path)})
                close_workspace(browser, wait)
            apply(kind, 'create_or_alter', {
                'name': name, 'using_mode': 'ANY_PLUGIN', 'from_any': True,
                'to_type': 'USER'}, lambda row: expect(
                    row, using='P', plugin=None, **{'from': '*'}))

            def absent(row):
                assert row is None
            apply(kind, 'drop', {'confirmation': name}, absent)
        result['passed'] = (len(result['checks']) == 22 and
                            len(result['negative_checks']) == 1)
    except Exception as error:
        result['failures'].append({'type': type(error).__name__,
                                   'message': str(error)})
        if browser is not None:
            screenshot(browser, options.output_root / 'failure.png')
    finally:
        if browser is not None:
            forms._quit_driver(browser)
        for kind in mappings.KINDS:
            if state(kind) is not None:
                plan = ADMINISTRATION.plan({
                    '_provider_route': route, 'resource_kind': kind,
                    'operation_id': 'drop', 'draft': {'confirmation': name},
                    'target_resource': {'display_name': name}})
                ADMINISTRATION.apply(client, plan, connection=native)
                native.commit()
        result['fixtures_removed'] = all(state(kind) is None
                                         for kind in mappings.KINDS)
        native.close()
    return result


def main():
    options, profiles = arguments()
    result = run(options, profiles)
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] and result['fixtures_removed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
