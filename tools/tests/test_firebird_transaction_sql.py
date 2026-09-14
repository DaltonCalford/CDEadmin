"""Exact transaction SQL dispatch without corrupting driver state."""

import pytest
from unittest.mock import Mock, patch

from tools.tests.test_firebird_async_queries import rig  # noqa: F401
from pgadmin.cdeadmin.providers.firebird.transaction_sql import (
    transaction_command,
    starts_transaction,
    start_native_transaction,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


@pytest.mark.parametrize('action', ['commit', 'rollback'])
@pytest.mark.parametrize('work', ['', ' WORK'])
@pytest.mark.parametrize('retain', ['', ' RETAIN', ' RETAIN SNAPSHOT'])
def test_exact_native_grammar(action, work, retain):
    text = '/* start */ ' + action + work + retain + '; -- end'
    assert transaction_command(text) == (action, bool(retain))


@pytest.mark.parametrize('source', [
    'ROLLBACK TO SAVEPOINT X', 'ROLLBACK WORK TO "X"',
    'COMMIT RETAINING', 'COMMIT SNAPSHOT', 'COMMIT WORK WORK',
    'COMMIT; SELECT 1', 'COMMIT;;', 'COMMIT /* unclosed',
    '/* outer /* inner */ tail */ COMMIT',
    'COMM/* not a joined keyword */IT', 'COMMIT_OWNED',
    "SELECT 'COMMIT'", '"COMMIT"', "q'{COMMIT;}'", 'COMMIT\u2003WORK',
    None, '', '-- nothing',
])
def test_other_or_invalid_sql_is_not_interpreted_as_finality(source):
    assert transaction_command(source) is None


@pytest.mark.parametrize('character', [
    chr(value) for value in range(33) if value not in (9, 10, 12, 13, 32)])
def test_non_native_control_whitespace_cannot_finalize_work(character):
    assert transaction_command(character + 'COMMIT') is None
    assert transaction_command('ROLLBACK' + character) is None
    assert transaction_command('COMMIT' + character + 'WORK') is None
    assert not starts_transaction('SET' + character + 'TRANSACTION')


@pytest.mark.parametrize('character', [' ', '\t', '\r', '\n', '\f'])
def test_exact_native_whitespace_is_accepted(character):
    assert transaction_command(character + 'COMMIT' + character + 'WORK') == (
        'commit', False)
    assert starts_transaction('SET' + character + 'TRANSACTION')


@pytest.mark.parametrize('action', ['commit', 'rollback'])
@pytest.mark.parametrize('retaining', [False, True])
def test_native_command_uses_transaction_method_not_cursor(
        rig, action, retaining):
    rig.handle.main_transaction.is_active.return_value = True
    source = action + (' WORK RETAIN SNAPSHOT' if retaining else '')
    token = rig.client.execute(rig.handle, {'source': source})
    getattr(rig.handle, action).assert_called_once_with(retaining=retaining)
    rig.handle.cursor.assert_not_called()
    receipt = rig.client.describe_result(token)['payload'][
        'transaction_action']
    assert receipt['native_call_made'] is True
    assert receipt['retaining_requested'] is retaining


@pytest.mark.parametrize('source', ['COMMIT', 'ROLLBACK RETAIN SNAPSHOT'])
def test_idle_command_does_not_start_or_claim_a_native_transaction(
        rig, source):
    rig.handle.main_transaction.is_active.return_value = False
    token = rig.client.execute(rig.handle, {'source': source})
    receipt = rig.client.describe_result(token)['payload'][
        'transaction_action']
    assert receipt['native_call_made'] is False
    rig.handle.begin.assert_not_called()
    rig.handle.commit.assert_not_called()
    rig.handle.rollback.assert_not_called()


def test_parameters_are_not_silently_ignored_by_transaction_sql(rig):
    with pytest.raises(RelationalClientError,
                       match='do not accept parameters'):
        rig.client.execute(rig.handle, {'source': 'COMMIT', 'parameters': [1]})
    rig.handle.commit.assert_not_called()


def test_async_command_reports_receipt_without_replaying_sql(rig):
    rig.handle.main_transaction.is_active.return_value = True
    token = rig.client.submit_query(rig.handle, {'source': 'COMMIT WORK'})
    token.worker.join(5)
    assert not token.worker.is_alive()
    result = rig.client.describe_result(token)
    assert result['complete']
    assert result['payload']['transaction_action']['action'] == 'commit'
    rig.handle.commit.assert_called_once_with(retaining=False)
    rig.handle.cursor.assert_not_called()


def test_native_transaction_error_is_redacted_and_not_retried(rig):
    rig.handle.main_transaction.is_active.return_value = True
    native_error = RuntimeError('private transaction canary')
    native_error.gds_codes = (335544794,)
    rig.handle.commit.side_effect = native_error
    token = rig.client.submit_query(rig.handle, {'source': 'COMMIT'})
    token.worker.join(5)
    result = rig.client.describe_result(token)
    assert result['payload']['error']['native_status_codes'] == [335544794]
    assert 'private transaction canary' not in str(result)
    rig.handle.commit.assert_called_once_with(retaining=False)
    rig.handle.commit.side_effect = None


@pytest.mark.parametrize('action', ['commit', 'rollback'])
@pytest.mark.parametrize('active', [True, False])
def test_transaction_buttons_preserve_idle_state_and_native_ownership(
        rig, action, active):
    rig.handle.main_transaction.is_active.return_value = active
    receipt = rig.client.control_transaction(rig.handle, action)
    assert receipt['native_call_made'] is active
    if active:
        getattr(rig.handle, action).assert_called_once_with(retaining=False)
    else:
        getattr(rig.handle, action).assert_not_called()
    rig.handle.begin.assert_not_called()
    assert rig.client._tokens == []


def test_transaction_buttons_do_not_dispatch_arbitrary_native_methods(rig):
    with pytest.raises(RelationalClientError, match='unavailable'):
        rig.client.control_transaction(rig.handle, 'drop_database')
    rig.handle.drop_database.assert_not_called()


@pytest.mark.parametrize('source', [
    'SET TRANSACTION', 'set /* gap */ transaction read only;',
    '-- lead\nSET\nTRANSACTION SNAPSHOT', 'SET TRANSACTION /* incomplete',
    'SET TRANSACTION INVALID NATIVE OPTION',
])
def test_transaction_start_is_routed_without_interpreting_options(source):
    assert starts_transaction(source)


@pytest.mark.parametrize('source', [
    None, '', 'SET ROLE X', 'SET TRANSACTIONAL', 'SET TRANSACTIONé',
    'SETTRANSACTION', "SELECT 'SET TRANSACTION'", '/* open SET TRANSACTION',
    'SET "TRANSACTION"', 'SET TRANSACTION$X', 'ſET TRANSACTION',
])
def test_other_statements_are_not_transaction_starts(source):
    assert not starts_transaction(source)


@pytest.mark.parametrize('asynchronous', [False, True])
def test_native_transaction_start_adopts_returned_handle(rig, asynchronous):
    rig.handle.main_transaction.is_active.return_value = False
    native = Mock()
    source = 'SET TRANSACTION READ ONLY SNAPSHOT'
    with patch('pgadmin.cdeadmin.providers.firebird.query_client.'
               'start_native_transaction', return_value=native) as start:
        if asynchronous:
            token = rig.client.submit_query(rig.handle, {'source': source})
            token.worker.join(5)
        else:
            token = rig.client.execute(rig.handle, {'source': source})
        start.assert_called_once_with(rig.handle, source)
    assert rig.handle.main_transaction._tra is native
    rig.handle.main_transaction._finish.assert_called_once_with()
    assert rig.client.describe_result(token)['payload'][
        'transaction_action']['action'] == 'begin'
    rig.handle.begin.assert_not_called()
    rig.handle.cursor.assert_not_called()


def test_set_transaction_never_replaces_an_active_transaction(rig):
    rig.handle.main_transaction.is_active.return_value = True
    with patch('pgadmin.cdeadmin.providers.firebird.query_client.'
               'start_native_transaction') as start:
        with pytest.raises(RelationalClientError, match='pending work'):
            rig.client.execute(rig.handle, {'source': 'SET TRANSACTION'})
        start.assert_not_called()
    rig.handle.commit.assert_not_called()
    rig.handle.rollback.assert_not_called()
    rig.handle.main_transaction._finish.assert_not_called()


def test_set_transaction_rejects_parameters_without_native_start(rig):
    rig.handle.main_transaction.is_active.return_value = False
    with patch('pgadmin.cdeadmin.providers.firebird.query_client.'
               'start_native_transaction') as start:
        with pytest.raises(RelationalClientError, match='accept parameters'):
            rig.client.execute(rig.handle, {
                'source': 'SET TRANSACTION', 'parameters': [42]})
        start.assert_not_called()
    rig.handle.main_transaction._finish.assert_not_called()


def test_invalid_native_start_preserves_driver_status_and_idle_state(rig):
    rig.handle.main_transaction.is_active.return_value = False
    rig.handle.main_transaction._tra = None
    error = RuntimeError('private canary')
    error.gds_codes = (335544569,)
    with patch('pgadmin.cdeadmin.providers.firebird.query_client.'
               'start_native_transaction', side_effect=error):
        with pytest.raises(RelationalClientError) as failure:
            rig.client.execute(rig.handle, {'source': 'SET TRANSACTION BAD'})
    assert failure.value.gds_codes == (335544569,)
    assert 'private canary' not in str(failure.value)
    assert rig.handle.main_transaction._tra is None


def test_native_start_bridge_passes_null_input_and_exact_encoded_source():
    handle = Mock(sql_dialect=3)
    handle._att.encoding = 'utf-8'
    pointer = object()
    handle._att.vtable.execute.return_value = pointer
    source = 'SET TRANSACTION RESERVING "é" FOR SHARED READ'
    with patch('firebird.driver.interfaces.iTransaction') as wrapper:
        result = start_native_transaction(handle, source)
        wrapper.assert_called_once_with(pointer)
        assert result is wrapper.return_value
    encoded = source.encode('utf-8')
    handle._att.vtable.execute.assert_called_once_with(
        handle._att, handle._att.status, None, len(encoded), encoded,
        3, None, None, None, None)
    handle._att._check.assert_called_once_with()


@pytest.mark.parametrize('native_error', [False, True])
def test_native_start_bridge_never_constructs_a_null_or_failed_handle(
        native_error):
    handle = Mock(sql_dialect=3)
    handle._att.encoding = 'utf-8'
    handle._att.vtable.execute.return_value = None
    if native_error:
        handle._att._check.side_effect = RuntimeError('native error')
    with patch('firebird.driver.interfaces.iTransaction') as wrapper:
        with pytest.raises(RuntimeError):
            start_native_transaction(handle, 'SET TRANSACTION')
        wrapper.assert_not_called()
