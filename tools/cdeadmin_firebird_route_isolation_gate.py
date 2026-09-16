#!/usr/bin/env python3
"""Verify minimal routes on an owned server with hostile driver defaults."""

import argparse
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace
from contextlib import nullcontext
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _configure_client_library,
        _route_arguments,
    )
else:
    from cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _configure_client_library,
        _route_arguments,
    )

from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.providers.firebird.provider import _server_arguments
from pgadmin.cdeadmin.providers.firebird.provider import _create_client
from pgadmin.cdeadmin.providers.firebird.failed_session import (
    discard_failed_session,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.security.secrets import SecretLease


def failed_initialization_case(native, route, password, retained, interrupted):
    from firebird.base.hooks import hook_manager
    seen, retained_calls = [], []
    client = _create_client(SimpleNamespace(
        acquire_secret=lambda *_args: SecretLease(password)))
    error = KeyboardInterrupt('owned cancellation') if interrupted else (
        RuntimeError('owned initialization failure'))

    def initialize(handle, _route):
        seen.append(handle)
        handle.execute_immediate('CREATE TABLE OWNED_FAILED_INIT (ID INTEGER)')
        raise error

    def retain(handle):
        retained_calls.append(handle)
        return True

    client.config = replace(client.config, **{
        'session_initializer' if retained else 'connection_initializer':
        initialize})
    event = native.core.ConnectionHook.DETACH_REQUEST
    owner = native.core.Connection
    hook_manager.add_hook(event, owner, retain)
    try:
        try:
            client.open_session({'route': {
                **route, 'credential_reference_id': 'owned-secret',
                'principal_reference': 'owned-principal'}})
        except KeyboardInterrupt as caught:
            assert interrupted and caught is error
        except RelationalClientError:
            assert not interrupted
        else:
            raise AssertionError('Initialization unexpectedly succeeded')
    finally:
        hook_manager.remove_hook(event, owner, retain)
        for handle in seen:
            if not handle.is_closed():
                discard_failed_session(handle)
                raise AssertionError('Failed attachment was retained')
    assert len(seen) == 1 and not retained_calls
    assert not client._connections and not client._connection_databases
    with native.connect(**_route_arguments(route, native),
                        password=password) as observer:
        with observer.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM MON$ATTACHMENTS '
                           'WHERE MON$SYSTEM_FLAG = 0')
            assert cursor.fetchone()[0] == 1
            cursor.execute('SELECT COUNT(*) FROM RDB$RELATIONS '
                           "WHERE RDB$RELATION_NAME = 'OWNED_FAILED_INIT'")
            assert cursor.fetchone()[0] == 0
        observer.rollback()
    return {'retained_initializer': retained, 'interrupted': interrupted,
            'native_attachment_released': True,
            'pending_ddl_rolled_back': True,
            'retention_hook_bypassed': True}


def temporary_retention_case(native, route, password, operation_failure=False):
    from firebird.base.hooks import hook_manager
    client = _create_client(SimpleNamespace(
        acquire_secret=lambda *_args: SecretLease(password)))
    if operation_failure:
        client.config = replace(
            client.config, version_query='SELECT OWNED_MISSING_COLUMN '
            'FROM RDB$DATABASE')
    retained = []

    def retain(handle):
        retained.append(handle)
        return True

    event = native.core.ConnectionHook.DETACH_REQUEST
    owner = native.core.Connection
    hook_manager.add_hook(event, owner, retain)
    try:
        try:
            client.runtime_identity({'route': {
                **route, 'credential_reference_id': 'owned-secret',
                'principal_reference': 'owned-principal'}})
        except RelationalClientError as caught:
            if operation_failure:
                assert 'profile verification failed' in str(caught)
                assert not caught.attachment_release['connection_released']
            assert len(retained) == 1
            assert client._connections == retained
            assert not retained[0].is_closed()
        else:
            raise AssertionError('Unconfirmed release reported success')
    finally:
        hook_manager.remove_hook(event, owner, retain)
        client.close()
    assert retained[0].is_closed() and not client._connections
    with native.connect(**_route_arguments(route, native),
                        password=password) as observer:
        with observer.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM MON$ATTACHMENTS '
                           'WHERE MON$SYSTEM_FLAG = 0')
            assert cursor.fetchone()[0] == 1
        observer.rollback()
    return {'unconfirmed_release_refused': True, 'ownership_retained': True,
            'explicit_retry_detached': True,
            'operation_failure_preserved': operation_failure}


def failed_detach_case(native, route, password):
    client = _create_client(SimpleNamespace(
        acquire_secret=lambda *_args: SecretLease(password)))
    seen = []
    release_attempts = 0
    original = RelationalClientError('owned initialization failure')

    def initialize(handle, _route):
        seen.append(handle)
        handle.execute_immediate('CREATE TABLE OWNED_DETACH_FAIL (ID INTEGER)')
        raise original

    def release(handle):
        nonlocal release_attempts
        release_attempts += 1
        if release_attempts == 1:
            with patch.object(type(handle._att), 'detach',
                              side_effect=RuntimeError('injected detach')):
                discard_failed_session(handle)
        else:
            discard_failed_session(handle)

    client.config = replace(client.config, connection_initializer=initialize,
                            failed_session_releaser=release)
    try:
        try:
            client.open_session({'route': {
                **route, 'credential_reference_id': 'owned-secret',
                'principal_reference': 'owned-principal'}})
        except RelationalClientError as caught:
            assert caught is original
        else:
            raise AssertionError('Initialization unexpectedly succeeded')
        assert client._connections == [seen[0]]
        assert seen[0]._att is not None
        try:
            client.execute(seen[0], {'source': 'SELECT 1 FROM RDB$DATABASE'})
        except RelationalClientError as caught:
            assert 'cleanup-only' in str(caught)
        else:
            raise AssertionError('Failed attachment admitted a query')
    finally:
        client.close()
    assert seen[0].is_closed() and not client._connections
    with native.connect(**_route_arguments(route, native),
                        password=password) as observer:
        with observer.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM MON$ATTACHMENTS '
                           'WHERE MON$SYSTEM_FLAG = 0')
            assert cursor.fetchone()[0] == 1
            cursor.execute('SELECT COUNT(*) FROM RDB$RELATIONS '
                           "WHERE RDB$RELATION_NAME = 'OWNED_DETACH_FAIL'")
            assert cursor.fetchone()[0] == 0
        observer.rollback()
    return {'injected_detach_failure': True, 'ownership_retained': True,
            'query_refused': True, 'explicit_retry_detached': True,
            'pending_ddl_rolled_back': True}


def service_interruption_case(native, route, password):
    cases = []
    for stage in ('hook', 'operation'):
        for detach_failure in (False, True):
            client = _create_client(SimpleNamespace(
                acquire_secret=lambda *_args: SecretLease(password)))
            seen = []
            interruption = KeyboardInterrupt('owned service interruption')

            def interrupt(server, *_args):
                seen.append(server)
                assert '5.0.4' in server.info.version
                raise interruption

            if stage == 'hook':
                client._service_attached = interrupt
            else:
                client.config = replace(client.config,
                                        server_operation_runner=interrupt)
            request = {'route': {
                **route, 'credential_reference_id': 'owned-secret',
                'principal_reference': 'owned-principal'}}
            try:
                with (patch.object(native.core.Server, 'close',
                                   side_effect=RuntimeError('injected close'))
                      if detach_failure else nullcontext()):
                    try:
                        if stage == 'hook':
                            client._connect_server(request)
                        else:
                            client.run_server_operation(
                                request, 'database_statistics',
                                route['database'], {})
                    except KeyboardInterrupt as caught:
                        assert caught is interruption
                        assert caught.service_release[
                            'service_handle_released'] is not detach_failure
                    else:
                        raise AssertionError('Interruption was lost')
                assert len(seen) == 1
                assert (seen[0] in client._connections) is detach_failure
            finally:
                client.close()
            assert seen[0]._svc is None and not client._connections
            cases.append({'stage': stage, 'injected_close_failure':
                          detach_failure, 'interruption_preserved': True,
                          'service_released': True})
    return cases


def opening_lifecycle_case(native, route, password):
    cases = []
    for stage in ('connection', 'retained'):
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))
        seen = []

        def initialize(handle, _route):
            try:
                client.close()
            except RelationalClientError as caught:
                assert 'still opening' in str(caught)
            else:
                raise AssertionError('Close admitted during initialization')
            with handle.cursor() as cursor:
                cursor.execute("SELECT RDB$GET_CONTEXT('SYSTEM', "
                               "'ENGINE_VERSION') FROM RDB$DATABASE")
                assert cursor.fetchone()[0] == '5.0.4'
            handle.rollback()
            seen.append(handle)

        client.config = replace(client.config, **{
            'connection_initializer' if stage == 'connection' else
            'session_initializer': initialize})
        try:
            handle = client.open_session({'route': {
                **route, 'credential_reference_id': 'owned-secret',
                'principal_reference': 'owned-principal'}})
            assert seen == [handle]
            assert client._opening == 0 and not handle.is_closed()
            assert client.runtime_identity({}, handle)['version'] == '5.0.4'
        finally:
            client.close()
        assert handle.is_closed() and not client._connections
        cases.append({'stage': stage, 'close_refused_during_initialization':
                      True, 'published_attachment_usable': True,
                      'explicit_close_released': True})
    return cases


def worker_interruption_case(native, route, password):
    client = _create_client(SimpleNamespace(
        acquire_secret=lambda *_args: SecretLease(password)))
    query = None
    native_execute = client._execute_sql

    def interrupted(handle, request):
        native_execute(handle, request)
        raise KeyboardInterrupt('owned worker interruption')

    try:
        handle = client.open_session({'route': {
            **route, 'credential_reference_id': 'owned-secret',
            'principal_reference': 'owned-principal'}})
        with patch.object(client, '_execute_sql', side_effect=interrupted):
            query = client.submit_query(handle, {
                'source': 'SELECT 1 FROM RDB$DATABASE'})
            query.worker.join(10)
            assert not query.worker.is_alive()
        result = client.describe_result(query)
        assert result['complete'] and result['payload'][
            'execution_outcome_unknown']
        try:
            client.execute(handle, {'source': 'SELECT 2 FROM RDB$DATABASE'})
        except RelationalClientError as caught:
            assert 'interrupted' in str(caught)
        else:
            raise AssertionError('Interrupted session admitted reuse')
        client.close_session(handle)
        assert handle.is_closed()
        assert not client._connections and not client._tokens
    finally:
        if query is not None and query.worker.is_alive():
            client.cancel(query)
            query.worker.join(10)
        client.close()
    with native.connect(**_route_arguments(route, native),
                        password=password) as observer:
        with observer.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM MON$ATTACHMENTS '
                           'WHERE MON$SYSTEM_FLAG = 0')
            assert cursor.fetchone()[0] == 1
        observer.rollback()
    return {'interruption_injected_after_native_select': True,
            'terminal_unknown_outcome': True, 'reuse_refused': True,
            'explicit_close_released': True}


def run(image):
    import firebird.driver as native
    from firebird.driver.config import DriverConfig
    _configure_client_library(native)
    result = {'complete': False, 'cases': [], 'failures': [],
              'failed_initializations': [], 'temporary_release': None,
              'combined_failure_release': None,
              'failed_detach_release': None,
              'service_interruptions': [],
              'opening_lifecycle': [],
              'worker_interruption': None,
              'owned_container_removed': False}
    container = None
    password = secrets.token_urlsafe(24)
    path = '/var/lib/firebird/data/owned_isolation.fdb'
    phase = 'create-owned-server'

    def failure(error):
        result['failures'].append({
            'case': phase, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error))})

    try:
        container = docker(
            'run', '--detach', '--name',
            'cdeadmin-route-isolation-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--memory', '512m', '--memory-swap', '512m',
            '--publish', '127.0.0.1::3050',
            '--env', 'FIREBIRD_ROOT_PASSWORD', '--env', 'FIREBIRD_DATABASE',
            image, env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                            FIREBIRD_DATABASE=path)).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise ValueError('Invalid owned container identity')
        port = published_port(container)
        route = {'host': '127.0.0.1', 'port': port, 'database': path,
                 'user': 'SYSDBA'}
        phase = 'readiness'
        deadline = time.monotonic() + 45
        while True:
            try:
                with native.connect(**_route_arguments(route, native),
                                    password=password):
                    break
            except native.Error:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        for phase in ('dsn', 'host', 'named', 'session', 'INET', 'INET4'):
            try:
                registry = DriverConfig('owned-isolation-' + phase)
                # Invalid local destinations cannot escape this test server.
                if phase == 'dsn':
                    registry.db_defaults.dsn.value = 'invalid-dsn'
                elif phase == 'host':
                    registry.server_defaults.host.value = '127.0.0.1'
                    registry.server_defaults.port.value = str(port)
                elif phase == 'named':
                    registry.register_database(
                        f'127.0.0.1/{port}:{path}').database.value = 'missing'
                elif phase == 'session':
                    registry.db_defaults.role.value = 'UNSELECTED'
                    registry.db_defaults.session_time_zone.value = (
                        'Not/A/TimeZone')
                with patch.object(native, 'driver_config', registry), \
                        patch.object(native.core, 'driver_config', registry):
                    selected = dict(route)
                    if phase in ('INET', 'INET4'):
                        selected['protocol'] = phase
                    with native.connect(**_route_arguments(selected, native),
                                        password=password) as handle:
                        with handle.cursor() as cursor:
                            cursor.execute(
                                "SELECT RDB$GET_CONTEXT('SYSTEM', "
                                "'ENGINE_VERSION'), CURRENT_USER, "
                                "CURRENT_ROLE FROM RDB$DATABASE")
                            version, user, role = cursor.fetchone()
                        assert version == '5.0.4'
                        assert user.strip() == 'SYSDBA'
                        assert role.strip() == 'NONE'
                        assert handle.info.name == path
                        handle.rollback()
                    if phase in ('INET', 'INET4'):
                        with native.connect_server(
                                **_server_arguments(selected, native),
                                password=password) as service:
                            assert '5.0.4' in service.info.version
                result['cases'].append({'case': phase, 'target_verified': True,
                                        'identity_verified': True,
                                        'service_verified': phase in (
                                            'INET', 'INET4')})
            except Exception as error:
                failure(error)
        for retained in (False, True):
            for interrupted in (False, True):
                phase = f'failed-init-{retained}-{interrupted}'
                try:
                    result['failed_initializations'].append(
                        failed_initialization_case(
                            native, route, password, retained, interrupted))
                except Exception as error:
                    failure(error)
        phase = 'worker-interruption'
        try:
            result['worker_interruption'] = worker_interruption_case(
                native, route, password)
        except Exception as error:
            failure(error)
        phase = 'opening-lifecycle'
        try:
            result['opening_lifecycle'] = opening_lifecycle_case(
                native, route, password)
        except Exception as error:
            failure(error)
        phase = 'service-interruptions'
        try:
            result['service_interruptions'] = service_interruption_case(
                native, route, password)
        except Exception as error:
            failure(error)
        phase = 'failed-detach-release'
        try:
            result['failed_detach_release'] = failed_detach_case(
                native, route, password)
        except Exception as error:
            failure(error)
        phase = 'temporary-retention'
        try:
            result['temporary_release'] = temporary_retention_case(
                native, route, password)
        except Exception as error:
            failure(error)
        phase = 'temporary-combined-failure'
        try:
            result['combined_failure_release'] = temporary_retention_case(
                native, route, password, operation_failure=True)
        except Exception as error:
            failure(error)
    except Exception as error:
        failure(error)
    finally:
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                phase = 'cleanup'
                failure(error)
    result['complete'] = (len(result['cases']) == 6 and
                          len(result['failed_initializations']) == 4 and
                          result['temporary_release'] is not None and
                          result['combined_failure_release'] is not None and
                          result['failed_detach_release'] is not None and
                          len(result['service_interruptions']) == 4 and
                          len(result['opening_lifecycle']) == 2 and
                          result['worker_interruption'] is not None and
                          result['owned_container_removed'] and
                          not result['failures'])
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
