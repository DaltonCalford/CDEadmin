##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

import copy
import json
from types import SimpleNamespace

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import columns
from pgadmin.cdeadmin.providers.firebird.provider import _catalog_resource_id
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine
from pgadmin.cdeadmin.visual_admin import ProviderVisualAdministration
from pgadmin.cdeadmin.visual_admin.provider import VisualAdminValidationError
from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_columns,
)
from tools.cdeadmin_firebird_columns_ui_gate import cleanup_column_fixtures
from tools.cdeadmin_firebird_ui_form_gate import (
    _draft_field_visible, fill_form_values,
)


@pytest.mark.parametrize('condition', [
    {'all': []}, {'all': None}, [], {'unknown': True},
])
def test_browser_record_helper_rejects_unknown_visibility(condition):
    with pytest.raises(ValueError):
        _draft_field_visible({'visible_when': condition}, {})


def test_browser_record_helper_uses_active_controls(monkeypatch):
    observed = []

    def fill(_wait, values, **_kwargs):
        observed.extend(values)

    monkeypatch.setattr(
        'tools.cdeadmin_firebird_ui_form_gate.fill_fields', fill)
    fields = [
        {'field_id': 'enabled', 'label': 'Enabled', 'control': 'boolean'},
        {'field_id': 'value', 'label': 'Value', 'control': 'text',
         'visible_when': {'all': [
             {'field_id': 'enabled', 'equals': True},
             {'field_id': 'mode', 'in': ['STORED']}]}},
        {'field_id': 'mode', 'label': 'Mode', 'control': 'text',
         'default': 'STORED'}]
    fill_form_values(None, None, fields, {'Enabled': 'true', 'Value': '42'})
    assert observed == ['Enabled=true', 'Value=42']
    observed.clear()
    fill_form_values(None, None, fields, {'Enabled': 'false', 'Value': '42'})
    assert observed == ['Enabled=false']
    with pytest.raises(ValueError, match='Unknown form label'):
        fill_form_values(None, None, fields, {'Unknown': '42'})


@pytest.mark.parametrize('quit_fails,drop_fails', [
    (False, False), (True, False), (False, True), (True, True),
])
def test_fixture_cleanup_collects_failures_and_attempts_remaining_objects(
        quit_fails, drop_fails):
    events = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def fetchone(self):
            return (1,)

        def execute(self, sql, parameters=()):
            events.append(sql)
            if drop_fails and '"T2"' in sql:
                raise OSError('simulated native failure')

    def quit_browser():
        events.append('quit')
        if quit_fails:
            raise OSError('simulated browser failure')

    native = SimpleNamespace(
        cursor=Cursor, commit=lambda: events.append('commit'),
        rollback=lambda: events.append('rollback'),
        close=lambda: events.append('close'),
        main_transaction=SimpleNamespace(is_active=lambda: True))
    result = cleanup_column_fixtures(
        SimpleNamespace(quit=quit_browser), native, ['T1', 'T2'], 'D')
    assert result['fixtures_removed'] is (not drop_fails)
    assert len(result['errors']) == int(quit_fails) + int(drop_fails)
    assert [item for item in events if item.startswith('DROP')] == [
        'DROP TABLE "T2"', 'DROP TABLE "T1"', 'DROP DOMAIN "D"']
    assert events[-1] == 'close'


@pytest.mark.parametrize('relation_type', [0, 2, 4, 5, '0'])
@pytest.mark.parametrize('mode', ['stored', 'computed', 'identity', 'array',
                                  'primary', 'default', 'blob'])
def test_column_actions_follow_native_structural_context(relation_type, mode):
    detail = {'position': '7'}
    if mode == 'computed':
        detail['computed_source'] = 'COMPUTED BY (X + 1)'
    if mode == 'identity':
        detail['identity_type'] = '0'
    if mode == 'default':
        detail['default_source'] = 'DEFAULT 42'
    if mode == 'blob':
        detail['field_type'] = '261'
    context = columns.alteration_context(
        detail, {'relation_type': relation_type},
        primary_key=mode == 'primary', array=mode == 'array')
    actions = context['allowed_actions']
    assert context['position'] == 8
    assert ('COMPUTED' in actions) == (mode == 'computed')
    assert ('TYPE COMPUTED' in actions) == (mode == 'computed')
    assert ('TYPE' in actions) == (mode not in {'computed', 'array', 'blob'})
    assert ('IDENTITY' in actions) == (mode == 'identity')
    assert ('DROP IDENTITY' in actions) == (mode == 'identity')
    assert ('DROP NOT NULL' in actions) == (
        mode not in {'identity', 'primary'})
    assert ('SET DEFAULT' in actions) == (
        mode not in {'identity', 'computed', 'array'})
    assert ('DROP DEFAULT' in actions) == (mode == 'default')


@pytest.mark.parametrize('relation', [
    {}, {'relation_type': 1}, {'relation_type': 3}, {'relation_type': 99},
    {'relation_type': 0, 'system_object': True},
])
def test_readonly_or_unknown_relation_has_no_column_alterations(relation):
    assert columns.alteration_context({}, relation)['allowed_actions'] == []


@pytest.mark.parametrize('wrapped', [True, False])
def test_planner_rejects_structurally_inapplicable_column_actions(wrapped):
    value = request({'action': 'DROP NOT NULL'})
    native = {'alteration': {'allowed_actions': ['POSITION', 'IDENTITY']}}
    value['target_resource'].update(
        {'extensions': {'firebird': {'native': native}}} if wrapped else
        {'native': native})
    assert ADMINISTRATION.validate(value)['errors']
    with pytest.raises(RelationalClientError, match='not applicable'):
        ADMINISTRATION.plan(value)


def request(draft, operation='alter'):
    return {'resource_kind': 'column', 'operation_id': operation,
            'draft': draft, '_provider_route': {'database': 'example.fdb'},
            'target_resource': {'display_name': 'V',
                                'display_path': ['T', 'V']}}


def create_request(**values):
    return {'resource_kind': 'column', 'operation_id': 'create',
            '_provider_route': {'database': 'example.fdb'},
            'draft': {'table': 'T', 'name': 'V', 'column_mode': 'STORED',
                      'data_type': 'INTEGER', **values}}


def test_catalog_delimiters_cannot_alias_distinct_physical_paths():
    paths = [('A:B', 'C'), ('A', 'B:C'), ('A%3AB', 'C'),
             ('A', 'B%3AC'), ('A:B:C', 'D'), ('A:B', 'C:D')]
    identities = [_catalog_resource_id('column', [table], column)
                  for table, column in paths]
    assert len(set(identities)) == len(paths)
    assert _catalog_resource_id('column', ['T'], 'V') == 'column:T:V'


@pytest.mark.parametrize('values,expected', [
    ({}, '"V" INTEGER'),
    ({'table': 'current', 'name': 'current'}, '"current" INTEGER'),
    ({'column_mode': 'IDENTITY', 'generation': 'ALWAYS',
      'start_value': '-9223372036854775808', 'increment': '-3'},
     '"V" INTEGER GENERATED ALWAYS AS IDENTITY '
     '(START WITH -9223372036854775808 INCREMENT BY -3)'),
    ({'column_mode': 'COMPUTED', 'expression': 'X + 1 -- comment'},
     '"V" INTEGER COMPUTED BY (X + 1 -- comment\n)'),
    ({'column_mode': 'COMPUTED INFERRED', 'expression': 'X + 1'},
     '"V" COMPUTED BY (X + 1\n)'),
    ({'data_type': 'VARCHAR', 'length': 10, 'character_set': 'UTF8',
      'dimensions': [{'lower': -2, 'upper': 3}, {'lower': 1, 'upper': 2}]},
     '"V" VARCHAR(10)[-2:3, 1:2] CHARACTER SET "UTF8"'),
    ({'data_type': 'DOMAIN', 'domain': ' leading domain'},
     '"V" " leading domain"'),
    ({'has_default': True, 'default_kind': 'NUMBER', 'default_value': '+42',
      'constraints': [{'kind': 'NOT NULL', 'name': 'NN'}]},
     '"V" INTEGER DEFAULT 42 CONSTRAINT "NN" NOT NULL'),
    ({'constraints': [{'kind': 'REFERENCES', 'name': 'FK',
                       'reference_table': 'P', 'reference_column': 'ID',
                       'on_update': 'CASCADE', 'on_delete': 'SET NULL',
                       'index_name': 'IX', 'index_direction': 'DESCENDING'}]},
     '"V" INTEGER CONSTRAINT "FK" REFERENCES "P" ("ID") '
     'ON UPDATE CASCADE ON DELETE SET NULL USING DESCENDING INDEX "IX"'),
    ({'constraints': [{'kind': 'CHECK', 'expression': 'V > 0 -- valid'}]},
     '"V" INTEGER CHECK (V > 0 -- valid\n)'),
])
def test_structured_create_column(values, expected):
    value = create_request(**values)
    assert ADMINISTRATION.validate(value) == {'errors': []}
    plan = ADMINISTRATION.plan(value)
    table = '"' + value['draft']['table'].replace('"', '""') + '"'
    assert plan['command_preview']['statements'][0]['source'] == (
        'ALTER TABLE ' + table + ' ADD ' + expected)


@pytest.mark.parametrize('values', [
    {'column_mode': 'UNKNOWN'}, {'has_default': 'yes'},
    {'dimensions': [{'lower': 2, 'upper': 1}]},
    {'dimensions': [{'lower': 1, 'upper': 2}] * 17},
    {'data_type': 'BLOB', 'dimensions': [{'lower': 1, 'upper': 2}]},
    {'column_mode': 'IDENTITY', 'increment': 0},
    {'column_mode': 'IDENTITY', 'has_default': True},
    {'column_mode': 'COMPUTED', 'data_type': 'DOMAIN', 'domain': 'D'},
    {'column_mode': 'COMPUTED', 'expression': '1', 'collation': 'C'},
    {'column_mode': 'COMPUTED', 'expression': '1',
     'constraints': [{'kind': 'NOT NULL'}]},
    {'column_mode': 'COMPUTED', 'expression': '1); DROP TABLE T; --'},
    {'constraints': [{'kind': 'FOREIGN KEY'}]},
    {'constraints': [{'kind': 'CHECK', 'expression': '1=1',
                      'index_name': 'I'}]},
    {'constraints': [{'kind': 'REFERENCES', 'reference_table': 'T',
                      'on_delete': 'RESTRICT'}]},
])
def test_invalid_create_column_rejected(values):
    value = create_request(**values)
    assert ADMINISTRATION.validate(value)['errors']
    with pytest.raises(RelationalClientError):
        ADMINISTRATION.plan(value)


def test_creation_form_has_native_structured_dimensions_and_constraints():
    form = ADMINISTRATION._form('column', 'create')
    assert form['form_id'] == 'firebird.column.create'
    fields = {item['field_id']: item for item in form['fields']}
    assert fields['dimensions']['array_editor']['item_kind'] == 'object'
    assert fields['constraints']['array_editor']['item_kind'] == 'object'
    for mode in columns.MODES:
        draft = {'column_mode': mode, 'data_type': 'INTEGER'}
        active = {name for name, item in fields.items() if
                  ProviderVisualAdministration._field_active(item, draft)}
        computed = mode in ('COMPUTED', 'COMPUTED INFERRED')
        assert ('expression' in active) == computed
        assert ('constraints' in active) != computed
        assert ('generation' in active) == (mode == 'IDENTITY')
        assert ('dimensions' in active) == (mode == 'STORED')


@pytest.mark.parametrize('operation,key', [
    ('create', 'columns'), ('alter', 'add_columns'),
])
def test_table_column_lists_use_native_creation_records(operation, key):
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    table = next(item for item in catalog['objects'] if
                 item['resource_kind'] == 'table')
    form = next(item['form'] for item in table['operations'] if
                item['operation_id'] == operation)
    field = next(item for item in form['fields'] if item['field_id'] == key)
    value = {'name': 'V', 'column_mode': 'IDENTITY', 'data_type': 'BIGINT',
             'generation': 'ALWAYS', 'start_value': '25', 'increment': '5'}
    normalized, error = ProviderVisualAdministration._validate_field(
        field, [value])
    assert error is None
    request = {'resource_kind': 'table', 'operation_id': operation,
               '_provider_route': {'database': 'example.fdb'},
               'target_resource': {'display_name': 'T', 'display_path': ['T']},
               'draft': {key: normalized}}
    if operation == 'create':
        request['draft']['name'] = 'T'
    plan = ADMINISTRATION.plan(request)
    assert ('"V" BIGINT GENERATED ALWAYS AS IDENTITY '
            '(START WITH 25 INCREMENT BY 5)') in (
                plan['command_preview']['statements'][0]['source'])
    names = {item['field_id'] for item in field['array_editor']['fields']}
    assert {'dimensions', 'constraints', 'expression', 'collation'} <= names
    assert 'table' not in names
    assert 'type' not in names


def test_table_drop_column_uses_firebird_grammar():
    plan = ADMINISTRATION.plan({
        'resource_kind': 'table', 'operation_id': 'alter',
        '_provider_route': {'database': 'example.fdb'},
        'target_resource': {'display_name': 'current',
                            'display_path': ['current']},
        'draft': {'drop_columns': ['V']}})
    assert plan['command_preview']['statements'][0]['source'] == (
        'ALTER TABLE "current" DROP "V"')


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
    cases |= {'create-type-' + kind for kind in columns.TYPES} | {
        'create-' + name for name in (
            'identity-always', 'identity-default', 'computed-explicit',
            'computed-inferred', 'array-integer', 'array-character',
            'default-not-null', 'check', 'unique', 'primary-key', 'collation')
    } | {'create-reference-' + action + '-' + str(explicit)
         for action in columns.REFERENTIAL_ACTIONS
         for explicit in (False, True)}
    cases |= {'table-structured-definition-recreation',
              'table-structured-add', 'table-structured-rename',
              'table-structured-drop', 'table-add-failure-atomicity'}
    return {'passed': True, 'engine_version': '5.0.4',
            'fixture_removed': True, 'temporary_user_removed': True,
            'failures': [], 'checks': [{'case': case} for case in cases],
            'table_task_evidence': {
                'visual_admin.table.create': {
                    'live_execution': 'passed',
                    'statements': ['CREATE TABLE "T" ("V" INTEGER)']},
                'visual_admin.table.alter': {
                    'live_execution': 'passed',
                    'statements': ['ALTER TABLE "T" ADD "V2" INTEGER']}},
            'task_evidence': {f'visual_admin.column.{operation}': {
                'live_execution': 'passed', 'statements': [statement]}
                for operation, statement in (
                    ('create', 'ALTER TABLE "T" ADD "V" INTEGER'),
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
    {'table_task_evidence': {}}, {'table_task_evidence': None},
])
def test_column_supplement_requires_complete_evidence(changed):
    with pytest.raises(ValueError):
        supplement_columns(contract(), {**proof(), **changed},
                           'a' * 64, 'test.json')
