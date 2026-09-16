"""Standalone trigger replacement preserves native declaration text."""
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import triggers
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine

TARGET = {'resource_kind': 'trigger', 'display_name': 'F"東京'}
DECLARATION = ('ACTIVE BEFORE INSERT ON T POSITION 2 SQL SECURITY INVOKER '
               "AS BEGIN /* literal ; */ NEW.X = 1; END")


@pytest.mark.parametrize('operation', sorted(triggers.OPERATIONS))
def test_native_statement_and_form(operation):
    draft = {'declaration': DECLARATION,
             'name' if operation == 'create_or_alter' else 'confirmation':
             TARGET['display_name']}
    request = {'resource_kind': 'trigger', 'operation_id': operation,
               'target_resource': TARGET, 'draft': draft,
               '_provider_route': {'database': 'owned'}}
    assert ADMINISTRATION.validate(request) == {'errors': []}
    plan = ADMINISTRATION.plan(request)
    command = ('CREATE OR ALTER' if operation == 'create_or_alter'
               else 'RECREATE')
    assert plan['command_preview']['statements'][0]['source'] == (
        command + ' TRIGGER "F""東京"\n' + DECLARATION)
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    resource = next(r for r in catalog['objects'] if
                    r['resource_kind'] == 'trigger')
    action = next(o for o in resource['operations'] if
                  o['operation_id'] == operation)
    assert action['target_required'] is (operation == 'recreate')
    assert action['confirmation_required'] is True
    fields = {f['field_id']: f for f in action['form']['fields']}
    assert fields['declaration']['control'] == 'code'
    assert triggers.NOTICE in fields['declaration']['help']
    if operation == 'recreate':
        assert action['mutation_class'] == 'destructive'
        assert 'name' not in fields
        assert 'privileges' in fields['confirmation']['help']


@pytest.mark.parametrize('change', [
    {'declaration': None}, {'declaration': 1}, {'declaration': ''},
    {'declaration': 'bad\x00text'}, {'declaration': '\ud800'},
    {'confirmation': 'wrong'}, {'name': 'redirect'}, {'cascade': True},
])
def test_invalid_recreate_draft(change):
    draft = {'declaration': DECLARATION,
             'confirmation': TARGET['display_name'], **change}
    with pytest.raises(RelationalClientError):
        triggers.compile_operation('recreate', draft, TARGET)


@pytest.mark.parametrize('operation', sorted(triggers.OPERATIONS))
def test_relation_nested_trigger_is_valid(operation):
    target = {**TARGET, 'display_path': ['TABLE', TARGET['display_name']]}
    draft = {'declaration': DECLARATION,
             'name' if operation == 'create_or_alter' else 'confirmation':
             TARGET['display_name']}
    assert triggers.compile_operation(operation, draft, target)


@pytest.mark.parametrize('target', [None, {}, {'resource_kind': 'procedure'}])
def test_recreate_requires_inspected_trigger(target):
    with pytest.raises(RelationalClientError):
        triggers.compile_operation('recreate', {
            'declaration': DECLARATION}, target)


@pytest.mark.parametrize('event', [
    'FOR T ACTIVE BEFORE INSERT OR UPDATE OR DELETE',
    'INACTIVE BEFORE INSERT ON T',
    'ACTIVE ON CONNECT', 'ACTIVE ON DISCONNECT',
    'ACTIVE ON TRANSACTION START', 'ACTIVE ON TRANSACTION COMMIT',
    'ACTIVE ON TRANSACTION ROLLBACK',
    'ACTIVE BEFORE CREATE TABLE OR ALTER TABLE',
    'ACTIVE AFTER ANY DDL STATEMENT',
])
def test_native_event_clauses_are_not_rewritten(event):
    declaration = event + ' POSITION 3 AS BEGIN END'
    assert triggers.compile_operation('create_or_alter', {
        'name': 'T', 'declaration': declaration}) == (
            'CREATE OR ALTER TRIGGER "T"\n' + declaration)


@pytest.mark.parametrize('name', [None, '', 'x' * 64, 'a\x00b', '\ud800'])
def test_invalid_create_name(name):
    with pytest.raises(RelationalClientError):
        triggers.compile_operation('create_or_alter', {
            'name': name, 'declaration': DECLARATION})


@pytest.mark.parametrize('operation', [None, [], 'drop'])
def test_invalid_operation(operation):
    with pytest.raises(RelationalClientError):
        triggers.compile_operation(operation, {})
    with pytest.raises(RelationalClientError):
        triggers.form(operation, None)
