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


@pytest.mark.parametrize('operation', ['runtime_identity', 'list_resources'])
@pytest.mark.parametrize('interrupted', [False, True])
def test_server_reader_failure_preserved(monkeypatch, operation, interrupted):
    monkeypatch.setattr(
        'pgadmin.cdeadmin.providers.firebird.provider.'
        '_configure_client_library', Mock())
    client = _create_client(SimpleNamespace(acquire_secret=None))
    error = (KeyboardInterrupt('cancelled') if interrupted else
             RelationalClientError('reader failed'))
    reader = Mock(side_effect=error)
    client.config = replace(client.config, server_identity_reader=reader,
                            server_metadata_reader=reader)
    handle = Mock()
    client._connections.append(handle)
    client._server_handles.add(id(handle))
    client._connect_server = Mock(return_value=handle)
    client._uses_server_scope = Mock(return_value=True)
    with pytest.raises(type(error)) as caught:
        getattr(client, operation)({'route': {}})
    assert caught.value is error
    assert error.attachment_release['connection_released'] is False
    assert client._connections == [handle]
    handle._svc = None
    client._forget_and_close(handle)
    assert not client._connections and not client._server_handles


def test_caller_owned_catalog_attachment_is_not_released(monkeypatch):
    monkeypatch.setattr(
        'pgadmin.cdeadmin.providers.firebird.provider.'
        '_configure_client_library', Mock())
    client = _create_client(SimpleNamespace(acquire_secret=None))
    error = RelationalClientError('reader failed')
    client.config = replace(client.config,
                            metadata_reader=Mock(side_effect=error))
    handle = Mock()
    client._connections.append(handle)
    with pytest.raises(RelationalClientError) as caught:
        client.list_resources({'route': {'database': 'owned'},
                               '_provider_session_handle': handle})
    assert caught.value is error
    handle.close.assert_not_called()
    assert not hasattr(error, 'attachment_release')


@pytest.mark.parametrize('operation', [
    'runtime_identity', 'list_resources', 'describe_security'])
@pytest.mark.parametrize('failure_type', [
    RelationalClientError, RuntimeError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('outcome', ['closed', 'retained', 'failure'])
def test_operation_failure_survives_temporary_release(
        monkeypatch, operation, failure_type, outcome):
    monkeypatch.setattr(
        'pgadmin.cdeadmin.providers.firebird.provider.'
        '_configure_client_library', Mock())
    client = _create_client(SimpleNamespace(acquire_secret=None))
    original = failure_type('operation failed')
    reader = Mock(side_effect=original)
    client.config = replace(client.config, connection_initializer=None,
                            metadata_reader=reader, security_reader=reader)
    handle = Mock()
    handle.is_closed.return_value = outcome == 'closed'
    handle.cursor().execute.side_effect = original
    if outcome == 'failure':
        handle.close.side_effect = RuntimeError('private cleanup detail')
    client._invoke_connector = Mock(return_value=handle)
    expected = (RelationalClientError if operation == 'runtime_identity'
                and failure_type is RuntimeError else failure_type)
    with pytest.raises(expected) as caught:
        getattr(client, operation)({'route': {'database': 'owned'}})
    if expected is failure_type:
        assert caught.value is original
    else:
        assert 'profile verification failed' in str(caught.value)
    if outcome != 'closed':
        assert caught.value.attachment_release == {
            'connection_released': False,
            'driver_observation_only': True,
            'native_status_codes': [],
        }
        assert client._connections == [handle]
        assert client._connection_databases == {id(handle): 'owned'}
        handle.close.side_effect = None
        handle.is_closed.return_value = True
        client._forget_and_close(handle)
    else:
        assert not hasattr(caught.value, 'attachment_release')
    assert not client._connections and not client._connection_databases
    handle.commit.assert_not_called()
