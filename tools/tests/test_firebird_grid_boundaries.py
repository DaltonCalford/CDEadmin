"""Grid state cannot survive explicit provider transaction boundaries."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from tools.tests.test_cdeadmin_actual_engine_pilots import context, Permissions
from pgadmin.cdeadmin.providers.firebird.provider import (
    FirebirdProvider, PROFILE, _create_client,
)
from pgadmin.cdeadmin.sdk.actual_engine import ActualEnginePilotProvider
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


@pytest.fixture
def rig(monkeypatch):
    monkeypatch.setattr(
        'pgadmin.cdeadmin.providers.firebird.provider.'
        '_configure_client_library', Mock())
    client = _create_client(SimpleNamespace(acquire_secret=None))
    handle = Mock()
    client._connections.append(handle)
    provider = FirebirdProvider(context(PROFILE), Permissions(), client)
    provider._sessions['owned'] = SimpleNamespace(handle=handle)
    for label, session in [('owned-token', 'owned'), ('other-token', 'other')]:
        ADMINISTRATION._row_identities[label] = SimpleNamespace(
            session_id=session)
        ADMINISTRATION._row_continuations[label] = SimpleNamespace(
            session_id=session)
        provider._visual_admin._plans[label] = SimpleNamespace(
            presentation={'session_id': session})
    yield provider, handle
    for session in ('owned', 'other'):
        ADMINISTRATION.invalidate_row_session(session)
    client._connections.clear()


def assert_invalidated(provider):
    for values in (ADMINISTRATION._row_identities,
                   ADMINISTRATION._row_continuations,
                   provider._visual_admin._plans):
        assert 'owned-token' not in values
        assert 'other-token' in values


@pytest.mark.parametrize('action', ['commit', 'rollback', 'close'])
@pytest.mark.parametrize('failure', [None, RuntimeError, KeyboardInterrupt])
def test_boundary_forgets_only_its_session_even_on_failure(
        rig, monkeypatch, action, failure):
    provider, handle = rig
    method = 'close_session' if action == 'close' else 'control_transaction'

    def boundary(_self, _request):
        assert_invalidated(provider)
        if failure:
            raise failure('owned failure')
        return {'done': True}

    monkeypatch.setattr(ActualEnginePilotProvider, method, boundary)
    request = {'session_id': 'owned', 'action': action}
    if failure:
        with pytest.raises(failure):
            getattr(provider, method)(request)
    else:
        assert getattr(provider, method)(request) == {'done': True}
    assert_invalidated(provider)


@pytest.mark.parametrize('method', [
    'read_visual_admin_rows', 'plan_visual_admin', 'apply_visual_admin'])
@pytest.mark.parametrize('boundary', ['commit', 'query'])
def test_boundary_cannot_race_grid_operation(
        rig, monkeypatch, method, boundary):
    provider, handle = rig
    entered, finish = threading.Event(), threading.Event()
    errors = []
    provider.client.submit_query = Mock()

    def operation(_self, _request):
        entered.set()
        assert finish.wait(5)

    monkeypatch.setattr(ActualEnginePilotProvider, method, operation)

    def run():
        try:
            getattr(provider, method)({'session_id': 'owned'})
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(5)
        with pytest.raises(RelationalClientError, match='busy'):
            if boundary == 'commit':
                provider.control_transaction(
                    {'session_id': 'owned', 'action': 'commit'})
            else:
                provider.execute({'session_id': 'owned',
                                  'source': 'SELECT 1 FROM RDB$DATABASE'})
        assert 'owned-token' in ADMINISTRATION._row_identities
        handle.commit.assert_not_called()
        provider.client.submit_query.assert_not_called()
    finally:
        finish.set()
        worker.join(5)
    assert not worker.is_alive() and not errors


@pytest.mark.parametrize('source', [
    'SELECT 1 FROM RDB$DATABASE', 'COMMIT', 'ROLLBACK',
    'EXECUTE PROCEDURE OWNED_PROCEDURE', 'invalid native source'])
@pytest.mark.parametrize('failure', [None, RuntimeError, KeyboardInterrupt])
def test_query_submission_invalidates_without_parsing_source(
        rig, source, failure):
    provider, handle = rig
    marker = object()

    def submit(actual_handle, payload):
        assert actual_handle is handle and payload['source'] == source
        assert_invalidated(provider)
        if failure:
            raise failure('owned dispatch failure')
        return marker

    provider.client.submit_query = Mock(side_effect=submit)
    if failure:
        with pytest.raises(failure):
            provider.execute({'session_id': 'owned', 'source': source})
    else:
        result = provider.execute({'session_id': 'owned', 'source': source})
        assert provider._operations[result['operation_id']].token is marker
    assert_invalidated(provider)
