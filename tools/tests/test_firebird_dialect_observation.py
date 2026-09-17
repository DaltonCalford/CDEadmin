"""Client and stored database dialects are independent native observations."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.tests.test_firebird_transaction_state import connection
from pgadmin.cdeadmin.providers.firebird.transaction_state import (
    observe_transaction,
)


@pytest.mark.parametrize('client', [1, 2, 3])
@pytest.mark.parametrize('database', [1, 3])
@pytest.mark.parametrize('active', [False, True])
def test_dialects_are_independent_and_observation_does_not_execute(
        client, database, active):
    handle = connection(active=active)
    handle.sql_dialect = client
    handle.info = SimpleNamespace(sql_dialect=database)
    handle.cursor = Mock(side_effect=AssertionError('Must not execute SQL'))
    result = observe_transaction(handle)
    assert result['attachment_fields'] == {
        'client_sql_dialect': {'available': True, 'value': client},
        'database_sql_dialect': {'available': True, 'value': database},
    }
    assert result['state'] == ('active' if active else 'idle')
    handle.cursor.assert_not_called()
    assert handle.main_transaction.method_calls == [
        ('is_active', (), {}), ('is_closed', (), {})]


@pytest.mark.parametrize('invalid', [
    None, True, False, 0, 4, '3', 3.0, [], {}])
@pytest.mark.parametrize('field', [
    'client_sql_dialect', 'database_sql_dialect'])
def test_invalid_observation_does_not_hide_other_fields(
        invalid, field):
    handle = connection()
    handle.sql_dialect = invalid if field == 'client_sql_dialect' else 3
    handle.info = SimpleNamespace(
        sql_dialect=invalid if field == 'database_sql_dialect' else 1)
    result = observe_transaction(handle)
    assert result['attachment_fields'][field] == {
        'available': False, 'error_type': 'ValueError'}
    other = ('database_sql_dialect' if field == 'client_sql_dialect'
             else 'client_sql_dialect')
    assert result['attachment_fields'][other]['available'] is True
    assert result['fields']['transaction_id']['value'] == 321


def test_failed_native_info_is_redacted_and_client_dialect_is_preserved():
    class Handle:
        sql_dialect = 3
        main_transaction = connection(active=False).main_transaction

        @property
        def info(self):
            raise RuntimeError('password=secret')

    result = observe_transaction(Handle())
    assert result['attachment_fields']['database_sql_dialect'] == {
        'available': False, 'error_type': 'RuntimeError'}
    assert result['attachment_fields']['client_sql_dialect']['value'] == 3
    assert 'secret' not in str(result)
    assert result['state'] == 'idle'
