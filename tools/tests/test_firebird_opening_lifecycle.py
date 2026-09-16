"""Client close cannot race either stage of Firebird session initialization."""

import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION  # noqa
from pgadmin.cdeadmin.providers.firebird.provider import _create_client
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
