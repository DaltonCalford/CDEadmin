"""The isolation gate collects failures and releases only its owned fixture."""

from unittest.mock import MagicMock, Mock

import pytest
import firebird.driver as native

from tools import cdeadmin_firebird_route_isolation_gate as gate


@pytest.mark.parametrize('fault', [
    None, 'connect', 'identity', 'cleanup', 'cancellation'])
def test_gate_collects_failures_and_always_attempts_cleanup(
        monkeypatch, fault):
    container = 'a' * 64
    monkeypatch.setattr(gate, '_configure_client_library', Mock())
    monkeypatch.setattr(gate, 'docker', Mock(return_value=container.encode()))
    monkeypatch.setattr(gate, 'published_port', Mock(return_value=53050))
    monkeypatch.setattr(gate, 'failed_initialization_case', Mock(
        return_value={'native_attachment_released': True}))
    monkeypatch.setattr(gate, 'temporary_retention_case', Mock(
        return_value={'explicit_retry_detached': True}))
    monkeypatch.setattr(gate, 'failed_detach_case', Mock(
        return_value={'explicit_retry_detached': True}))
    monkeypatch.setattr(gate, 'service_interruption_case', Mock(
        return_value=[{'service_released': True}] * 4))
    monkeypatch.setattr(gate, 'opening_lifecycle_case', Mock(
        return_value=[{'explicit_close_released': True}] * 2))
    monkeypatch.setattr(gate, 'worker_interruption_case', Mock(
        return_value={'explicit_close_released': True}))
    monkeypatch.setattr(gate, 'query_diagnostics_case', Mock(
        return_value={'sqlstate_preserved': True}))
    monkeypatch.setattr(gate, 'native_cancellation_case', Mock(
        return_value={'complete': fault != 'cancellation'}))
    cleanup = Mock(side_effect=(
        RuntimeError('secret') if fault == 'cleanup' else None))
    monkeypatch.setattr(gate, 'remove_owned', cleanup)
    handle = MagicMock()
    handle.info.name = '/var/lib/firebird/data/owned_isolation.fdb'
    handle.cursor().__enter__().fetchone.return_value = (
        '5.0.4', 'UNSELECTED' if fault == 'identity' else 'SYSDBA', 'NONE')
    manager = MagicMock()
    manager.__enter__.return_value = handle
    connect = Mock(side_effect=[manager] + [
        RuntimeError('secret') if fault == 'connect' else manager] * 6)
    monkeypatch.setattr(native, 'connect', connect)
    service = MagicMock()
    service.__enter__().info.version = '5.0.4'
    monkeypatch.setattr(native, 'connect_server', Mock(return_value=service))
    original = native.driver_config
    result = gate.run('owned-image')
    cleanup.assert_called_once_with(container)
    assert connect.call_count == 7
    assert native.driver_config is original
    assert result['complete'] is (fault is None)
    assert len(result['failures']) == (
        6 if fault in ('identity', 'connect') else
        1 if fault in ('cleanup', 'cancellation') else 0)
    assert 'secret' not in str(result)
    assert result['owned_container_removed'] is (fault != 'cleanup')


def test_native_cancellation_uses_only_owned_in_memory_profile(monkeypatch):
    from tools import cdeadmin_firebird_query_cancellation_gate as cancellation
    runner = Mock(return_value={'complete': True})
    monkeypatch.setattr(cancellation, 'run_document', runner)
    route = {'host': '127.0.0.1', 'port': 55555, 'database': '/owned.fdb',
             'user': 'SYSDBA'}
    original = dict(route)
    result = gate.native_cancellation_case(route, 'private-password', 'a' * 64)
    runner.assert_called_once_with(
        {'profiles': [{**route, 'engine': 'firebird',
                       'password': 'private-password'}]},
        'a' * 64, application_path=True, registry_path=True)
    assert route == original and result == {'complete': True}
    assert 'private-password' not in str(result)


def test_cancellation_file_entry_point_preserves_options(monkeypatch):
    from tools import cdeadmin_firebird_query_cancellation_gate as cancellation
    profiles = Mock()
    profiles.read_text.return_value = '{"profiles": []}'
    runner = Mock(return_value={'complete': False})
    monkeypatch.setattr(cancellation, 'run_document', runner)
    assert cancellation.run(profiles, 'owned-container', True, True) == {
        'complete': False}
    runner.assert_called_once_with({'profiles': []}, 'owned-container',
                                   True, True)


@pytest.mark.parametrize('action', ['rollback', 'commit'])
@pytest.mark.parametrize('path', ['registered-provider', 'provider-client',
                                  'native-driver'])
def test_cancellation_finality_uses_selected_public_path(action, path):
    from tools import cdeadmin_firebird_query_cancellation_gate as cancellation
    client, handle, binding = Mock(), Mock(), Mock()
    binding.instance.control_transaction.return_value = {'provider_payload': {
        'driver_observation_only': True,
        'finality_interpreted_by_common_code': False}}
    result = cancellation.finish_case_transaction(
        client, handle, action, path != 'native-driver',
        binding if path == 'registered-provider' else None, 'owned-session')
    assert result == path
    if path == 'registered-provider':
        binding.instance.control_transaction.assert_called_once_with({
            'session_id': 'owned-session', 'action': action})
        client.control_transaction.assert_not_called()
        assert not handle.mock_calls
    elif path == 'provider-client':
        client.control_transaction.assert_called_once_with(handle, action)
        assert not handle.mock_calls
    else:
        getattr(handle, action).assert_called_once_with()
        client.control_transaction.assert_not_called()


@pytest.mark.parametrize('observation', [
    {'driver_observation_only': False,
     'finality_interpreted_by_common_code': False},
    {'driver_observation_only': True,
     'finality_interpreted_by_common_code': True},
])
def test_cancellation_gate_refuses_common_finality_claims(observation):
    from tools import cdeadmin_firebird_query_cancellation_gate as cancellation
    binding = Mock()
    binding.instance.control_transaction.return_value = {
        'provider_payload': observation}
    with pytest.raises(AssertionError):
        cancellation.finish_case_transaction(
            Mock(), Mock(), 'commit', True, binding, 'owned-session')
