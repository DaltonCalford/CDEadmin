"""Ownership, failure inventory and native worker baseline scope."""

import json
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
