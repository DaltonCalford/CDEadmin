"""Unpublished sessions must be released when initialization is interrupted."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION  # noqa
from pgadmin.cdeadmin.sdk.relational import RelationalDBAPIClient


class OwnedCancellation(BaseException):
    pass


@pytest.mark.parametrize('stage', ['connection', 'retained'])
@pytest.mark.parametrize('kind', [
    KeyboardInterrupt, SystemExit, OwnedCancellation])
@pytest.mark.parametrize('close_fails', [False, True])
def test_interruption_preserves_identity_and_releases_only_new_session(
        stage, kind, close_fails):
    interruption = kind('owned interruption')
    previous, connection = Mock(), Mock()
    if close_fails:
        connection.close.side_effect = RuntimeError('owned close failure')
    client = object.__new__(RelationalDBAPIClient)
    client.config = SimpleNamespace(
        profile=SimpleNamespace(engine_id='firebird', engine_name='Firebird'),
        server_route=None,
        connection_initializer=Mock(side_effect=interruption)
        if stage == 'connection' else None,
        session_initializer=Mock(side_effect=interruption)
        if stage == 'retained' else None)
    client._connector = Mock()
    client._invoke_connector = Mock(return_value=connection)
    client._connections = [previous]
    client._connection_databases = {id(previous): 'previous'}
    with pytest.raises(kind) as caught:
        client.open_session({'route': {'database': 'owned'}})
    assert caught.value is interruption
    connection.close.assert_called_once_with()
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()
    assert client._connections == [previous]
    assert client._connection_databases == {id(previous): 'previous'}
    assert not previous.mock_calls


def test_successful_session_is_published_without_early_cleanup():
    connection = Mock()
    client = object.__new__(RelationalDBAPIClient)
    first, retained = Mock(), Mock()
    client.config = SimpleNamespace(
        server_route=None, connection_initializer=first,
        session_initializer=retained)
    client._connector = Mock()
    client._invoke_connector = Mock(return_value=connection)
    client._connections = []
    client._connection_databases = {}
    assert client.open_session({'route': {'database': 'owned'}}) is connection
    first.assert_called_once_with(connection, {'database': 'owned'})
    retained.assert_called_once_with(connection, {'database': 'owned'})
    assert client._connections == [connection]
    assert client._connection_databases == {id(connection): 'owned'}
    connection.close.assert_not_called()
