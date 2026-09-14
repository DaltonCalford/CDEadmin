"""Character metadata tasks use native Firebird grammar and field contracts."""

import itertools

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import (
    ADMINISTRATION, RelationalClientError,
)
from pgadmin.cdeadmin.providers.firebird.character_metadata import (
    compile_operation, specific_attributes, recreation, OPERATIONS,
)
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine
from pgadmin.cdeadmin.context_menu import resource_context_actions


def draft(**values):
    return {'name': 'Owned', 'character_set': 'UTF8',
            'base_collation': 'UNICODE', **values}


@pytest.mark.parametrize('padding,case,accent', itertools.product(
    ('INHERIT', 'PAD_SPACE', 'NO_PAD'),
    ('INHERIT', 'SENSITIVE', 'INSENSITIVE'),
    ('INHERIT', 'SENSITIVE', 'INSENSITIVE'),
))
def test_every_flag_category_inherits_or_emits_its_native_clause(
        padding, case, accent):
    source, = compile_operation('collation', 'create', draft(
        padding=padding, case_sensitivity=case, accent_sensitivity=accent))
    expected = 'CREATE COLLATION "Owned" FOR "UTF8" FROM "UNICODE"'
    if padding != 'INHERIT':
        expected += ' ' + padding.replace('_', ' ')
    if case != 'INHERIT':
        expected += ' CASE ' + case
    if accent != 'INHERIT':
        expected += ' ACCENT ' + accent
    assert source == expected


@pytest.mark.parametrize('name', ['東京', 'quoted"name', "semi; ' --"])
def test_identifier_escaping_is_separate_from_literal_escaping(name):
    statements = compile_operation('collation', 'create', draft(
        name=name, description="é ' comment; --"))
    quoted = '"' + name.replace('"', '""') + '"'
    assert statements[0].startswith('CREATE COLLATION ' + quoted + ' FOR ')
    assert statements[1] == ('COMMENT ON COLLATION ' + quoted +
                             " IS 'é '' comment; --'")


def test_external_and_same_name_have_distinct_native_syntax():
    assert compile_operation('collation', 'create', draft(
        source_mode='EXTERNAL', base_collation='', external_name="O'wn")) == [
            'CREATE COLLATION "Owned" FOR "UTF8" FROM EXTERNAL (\'O\'\'wn\')']
    assert compile_operation('collation', 'create', draft(
        source_mode='SAME_NAME', base_collation='')) == [
            'CREATE COLLATION "Owned" FOR "UTF8"']


@pytest.mark.parametrize('value', ['x' * 64, '東' * 64, '\ud800', '\x00', 1])
def test_external_base_must_fit_native_metadata_name(value):
    with pytest.raises(RelationalClientError):
        compile_operation('collation', 'create', draft(
            source_mode='EXTERNAL', base_collation='', external_name=value))


@pytest.mark.parametrize('value', ['x' * 63, '東' * 63])
def test_external_base_character_boundary_is_not_an_ascii_byte_limit(value):
    assert compile_operation('collation', 'create', draft(
        source_mode='EXTERNAL', base_collation='', external_name=value)) == [
            'CREATE COLLATION "Owned" FOR "UTF8" FROM EXTERNAL (\'' +
            value + '\')']


def test_specific_attribute_escaping_order_and_inherited_removal():
    records = [{'name': 'NUMERIC-SORT', 'value': '1'},
               {'name': 'NUMERIC-SORT', 'value': ''},
               {'name': 'CUSTOM', 'value': r'one;two=three\four'},
               {'name': 'REMOVED'}]
    assert specific_attributes(records) == (
        r'NUMERIC-SORT=1;NUMERIC-SORT=;CUSTOM=one\;two\=three\\four;REMOVED=')


@pytest.mark.parametrize('value', [
    None, {}, 'raw SQL', [None], [{}], [{'name': 'NUMERIC-SORT', 'value': 1}],
    [{'name': 'OPTION1', 'value': 'x'}], [{'name': 'A;B', 'value': 'x'}],
    [{'name': 'A', 'value': 'x', 'unrecognized': True}],
])
def test_invalid_attribute_records_are_rejected(value):
    with pytest.raises(RelationalClientError):
        specific_attributes(value)


@pytest.mark.parametrize('values', [
    {'source_mode': 'UNKNOWN'}, {'source_mode': 'SAME_NAME'},
    {'external_name': 'conflicts'}, {'padding': 'UNKNOWN'},
    {'case_sensitivity': False}, {'accent_sensitivity': []},
    {'name': ''}, {'name': 'x' * 64}, {'name': '\x00'}, {'name': '\ud800'},
    {'unknown': 'field'}, {'character_set': None},
])
def test_invalid_or_conflicting_create_fields(values):
    with pytest.raises(RelationalClientError):
        compile_operation('collation', 'create', draft(**values))


def test_only_native_lifecycle_operations_are_admitted():
    assert OPERATIONS['collation'] == {'inspect', 'create', 'comment', 'drop'}
    assert OPERATIONS['character-set'] == {'inspect', 'alter', 'comment'}
    for kind, operation in [('collation', 'alter'), ('collation', 'rename'),
                            ('character-set', 'create'),
                            ('character-set', 'drop')]:
        with pytest.raises(RelationalClientError):
            compile_operation(kind, operation, {})


def test_drop_confirmation_and_charset_default_are_exact():
    target = {'display_name': 'Owned'}
    with pytest.raises(RelationalClientError, match='exact collation name'):
        compile_operation('collation', 'drop', {'confirmation': 'owned'},
                          target)
    assert compile_operation('collation', 'drop', {'confirmation': 'Owned'},
                             target) == ['DROP COLLATION "Owned"']
    assert compile_operation('character-set', 'alter', {
        'default_collation': 'Owned'}, {'display_name': 'UTF8'}) == [
            'ALTER CHARACTER SET "UTF8" SET DEFAULT COLLATION "Owned"']


@pytest.mark.parametrize('mask', range(8))
def test_recreation_uses_external_base_and_all_observed_bits(mask):
    statements = recreation('collation', 'Owned', {
        'attributes': str(mask), 'character_set': 'UTF8',
        'base_collation': 'UNICODE', 'specific_attributes': 'NUMERIC-SORT=1',
        'description': 'comment'})
    source = statements[0]
    assert 'FROM EXTERNAL (\'UNICODE\')' in source
    assert (' PAD SPACE' if mask & 1 else ' NO PAD') in source
    assert (' CASE INSENSITIVE' if mask & 2 else ' CASE SENSITIVE') in source
    accent_clause = ' ACCENT INSENSITIVE' if mask & 4 else ' ACCENT SENSITIVE'
    assert accent_clause in source
    assert source.endswith(" 'NUMERIC-SORT=1'")
    assert statements[1] == 'COMMENT ON COLLATION "Owned" IS \'comment\''


@pytest.mark.parametrize('attributes', [None, -1, 8, '8', True, 'garbage'])
def test_recreation_never_invents_unknown_attribute_bits(attributes):
    with pytest.raises(RelationalClientError, match='unavailable or unknown'):
        recreation('collation', 'Owned', {'attributes': attributes})


def test_catalog_forms_and_operation_filters_are_provider_owned():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    for kind, operations in OPERATIONS.items():
        entry = next(item for item in catalog['objects']
                     if item['resource_kind'] == kind)
        assert {item['operation_id'] for item in entry['operations']} == (
            operations)
        for operation in entry['operations']:
            action = operation['operation_id']
            if action == 'inspect':
                continue
            assert operation['form']['form_id'] == f'firebird.{kind}.{action}'
            assert all(field['field_id'] not in {'definition', 'changes'}
                       for field in operation['form']['fields'])
            if kind == 'character-set':
                assert operation['allow_system_target'] is True
    create = ADMINISTRATION._form('collation', 'create')
    attributes = next(field for field in create['fields']
                      if field['field_id'] == 'specific_attributes')
    assert attributes['array_editor']['item_kind'] == 'object'
    assert [field['field_id'] for field in attributes['array_editor'][
        'fields']] == ['name', 'value']


@pytest.mark.parametrize('kind', ['character-set', 'collation'])
@pytest.mark.parametrize('available', [True, False])
def test_system_character_metadata_context_uses_explicit_native_admission(
        kind, available):
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    for item in catalog['objects']:
        for operation in item['operations']:
            operation['execution_available'] = available
    resource = {
        'resource_id': kind + ':UTF8', 'resource_kind': kind,
        'display_name': 'UTF8',
        'extensions': {'firebird': {'native': {'system_object': True}}}}
    actions = {item['command_id']: item for item in resource_context_actions(
        {'engine_id': 'firebird'}, resource, catalog)}
    prefix = 'resource.firebird.' + kind + '.'
    assert actions[prefix + 'comment']['enabled'] is available
    if kind == 'character-set':
        assert actions[prefix + 'alter']['enabled'] is available
    for unsupported in ('drop', 'rename', 'grant', 'revoke'):
        assert prefix + unsupported not in actions


def test_admin_plan_executes_the_same_compiler_not_generic_definition_sql():
    planned = ADMINISTRATION.plan({
        'resource_kind': 'collation', 'operation_id': 'create',
        'draft': draft(), '_provider_route': {'database': '/owned/test.fdb'},
        'target_resource': None})
    assert planned['provider_payload']['compiled']['statements'] == [{
        'source': 'CREATE COLLATION "Owned" FOR "UTF8" FROM "UNICODE"',
        'parameters': ()}]
