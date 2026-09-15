"""Native dialect evidence must preserve distinctions and collect failures."""

from unittest.mock import MagicMock, Mock

import pytest

from tools import cdeadmin_firebird_client_dialect_gate as gate


@pytest.mark.parametrize('client', [1, 2, 3])
def test_unquoted_columns_remain_columns(client):
    assert gate.expected_probe('unquoted-column', client, 200) == (
        [(73,)], None)


@pytest.mark.parametrize('name,value', [
    ('quoted-column', 'ID'), ('quoted-system-column', 'RDB$RELATION_ID'),
])
def test_dialect_one_quoted_columns_are_literals(name, value):
    assert gate.expected_probe(name, 1, 200) == ([(value,)], None)


@pytest.mark.parametrize('name', [name for name, _ in gate.PROBES][1:])
def test_transition_dialect_rejects_double_quotes(name):
    assert gate.expected_probe(name, 2, 200) == (None, 335544763)


def test_dialect_one_string_cannot_be_a_relation():
    assert gate.expected_probe('quoted-relation', 1, 200) == (None, 335544634)


@pytest.mark.parametrize('name,value', [
    ('quoted-column', 73), ('quoted-system-column', 200),
    ('quoted-relation', 73),
])
def test_dialect_three_resolves_delimited_identifiers(name, value):
    assert gate.expected_probe(name, 3, 200) == ([(value,)], None)


@pytest.mark.parametrize('name,dialect', [
    ('missing', 3), ('quoted-column', 0), ('quoted-column', 4),
])
def test_unknown_probe_has_no_assumed_behavior(name, dialect):
    with pytest.raises(ValueError, match='Unknown native dialect probe'):
        gate.expected_probe(name, dialect, 200)


@pytest.mark.parametrize('cleanup_error', [False, True])
def test_setup_failure_preserves_cleanup_without_exposing_credentials(
        monkeypatch, cleanup_error):
    monkeypatch.setattr(gate, '_configure_client_library', Mock())
    docker = Mock(return_value=('a' * 64).encode())
    monkeypatch.setattr(gate, 'docker', docker)
    monkeypatch.setattr(gate, 'published_port', Mock(
        side_effect=RuntimeError('credential-canary')))
    cleanup = Mock(side_effect=RuntimeError('credential-canary')
                   if cleanup_error else None)
    monkeypatch.setattr(gate, 'remove_owned', cleanup)
    result = gate.run('owned-image')
    assert not result['complete']
    assert result['owned_container_removed'] is not cleanup_error
    assert 'credential-canary' not in str(result)
    cleanup.assert_called_once_with('a' * 64)
    arguments = docker.call_args.args
    assert '127.0.0.1::3050' in arguments
    assert '--memory' in arguments and '512m' in arguments


def test_all_cases_collected_after_attachment_assertion_failures(monkeypatch):
    import firebird.driver as native
    monkeypatch.setattr(gate, '_configure_client_library', Mock())
    monkeypatch.setattr(gate, 'docker', Mock(return_value=('b' * 64).encode()))
    monkeypatch.setattr(gate, 'published_port', Mock(return_value=54321))
    cleanup = Mock()
    monkeypatch.setattr(gate, 'remove_owned', cleanup)
    handle = MagicMock()
    handle.__enter__.return_value = handle
    handle.cursor.return_value.__enter__.return_value.fetchone.return_value = (
        '5.0.4',)
    handle.sql_dialect = 0  # Every individual attachment must fail, not skip.
    monkeypatch.setattr(native, 'connect', Mock(return_value=handle))
    monkeypatch.setattr(native, 'create_database', Mock(return_value=handle))
    result = gate.run('owned-image')
    assert len(result['checks']) == len(result['failures']) == 48
    assert not result['complete']
    assert all(not check['passed'] for check in result['checks'])
    assert result['owned_container_removed']
    cleanup.assert_called_once_with('b' * 64)
