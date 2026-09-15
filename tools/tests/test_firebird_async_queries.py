"""Firebird query submission, cancellation races and session ownership."""

import threading
from dataclasses import replace
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest

WEB = Path(__file__).resolve().parents[2] / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.firebird.provider import PROFILE  # noqa: E402
from pgadmin.cdeadmin.providers.firebird.query_client import (  # noqa: E402
    FirebirdQueryClient,
)
from pgadmin.cdeadmin.providers.firebird.query_parameters import (  # noqa: E402
    normalize_parameters,
)
from pgadmin.cdeadmin.sdk.relational import (  # noqa: E402
    RelationalClientConfig, RelationalClientError,
)


@pytest.fixture
def rig():
    entered, finish = threading.Event(), threading.Event()
    module = SimpleNamespace(connect=Mock(), CancelType=SimpleNamespace(
        RAISE=3, DISABLE=1, ENABLE=2))
    client = FirebirdQueryClient(RelationalClientConfig(
        PROFILE, 'owned-driver', 'SELECT 1 FROM RDB$DATABASE',
        lambda route: {}, lambda handle, request: [],
        query_parameter_normalizer=normalize_parameters), module=module)
    handle = Mock()
    cursor = handle.cursor.return_value
    cursor.description = [('V', int)]
    cursor.rowcount = 1
    cursor.fetchall.return_value = [(42,)]

    def execute(*_args):
        entered.set()
        assert finish.wait(5), 'Test did not release owned query worker'

    cursor.execute.side_effect = execute
    client._connections.append(handle)
    value = SimpleNamespace(client=client, handle=handle, cursor=cursor,
                            entered=entered, finish=finish, module=module)
    yield value
    finish.set()
    for query in client._queries:
        if query.worker.ident is not None:
            query.worker.join(5)
            assert not query.worker.is_alive()
    client.close()


def submit(rig):
    query = rig.client.submit_query(rig.handle, {
        'source': 'SELECT ? FROM RDB$DATABASE', 'parameters': [42]})
    assert rig.entered.wait(2)
    return query


def service_handle(rig):
    handle = SimpleNamespace(_svc=object())
    handle.close = Mock(side_effect=lambda: setattr(handle, '_svc', None))
    rig.client._server_connector = Mock()
    with patch.object(rig.client, '_invoke_connector', return_value=handle):
        opened = rig.client._connect_server({'route': {'host': 'test'}})
        assert opened is handle
    return handle


@pytest.mark.parametrize('release', ['temporary', 'client', 'session'])
def test_service_release_does_not_use_database_transaction_methods(
        rig, release):
    handle = service_handle(rig)
    if release == 'temporary':
        rig.client._forget_and_close(handle)
    elif release == 'session':
        receipt = rig.client.close_session(handle)
        assert receipt['service_handle_released'] is True
        assert receipt['rollback_requested'] is False
    else:
        rig.client.close()
    handle.close.assert_called_once_with()
    assert handle not in rig.client._connections
    assert id(handle) not in rig.client._server_handles


@pytest.mark.parametrize('failure', ['exception', 'still_attached'])
def test_service_release_failure_retains_owner_for_explicit_retry(
        rig, failure):
    handle = service_handle(rig)
    handle.close.side_effect = (
        RuntimeError('credential-canary') if failure == 'exception' else None)
    with pytest.raises(RelationalClientError) as caught:
        rig.client._forget_and_close(handle)
    assert 'credential-canary' not in str(caught.value)
    assert handle in rig.client._connections
    assert id(handle) in rig.client._server_handles
    handle.close.side_effect = lambda: setattr(handle, '_svc', None)
    rig.client._forget_and_close(handle)
    assert handle not in rig.client._connections


def test_failed_service_detach_blocks_client_close_until_release(rig):
    handle = service_handle(rig)
    handle.close.side_effect = RuntimeError('credential-canary')
    with pytest.raises(RelationalClientError, match='service release'):
        rig.client.close()
    assert not rig.client._closed
    assert handle in rig.client._connections
    handle.close.side_effect = lambda: setattr(handle, '_svc', None)
    rig.client.close()
    assert rig.client._closed


def test_failed_service_release_does_not_skip_other_owned_handles(rig):
    failed = service_handle(rig)
    healthy = service_handle(rig)
    failed.close.side_effect = RuntimeError('credential-canary')
    with pytest.raises(RelationalClientError, match='service release'):
        rig.client.close()
    failed.close.assert_called_once_with()
    healthy.close.assert_called_once_with()
    assert rig.client._connections == [failed]
    assert rig.client._server_handles == {id(failed)}
    failed.close.side_effect = lambda: setattr(failed, '_svc', None)
    rig.client.close()
    assert rig.client._closed


def test_service_execution_blocks_whole_client_release(rig):
    def native_service(*_args):
        with pytest.raises(RelationalClientError, match='native operations'):
            rig.client.close()
        assert not rig.client._closed
        return {'server_completed': True}

    with patch('pgadmin.cdeadmin.sdk.relational.RelationalDBAPIClient.'
               'run_server_operation', side_effect=native_service):
        result = rig.client.run_server_operation(
            {'route': {'host': 'test'}}, 'database_statistics', 'owned', {})
    assert result == {'server_completed': True}
    assert rig.client._native_operations == 0


@pytest.mark.parametrize('operation', [
    'activate_shadow', 'database_statistics', 'backup_logical',
    'restore_logical', 'validate_database', 'bring_online', 'set_write_mode'])
def test_task_role_is_attached_without_unsupported_start_spb_role(
        rig, operation):
    request = {'route': {'host': 'exact', 'role': 'DEFAULT_ROLE'}}
    options = {'role': 'RECOVERY_OPERATOR', 'mode': 'owned'}
    with patch('pgadmin.cdeadmin.sdk.relational.RelationalDBAPIClient.'
               'run_server_operation', return_value={}) as native:
        rig.client.run_server_operation(request, operation, 'owned', options)
    native.assert_called_once_with(
        {'route': {'host': 'exact', 'role': 'RECOVERY_OPERATOR'}},
        operation, 'owned', {'mode': 'owned'})
    assert request['route']['role'] == 'DEFAULT_ROLE'
    assert options['role'] == 'RECOVERY_OPERATOR'


def test_service_preflight_uses_the_reviewed_task_role(rig):
    handle = service_handle(rig)
    plan = {'provider_payload': {'route': {'host': 'exact'}, 'compiled': {
        'driver_operation': 'firebird-service',
        'options': {'role': 'RECOVERY_OPERATOR'}}}}
    with patch('pgadmin.cdeadmin.sdk.relational.RelationalDBAPIClient.'
               'plan_admin_operation', return_value=plan), patch.object(
                   rig.client, '_connect_server',
                   return_value=handle) as open_:
        assert rig.client.plan_admin_operation({}) is plan
    open_.assert_called_once_with({'route': {'host': 'exact',
                                             'role': 'RECOVERY_OPERATOR'}})
    assert plan['provider_payload']['route'] == {'host': 'exact'}


@pytest.mark.parametrize('role', [1, False, [], {}, 'bad\x00role'])
def test_invalid_service_role_fails_before_attachment(rig, role):
    with patch.object(rig.client, '_connect_server') as open_:
        with pytest.raises(RelationalClientError, match='role is invalid'):
            rig.client.run_server_operation(
                {'route': {}}, 'activate_shadow', 'owned', {'role': role})
    open_.assert_not_called()


@pytest.mark.parametrize('service', [False, True])
def test_service_plan_authenticates_before_retention_without_starting_task(
        rig, service):
    handle = service_handle(rig)
    plan = {'provider_payload': {'route': {'host': 'exact'}, 'compiled': {
        'driver_operation': 'firebird-service' if service else None}}}
    with patch('pgadmin.cdeadmin.sdk.relational.RelationalDBAPIClient.'
               'plan_admin_operation', return_value=plan), patch.object(
                   rig.client, '_connect_server',
                   return_value=handle) as open_:
        assert rig.client.plan_admin_operation({}) is plan
    if service:
        open_.assert_called_once_with({'route': {'host': 'exact'}})
        handle.close.assert_called_once_with()
    else:
        open_.assert_not_called()
        handle.close.assert_not_called()


@pytest.mark.parametrize('failure', ['credentials', 'release'])
def test_service_preflight_cannot_return_a_plan_after_auth_or_release_failure(
        rig, failure):
    handle = service_handle(rig)
    plan = {'provider_payload': {'route': {}, 'compiled': {
        'driver_operation': 'firebird-service'}}}
    if failure == 'release':
        handle.close.side_effect = RuntimeError('detach-canary')
    with patch('pgadmin.cdeadmin.sdk.relational.RelationalDBAPIClient.'
               'plan_admin_operation', return_value=plan), patch.object(
                   rig.client, '_connect_server', return_value=handle,
                   side_effect=(RelationalClientError('credentials missing')
                                if failure == 'credentials' else None)):
        with pytest.raises(RelationalClientError):
            rig.client.plan_admin_operation({})
    assert rig.client._native_operations == 0
    assert handle in rig.client._connections
    handle.close.side_effect = lambda: setattr(handle, '_svc', None)


@pytest.mark.parametrize('detach_failure', [False, True])
def test_attachment_hooks_run_after_ownership_and_failure_stays_owned(
        rig, detach_failure):
    handle = SimpleNamespace(_svc=object())
    handle.close = Mock(side_effect=(RuntimeError('detach-canary')
                        if detach_failure else
                        lambda: setattr(handle, '_svc', None)))

    def attached(value):
        assert value in rig.client._connections
        assert id(value) in rig.client._server_handles
        raise RuntimeError('hook-credential-canary')

    rig.client._service_attached = attached
    rig.client._server_connector = Mock()
    with patch.object(rig.client, '_invoke_connector', return_value=handle):
        with pytest.raises(RelationalClientError) as caught:
            rig.client._connect_server({'route': {}})
    assert 'canary' not in str(caught.value)
    assert (handle in rig.client._connections) is detach_failure
    assert caught.value.service_release['service_handle_released'] is (
        not detach_failure)
    handle.close.side_effect = lambda: setattr(handle, '_svc', None)


@pytest.mark.parametrize('detach_failure', [False, True])
@pytest.mark.parametrize('outcome', [
    'returned', 'native_error', 'driver_error', 'foreign_error', 'invalid'])
def test_service_outcome_is_not_replaced_by_detach_failure(
        rig, detach_failure, outcome):
    handle = service_handle(rig)
    observation = {'server_completed': True, 'output': ['native result']}
    native_error = RelationalClientError('native operation rejected')
    native_error.gds_codes = (335544344,)
    runner = Mock(return_value=(observation if outcome == 'returned' else []))
    if outcome == 'native_error':
        runner.side_effect = native_error
    elif outcome == 'driver_error':
        driver_error = RuntimeError('credential-canary')
        driver_error.gds_codes = (337117261,)
        runner.side_effect = driver_error
    elif outcome == 'foreign_error':
        runner.side_effect = RuntimeError('credential-canary')
    rig.client.config = replace(rig.client.config,
                                server_operation_runner=runner)
    if detach_failure:
        handle.close.side_effect = RuntimeError('credential-canary')
    with patch.object(rig.client, '_connect_server', return_value=handle):
        if outcome == 'returned':
            result = rig.client.run_server_operation(
                {'route': {}}, 'database_statistics', ' owned ', {})
            assert result['server_completed'] is True
            assert result['output'] == ['native result']
            assert 'service_release' not in observation
            receipt = result['service_release']
        else:
            with pytest.raises(RelationalClientError) as caught:
                rig.client.run_server_operation(
                    {'route': {}}, 'database_statistics', ' owned ', {})
            if outcome == 'native_error':
                assert caught.value is native_error
                assert caught.value.gds_codes == (335544344,)
            elif outcome == 'invalid':
                assert 'invalid result' in str(caught.value)
            else:
                assert ('Firebird service operation failed (RuntimeError'
                        in str(caught.value))
                assert 'Do not automatically replay' in str(caught.value)
                assert caught.value.gds_codes == (
                    (337117261,) if outcome == 'driver_error' else ())
                if outcome == 'driver_error':
                    assert 'native status 337117261' in str(caught.value)
            receipt = caught.value.service_release
            assert 'credential-canary' not in str(caught.value)
    runner.assert_called_once_with(handle, 'database_statistics', 'owned', {})
    handle.close.assert_called_once_with()
    assert receipt['service_handle_released'] is not detach_failure
    assert 'credential-canary' not in repr(receipt)
    assert (handle in rig.client._connections) is detach_failure
    if detach_failure:
        assert 'Do not replay' in receipt['message']
        handle.close.side_effect = lambda: setattr(handle, '_svc', None)
        rig.client.close()
        runner.assert_called_once()


def complete(rig, query):
    rig.finish.set()
    query.worker.join(5)
    assert not query.worker.is_alive()
    return rig.client.describe_result(query)


def test_submission_returns_while_native_execution_is_running(rig):
    query = submit(rig)
    pending = rig.client.describe_result(query)
    assert pending['complete'] is False
    assert pending['payload']['execution_state'] == 'running'
    result = complete(rig, query)
    assert result['complete'] is True
    assert result['payload']['rows'] == [(42,)]
    assert result['payload']['execution_state'] == 'succeeded'
    rig.cursor.execute.assert_called_once_with(
        'SELECT ? FROM RDB$DATABASE', (42,))
    rig.cursor.close.assert_called_once_with()


def test_cursor_release_failure_is_not_reported_as_success_or_replayed(rig):
    rig.cursor.close.side_effect = RuntimeError('credential-canary')
    query = submit(rig)
    result = complete(rig, query)
    assert result['payload']['execution_state'] == 'failed'
    error = result['payload']['error']
    assert error['native_execution_completed'] is True
    assert result['payload']['session_reuse_blocked'] is True
    assert 'Do not replay' in error['message']
    assert 'credential-canary' not in str(result)
    assert rig.client._tokens and not rig.client._tokens[-1].closed
    rig.client.describe_result(query)
    rig.cursor.execute.assert_called_once()
    rig.handle.commit.assert_not_called()
    rig.handle.rollback.assert_not_called()
    with pytest.raises(RelationalClientError, match='result cleanup'):
        rig.client.submit_query(rig.handle, {'source': 'SELECT 42'})
    rig.cursor.close.side_effect = None
    receipt = rig.client.close_session(rig.handle)
    assert receipt['connection_released'] is True


def test_synchronous_cursor_release_can_be_retried_without_execution(rig):
    rig.finish.set()
    token = rig.client.execute(rig.handle, {'source': 'SELECT 42'})
    rig.cursor.close.side_effect = RuntimeError('credential-canary')
    with pytest.raises(RelationalClientError) as caught:
        rig.client.describe_result(token)
    assert caught.value.native_execution_completed is True
    assert not token.closed
    rig.cursor.close.side_effect = None
    assert rig.client.describe_result(token)['payload']['rows'] == [(42,)]
    assert token.closed
    rig.cursor.execute.assert_called_once()


def test_successful_database_drop_forgets_its_released_attachment(rig):
    with patch.object(rig.client, '_connect', return_value=rig.handle):
        receipt = rig.client.drop_database(
            {'route': {'database': 'owned.fdb'}},
            'owned.fdb', 'firebird-drop-database')
    rig.handle.drop_database.assert_called_once_with()
    assert receipt['driver_returned'] is True
    assert rig.handle not in rig.client._connections
    rig.handle.commit.assert_not_called()


def test_failed_database_drop_retains_owner_for_cleanup_retry(rig):
    rig.handle.drop_database.side_effect = RuntimeError('owned drop failure')
    rig.handle.close.side_effect = RuntimeError('owned close failure')
    with patch.object(rig.client, '_connect', return_value=rig.handle):
        with pytest.raises(RuntimeError, match='owned drop failure'):
            rig.client.drop_database({'route': {'database': 'owned.fdb'}},
                                     'owned.fdb', 'firebird-drop-database')
    assert rig.handle in rig.client._connections
    rig.handle.close.side_effect = None


@pytest.mark.parametrize('operation', [
    lambda r: r.client.submit_query(r.handle, {'source': 'SELECT 2'}),
    lambda r: r.client.execute(r.handle, {'source': 'SELECT 2'}),
    lambda r: r.client.describe_transaction(r.handle),
    lambda r: r.client.control_transaction(r.handle, 'commit'),
    lambda r: r.client.control_transaction(r.handle, 'rollback'),
    lambda r: r.client.close_session(r.handle),
    lambda r: r.client.list_resources({'_provider_session_handle': r.handle}),
    lambda r: r.client.apply_admin_operation(
        {'_provider_session_handle': r.handle}),
    lambda r: r.client.read_admin_rows({'_provider_session_handle': r.handle}),
    lambda r: r.client.close(),
])
def test_busy_session_rejects_competing_actions_before_native_calls(
        rig, operation):
    query = submit(rig)
    before = list(rig.handle.mock_calls)
    with pytest.raises(RelationalClientError, match='running|finish'):
        operation(rig)
    assert rig.handle.mock_calls == before
    complete(rig, query)


def test_cancel_acceptance_does_not_claim_cancellation_or_finality(rig):
    query = submit(rig)
    assert rig.client.cancel(query) is True
    pending = rig.client.describe_result(query)
    assert pending['complete'] is False
    assert pending['payload']['cancel_requested'] is True
    rig.handle.commit.assert_not_called()
    rig.handle.rollback.assert_not_called()
    result = complete(rig, query)
    assert result['payload']['execution_state'] == 'succeeded'
    assert rig.handle._att.cancel_operation.call_args_list == [
        call(3), call(1), call(2)]
    assert rig.client.cancel(query) is False


def test_native_cancellation_observed_with_status_without_query_text(rig):
    error = RuntimeError('private-query-text')
    error.gds_codes = (335544794,)
    rig.cursor.fetchall.side_effect = error
    query = submit(rig)
    rig.client.cancel(query)
    result = complete(rig, query)
    assert result['payload']['execution_state'] == 'cancelled'
    assert result['payload']['error']['native_status_codes'] == [335544794]
    assert 'private-query-text' not in str(result)
    rig.handle.rollback.assert_not_called()


def test_native_error_is_terminal_and_preserves_status_identity(rig):
    error = RuntimeError('private-bound-value')
    error.gds_codes = (335544665,)
    rig.cursor.fetchall.side_effect = error
    query = submit(rig)
    result = complete(rig, query)
    assert result['complete'] is True
    assert result['payload']['execution_state'] == 'failed'
    assert result['payload']['error']['native_status_codes'] == [335544665]
    assert 'private-bound-value' not in str(result)
    rig.handle.rollback.assert_not_called()


def test_cancel_delivery_error_is_not_finality_and_cleanup_still_runs(rig):
    query = submit(rig)
    rig.handle._att.cancel_operation.side_effect = [RuntimeError('secret'),
                                                    None, None]
    with pytest.raises(RelationalClientError, match='unknown') as caught:
        rig.client.cancel(query)
    assert 'secret' not in str(caught.value)
    assert not rig.client.describe_result(query)['complete']
    assert complete(rig, query)['payload']['execution_state'] == 'succeeded'
    assert not rig.client._state(rig.handle).cancellation_state_unknown


def test_failed_cancel_cleanup_blocks_reuse_but_allows_explicit_close(rig):
    query = submit(rig)
    rig.handle._att.cancel_operation.side_effect = [
        None, RuntimeError('secret')]
    rig.client.cancel(query)
    result = complete(rig, query)
    assert result['payload']['session_reuse_blocked'] is True
    assert 'secret' not in str(result)
    with pytest.raises(RelationalClientError, match='explicitly reconnect'):
        rig.client.execute(rig.handle, {'source': 'SELECT 2'})
    rig.client.close_session(rig.handle)
    assert rig.handle not in rig.client._connections


@pytest.mark.parametrize('payload', [
    {'source': ''}, {'source': 'SELECT ?', 'parameters': {'secret': 42}},
])
def test_invalid_submission_does_not_start_worker(rig, payload):
    with pytest.raises(RelationalClientError):
        rig.client.submit_query(rig.handle, payload)
    assert not rig.client._queries
    assert not rig.entered.is_set()


def test_thread_start_failure_releases_submission_slot(rig):
    with patch.object(threading.Thread, 'start', side_effect=RuntimeError):
        with pytest.raises(RuntimeError):
            rig.client.submit_query(rig.handle, {'source': 'SELECT 1'})
    assert not rig.client._queries
    assert rig.client._state(rig.handle).query is None


def test_other_attachment_is_usable_while_query_runs(rig):
    query = submit(rig)
    other = Mock()
    other.cursor.return_value.description = []
    rig.client._connections.append(other)
    token = rig.client.execute(other, {'source': 'SELECT 2'})
    assert rig.client.describe_result(token)['complete']
    assert not query.done
    complete(rig, query)


def test_retained_identity_uses_attachment_info_without_starting_transaction(
        rig):
    rig.handle.info.firebird_version = '5.0.4'
    identity = rig.client.runtime_identity({}, rig.handle)
    assert identity['version'] == '5.0.4'
    assert not rig.handle.main_transaction.mock_calls
    rig.handle.cursor.assert_not_called()


def test_cancel_of_finished_operation_cannot_interrupt_next_query(rig):
    first = submit(rig)
    complete(rig, first)
    rig.finish.clear()
    rig.entered.clear()
    second = submit(rig)
    assert rig.client.cancel(first) is False
    rig.handle._att.cancel_operation.assert_not_called()
    assert not second.done
    complete(rig, second)


def test_explicit_close_drops_attachment_state_and_result_storage(rig):
    query = submit(rig)
    complete(rig, query)
    rig.client.close_session(rig.handle)
    assert id(rig.handle) not in rig.client._attachment_states
    assert not rig.client._queries
    assert not rig.client._tokens


def test_opening_connection_does_not_block_cancel_and_prevents_global_close(
        rig):
    query = submit(rig)
    with rig.client._connecting():
        assert rig.client.cancel(query) is True
        with pytest.raises(RelationalClientError, match='still opening'):
            rig.client.close()
    assert rig.client._opening == 0
    complete(rig, query)


def test_closed_client_does_not_admit_new_connections_or_queries(rig):
    rig.client.close()
    for callback in (
        lambda: rig.client.open_session({'route': {}}),
        lambda: rig.client._connect_server({'route': {}}),
        lambda: rig.client.submit_query(rig.handle, {'source': 'SELECT 1'}),
    ):
        with pytest.raises(RelationalClientError, match='client is closed'):
            callback()
    rig.module.connect.assert_not_called()


@pytest.mark.parametrize('method', [
    'runtime_identity', 'list_resources', 'apply_admin_operation',
    'read_admin_rows',
])
def test_temporary_native_operations_protect_handles_from_global_close(
        rig, method):
    from pgadmin.cdeadmin.sdk.relational import RelationalDBAPIClient

    def native(*_args):
        assert rig.client._native_operations == 1
        with pytest.raises(RelationalClientError, match='still running'):
            rig.client.close()
        raise RuntimeError('owned native failure')

    with patch.object(RelationalDBAPIClient, method, side_effect=native):
        with pytest.raises(RuntimeError, match='owned native failure'):
            getattr(rig.client, method)({})
    assert rig.client._native_operations == 0
    assert rig.handle in rig.client._connections


def test_provider_publishes_operation_before_completion_and_keeps_busy_handles(
        rig):
    from tools.tests.test_cdeadmin_actual_engine_pilots import context
    from pgadmin.cdeadmin.providers.firebird.provider import FirebirdProvider
    provider = FirebirdProvider(context(PROFILE), Mock(), rig.client)
    provider._sessions['owned-session'] = SimpleNamespace(handle=rig.handle)
    operation = provider.execute({'session_id': 'owned-session',
                                  'execution_id': 'owned-execution',
                                  'source': 'SELECT 1 FROM RDB$DATABASE'})
    assert rig.entered.wait(2)
    assert operation['terminal'] is False
    pending = provider.describe_result(operation)
    assert pending['complete'] is False
    assert pending['execution_id'] == 'owned-execution'
    receipt = provider.cancel(operation)
    assert receipt['provider_receipt']['cancel_request_accepted'] is True
    assert receipt['terminal'] is False
    with pytest.raises(RelationalClientError, match='finish'):
        provider.close()
    assert 'owned-session' in provider._sessions
    assert operation['operation_id'] in provider._operations
    complete(rig, rig.client._queries[0])
    result = provider.describe_result(operation)
    assert result['complete'] is True
    assert provider.get_operation(operation)['terminal'] is True
    provider.close()
    assert not provider._sessions
    assert not provider._operations


@pytest.mark.parametrize('codes,closed', [
    ((335544856, 335545131), True),
    ((335544856, 335545131), False),
    ((335544721,), True),
])
def test_only_confirmed_native_shutdown_can_release_after_rollback_error(
        rig, codes, closed):
    from dataclasses import replace
    failure = RuntimeError('private-shutdown-detail')
    failure.gds_codes = codes
    rig.handle.rollback.side_effect = failure
    rig.handle.is_closed.return_value = closed
    releaser = Mock(side_effect=RuntimeError('private-cleanup-detail'))
    rig.client.config = replace(rig.client.config, session_releaser=releaser)
    if 335544856 in codes and closed:
        result = rig.client.close_session(rig.handle)
        assert result['native_attachment_shutdown_observed']
        assert result['rollback_completion_confirmed'] is False
        assert result['native_status_codes'] == list(codes)
        assert 'private' not in str(result)
        assert rig.handle not in rig.client._connections
    else:
        with pytest.raises((RuntimeError, RelationalClientError)):
            rig.client.close_session(rig.handle)
        assert rig.handle in rig.client._connections
        if 335544856 not in codes:
            releaser.assert_not_called()
        rig.handle.rollback.side_effect = None
        rig.handle.is_closed.return_value = True
        releaser.side_effect = None
