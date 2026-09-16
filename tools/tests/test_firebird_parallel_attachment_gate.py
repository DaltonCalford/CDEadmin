"""Ownership, failure inventory and native worker baseline scope."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import cdeadmin_firebird_parallel_attachment_gate as gate


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_parallel_baseline_collects_policies_and_redacts_errors(
        monkeypatch, cleanup_fails):
    secret = 'owned-parallel-fixture-secret-canary'
    monkeypatch.setattr(gate.secrets, 'token_urlsafe', lambda _size: secret)
    monkeypatch.setattr(gate, '_configure_client_library',
                        lambda _module: None)
    start = Mock(return_value=('a' * 64).encode())
    monkeypatch.setattr(gate, 'docker', start)
    monkeypatch.setattr(gate, 'published_port', Mock(
        side_effect=RuntimeError(secret)))
    cleanup = Mock(side_effect=RuntimeError(secret) if cleanup_fails else None)
    monkeypatch.setattr(gate, 'remove_owned', cleanup)
    result = gate.run('owned-test-image')
    assert not result['complete']
    assert not result['checks']
    assert not result['provider_forms_qualified']
    assert not result['actual_task_worker_counts_qualified']
    assert len(result['failures']) == (4 if cleanup_fails else 2)
    assert len(result['removed_policies']) == (0 if cleanup_fails else 2)
    assert cleanup.call_count == 2
    assert secret not in json.dumps(result)
    assert start.call_count == 2
    for call, (default, maximum) in zip(start.call_args_list, gate.POLICIES):
        args = call.args
        assert args[args.index('--publish') + 1] == '127.0.0.1::3050'
        assert args[args.index('--memory') + 1] == '512m'
        assert args[args.index('--label') + 1] == (
            'cdeadmin-owned-gate=' + gate.OWNER)
        env = call.kwargs['env']
        assert env['FIREBIRD_CONF_ParallelWorkers'] == str(default)
        assert env['FIREBIRD_CONF_MaxParallelWorkers'] == str(maximum)
        assert env['FIREBIRD_ROOT_PASSWORD'] == secret
        assert secret not in args


def test_parallel_baseline_covers_omitted_zero_and_native_cap():
    assert gate.POLICIES == ((1, 2), (2, 4))
    assert gate.REQUESTS == (None, 0, 1, 2, 4, 5, 32767)
    assert gate.OBSERVE == (
        "SELECT RDB$GET_CONTEXT('SYSTEM', 'PARALLEL_WORKERS') "
        "FROM RDB$DATABASE")


@pytest.mark.parametrize('mode', ['Super', 'SuperClassic', 'Classic'])
def test_provider_gate_preserves_ownership_for_each_mode(monkeypatch, mode):
    monkeypatch.setattr(gate, '_configure_client_library', Mock())
    start = Mock(return_value=('b' * 64).encode())
    monkeypatch.setattr(gate, 'docker', start)
    monkeypatch.setattr(gate, 'published_port', Mock(
        side_effect=RuntimeError('owned-error-secret')))
    cleanup = Mock()
    monkeypatch.setattr(gate, 'remove_owned', cleanup)
    result = gate.run('owned', provider_creation=True, server_mode=mode)
    assert not result['complete']
    assert result['provider_creation'] is True
    assert result['observed_server_modes'] == []
    assert result['server_mode'] == mode
    assert cleanup.call_count == 2
    assert len(result['failures']) == 2
    assert 'owned-error-secret' not in json.dumps(result)
    for call in start.call_args_list:
        assert call.kwargs['env']['FIREBIRD_CONF_ServerMode'] == mode


@pytest.mark.parametrize('mode', ['', None, 'Other', 'super'])
def test_invalid_mode_refused_before_native_or_docker_access(
        monkeypatch, mode):
    start = Mock()
    configure = Mock()
    monkeypatch.setattr(gate, 'docker', start)
    monkeypatch.setattr(gate, '_configure_client_library', configure)
    with pytest.raises(ValueError, match='server mode'):
        gate.run('owned', server_mode=mode)
    start.assert_not_called()
    configure.assert_not_called()


def test_ownership_probe_requires_provider_creation(monkeypatch):
    start = Mock()
    monkeypatch.setattr(gate, 'docker', start)
    with pytest.raises(ValueError, match='requires provider creation'):
        gate.run('owned', creation_ownership=True)
    start.assert_not_called()


def test_ownership_probe_removes_only_its_hook_when_creation_fails(
        monkeypatch):
    from firebird.base.hooks import hook_manager
    from pgadmin.cdeadmin.providers.firebird import provider
    error = ValueError('owned-secret-error')
    monkeypatch.setattr(provider, '_database_create_arguments', Mock(
        return_value={'database': 'owned-test-configuration'}))
    monkeypatch.setattr(provider, 'create_owned_database', Mock(
        side_effect=error))
    add = Mock()
    remove = Mock()
    monkeypatch.setattr(hook_manager, 'add_hook', add)
    monkeypatch.setattr(hook_manager, 'remove_hook', remove)
    remove_all = Mock()
    monkeypatch.setattr(hook_manager, 'remove_all_hooks', remove_all)
    native = SimpleNamespace(core=SimpleNamespace(
        Connection=object,
        ConnectionHook=SimpleNamespace(ATTACHED='attached')))
    with pytest.raises(ValueError) as caught:
        gate.creation_ownership_case(native, 50000, 'secret', fail_hook=True)
    assert caught.value is error
    add.assert_called_once()
    remove.assert_called_once_with(*add.call_args.args)
    remove_all.assert_not_called()
