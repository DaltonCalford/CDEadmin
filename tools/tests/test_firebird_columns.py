##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

import copy
import json

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import columns
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine
from pgadmin.cdeadmin.visual_admin import ProviderVisualAdministration
from pgadmin.cdeadmin.visual_admin.provider import VisualAdminValidationError
from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_columns,
)


def request(draft, operation='alter'):
    return {'resource_kind': 'column', 'operation_id': operation,
            'draft': draft, '_provider_route': {'database': 'example.fdb'},
            'target_resource': {'display_name': 'V',
                                'display_path': ['T', 'V']}}


@pytest.mark.parametrize('action,values,clause', [
    ('POSITION', {'position': 2}, 'POSITION 2'),
    ('SET NOT NULL', {}, 'SET NOT NULL'),
    ('DROP NOT NULL', {}, 'DROP NOT NULL'),
    ('DROP DEFAULT', {}, 'DROP DEFAULT'),
    ('DROP IDENTITY', {}, 'DROP IDENTITY'),
    ('SET DEFAULT', {'default_kind': 'TEXT', 'default_value': "O'Connor"},
     "SET DEFAULT 'O''Connor'"),
    ('TYPE', {'data_type': 'NUMERIC', 'precision': 38, 'scale': 12},
     'TYPE NUMERIC(38, 12)'),
    ('COMPUTED', {'expression': '"X" + 1'}, 'COMPUTED BY ("X" + 1\n)'),
    ('TYPE COMPUTED', {'data_type': 'BIGINT', 'expression': '"X" + 1'},
     'TYPE BIGINT COMPUTED BY ("X" + 1\n)'),
    ('COMPUTED', {'expression': 'X + 2 -- comment'},
     'COMPUTED BY (X + 2 -- comment\n)'),
    ('IDENTITY', {'generation': 'ALWAYS', 'restart': 'WITH VALUE',
                  'restart_value': '-9223372036854775808', 'increment': '-1'},
     'SET GENERATED ALWAYS RESTART WITH -9223372036854775808 '
     'SET INCREMENT BY -1'),
    ('IDENTITY', {'restart': 'ORIGINAL'}, 'RESTART'),
])
def test_provider_compiles_exact_native_clause(action, values, clause):
    plan = ADMINISTRATION.plan(request({'action': action, **values}))
    assert plan['command_preview']['statements'][0]['source'] == (
        'ALTER TABLE "T" ALTER COLUMN "V" ' + clause)


@pytest.mark.parametrize('path', [
    ['current', 'V'], ['T', 'current'], ['current', 'current'],
    [' leading table', ' leading column'], ['table.name', 'column.name'],
    ['table"name', 'column"name'],
])
def test_physical_column_path_preserves_exact_identifiers(path):
    value = request({'action': 'POSITION', 'position': 1})
    value['target_resource']['display_path'] = path
    value['target_resource']['display_name'] = path[-1]
    plan = ADMINISTRATION.plan(value)
    quoted = ['"' + name.replace('"', '""') + '"' for name in path]
    assert plan['command_preview']['statements'][0]['source'] == (
        f'ALTER TABLE {quoted[0]} ALTER COLUMN {quoted[1]} POSITION 1')


@pytest.mark.parametrize('name', columns.TYPES)
def test_all_native_type_selectors_compile(name):
    value = {'data_type': name, 'length': 30, 'domain': 'DOMAIN NAME'}
    sql = columns.data_type(value)
    assert sql
    assert ';' not in sql


@pytest.mark.parametrize('empty', (None, ''))
def test_optional_type_parameters_accept_empty_controls(empty):
    assert columns.data_type({'data_type': 'NUMERIC', 'precision': empty,
                              'scale': empty}) == 'NUMERIC(9, 0)'
    assert columns.data_type({'data_type': 'DECFLOAT',
                              'precision': empty}) == 'DECFLOAT(34)'
    assert columns.data_type({'data_type': 'BLOB',
                              'blob_subtype': empty}) == 'BLOB SUB_TYPE 0'


@pytest.mark.parametrize('value', [
    {'action': 'POSITION', 'position': 0},
    {'action': 'POSITION', 'position': True},
    {'action': 'POSITION', 'position': '1, DROP X'},
    {'action': 'IDENTITY'},
    {'action': 'IDENTITY', 'increment': '0'},
    {'action': 'IDENTITY', 'increment': '2147483648'},
    {'action': 'IDENTITY', 'restart': 'WITH VALUE',
     'restart_value': '9223372036854775808'},
    {'action': 'COMPUTED', 'expression': '1); DROP TABLE T; --'},
    {'action': 'TYPE', 'data_type': 'INTEGER, DROP X'},
    {'action': 'TYPE', 'data_type': 'BLOB'},
    {'action': 'TYPE', 'data_type': 'NUMERIC', 'precision': 39},
    {'action': 'TYPE', 'data_type': 'NUMERIC', 'precision': 4, 'scale': 5},
    {'action': 'TYPE', 'data_type': 'DECFLOAT', 'precision': 20},
    {'action': 'TYPE COMPUTED', 'data_type': 'DOMAIN', 'domain': 'D',
     'expression': '1'},
    {'action': 'SET DEFAULT', 'default_kind': 'NUMBER',
     'default_value': '1, X'},
    {'action': 'SET DEFAULT', 'default_kind': 'SQL'},
])
def test_invalid_fields_are_rejected_before_execution(value):
    assert ADMINISTRATION.validate(request(value))['errors']
    with pytest.raises(RelationalClientError):
        ADMINISTRATION.plan(request(value))


def test_column_comments_are_quoted_and_can_be_cleared():
    sql = columns.compile_column('comment', {'description': "  'é'  "},
                                 ['T', 'V'])
    assert sql == 'COMMENT ON COLUMN "T"."V" IS \'  \'\'é\'\'  \''
    assert columns.compile_column('comment', {'description': ''},
                                  ['T', 'V']).endswith('IS NULL')


@pytest.mark.parametrize('kind', columns.TIMED_DEFAULTS)
@pytest.mark.parametrize('precision', range(4))
def test_native_time_default_precision(kind, precision):
    assert columns.default_value({'default_kind': kind,
                                  'time_precision': precision}) == (
        f'{kind}({precision})')


@pytest.mark.parametrize('value', (-1, 4, True, '3); DROP T'))
def test_invalid_default_precision(value):
    with pytest.raises(RelationalClientError):
        columns.default_value({'default_kind': 'CURRENT_TIME',
                               'time_precision': value})


def test_binary_default_uses_hex_bytes_and_numeric_plus_is_canonicalized():
    assert columns.default_value({'default_kind': 'BINARY',
                                  'default_value': 'aB01'}) == "X'AB01'"
    assert columns.default_value({'default_kind': 'NUMBER',
                                  'default_value': '+12'}) == '12'
    for value in ('a', 'GG', "AB'; DROP TABLE T", None):
        with pytest.raises(RelationalClientError):
            columns.default_value({'default_kind': 'BINARY',
                                   'default_value': value})


def test_catalog_exposes_both_structured_forms():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    obj = next(item for item in catalog['objects']
               if item['resource_kind'] == 'column')
    operations = {item['operation_id']: item for item in obj['operations']}
    for operation in ('alter', 'comment'):
        assert operations[operation]['target_required']
        assert operations[operation]['form']['form_id'] == (
            'firebird.column.' + operation)
    fields = operations['alter']['form']['fields']
    assert not {'definition', 'changes', 'options'} & {
        item['field_id'] for item in fields}
    choices = next(item['options'] for item in fields
                   if item['field_id'] == 'data_type')
    for action, included, excluded in (
            ('TYPE', 'DOMAIN', 'BLOB'),
            ('TYPE COMPUTED', 'BLOB', 'DOMAIN')):
        active = {item['value'] for item in choices
                  if ProviderVisualAdministration._field_active(
                      item, {'action': action})}
        assert included in active
        assert excluded not in active


@pytest.mark.parametrize('draft,visible,hidden', [
    ({'action': 'TYPE', 'data_type': 'INTEGER'}, ['data_type'],
     ['length', 'precision', 'domain', 'time_zone', 'default_value']),
    ({'action': 'TYPE', 'data_type': 'VARCHAR'}, ['length', 'character_set'],
     ['precision', 'domain', 'time_zone']),
    ({'action': 'TYPE', 'data_type': 'NUMERIC'}, ['precision', 'scale'],
     ['length', 'character_set', 'domain']),
    ({'action': 'SET DEFAULT', 'default_kind': 'TEXT'}, ['default_value'],
     ['length', 'time_zone', 'data_type']),
    ({'action': 'SET DEFAULT', 'default_kind': 'CURRENT_USER'},
     ['default_kind'],
     ['default_value', 'data_type']),
    ({'action': 'IDENTITY', 'restart': 'WITH VALUE'}, ['restart_value'],
     ['length', 'default_value']),
    ({'action': 'IDENTITY', 'restart': 'ORIGINAL'}, ['restart'],
     ['restart_value']),
])
def test_only_applicable_column_fields_are_visible(draft, visible, hidden):
    fields = columns.form('alter', ADMINISTRATION._field)['fields']
    active = {item['field_id'] for item in fields
              if ProviderVisualAdministration._field_active(item, draft)}
    assert set(visible) <= active
    assert not set(hidden) & active


@pytest.mark.parametrize('condition', [
    {'all': []}, {'all': None}, {'all': [None]}, {'all': ['action']},
])
def test_invalid_visibility_conjunctions_fail_closed(condition):
    with pytest.raises(VisualAdminValidationError):
        ProviderVisualAdministration._field_active(
            {'visible_when': condition}, {})


def proof():
    cases = {'position', 'set-not-null', 'drop-not-null', 'drop-default',
             'COMPUTED', 'TYPE COMPUTED', 'identity-always',
             'identity-by-default', 'drop-identity', 'comment-set',
             'comment-clear', 'permission-denied-alter',
             'permission-denied-comment', 'existing-null-rejected',
             'type-narrowing-rejected', 'computed-line-comment'} | {
        'default-' + kind for kind in columns.DEFAULTS
    } | {f'default-{kind}-{precision}' for kind in columns.TIMED_DEFAULTS
         for precision in range(4)} | {
        'type-' + kind for kind in columns.TYPES} | {
        'identity-state-' + str(number) for number in range(5)}
    return {'passed': True, 'engine_version': '5.0.4',
            'fixture_removed': True, 'temporary_user_removed': True,
            'failures': [], 'checks': [{'case': case} for case in cases],
            'task_evidence': {f'visual_admin.column.{operation}': {
                'live_execution': 'passed', 'statements': [statement]}
                for operation, statement in (
                    ('alter', 'ALTER TABLE "T" ALTER COLUMN "V" POSITION 1'),
                    ('comment', 'COMMENT ON COLUMN "T"."V" IS NULL'))}}


def contract():
    return json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                       'firebird_dialect_5_0_4.json').read_text())


def test_column_supplement_preserves_other_tasks_and_is_idempotent():
    original = contract()
    before = copy.deepcopy(original)
    result = supplement_columns(original, proof(), 'a' * 64, 'test.json')
    assert original == before
    assert len(result['task_templates']) == 64
    assert supplement_columns(result, proof(), 'a' * 64, 'test.json') == result


@pytest.mark.parametrize('changed', [
    {'passed': False}, {'engine_version': '5.0.3'}, {'fixture_removed': False},
    {'temporary_user_removed': False}, {'failures': ['failed']},
    {'checks': []}, {'task_evidence': {}},
])
def test_column_supplement_requires_complete_evidence(changed):
    with pytest.raises(ValueError):
        supplement_columns(contract(), {**proof(), **changed},
                           'a' * 64, 'test.json')
