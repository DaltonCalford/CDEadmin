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
def test_boundary_cannot_race_grid_operation(rig, monkeypatch, method):
    provider, handle = rig
    entered, finish = threading.Event(), threading.Event()
    errors = []

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
            provider.control_transaction(
                {'session_id': 'owned', 'action': 'commit'})
        assert 'owned-token' in ADMINISTRATION._row_identities
        handle.commit.assert_not_called()
    finally:
        finish.set()
        worker.join(5)
    assert not worker.is_alive() and not errors
