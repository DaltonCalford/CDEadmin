#!/usr/bin/env python3
"""Observe native asynchronous cancellation without rolling back caller work.

Only a uniquely owned database is mutated. A native attachment statement
timeout bounds the deliberately expensive SELECT if cancellation fails.
"""

import argparse
import json
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

from cdeadmin_firebird_admin_mapping_gate import (
    ADMINISTRATION, _create_client, _route_arguments,
)
from pgadmin.cdeadmin.security.secrets import SecretLease


def run(profiles, container, application_path=False, registry_path=False):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    password = route.pop('password')
    route.update(credential_reference_id='owned-cancellation-secret',
                 principal_reference='owned-cancellation-principal')
    path = str(PurePosixPath(route['database']).parent /
               ('cde_cancel_' + uuid.uuid4().hex + '.fdb'))
    result = {'complete': False, 'cases': [], 'failures': [],
              'fixture_database': path, 'fixture_removed': False,
              'credential_values_exported': False,
              'native_api_probe_only': not application_path}
    client = _create_client(SimpleNamespace(
        acquire_secret=lambda *_args: SecretLease(password)))
    registry = registration = binding = None
    if registry_path:
        from pgadmin.cdeadmin.core import (
            EndpointContext, ProviderRegistry, ProviderReleaseError,
        )
        from pgadmin.cdeadmin.providers.firebird.provider import (
            FirebirdProvider, PROFILE,
        )
        manifest_path = (Path(__file__).resolve().parents[1] / 'web' /
                         'pgadmin/cdeadmin/providers/firebird/'
                         'provider_manifest.json')
        manifest = json.loads(manifest_path.read_text())
        identity = manifest['identity']
        context = EndpointContext(
            endpoint_id=str(uuid.uuid4()), mode='legacy_native',
            experience_family=PROFILE.engine_id,
            provider_id=identity['provider_id'],
            provider_version=identity['provider_version'],
            profile_id=identity['profile_id'],
            profile_version=identity['profile_version'],
            target_adapter_id=manifest['composition']['target_adapter_ids'][0],
            target_adapter_version='owned-native-probe',
            pool_namespace=str(uuid.uuid4()),
            session_namespace=str(uuid.uuid4()),
            cache_namespace=str(uuid.uuid4()),
            diagnostic_namespace=str(uuid.uuid4()),
            effective_permissions=frozenset(
                item['permission_id'] for item in manifest['permissions']
                if item['granted']),
            runtime_identity_generation='owned-native-generation',
        )
        registry = ProviderRegistry()
        with patch(
            'pgadmin.cdeadmin.providers.firebird.provider.create_provider',
            side_effect=lambda context, permissions: FirebirdProvider(
                context, permissions, client),
        ):
            registration = registry.register_package(
                manifest, 'pgadmin.cdeadmin.providers.firebird.provider')
            binding = registry.resolve(context)
    handle = observer = worker = None
    requested = False

    def exists():
        check = subprocess.run(
            ['docker', 'exec', container, 'test', '-e', path],
            capture_output=True, check=False)
        if check.returncode not in (0, 1):
            raise RuntimeError('Cannot observe owned fixture existence')
        return check.returncode == 0

    def rows(connection, sql, parameters=()):
        with connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            return cursor.fetchall() if cursor.description else []

    try:
        assert not exists()
        plan = ADMINISTRATION.plan({
            '_provider_route': route, 'resource_kind': 'database',
            'operation_id': 'create', 'target_resource': None,
            'draft': {'database_path': path}})
        requested = True
        ADMINISTRATION.apply(client, plan)
        handle = client.open_session({'route': {**route, 'database': path}})
        observer = driver.connect(password=password, **_route_arguments(
            {**route, 'database': path}, driver))
        rows(handle, 'CREATE TABLE MARKERS (ID INTEGER PRIMARY KEY)')
        rows(handle, 'CREATE TABLE NUMBERS (ID INTEGER PRIMARY KEY)')
        handle.commit()
        for number in range(100):
            rows(handle, 'INSERT INTO NUMBERS VALUES (?)', [number])
        handle.commit()
        # Firebird 5.0.4's remote setStatementTimeout emits unitless SQL
        # (seconds), although the interface documents milliseconds. Use
        # explicit SQL units and verify the native value before costly work.
        rows(handle, 'SET STATEMENT TIMEOUT 10000 MILLISECOND')
        reported_timeout = handle._att.get_statement_timeout()
        result['reported_statement_timeout'] = reported_timeout
        sql_timeout = rows(handle, "SELECT RDB$GET_CONTEXT('SYSTEM', "
                           "'STATEMENT_TIMEOUT') FROM RDB$DATABASE")[0][0]
        result['sql_statement_timeout'] = sql_timeout
        assert int(sql_timeout) == 10000
        handle.rollback()
        attachment_id = handle.info.id
        for number, action in enumerate(('rollback', 'commit'), 1):
            rows(handle, 'INSERT INTO MARKERS VALUES (?)', [number])
            transaction_id = handle.main_transaction.info.id
            if application_path:
                duplicate = client.submit_query(handle, {
                    'source': 'INSERT INTO MARKERS VALUES (?)',
                    'parameters': [number]})
                worker = duplicate.worker
                worker.join(10)
                assert not worker.is_alive()
                failure = client.describe_result(duplicate)
                assert failure['complete']
                assert failure['payload']['execution_state'] == 'failed'
                assert 335544665 in failure['payload']['error'][
                    'native_status_codes']
                assert handle.main_transaction.info.id == transaction_id
                assert rows(handle, 'SELECT ID FROM MARKERS') == [(number,)]
                assert client.cancel(duplicate) is False
            outcome = {}
            source = ('SELECT /* cde-owned-cancellation */ COUNT(*) FROM '
                      'NUMBERS A CROSS JOIN NUMBERS B CROSS JOIN NUMBERS C '
                      'CROSS JOIN NUMBERS D CROSS JOIN NUMBERS E')

            def execute():
                try:
                    outcome['rows'] = rows(handle, source)
                except Exception as exc:
                    outcome['error_type'] = type(exc).__name__
                    outcome['gds_codes'] = list(getattr(exc, 'gds_codes', ()))

            if application_path:
                query = client.submit_query(handle, {'source': source,
                                                     'parameters': []})
                worker = query.worker
                assert not client.describe_result(query)['complete']
                try:
                    client.control_transaction(handle, 'commit')
                except Exception as exc:
                    assert 'running' in str(exc)
                else:
                    raise AssertionError('Busy session accepted commit')
                if registry is not None:
                    try:
                        registry.unload(*registration.key)
                    except ProviderReleaseError:
                        pass
                    else:
                        raise AssertionError('Busy registry binding unloaded')
                    assert registry.resolve(context) is binding
                    assert handle in client._connections
                    try:
                        with registry.endpoint_configuration_change(
                                context.endpoint_id):
                            raise AssertionError(
                                'Busy profile change admitted')
                    except ProviderReleaseError:
                        pass
            else:
                worker = threading.Thread(target=execute, daemon=True)
                worker.start()
            observed = False
            deadline = time.monotonic() + 8
            while worker.is_alive() and time.monotonic() < deadline:
                if observer.main_transaction.is_active():
                    observer.rollback()
                active = rows(observer,
                              'SELECT MON$SQL_TEXT FROM MON$STATEMENTS '
                              'WHERE MON$ATTACHMENT_ID = ? AND MON$STATE = 1',
                              [attachment_id])
                observed = any('cde-owned-cancellation' in str(row[0])
                               for row in active)
                if observed:
                    break
                time.sleep(0.05)
            assert observed, 'Expensive statement was not observed running'
            if application_path:
                assert client.cancel(query) is True
            else:
                handle._att.cancel_operation(driver.CancelType.RAISE)
            worker.join(20)
            assert not worker.is_alive(), 'Native statement did not finish'
            if application_path:
                native = client.describe_result(query)
                assert native['complete']
                assert native['payload']['execution_state'] == 'cancelled'
                assert not native['payload'].get('session_reuse_blocked')
                outcome['gds_codes'] = native['payload']['error'][
                    'native_status_codes']
                assert client.cancel(query) is False
            assert 335544794 in outcome.get('gds_codes', ()), outcome
            # Timeout errors also contain isc_cancelled. They must not be
            # mistaken for successful explicit cancellation.
            assert len(outcome['gds_codes']) == 1, outcome['gds_codes']
            assert handle.main_transaction.is_active()
            assert handle.main_transaction.info.id == transaction_id
            if registry is not None:
                try:
                    with registry.endpoint_configuration_change(
                            context.endpoint_id):
                        raise AssertionError('Pending work profile changed')
                except ProviderReleaseError:
                    pass
                assert handle.main_transaction.info.id == transaction_id
            assert rows(handle, 'SELECT ID FROM MARKERS') == [(number,)]
            observer.rollback()
            assert rows(observer, 'SELECT ID FROM MARKERS') == []
            getattr(handle, action)()
            observer.rollback()
            assert rows(observer, 'SELECT ID FROM MARKERS') == (
                [(number,)] if action == 'commit' else [])
            result['cases'].append({
                'final_action': action, 'active_statement_observed': True,
                'native_codes': outcome['gds_codes'],
                'async_statement_error_preserves_prior_work': application_path,
                'busy_registry_release_retains_ownership': registry_path,
                'profile_change_preserves_pending_work': registry_path,
                'caller_transaction_preserved': True,
                'explicit_finality_verified': True})
    except Exception as exc:
        result['failures'].append({'case': 'native-cancellation',
                                   'error_type': type(exc).__name__,
                                   'line': traceback.extract_tb(
                                       exc.__traceback__)[-1].lineno,
                                   'message': str(exc).replace(password,
                                                               '[redacted]')})
    finally:
        if worker is not None and worker.is_alive():
            worker.join(20)
        running = worker is not None and worker.is_alive()
        for name, connection in (('observer', observer), ('query', handle)):
            if connection is not None and not (name == 'query' and running):
                try:
                    connection.close()
                except Exception as exc:
                    result['failures'].append({
                        'case': 'close-' + name,
                        'error_type': type(exc).__name__})
        if requested and not running:
            try:
                if exists():
                    connection = driver.connect(password=password,
                                                **_route_arguments(
                                                    dict(route, database=path),
                                                    driver))
                    connection.drop_database()
                result['fixture_removed'] = not exists()
            except Exception as exc:
                result['failures'].append({'case': 'cleanup',
                                           'error_type': type(exc).__name__})
        if not running:
            try:
                if registry is not None:
                    registry.unload(*registration.key)
                    assert not registration.bindings
                    assert registration.state == 'unloaded'
                else:
                    client.close()
            except Exception as exc:
                result['failures'].append({'case': 'client-close',
                                           'error_type': type(exc).__name__})
        if running:
            result['failures'].append({'case': 'worker-still-running',
                                       'fixture_retained': True})
    result['complete'] = (len(result['cases']) == 2 and
                          result['fixture_removed'] and not result['failures'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--container', default='cdeadmin-demo-firebird')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--application-path', action='store_true')
    parser.add_argument('--registry-path', action='store_true')
    args = parser.parse_args()
    result = run(args.profiles, args.container,
                 args.application_path or args.registry_path,
                 args.registry_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
