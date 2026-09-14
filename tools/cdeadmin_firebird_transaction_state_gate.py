#!/usr/bin/env python3
"""Compare Firebird transaction information with its live monitoring tables."""

import argparse
import importlib.metadata
import json
from pathlib import Path
from types import SimpleNamespace

from cdeadmin_firebird_admin_mapping_gate import (
    _create_client, _route_arguments, RelationalClientError,
)
from pgadmin.cdeadmin.security.secrets import SecretLease


def run(profiles):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    password = route.pop('password')
    route.update(credential_reference_id='transaction-state-secret',
                 principal_reference='transaction-state-principal')
    result = {'complete': False, 'cases': [], 'failures': [],
              'driver_version': importlib.metadata.version('firebird-driver'),
              'read_only_queries': True, 'credential_values_exported': False}
    for isolation in ('SERIALIZABLE', 'SNAPSHOT', 'READ_COMMITTED',
                      'READ_COMMITTED_NO_RECORD_VERSION',
                      'READ_COMMITTED_RECORD_VERSION',
                      'READ_COMMITTED_READ_CONSISTENCY'):
        for access in ('READ', 'WRITE'):
            for timeout in (-1, 0, 3, 32767):
                case = {'requested_isolation': isolation, 'access': access,
                        'lock_timeout': timeout}
                client = _create_client(SimpleNamespace(
                    acquire_secret=lambda *_args: SecretLease(password)))
                handle = None
                try:
                    handle = client.open_session({'route': {
                        **route, 'transaction_isolation': isolation,
                        'transaction_access': access,
                        'transaction_lock_timeout': timeout}})
                    assert client.describe_transaction(handle)[
                        'state'] == 'idle'
                    assert not handle.main_transaction.is_active()
                    with handle.cursor() as cursor:
                        cursor.execute(
                            'SELECT CURRENT_TRANSACTION, MON$READ_ONLY, '
                            'MON$LOCK_TIMEOUT, MON$ISOLATION_MODE '
                            'FROM MON$TRANSACTIONS WHERE '
                            'MON$TRANSACTION_ID = CURRENT_TRANSACTION')
                        row = cursor.fetchone()
                    observation = client.describe_transaction(handle)
                    assert observation['state'] == 'active'
                    fields = observation['fields']
                    assert all(item['available'] for item in fields.values())
                    assert fields['transaction_id']['value'] == row[0]
                    assert fields['read_only']['value'] == bool(row[1])
                    assert fields['read_only']['value'] == (access == 'READ')
                    assert fields['lock_timeout_seconds']['value'] == row[2]
                    assert row[2] == timeout
                    modes = {
                        0: 'SERIALIZABLE', 1: 'SNAPSHOT',
                        2: 'READ_COMMITTED_RECORD_VERSION',
                        3: 'READ_COMMITTED_NO_RECORD_VERSION',
                        4: 'READ_COMMITTED_READ_CONSISTENCY'}
                    assert fields['isolation']['value'] == modes[row[3]]
                    case['observed_isolation'] = fields['isolation']['value']
                    for action in ('commit', 'rollback'):
                        if not handle.main_transaction.is_active():
                            with handle.cursor() as cursor:
                                cursor.execute('SELECT 1 FROM RDB$DATABASE')
                                cursor.fetchone()
                        client.control_transaction(handle, action)
                        assert client.describe_transaction(handle)[
                            'state'] == 'idle'
                        assert not handle.main_transaction.is_active()
                    closed = client.close_session(handle)
                    handle = None
                    assert closed['rollback_requested'] is False
                    result['cases'].append(case)
                except Exception as exc:
                    result['failures'].append({
                        **case, 'error_type': type(exc).__name__})
                finally:
                    client.close()
    for timeout in (32768, 86400):
        case = {'invalid_lock_timeout': timeout}
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))
        direct = None
        try:
            try:
                client.open_session({'route': {
                    **route, 'transaction_lock_timeout': timeout}})
            except RelationalClientError:
                assert not client._connections
            else:
                raise AssertionError('Provider accepted excessive timeout')
            direct = driver.connect(password=password,
                                    **_route_arguments(route, driver))
            try:
                direct.begin(driver.tpb(isolation=driver.Isolation.SNAPSHOT,
                                        lock_timeout=timeout))
            except driver.DatabaseError as exc:
                assert 335544330 in exc.gds_codes
                assert 335544903 in exc.gds_codes
                case['native_codes'] = list(exc.gds_codes)
            else:
                raise AssertionError('Native engine accepted excess timeout')
            assert not direct.main_transaction.is_active()
            result['cases'].append(case)
        except Exception as exc:
            result['failures'].append({**case,
                                       'error_type': type(exc).__name__})
        finally:
            if direct is not None:
                try:
                    direct.close()
                except Exception as exc:
                    result['failures'].append({
                        **case, 'phase': 'close',
                        'error_type': type(exc).__name__})
            client.close()
    result['complete'] = len(result['cases']) == 50 and not result['failures']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    result = run(options.profiles)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
