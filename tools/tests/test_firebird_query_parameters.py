"""Ordered native bindings without value leakage."""

import json
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from tools.tests.test_cdeadmin_data_studio import harness, endpoint_payload
from tools.cdeadmin_generate_contracts import py_type, ts_type
from pgadmin.cdeadmin.data_studio import DataStudioAccessError
from pgadmin.cdeadmin.providers.firebird.query_parameters import (
    normalize_parameters,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.providers.firebird.provider import _create_client


@pytest.mark.parametrize('parameters,expected', [
    (None, ()), ({}, ()), ([], ()), ([None, 42, 'é'], (None, 42, 'é')),
    ((False, 1.25), (False, 1.25)),
])
def test_native_binding_keeps_order_and_values(parameters, expected):
    assert normalize_parameters(parameters) == expected


@pytest.mark.parametrize('parameters', [
    {'second': 'secret', 'first': 42}, 'secret', 42, True,
])
def test_nonpositional_bindings_are_rejected_without_echo(parameters):
    with pytest.raises(RelationalClientError, match='ordered array') as caught:
        normalize_parameters(parameters)
    assert 'secret' not in str(caught.value)


def positional_studio():
    context, provider, _registry, service = harness()
    contributions = provider.data_studio_contributions()
    contributions['languages'] = tuple(replace(
        item, parameter_shape='array', parameter_hint='Ordered ? parameters')
        for item in contributions['languages'])
    with patch.object(provider, 'data_studio_contributions',
                      return_value=contributions):
        service.open_session(context, endpoint_payload(context), 'example-sql')
    return context, provider, service


def test_studio_preserves_positional_shape_and_history_contains_only_count():
    context, provider, service = positional_studio()
    values = [42, 'private-binding-canary', None, {'nested': [1, 2]}]
    occurrence = service.execute(context, 'session-one', 'native ? source',
                                 parameters=values)
    actual = next(request for action, request in provider.calls
                  if action == 'execute')['parameters']
    assert actual == values
    values[-1]['nested'].append(3)
    assert actual[-1] == {'nested': [1, 2]}
    summary = occurrence['request_summary']
    assert summary['parameter_shape'] == 'array'
    assert summary['parameter_count'] == 4
    assert summary['parameter_names'] == []
    assert 'private-binding-canary' not in json.dumps(service.history.export())
    language = service.languages(context)[0]
    assert language['parameter_shape'] == 'array'
    assert language['parameter_hint'] == 'Ordered ? parameters'


@pytest.mark.parametrize('values', [None, {}, []])
def test_empty_legacy_input_becomes_empty_native_sequence(values):
    context, provider, service = positional_studio()
    service.execute(context, 'session-one', 'native source', parameters=values)
    request = next(request for action, request in provider.calls
                   if action == 'execute')
    assert request['parameters'] == []


@pytest.mark.parametrize('values', [{'one': 'private'}, 'private', 42, True])
def test_shape_failure_is_before_provider_dispatch_and_not_logged(values):
    context, provider, service = positional_studio()
    with pytest.raises(DataStudioAccessError, match='parameter array'):
        service.execute(context, 'session-one', 'native source',
                        parameters=values)
    assert not any(action == 'execute' for action, _value in provider.calls)
    assert 'private' not in json.dumps(service.history.export())


def test_object_provider_still_rejects_positional_input():
    context, provider, _registry, service = harness()
    service.open_session(context, endpoint_payload(context), 'example-sql')
    with pytest.raises(DataStudioAccessError, match='parameter object'):
        service.execute(context, 'session-one', 'native source',
                        parameters=[1])
    assert not any(action == 'execute' for action, _value in provider.calls)


def test_generator_preserves_union_and_unconstrained_array_values():
    schema = {'type': ['object', 'array'], 'items': {}}
    assert py_type(schema) == 'dict[str, Any] | list[Any]'
    assert ts_type(schema) == 'Record<string, unknown> | Array<unknown>'
    assert py_type({'type': ['string', 'null']}) == 'str | None'
    assert py_type({}) == 'Any'


def test_workspace_transports_array_without_coercing_it_to_a_dictionary():
    from pgadmin.cdeadmin.workspace.service import ProviderWorkspaceService
    workspace = object.__new__(ProviderWorkspaceService)
    workspace.endpoint_service = Mock()
    workspace.endpoint_service.workspace.return_value = ('context', None, None)
    workspace.studio_service = Mock()
    values = [7, 'text', None]
    workspace.execute('server', 'session', 'source', values,
                      database_target_id='selected-target')
    workspace.studio_service.execute.assert_called_once_with(
        'context', 'session', 'source', parameters=values,
        output_policy={'redact_keys': []})
    workspace.endpoint_service.workspace.assert_called_once_with(
        'server', database_target_id='selected-target')


@pytest.mark.parametrize('active', [False, True])
def test_firebird_close_only_rolls_back_an_active_native_transaction(active):
    client = _create_client(Mock())
    connection = Mock()
    connection.main_transaction.is_active.return_value = active
    connection.is_closed.return_value = True
    client._connections.append(connection)
    result = client.close_session(connection)
    assert result['rollback_requested'] is active
    assert connection.rollback.call_count == int(active)
    connection.close.assert_called_once_with()
    assert connection not in client._connections


def test_firebird_close_does_not_claim_success_if_state_observation_fails():
    client = _create_client(Mock())
    connection = Mock()
    connection.main_transaction.is_active.side_effect = RuntimeError('closed')
    client._connections.append(connection)
    with pytest.raises(RuntimeError, match='closed'):
        client.close_session(connection)
    connection.rollback.assert_not_called()
    assert connection in client._connections
    client.close()


def test_query_failure_retains_status_codes_without_sql_values_or_rollback():
    class NativeFailure(Exception):
        gds_codes = (335544665, 335544349)
        sqlstate = '23000'

    client = _create_client(Mock())
    connection = Mock()
    cursor = connection.cursor.return_value
    cursor.execute.side_effect = NativeFailure('private SQL/value canary')
    with pytest.raises(RelationalClientError) as caught:
        client.execute(connection, {'source': 'INSERT INTO T VALUES (?)',
                                    'parameters': ['private binding']})
    assert caught.value.gds_codes == (335544665, 335544349)
    assert '335544665' in str(caught.value)
    assert 'private' not in str(caught.value)
    connection.rollback.assert_not_called()
    connection.commit.assert_not_called()
    cursor.close.assert_called_once_with()


@pytest.mark.parametrize('failure', ['close-error', 'not-closed', 'unknown'])
def test_failed_native_release_keeps_session_tracked(failure):
    client = _create_client(Mock())
    connection = Mock()
    connection.main_transaction.is_active.return_value = False
    connection.is_closed.return_value = (
        False if failure == 'not-closed' else None)
    if failure == 'close-error':
        connection.close.side_effect = RuntimeError('private close canary')
    client._connections.append(connection)
    with pytest.raises(RelationalClientError) as caught:
        client.close_session(connection)
    assert 'private' not in str(caught.value)
    assert connection in client._connections
    connection.rollback.assert_not_called()
    connection.close.side_effect = None
    connection.is_closed.return_value = True
    assert client.close_session(connection)['connection_released'] is True
    assert connection not in client._connections


def test_provider_keeps_session_identity_until_release_is_confirmed():
    from tools.tests.test_cdeadmin_actual_engine_pilots import (
        provider, FirebirdProvider, FIREBIRD,
    )
    instance = provider(FirebirdProvider, FIREBIRD)
    session = instance.open_session({'route': {'route_id': 'owned-test'}})
    instance.client.close_session = Mock(
        side_effect=RelationalClientError('Release not confirmed'))
    with pytest.raises(RelationalClientError):
        instance.close_session(session)
    assert session['session_id'] in instance._sessions
    instance.client.close_session.side_effect = None
    instance.client.close_session.return_value = {'connection_released': True}
    assert instance.close_session(session)['provider_closed'] is True
    assert session['session_id'] not in instance._sessions
