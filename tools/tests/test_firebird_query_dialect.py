"""Statement dialect admission and ownership are not attachment mutations."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tools.tests.test_firebird_query_limits import client_fixture
from pgadmin.cdeadmin.providers.firebird.query_dialect import (
    DialectCursor, requested_dialect,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.workspace.service import (
    ProviderWorkspaceService, ProviderWorkspaceError,
)


@pytest.mark.parametrize('value', [1, 2, 3, None])
def test_supported_values(value):
    assert requested_dialect({'output_policy': {
        'client_sql_dialect': value}}) == value


@pytest.mark.parametrize('value', [True, False, 0, 4, '1', 1.0, [], {}])
@pytest.mark.parametrize('asynchronous', [False, True])
def test_invalid_dialect_never_dispatches(value, asynchronous):
    client, connection, _ = client_fixture()
    with pytest.raises(RelationalClientError, match='dialect'):
        (client.submit_query if asynchronous else client.execute)(connection, {
            'source': 'SELECT 1 FROM RDB$DATABASE',
            'output_policy': {'client_sql_dialect': value}})
    connection.cursor.assert_not_called()
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()
    assert not client._queries


@pytest.mark.parametrize('dialect', [1, 2, 3])
def test_workspace_passes_selected_dialect_without_changing_endpoint(dialect):
    service = object.__new__(ProviderWorkspaceService)
    service.endpoint_service = Mock()
    context = SimpleNamespace(provider_id='org.cdeadmin.firebird')
    service.endpoint_service.workspace.return_value = (context, {}, None)
    service.studio_service = Mock()
    service.execute('server', 'session', 'SQL', client_sql_dialect=dialect)
    policy = service.studio_service.execute.call_args.kwargs['output_policy']
    assert policy == {
        'redact_keys': [], 'client_sql_dialect': dialect}


def test_other_engines_cannot_select_firebird_dialect():
    service = object.__new__(ProviderWorkspaceService)
    service.endpoint_service = Mock()
    service.endpoint_service.workspace.return_value = (
        SimpleNamespace(provider_id='other'), {}, None)
    service.studio_service = Mock()
    with pytest.raises(ProviderWorkspaceError, match='only admitted'):
        service.execute('server', 'session', 'SQL', client_sql_dialect=1)
    service.studio_service.execute.assert_not_called()


def test_external_statement_is_freed_only_after_result_cursor_closes():
    handle = Mock()
    cursor = DialectCursor(handle, 1)
    statement = Mock()
    cursor.statement = statement
    cursor.cursor.close.side_effect = RuntimeError('close failure')
    with pytest.raises(RuntimeError):
        cursor.close()
    statement.free.assert_not_called()
    assert cursor.statement is statement
    cursor.cursor.close.side_effect = None
    cursor.close()
    statement.free.assert_called_once()
    assert cursor.statement is None


def test_default_path_uses_existing_cursor_without_native_adapter():
    client, handle, cursor = client_fixture([(1,)])
    handle.sql_dialect = 3
    with patch('pgadmin.cdeadmin.providers.firebird.query_client.'
               'DialectCursor') as adapter:
        token = client.execute(handle, {'source': 'SQL', 'output_policy': {
            'client_sql_dialect': 3}})
        result = client.describe_result(token)
    adapter.assert_not_called()
    assert result['payload']['statement_sql_dialect'] == 3
    cursor.execute.assert_called_once_with('SQL')


@pytest.mark.parametrize('stage', [
    'prepare', 'metadata', 'execute', 'success'])
@pytest.mark.parametrize('active', [False, True])
def test_native_preparation_ownership_and_pending_transaction(stage, active):
    handle = Mock()
    handle._statements = []
    handle.main_transaction.is_active.return_value = active
    native = handle._att.prepare.return_value
    if stage == 'prepare':
        handle._att.prepare.side_effect = RuntimeError('prepare')
    if stage == 'execute':
        handle.cursor.return_value.execute.side_effect = RuntimeError(
            'execute')

    class Statement:
        def __init__(self, connection, statement, sql, dialect):
            assert connection is handle
            assert statement is native
            assert sql == 'unchanged source' and dialect == 1
            if stage == 'metadata':
                raise RuntimeError('metadata')

        def free(self):
            if self._istmt is not None:
                self._istmt.free()
                self._istmt = None

    cursor = DialectCursor(handle, 1)
    assert cursor.cursor._dialect == 1
    with patch('firebird.driver.core.Statement', Statement):
        if stage == 'success':
            cursor.execute('unchanged source', (7,))
        else:
            with pytest.raises(RuntimeError, match=stage):
                cursor.execute('unchanged source', (7,))
    assert len(handle._statements) == 1
    assert handle._statements[0]() is cursor.statement
    handle._att.prepare.assert_called_once_with(
        handle.main_transaction._tra, 'unchanged source', 1)
    assert handle.main_transaction.begin.call_count == (0 if active else 1)
    cursor.close()
    assert native.free.call_count == (0 if stage == 'prepare' else 1)
    handle.commit.assert_not_called()
    handle.rollback.assert_not_called()


def test_failed_statement_cleanup_retains_owner_and_quarantines_session():
    client, handle, _ = client_fixture()
    wrapper = Mock()
    wrapper.execute.side_effect = RuntimeError('private execution canary')
    wrapper.close.side_effect = RuntimeError('private cleanup canary')
    with patch.object(client, '_query_cursor', return_value=wrapper):
        with pytest.raises(RelationalClientError) as error:
            client.execute(handle, {'source': 'SQL'})
    assert 'canary' not in str(error.value)
    assert client._state(handle).failed_cursors == [wrapper]
    with pytest.raises(RelationalClientError, match='cleanup failed'):
        client.execute(handle, {'source': 'SQL'})
    wrapper.execute.assert_called_once()
