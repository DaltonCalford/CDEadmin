"""Failed Firebird handles cannot be retained by normal close hooks."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION  # noqa
from pgadmin.cdeadmin.providers.firebird.failed_session import (
    discard_failed_session,
)
from pgadmin.cdeadmin.providers.firebird.provider import _create_client
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


@pytest.mark.parametrize('stage', [None, 'resources', 'internals', 'detach'])
def test_native_cleanup_attempts_all_stages_without_retention_hooks(stage):
    connection = Mock()
    attachment = connection._att
    callbacks = {'resources': connection._close,
                 'internals': connection._close_internals,
                 'detach': attachment.detach}
    error = RuntimeError('owned cleanup failure')
    if stage:
        callbacks[stage].side_effect = error
        with pytest.raises(RuntimeError) as caught:
            discard_failed_session(connection)
        assert caught.value is error
    else:
        discard_failed_session(connection)
    for callback in callbacks.values():
        callback.assert_called_once_with()
    connection.close.assert_not_called()
    connection.commit.assert_not_called()
    assert connection._att is None
    discard_failed_session(connection)
    attachment.detach.assert_called_once_with()


@pytest.mark.parametrize('stage', ['connection', 'retained'])
@pytest.mark.parametrize('failure', [
    RuntimeError, RelationalClientError, KeyboardInterrupt])
@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_real_provider_wires_failed_cleanup_for_each_initializer(
        monkeypatch, stage, failure, cleanup_fails):
    monkeypatch.setattr(
        'pgadmin.cdeadmin.providers.firebird.provider.'
        '_configure_client_library',
        Mock())
    client = _create_client(SimpleNamespace(acquire_secret=None))
    connection = Mock()
    attachment = connection._att
    if cleanup_fails:
        attachment.detach.side_effect = RuntimeError('private cleanup failure')
    error = failure('owned failure')
    client.config = replace(
        client.config,
        connection_initializer=Mock(side_effect=error)
        if stage == 'connection' else None,
        session_initializer=Mock(side_effect=error)
        if stage == 'retained' else None)
    client._invoke_connector = Mock(return_value=connection)
    with pytest.raises(KeyboardInterrupt if failure is KeyboardInterrupt
                       else RelationalClientError) as caught:
        client.open_session({'route': {'database': 'owned'}})
    assert 'private cleanup failure' not in str(caught.value)
    if failure in (KeyboardInterrupt, RelationalClientError):
        assert caught.value is error
    attachment.detach.assert_called_once_with()
    connection.close.assert_not_called()
    assert not client._connections
    assert not client._connection_databases


@pytest.mark.parametrize('value', [False, 1, 'invalid'])
def test_invalid_sdk_cleanup_callback_is_refused(monkeypatch, value):
    monkeypatch.setattr(
        'pgadmin.cdeadmin.providers.firebird.provider.'
        '_configure_client_library',
        Mock())
    client = _create_client(SimpleNamespace(acquire_secret=None))
    with pytest.raises(RelationalClientError, match='failed_session_releaser'):
        replace(client.config, failed_session_releaser=value)
