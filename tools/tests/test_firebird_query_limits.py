"""Bounded native fetching without transaction rewrites."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import _create_client
from pgadmin.cdeadmin.providers.firebird.query_limits import query_row_limit
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.workspace.service import (
    ProviderWorkspaceError, ProviderWorkspaceService,
)


def client_fixture(rows=()):
    client = _create_client(Mock())
    client.config = replace(client.config, query_columns_reader=None,
                            query_value_normalizer=lambda value: value)
    connection = Mock()
    cursor = connection.cursor.return_value
    cursor.description = [('N', int)]
    cursor.rowcount = -1
    cursor.fetchmany.side_effect = lambda count: list(rows)[:count]
    cursor.fetchall.return_value = list(rows)
    client._connections.append(connection)
    return client, connection, cursor


@pytest.mark.parametrize('value', [1, 1000, 1_000_000, None])
def test_accept_only_explicit_integer_application_bounds(value):
    assert query_row_limit({'output_policy': {'max_rows': value}}) == value


@pytest.mark.parametrize('policy', [None, {}, {'redact_keys': []}])
def test_existing_programmatic_calls_remain_unbounded(policy):
    assert query_row_limit({'output_policy': policy}) is None


@pytest.mark.parametrize('value', [
    0, -1, 1_000_001, True, False, 1.0,
    '1000', '', [], {}, 'private-canary'])
@pytest.mark.parametrize('asynchronous', [False, True])
def test_invalid_bounds_never_dispatch_or_change_transaction(
        value, asynchronous):
    client, connection, cursor = client_fixture()
    callback = client.submit_query if asynchronous else client.execute
    with pytest.raises(RelationalClientError) as error:
        callback(connection, {'source': 'native source',
                              'output_policy': {'max_rows': value}})
    assert 'private-canary' not in str(error.value)
    connection.cursor.assert_not_called()
    cursor.execute.assert_not_called()
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()
    assert not client._queries


@pytest.mark.parametrize('policy', [[], 'private-canary', 1, False])
def test_policy_shape_is_checked(policy):
    with pytest.raises(RelationalClientError, match='must be an object'):
        query_row_limit({'output_policy': policy})


@pytest.mark.parametrize('row_count', [0, 1, 2, 3])
@pytest.mark.parametrize('asynchronous', [False, True])
def test_bound_uses_fetchmany_without_lookahead_or_sql_rewrite(
        row_count, asynchronous):
    client, connection, cursor = client_fixture(
        [(n,) for n in range(row_count)])
    source = 'SELECT N FROM NATIVE_SOURCE WHERE N >= ?'
    request = {'source': source, 'parameters': [0],
               'output_policy': {'max_rows': 2}}
    token = (client.submit_query if asynchronous else client.execute)(
        connection, request)
    if asynchronous:
        token.worker.join(2)
        assert token.done
    result = client.describe_result(token)
    assert result['complete'] is True
    assert result['stream_reference'] is None  # This is not streaming.
    count = min(2, row_count)
    assert result['payload']['rows'] == [(n,) for n in range(count)]
    assert result['payload']['fetch_observation'] == {
        'max_rows': 2, 'rows_returned': count, 'limit_reached': count == 2,
        'end_of_cursor_observed': count < 2,
        'total_rows': count if count < 2 else None,
        'sql_rewritten': False, 'transaction_action_requested': False,
    }
    cursor.execute.assert_called_once_with(source, (0,))
    cursor.fetchmany.assert_called_once_with(2)
    cursor.fetchall.assert_not_called()
    cursor.fetchone.assert_not_called()
    cursor.close.assert_called_once_with()
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()
    # Subsequent polling is observational, not another cursor fetch.
    assert client.describe_result(token) == result
    cursor.fetchmany.assert_called_once_with(2)


def test_no_result_columns_do_not_fetch_or_invent_a_limit_observation():
    client, connection, cursor = client_fixture()
    cursor.description = None
    token = client.execute(connection, {'source': 'native DDL',
                                        'output_policy': {'max_rows': 1}})
    result = client.describe_result(token)
    assert 'fetch_observation' not in result['payload']
    cursor.fetchmany.assert_not_called()
    cursor.fetchall.assert_not_called()


def test_absent_limit_preserves_native_fetchall():
    client, connection, cursor = client_fixture([(1,), (2,)])
    result = client.describe_result(client.execute(connection, {
        'source': 'native source'}))
    assert result['payload']['rows'] == [(1,), (2,)]
    assert 'fetch_observation' not in result['payload']
    cursor.fetchall.assert_called_once_with()
    cursor.fetchmany.assert_not_called()


def test_only_fetched_values_are_materialized():
    client, connection, cursor = client_fixture([(1,), (2,), (3,)])
    normalizer = Mock(side_effect=lambda value: str(value))
    client.config = replace(client.config, query_value_normalizer=normalizer)
    result = client.describe_result(client.execute(connection, {
        'source': 'native source', 'output_policy': {'max_rows': 1}}))
    assert result['payload']['rows'] == [('1',)]
    normalizer.assert_called_once_with(1)


@pytest.mark.parametrize('asynchronous', [False, True])
def test_fetch_failure_is_not_success_or_replayed(asynchronous):
    client, connection, cursor = client_fixture()
    failure = RuntimeError('private-canary')
    failure.gds_codes = (335544321,)
    cursor.fetchmany.side_effect = failure
    request = {'source': 'native source', 'output_policy': {'max_rows': 1}}
    if asynchronous:
        token = client.submit_query(connection, request)
        token.worker.join(2)
        assert token.done
        result = client.describe_result(token)
        assert result['payload']['execution_state'] == 'failed'
        assert result['payload']['error']['native_status_codes'] == [335544321]
        assert 'fetch_observation' not in result['payload']
        assert 'private-canary' not in str(result)
    else:
        with pytest.raises(RelationalClientError) as error:
            client.execute(connection, request)
        assert error.value.gds_codes == (335544321,)
        assert 'private-canary' not in str(error.value)
    cursor.execute.assert_called_once_with('native source')
    cursor.fetchmany.assert_called_once_with(1)
    cursor.close.assert_called_once_with()
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()


def test_bounded_result_release_failure_blocks_reuse_without_replay():
    client, connection, cursor = client_fixture([(1,), (2,)])
    cursor.close.side_effect = RuntimeError('private-canary')
    token = client.submit_query(connection, {
        'source': 'native source', 'output_policy': {'max_rows': 1}})
    token.worker.join(2)
    result = client.describe_result(token)
    assert result['payload']['execution_state'] == 'failed'
    assert result['payload']['session_reuse_blocked'] is True
    assert 'fetch_observation' not in result['payload']
    assert 'private-canary' not in str(result)
    with pytest.raises(RelationalClientError, match='result cleanup'):
        client.execute(connection, {'source': 'no replay'})
    cursor.execute.assert_called_once_with('native source')
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()


def workspace_fixture(provider_id='org.cdeadmin.firebird'):
    workspace = object.__new__(ProviderWorkspaceService)
    workspace.endpoint_service = Mock()
    context = SimpleNamespace(provider_id=provider_id)
    workspace.endpoint_service.workspace.return_value = (context, None, None)
    workspace.studio_service = Mock()
    return workspace, context


def test_workspace_transports_policy_to_the_selected_database_session():
    workspace, context = workspace_fixture()
    workspace.execute('server', 'session', 'source', [], 'target', max_rows=7)
    workspace.studio_service.execute.assert_called_once_with(
        context, 'session', 'source', parameters=[],
        output_policy={'redact_keys': [], 'max_rows': 7})
    workspace.endpoint_service.workspace.assert_called_once_with(
        'server', database_target_id='target')


@pytest.mark.parametrize('value', [0, True, -1, '1', 1.2, 1_000_001])
def test_workspace_rejects_bad_bounds_before_dispatch(value):
    workspace, _context = workspace_fixture()
    with pytest.raises(ProviderWorkspaceError, match='maximum fetched rows'):
        workspace.execute('server', 'session', 'source', max_rows=value)
    workspace.studio_service.execute.assert_not_called()


def test_unqualified_provider_must_not_silently_ignore_the_fetch_policy():
    workspace, _context = workspace_fixture('org.cdeadmin.other')
    with pytest.raises(ProviderWorkspaceError, match='has not admitted'):
        workspace.execute('server', 'session', 'source', max_rows=7)
    workspace.studio_service.execute.assert_not_called()
