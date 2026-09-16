"""Browser lifecycle checks must read the stored header independently."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import cdeadmin_firebird_database_lifecycle_ui_gate as gate


@pytest.mark.parametrize('stored', [0, 50, 64])
def test_native_lifecycle_observation_reads_stored_buffers(
        monkeypatch, stored):
    cursor = Mock()
    cursor.fetchone.side_effect = [
        (16384, 13, 1, 3, True, True), ('UTF8', 0, False)]
    connection = Mock()
    connection.cursor.return_value = cursor
    connection.info.engine_version = '5.0.4'
    connection.info.get_info.return_value = stored
    monkeypatch.setattr(gate, '_connect', Mock(return_value=connection))
    native = SimpleNamespace(DbInfoCode=SimpleNamespace(SET_PAGE_BUFFERS=61))
    result = gate._native_state(native, object(), 'owned-secret', '/owned.fdb')
    assert result['stored_page_buffers'] == stored
    connection.info.get_info.assert_called_once_with(61)
    assert result['page_size'] == 16384
    assert result['default_character_set'] == 'UTF8'
    connection.close.assert_called_once()


def test_missing_header_observation_is_failure_not_default_zero(monkeypatch):
    cursor = Mock()
    cursor.fetchone.side_effect = [
        (16384, 13, 1, 3, True, True), ('UTF8', 0, False)]
    connection = Mock()
    connection.cursor.return_value = cursor
    connection.info.get_info.side_effect = RuntimeError('observation failed')
    monkeypatch.setattr(gate, '_connect', Mock(return_value=connection))
    native = SimpleNamespace(DbInfoCode=SimpleNamespace(SET_PAGE_BUFFERS=61))
    with pytest.raises(RuntimeError, match='observation failed'):
        gate._native_state(native, object(), 'owned-secret', '/owned.fdb')
    connection.close.assert_called_once()
