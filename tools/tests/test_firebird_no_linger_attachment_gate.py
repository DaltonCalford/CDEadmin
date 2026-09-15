"""Native no-linger probe safety and complete failure inventory."""

import json
from unittest.mock import Mock, MagicMock

import firebird.driver as native
import pytest

from tools import cdeadmin_firebird_no_linger_attachment_gate as gate


@pytest.mark.parametrize('cleanup_fails', [False, True])
@pytest.mark.parametrize('server_mode', gate.SERVER_MODES)
def test_setup_failure_is_redacted_and_owned_cleanup_is_attempted(
        monkeypatch, cleanup_fails, server_mode):
    secret = 'owned-linger-credential-canary'
    monkeypatch.setattr(gate.secrets, 'token_urlsafe', lambda _size: secret)
    monkeypatch.setattr(gate, '_configure_client_library', Mock())
    start = Mock(return_value=('a' * 64).encode())
    monkeypatch.setattr(gate, 'docker', start)
    monkeypatch.setattr(gate, 'published_port',
                        Mock(side_effect=RuntimeError(secret)))
    cleanup = Mock(side_effect=RuntimeError(secret) if cleanup_fails else None)
    monkeypatch.setattr(gate, 'remove_owned', cleanup)
    result = gate.run('owned-test-image', server_mode)
    assert not result['complete']
    assert len(result['failures']) == (2 if cleanup_fails else 1)
    assert result['owned_container_removed'] is not cleanup_fails
    assert secret not in json.dumps(result)
    cleanup.assert_called_once_with('a' * 64)
    args = start.call_args.args
    assert args[args.index('--publish') + 1] == '127.0.0.1::3050'
    assert args[args.index('--memory') + 1] == '512m'
    assert args[args.index('--memory-swap') + 1] == '512m'
    assert args[args.index('--label') + 1] == (
        'cdeadmin-owned-gate=' + gate.OWNER)
    assert start.call_args.kwargs['env']['FIREBIRD_CONF_ServerMode'] == (
        server_mode)
    assert secret not in args


@pytest.mark.parametrize('mode', [None, '', 'super', 'Unknown'])
def test_invalid_server_mode_is_rejected_before_fixture_creation(
        monkeypatch, mode):
    start = Mock()
    monkeypatch.setattr(gate, 'docker', start)
    with pytest.raises(ValueError, match='exact Firebird server mode'):
        gate.run('owned-test-image', mode)
    start.assert_not_called()


def test_all_twelve_cases_run_after_individual_setup_failures(monkeypatch):
    secret = 'owned-linger-failure-canary'
    monkeypatch.setattr(gate, '_configure_client_library', Mock())
    monkeypatch.setattr(gate, 'docker',
                        Mock(return_value=('b' * 64).encode()))
    monkeypatch.setattr(gate, 'published_port', Mock(return_value=53051))
    cleanup = Mock()
    monkeypatch.setattr(gate, 'remove_owned', cleanup)
    connection = MagicMock()
    cursor = connection.__enter__.return_value.cursor.return_value
    cursor.__enter__.return_value.fetchone.return_value = ('5.0.4',)
    monkeypatch.setattr(native, 'connect', Mock(return_value=connection))
    create = Mock(side_effect=RuntimeError(secret))
    monkeypatch.setattr(native, 'create_database', create)
    monkeypatch.setattr(native, 'driver_config', Mock())
    result = gate.run('owned-test-image')
    assert not result['complete']
    assert len(result['checks']) == len(result['failures']) == 12
    assert create.call_count == 12
    assert len({item['case'] for item in result['checks']}) == 12
    assert all(not item['passed'] for item in result['checks'])
    assert result['owned_container_removed']
    assert secret not in json.dumps(result)
    cleanup.assert_called_once_with('b' * 64)
