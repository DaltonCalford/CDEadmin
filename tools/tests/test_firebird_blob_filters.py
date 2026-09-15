"""Native BLOB-filter grammar and structured, non-invented task controls."""

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import blob_filters as filters
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine
from pgadmin.cdeadmin.navigator import resource_children
from pgadmin.cdeadmin.context_menu import resource_group_context_actions


def draft(**values):
    return {'name': 'Owned', 'input_subtype': -1, 'output_subtype': 1,
            'entrypoint': 'owned_filter', 'module_name': 'owned_filter',
            **values}


@pytest.mark.parametrize('number', [-32768, -1, 0, 1, 32767])
def test_signed_native_subtype_range(number):
    source, = filters.compile_operation('create', draft(input_subtype=number))
    assert source == ('DECLARE FILTER "Owned" INPUT_TYPE ' + str(number) +
                      ' OUTPUT_TYPE 1 ENTRY_POINT \'owned_filter\' '
                      'MODULE_NAME \'owned_filter\'')


@pytest.mark.parametrize('prefix', ['input', 'output'])
def test_exact_registered_mnemonic_is_quoted(prefix):
    values = draft()
    values.pop(prefix + '_subtype')
    values.update({prefix + '_mode': 'MNEMONIC',
                   prefix + '_mnemonic': 'Owned"東京'})
    source, = filters.compile_operation('create', values)
    assert prefix.upper() + '_TYPE "Owned""東京"' in source


def test_names_literals_and_comment_recreation():
    source, comment = filters.compile_operation('create', draft(
        name='Owned"東京', entrypoint="own'ed", module_name="lib'filter",
        description="note ' é\n  "))
    assert 'FILTER "Owned""東京"' in source
    assert "ENTRY_POINT 'own''ed' MODULE_NAME 'lib''filter'" in source
    assert comment == 'COMMENT ON FILTER "Owned""東京" IS \'note \'\' é\n  \''


def test_empty_comment_and_explicit_drop():
    target = {'display_name': 'Owned'}
    assert filters.compile_operation('comment', {}, target) == [
        'COMMENT ON FILTER "Owned" IS NULL']
    assert filters.compile_operation('drop', {'confirmation': 'Owned'},
                                     target) == ['DROP FILTER "Owned"']
    with pytest.raises(RelationalClientError, match='exact'):
        filters.compile_operation('drop', {'confirmation': 'owned'}, target)


@pytest.mark.parametrize('values', [
    {'input_subtype': -32769}, {'input_subtype': 32768},
    {'output_subtype': True}, {'input_subtype': 1.2},
    {'input_subtype': '1; DROP DATABASE'}, {'input_subtype': None},
    {'input_mnemonic': 'TEXT'}, {'output_mode': 'MNEMONIC'},
    {'output_mode': 'MNEMONIC', 'output_mnemonic': 'TEXT'},
    {'output_mode': 'SQL'}, {'input_mode': []},
    {'module_name': ''}, {'module_name': 'é' * 128},
    {'entrypoint': '\x00'}, {'entrypoint': '\ud800'},
    {'name': ''}, {'name': 'a' * 64}, {'description': {}},
    {'cascade': True}, {'arguments': []}, {'options': {}},
])
def test_invalid_or_unrelated_fields_are_rejected(values):
    with pytest.raises(RelationalClientError):
        filters.compile_operation('create', draft(**values))


@pytest.mark.parametrize('operation', [
    'alter', 'rename', 'create_or_alter', 'grant', 'revoke', 'execute',
    'inspect'])
def test_no_invented_mutations(operation):
    with pytest.raises(RelationalClientError):
        filters.compile_operation(operation, draft())


def test_forms_are_specific_and_contain_no_raw_definition_or_options():
    assert filters.OPERATIONS == {'inspect', 'create', 'comment', 'drop'}
    for operation in filters.OPERATIONS - {'inspect'}:
        form = filters.form(operation, ADMINISTRATION._field)
        assert form['form_id'] == 'firebird.blob-filter.' + operation
        assert all(item['field_id'] not in {
            'definition', 'options', 'arguments'}
            for item in form['fields'])
        if operation == 'create':
            fields = {item['field_id']: item for item in form['fields']}
            for prefix in ('input', 'output'):
                assert fields[prefix + '_subtype']['visible_when'] == {
                    'field_id': prefix + '_mode', 'equals': 'NUMBER'}
                assert fields[prefix + '_mnemonic']['visible_when'] == {
                    'field_id': prefix + '_mode', 'equals': 'MNEMONIC'}
        elif operation == 'comment':
            assert form['fields'][0]['submit_unchanged'] is True
            assert form['fields'][0]['initial_value_path'] == ['description']


def test_provider_catalog_admits_only_native_filter_tasks_and_owned_forms():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    item = next(item for item in catalog['objects']
                if item['resource_kind'] == 'blob-filter')
    assert item['title'] == 'BLOB filter'
    assert {item['operation_id'] for item in item['operations']} == (
        filters.OPERATIONS)
    for operation in item['operations']:
        assert ADMINISTRATION.supports('blob-filter',
                                       operation['operation_id'])
        if operation['operation_id'] != 'inspect':
            assert operation['form']['form_id'].startswith(
                'firebird.blob-filter.')
            assert operation['target_required'] is (
                operation['operation_id'] != 'create')
    assert 'parameters' not in item['editor']['sections']
    assert 'data' not in item['editor']['sections']


def test_empty_database_has_separate_filter_and_udf_creation_groups():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    groups = resource_children([], {'scope': 'database'}, catalog)
    assert {'BLOB filters', 'External functions'} <= {
        item['label'] for item in groups}
    item = next(item for item in catalog['objects']
                if item['resource_kind'] == 'blob-filter')
    assert item['navigator']['parent_kinds'] == ['database']
    assert item['navigator']['group_id'] == 'programmable'
    for operation in item['operations']:
        operation['execution_available'] = True
    actions = resource_group_context_actions(
        {'engine_id': 'firebird'}, 'blob-filter', catalog,
        database_target_id='owned-database')
    assert len(actions) == 1
    assert actions[0]['label'] == 'New BLOB filter'
    assert actions[0]['arguments']['database_target_id'] == 'owned-database'
    assert 'resource_id' not in actions[0]['arguments']


def test_provider_validation_and_preview_preserve_exact_native_statements():
    for action, values in (('create', draft()),
                           ('comment', {'description': ''}),
                           ('drop', {'confirmation': 'Owned'})):
        request = {'resource_kind': 'blob-filter', 'operation_id': action,
                   '_provider_route': {'host': '127.0.0.1', 'port': 53050,
                                       'database': '/owned.fdb',
                                       'user': 'SYSDBA'},
                   'draft': values, 'target_resource': {
                       'display_name': 'Owned', 'display_path': ['Owned']}}
        assert ADMINISTRATION.validate(request) == {'errors': []}
        plan = ADMINISTRATION.plan(request)
        assert [item['source'] for item in plan['command_preview'][
            'statements']] == filters.compile_operation(
                action, values, request['target_resource'])
        if action in {'create', 'drop'}:
            assert filters.WARNING in plan['warnings']
        request['draft'] = {**values, 'options': {}}
        assert ADMINISTRATION.validate(request)['errors'][0]['code'] == (
            'invalid_blob_filter')
