#!/usr/bin/env python3
"""Observe native DECFLOAT attachment/reset semantics on an owned server.

This is a native baseline, not provider form qualification.
"""

import argparse
import itertools
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path

if __package__:
    from .cdeadmin_firebird_rounding_oracle import (
        expected_rounding, observe_rounding,
    )
    from .cdeadmin_firebird_external_functions_gate import browser_checks
    from .cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER,
        _configure_client_library, _route_arguments,
    )
else:
    from cdeadmin_firebird_rounding_oracle import (
        expected_rounding, observe_rounding,
    )
    from cdeadmin_firebird_external_functions_gate import browser_checks
    from cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER,
        _configure_client_library, _route_arguments,
    )

from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.providers.firebird.provider import (
    _initialize_connection, _database_create_arguments,
)


ROUND_RESULTS = {
    'CEILING': ('1.3', '-1.2'), 'UP': ('1.3', '-1.3'),
    'HALF_UP': ('1.3', '-1.3'), 'HALF_EVEN': ('1.2', '-1.2'),
    'HALF_DOWN': ('1.2', '-1.2'), 'DOWN': ('1.2', '-1.2'),
    'FLOOR': ('1.2', '-1.3'), 'REROUND': ('1.2', '-1.2'),
}
# Firebird DecimalStatus initializes HALF_UP, not Python's HALF_EVEN.
CONNECTION_ROUND_RESULTS = {
    **ROUND_RESULTS, 'NATIVE_DEFAULT': ROUND_RESULTS['HALF_UP'],
}

TRAP_PROBES = {
    'DIVISION_BY_ZERO': (
        'CAST(1 AS DECFLOAT(16)) / CAST(0 AS DECFLOAT(16))', 335545139),
    'INEXACT': (
        'CAST(1 AS DECFLOAT(16)) / CAST(3 AS DECFLOAT(16))', 335545140),
    'INVALID_OPERATION': (
        'CAST(0 AS DECFLOAT(16)) / CAST(0 AS DECFLOAT(16))', 335545141),
    'OVERFLOW': ("CAST('1e9999' AS DECFLOAT(16))", 335545142),
    'UNDERFLOW': ("CAST('1e-9999' AS DECFLOAT(16))", 335545143),
}


def run(image, provider_rounding=False, browser_options=None):
    import firebird.driver as native
    _configure_client_library(native)
    password = secrets.token_urlsafe(24)
    database = '/var/lib/firebird/data/owned_decfloat.fdb'
    container = None
    result = {'complete': False, 'checks': [], 'failures': [],
              'provider_forms_qualified': False,
              'provider_rounding_mapping': provider_rounding,
              'owned_container_removed': False}

    def failure(case, error):
        result['failures'].append({
            'case': case, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error))})

    def connect(rounding=None, traps=None):
        if provider_rounding and rounding is not None:
            selected = {**route, 'decfloat_round': getattr(
                rounding, 'name', rounding)}
            handle = native.connect(password=password,
                                    **_route_arguments(selected, native))
            try:
                _initialize_connection(handle, selected, native)
            except Exception:
                handle.close()
                raise
            return handle
        name = 'owned_decfloat_' + uuid.uuid4().hex
        config = native.driver_config.register_database(name)
        config.dsn.value = dsn
        config.user.value = None
        config.password.value = None
        config.charset.value = 'UTF8'
        config.decfloat_round.value = (
            None if rounding == 'NATIVE_DEFAULT' else rounding)
        config.decfloat_traps.value = traps
        return native.connect(name, user='SYSDBA', password=password)

    def observe(connection, expression):
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT ' + expression + ' FROM RDB$DATABASE')
                return str(cursor.fetchone()[0])
        except native.DatabaseError as error:
            codes = list(status_codes(error))
            assert codes, 'Native error must have a bounded status code'
            return {'native_status_codes': codes}

    def divide(connection):
        return observe(connection, TRAP_PROBES['DIVISION_BY_ZERO'][0])

    phase = 'create-owned-server'
    try:
        container = docker(
            'run', '--detach', '--name',
            'cdeadmin-decfloat-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--publish', '127.0.0.1::3050', '--env', 'FIREBIRD_ROOT_PASSWORD',
            '--env', 'FIREBIRD_DATABASE', image,
            env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                     FIREBIRD_DATABASE=database)).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise ValueError('Owned container identity is invalid')
        dsn = f'127.0.0.1/{published_port(container)}:{database}'
        route = {'host': '127.0.0.1', 'port': published_port(container),
                 'database': database, 'user': 'SYSDBA',
                 'auth_plugin_list': 'Srp256'}
        phase = 'readiness'
        deadline = time.monotonic() + 45
        while True:
            try:
                with connect() as handle:
                    with handle.cursor() as cursor:
                        cursor.execute("SELECT RDB$GET_CONTEXT('SYSTEM', "
                                       "'ENGINE_VERSION') FROM RDB$DATABASE")
                        result['engine_version'] = cursor.fetchone()[0]
                break
            except native.Error:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        assert result['engine_version'] == '5.0.4'
        for mode, expected in CONNECTION_ROUND_RESULTS.items():
            phase = 'round-' + mode
            try:
                selected_rounding = (
                    mode if mode == 'NATIVE_DEFAULT'
                    else native.DecfloatRound[mode])
                with connect(selected_rounding) as handle:
                    expression = (
                            "SELECT QUANTIZE(CAST('1.25' AS DECFLOAT(16)), "
                            "CAST('0.1' AS DECFLOAT(16))), "
                            "QUANTIZE(CAST('-1.25' AS DECFLOAT(16)), "
                            "CAST('0.1' AS DECFLOAT(16))) FROM RDB$DATABASE")
                    with handle.cursor() as cursor:
                        cursor.execute(expression)
                        observed = tuple(str(value)
                                         for value in cursor.fetchone())
                    assert observed == expected
                    initial_mode = observe_rounding(handle)
                    assert initial_mode == expected_rounding(mode)
                    handle.rollback()
                    changed_mode = 'UP' if mode != 'UP' else 'DOWN'
                    handle.execute_immediate(
                        'SET DECFLOAT ROUND ' + changed_mode)
                    with handle.cursor() as cursor:
                        cursor.execute(expression)
                        changed = tuple(str(v) for v in cursor.fetchone())
                    assert changed == ROUND_RESULTS[changed_mode]
                    changed_observation = observe_rounding(handle)
                    assert changed_observation == expected_rounding(
                        changed_mode)
                    handle.rollback()
                    handle.execute_immediate('ALTER SESSION RESET')
                    with handle.cursor() as cursor:
                        cursor.execute(expression)
                        reset = tuple(str(v) for v in cursor.fetchone())
                    assert reset == expected
                    reset_observation = observe_rounding(handle)
                    assert reset_observation == expected_rounding(mode)
                with connect(selected_rounding) as reopened:
                    with reopened.cursor() as cursor:
                        cursor.execute(expression)
                        restored = tuple(str(v) for v in cursor.fetchone())
                    assert restored == expected
                    reopened_observation = observe_rounding(reopened)
                    assert reopened_observation == expected_rounding(mode)
                result['checks'].append({
                    'case': phase, 'values': observed, 'changed': changed,
                    'reset': reset, 'reopened': restored,
                    'distinguishing_observations': {
                        'initial': initial_mode,
                        'changed': changed_observation,
                        'reset': reset_observation,
                        'reopened': reopened_observation}})
            except Exception as error:
                failure(phase, error)
        traps = list(native.DecfloatTraps)
        for count in range(len(traps) + 1):
            for selected in itertools.combinations(traps, count):
                phase = 'traps-' + ('-'.join(t.name for t in selected)
                                    or 'empty')
                try:
                    with connect(traps=list(selected)) as handle:
                        before = divide(handle)
                        should_trap = (not selected or
                                       native.DecfloatTraps.DIVISION_BY_ZERO
                                       in selected)
                        assert isinstance(before, dict) is should_trap
                        if not should_trap:
                            assert before == 'Infinity'
                        handle.rollback()
                        handle.execute_immediate('SET DECFLOAT TRAPS TO')
                        after = divide(handle)
                        assert after == 'Infinity'
                        handle.rollback()
                        handle.execute_immediate('ALTER SESSION RESET')
                        reset = divide(handle)
                        assert reset == before
                    result['checks'].append({
                        'case': phase, 'attachment': before,
                        'explicit_no_traps': after, 'session_reset': reset})
                except Exception as error:
                    failure(phase, error)
        for name, (expression, code) in TRAP_PROBES.items():
            phase = 'condition-' + name
            try:
                with connect(traps=[native.DecfloatTraps[name]]) as handle:
                    trapped = observe(handle, expression)
                    assert trapped == {'native_status_codes': [code]}
                    handle.rollback()
                    handle.execute_immediate('SET DECFLOAT TRAPS TO')
                    untrapped = observe(handle, expression)
                    assert isinstance(untrapped, str)
                    handle.rollback()
                    handle.execute_immediate('ALTER SESSION RESET')
                    reset = observe(handle, expression)
                    assert reset == trapped
                result['checks'].append({
                    'case': phase, 'attachment': trapped,
                    'explicit_no_traps': untrapped, 'session_reset': reset})
            except Exception as error:
                failure(phase, error)
        if provider_rounding:
            for mode, expected in CONNECTION_ROUND_RESULTS.items():
                phase = 'create-round-' + mode
                try:
                    path = ('/var/lib/firebird/data/owned_decfloat_create_' +
                            mode.lower() + '.fdb')
                    target_dsn = f'127.0.0.1/{route["port"]}:{path}'
                    selected = {**route, 'database': path,
                                'decfloat_round': mode}
                    arguments = _database_create_arguments(
                        selected, target_dsn, {}, native)
                    expression = (
                        "SELECT QUANTIZE(CAST('1.25' AS DECFLOAT(16)), "
                        "CAST('0.1' AS DECFLOAT(16))), "
                        "QUANTIZE(CAST('-1.25' AS DECFLOAT(16)), "
                        "CAST('0.1' AS DECFLOAT(16))) FROM RDB$DATABASE")
                    with native.create_database(
                            password=password, **arguments) as created:
                        _initialize_connection(created, selected, native)
                        with created.cursor() as cursor:
                            cursor.execute(expression)
                            observed = tuple(str(v) for v in cursor.fetchone())
                        assert observed == expected
                        creation_observation = observe_rounding(created)
                        assert creation_observation == expected_rounding(mode)
                    with native.connect(
                            target_dsn, user='SYSDBA', password=password,
                            charset='UTF8') as unconfigured:
                        with unconfigured.cursor() as cursor:
                            cursor.execute(expression)
                            default = tuple(str(v) for v in cursor.fetchone())
                        assert default == CONNECTION_ROUND_RESULTS[
                            'NATIVE_DEFAULT']
                        default_observation = observe_rounding(unconfigured)
                        assert default_observation == expected_rounding(
                            'NATIVE_DEFAULT')
                    result['checks'].append({
                        'case': phase, 'creation_attachment': observed,
                        'unconfigured_attachment': default,
                        'creation_observation': creation_observation,
                        'unconfigured_observation': default_observation,
                        'stored_database_setting_changed': False})
                except Exception as error:
                    failure(phase, error)
        for mode in CONNECTION_ROUND_RESULTS:
            phase = 'round-session-isolation-' + mode
            try:
                selected = (mode if mode == 'NATIVE_DEFAULT'
                            else native.DecfloatRound[mode])
                with connect(selected) as changing, connect(selected) as peer:
                    expected_mode = expected_rounding(mode)
                    assert observe_rounding(changing) == expected_mode
                    before = observe_rounding(peer)
                    assert before == expected_rounding(mode)
                    alternate = 'UP' if mode != 'UP' else 'DOWN'
                    changing.execute_immediate(
                        'SET DECFLOAT ROUND ' + alternate)
                    assert observe_rounding(changing) == expected_rounding(
                        alternate)
                    assert observe_rounding(peer) == before
                    changing.rollback()
                    changing.execute_immediate('ALTER SESSION RESET')
                    assert observe_rounding(changing) == expected_mode
                    assert observe_rounding(peer) == before
                result['checks'].append({
                    'case': phase, 'simultaneous_attachments': 2,
                    'peer_unchanged': True, 'reset_restored_mode': True})
            except Exception as error:
                failure(phase, error)
        if browser_options is not None:
            browser_scope = getattr(browser_options, 'browser_scope', 'full')
            result['browser_scope'] = browser_scope
            result['browser_checks'] = browser_checks(
                browser_options, route, password, container,
                browser_options.build_root, gate_kind=(
                    'lifecycle' if browser_scope == 'full' else
                    'rounding-inheritance'),
                fixture_kind='firebird-decfloat-qualification')
            if not result['browser_checks'] or not all(
                    item['passed'] for item in result['browser_checks']):
                failure('browser-qualification',
                        RuntimeError('Lifecycle browser gate failed'))
    except Exception as error:
        failure(phase, error)
    finally:
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                failure('remove-owned-server', error)
    expected_count = 64 if provider_rounding else 55
    result['complete'] = (len(result['checks']) == expected_count and
                          not result['failures'] and
                          result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--provider-rounding', action='store_true')
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--browser-scope', choices=('full', 'inheritance'),
                        default='full')
    parser.add_argument('--build-root', type=Path)
    parser.add_argument('--source-config-db', type=Path,
                        default=Path('/var/lib/cdeadmin/cdeadmin.db'))
    parser.add_argument('--desktop-user', default='dalton.calford@gmail.com')
    parser.add_argument('--font-scale', type=int, action='append',
                        choices=(100, 200, 300))
    options = parser.parse_args()
    if options.output.exists():
        parser.error('Use a new evidence file')
    if options.browser:
        if options.build_root is None or options.build_root.exists():
            parser.error('Browser tests require a new --build-root directory')
        options.build_root.mkdir(parents=True, exist_ok=False)
    result = run(options.image, options.provider_rounding,
                 options if options.browser else None)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
