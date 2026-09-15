"""Firebird current, next, restart and recreation semantics stay distinct."""

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import sequences
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine


TARGET = {'resource_kind': 'sequence', 'display_name': 'S"東京'}
QUOTED = '"S""東京"'


@pytest.mark.parametrize('value', [-(2 ** 63), -1, 0, 1, 2 ** 63 - 1])
@pytest.mark.parametrize('as_text', [False, True])
def test_full_int64_values_keep_exact_current_versus_next_semantics(
        value, as_text):
    selected = str(value) if as_text else value
    assert sequences.compile_operation('set_current', {
        'current': selected, 'confirmation': TARGET['display_name']},
        TARGET) == [
        'SET GENERATOR ' + QUOTED + ' TO ' + str(value)]
    assert sequences.compile_operation('alter', {'restart': selected},
                                       TARGET) == [
        'ALTER SEQUENCE ' + QUOTED + ' RESTART WITH ' + str(value)]
    assert sequences.compile_operation('create', {
        'name': TARGET['display_name'], 'start': selected}) == [
        'CREATE SEQUENCE ' + QUOTED + ' START WITH ' + str(value)]


@pytest.mark.parametrize('value', [-(2 ** 31) + 1, -1, 1, 2 ** 31 - 1])
def test_native_increment_range_is_int32(value):
    assert sequences.compile_operation('alter', {'increment': value},
                                       TARGET) == [
        'ALTER SEQUENCE ' + QUOTED + ' INCREMENT BY ' + str(value)]


@pytest.mark.parametrize('value', [0, -(2 ** 31), -(2 ** 31) - 1, 2 ** 31,
                                   2 ** 63 - 1, False, 1.5, '1; DROP TABLE T'])
def test_invalid_increment_never_reaches_execution(value):
    with pytest.raises(RelationalClientError):
        sequences.compile_operation('alter', {'increment': value}, TARGET)


@pytest.mark.parametrize('value', [-(2 ** 63) - 1, 2 ** 63, False, 1.5,
                                   '1e3', None, 'nan', '1; DROP TABLE T',
                                   '9' * 10000])
def test_invalid_current_values_are_not_rounded_or_evaluated(value):
    with pytest.raises(RelationalClientError):
        sequences.compile_operation('set_current', {
            'current': value, 'confirmation': TARGET['display_name']}, TARGET)


def test_native_creation_and_original_restart_have_no_fabricated_clauses():
    assert sequences.compile_operation('create', {'name': 'S'}) == [
        'CREATE SEQUENCE "S"']
    assert sequences.compile_operation('create', {
        'name': 'S', 'increment': -1}) == [
        'CREATE SEQUENCE "S" INCREMENT BY -1']
    assert sequences.compile_operation('alter', {'restart_initial': True},
                                       TARGET) == [
        'ALTER SEQUENCE ' + QUOTED + ' RESTART']


@pytest.mark.parametrize('operation,key', [('alter', 'restart'),
                                           ('create_or_alter', 'start')])
def test_explicit_and_original_restart_are_mutually_exclusive(operation, key):
    with pytest.raises(RelationalClientError, match='Choose'):
        sequences.compile_operation(operation, {
            **({'name': 'S'} if operation == 'create_or_alter' else {}),
            key: '0', 'restart_initial': True}, TARGET)


def test_create_or_alter_requires_options_and_uses_start_not_restart_with():
    with pytest.raises(RelationalClientError, match='requires'):
        sequences.compile_operation('create_or_alter', {'name': 'S'})
    for values, suffix in (({'start': '0'}, 'START WITH 0'),
                           ({'restart_initial': True}, 'RESTART'),
                           ({'increment': -2}, 'INCREMENT BY -2')):
        actual = sequences.compile_operation('create_or_alter', {
            'name': 'S', **values})
        assert actual == ['CREATE OR ALTER SEQUENCE "S" ' + suffix]


@pytest.mark.parametrize('operation', ['drop', 'recreate', 'set_current'])
@pytest.mark.parametrize('confirmation', [None, '', 'S', QUOTED])
def test_destructive_and_position_tasks_require_exact_confirmation(
        operation, confirmation):
    with pytest.raises(RelationalClientError, match='Confirm'):
        sequences.compile_operation(operation, {'confirmation': confirmation},
                                    TARGET)


def test_recreate_does_not_fabricate_preservation_of_grants_or_position():
    assert sequences.compile_operation('recreate', {
        'confirmation': TARGET['display_name']}, TARGET) == [
        'RECREATE SEQUENCE ' + QUOTED]


def test_comment_only_alter_uses_comment_and_rejects_contradictory_controls():
    assert sequences.compile_operation('alter', {
        'description': "Owner's notes"}, TARGET) == [
        'COMMENT ON SEQUENCE ' + QUOTED + " IS 'Owner''s notes'"]
    assert sequences.compile_operation('alter', {
        'clear_description': True}, TARGET) == [
        'COMMENT ON SEQUENCE ' + QUOTED + ' IS NULL']
    with pytest.raises(RelationalClientError, match='not both'):
        sequences.compile_operation('alter', {
            'description': 'notes', 'clear_description': True}, TARGET)


@pytest.mark.parametrize('field', ['minimum', 'maximum', 'cycle', 'cache',
                                   'data_type', 'owner', 'statement'])
def test_nonexistent_sequence_options_are_not_accepted(field):
    with pytest.raises(RelationalClientError, match='Unknown'):
        sequences.compile_operation('create', {'name': 'S', field: 1})


@pytest.mark.parametrize('operation', sorted(
    sequences.OPERATIONS - {'inspect'}))
def test_native_sequence_forms_use_text_for_wide_values_and_int32_for_step(
        operation):
    form = sequences.form(operation, ADMINISTRATION._field)
    assert form['form_id'] == 'firebird.sequence.' + operation
    fields = {field['field_id']: field for field in form['fields']}
    for key in {'start', 'restart', 'current'} & fields.keys():
        assert fields[key]['control'] == 'text'
    if 'increment' in fields:
        assert fields['increment']['minimum'] == -(2 ** 31) + 1
        assert fields['increment']['maximum'] == 2 ** 31 - 1
    assert not {'minimum', 'maximum', 'cycle', 'cache'} & fields.keys()


@pytest.mark.parametrize('operation', sorted(
    sequences.OPERATIONS - {'inspect'}))
def test_provider_validation_normalization_and_plans_use_native_compiler(
        operation):
    values = {
        'create': {'name': TARGET['display_name'], 'start': '10'},
        'create_or_alter': {'name': TARGET['display_name'], 'start': '10'},
        'alter': {'restart': '10'},
        'set_current': {'current': '10',
                        'confirmation': TARGET['display_name']},
        'recreate': {'confirmation': TARGET['display_name'], 'start': '10'},
        'comment': {'description': "Owner's notes"},
        'drop': {'confirmation': TARGET['display_name']},
    }[operation]
    request = {'resource_kind': 'sequence', 'operation_id': operation,
               'target_resource': TARGET, 'draft': values,
               '_provider_route': {'database': 'owned'}}
    assert ADMINISTRATION.validate(request) == {'errors': []}
    planned = ADMINISTRATION.plan(request)
    assert [item['source'] for item in
            planned['command_preview']['statements']] == (
                sequences.compile_operation(operation, values, TARGET))
    request['draft'] = {**values, 'cascade': True}
    assert ADMINISTRATION.validate(request)['errors']
    with pytest.raises(RelationalClientError):
        ADMINISTRATION.plan(request)


@pytest.mark.parametrize('operation', sorted(
    sequences.OPERATIONS - {'inspect', 'create', 'create_or_alter'}))
@pytest.mark.parametrize('target', [None, {}, {
    'resource_kind': 'table', 'display_name': 'OTHER'}])
def test_sequence_tasks_cannot_retarget_to_missing_or_other_object_kinds(
        operation, target):
    with pytest.raises(RelationalClientError, match='inspected sequence'):
        sequences.compile_operation(operation, {}, target)


def test_recreate_submits_every_displayed_definition_even_when_unchanged():
    fields = {item['field_id']: item for item in
              sequences.form('recreate', ADMINISTRATION._field)['fields']}
    assert all(fields[key]['submit_unchanged'] for key in
               ('start', 'increment', 'description'))
    altered = {item['field_id']: item for item in
               sequences.form('alter', ADMINISTRATION._field)['fields']}
    assert not altered['increment']['submit_unchanged']
    assert not altered['description']['submit_unchanged']


@pytest.mark.parametrize('value,expected', [
    ('+' + '0' * 10000 + '1', '1'), ('-' + '0' * 10000, '0'),
    ('-' + '0' * 10000 + '9223372036854775808', '-9223372036854775808')])
def test_leading_zeros_do_not_overflow_python_conversion(value, expected):
    assert sequences.number(value, 'Value') == expected


def test_sequence_catalog_is_task_specific_and_keeps_bound_security_actions():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    sequence = next(item for item in catalog['objects']
                    if item['resource_kind'] == 'sequence')
    operations = {item['operation_id']: item
                  for item in sequence['operations']}
    assert set(operations) == sequences.OPERATIONS | {'grant', 'revoke'}
    for operation in sequences.OPERATIONS - {'inspect'}:
        record = operations[operation]
        assert record['form']['form_id'] == 'firebird.sequence.' + operation
        assert record['target_required'] is (operation not in {
            'create', 'create_or_alter'})
        assert record['confirmation_required'] is True
    assert operations['set_current']['mutation_class'] == 'destructive'
