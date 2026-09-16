"""Sweep baseline ownership, independent observations and failure inventory."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import pytest
from firebird.driver.config import DriverConfig

from tools import cdeadmin_firebird_creation_sweep_gate as gate


@pytest.mark.parametrize('mode', gate.cache.SERVER_MODES)
@pytest.mark.parametrize('interval', gate.INTERVALS)
def test_interval_creation_and_reopen(monkeypatch, mode, interval):
    handles = [MagicMock(), MagicMock()]
    expected = 20000 if interval is None else interval
    for handle in handles:
        handle.info.sweep_interval = expected
        cursor = handle.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (expected,)
        cursor.fetchall.return_value = [(42,)]
    native = SimpleNamespace(driver_config=DriverConfig('owned-sweep-test'),
                             create_database=Mock(return_value=handles[0]),
                             connect=Mock(return_value=handles[1]))
    presence = Mock(return_value=False)
    monkeypatch.setattr(gate.cache, 'file_present', presence)
    result = gate.creation_case(
        native, 'a' * 64, 50000, 'owned-secret', mode, interval)
    assert presence.call_count == 2
    if interval == -1:
        assert result['rejected_before_native_create_by_driver']
        native.create_database.assert_not_called()
        native.connect.assert_not_called()
    else:
        assert result['created'] == result['reopened']
        assert result['created']['info_interval'] == expected
        assert result['committed_rows_preserved'] and result['dropped']
        assert native.create_database.call_args.kwargs['overwrite'] is False
        handles[0].close.assert_called_once()
        handles[1].drop_database.assert_called_once()
        handles[1].close.assert_not_called()


@pytest.mark.parametrize('bad_source', ['info', 'monitor'])
def test_observation_mismatch_closes_handle_and_fails(monkeypatch, bad_source):
    handle = MagicMock()
    handle.info.sweep_interval = 128 if bad_source == 'info' else 0
    cursor = handle.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (128 if bad_source == 'monitor' else 0,)
    native = SimpleNamespace(driver_config=DriverConfig('owned-sweep-test'),
                             create_database=Mock(return_value=handle))
    monkeypatch.setattr(gate.cache, 'file_present', Mock(return_value=False))
    with pytest.raises(AssertionError):
        gate.creation_case(native, 'a' * 64, 50000, 'secret', 'Super', 0)
    handle.close.assert_called_once()
    handle.drop_database.assert_not_called()


@pytest.mark.parametrize('case_fails', [False, True])
@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_full_failure_inventory_and_owned_cleanup(
        monkeypatch, case_fails, cleanup_fails):
    import firebird.driver as native
    monkeypatch.setattr(native, 'driver_config', DriverConfig('owned-sweep'))
    handle = MagicMock()
    handle.__enter__.return_value = handle
    handle.cursor.return_value.__enter__.return_value.fetchone.return_value = (
        '5.0.4',)
    monkeypatch.setattr(native, 'connect', Mock(return_value=handle))
    monkeypatch.setattr(gate.cache, '_configure_client_library', Mock())
    start = Mock(return_value=('a' * 64).encode())
    monkeypatch.setattr(gate.cache, 'docker', start)
    monkeypatch.setattr(gate.cache, 'published_port', Mock(return_value=50000))
    cleanup = Mock(side_effect=RuntimeError('secret-canary')
                   if cleanup_fails else None)
    monkeypatch.setattr(gate.cache, 'remove_owned', cleanup)
    cases = Mock(side_effect=RuntimeError('secret-canary')
                 if case_fails else None, return_value={'observed': True})
    monkeypatch.setattr(gate, 'creation_case', cases)
    result = gate.run('owned-test-image')
    assert cases.call_count == 21
    assert cleanup.call_count == 3
    assert len(result['failures']) == 21 * case_fails + 3 * cleanup_fails
    assert result['complete'] is (not case_fails and not cleanup_fails)
    assert result['driver_defaults_unchanged']
    assert result['provider_qualified'] is False
    assert 'secret-canary' not in json.dumps(result)
    for call in start.call_args_list:
        args = call.args
        assert args[args.index('--publish') + 1] == '127.0.0.1::3050'
        assert args[args.index('--label') + 1] == (
            'cdeadmin-owned-gate=' + gate.cache.OWNER)
        assert args[args.index('--memory') + 1] == '512m'
