#!/usr/bin/env python3
"""Disable traps through retained query execution, preserving caller work."""
import argparse
import json
from pathlib import Path
import time

if __package__:
    from . import cdeadmin_firebird_views_gate as base
    from .cdeadmin_firebird_traps_oracle import observe_traps, DEFAULT_TRAPS
else:
    import cdeadmin_firebird_views_gate as base
    from cdeadmin_firebird_traps_oracle import observe_traps, DEFAULT_TRAPS

INSPECT = ("SELECT RDB$GET_CONTEXT('SYSTEM', 'DECFLOAT_TRAPS') "
           'AS DECFLOAT_TRAPS FROM RDB$DATABASE')
DISABLE = 'SET DECFLOAT TRAPS TO'


def verify(connection, client, route, password, result):
    from pgadmin.cdeadmin.providers.firebird.decfloat_traps import TRAP_FIELDS
    checks = result['session_traps_checks'] = []

    def sql(source, params=()):
        with connection.cursor() as cursor:
            cursor.execute(source, params)
            return cursor.fetchall() if cursor.description else []

    def execute(handle, source):
        token = client.submit_query(handle, {
            'source': source, 'parameters': []})
        deadline = time.monotonic() + 20
        while True:
            value = client.describe_result(token)
            if value['complete']:
                assert value['payload']['execution_state'] == 'succeeded', (
                    value)
                return value['payload']['rows']
            if time.monotonic() > deadline:
                raise AssertionError('Retained-session query did not finish')
            time.sleep(0.01)

    sql('CREATE TABLE TRAP_SESSION_WORK (ID INTEGER)')
    connection.commit()
    for mask in range(32):
        flags = {key: bool(mask & (1 << index))
                 for index, key in enumerate(TRAP_FIELDS)}
        configured = {**route, **flags,
                      'decfloat_traps_policy': 'CUSTOM' if mask else
                      'NATIVE_DEFAULT'}
        expected_reset = sorted(
            [name for key, name in TRAP_FIELDS.items() if flags[key]]
            if mask else DEFAULT_TRAPS)
        handle = client.open_session({'route': configured})
        try:
            assert observe_traps(handle) == expected_reset
            if not mask:
                execute(handle, DISABLE)
            execute(handle, 'INSERT INTO TRAP_SESSION_WORK VALUES (' +
                    str(mask) + ')')
            before = handle.main_transaction.info.id
            execute(handle, DISABLE)
            assert handle.main_transaction.info.id == before
            observed = execute(handle, INSPECT)
            assert observed == [['None']] or observed == [('None',)], observed
            assert observe_traps(handle) == []
            assert sql('SELECT ID FROM TRAP_SESSION_WORK WHERE ID = ?',
                       (mask,)) == []
            connection.rollback()
            client.control_transaction(handle, 'rollback')
            assert observe_traps(handle) == []
            execute(handle, 'INSERT INTO TRAP_SESSION_WORK VALUES (' +
                    str(mask) + ')')
            client.control_transaction(handle, 'commit')
            assert observe_traps(handle) == []
            assert sql('SELECT ID FROM TRAP_SESSION_WORK WHERE ID = ?',
                       (mask,)) == [(mask,)]
            connection.rollback()
            client.control_transaction(handle, 'rollback')
            execute(handle, 'ALTER SESSION RESET')
            assert observe_traps(handle) == expected_reset
            checks.append({'mask': mask,
                           'existing_transaction_preserved': True,
                           'commit_rollback_do_not_restore_traps': True,
                           'reset_restores_attachment_settings': True,
                           'explicit_readback': 'None'})
        finally:
            client.close_session(handle)
        reopened = client.open_session({'route': configured})
        try:
            assert observe_traps(reopened) == expected_reset
            checks[-1]['reopen_uses_preferences'] = True
        finally:
            client.close_session(reopened)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite evidence')
    result = base.run(extra_checks=verify)
    result['complete'] = (result['complete'] and
                          len(result.get('session_traps_checks', [])) == 32)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
