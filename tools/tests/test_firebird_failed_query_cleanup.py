"""No failed cursor cleanup may leave a reusable Firebird attachment."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from tools.tests.test_firebird_query_limits import client_fixture
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('stage', ['execute', 'fetch', 'columns', 'values'])
@pytest.mark.parametrize('typed_error', [False, True])
@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_failed_query_cleanup_controls_native_session_reuse(
        asynchronous, stage, typed_error, cleanup_fails):
    client, handle, cursor = client_fixture([(1,)])
    handle.is_closed.return_value = False
    handle.close.side_effect = lambda: setattr(
        handle.is_closed, 'return_value', True)
    original = (RelationalClientError('Safe validation error') if typed_error
                else RuntimeError('PRIVATE-QUERY-VALUE'))
    original.gds_codes = (335544665,)
    cleanup = RuntimeError('PRIVATE-CLEANUP-VALUE')
    cleanup.gds_codes = (335544344,)
    if stage == 'execute':
        cursor.execute.side_effect = original
    elif stage == 'fetch':
        cursor.fetchmany.side_effect = original
    elif stage == 'columns':
        client.config = replace(
            client.config, query_columns_reader=Mock(side_effect=original))
    else:
        client.config = replace(client.config,
                                query_value_normalizer=Mock(
                                    side_effect=original))
    if cleanup_fails:
        cursor.close.side_effect = cleanup
    request = {'source': 'SELECT N FROM OWNED_SOURCE',
               'output_policy': {'max_rows': 1}}
    if asynchronous:
        token = client.submit_query(handle, request)
        token.worker.join(3)
        assert token.done
        result = client.describe_result(token)['payload']
        assert result['execution_state'] == 'failed'
        assert result['error']['native_status_codes'] == [335544665]
        assert result.get('session_reuse_blocked', False) is cleanup_fails
        if cleanup_fails:
            assert result['session_reuse_blocked_reason'] == (
                'result_cleanup_failed')
            assert result['error']['cleanup_status_codes'] == [335544344]
            assert 'Do not replay' in result['error']['message']
        assert 'PRIVATE-' not in str(result)
    else:
        with pytest.raises(RelationalClientError) as caught:
            client.execute(handle, request)
        assert caught.value.gds_codes == (335544665,)
        if cleanup_fails:
            assert caught.value.cleanup_gds_codes == (335544344,)
            assert caught.value.cursor_cleanup_failed is True
        assert 'PRIVATE-' not in str(caught.value)
    cursor.close.assert_called_once_with()
    assert not client._tokens
    handle.commit.assert_not_called()
    handle.rollback.assert_not_called()
    assert client._state(handle).result_cleanup_failed is cleanup_fails
    cursor.execute.side_effect = None
    cursor.fetchmany.side_effect = lambda count: [(2,)]
    client.config = replace(client.config, query_columns_reader=None,
                            query_value_normalizer=None)
    if cleanup_fails:
        before = cursor.execute.call_count
        with pytest.raises(RelationalClientError,
                           match='explicitly reconnect'):
            client.execute(handle, request)
        with pytest.raises(RelationalClientError,
                           match='explicitly reconnect'):
            client.submit_query(handle, request)
        assert cursor.execute.call_count == before
    else:
        result = client.execute(handle, request)
        assert result.rows == [(2,)]
    client.close_session(handle)
    assert not client._connections
