##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

from unittest.mock import Mock
from types import SimpleNamespace
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION  # noqa: F401
from pgadmin.cdeadmin.providers.firebird.task_savepoint import (
    NativeTaskSavepoint,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def test_native_savepoint_releases_only_its_own_boundary():
    cursor = Mock()
    task = NativeTaskSavepoint(cursor)
    other = NativeTaskSavepoint(cursor)
    assert task.name != other.name
    assert len(task.name) <= 63
    task.begin()
    task.release()
    assert cursor.execute.call_args_list[0].args == (
        'SAVEPOINT ' + task.name,)
    assert cursor.execute.call_args_list[1].args == (
        'RELEASE SAVEPOINT ' + task.name + ' ONLY',)
    assert task.active is False


def test_failed_task_uses_native_savepoint_rollback_not_transaction_rollback():
    cursor = Mock()
    task = NativeTaskSavepoint(cursor)
    assert task.rollback() is False
    task.begin()
    assert task.rollback() is True
    assert [call.args[0] for call in cursor.execute.call_args_list] == [
        'SAVEPOINT ' + task.name,
        'ROLLBACK TO SAVEPOINT ' + task.name,
        'RELEASE SAVEPOINT ' + task.name + ' ONLY']
    assert task.active is False
    assert task.rollback() is False


@pytest.mark.parametrize('stage', ['begin', 'release', 'rollback'])
def test_native_boundary_failures_are_not_fabricated_as_success(stage):
    cursor = Mock()
    task = NativeTaskSavepoint(cursor)
    if stage != 'begin':
        task.begin()
    cursor.execute.side_effect = RuntimeError('native boundary failed')
    with pytest.raises(RuntimeError, match='native boundary failed'):
        getattr(task, stage)()
    assert task.active is (stage != 'begin')


def test_active_boundary_cannot_be_overwritten():
    cursor = Mock()
    task = NativeTaskSavepoint(cursor)
    task.begin()
    with pytest.raises(RuntimeError, match='already active'):
        task.begin()
    cursor.execute.assert_called_once()


@pytest.mark.parametrize('failure', [
    'cursor', 'begin', 'mutation', 'rollback', 'release',
    'verification-cleanup',
])
def test_executor_never_ends_caller_transaction_on_task_failure(failure):
    cursor = Mock(description=None, rowcount=1)

    def execute(source):
        if ((failure == 'begin' and source.startswith('SAVEPOINT ')) or
                (failure in {'mutation', 'rollback'} and
                 source.startswith('INSERT ')) or
                (failure in {'rollback', 'verification-cleanup'} and
                 source.startswith('ROLLBACK TO ')) or
                (failure == 'release' and source.startswith('RELEASE '))):
            raise RuntimeError('sensitive-native-message')

    cursor.execute.side_effect = execute
    connection = Mock()
    connection.cursor.return_value = cursor
    if failure == 'cursor':
        connection.cursor.side_effect = RuntimeError('cursor unavailable')
    client = SimpleNamespace(config=SimpleNamespace(
        execute_on_connection=False), _safe_close=Mock(),
        _forget_and_close=Mock(), _connect=Mock())
    statement = {'source': 'INSERT INTO T VALUES (2, 20)', 'parameters': ()}
    if failure == 'verification-cleanup':
        statement['expected_rowcount'] = 2
    with pytest.raises(RelationalClientError) as raised:
        ADMINISTRATION.apply(client, {'provider_payload': {
            'route': {'database': 'test'},
            'compiled': {'statements': [statement]},
        }}, connection=connection)
    assert 'sensitive-native-message' not in str(raised.value)
    if failure in {'rollback', 'release', 'verification-cleanup'}:
        assert 'transaction remains caller-owned' in str(raised.value)
        assert raised.value.task_rollback_unconfirmed is True
    connection.rollback.assert_not_called()
    connection.commit.assert_not_called()
    client._forget_and_close.assert_not_called()
    client._connect.assert_not_called()
    if failure in {'cursor', 'begin'}:
        assert not any(call.args[0].startswith('INSERT ')
                       for call in cursor.execute.call_args_list)


@pytest.mark.parametrize('native_error', [True, False])
@pytest.mark.parametrize('rollback_fails', [True, False])
def test_execution_and_task_rollback_preserve_structured_status_codes(
        native_error, rollback_fails):
    error = (RuntimeError('private driver text') if native_error else
             RelationalClientError('safe verification failure'))
    error.gds_codes = (335544351, 336068830)
    cursor = Mock(description=None, rowcount=1)

    def execute(source):
        if source.startswith('CREATE COLLATION '):
            raise error
        if rollback_fails and source.startswith('ROLLBACK TO '):
            raise RuntimeError('private rollback text')

    cursor.execute.side_effect = execute
    connection = Mock()
    connection.cursor.return_value = cursor
    client = SimpleNamespace(config=SimpleNamespace(
        execute_on_connection=False), _safe_close=Mock(),
        _forget_and_close=Mock(), _connect=Mock())
    with pytest.raises(RelationalClientError) as caught:
        ADMINISTRATION.apply(client, {'provider_payload': {
            'route': {'database': 'owned'}, 'compiled': {'statements': [{
                'source': 'CREATE COLLATION "C" FOR UTF8 FROM UNICODE',
                'parameters': (),
            }]}}}, connection=connection)
    assert caught.value.gds_codes == error.gds_codes
    if native_error or rollback_fails:
        assert caught.value.native_status_codes == error.gds_codes
    assert getattr(caught.value, 'task_rollback_unconfirmed', False) is (
        rollback_fails)
    assert 'private' not in str(caught.value)
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()
    client._forget_and_close.assert_not_called()
