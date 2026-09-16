"""Temporary database attachments remain owned until closure is confirmed."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION  # noqa
from pgadmin.cdeadmin.providers.firebird.provider import _create_client
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


@pytest.mark.parametrize('operation', [
    'runtime_identity', 'list_resources', 'describe_security', 'direct'])
@pytest.mark.parametrize('outcome', ['closed', 'retained', 'failure'])
def test_temporary_release_requires_confirmation_and_supports_retry(
        monkeypatch, operation, outcome):
    monkeypatch.setattr(
        'pgadmin.cdeadmin.providers.firebird.provider.'
        '_configure_client_library', Mock())
    client = _create_client(SimpleNamespace(acquire_secret=None))
    client.config = replace(client.config, connection_initializer=None,
                            metadata_reader=lambda *_args: [],
                            security_reader=lambda *_args: {})
    handle = Mock()
    handle.is_closed.return_value = outcome == 'closed'
    handle.cursor().fetchone.return_value = ('5.0.4',)
    if outcome == 'failure':
        handle.close.side_effect = RuntimeError('private close error')
    client._invoke_connector = Mock(return_value=handle)
    request = {'route': {'database': 'owned'}}

    def invoke():
        if operation == 'direct':
            client._connections.append(handle)
            client._connection_databases[id(handle)] = 'owned'
            client._forget_and_close(handle)
        else:
            getattr(client, operation)(request)

    if outcome == 'closed':
        invoke()
    else:
        with pytest.raises(RelationalClientError) as caught:
            invoke()
        assert 'private close error' not in str(caught.value)
        assert client._connections == [handle]
        assert client._connection_databases == {id(handle): 'owned'}
        handle.close.side_effect = None
        handle.is_closed.return_value = True
        client._forget_and_close(handle)
    assert not client._connections and not client._connection_databases
    handle.commit.assert_not_called()
    assert handle.close.call_count == (1 if outcome == 'closed' else 2)
