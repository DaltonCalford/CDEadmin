#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Apply both mapping choices through the UI, only in a disposable database."""

import json
import os
from pathlib import PurePosixPath

from cdeadmin_firebird_role_ui_gate import arguments, forms, firebird
from cdeadmin_firebird_ui_form_gate import (
    close_workspace, create_driver, plan_preview, screenshot,
)
from cdeadmin_ui_evidence import visible_named_control
from selenium.webdriver.support.ui import WebDriverWait
from pgadmin.cdeadmin.providers.firebird.provider import (
    _admin_mapping_state, _route_arguments,
)


def run(options, profiles):
    path = PurePosixPath(options.database_path)
    if not path.name.startswith('cdeadmin_mapping_') or path.suffix != '.fdb':
        raise ValueError('Mapping mutation UI gate requires its disposable DB')
    document = json.loads(profiles.read_text())
    route = next(dict(p) for p in document['profiles']
                 if p['engine'] == 'firebird')
    if path.parent != PurePosixPath(route['database']).parent:
        raise ValueError('Unexpected disposable mapping database parent')
    route['database'] = str(path)
    route.setdefault('host', document.get('host', '127.0.0.1'))
    firebird.driver_config.fb_client_library.value = os.environ[
        'CDEADMIN_FIREBIRD_CLIENT_LIBRARY']
    connection = firebird.connect(password=route['password'],
                                  **_route_arguments(route, firebird))
    driver = None
    result = {'passed': False, 'checks': [], 'failures': [],
              'credential_values_exported': False}
    try:
        driver = create_driver(options)
        driver.set_script_timeout(120)
        wait = WebDriverWait(driver, options.timeout)
        forms._prepare_tree(driver, wait, options)
        probe = forms._workspace_probe(driver, ['role'])
        role = next(item for item in probe['resources']
                    if item['resource_kind'] == 'role' and
                    item['display_name'] == 'RDB$ADMIN')
        operation = next(item for item in forms._enumerate_operations(
            probe['catalog'], ['role'], ['configure_admin_mapping']))
        for action in ('SET', 'DROP'):
            forms._open_focused_form(driver, operation, role,
                                     probe['database_target_id'])
            forms._wait_for_operation(wait, operation)
            plan = plan_preview(driver, wait, operation, {
                'Windows administrator mapping': action})
            expected = f'ALTER ROLE "RDB$ADMIN" {action} AUTO ADMIN MAPPING'
            assert [item['source'] for item in
                    plan['command_preview']['statements']] == [expected]
            confirm = wait.until(lambda browser: visible_named_control(
                browser, 'I confirm this provider-planned operation.'))
            if not confirm.is_selected():
                confirm.click()
            button = wait.until(lambda browser: visible_named_control(
                browser, 'Apply provider plan'))
            wait.until(lambda browser: button.is_enabled())
            button.click()
            rendered = wait.until(lambda browser: next((
                element for element in browser.find_elements(
                    'css selector', '[aria-label="Provider operation result"]')
                if element.is_displayed()), None))
            assert json.loads(rendered.text)['accepted'] is True
            with connection.cursor() as cursor:
                state = _admin_mapping_state(cursor)
            connection.commit()
            assert state['available'] is True
            assert state['present'] is (action == 'SET')
            if action == 'SET':
                assert state['canonical'] is True
            image = options.output_root / (action + '.png')
            digest = screenshot(driver, image)
            result['checks'].append({'action': action,
                                     'statement': expected,
                                     'native_postcondition': state,
                                     'screenshot': str(image),
                                     'sha256': digest})
            close_workspace(driver, wait)
        result['passed'] = True
    except Exception as error:
        result['failures'].append({'type': type(error).__name__,
                                   'message': str(error)})
        if driver:
            screenshot(driver, options.output_root / 'failure.png')
    finally:
        if driver:
            forms._quit_driver(driver)
        connection.close()
    return result


def main():
    options, profiles = arguments()
    result = run(options, profiles)
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
