"""Exact legacy UDF grammar, structured controls and unsafe-input rejection."""

import itertools

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import external_functions as udf
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine


def draft(**values):
    return {'name': 'Owned', 'arguments': [{'data_type': 'INTEGER'}],
            'return_data_type': 'INTEGER', 'return_mechanism': 'VALUE',
            'entrypoint': 'owned', 'module_name': 'owned_udf', **values}


@pytest.mark.parametrize('input_mode,return_mode', itertools.product(
    udf.INPUT_MECHANISMS, udf.RETURN_MECHANISMS))
def test_every_argument_and_return_mechanism(input_mode, return_mode):
    source, = udf.compile_operation('create', draft(
        arguments=[{'data_type': 'INTEGER', 'mechanism': input_mode}],
        return_mechanism=return_mode))
    argument = 'INTEGER' + {'REFERENCE': '', 'NULL': ' NULL',
                            'DESCRIPTOR': ' BY DESCRIPTOR',
                            'SCALAR_ARRAY': ' BY SCALAR_ARRAY'}[input_mode]
    returned = 'INTEGER' + {'REFERENCE': '', 'VALUE': ' BY VALUE',
                            'DESCRIPTOR': ' BY DESCRIPTOR',
                            'FREE_IT': ' FREE_IT',
                            'DESCRIPTOR_FREE_IT':
                            ' BY DESCRIPTOR FREE_IT'}[return_mode]
    assert source == ('DECLARE EXTERNAL FUNCTION "Owned" ' + argument +
                      ' RETURNS ' + returned +
                      " ENTRY_POINT 'owned' MODULE_NAME 'owned_udf'")


def test_return_parameter_uses_existing_one_based_argument():
    values = draft(return_mode='PARAMETER', return_parameter=1)
    values.pop('return_data_type')
    values.pop('return_mechanism')
    assert 'RETURNS PARAMETER 1' in udf.compile_operation('create', values)[0]
    values['arguments'][0]['mechanism'] = 'SCALAR_ARRAY'
    with pytest.raises(RelationalClientError, match='cannot be returned'):
        udf.compile_operation('create', values)


@pytest.mark.parametrize('mode', ['ENTRY_POINT', 'MODULE_NAME', 'BOTH'])
def test_alter_only_library_bindings(mode):
    values = {'alter_target': mode}
    expected = 'ALTER EXTERNAL FUNCTION "Owned"'
    if mode in ('BOTH', 'ENTRY_POINT'):
        values['entrypoint'] = "own'ed"
        expected += " ENTRY_POINT 'own''ed'"
    if mode in ('BOTH', 'MODULE_NAME'):
        values['module_name'] = 'other'
        expected += " MODULE_NAME 'other'"
    assert udf.compile_operation('alter', values, {
        'display_name': 'Owned'}) == [expected]


def test_unicode_identifier_comment_and_literal_escaping():
    source, comment = udf.compile_operation('create', draft(
        name='Owned"東京', entrypoint="own'ed", description="text ' é\n  "))
    assert '"Owned""東京"' in source
    assert "ENTRY_POINT 'own''ed'" in source
    assert comment == 'COMMENT ON FUNCTION "Owned""東京" IS \'text \'\' é\n  \''


def test_empty_comment_clears_and_exact_drop_confirmation_is_required():
    target = {'display_name': 'Owned'}
    assert udf.compile_operation('comment', {}, target) == [
        'COMMENT ON FUNCTION "Owned" IS NULL']
    drop = udf.compile_operation('drop', {'confirmation': 'Owned'}, target)
    assert drop == ['DROP EXTERNAL FUNCTION "Owned"']
    with pytest.raises(RelationalClientError):
        udf.compile_operation('drop', {'confirmation': 'owned'}, target)


@pytest.mark.parametrize('values', [
    {'arguments': [{}]}, {'arguments': None}, {'arguments': 'SQL'},
    {'arguments': [{'data_type': 'INTEGER', 'mechanism': 'VALUE'}]},
    {'arguments': [{'data_type': 'DOMAIN', 'domain': 'D'}]},
    {'arguments': [{'data_type': 'BLOB', 'blob_subtype': 1}]},
    {'arguments': [{'data_type': 'INTEGER', 'length': 32}]},
    {'arguments': [{'data_type': 'INTEGER'}] * 16},
    {'return_data_type': 'BLOB', 'return_mechanism': 'REFERENCE',
     'arguments': [{'data_type': 'INTEGER'}] * 15},
    {'return_mode': 'PARAMETER', 'return_parameter': 1},
    {'return_mode': 'invalid'}, {'return_parameter': 1},
    {'return_mechanism': 'NULL'}, {'return_data_type': 'CSTRING',
                                   'return_length': 32},
    {'entrypoint': ''}, {'entrypoint': 'é' * 128},
    {'module_name': 'a' * 256}, {'module_name': '\x00'},
    {'entrypoint': '\ud800'}, {'name': 'a' * 64},
    {'options': {}}, {'definition': 'SELECT 1'},
])
def test_invalid_or_conflicting_declarations(values):
    with pytest.raises(RelationalClientError):
        udf.compile_operation('create', draft(**values))


@pytest.mark.parametrize('values,expected', [
    ({'data_type': 'CSTRING', 'length': 32, 'character_set': 'UTF8'},
     'CSTRING(32) CHARACTER SET "UTF8"'),
    ({'data_type': 'BLOB'}, 'BLOB'),
    ({'data_type': 'NUMERIC', 'precision': 38, 'scale': 12},
     'NUMERIC(38, 12)'),
    ({'data_type': 'DECFLOAT', 'precision': 16}, 'DECFLOAT(16)'),
    ({'data_type': 'TIMESTAMP', 'time_zone': 'WITH TIME ZONE'},
     'TIMESTAMP WITH TIME ZONE'),
])
def test_type_compiler_does_not_admit_table_column_syntax(values, expected):
    assert udf.type_sql(values) == expected


def test_forms_offer_typed_ordered_arguments_not_raw_declarations():
    for operation in udf.OPERATIONS - {'inspect'}:
        form = udf.form(operation, ADMINISTRATION._field)
        assert form['form_id'] == 'firebird.external-function.' + operation
        assert all(item['field_id'] not in {'definition', 'options'}
                   for item in form['fields'])
    fields = {item['field_id']: item for item in
              udf.form('create', ADMINISTRATION._field)['fields']}
    editor = fields['arguments']['array_editor']
    assert editor['item_kind'] == 'object'
    assert any(item['field_id'] == 'mechanism' for item in editor['fields'])
    assert fields['return_parameter']['visible_when'] == {
        'field_id': 'return_mode', 'equals': 'PARAMETER'}


def test_catalog_and_planner_use_only_native_udf_lifecycle_operations():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    entry = next(item for item in catalog['objects']
                 if item['resource_kind'] == 'external-function')
    assert entry['title'] == 'Legacy external function'
    assert {item['operation_id'] for item in entry['operations']} == (
        udf.OPERATIONS | {'grant', 'revoke'})
    for action, values in (
            ('create', draft()),
            ('alter', {'alter_target': 'ENTRY_POINT', 'entrypoint': 'other'}),
            ('comment', {'description': ''}),
            ('drop', {'confirmation': 'Owned'})):
        request = {'resource_kind': 'external-function',
                   'operation_id': action, 'draft': values,
                   '_provider_route': {'host': '127.0.0.1', 'port': 53050,
                                       'database': '/owned.fdb',
                                       'user': 'SYSDBA'},
                   'target_resource': {'display_name': 'Owned',
                                       'display_path': ['Owned']}}
        assert not ADMINISTRATION.validate(request)['errors']
        plan = ADMINISTRATION.plan(request)
        assert [item['source'] for item in
                plan['command_preview']['statements']] == (
                    udf.compile_operation(action, values,
                                          request['target_resource']))
        operation = next(item for item in entry['operations']
                         if item['operation_id'] == action)
        assert operation['target_required'] == (action != 'create')
        assert operation['form']['form_id'] == (
            'firebird.external-function.' + action)
        assert operation['title'] == operation['form']['title']
    assert not ADMINISTRATION.supports('external-function', 'rename')
    assert not ADMINISTRATION.supports('external-function', 'create_or_alter')
