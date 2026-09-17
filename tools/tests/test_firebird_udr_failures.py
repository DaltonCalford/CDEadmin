"""External module resolution belongs to Firebird, not guessed client rules."""
import pytest
from types import SimpleNamespace
from unittest.mock import Mock

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from tools.cdeadmin_firebird_udr_failures_gate import cases
from pgadmin.cdeadmin.providers.firebird.query_client import (
    COMMIT_FAILURE_NOTICE, FirebirdQueryClient,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


@pytest.mark.parametrize('kind', ['function', 'procedure'])
@pytest.mark.parametrize('case,entry,message', cases())
def test_external_resolution_is_left_to_native_preparation(
        kind, case, entry, message):
    declaration = ('(X INTEGER) RETURNS ' +
                   ('INTEGER' if kind == 'function' else '(Y INTEGER)') +
                   f" EXTERNAL NAME '{entry}' ENGINE UDR")
    request = {
        'resource_kind': kind, 'operation_id': 'create_or_alter',
        '_provider_route': {'database': 'owned'},
        'draft': {'name': 'UDR_TEST', 'declaration': declaration}}
    assert ADMINISTRATION.validate(request) == {'errors': []}
    plan = ADMINISTRATION.plan(request)
    statements = plan['command_preview']['statements']
    assert len(statements) == 1
    assert statements[0]['source'] == (
        f'CREATE OR ALTER {kind.upper()} "UDR_TEST"\n' + declaration)
    assert case and message


def test_failure_inventory_is_complete_and_distinct():
    fixtures = cases()
    assert len(fixtures) == 4
    assert {item[0] for item in fixtures} == {
        'missing-module', 'missing-entry', 'invalid-entry', 'invalid-module'}
    assert len({item[1] for item in fixtures}) == 4


@pytest.mark.parametrize('action', ['commit', 'rollback'])
@pytest.mark.parametrize('stage', ['observation', 'command'])
@pytest.mark.parametrize('valid', [True, False])
def test_transaction_error_exposes_only_bounded_native_identity(
        action, stage, valid):
    error = RuntimeError('PRIVATE server path and password')
    error.gds_codes = (335544382,) if valid else ['PRIVATE']
    error.sqlstate = 'HY000' if valid else 'PRIVATE'
    error.errno = -901 if valid else 'PRIVATE'
    handle = SimpleNamespace(
        main_transaction=SimpleNamespace(is_active=Mock(return_value=True)),
        commit=Mock(), rollback=Mock())
    failing = (handle.main_transaction.is_active if stage == 'observation'
               else getattr(handle, action))
    failing.side_effect = error
    with pytest.raises(RelationalClientError) as caught:
        FirebirdQueryClient._finish_transaction(handle, action)
    shown = str(caught.value)
    assert 'PRIVATE' not in shown
    assert ('335544382' in shown) is valid
    assert ('sqlstate=HY000' in shown) is valid
    assert ('errno=-901' in shown) is valid
    assert caught.value.gds_codes == ((335544382,) if valid else ())
    assert caught.value.native_status_codes == caught.value.gds_codes
    assert (COMMIT_FAILURE_NOTICE in shown) is (action == 'commit')
    if stage == 'observation':
        handle.commit.assert_not_called()
        handle.rollback.assert_not_called()
    else:
        getattr(handle, action).assert_called_once_with(retaining=False)
        getattr(handle, 'rollback' if action == 'commit' else
                'commit').assert_not_called()
