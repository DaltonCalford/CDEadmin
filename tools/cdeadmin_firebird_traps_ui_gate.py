#!/usr/bin/env python3
"""Save/reopen initial trap preferences against a labelled owned fixture."""

import json
import os
import traceback
from pathlib import Path
from types import SimpleNamespace

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

if __package__:
    from . import cdeadmin_firebird_cache_ui_gate as cache
else:
    import cdeadmin_firebird_cache_ui_gate as cache
from tools.cdeadmin_firebird_traps_oracle import DEFAULT_TRAPS, observe_traps
from pgadmin.cdeadmin.providers.firebird.decfloat_traps import TRAP_FIELDS


lifecycle = cache.lifecycle
shared = cache.shared
LABEL = 'Initial DECFLOAT trap policy'
LABELS = {'SERVER_DEFAULT': 'Use server preference',
          'NATIVE_DEFAULT': 'Native default', 'CUSTOM': 'Custom initial traps'}
TRAP_LABELS = ('Trap division by zero', 'Trap inexact results',
               'Trap invalid operations', 'Trap overflow', 'Trap underflow')
TARGET_TRAPS = ('INEXACT', 'UNDERFLOW')
PARENTS = (('NATIVE_DEFAULT', ()), ('CUSTOM', DEFAULT_TRAPS),
           ('CUSTOM', tuple(TRAP_FIELDS.values())))


def fill(wait, policy, selected=()):
    shared.fill_fields(wait, [LABEL + '=' + LABELS[policy]],
                       control_root=cache.active_form)
    if policy == 'CUSTOM':
        shared.fill_fields(wait, [
            label + '=' + str(name in selected).lower()
            for label, name in zip(TRAP_LABELS, TRAP_FIELDS.values())
        ], control_root=cache.active_form)


def capture(driver, wait, folder, policy, selected=()):
    scope = cache.active_form(driver)
    control = wait.until(lambda _: shared.visible_named_control(scope, LABEL))
    if control is None or ' '.join(control.text.split()) != LABELS[policy]:
        raise RuntimeError('Displayed trap policy differs')
    proof = cache.linger.capture(
        driver, wait, folder, label=LABEL, scope=scope)
    proof['selections'] = {}
    for label, name in zip(TRAP_LABELS, TRAP_FIELDS.values()):
        control = shared.visible_named_control(scope, label)
        if policy != 'CUSTOM':
            if control is not None:
                raise RuntimeError('Inactive trap selection is visible')
            continue
        if control is None or control.is_selected() != (name in selected):
            raise RuntimeError('Displayed trap selection differs')
        proof['selections'][name] = cache.linger.capture(
            driver, wait, folder / name.lower(), label=label, scope=scope)
    return proof


def observe(options, module, password, selected, profile, expected):
    if selected['database'] != profile['database']:
        raise ValueError('Saved target differs from owned fixture')
    routes = lifecycle._saved_route(options, selected['target_id'])
    service = lifecycle.EndpointService(SimpleNamespace(), SimpleNamespace(
        secrets=SimpleNamespace(register_resolver=lambda *_args: None)))
    route, _ = service._route_and_reference(
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
    configured = module.driver_config.get_database(args['database'])
    requested = configured.decfloat_traps.value
    names = None if requested is None else sorted(t.name for t in requested)
    if names != (None if expected is None else sorted(expected)):
        raise RuntimeError('Saved trap policy did not reach private DPB')
    expected_native = sorted(DEFAULT_TRAPS if expected is None else expected)
    with module.connect(password=password, **args) as handle:
        initial = observe_traps(handle)
        if initial != expected_native:
            raise RuntimeError('Native initial trap state differs')
        handle.rollback()
        handle.execute_immediate('SET DECFLOAT TRAPS TO')
        disabled = observe_traps(handle)
        assert disabled == []
        handle.rollback()
        handle.execute_immediate('ALTER SESSION RESET')
        reset = observe_traps(handle)
        assert reset == initial
        handle.rollback()
    return {'initial': initial, 'explicit_empty_sql': disabled, 'reset': reset,
            'native_dpb_verified': True}


def run(options):
    profile = cache.owned_profile(options)
    password = os.environ.get(options.password_env)
    if not password:
        raise RuntimeError('Owned test credential missing')
    options.endpoint_password_env = options.password_env
    lifecycle._configure_shared(password)
    module = lifecycle._load_firebird(options)
    driver = lifecycle.create_driver(options)
    driver.set_script_timeout(max(120, options.timeout * 10))
    wait = WebDriverWait(driver, options.timeout)
    result = {'complete': False, 'cases': [], 'target_setups': [],
              'failures': [], 'font_scale': options.font_scale,
              'control_scope': 'single-visible-dialog'}

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
                fill(wait, choice, TARGET_TRAPS)
                shared._submit_target_form(driver, wait, 'edit', {})
                wait.until(lambda _: target()['configuration'].get(
                    'decfloat_traps_policy') == choice)
                result['target_setups'].append({
                    'choice': choice, **capture(driver, wait,
                                               options.output_root / phase,
                                               choice, TARGET_TRAPS)})
                shared._close(driver, wait)
            except Exception as error:
                failure(phase, error)
                continue
            for index, (parent, selections) in enumerate(PARENTS):
                phase = choice + '-parent-' + str(index)
                try:
                    shared._open_form(driver, wait, 'server_edit')
                    fill(wait, parent, selections)
                    wait.until(lambda value: shared.visible_named_control(
                        value, 'Save endpoint profile')).click()
                    wait.until(lambda value: any(
                        item.is_displayed() and
                        'Endpoint profile saved.' in item.text
                        for item in value.find_elements(
                            By.CSS_SELECTOR, '[role="alert"]')))
                    server = capture(driver, wait,
                                     options.output_root / ('server-' + phase),
                                     parent, selections)
                    shared._close(driver, wait)
                    shared._open_form(driver, wait, 'edit', options.database)
                    reopened = capture(driver, wait,
                                       options.output_root / phase,
                                       choice, TARGET_TRAPS)
                    selected = target()
                    assert selected['configuration'].get(
                        'decfloat_traps_policy') == choice
                    shared._close(driver, wait)
                    expected = (selections if parent == 'CUSTOM' else None)
                    if choice != 'SERVER_DEFAULT':
                        expected = TARGET_TRAPS if choice == 'CUSTOM' else None
                    result['cases'].append({
                        'choice': choice, 'parent': parent,
                        'parent_selections': selections,
                        'server': server, 'reopened': reopened,
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
    options = cache.linger.arguments()
    result = run(options)
    options.summary_output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'cases': len(result['cases']),
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
