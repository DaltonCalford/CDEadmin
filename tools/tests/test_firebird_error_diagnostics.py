##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def test_only_native_numeric_status_codes_are_retained():
    value = SimpleNamespace(gds_codes=(335544351, 335544352),
                            message='password=do-not-retain')
    assert status_codes(value) == (335544351, 335544352)
    assert 'password' not in str(status_codes(value))


@pytest.mark.parametrize('value', [
    None, 'password', {}, [True], [0], [-1], [2147483648], ['335544352'],
    [335544352] * 33,
])
def test_malformed_status_codes_do_not_enter_diagnostics(value):
    assert status_codes(SimpleNamespace(gds_codes=value)) == ()


def test_faulty_status_property_cannot_replace_the_original_failure():
    class Failure:
        @property
        def gds_codes(self):
            raise RuntimeError('secret')
    assert status_codes(Failure()) == ()


def test_executor_reports_codes_without_native_message_arguments():
    class NativeFailure(Exception):
        gds_codes = (335544351, 335544352)
    cursor = Mock()

    def execute(source):
        if source.startswith('SELECT '):
            raise NativeFailure('password=do-not-retain')

    cursor.execute.side_effect = execute
    connection = Mock()
    connection.cursor.return_value = cursor
    client = SimpleNamespace(config=SimpleNamespace(
        execute_on_connection=False), _safe_close=lambda _cursor: None)
    with pytest.raises(RelationalClientError) as failure:
        ADMINISTRATION.apply(client, {'provider_payload': {
            'route': {'database': 'test'}, 'compiled': {'statements': [
                {'source': 'SELECT 1 FROM RDB$DATABASE', 'parameters': ()}]}}},
            connection=connection)
    assert '335544351, 335544352' in str(failure.value)
    assert 'password' not in str(failure.value)
    assert 'do-not-retain' not in str(failure.value)
    connection.rollback.assert_not_called()
    assert any(call.args[0].startswith('ROLLBACK TO SAVEPOINT ')
               for call in cursor.execute.call_args_list)
