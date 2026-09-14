#!/usr/bin/env python3
"""Verify Firebird query bindings and native transaction outcomes."""

import argparse
import importlib.metadata
import itertools
import json
import subprocess
import time
import uuid
from pathlib import Path, PurePosixPath
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cdeadmin_firebird_admin_mapping_gate import (
    ADMINISTRATION, RelationalClientError, _create_client, _route_arguments,
)
from pgadmin.cdeadmin.security.secrets import SecretLease


def run(profiles, container, unicode_path=False):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    password = route.pop('password')
    route.update(credential_reference_id='owned-bindings-secret',
                 principal_reference='owned-bindings-principal')
    suffix = '_é_東京' if unicode_path else ''
    path = str(PurePosixPath(route['database']).parent /
               ('cde_bindings_' + uuid.uuid4().hex + suffix + '.fdb'))
    result = {'complete': False, 'cases': [], 'failures': [],
              'fixture_database': path, 'fixture_removed': False,
              'driver_version': importlib.metadata.version('firebird-driver'),
              'credential_values_exported': False}
    client = _create_client(SimpleNamespace(
        acquire_secret=lambda *_args: SecretLease(password)))
    handle = None
    requested = False

    def exists():
        check = subprocess.run(
            ['docker', 'exec', container, 'test', '-e', path],
            capture_output=True, check=False)
        if check.returncode not in (0, 1):
            raise RuntimeError('Cannot observe owned fixture existence')
        return check.returncode == 0

    def execute(source, parameters=()):
        token = client.execute(handle, {'source': source,
                                        'parameters': parameters})
        return client.describe_result(token)['payload']['rows']

    def run_request(request, asynchronous=False, expected_state='succeeded'):
        if not asynchronous:
            return client.describe_result(client.execute(handle, request))
        token = client.submit_query(handle, request)
        deadline = time.monotonic() + 30
        while True:
            receipt = client.describe_result(token)
            if receipt['complete']:
                assert receipt['payload']['execution_state'] == expected_state
                return receipt
            if time.monotonic() >= deadline:
                raise TimeoutError('Owned query timed out')
            time.sleep(0.01)

    def external_rows():
        other = driver.connect(password=password, **_route_arguments(
            {**route, 'database': path}, driver))
        try:
            with other.cursor() as cursor:
                cursor.execute('SELECT I, V FROM DATA ORDER BY I')
                return cursor.fetchall()
        finally:
            other.close()

    try:
        assert not exists()
        plan = ADMINISTRATION.plan({
            '_provider_route': route, 'resource_kind': 'database',
            'operation_id': 'create', 'target_resource': None,
            'draft': {'database_path': path}})
        requested = True
        ADMINISTRATION.apply(client, plan)
        handle = client.open_session({'route': {**route, 'database': path}})
        scalar_cases = [
            ('integer', 'INTEGER', 2147483647, '2147483647'),
            ('bigint', 'BIGINT', -9223372036854775808, '-9223372036854775808'),
            ('unicode', 'VARCHAR(80) CHARACTER SET UTF8',
             "é ' ? ; -- text", "é ' ? ; -- text"),
            ('null', 'INTEGER', None, 'None'),
            ('decimal', 'NUMERIC(18,4)', '1234567.0123', '1234567.0123'),
            ('boolean', 'BOOLEAN', True, 'True'),
            ('date', 'DATE', '2026-09-14', '2026-09-14'),
            ('timestamp', 'TIMESTAMP', '2026-09-14 12:34:56.1234',
             '2026-09-14 12:34:56.123400'),
        ]
        for name, cast, value, expected in scalar_cases:
            try:
                rows = execute('SELECT CAST(? AS ' + cast +
                               ') FROM RDB$DATABASE', [value])
                assert str(rows[0][0]) == expected
                result['cases'].append(name)
            except Exception as exc:
                result['failures'].append({'case': name,
                                           'error_type': type(exc).__name__})
            finally:
                client.control_transaction(handle, 'rollback')
        execute('CREATE TABLE DATA (I INTEGER PRIMARY KEY, '
                'V VARCHAR(80) CHARACTER SET UTF8)')
        client.control_transaction(handle, 'commit')
        execute('CREATE TABLE ARRAY_DATA (I INTEGER PRIMARY KEY, '
                'M INTEGER[0:1,3:4])')
        client.control_transaction(handle, 'commit')
        execute('INSERT INTO ARRAY_DATA (I, M) VALUES (?, ?)',
                [1, [[11, 12], [21, 22]]])
        array_rows = execute('SELECT M FROM ARRAY_DATA WHERE I = ?', [1])
        assert array_rows == [([[11, 12], [21, 22]],)]
        assert json.loads(json.dumps(array_rows)) == [
            [[[11, 12], [21, 22]]]]
        client.control_transaction(handle, 'rollback')
        assert execute('SELECT COUNT(*) FROM ARRAY_DATA') == [(0,)]
        client.control_transaction(handle, 'rollback')
        result['cases'].append('multidimensional-array-binding-and-rollback')
        source = 'INSERT INTO DATA (I, V) VALUES (?, ?)'
        execute(source, [1, "first ' ? ; value"])
        assert external_rows() == []
        client.control_transaction(handle, 'rollback')
        assert external_rows() == []
        result['cases'].append('insert-rollback')
        execute(source, [1, "first ' ? ; value"])
        client.control_transaction(handle, 'commit')
        assert external_rows() == [(1, "first ' ? ; value")]
        result['cases'].append('insert-commit')
        execute('UPDATE DATA SET V = ? WHERE I = ?', ['changed', 1])
        assert execute('SELECT V FROM DATA WHERE I = ?', [1]) == [('changed',)]
        client.control_transaction(handle, 'rollback')
        assert external_rows() == [(1, "first ' ? ; value")]
        result['cases'].append('update-rollback-and-binding-order')
        execute(source, [2, 'prior pending row'])
        for invalid in ({'first': 'private-canary', 'second': 3}, [3]):
            try:
                execute(source, invalid)
            except RelationalClientError as exc:
                assert 'private-canary' not in str(exc)
            else:
                raise AssertionError('Invalid binding unexpectedly executed')
            assert execute('SELECT COUNT(*) FROM DATA') == [(2,)]
            assert external_rows() == [(1, "first ' ? ; value")]
        client.control_transaction(handle, 'rollback')
        result['cases'].append('rejected-bindings-preserve-caller-transaction')
        execute(source, [2, 'pending before duplicate key'])
        try:
            execute(source, [1, 'private-diagnostic-canary'])
        except RelationalClientError as exc:
            assert 335544665 in exc.gds_codes
            assert 'private-diagnostic-canary' not in str(exc)
            assert '335544665' in str(exc)
        else:
            raise AssertionError('Duplicate primary key unexpectedly accepted')
        assert execute('SELECT COUNT(*) FROM DATA') == [(2,)]
        assert external_rows() == [(1, "first ' ? ; value")]
        client.control_transaction(handle, 'rollback')
        result['cases'].append('native-status-and-statement-atomicity')
        execute('DELETE FROM DATA WHERE I = ?', [1])
        client.control_transaction(handle, 'rollback')
        assert len(external_rows()) == 1
        result['cases'].append('delete-rollback')
        execute('DELETE FROM DATA WHERE I = ?', [1])
        client.control_transaction(handle, 'commit')
        assert external_rows() == []
        result['cases'].append('delete-commit')
        for asynchronous in (False, True):
            for index, invalid in enumerate((
                    '\vCOMMIT', 'COMMIT\vWORK', 'COMMIT\v', 'ROLLBACK\v')):
                execute(source, [17, 'must remain pending'])
                transaction_id = handle.main_transaction.info.id
                if asynchronous:
                    response = run_request({'source': invalid}, True, 'failed')
                    assert response['payload']['error']['native_status_codes']
                else:
                    try:
                        execute(invalid)
                    except RelationalClientError as exc:
                        assert exc.gds_codes
                    else:
                        raise AssertionError('Invalid native whitespace ran')
                assert handle.main_transaction.info.id == transaction_id
                assert execute('SELECT I FROM DATA') == [(17,)]
                assert external_rows() == []
                client.control_transaction(handle, 'rollback')
                assert external_rows() == []
                result['cases'].append('native-invalid-whitespace-' +
                                       str(asynchronous) + '-' + str(index))
        for asynchronous in (False, True):
            for action in ('COMMIT', 'ROLLBACK'):
                for work in ('', ' WORK'):
                    for suffix in ('', ' RETAIN', ' RETAIN SNAPSHOT'):
                        statement = action + work + suffix
                        case = (('async-' if asynchronous else 'sync-') +
                                statement)
                        try:
                            execute(source, [42, 'owned transaction SQL'])
                            request = {'source': '/* transaction */ ' +
                                       statement + '; -- end'}
                            receipt = run_request(request, asynchronous)
                            assert receipt['payload']['transaction_action'][
                                'native_call_made'] is True
                            assert handle.main_transaction.is_active() == bool(
                                suffix)
                            if suffix:
                                assert handle.main_transaction.info.id > 0
                            expected = ([(42, 'owned transaction SQL')]
                                        if action == 'COMMIT' else [])
                            assert external_rows() == expected
                            assert execute('SELECT COUNT(*) FROM DATA') == [
                                (len(expected),)]
                            result['cases'].append(case)
                        except Exception as exc:
                            result['failures'].append({
                                'case': case,
                                'error_type': type(exc).__name__})
                        finally:
                            client.control_transaction(handle, 'rollback')
                            execute('DELETE FROM DATA WHERE I = ?', [42])
                            client.control_transaction(handle, 'commit')
        for action in ('commit', 'rollback'):
            assert not handle.main_transaction.is_active()
            receipt = client.control_transaction(handle, action)
            assert receipt['native_call_made'] is False
            assert not handle.main_transaction.is_active()
            result['cases'].append('idle-button-' + action)
            for asynchronous, retaining in itertools.product(
                    (False, True), repeat=2):
                statement = action + (' RETAIN SNAPSHOT' if retaining else '')
                receipt = run_request({'source': statement}, asynchronous)
                assert receipt['payload']['transaction_action'][
                    'native_call_made'] is False
                assert not handle.main_transaction.is_active()
                result['cases'].append(
                    ('idle-async-' if asynchronous else 'idle-sync-') +
                    statement)
        for asynchronous in (False, True):
            for suffix in (
                    'READ ONLY SNAPSHOT',
                    'READ ONLY ISOLATION LEVEL SNAPSHOT TABLE STABILITY',
                    'READ ONLY READ COMMITTED RECORD_VERSION',
                    'READ ONLY READ COMMITTED NO RECORD_VERSION',
                    'READ ONLY READ COMMITTED READ CONSISTENCY',
                    'READ ONLY NO WAIT', 'READ ONLY WAIT LOCK TIMEOUT 3',
                    'READ ONLY NO AUTO UNDO IGNORE LIMBO',
                    'READ ONLY RESERVING DATA FOR SHARED READ',
                    'READ ONLY AUTO RELEASE TEMP BLOBID',
                    'READ ONLY RESTART REQUESTS', 'READ ONLY AUTO COMMIT'):
                case = (('async-start-' if asynchronous else 'sync-start-') +
                        suffix)
                try:
                    assert not handle.main_transaction.is_active()
                    execute('SELECT 1 FROM RDB$DATABASE')
                    handle.main_transaction._get_handle()
                    client.control_transaction(handle, 'rollback')
                    receipt = run_request({
                        'source': 'SET /* native */ TRANSACTION ' + suffix},
                        asynchronous)
                    assert receipt['payload']['transaction_action'][
                        'action'] == 'begin'
                    assert getattr(handle.main_transaction,
                                   '_TransactionManager__handle') is None
                    assert handle.main_transaction.info.is_read_only()
                    native_id = handle.main_transaction.info.id
                    assert execute('SELECT CURRENT_TRANSACTION '
                                   'FROM RDB$DATABASE') == [(native_id,)]
                    try:
                        execute('SET TRANSACTION READ WRITE')
                    except RelationalClientError as exc:
                        assert 'pending work' in str(exc)
                    else:
                        raise AssertionError('Active transaction replaced')
                    assert handle.main_transaction.info.id == native_id
                    result['cases'].append(case)
                except Exception as exc:
                    result['failures'].append({
                        'case': case, 'error_type': type(exc).__name__,
                        'native_status_codes': list(getattr(
                            exc, 'gds_codes', ()))})
                finally:
                    client.control_transaction(handle, 'rollback')
            for suffix in ('READ ONLY READ WRITE', 'NO WAIT LOCK TIMEOUT 1',
                           'MADE_UP_NATIVE'):
                case = ('async-invalid-start-' if asynchronous else
                        'sync-invalid-start-') + suffix
                try:
                    request = {'source': 'SET TRANSACTION ' + suffix}
                    if asynchronous:
                        receipt = run_request(request, True, 'failed')
                        assert receipt['payload']['error'][
                            'native_status_codes']
                    else:
                        try:
                            run_request(request)
                        except RelationalClientError as exc:
                            assert exc.gds_codes
                        else:
                            raise AssertionError(
                                'Invalid native start accepted')
                    assert not handle.main_transaction.is_active()
                    result['cases'].append(case)
                except Exception as exc:
                    result['failures'].append({
                        'case': case, 'error_type': type(exc).__name__})
                finally:
                    client.control_transaction(handle, 'rollback')
        closed = client.close_session(handle)
        handle = None
        assert closed['rollback_requested'] is False
        result['cases'].append('close-idle-session')
        handle = client.open_session({'route': {**route, 'database': path}})
        execute(source, [9, 'close must roll back'])
        assert external_rows() == []
        closed = client.close_session(handle)
        handle = None
        assert closed['rollback_requested'] is True
        assert external_rows() == []
        result['cases'].append('close-active-session-rolls-back')
        handle = client.open_session({'route': {**route, 'database': path}})
        execute(source, [10, 'pending before close failure'])
        with patch.object(handle, 'close', side_effect=RuntimeError(
                'private-close-failure-canary')):
            try:
                client.close_session(handle)
            except RelationalClientError as exc:
                assert 'private-close-failure-canary' not in str(exc)
            else:
                raise AssertionError('Injected close failure was hidden')
        assert not handle.is_closed()
        assert handle in client._connections
        assert not handle.main_transaction.is_active()
        assert external_rows() == []
        closed = client.close_session(handle)
        assert closed['connection_released'] is True
        assert closed['rollback_requested'] is False
        handle = None
        result['cases'].append('close-error-after-native-rollback-recovery')
        for asynchronous in (False, True):
            handle = client.open_session({'route': {
                **route, 'database': path}})
            cursor = handle.cursor()
            native_close = cursor.close

            def failed_result_close():
                if cursor._executed:
                    raise RuntimeError('private-result-close-canary')
                native_close()

            with patch.object(handle, 'cursor', return_value=cursor), \
                    patch.object(cursor, 'close',
                                 side_effect=failed_result_close):
                request = {'source': source + ' RETURNING I',
                           'parameters': [12, 'pending result cleanup']}
                if asynchronous:
                    response = run_request(request, True, 'failed')
                    assert response['payload']['error'][
                        'native_execution_completed'] is True
                    assert response['payload']['session_reuse_blocked']
                else:
                    try:
                        run_request(request)
                    except RelationalClientError as exc:
                        assert exc.native_execution_completed is True
                        assert 'private-result-close-canary' not in str(exc)
                    else:
                        raise AssertionError('Result cleanup failure hidden')
            assert handle.main_transaction.is_active()
            with handle.cursor() as observer:
                observer.execute('SELECT I FROM DATA')
                assert observer.fetchall() == [(12,)]
            assert external_rows() == []
            try:
                execute('SELECT 1 FROM RDB$DATABASE')
            except RelationalClientError as exc:
                assert 'result cleanup' in str(exc)
            else:
                raise AssertionError('Incomplete cleanup session was reused')
            closed = client.close_session(handle)
            handle = None
            assert closed['rollback_requested'] is True
            assert external_rows() == []
            result['cases'].append('native-result-close-failure-' +
                                   ('async' if asynchronous else 'sync'))
        for auto_commit, no_auto_undo, ignore_limbo in itertools.product(
                (False, True), repeat=3):
            flags = {'transaction_auto_commit': auto_commit,
                     'transaction_no_auto_undo': no_auto_undo,
                     'transaction_ignore_limbo': ignore_limbo}
            case = 'native-tpb-flags-' + '-'.join(
                str(int(value)) for value in flags.values())
            try:
                handle = client.open_session({'route': {
                    **route, 'database': path, **flags}})
                rows = execute(
                    'SELECT MON$AUTO_COMMIT, MON$AUTO_UNDO '
                    'FROM MON$TRANSACTIONS WHERE '
                    'MON$TRANSACTION_ID = CURRENT_TRANSACTION')
                assert rows == [(int(auto_commit), int(not no_auto_undo))]
                client.control_transaction(handle, 'rollback')
                execute(source, [43, 'native TPB flag test'])
                visible = [(43, 'native TPB flag test')] if auto_commit else []
                assert external_rows() == visible
                client.control_transaction(handle, 'rollback')
                assert external_rows() == visible
                result['cases'].append(case)
            except Exception as exc:
                result['failures'].append({
                    'case': case, 'error_type': type(exc).__name__})
            finally:
                if handle is not None:
                    client.close_session(handle)
                handle = client.open_session({'route': {
                    **route, 'database': path}})
                execute('DELETE FROM DATA WHERE I = ?', [43])
                client.control_transaction(handle, 'commit')
                client.close_session(handle)
                handle = None
        # Keep the prepared native transaction owned and recoverable throughout
        # this test. Never prepare a transaction in the user's sample database.
        prepared = driver.connect(password=password, **_route_arguments(
            {**route, 'database': path}, driver))
        detached = False
        prepared_id = None
        try:
            with prepared.cursor() as cursor:
                cursor.execute(source, [44, 'committed before prepare'])
            prepared.commit()
            with prepared.cursor() as cursor:
                cursor.execute('UPDATE DATA SET V = ? WHERE I = ?',
                               ['uncommitted prepared version', 44])
                assert cursor.rowcount == 1
                cursor.execute('SELECT CURRENT_TRANSACTION, V FROM DATA '
                               'WHERE I = 44')
                transaction_row = cursor.fetchone()
            prepared_id = prepared.main_transaction.info.id
            result['prepared_observation'] = {
                'transaction_id': prepared_id,
                'sql_transaction_id': transaction_row[0],
                'owned_pending_value_seen': transaction_row[1] ==
                'uncommitted prepared version'}
            assert transaction_row == (prepared_id,
                                       'uncommitted prepared version')
            native = prepared.main_transaction._tra
            native.prepare()
            # Detach the attachment with its prepared transaction still alive.
            # Closing the DB-API connection would explicitly roll it back;
            # releasing/disconnecting the remote transaction first also rolls
            # it back in Firebird 5's remote provider. Attachment detach keeps
            # prepared transactions in limbo for explicit recovery.
            prepared._att.detach()
            prepared._att = None
            detached = True
            native.release()
            prepared.main_transaction._tra = None
            observer = driver.connect(password=password, **_route_arguments(
                {**route, 'database': path}, driver))
            try:
                result['prepared_observation']['limbo_ids'] = (
                    observer.info.get_info(driver.DbInfoCode.LIMBO))
                assert prepared_id in result['prepared_observation'][
                    'limbo_ids']
            finally:
                observer.close()
            for ignore_limbo in (False, True):
                case = 'prepared-record-ignore-limbo-' + str(ignore_limbo)
                observed = {}
                try:
                    handle = client.open_session({'route': {
                        **route, 'database': path,
                        'transaction_ignore_limbo': ignore_limbo,
                        'transaction_access': 'READ',
                        'transaction_isolation': 'SNAPSHOT',
                        'transaction_lock_timeout': 0,
                        'statement_timeout_ms': 1000}})
                    try:
                        rows = execute('SELECT V FROM DATA WHERE I = ?', [44])
                    except RelationalClientError as exc:
                        observed['native_status_codes'] = list(exc.gds_codes)
                        assert ignore_limbo is False
                        assert 335544459 in exc.gds_codes
                    else:
                        observed['returned_committed_version'] = (
                            rows == [('committed before prepare',)])
                        assert ignore_limbo is True
                        assert rows == [('committed before prepare',)]
                    result['cases'].append(case)
                except Exception as exc:
                    result['failures'].append({
                        'case': case, 'error_type': type(exc).__name__,
                        'observed': observed})
                finally:
                    if handle is not None:
                        client.close_session(handle)
                        handle = None
        finally:
            try:
                if detached:
                    recovery = driver.connect(
                        password=password, **_route_arguments(
                            {**route, 'database': path}, driver))
                    try:
                        native = recovery._att.reconnect_transaction(
                            prepared_id.to_bytes(8, 'little'))
                        native.rollback()
                    finally:
                        recovery.close()
                elif prepared.main_transaction.is_active():
                    prepared.rollback()
            finally:
                prepared.close()
        assert external_rows() == [(44, 'committed before prepare')]
        result['cases'].append('prepared-transaction-explicit-rollback')
        snapshot_owner = driver.connect(
            password=password, **_route_arguments(
                {**route, 'database': path}, driver))
        try:
            snapshot_owner.begin(driver.tpb(
                driver.Isolation.SNAPSHOT,
                access_mode=driver.TraAccessMode.READ))
            with snapshot_owner.cursor() as cursor:
                cursor.execute('SELECT COUNT(*) FROM DATA')
                assert cursor.fetchone() == (1,)
            snapshot = snapshot_owner.main_transaction.info.snapshot_number
            handle = client.open_session({'route': {
                **route, 'database': path}})
            execute(source, [45, 'committed after snapshot'])
            client.control_transaction(handle, 'commit')
            assert len(external_rows()) == 2
            execute('SET TRANSACTION READ ONLY SNAPSHOT AT NUMBER ' +
                    str(snapshot))
            assert handle.main_transaction.info.snapshot_number == snapshot
            assert execute('SELECT COUNT(*) FROM DATA') == [(1,)]
            client.control_transaction(handle, 'rollback')
            result['cases'].append('native-shared-snapshot-visibility')
        finally:
            snapshot_owner.close()
        try:
            execute('SET TRANSACTION READ ONLY SNAPSHOT AT NUMBER ' +
                    str(snapshot))
        except RelationalClientError as exc:
            assert exc.gds_codes
        else:
            raise AssertionError('Expired shared snapshot was accepted')
        assert not handle.main_transaction.is_active()
        result['cases'].append('native-expired-snapshot-rejected')
        for release in ('temporary', 'session', 'client'):
            service_client = _create_client(SimpleNamespace(
                acquire_secret=lambda *_args: SecretLease(password)))
            try:
                server = service_client._connect_server({'route': {
                    **route, 'service_expected_database': path}})
                assert '5.0.4' in server.info.version
                if release == 'temporary':
                    service_client._forget_and_close(server)
                elif release == 'session':
                    receipt = service_client.close_session(server)
                    assert receipt['service_handle_released'] is True
                    assert receipt['rollback_requested'] is False
                else:
                    service_client.close()
                assert server._svc is None
                assert not service_client._connections
                assert not service_client._server_handles
                result['cases'].append('native-service-release-' + release)
            finally:
                service_client.close()
        for native_failure, detach_failure in itertools.product(
                (False, True), repeat=2):
            service_client = _create_client(SimpleNamespace(
                acquire_secret=lambda *_args: SecretLease(password)))
            server = service_client._connect_server({'route': route})
            runner = Mock(wraps=service_client.config.server_operation_runner)
            service_client.config = replace(
                service_client.config, server_operation_runner=runner)
            database = path + '.nonexistent' if native_failure else path
            try:
                with patch.object(service_client, '_connect_server',
                                  return_value=server), patch.object(
                                      server, 'close', wraps=server.close
                                  ) as close:
                    if detach_failure:
                        close.side_effect = RuntimeError('owned detach fault')
                    if native_failure:
                        try:
                            service_client.run_server_operation(
                                {'route': route}, 'database_statistics',
                                database, {'statistics_flags': ['HDR_PAGES']})
                        except RelationalClientError as exc:
                            assert 'operation failed' in str(exc)
                            receipt = exc.service_release
                        else:
                            raise AssertionError('Missing database accepted')
                    else:
                        observed = service_client.run_server_operation(
                            {'route': route}, 'database_statistics', database,
                            {'statistics_flags': ['HDR_PAGES']})
                        assert observed['server_completed'] is True
                        assert observed['output']
                        receipt = observed['service_release']
                    close.assert_called_once_with()
                    assert receipt['service_handle_released'] is (
                        not detach_failure)
                    assert (server in service_client._connections) is (
                        detach_failure)
                service_client.close()
                assert server._svc is None
                runner.assert_called_once()
                result['cases'].append(
                    f'native-service-outcome-{native_failure}-'
                    f'detach-failure-{detach_failure}')
            finally:
                service_client.close()
    except Exception as exc:
        result['failures'].append({'case': 'gate',
                                   'error_type': type(exc).__name__,
                                   'message': str(exc).replace(password,
                                                               '[redacted]')})
    finally:
        try:
            if handle is not None:
                client.close_session(handle)
        except Exception as exc:
            result['failures'].append({'case': 'close-session',
                                       'error_type': type(exc).__name__})
        try:
            client.close()
        except Exception as exc:
            result['failures'].append({'case': 'close-client',
                                       'error_type': type(exc).__name__})
        if requested:
            try:
                if exists():
                    cleanup_client = _create_client(SimpleNamespace(
                        acquire_secret=lambda *_args: SecretLease(password)))
                    try:
                        cleanup_client.drop_database(
                            {'route': {**route, 'database': path}},
                            path, 'firebird-drop-database')
                        assert not cleanup_client._connections
                    finally:
                        cleanup_client.close()
                result['fixture_removed'] = not exists()
            except Exception as exc:
                result['failures'].append({'case': 'cleanup',
                                           'error_type': type(exc).__name__})
    if len(result['cases']) != 113:
        result['failures'].append({'case': 'coverage-count',
                                   'expected': 113,
                                   'observed': len(result['cases'])})
    result['complete'] = (len(result['cases']) == 113 and
                          result['fixture_removed'] and not result['failures'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--container', default='cdeadmin-demo-firebird')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--unicode-path', action='store_true')
    args = parser.parse_args()
    result = run(args.profiles, args.container, args.unicode_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
