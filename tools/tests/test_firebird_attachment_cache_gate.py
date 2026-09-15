"""Cache baseline ownership, complete failure inventory and redaction."""

import json
from unittest.mock import Mock

import pytest

from tools import cdeadmin_firebird_attachment_cache_gate as gate


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_cache_gate_collects_every_mode_and_does_not_export_error_secrets(
        monkeypatch, cleanup_fails):
    secret = 'owned-cache-fixture-secret-canary'
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
    assert len(result['failures']) == (6 if cleanup_fails else 3)
    assert result['removed_server_modes'] == (
        [] if cleanup_fails else list(gate.SERVER_MODES))
    assert cleanup.call_count == 3
    assert secret not in json.dumps(result)
    assert start.call_count == 3
    for call, mode in zip(start.call_args_list, gate.SERVER_MODES):
        args = call.args
        assert args[args.index('--publish') + 1] == '127.0.0.1::3050'
        assert args[args.index('--memory') + 1] == '512m'
        assert args[args.index('--memory-swap') + 1] == '512m'
        assert args[args.index('--label') + 1] == (
            'cdeadmin-owned-gate=' + gate.OWNER)
        assert call.kwargs['env']['FIREBIRD_CONF_ServerMode'] == mode
        assert call.kwargs['env']['FIREBIRD_ROOT_PASSWORD'] == secret
        assert secret not in args


def test_cache_baseline_covers_native_parser_and_allocator_boundaries():
    assert gate.SERVER_MODES == ('Super', 'SuperClassic', 'Classic')
    assert gate.CACHE_REQUESTS == (None, 0, 1, 24, 25, 49, 50, 128, 256)
    assert all(value is None or value <= 256 for value in gate.CACHE_REQUESTS)
