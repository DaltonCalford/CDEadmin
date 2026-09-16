#!/usr/bin/env python3
"""Save/reopen cache preferences and observe an owned SuperClassic server."""

import json
import os
import re
import traceback
from pathlib import Path
from types import SimpleNamespace

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

if __package__:
    from . import cdeadmin_firebird_linger_ui_gate as linger
    from .cdeadmin_firebird_logical_volumes_gate import docker, OWNER
else:
    import cdeadmin_firebird_linger_ui_gate as linger
    from cdeadmin_firebird_logical_volumes_gate import docker, OWNER

lifecycle = linger.lifecycle
shared = lifecycle.shared
LABEL = 'Attachment page-cache policy'
PAGES = 'Requested attachment cache pages'
LABELS = {'SERVER_DEFAULT': 'Use server preference',
          'NATIVE_DEFAULT': 'Native default', 'CUSTOM': 'Request cache pages'}


def owned_profile(options):
    profiles = json.loads(options.profiles.read_text()).get('profiles', [])
    if len(profiles) != 1:
        raise ValueError('Expected one owned cache profile')
    profile = profiles[0]
    path = '/var/lib/firebird/data/owned_cache.fdb'
    container = profile.get('owned_container_id', '')
    if (profile.get('fixture_kind') != 'firebird-cache-qualification' or
            profile.get('engine') != 'firebird' or
            profile.get('host') != '127.0.0.1' or
            options.host != '127.0.0.1' or
            profile.get('port') != options.firebird_port or
            profile.get('user') != options.user or
            profile.get('database') != path or
            options.database != Path(path).name or
            options.database_root != str(Path(path).parent) or
            not re.fullmatch('[0-9a-f]{64}', container)):
        raise ValueError('Browser target is not the owned cache fixture')
    owner = docker('inspect', '--format',
                   '{{index .Config.Labels "cdeadmin-owned-gate"}}',
                   container).decode().strip()
    if owner != OWNER:
        raise ValueError('Cache fixture ownership differs')
    return profile


def observe(options, module, password, selected, profile, expected):
    if selected['database'] != profile['database']:
        raise ValueError('Saved target differs from owned fixture')
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
        raise ValueError('Saved route escaped owned fixture')
    args = lifecycle._route_arguments(route, module)
    config = module.driver_config.get_database(args['database'])
    if config.cache_size.value != expected:
        raise RuntimeError('Saved cache policy did not reach private DPB')
    with module.connect(password=password, **args) as handle:
        allocated = handle.info.page_cache_size
        stored = handle.info.get_info(module.DbInfoCode.SET_PAGE_BUFFERS)
        with handle.cursor() as cursor:
            cursor.execute('SELECT MON$PAGE_BUFFERS FROM MON$DATABASE')
            monitored = cursor.fetchone()[0]
        handle.rollback()
    if stored != 0 or allocated != (expected or 128) or monitored != allocated:
        raise RuntimeError('Cache allocation or stored override differs')
    return {'requested_pages': expected, 'allocated_pages': allocated,
            'monitor_pages': monitored, 'stored_pages': stored,
            'native_dpb_verified': True}


def fill(wait, policy, pages):
    shared.fill_fields(wait, [LABEL + '=' + LABELS[policy]])
    if policy == 'CUSTOM':
        shared.fill_fields(wait, [PAGES + '=' + str(pages)])


def capture(driver, wait, folder):
    scope = active_form(driver)
    proof = linger.capture(driver, wait, folder, label=LABEL, scope=scope)
    if shared.visible_named_control(scope, PAGES):
        proof['page_count'] = linger.capture(
            driver, wait, folder / 'page-count', label=PAGES, scope=scope)
    return proof


def active_form(driver):
    forms = [item for item in driver.find_elements(
        By.CSS_SELECTOR, '[role="dialog"]') if item.is_displayed()]
    if len(forms) != 1:
        raise RuntimeError('Expected one visible cache preference dialog')
    return forms[0]


def run(options):
    profile = owned_profile(options)
    password = os.environ.get(options.password_env)
    if not password:
        raise RuntimeError('Owned test credential missing')
    options.endpoint_password_env = options.password_env
    lifecycle._configure_shared(password)
    module = lifecycle._load_firebird(options)
    driver = lifecycle.create_driver(options)
    driver.set_script_timeout(max(120, options.timeout * 10))
    wait = WebDriverWait(driver, options.timeout)
    result = {'complete': False, 'cases': [], 'failures': [],
              'control_scope': 'single-visible-dialog',
              'target_setups': [], 'font_scale': options.font_scale}

    def target():
        return next(row for row in shared._target_rows(options.config_db)
                    if row['display_name'] == options.database)

    def failure(phase, error):
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
        except Exception as capture_error:
            record['capture_error_type'] = type(capture_error).__name__
        result['failures'].append(record)

    try:
        driver.get(options.url.rstrip('/') + '/browser/')
        shared._prepare_tree(driver, wait, options, options.database)
        options.database_forms = shared._catalog_forms(driver)
        for choice in LABELS:
            phase = 'target-' + choice
            try:
                shared._open_form(driver, wait, 'edit', options.database)
                fill(wait, choice, 512)
                shared._submit_target_form(driver, wait, 'edit', {})
                wait.until(lambda _driver: target()['configuration'].get(
                    'attachment_cache_policy') == choice)
                result['target_setups'].append({
                    'choice': choice, **capture(
                        driver, wait, options.output_root / phase)})
                shared._close(driver, wait)
            except Exception as error:
                failure(phase, error)
                continue
            for parent, pages in [('NATIVE_DEFAULT', None), ('CUSTOM', 128),
                                  ('CUSTOM', 256)]:
                phase = choice + '-parent-' + parent + '-' + str(pages)
                try:
                    shared._open_form(driver, wait, 'server_edit')
                    fill(wait, parent, pages)
                    wait.until(lambda value: shared.visible_named_control(
                        value, 'Save endpoint profile')).click()
                    wait.until(lambda value: any(
                        item.is_displayed() and
                        'Endpoint profile saved.' in item.text
                        for item in value.find_elements(
                            By.CSS_SELECTOR, '[role="alert"]')))
                    saved = json.loads(lifecycle._saved_route(
                        options, target()['target_id'])[0].configuration)
                    if (saved.get('attachment_cache_policy') != parent or
                            (parent == 'CUSTOM' and saved.get(
                                'attachment_cache_pages') != pages)):
                        raise RuntimeError('Server cache policy not saved')
                    server_proof = capture(
                        driver, wait,
                        options.output_root / ('server-' + phase))
                    shared._close(driver, wait)
                    shared._open_form(driver, wait, 'edit', options.database)
                    field = wait.until(lambda value:
                                       shared.visible_named_control(
                                           value, LABEL))
                    if ' '.join(field.text.split()) != LABELS[choice]:
                        raise RuntimeError('Reopened target policy differs')
                    control = shared.visible_named_control(
                        active_form(driver), PAGES)
                    if choice == 'CUSTOM':
                        if (not control or
                                control.get_attribute('value') != '512'):
                            raise RuntimeError('Target page count differs')
                    elif control:
                        raise RuntimeError('Inactive page count is visible')
                    proof = capture(driver, wait, options.output_root / phase)
                    selected = target()
                    if selected['configuration'].get(
                            'attachment_cache_policy') != choice:
                        raise RuntimeError('Parent edit rewrote target')
                    shared._close(driver, wait)
                    expected = (pages if choice == 'SERVER_DEFAULT' else
                                512 if choice == 'CUSTOM' else None)
                    result['cases'].append({
                        'choice': choice, 'parent': parent, 'pages': pages,
                        'server': server_proof, 'reopened': proof,
                        'native': observe(options, module, password,
                                          selected, profile, expected)})
                except Exception as error:
                    failure(phase, error)
                    try:
                        shared._close(driver, wait)
                    except Exception as cleanup_error:
                        failure(phase + '-dialog-cleanup', cleanup_error)
    except Exception as error:
        failure('setup', error)
    finally:
        try:
            driver.quit()
        except Exception as error:
            failure('browser-cleanup', error)
    result['complete'] = (len(result['cases']) == 9 and
                          len(result['target_setups']) == 3 and
                          not result['failures'])
    return result


def main():
    options = linger.arguments()
    result = run(options)
    options.summary_output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'cases': len(result['cases']),
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
