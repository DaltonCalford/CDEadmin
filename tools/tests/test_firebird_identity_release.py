"""Unpublished provider sessions use Firebird failed-attachment ownership."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.tests.test_cdeadmin_actual_engine_pilots import context, Permissions
from pgadmin.cdeadmin.providers.firebird.provider import (
    FirebirdProvider, PROFILE, _create_client,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.sdk.actual_engine import ActualEnginePilotProvider


@pytest.mark.parametrize('failure', [
    RelationalClientError, RuntimeError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('detach_fails', [False, True])
def test_identity_failure_releases_or_quarantines_without_publication(
        monkeypatch, failure, detach_fails):
    monkeypatch.setattr(
        'pgadmin.cdeadmin.providers.firebird.provider.'
        '_configure_client_library', Mock())
    client = _create_client(SimpleNamespace(acquire_secret=None))
    client.config = replace(client.config, connection_initializer=None,
                            session_initializer=None)
    handle = Mock()
    attachment = handle._att
    if detach_fails:
        attachment.detach.side_effect = RuntimeError('private detach error')
    client._invoke_connector = Mock(return_value=handle)
    provider = FirebirdProvider(context(PROFILE), Permissions(), client)
    original = failure('identity verification failed')
    provider._runtime_identity = Mock(side_effect=original)
    with pytest.raises(failure) as caught:
        provider.open_session({'route': {'database': 'owned'}})
    assert caught.value is original
    assert not provider._sessions
    handle.close.assert_not_called()
    handle.commit.assert_not_called()
    attachment.detach.assert_called_once_with()
    if detach_fails:
        assert client._connections == [handle]
        assert id(handle) in client._failed_initializations
        with pytest.raises(RelationalClientError, match='cleanup-only'):
            client.execute(handle, {'source': 'SELECT 1 FROM RDB$DATABASE'})
        attachment.detach.side_effect = None
    else:
        assert not client._connections and not client._attachment_states
    client.close()
    assert not client._connections and not client._failed_initializations


@pytest.mark.parametrize('failure', [
    RuntimeError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_default_provider_preserves_failure_and_attempts_close(
        failure, cleanup_fails):
    provider = object.__new__(ActualEnginePilotProvider)
    handle = Mock()
    if cleanup_fails:
        handle.close.side_effect = RuntimeError('private cleanup error')
    provider.client = SimpleNamespace(open_session=Mock(return_value=handle))
    provider.profile = SimpleNamespace(required_permissions=())
    provider._sessions = {}
    original = failure('identity failure')
    provider._runtime_identity = Mock(side_effect=original)
    with pytest.raises(failure) as caught:
        provider.open_session({'route': {'database': 'owned'}})
    assert caught.value is original
    handle.close.assert_called_once_with()
    assert not provider._sessions
