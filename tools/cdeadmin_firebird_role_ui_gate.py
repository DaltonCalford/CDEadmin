#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Execute Firebird role forms against uniquely named disposable roles."""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'web'))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(ROOT / 'web/pgadmin')]
    sys.modules['pgadmin'] = package

from firebird import driver as firebird  # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402
from selenium.webdriver.common.by import By  # noqa: E402
from tools import cdeadmin_provider_object_form_gate as forms  # noqa: E402
from tools.cdeadmin_firebird_ui_form_gate import (  # noqa: E402
    assert_form_controls, close_workspace, create_driver, plan_preview,
    screenshot,
)
from tools.cdeadmin_ui_evidence import visible_named_control  # noqa: E402
from pgadmin.cdeadmin.providers.firebird.provider import (  # noqa: E402
    _resources, _route_arguments,
)


def run(options, profiles):
    document = json.loads(profiles.read_text())
    profile = next(dict(value) for value in document['profiles']
                   if value['engine'] == 'firebird')
    profile.setdefault('host', document.get('host', '127.0.0.1'))
    if profile['database'] != options.database_path:
        raise ValueError('Browser and native verification targets differ')
    firebird.driver_config.fb_client_library.value = os.environ[
        'CDEADMIN_FIREBIRD_CLIENT_LIBRARY']
    native = firebird.connect(password=profile['password'],
                              **_route_arguments(profile, firebird))
    try:
        browser = create_driver(options)
    except Exception:
        native.close()
        raise
    browser.set_script_timeout(120)
    wait = WebDriverWait(browser, options.timeout)
    prefix = 'CDE_UI_ROLE_' + uuid.uuid4().hex[:12].upper()
    names = [prefix, prefix + '_MEMBER']
    print('Disposable roles: ' + ', '.join(names), flush=True)
    result = {'passed': False, 'checks': [], 'failures': [],
              'fixtures': names, 'expected_mutation_count': 10,
              'credential_values_exported': False}

    def state(name):
        resources = _resources(native, {'route': profile})
        native.commit()
        return next((item['native'] for item in resources
                     if item['resource_kind'] == 'role' and
                     item['display_name'] == name), None)

    def refresh():
        current = forms._workspace_probe(browser, ['role'])
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
            """, current['endpoint_url'], current['database_target_id'],
            current['generation'])
        if response.get('status') != 200:
            raise RuntimeError('Role catalog refresh failed')
        return forms._workspace_probe(browser, ['role'])

    def apply(label, operation_id, name, values):
        nonlocal probe
        print('role mutation: ' + label, flush=True)
        probe = refresh()
        operation = next(op for resource in probe['catalog']['objects']
                         if resource['resource_kind'] == 'role'
                         for op in resource['operations']
                         if op['operation_id'] == operation_id)
        if not operation.get('execution_available'):
            raise RuntimeError('Role operation blocked: ' + operation_id)
        target = next((item for item in probe['resources']
                       if item['resource_kind'] == 'role' and
                       item['display_name'] == name), None)
        if target is None and operation_id == 'create':
            target = {'resource_id': 'cdeadmin-create-scope:role'}
        if target is None:
            raise RuntimeError('Created role missing from refreshed catalog')
        operation = {**operation, 'resource_kind': 'role'}
        forms._open_focused_form(browser, operation, target,
                                 probe['database_target_id'])
        forms._wait_for_operation(wait, operation)
        fields = operation['form']['fields']
        assert_form_controls(wait, fields)
        labels = {field['field_id']: field['label'] for field in fields}
        plan_preview(browser, wait, operation, {
            labels[key]: json.dumps(value) if isinstance(value, list)
            else str(value) for key, value in values.items()})
        confirmation = visible_named_control(
            browser, 'I confirm this provider-planned operation.')
        if confirmation is not None and not confirmation.is_selected():
            confirmation.click()
        button = wait.until(lambda current: visible_named_control(
            current, 'Apply provider plan'))
        wait.until(lambda current: button.is_enabled())
        button.click()
        rendered = wait.until(lambda current: next((
            item for item in current.find_elements(
                By.CSS_SELECTOR, '[aria-label="Provider operation result"]')
            if item.is_displayed()), None))
        observed = json.loads(rendered.text)
        if observed.get('accepted') is not True:
            raise RuntimeError('Browser role mutation was not accepted')
        path = options.output_root / (label + '.png')
        digest = screenshot(browser, path)
        close_workspace(browser, wait)
        result['checks'].append({'case': label, 'operation': operation_id,
                                 'screenshot': str(path), 'sha256': digest})

    try:
        forms._prepare_tree(browser, wait, options)
        probe = forms._workspace_probe(browser, ['role'])
        for name in names:
            assert state(name) is None
        apply('create', 'create', names[0], {
            'name': names[0], 'description': 'Browser-created role',
            'system_privileges': ['MONITOR_ANY_ATTACHMENT']})
        value = state(names[0])
        assert value['description'] == 'Browser-created role'
        assert value['system_privileges'] == ['MONITOR_ANY_ATTACHMENT']
        apply('create-member', 'create', names[1], {'name': names[1]})
        assert state(names[1]) is not None
        apply('alter', 'alter', names[0], {
            'description': 'Browser-altered role',
            'system_privileges': ['USER_MANAGEMENT']})
        value = state(names[0])
        assert value['description'] == 'Browser-altered role'
        assert value['system_privileges'] == ['USER_MANAGEMENT']
        apply('clear', 'alter', names[0], {
            'clear_description': True, 'drop_system_privileges': True})
        value = state(names[0])
        assert not value['description'] and value['system_privileges'] == []
        apply('grant', 'grant', names[0], {
            'member': names[1], 'member_kind': 'ROLE',
            'default_role': True, 'admin_option': True})
        grants = state(names[0])['memberships']
        assert len(grants) == 1 and grants[0]['grant_option'] == 2
        assert grants[0]['default_role'] is True
        for label, changes, default in (
            ('revoke-admin', {'admin_option_only': True}, True),
            ('revoke-default', {'default_role': True}, False),
        ):
            apply(label, 'revoke', names[0], {
                'member': names[1], 'member_kind': 'ROLE',
                'confirmation': names[0], **changes})
            grants = state(names[0])['memberships']
            assert len(grants) == 1 and grants[0]['grant_option'] == 0
            assert grants[0]['default_role'] is default
        apply('revoke', 'revoke', names[0], {
            'member': names[1], 'member_kind': 'ROLE',
            'confirmation': names[0]})
        assert state(names[0])['memberships'] == []
        for index, name in enumerate(reversed(names)):
            apply('drop-' + str(index), 'drop', name, {'confirmation': name})
            assert state(name) is None
        result['passed'] = True
    except Exception as error:
        result['failures'].append({'type': type(error).__name__,
                                   'message': str(error)})
        options.output_root.mkdir(parents=True, exist_ok=True)
        browser.save_screenshot(str(options.output_root / 'failure.png'))
    finally:
        forms._quit_driver(browser)
        for name in reversed(names):
            try:
                if state(name) is not None:
                    with native.cursor() as cursor:
                        cursor.execute('DROP ROLE "' + name + '"')
                    native.commit()
            except Exception as error:
                try:
                    native.rollback()
                except Exception:
                    pass
                result['failures'].append({'cleanup_role': name,
                                           'type': type(error).__name__})
        try:
            result['fixtures_removed'] = all(
                state(name) is None for name in names)
        except Exception as error:
            result['fixtures_removed'] = False
            result['failures'].append({'cleanup_verification': 'unavailable',
                                       'type': type(error).__name__})
        try:
            native.close()
        except Exception as error:
            result['failures'].append({'native_close': 'failed',
                                       'type': type(error).__name__})
    result['passed'] &= not result['failures'] and result['fixtures_removed']
    result['dependent_cases_not_completed'] = (
        result['expected_mutation_count'] - len(result['checks']))
    return result


def arguments(argv=None):
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--database-path', required=True)
    special, remainder = parser.parse_known_args(argv)
    options = forms.arguments(remainder)
    options.database_path = special.database_path
    return options, special.profiles


def main():
    options, profiles = arguments()
    result = run(options, profiles)
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
