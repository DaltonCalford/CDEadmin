#!/usr/bin/env python3
"""Qualify Firebird availability transitions in an owned isolated server."""

import argparse
import json
import os
import re
import secrets
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_external_functions_gate import browser_checks
    from .cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _create_client, _configure_client_library, SecretLease,
    )
else:
    from cdeadmin_firebird_external_functions_gate import browser_checks
    from cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _create_client, _configure_client_library, SecretLease,
    )
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def missing_privileges(error):
    """Retain only native privilege identifiers, never arbitrary error text."""
    names = set()
    seen = set()
    for _index in range(8):
        if error is None or id(error) in seen:
            break
        seen.add(id(error))
        names.update(re.findall(
            r'System privilege ([A-Z_]{1,63}) is missing', str(error)))
        error = error.__cause__ or error.__context__
    return sorted(names)


def run(image, browser_options=None):
    import firebird.driver as native
    _configure_client_library(native)
    password = secrets.token_urlsafe(24)
    operator_password = secrets.token_urlsafe(24)
    container = client = None
    result = {'complete': False, 'checks': [], 'failures': [],
              'owned_container_removed': False,
              'credential_values_exported': False}
    connections = []
    bootstrap = '/var/lib/firebird/data/owned_availability_bootstrap.fdb'

    def failure(case, error):
        result['failures'].append({
            'case': case, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error)),
            'frames': [{'file': Path(frame.filename).name,
                        'line': frame.lineno, 'function': frame.name}
                       for frame in traceback.extract_tb(
                           error.__traceback__)]})

    def connect(path, create=False):
        method = native.create_database if create else native.connect
        handle = method(password=password, **_route_arguments(
            {**route, 'database': path}, native))
        connections.append(handle)
        return handle

    def state(path):
        # This oracle remains independently authorized as SYSDBA even while
        # the operation under test uses a limited role on another client.
        handle = native.connect(password=password, **_route_arguments(
            {**route, 'database': path, 'user': 'SYSDBA'}, native))
        connections.append(handle)
        try:
            with handle.cursor() as cursor:
                cursor.execute('SELECT MON$SHUTDOWN_MODE FROM MON$DATABASE')
                return cursor.fetchone()[0]
        finally:
            handle.close()

    def task(path, operation, draft):
        retained = {id(handle) for handle in client._connections}
        request = {'resource_kind': 'database', 'operation_id': operation,
                   'draft': draft, '_provider_route': {
                       **route, 'database': path,
                       'credential_reference_id': 'owned-availability',
                       'principal_reference': 'owned-qa'}}
        errors = client.config.administration.validate(request)['errors']
        if errors:
            raise AssertionError('Owned availability draft did not validate')
        plan = client.plan_admin_operation(request)
        assert {id(handle) for handle in client._connections} == retained
        response = client.apply_admin_operation(plan)
        assert {id(handle) for handle in client._connections} == retained
        return response

    phase = 'create-owned-server'
    try:
        container = docker(
            'run', '--detach', '--name',
            'cdeadmin-availability-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--publish', '127.0.0.1::3050',
            '--env', 'FIREBIRD_ROOT_PASSWORD',
            '--env', 'FIREBIRD_DATABASE', image,
            env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                     FIREBIRD_DATABASE=bootstrap)).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise ValueError('Owned container identity is invalid')
        route = {'host': '127.0.0.1', 'port': published_port(container),
                 'user': 'SYSDBA', 'timeout': 2,
                 'auth_plugin_list': 'Srp256'}
        phase = 'readiness'
        deadline = time.monotonic() + 45
        ready = None
        while time.monotonic() < deadline:
            try:
                ready = connect(bootstrap)
                break
            except native.Error:
                time.sleep(0.25)
        if ready is None:
            raise RuntimeError('Owned Firebird did not become ready')
        with ready.cursor() as cursor:
            cursor.execute(
                "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
                'FROM RDB$DATABASE')
            result['engine_version'] = cursor.fetchone()[0]
        assert result['engine_version'] == '5.0.4'
        ready.close()
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))
        # Native shut.cpp permits monotonically more restrictive shutdown
        # and less restrictive online transitions. Each case starts ONLINE
        # with no borrowed user attachment or transaction.
        for mode in ('MULTI', 'SINGLE', 'FULL'):
            for method in ('FORCED', 'DENY_ATTACHMENTS', 'DENY_TRANSACTIONS'):
                phase = mode + '-' + method
                check = {'case': phase, 'passed': False}
                result['checks'].append(check)
                path = '/var/lib/firebird/data/owned_' + phase + '.fdb'
                try:
                    connect(path, create=True).close()
                    assert state(path) == 0
                    task(path, 'shutdown_database', {
                        'mode': mode, 'method': method, 'shutdown_timeout': 0})
                    if mode == 'FULL':
                        try:
                            connect(path)
                        except native.Error as error:
                            check['full_attach_denied_codes'] = list(
                                status_codes(error))
                        else:
                            raise AssertionError(
                                'FULL shutdown admitted an ordinary attach')
                    else:
                        check['shutdown_mode'] = state(path)
                        assert check['shutdown_mode'] == {
                            'MULTI': 1, 'SINGLE': 2}[mode]
                    # Exercise every valid ascending availability level.
                    steps = {'FULL': ('SINGLE', 'MULTI', 'NORMAL'),
                             'SINGLE': ('MULTI', 'NORMAL'),
                             'MULTI': ('NORMAL',)}[mode]
                    check['online_states'] = []
                    for online in steps:
                        task(path, 'bring_online', {'mode': online})
                        observed = state(path)
                        check['online_states'].append(observed)
                        assert observed == {
                            'NORMAL': 0, 'MULTI': 1, 'SINGLE': 2}[online]
                    check['passed'] = True
                except Exception as error:
                    failure(phase, error)
        for initial, operation, desired in (
                ('NORMAL', 'bring_online', 'MULTI'),
                ('NORMAL', 'bring_online', 'SINGLE'),
                ('MULTI', 'bring_online', 'SINGLE'),
                ('SINGLE', 'shutdown_database', 'MULTI'),
                ('FULL', 'shutdown_database', 'MULTI'),
                ('FULL', 'shutdown_database', 'SINGLE')):
            phase = initial + '-' + operation + '-' + desired
            check = {'case': phase, 'passed': False}
            result['checks'].append(check)
            path = '/var/lib/firebird/data/owned_' + phase + '.fdb'
            try:
                connect(path, create=True).close()
                if initial != 'NORMAL':
                    task(path, 'shutdown_database', {
                        'mode': initial, 'method': 'FORCED',
                        'shutdown_timeout': 0})
                draft = {'mode': desired}
                if operation == 'shutdown_database':
                    draft.update(method='FORCED', shutdown_timeout=0)
                try:
                    task(path, operation, draft)
                except RelationalClientError as error:
                    check['rejection_codes'] = list(status_codes(error))
                    assert 335544835 in check['rejection_codes']
                else:
                    raise AssertionError('Invalid native transition accepted')
                if initial != 'FULL':
                    assert state(path) == {
                        'NORMAL': 0, 'MULTI': 1, 'SINGLE': 2}[initial]
                if initial != 'NORMAL':
                    task(path, 'bring_online', {'mode': 'NORMAL'})
                assert state(path) == 0
                check['passed'] = True
            except Exception as error:
                failure(phase, error)
        # Native CHANGE_SHUTDOWN_MODE is an explicit system privilege, not
        # ordinary table DML or a guessed administrator-role convention.
        with connect(bootstrap) as administrator:
            with administrator.cursor() as cursor:
                cursor.execute("CREATE USER CDE_AVAIL_OPERATOR PASSWORD '" +
                               operator_password.replace("'", "''") + "'")
            administrator.commit()
        admin_client = client
        for role_case in ('no_active_role', *range(16)):
            phase = 'authorization-' + str(role_case)
            check = {'case': phase, 'passed': False}
            result['checks'].append(check)
            path = '/var/lib/firebird/data/owned_' + phase + '.fdb'
            original_route = route
            try:
                with connect(path, create=True) as administrator:
                    with administrator.cursor() as cursor:
                        mask = (15 if role_case == 'no_active_role'
                                else role_case)
                        privileges = ', '.join(
                            privilege for bit, privilege in enumerate((
                                'CHANGE_SHUTDOWN_MODE',
                                'ACCESS_SHUTDOWN_DATABASE',
                                'USE_GFIX_UTILITY',
                                'IGNORE_DB_TRIGGERS')) if mask & (1 << bit))
                        check['role_privileges'] = privileges
                        cursor.execute('CREATE ROLE CDE_AVAIL_ROLE' + (
                            ' SET SYSTEM PRIVILEGES TO ' + privileges
                            if privileges else ''))
                        cursor.execute('GRANT CDE_AVAIL_ROLE TO '
                                       'USER CDE_AVAIL_OPERATOR')
                    administrator.commit()
                client = _create_client(SimpleNamespace(
                    acquire_secret=lambda *_args: SecretLease(
                        operator_password)))
                route = {**route, 'user': 'CDE_AVAIL_OPERATOR'}
                draft = {'mode': 'MULTI', 'method': 'FORCED',
                         'shutdown_timeout': 0}
                if role_case != 'no_active_role':
                    draft['role'] = 'CDE_AVAIL_ROLE'
                try:
                    task(path, 'shutdown_database', draft)
                except RelationalClientError as error:
                    check['denial_codes'] = list(status_codes(error))
                    check['missing_system_privileges'] = missing_privileges(
                        error)
                    assert role_case != 15
                    changes = role_case != 'no_active_role' and mask & 1
                    expected = (335544528 if changes and not mask & 2 else
                                335545112 if changes else 335544352)
                    assert expected in check['denial_codes']
                    if changes and mask & 2:
                        assert check['missing_system_privileges'] == [
                            'USE_GFIX_UTILITY' if not mask & 4 else
                            'IGNORE_DB_TRIGGERS']
                else:
                    assert role_case == 15
                    task(path, 'bring_online', {
                        'mode': 'NORMAL', 'role': 'CDE_AVAIL_ROLE'})
                    check['privileged_shutdown_and_online_returned'] = True
                    check['privileged_additional_modes'] = []
                    for mode in ('SINGLE', 'FULL'):
                        task(path, 'shutdown_database', {
                            'mode': mode, 'method': 'FORCED',
                            'shutdown_timeout': 0, 'role': 'CDE_AVAIL_ROLE'})
                        if mode == 'FULL':
                            try:
                                state(path)
                            except native.Error as error:
                                assert 335544528 in status_codes(error)
                            else:
                                raise AssertionError(
                                    'FULL admitted an ordinary attachment')
                        else:
                            assert state(path) == 2
                        for online in (('SINGLE', 'MULTI', 'NORMAL')
                                       if mode == 'FULL' else
                                       ('MULTI', 'NORMAL')):
                            task(path, 'bring_online', {
                                'mode': online, 'role': 'CDE_AVAIL_ROLE'})
                            assert state(path) == {
                                'SINGLE': 2, 'MULTI': 1, 'NORMAL': 0}[online]
                        check['privileged_additional_modes'].append(mode)
                route = original_route
                observed = state(path)
                check['independent_post_response_mode'] = observed
                changed_despite_error = (role_case != 'no_active_role' and
                                         role_case != 15 and mask & 1)
                assert observed == (1 if changed_despite_error else 0)
                if changed_despite_error:
                    check['native_changed_before_denied_response'] = True
                    # A separately authorized operator explicitly restores
                    # availability after recording the actual native state.
                    # Never replay the failed shutdown request.
                    operator_client = client
                    client = admin_client
                    try:
                        task(path, 'bring_online', {'mode': 'NORMAL'})
                    finally:
                        client = operator_client
                check['passed'] = True
            except Exception as error:
                failure(phase, error)
            finally:
                route = original_route
                if client is not admin_client:
                    try:
                        client.close()
                    except Exception as error:
                        failure('close-owned-operator', error)
                client = admin_client
        for provider_owned, method in (
                (owned, method) for owned in (False, True)
                for method in ('DENY_ATTACHMENTS', 'DENY_TRANSACTIONS',
                               'FORCED')):
            phase = ('provider-' if provider_owned else 'native-') + (
                'pending-transaction-' + method)
            check = {'case': phase, 'passed': False}
            result['checks'].append(check)
            path = '/var/lib/firebird/data/owned_' + phase + '.fdb'
            held = None
            try:
                held = connect(path, create=True)
                if provider_owned:
                    held.close()
                    held = client._connect({'route': {
                        **route, 'database': path,
                        'credential_reference_id': 'owned-availability',
                        'principal_reference': 'owned-qa'}})
                with held.cursor() as cursor:
                    cursor.execute('CREATE TABLE PENDING_MARKER (ID INTEGER)')
                held.commit()
                with held.cursor() as cursor:
                    cursor.execute('INSERT INTO PENDING_MARKER VALUES (1)')
                    cursor.execute('SELECT CURRENT_TRANSACTION '
                                   'FROM RDB$DATABASE')
                    transaction = cursor.fetchone()[0]
                try:
                    task(path, 'shutdown_database', {
                        'mode': 'FULL', 'method': method,
                        'shutdown_timeout': 0})
                except RelationalClientError as error:
                    assert method != 'FORCED'
                    check['rejection_codes'] = list(status_codes(error))
                    assert 335544557 in check['rejection_codes']
                    assert state(path) == 0
                    with held.cursor() as cursor:
                        cursor.execute('SELECT CURRENT_TRANSACTION '
                                       'FROM RDB$DATABASE')
                        assert cursor.fetchone()[0] == transaction
                        cursor.execute('SELECT ID FROM PENDING_MARKER')
                        assert cursor.fetchall() == [(1,)]
                    check['pending_transaction_preserved'] = True
                    held.rollback()
                else:
                    assert method == 'FORCED'
                    # Native forced shutdown, not application cleanup, ends
                    # the remote attachment and its pending transaction.
                    task(path, 'bring_online', {'mode': 'NORMAL'})
                    with connect(path) as observer:
                        with observer.cursor() as cursor:
                            cursor.execute('SELECT ID FROM PENDING_MARKER')
                            assert cursor.fetchall() == []
                    check['forced_pending_rows_absent'] = True
                try:
                    if provider_owned:
                        receipt = client.close_session(held)
                        check['provider_session_release'] = receipt
                        assert receipt['connection_released'] is True
                        assert held not in client._connections
                        if method == 'FORCED':
                            assert receipt[
                                'native_attachment_shutdown_observed'] is True
                            assert receipt[
                                'rollback_completion_confirmed'] is False
                    else:
                        held.close()
                except native.DatabaseError as error:
                    # Driver 1.10.11 attempts its default rollback even after
                    # native forced shutdown destroyed the remote transaction.
                    # Preserve that failure independently of the observed
                    # shutdown outcome; only a confirmed closed handle may
                    # be removed from this gate's ownership list.
                    assert method == 'FORCED'
                    check['shutdown_cleanup_codes'] = list(status_codes(error))
                    assert 335544856 in check['shutdown_cleanup_codes']
                    assert held.is_closed() is True
                    check['shutdown_attachment_closed'] = True
                check['passed'] = True
            except Exception as error:
                failure(phase, error)
        # Each worker owns its attachment exclusively. Let a real pending
        # transaction finish during the native grace period, not before the
        # maintenance call. Test commit and rollback independently.
        for method, delay in (
                ('DENY_ATTACHMENTS', 5), ('DENY_TRANSACTIONS', 5),
                ('DENY_ATTACHMENTS', 30), ('DENY_TRANSACTIONS', 30)):
            for decision in ('commit', 'rollback'):
                phase = ('graceful-' + method + '-' + decision + '-' +
                         str(delay))
                check = {'case': phase, 'passed': False}
                result['checks'].append(check)
                path = '/var/lib/firebird/data/owned_' + phase + '.fdb'
                ready_event, release_event = Event(), Event()

                def finish_pending():
                    held = native.connect(
                        password=password, **_route_arguments(
                            {**route, 'database': path}, native))
                    try:
                        with held.cursor() as cursor:
                            cursor.execute('INSERT INTO PENDING_MARKER '
                                           'VALUES (1)')
                        ready_event.set()
                        if not release_event.wait(10):
                            raise AssertionError(
                                'Graceful test was not started')
                        time.sleep(1)
                        getattr(held, decision)()
                        check['worker_decision_finished'] = True
                    finally:
                        held.close()
                        check['worker_attachment_closed'] = True

                try:
                    setup = connect(path, create=True)
                    with setup.cursor() as cursor:
                        cursor.execute('CREATE TABLE PENDING_MARKER '
                                       '(ID INTEGER PRIMARY KEY)')
                    setup.commit()
                    setup.close()
                    with ThreadPoolExecutor(max_workers=1) as workers:
                        worker = workers.submit(finish_pending)
                        try:
                            assert ready_event.wait(10)
                            started = time.monotonic()
                            release_event.set()
                            try:
                                task(path, 'shutdown_database', {
                                    'mode': 'FULL', 'method': method,
                                    'shutdown_timeout': delay})
                            except RelationalClientError as error:
                                # Firebird 5.0.4 shared-cache code decrements
                                # its retry count in millisecond-sized steps.
                                # Its advertised seconds can expire early.
                                # Preserve that native limitation, never retry
                                # or silently multiply the user's timeout.
                                assert delay == 5
                                assert 335544557 in status_codes(error)
                                check['native_early_timeout_codes'] = list(
                                    status_codes(error))
                            else:
                                assert delay == 30
                            finally:
                                elapsed = time.monotonic() - started
                                check['elapsed_seconds'] = round(elapsed, 3)
                                worker.result(timeout=10)
                            if delay == 30:
                                assert elapsed >= 0.9
                            else:
                                assert elapsed < 5
                        finally:
                            release_event.set()
                    if delay == 30:
                        try:
                            connect(path)
                        except native.Error as error:
                            assert 335544528 in status_codes(error)
                        else:
                            raise AssertionError(
                                'Graceful FULL still admits attach')
                        task(path, 'bring_online', {'mode': 'NORMAL'})
                    else:
                        assert state(path) == 0
                    observer = connect(path)
                    with observer.cursor() as cursor:
                        cursor.execute('SELECT ID FROM PENDING_MARKER')
                        assert cursor.fetchall() == (
                            [(1,)] if decision == 'commit' else [])
                    observer.close()
                    check['pending_decision_preserved'] = decision
                    check['passed'] = True
                except Exception as error:
                    failure(phase, error)
        for mode in ('MULTI', 'SINGLE', 'FULL'):
            phase = 'physical-backup-shutdown-' + mode
            check = {'case': phase, 'passed': False}
            result['checks'].append(check)
            path = '/var/lib/firebird/data/owned_' + phase + '.fdb'
            try:
                setup = connect(path, create=True)
                with setup.cursor() as cursor:
                    cursor.execute('ALTER DATABASE BEGIN BACKUP')
                setup.commit()
                setup.close()
                try:
                    task(path, 'shutdown_database', {
                        'mode': mode, 'method': 'FORCED',
                        'shutdown_timeout': 0})
                except RelationalClientError as error:
                    assert mode in ('SINGLE', 'FULL')
                    check['rejection_codes'] = list(status_codes(error))
                    assert 335544835 in check['rejection_codes']
                else:
                    assert mode == 'MULTI'
                observer = connect(path)
                with observer.cursor() as cursor:
                    cursor.execute('SELECT MON$SHUTDOWN_MODE, '
                                   'MON$BACKUP_STATE FROM MON$DATABASE')
                    observed = cursor.fetchone()
                    assert observed == (1 if mode == 'MULTI' else 0, 1)
                    check['post_state'] = list(observed)
                observer.close()
                if mode == 'MULTI':
                    task(path, 'bring_online', {'mode': 'NORMAL'})
                setup = connect(path)
                with setup.cursor() as cursor:
                    cursor.execute('ALTER DATABASE END BACKUP')
                setup.commit()
                setup.close()
                observer = connect(path)
                with observer.cursor() as cursor:
                    cursor.execute('SELECT MON$BACKUP_STATE FROM MON$DATABASE')
                    assert cursor.fetchone() == (0,)
                observer.close()
                check['backup_state_restored'] = True
                check['passed'] = True
            except Exception as error:
                failure(phase, error)
        phase = 'native-trailing-space-canonicalization'
        check = {'case': phase, 'passed': False}
        result['checks'].append(check)
        try:
            plain = '/var/lib/firebird/data/owned_exact_target.fdb'
            spaced = plain + ' '
            connect(plain, create=True).close()
            # why.cpp::attachOrCreateDatabase rtrim()s the native filename.
            # A spaced name is NOT an independently addressable sibling.
            try:
                connect(spaced, create=True)
            except native.Error as error:
                check['native_create_collision_codes'] = list(
                    status_codes(error))
                assert 335544733 in check['native_create_collision_codes']
            else:
                raise AssertionError(
                    'Expected native canonical-name collision')
            task(spaced, 'shutdown_database', {
                'mode': 'MULTI', 'method': 'DENY_ATTACHMENTS',
                'shutdown_timeout': 0})
            check['trimmed_sibling_mode'] = state(plain)
            check['exact_target_mode'] = state(spaced)
            assert check['trimmed_sibling_mode'] == 1
            assert check['exact_target_mode'] == 1
            check['distinct_trailing_space_database_supported'] = False
            task(spaced, 'bring_online', {'mode': 'NORMAL'})
            assert state(spaced) == 0
            check['passed'] = True
        except Exception as error:
            failure(phase, error)
        for timeout in (-1, 32767, 32768, 86400):
            phase = 'native-timeout-' + str(timeout)
            check = {'case': phase, 'passed': False}
            result['checks'].append(check)
            path = '/var/lib/firebird/data/owned_' + phase + '.fdb'
            service = None
            try:
                connect(path, create=True).close()
                if timeout == 32767:
                    # No existing attachment: the maximum permitted waiting
                    # period need not elapse for successful shutdown.
                    task(path, 'shutdown_database', {
                        'mode': 'MULTI', 'method': 'DENY_ATTACHMENTS',
                        'shutdown_timeout': timeout})
                    assert state(path) == 1
                    task(path, 'bring_online', {'mode': 'NORMAL'})
                else:
                    service = client._connect_server({'route': {
                        **route,
                        'credential_reference_id': 'owned-availability',
                        'principal_reference': 'owned-qa'}})
                    # Compare the actual native utility bound, deliberately
                    # outside the application validation layer, owned DB only.
                    try:
                        service.database.shutdown(
                            database=path, mode=native.ShutdownMode.MULTI,
                            method=native.ShutdownMethod.DENY_ATTACHMENTS,
                            timeout=timeout)
                    except native.Error as error:
                        check['native_rejection_codes'] = list(
                            status_codes(error))
                        assert check['native_rejection_codes']
                    else:
                        raise AssertionError('Native invalid timeout accepted')
                    assert state(path) == 0
                check['passed'] = True
            except Exception as error:
                failure(phase, error)
            finally:
                if service is not None:
                    try:
                        client._release_server(service)
                    except Exception as error:
                        failure('close-timeout-service', error)
        if browser_options is not None:
            result['browser_checks'] = []
            for scale in browser_options.font_scale or (100, 200, 300):
                phase = 'browser-' + str(scale)
                folder = browser_options.build_root / phase
                folder.mkdir(parents=True, exist_ok=False)
                path = ('/var/lib/firebird/data/owned_availability_browser_' +
                        str(scale) + '.fdb')
                try:
                    connect(path, create=True).close()
                    selected = SimpleNamespace(**{
                        **vars(browser_options), 'font_scale': [scale]})
                    result['browser_checks'].extend(browser_checks(
                        selected, {**route, 'database': path}, password,
                        container, folder, gate_kind='availability',
                        fixture_kind='firebird-availability-qualification'))
                except Exception as error:
                    failure(phase, error)
            if not result['browser_checks'] or not all(
                    item['passed'] for item in result['browser_checks']):
                result['failures'].append({'case': 'browser-qualification',
                                           'error_type': 'FailedBrowserGate'})
    except Exception as error:
        failure(phase, error)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as error:
                failure('close-owned-client', error)
        for handle in connections:
            try:
                handle.close()
            except Exception as error:
                failure('close-owned-attachment', error)
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                failure('remove-owned-server', error)
    result['complete'] = (len(result['checks']) == 54 and
                          not result['failures'] and
                          result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser', action='store_true')
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
    result = run(options.image, options if options.browser else None)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
