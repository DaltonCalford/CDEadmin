#!/usr/bin/env python3
"""Persist/reopen worker preferences and observe only the owned fixture."""

import json
from types import SimpleNamespace

if __package__:
    from . import cdeadmin_firebird_cache_ui_gate as cache
else:
    import cdeadmin_firebird_cache_ui_gate as cache

lifecycle = cache.lifecycle
shared = cache.shared
LABEL = 'Initial parallel-worker policy'
COUNT = 'Requested parallel workers'
LABELS = {'SERVER_DEFAULT': 'Use server preference',
          'NATIVE_DEFAULT': 'Native default',
          'CUSTOM': 'Request parallel workers'}


def fill(wait, policy, count):
    shared.fill_fields(wait, [LABEL + '=' + LABELS[policy]],
                       control_root=cache.active_form)
    if policy == 'CUSTOM':
        shared.fill_fields(wait, [COUNT + '=' + str(count)],
                           control_root=cache.active_form)


def capture(driver, wait, folder):
    scope = cache.active_form(driver)
    wait.until(lambda _: shared.visible_named_control(scope, LABEL))
    proof = cache.linger.capture(
        driver, wait, folder, label=LABEL, scope=scope)
    if shared.visible_named_control(scope, COUNT):
        proof['requested_count'] = cache.linger.capture(
            driver, wait, folder / 'count', label=COUNT, scope=scope)
    return proof


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
    private = module.driver_config.get_database(args['database'])
    if private.parallel_workers.value != expected:
        raise RuntimeError('Saved worker preference did not reach DPB')
    with module.connect(password=password, **args) as handle:
        with handle.cursor() as cursor:
            cursor.execute('SELECT RDB$CONFIG_NAME, RDB$CONFIG_VALUE '
                           'FROM RDB$CONFIG WHERE RDB$CONFIG_NAME IN '
                           "('ParallelWorkers', 'MaxParallelWorkers')")
            config = {name.strip(): int(value)
                      for name, value in cursor.fetchall()}
        effective = (config['ParallelWorkers'] if expected is None else
                     min(expected, config['MaxParallelWorkers']))
        observations = []
        for stage in ('initial', 'rollback', 'reset'):
            if stage != 'initial':
                handle.rollback()
            if stage == 'reset':
                handle.execute_immediate('ALTER SESSION RESET')
            with handle.cursor() as cursor:
                cursor.execute("SELECT RDB$GET_CONTEXT('SYSTEM', "
                               "'PARALLEL_WORKERS') FROM RDB$DATABASE")
                observations.append(int(cursor.fetchone()[0]))
        handle.rollback()
    if observations != [effective] * 3:
        raise RuntimeError('Effective worker preference differs')
    return {'requested_workers': expected, 'server_configuration': config,
            'effective_workers': observations, 'native_dpb_verified': True,
            'actual_task_worker_counts_qualified': False}


def run(options):
    return cache.run(options, preferences=SimpleNamespace(
        policy_key='parallel_workers_policy', value_key='parallel_workers',
        label=LABEL, value_label=COUNT, labels=LABELS, custom_value=32767,
        parent_values=[('NATIVE_DEFAULT', None), ('CUSTOM', 0), ('CUSTOM', 2)],
        fill=fill, capture=capture, observe=observe,
        record_key='requested_workers'))


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
