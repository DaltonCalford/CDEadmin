"""Initialization failures retain bounded codes, never native message data."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION  # noqa
from pgadmin.cdeadmin.sdk.relational import (
    RelationalClientError, RelationalDBAPIClient,
)


@pytest.mark.parametrize('stage', ['connection', 'retained'])
@pytest.mark.parametrize('engine', ['firebird', 'postgresql'])
@pytest.mark.parametrize('codes,expected', [
    ((335544375,), (335544375,)),
    ([335544721, 335544344], (335544721, 335544344)),
    (('private-password',), ()), ((True,), ()),
    ((0,), ()), ((2147483648,), ()), (tuple(range(1, 34)), ()),
])
@pytest.mark.parametrize('close_fails', [False, True])
def test_initialization_diagnostics_preserve_cleanup_and_redaction(
        stage, engine, codes, expected, close_fails):
    error = RuntimeError('private-password SQL private-path')
    error.gds_codes = codes
    connection = Mock()
    if close_fails:
        connection.close.side_effect = RuntimeError('cleanup-private')
    client = object.__new__(RelationalDBAPIClient)
    client.config = SimpleNamespace(
        profile=SimpleNamespace(engine_id=engine, engine_name=engine),
        server_route=None,
        connection_initializer=Mock(side_effect=error)
        if stage == 'connection' else None,
        session_initializer=Mock(side_effect=error)
        if stage == 'retained' else None)
    client._connector = Mock()
    client._invoke_connector = Mock(return_value=connection)
    client._connections = []
    client._connection_databases = {}
    with pytest.raises(RelationalClientError) as caught:
        client.open_session({'route': {'database': 'owned'}})
    message = str(caught.value)
    assert 'initialization failed' in message
    assert 'RuntimeError' in message
    assert 'private' not in message
    assert caught.value.gds_codes == (expected if engine == 'firebird' else ())
    assert ('Firebird status codes:' in message) is bool(
        expected and engine == 'firebird')
    connection.close.assert_called_once_with()
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()
    assert not client._connections and not client._connection_databases
