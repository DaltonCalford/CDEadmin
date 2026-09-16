"""Client close cannot race either stage of Firebird session initialization."""

import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION  # noqa
from pgadmin.cdeadmin.providers.firebird.provider import (
    FirebirdProvider, PROFILE, _create_client,
)
from tools.tests.test_cdeadmin_actual_engine_pilots import context, Permissions
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


@pytest.mark.parametrize('stage', ['connection', 'retained'])
@pytest.mark.parametrize('outcome', ['success', 'failure', 'interruption'])
def test_entire_opening_is_protected_without_blocking_other_sessions(
        monkeypatch, stage, outcome):
    monkeypatch.setattr(
        'pgadmin.cdeadmin.providers.firebird.provider.'
        '_configure_client_library', Mock())
    client = _create_client(SimpleNamespace(acquire_secret=None))
    entered, finish = threading.Event(), threading.Event()
    opening, other = Mock(), Mock()
    other.info.firebird_version = '5.0.4'
    opening.is_closed.return_value = other.is_closed.return_value = True
    client._connections.append(other)
    client._invoke_connector = Mock(return_value=opening)
    error = (KeyboardInterrupt('owned interruption') if outcome ==
             'interruption' else RelationalClientError('owned failure'))
    results, failures = [], []

    def initialize(_handle, _route):
        entered.set()
        assert finish.wait(5), 'Initializer was not released'
        if outcome != 'success':
            raise error

    client.config = replace(
        client.config,
        connection_initializer=initialize if stage == 'connection' else None,
        session_initializer=initialize if stage == 'retained' else None)

    def open_session():
        try:
            results.append(client.open_session(
                {'route': {'database': 'owned'}}))
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=open_session)
    worker.start()
    try:
        assert entered.wait(5), 'Initializer did not start'
        assert client.runtime_identity({}, other)['version'] == '5.0.4'
        with pytest.raises(RelationalClientError, match='still opening'):
            client.close()
        opening.close.assert_not_called()
        other.close.assert_not_called()
        assert not client._closed
    finally:
        finish.set()
        worker.join(5)
        assert not worker.is_alive()
        client.close()
    assert client._opening == 0
    assert client._closed and not client._connections
    if outcome == 'success':
        assert results == [opening] and not failures
    else:
        assert not results and failures == [error]
    opening.commit.assert_not_called()


@pytest.mark.parametrize('stage', ['identity', 'publication'])
@pytest.mark.parametrize('outcome', ['success', 'failure', 'interruption'])
def test_provider_opening_keeps_attachment_owned_until_publication(
        monkeypatch, stage, outcome):
    monkeypatch.setattr(
        'pgadmin.cdeadmin.providers.firebird.provider.'
        '_configure_client_library', Mock())
    client = _create_client(SimpleNamespace(acquire_secret=None))
    client.config = replace(client.config, connection_initializer=None,
                            session_initializer=None)
    handle = Mock()
    handle.is_closed.return_value = True
    client._invoke_connector = Mock(return_value=handle)
    provider = FirebirdProvider(context(PROFILE), Permissions(), client)
    entered, finish = threading.Event(), threading.Event()
    results, failures = [], []
    error = (KeyboardInterrupt('owned interruption') if outcome ==
             'interruption' else RuntimeError('owned failure'))

    def pause():
        entered.set()
        assert finish.wait(5), 'Provider opening was not released'

    def identity(*_args):
        if stage == 'identity':
            pause()
        if outcome != 'success':
            raise error

    class Sessions(dict):
        def __setitem__(self, key, value):
            if stage == 'publication':
                pause()
            super().__setitem__(key, value)

    # Failure paths pause in failed-open cleanup rather than publication.
    discard = provider._discard_unverified_session

    def cleanup(value):
        if stage == 'publication':
            pause()
        discard(value)

    provider._runtime_identity = identity
    provider._discard_unverified_session = cleanup
    provider._sessions = Sessions()

    def open_session():
        try:
            results.append(provider.open_session(
                {'route': {'database': 'owned'}}))
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=open_session)
    worker.start()
    try:
        assert entered.wait(5), 'Provider opening did not start'
        for close in (client.close, provider.close):
            with pytest.raises(RelationalClientError, match='still opening'):
                close()
        assert client._connections == [handle]
        handle.close.assert_not_called()
        handle._att.detach.assert_not_called()
        assert not provider._sessions
    finally:
        finish.set()
        worker.join(5)
        assert not worker.is_alive()
        provider.close()
    assert client._opening == 0
    assert client._closed and not client._connections
    assert not provider._sessions
    if outcome == 'success':
        assert len(results) == 1 and not failures
    else:
        assert not results and failures == [error]
    handle.commit.assert_not_called()
