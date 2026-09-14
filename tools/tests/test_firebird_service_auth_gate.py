"""Bounded non-secret SPB evidence and safe owned-fixture cleanup."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from tools import cdeadmin_firebird_service_auth_gate as gate


@pytest.mark.parametrize('stalled', [False, True])
def test_spb_reader_advances_and_never_reads_password(stalled):
    tags = [29, 28, 118, 124]
    index = [0]
    reads = []
    builder = Mock()
    builder.is_eof.side_effect = lambda: index[0] == len(tags)
    builder.get_tag.side_effect = lambda: tags[index[0]]

    def advance():
        if not stalled:
            index[0] += 1

    def read(**kwargs):
        assert kwargs == {'encoding': 'utf-8'}
        tag = tags[index[0]]
        assert tag != 29, 'Evidence parser must never inspect a password'
        reads.append(tag)
        return {28: 'É', 124: '/owned/東京.fdb'}[tag]

    builder.move_next.side_effect = advance
    builder.get_string.side_effect = read
    driver = Mock()
    driver.core.XpbKind.SPB_ATTACH = 3
    driver.core.SPBItem = SimpleNamespace(
        USER_NAME=28, EXPECTED_DB=124, UTF8_FILENAME=118)
    driver.get_api.return_value.util.get_xpb_builder.return_value = (
        MagicMock(__enter__=Mock(return_value=builder)))
    if stalled:
        with pytest.raises(AssertionError, match='did not terminate'):
            gate.service_context(driver, b'1234')
        assert builder.move_next.call_count == 5
        assert reads == []
    else:
        assert gate.service_context(driver, b'1234') == {
            'user': 'É', 'expected_db': '/owned/東京.fdb', 'utf8': True}
        assert builder.move_next.call_count == 4
        assert reads == [28, 124]


@pytest.mark.parametrize('collision', [False, True])
def test_gate_cleans_only_its_owned_account_and_handles_idle_rollback(
        tmp_path, collision):
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'profiles': [{
        'engine': 'firebird', 'database': '/owned/sample.fdb',
        'password': 'test-credential'}]}))
    state = {'user': collision, 'active': False, 'drops': 0}
    admin, cursor = Mock(), Mock()
    admin.cursor.return_value = MagicMock(
        __enter__=Mock(return_value=cursor))
    admin.main_transaction.is_active.side_effect = lambda: state['active']

    def execute(source, _parameters=()):
        state['active'] = True
        if source.startswith('CREATE USER'):
            state['user'] = True
        elif source.startswith('DROP USER'):
            state['drops'] += 1
            state['user'] = False

    def finish():
        assert state['active'], 'Driver rejects idle transaction finality'
        state['active'] = False

    cursor.execute.side_effect = execute
    cursor.fetchone.side_effect = lambda: (int(state['user']),)
    admin.rollback.side_effect = finish
    admin.commit.side_effect = finish
    service = Mock()
    service._connect_server.side_effect = gate.RelationalClientError(
        'owned connection denial')
    with patch.object(gate, '_configure_client_library'), patch.object(
            gate, '_route_arguments', return_value={}), patch.object(
                gate, '_create_client', return_value=service), patch(
                    'firebird.driver.create_database', return_value=admin):
        result = gate.run(profile)
    assert result['complete'] is False
    assert result['owned_user_removed'] is True
    assert result['owned_database_removed'] is True
    assert state['drops'] == (0 if collision else 1)
    assert state['user'] is collision
    admin.drop_database.assert_called_once_with()
    assert not any(item['stage'].endswith('cleanup')
                   for item in result['failures'])
