#!/usr/bin/env python3
"""Save Firebird linger preferences visually and observe the owned cache."""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import cdeadmin_firebird_database_lifecycle_ui_gate as lifecycle  # noqa: E402
from tools.cdeadmin_firebird_repair_gate import owned_database_open_files  # noqa: E402


shared = lifecycle.shared
LABEL = 'Attachment linger policy'
LABELS = {'SERVER_DEFAULT': 'Use server preference',
          'NATIVE_DEFAULT': 'Native default',
          'SUPPRESS': 'Suppress current cache linger (SuperServer)'}


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('url', 'database-root', 'database'):
        parser.add_argument('--' + name, required=True)
    for name in ('config-db', 'client-library', 'profiles', 'output-root',
                 'summary-output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--engine', choices=('Firebird',), default='Firebird')
    parser.add_argument('--server', default='localhost')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--firebird-port', type=int, required=True)
    parser.add_argument('--user', default='SYSDBA')
    parser.add_argument(
        '--password-env', default='CDEADMIN_FIREBIRD_DEMO_PASSWORD')
    parser.add_argument('--browser-binary')
    parser.add_argument('--width', type=int, default=1600)
    parser.add_argument('--height', type=int, default=1000)
    parser.add_argument('--theme', choices=('default', 'high-contrast'),
                        default='high-contrast')
    parser.add_argument('--font-scale', type=int, choices=(100, 200, 300),
                        default=100)
    parser.add_argument('--timeout', type=int, default=90)
    return parser.parse_args(argv)


def owned_profile(options):
    profiles = json.loads(options.profiles.read_text()).get('profiles', [])
    if len(profiles) != 1:
        raise ValueError('Expected one owned linger profile')
    profile = profiles[0]
    database = profile.get('database', '')
    if (profile.get('fixture_kind') != 'firebird-no-linger-qualification' or
            profile.get('engine') != 'firebird' or
            profile.get('host') != '127.0.0.1' or
            options.host != '127.0.0.1' or
            profile.get('port') != options.firebird_port or
            profile.get('user') != options.user or
            not isinstance(database, str) or not re.fullmatch(
                r'/var/lib/firebird/data/owned_repair_linger_\d+\.fdb',
                database) or
            Path(database).name != options.database or
            str(Path(database).parent) != options.database_root):
        raise ValueError('Browser target is not the owned linger fixture')
    owned_database_open_files(profile.get('owned_container_id', ''), database)
    return profile


def observe_saved_policy(options, module, password, selected, profile,
                         expected):
    if expected not in ('NATIVE_DEFAULT', 'SUPPRESS'):
        raise ValueError('Unresolved linger policy')
    if selected['database'] != profile['database']:
        raise ValueError(
            'Saved database differs from the owned linger fixture')
    routes = lifecycle._saved_route(options, selected['target_id'])
    service = lifecycle.EndpointService(SimpleNamespace(), SimpleNamespace(
        secrets=SimpleNamespace(register_resolver=lambda *_args: None)))
    route, _reference = service._route_and_reference(
        SimpleNamespace(user_id=0),
        SimpleNamespace(routes=routes, secret_references=[]),
        {'requires_secret': False, 'form_contract': {'database': {
            'forms': options.database_forms}}},
        database_override=selected['database'],
        database_options=selected['configuration'])
    if (route.get('host') != options.host or
            int(route.get('port', 0)) != options.firebird_port or
            route.get('database') != profile['database']):
        raise ValueError('Saved route escaped the owned linger fixture')

    # Explicitly reset this owned cache before each observation. This is test
    # instrumentation, not production session inspection or a stored mutation.
    reset = module.driver_config.register_database(
        'owned_reset_' + uuid.uuid4().hex)
    reset.dsn.value = (
        f'127.0.0.1/{options.firebird_port}:{profile["database"]}')
    reset.no_linger.value = True
    with module.connect(reset.name, user=options.user, password=password):
        pass
    container = profile['owned_container_id']
    deadline = time.monotonic() + 5
    while owned_database_open_files(container, profile['database']):
        if time.monotonic() >= deadline:
            raise RuntimeError('Owned cache still has an attachment')
        time.sleep(0.1)
    args = lifecycle._route_arguments(route, module)
    config = module.driver_config.get_database(args['database'])
    expected_flag = True if expected == 'SUPPRESS' else None
    if config.no_linger.value is not expected_flag:
        raise RuntimeError('Saved policy did not reach the native DPB')
    with module.connect(password=password, **args) as handle:
        lifecycle._initialize_connection(handle, route, module)
        with handle.cursor() as cursor:
            cursor.execute('SELECT RDB$LINGER FROM RDB$DATABASE')
            if cursor.fetchone() != (600,):
                raise RuntimeError('Stored database linger changed')
        handle.rollback()
    count = owned_database_open_files(container, profile['database'])
    deadline = time.monotonic() + 5
    while expected == 'SUPPRESS' and count and time.monotonic() < deadline:
        time.sleep(0.1)
        count = owned_database_open_files(container, profile['database'])
    if (count == 0) is not (expected == 'SUPPRESS'):
        raise RuntimeError('Native cache lifetime differs from saved policy')
    return {'effective_policy': expected, 'open_files_after_detach': count,
            'stored_linger': 600, 'native_dpb_verified': True}


def capture(driver, wait, folder):
    folder.mkdir(parents=True, exist_ok=False)
    field = wait.until(
        lambda value: shared.visible_named_control(value, LABEL))
    driver.execute_script(
        'arguments[0].scrollIntoView({block:"center",inline:"nearest"})',
        field)
    layout = driver.execute_script('''
      return {controls:[{width:arguments[0].clientWidth,
        content_width:arguments[0].scrollWidth,
        height:arguments[0].clientHeight,
        content_height:arguments[0].scrollHeight,
        white_space:getComputedStyle(arguments[0]).whiteSpace}]};
    ''', field)
    (folder / 'layout.json').write_text(json.dumps(layout, indent=2) + '\n')
    lifecycle._validate_selected_value_layout(layout)
    crop = folder / 'linger-control.png'
    if not field.screenshot(str(crop)):
        raise RuntimeError('Linger control capture failed')
    context = folder / 'context.png'
    return {'layout': layout, 'control': str(crop),
            'control_sha256': hashlib.sha256(crop.read_bytes()).hexdigest(),
            'context': str(context), 'context_sha256': lifecycle.screenshot(
                driver, context, reset_scroll=False)}


def parent_case(driver, wait, options, choice, parent, target, module,
                password, profile):
    phase = choice + '-parent-' + parent
    shared._open_form(driver, wait, 'server_edit')
    shared.fill_fields(wait, [LABEL + '=' + LABELS[parent]])
    button = wait.until(lambda value: shared.visible_named_control(
        value, 'Save endpoint profile'))
    button.click()
    wait.until(lambda value: any(
        item.is_displayed() and 'Endpoint profile saved.' in item.text
        for item in value.find_elements(By.CSS_SELECTOR, '[role="alert"]')))
    saved_route = json.loads(lifecycle._saved_route(
        options, target()['target_id'])[0].configuration)
    if saved_route.get('no_linger') != parent:
        raise RuntimeError('Server linger policy did not persist')
    wait.until(lambda value: value.execute_script('''
      const tree=window.pgAdmin?.Browser?.tree;
      const selected=tree?.selected?.();
      const data=selected ? tree.itemData(selected) : null;
      return data?._type === 'server' &&
        data.runtime_verification_state === 'stale' &&
        data.cde_session_authenticated === false;
    '''))
    server_proof = capture(
        driver, wait, options.output_root / ('server-' + phase))
    shared._close(driver, wait)
    shared._open_form(driver, wait, 'edit', options.database)
    field = wait.until(
        lambda value: shared.visible_named_control(value, LABEL))
    if ' '.join(field.text.split()) != LABELS[choice]:
        raise RuntimeError('Reopened database policy differs')
    proof = capture(driver, wait, options.output_root / phase)
    selected = target()
    if selected['configuration'].get('no_linger') != choice:
        raise RuntimeError('Parent edit rewrote database policy')
    shared._close(driver, wait)
    observed = observe_saved_policy(
        options, module, password, selected, profile,
        parent if choice == 'SERVER_DEFAULT' else choice)
    return {'choice': choice, 'parent': parent, 'server': server_proof,
            'reopened': proof, 'native': observed,
            'navigator_verification_invalidated': True,
            'application_reloaded': False}


def run(options):
    profile = owned_profile(options)
    password = os.environ.get(options.password_env)
    if not password:
        raise RuntimeError('Owned test credential is missing')
    options.endpoint_password_env = options.password_env
    lifecycle._configure_shared(password)
    module = lifecycle._load_firebird(options)
    driver = lifecycle.create_driver(options)
    driver.set_script_timeout(max(120, options.timeout * 10))
    wait = WebDriverWait(driver, options.timeout)
    result = {'schema': 'cdeadmin.firebird-linger-preferences-ui.v1',
              'complete': False, 'cases': [], 'target_setups': [],
              'failures': [],
              'font_scale': options.font_scale}

    def target():
        return next(row for row in shared._target_rows(options.config_db)
                    if row['display_name'] == options.database)

    def record_failure(phase, error):
        record = {'phase': phase, 'error_type': type(error).__name__,
                  'frames': [{'file': Path(frame.filename).name,
                              'function': frame.name, 'line': frame.lineno}
                             for frame in traceback.extract_tb(
                                 error.__traceback__)[-5:]]}
        try:
            path = options.output_root / (phase + '-failure.png')
            path.parent.mkdir(parents=True, exist_ok=True)
            if driver.save_screenshot(str(path)):
                record['screenshot'] = str(path)
        except Exception as screenshot_error:
            record['screenshot_error_type'] = type(screenshot_error).__name__
        result['failures'].append(record)

    try:
        driver.get(options.url.rstrip('/') + '/browser/')
        shared._prepare_tree(driver, wait, options, options.database)
        options.database_forms = shared._catalog_forms(driver)
        for choice in LABELS:
            phase = 'target-' + choice
            try:
                shared._open_form(driver, wait, 'edit', options.database)
                shared.fill_fields(wait, [LABEL + '=' + LABELS[choice]])
                shared._submit_target_form(driver, wait, 'edit', {})
                wait.until(lambda _driver: target()['configuration'].get(
                    'no_linger') == choice)
                proof = capture(driver, wait, options.output_root / phase)
                result['target_setups'].append({'choice': choice, **proof})
                shared._close(driver, wait)
            except Exception as error:
                record_failure(phase, error)
                continue
            for parent in ('NATIVE_DEFAULT', 'SUPPRESS'):
                phase = choice + '-parent-' + parent
                try:
                    result['cases'].append(parent_case(
                        driver, wait, options, choice, parent, target, module,
                        password, profile))
                except Exception as error:
                    record_failure(phase, error)
                    try:
                        shared._close(driver, wait)
                    except Exception as cleanup_error:
                        record_failure(
                            phase + '-dialog-cleanup', cleanup_error)
    except Exception as error:
        record_failure('setup', error)
    finally:
        try:
            driver.quit()
        except Exception as error:
            record_failure('browser-cleanup', error)
    result['complete'] = (len(result['cases']) == 6 and
                          len(result['target_setups']) == 3 and
                          not result['failures'])
    return result


def main():
    options = arguments()
    result = run(options)
    options.summary_output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'cases': len(result['cases']),
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
