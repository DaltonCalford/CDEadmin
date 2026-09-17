"""Native dialect binding precedes generated DDL and preserves source text."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from tools.tests.test_firebird_query_limits import client_fixture
from pgadmin.cdeadmin.providers.firebird.ddl_dialect import (
    generated_dialect, identifier_sql, observed_dialects, verify_binding,
)
from pgadmin.cdeadmin.providers.firebird.mappings import identifier
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def connection(database=1, client=3):
    return Mock(info=SimpleNamespace(sql_dialect=database), sql_dialect=client)


def request():
    return {'resource_kind': 'table', 'operation_id': 'create',
            '_provider_route': {'database': 'fixture'},
            'draft': {'name': 'T', 'columns': [{
                'name': 'V', 'column_mode': 'STORED', 'data_type': 'VARCHAR',
                'length': 20, 'has_default': True, 'default_kind': 'TEXT',
                'default_value': 'A"B'}]}}


@pytest.mark.parametrize('name', ['T', 'ID', 'RDB$NAME', '_NAME', 'A' * 63])
def test_legacy_identifiers_are_rendered_not_case_folded(name):
    with generated_dialect(1):
        assert identifier(name) == name
        assert ADMINISTRATION._quote(name) == name
    assert identifier(name) == '"' + name + '"'


@pytest.mark.parametrize('name', ['t', 'Mixed', 'A B', 'A"B', '東京', '1T'])
def test_unrepresentable_legacy_name_never_becomes_another_identifier(name):
    with pytest.raises(RelationalClientError, match='uppercase'):
        with generated_dialect(1):
            identifier(name)
    assert identifier('T') == '"T"'


def test_nested_and_concurrent_compilers_do_not_share_dialect():
    with generated_dialect(1):
        with generated_dialect(3):
            assert identifier('T') == '"T"'
        assert identifier('T') == 'T'

    def compile_name(dialect):
        with generated_dialect(dialect):
            return identifier('T')
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(compile_name, [1, 3] * 20))
    assert values == ['T', '"T"'] * 20
    assert identifier_sql('T') == '"T"'


@pytest.mark.parametrize('database', [1, 3])
def test_planner_observes_and_binds_native_dialect_without_changing_literals(
        database):
    client, _, _ = client_fixture()
    handle = connection(database)
    with patch.object(client, '_connect', return_value=handle) as attach, \
            patch.object(client, '_forget_and_close') as close:
        plan = client.plan_admin_operation(request())
    attach.assert_called_once_with({'route': {'database': 'fixture'}})
    close.assert_called_once_with(handle)
    binding = {'database_sql_dialect': database, 'client_sql_dialect': 3}
    assert plan['provider_payload']['firebird_dialect_binding'] == binding
    assert plan['command_preview']['firebird_dialect_binding'] == binding
    source = plan['command_preview']['statements'][0]['source']
    assert "DEFAULT 'A\"B'" in source
    assert source.startswith('CREATE TABLE T' if database == 1 else
                             'CREATE TABLE "T"')
    assert source == plan['provider_payload']['compiled']['statements'][0][
        'source']
    handle.cursor.assert_not_called()
    handle.commit.assert_not_called()
    handle.rollback.assert_not_called()


def test_user_written_view_definition_is_not_rewritten():
    value = {'resource_kind': 'view', 'operation_id': 'create',
             '_provider_route': {'database': 'fixture'},
             'draft': {'name': 'V', 'definition': 'SELECT "ID" FROM T'}}
    with generated_dialect(1):
        plan = ADMINISTRATION.plan(value)
    assert 'SELECT "ID" FROM T' in plan['command_preview']['statements'][0][
        'source']


@pytest.mark.parametrize('database,client', [
    (True, 3), ('1', 3), (2, 3), (1, 1), (3, None)])
def test_unknown_dialects_are_not_guessed(database, client):
    with pytest.raises(RelationalClientError):
        observed_dialects(connection(database, client))


@pytest.mark.parametrize('expected', [
    {}, {'database_sql_dialect': True, 'client_sql_dialect': 3},
    {'database_sql_dialect': 3, 'client_sql_dialect': 3},
    {'database_sql_dialect': 1, 'client_sql_dialect': 1},
])
@pytest.mark.parametrize('borrowed', [False, True])
def test_apply_rejects_stale_binding_before_cursor_savepoint_or_rollback(
        expected, borrowed):
    client, _, _ = client_fixture()
    handle = connection(1)
    plan = ADMINISTRATION.plan(request())
    plan['provider_payload']['firebird_dialect_binding'] = expected
    with patch.object(client, '_connect', return_value=handle), \
            patch.object(client, '_forget_and_close') as close:
        with pytest.raises(RelationalClientError, match='binding'):
            ADMINISTRATION.apply(client, plan,
                                 connection=handle if borrowed else None)
    assert close.call_count == (0 if borrowed else 1)
    handle.cursor.assert_not_called()
    handle.commit.assert_not_called()
    handle.rollback.assert_not_called()


def test_observation_failure_still_releases_temporary_attachment():
    client, _, _ = client_fixture()
    handle = connection(2)
    with patch.object(client, '_connect', return_value=handle), \
            patch.object(client, '_forget_and_close') as close:
        with pytest.raises(RelationalClientError):
            client.plan_admin_operation(request())
    close.assert_called_once_with(handle)


def test_matching_binding_is_read_only():
    handle = connection(1)
    verify_binding(handle, {
        'database_sql_dialect': 1, 'client_sql_dialect': 3})
    handle.cursor.assert_not_called()


@pytest.mark.parametrize('operation', ['insert', 'update', 'delete'])
def test_row_planning_keeps_identity_single_use_and_skips_ddl_probe(operation):
    client, _, _ = client_fixture()
    plan = {'provider_payload': {
        'route': {'database': 'fixture'},
        'compiled': {'statements': [{'source': 'native row operation'}]}}}
    adapter = Mock()
    adapter.plan.side_effect = [plan, AssertionError('identity reused')]
    value = {**request(), 'operation_id': operation}
    with patch.object(client, '_administration', return_value=adapter), \
            patch.object(client, '_connect') as attach:
        assert client.plan_admin_operation(value) is plan
    adapter.plan.assert_called_once_with(value)
    attach.assert_not_called()


def test_modern_ddl_is_compiled_only_once():
    client, _, _ = client_fixture()
    adapter = Mock()
    plan = ADMINISTRATION.plan(request())
    adapter.plan.side_effect = [plan, AssertionError('unnecessary recompile')]
    with patch.object(client, '_administration', return_value=adapter), \
            patch.object(client, '_connect', return_value=connection(3)), \
            patch.object(client, '_forget_and_close'):
        assert client.plan_admin_operation(request()) is plan
    adapter.plan.assert_called_once()
