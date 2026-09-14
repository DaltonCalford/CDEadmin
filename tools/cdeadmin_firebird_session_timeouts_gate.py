#!/usr/bin/env python3
"""Verify Firebird timeout settings and expiry with read-only queries."""

import argparse
import json
import time
from pathlib import Path
from types import SimpleNamespace

from cdeadmin_firebird_admin_mapping_gate import _create_client
from pgadmin.cdeadmin.providers.firebird.session_settings import (
    initialize_timeouts,
)
from pgadmin.cdeadmin.security.secrets import SecretLease


def run(profiles):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    password = route.pop('password')
    route.update(credential_reference_id='timeout-verification-secret',
                 principal_reference='timeout-verification-principal')
    result = {'complete': False, 'cases': [], 'failures': [],
              'read_only_queries': True, 'credential_values_exported': False}

    def rows(connection, sql):
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchall()

    for statement, idle in ((1234, 60), (2147483647, 2147483647), (0, 0)):
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))
        try:
            handle = client.open_session({'route': {
                **route, 'statement_timeout_ms': statement,
                'session_idle_timeout_seconds': idle}})
            assert not handle.main_transaction.is_active()
            identity = client.runtime_identity({}, handle)
            assert identity['version'] == '5.0.4'
            assert not handle.main_transaction.is_active()
            assert handle._att.get_statement_timeout() == statement
            assert handle._att.get_idle_timeout() == idle
            values = rows(handle,
                          "SELECT RDB$GET_CONTEXT('SYSTEM', "
                          "'STATEMENT_TIMEOUT'), RDB$GET_CONTEXT('SYSTEM', "
                          "'SESSION_IDLE_TIMEOUT') FROM RDB$DATABASE")[0]
            assert tuple(map(int, values)) == (statement, idle)
            transaction_id = handle.main_transaction.info.id
            client.runtime_identity({}, handle)
            assert handle.main_transaction.info.id == transaction_id
            initialize_timeouts(handle, {'statement_timeout_ms': 2000,
                                         'session_idle_timeout_seconds': 90},
                                driver)
            assert handle.main_transaction.info.id == transaction_id
            assert handle._att.get_statement_timeout() == 2000
            assert handle._att.get_idle_timeout() == 90
            client.close_session(handle)
            result['cases'].append({'statement_ms': statement,
                                    'idle_seconds': idle,
                                    'caller_transaction_preserved': True})
        except Exception as exc:
            result['failures'].append({'case': 'settings',
                                       'statement_ms': statement,
                                       'error_type': type(exc).__name__})
        finally:
            client.close()

    client = _create_client(SimpleNamespace(
        acquire_secret=lambda *_args: SecretLease(password)))
    try:
        handle = client.open_session({'route': route})
        initialize_timeouts(handle, {'statement_timeout_ms': 25}, driver)
        rows(handle, 'SELECT 1 FROM RDB$DATABASE')
        transaction_id = handle.main_transaction.info.id
        try:
            rows(handle, 'SELECT COUNT(*) FROM RDB$TYPES A '
                 'CROSS JOIN RDB$TYPES B CROSS JOIN RDB$TYPES C '
                 'CROSS JOIN RDB$TYPES D')
        except driver.DatabaseError as exc:
            codes = list(exc.gds_codes)
            assert 335544794 in codes and 335545128 in codes
        else:
            raise AssertionError('Expected native statement timeout')
        assert handle.main_transaction.info.id == transaction_id
        initialize_timeouts(handle, {'statement_timeout_ms': 0}, driver)
        assert rows(handle, 'SELECT 1 FROM RDB$DATABASE') == [(1,)]
        client.close_session(handle)
        result['cases'].append({'statement_expiry_codes': codes,
                                'same_transaction_usable_after_timeout': True})
    except Exception as exc:
        result['failures'].append({'case': 'statement-expiry',
                                   'error_type': type(exc).__name__})
    finally:
        client.close()

    client = _create_client(SimpleNamespace(
        acquire_secret=lambda *_args: SecretLease(password)))
    try:
        handle = client.open_session({'route': {
            **route, 'session_idle_timeout_seconds': 1}})
        rows(handle, 'SELECT 1 FROM RDB$DATABASE')
        time.sleep(3)
        try:
            rows(handle, 'SELECT 1 FROM RDB$DATABASE')
        except driver.DatabaseError as exc:
            codes = list(exc.gds_codes)
            assert 335545131 in codes
        else:
            raise AssertionError('Expected native idle expiry')
        result['cases'].append({'idle_expiry_codes': codes,
                                'automatic_reconnection_attempted': False})
    except Exception as exc:
        result['failures'].append({'case': 'idle-expiry',
                                   'error_type': type(exc).__name__})
    finally:
        client.close()
    result['complete'] = len(result['cases']) == 5 and not result['failures']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.profiles)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
