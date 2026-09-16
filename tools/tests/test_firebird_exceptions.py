"""Exception replacement uses native SQL and explicit destructive admission."""
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import exceptions
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine

TARGET = {'resource_kind': 'exception', 'display_name': 'E"東京'}


@pytest.mark.parametrize('operation', sorted(exceptions.OPERATIONS))
def test_statement_and_form(operation):
    draft = {'message': "O'Brien; -- literal @1",
             'name' if operation == 'create_or_alter' else 'confirmation':
             TARGET['display_name']}
    request = {'resource_kind': 'exception', 'operation_id': operation,
               'target_resource': TARGET, 'draft': draft,
               '_provider_route': {'database': 'owned'}}
    assert ADMINISTRATION.validate(request) == {'errors': []}
    plan = ADMINISTRATION.plan(request)
    command = ('CREATE OR ALTER' if operation == 'create_or_alter'
               else 'RECREATE')
    assert plan['command_preview']['statements'][0]['source'] == (
        command + ' EXCEPTION "E""東京" \'O\'\'Brien; -- literal @1\'')
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    resource = next(r for r in catalog['objects'] if
                    r['resource_kind'] == 'exception')
    action = next(o for o in resource['operations'] if
                  o['operation_id'] == operation)
    assert action['target_required'] is (operation == 'recreate')
    assert action['confirmation_required'] is True
    fields = {f['field_id']: f for f in action['form']['fields']}
    assert fields['message']['initial_value_path'] == ['message']
    if operation == 'recreate':
        assert action['mutation_class'] == 'destructive'
        assert 'name' not in fields
        assert 'grants' in fields['confirmation']['help']


@pytest.mark.parametrize('change', [
    {'message': None}, {'message': 1}, {'message': 'bad\x00text'},
    {'message': '\ud800'}, {'confirmation': 'wrong'}, {'name': 'redirect'},
    {'cascade': True},
])
def test_invalid_recreate_draft(change):
    draft = {'message': 'safe', 'confirmation': TARGET['display_name'],
             **change}
    with pytest.raises(RelationalClientError):
        exceptions.compile_operation('recreate', draft, TARGET)
    assert ADMINISTRATION.validate({
        'resource_kind': 'exception', 'operation_id': 'recreate',
        'target_resource': TARGET, 'draft': draft})['errors']


@pytest.mark.parametrize('target', [None, {}, {'resource_kind': 'table'}])
def test_inspected_exception_required(target):
    with pytest.raises(RelationalClientError):
        exceptions.compile_operation('recreate', {'message': 'safe'}, target)
