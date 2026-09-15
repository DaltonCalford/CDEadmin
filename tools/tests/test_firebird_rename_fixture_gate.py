"""Rename UI fixtures must be owned, cleaned, and not alter caller settings."""

from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import firebird.driver as native
import pytest

from tools import cdeadmin_firebird_rename_fixture_gate as gate


@pytest.mark.parametrize('kind', ['domain', 'column'])
@pytest.mark.parametrize('previous', [None, 'owned-original-value'])
@pytest.mark.parametrize('browser_passes', [False, True])
def test_fixture_ownership_environment_restoration_and_browser_result(
        monkeypatch, tmp_path, kind, previous, browser_passes):
    if previous is None:
        monkeypatch.delenv('CDEADMIN_FIREBIRD_RENAME_KIND', raising=False)
    else:
        monkeypatch.setenv('CDEADMIN_FIREBIRD_RENAME_KIND', previous)
    monkeypatch.setattr(gate, '_configure_client_library', Mock())
    start = Mock(return_value=('a' * 64).encode())
    monkeypatch.setattr(gate, 'docker', start)
    monkeypatch.setattr(gate, 'published_port', Mock(return_value=54321))
    handle = MagicMock()
    handle.__enter__.return_value = handle
    cursor = handle.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = ('5.0.4',)
    monkeypatch.setattr(native, 'connect', Mock(return_value=handle))

    def browser(*args, **kwargs):
        assert gate.os.environ['CDEADMIN_FIREBIRD_RENAME_KIND'] == kind
        assert args[1]['host'] == '127.0.0.1'
        assert args[3] == 'a' * 64
        assert kwargs['gate_kind'] == 'rename'
        return [{'passed': browser_passes, 'scale': 100}]

    monkeypatch.setattr(gate, 'browser_checks', browser)
    cleanup = Mock()
    monkeypatch.setattr(gate, 'remove_owned', cleanup)
    result = gate.run(SimpleNamespace(kind=kind, image='owned-image',
                                      build_root=tmp_path / 'new-evidence'))
    assert result['complete'] is browser_passes
    assert result['native_version_verified'] == '5.0.4'
    assert result['owned_container_removed']
    assert gate.os.environ.get('CDEADMIN_FIREBIRD_RENAME_KIND') == previous
    cleanup.assert_called_once_with('a' * 64)
    args = start.call_args.args
    assert '127.0.0.1::3050' in args
    assert args[args.index('--label') + 1] == (
        'cdeadmin-owned-gate=' + gate.OWNER)
    assert args[args.index('--memory') + 1] == '512m'


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_setup_failure_is_redacted_and_cleanup_is_not_skipped(
        monkeypatch, tmp_path, cleanup_fails):
    monkeypatch.setattr(gate, '_configure_client_library', Mock())
    monkeypatch.setattr(gate, 'docker', Mock(return_value=('b' * 64).encode()))
    monkeypatch.setattr(gate, 'published_port', Mock(
        side_effect=RuntimeError('owned-password-canary')))
    cleanup = Mock(side_effect=RuntimeError('owned-password-canary')
                   if cleanup_fails else None)
    monkeypatch.setattr(gate, 'remove_owned', cleanup)
    result = gate.run(SimpleNamespace(kind='domain', image='owned-image',
                                      build_root=tmp_path / 'new-evidence'))
    assert not result['complete']
    assert 'owned-password-canary' not in str(result)
    assert result['owned_container_removed'] is not cleanup_fails
    cleanup.assert_called_once_with('b' * 64)


def test_unknown_editor_rejected_before_any_fixture_changes(monkeypatch):
    start = Mock()
    monkeypatch.setattr(gate, 'docker', start)
    with pytest.raises(ValueError, match='domain or column'):
        gate.run(SimpleNamespace(kind='unknown'))
    start.assert_not_called()


@pytest.mark.parametrize('outcome', ['wrong-version', 'empty', 'exception'])
def test_incomplete_native_or_browser_evidence_cannot_pass(
        monkeypatch, tmp_path, outcome):
    monkeypatch.setenv('CDEADMIN_FIREBIRD_RENAME_KIND', 'previous-kind')
    monkeypatch.setattr(gate, '_configure_client_library', Mock())
    monkeypatch.setattr(gate, 'docker', Mock(return_value=('c' * 64).encode()))
    monkeypatch.setattr(gate, 'published_port', Mock(return_value=54321))
    handle = MagicMock()
    handle.__enter__.return_value = handle
    cursor = handle.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (
        '5.0.3' if outcome == 'wrong-version' else '5.0.4',)
    monkeypatch.setattr(native, 'connect', Mock(return_value=handle))
    browser = Mock(return_value=[], side_effect=(
        RuntimeError('owned-password-canary')
        if outcome == 'exception' else None))
    monkeypatch.setattr(gate, 'browser_checks', browser)
    cleanup = Mock()
    monkeypatch.setattr(gate, 'remove_owned', cleanup)
    result = gate.run(SimpleNamespace(kind='column', image='owned-image',
                                      build_root=tmp_path / 'new-evidence'))
    assert not result['complete']
    assert result['owned_container_removed']
    assert len(result['failures']) == 1
    assert 'owned-password-canary' not in str(result)
    assert gate.os.environ['CDEADMIN_FIREBIRD_RENAME_KIND'] == 'previous-kind'
    cleanup.assert_called_once_with('c' * 64)
    if outcome == 'wrong-version':
        browser.assert_not_called()
        assert result['failures'][0]['phase'] == 'native-readiness'
        assert 'native_version_verified' not in result
    else:
        browser.assert_called_once()
        assert result['failures'][0]['phase'] == 'browser-rename-editors'
