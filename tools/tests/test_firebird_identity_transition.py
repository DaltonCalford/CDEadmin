##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird.identity import (
    column_rename, verify_column_rename,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def plan():
    return ADMINISTRATION.plan({
        '_provider_route': {'database': 'owned-test'},
        'resource_kind': 'column', 'operation_id': 'rename',
        'target_resource': {'display_path': ['T', 'V']},
        'draft': {'new_name': 'W'},
    })


def test_native_names_are_not_split_into_schemas():
    value = column_rename(['T:a%', 'old.name'], 'new:name%')
    assert value['previous_resource_id'] == 'column:T%3Aa%25:old.name'
    assert value['resource_id'] == 'column:T%3Aa%25:new%3Aname%25'


@pytest.mark.parametrize('path,name', [
    (['V'], 'W'), (['S', 'T', 'V'], 'W'), (['T', 'V'], 'V'),
    (['T', 'V'], ''), (['T', 'V'], 'bad\x00name'),
])
def test_invalid_rename_identities_are_rejected(path, name):
    with pytest.raises(RelationalClientError):
        column_rename(path, name)


@pytest.mark.parametrize('rows,remaining', [
    ([], 0), ([(1, 2)], 0), ([(1, 3)], 1),
    ([(None, None)], 0), ([(True, 3)], 0), ([('1', 3)], 0),
])
def test_native_identity_mismatch_is_rejected(rows, remaining):
    cursor = Mock()
    cursor.fetchall.return_value = rows
    cursor.fetchone.return_value = (remaining,)
    with pytest.raises(RelationalClientError):
        verify_column_rename(cursor, column_rename(['T', 'V'], 'W'), (1, 3))


@pytest.mark.parametrize('owned', [False, True])
@pytest.mark.parametrize('fails', [False, True])
def test_receipt_requires_native_identity_and_preserves_transaction_owner(
        owned, fails):
    cursor = Mock(description=None, rowcount=-1)
    cursor.fetchall.side_effect = [[(4, 7)], [(4, 8 if fails else 7)]]
    cursor.fetchone.return_value = (0,)
    connection = Mock()
    connection.cursor.return_value = cursor
    client = SimpleNamespace(config=SimpleNamespace(
        execute_on_connection=False), _connect=lambda _request: connection,
        _safe_close=Mock(), _forget_and_close=Mock())
    request = plan()
    assert request['warnings']
    if fails:
        with pytest.raises(RelationalClientError):
            ADMINISTRATION.apply(client, request,
                                 connection=None if owned else connection)
        connection.commit.assert_not_called()
        assert connection.rollback.call_count == int(owned)
        if not owned:
            assert any(call.args[0].startswith('ROLLBACK TO SAVEPOINT ')
                       for call in cursor.execute.call_args_list)
    else:
        result = ADMINISTRATION.apply(
            client, request, connection=None if owned else connection)
        receipt = result['resource_identity_change']
        assert receipt['resource_id'] == 'column:T:W'
        assert receipt['previous_resource_id'] == 'column:T:V'
        assert receipt['field_id'] == 7
        assert receipt['native_identity_verified'] is True
        assert receipt['committed_by_provider'] is owned
        assert receipt['staged_in_provider_session'] is not owned
        assert connection.commit.call_count == int(owned)
        connection.rollback.assert_not_called()
    assert client._forget_and_close.call_count == int(owned)


def test_commit_failure_never_returns_a_committed_identity_receipt():
    cursor = Mock(description=None, rowcount=-1)
    cursor.fetchall.side_effect = [[(4, 7)], [(4, 7)]]
    cursor.fetchone.return_value = (0,)
    connection = Mock()
    connection.cursor.return_value = cursor
    connection.commit.side_effect = RuntimeError('native failure')
    client = SimpleNamespace(config=SimpleNamespace(
        execute_on_connection=False), _connect=lambda _request: connection,
        _safe_close=Mock(), _forget_and_close=Mock())
    with pytest.raises(RelationalClientError):
        ADMINISTRATION.apply(client, plan())
    connection.rollback.assert_called_once()
    client._forget_and_close.assert_called_once_with(connection)
