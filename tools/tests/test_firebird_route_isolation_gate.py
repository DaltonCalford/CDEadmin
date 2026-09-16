"""The isolation gate collects failures and releases only its owned fixture."""

from unittest.mock import MagicMock, Mock

import pytest
import firebird.driver as native

from tools import cdeadmin_firebird_route_isolation_gate as gate


@pytest.mark.parametrize('fault', [None, 'connect', 'identity', 'cleanup'])
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
        1 if fault == 'cleanup' else 0)
    assert 'secret' not in str(result)
    assert result['owned_container_removed'] is (fault != 'cleanup')
