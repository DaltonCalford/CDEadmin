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
    from .cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER,
        _configure_client_library,
    )
else:
    from cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER,
        _configure_client_library,
    )

from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes


ROUND_RESULTS = {
    'CEILING': ('1.3', '-1.2'), 'UP': ('1.3', '-1.3'),
    'HALF_UP': ('1.3', '-1.3'), 'HALF_EVEN': ('1.2', '-1.2'),
    'HALF_DOWN': ('1.2', '-1.2'), 'DOWN': ('1.2', '-1.2'),
    'FLOOR': ('1.2', '-1.3'), 'REROUND': ('1.2', '-1.2'),
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


def run(image):
    import firebird.driver as native
    _configure_client_library(native)
    password = secrets.token_urlsafe(24)
    database = '/var/lib/firebird/data/owned_decfloat.fdb'
    container = None
    result = {'complete': False, 'checks': [], 'failures': [],
              'provider_forms_qualified': False,
              'owned_container_removed': False}

    def failure(case, error):
        result['failures'].append({
            'case': case, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error))})

    def connect(rounding=None, traps=None):
        name = 'owned_decfloat_' + uuid.uuid4().hex
        config = native.driver_config.register_database(name)
        config.dsn.value = dsn
        config.user.value = None
        config.password.value = None
        config.charset.value = 'UTF8'
        config.decfloat_round.value = rounding
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
        for mode, expected in ROUND_RESULTS.items():
            phase = 'round-' + mode
            try:
                with connect(native.DecfloatRound[mode]) as handle:
                    with handle.cursor() as cursor:
                        cursor.execute(
                            "SELECT QUANTIZE(CAST('1.25' AS DECFLOAT(16)), "
                            "CAST('0.1' AS DECFLOAT(16))), "
                            "QUANTIZE(CAST('-1.25' AS DECFLOAT(16)), "
                            "CAST('0.1' AS DECFLOAT(16))) FROM RDB$DATABASE")
                        observed = tuple(str(value)
                                         for value in cursor.fetchone())
                    assert observed == expected
                result['checks'].append({'case': phase, 'values': observed})
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
    except Exception as error:
        failure(phase, error)
    finally:
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                failure('remove-owned-server', error)
    result['complete'] = (len(result['checks']) == 45 and
                          not result['failures'] and
                          result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        parser.error('Use a new evidence file')
    result = run(options.image)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
