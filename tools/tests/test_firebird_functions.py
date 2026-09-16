"""Standalone function replacement preserves native declaration text."""
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import functions
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine

TARGET = {'resource_kind': 'function', 'display_name': 'F"東京'}
DECLARATION = ('(X INTEGER = 2) RETURNS INTEGER DETERMINISTIC '
               'SQL SECURITY INVOKER '
               "AS BEGIN /* literal ; */ RETURN X; END")


@pytest.mark.parametrize('operation', sorted(functions.OPERATIONS))
def test_native_statement_and_form(operation):
    draft = {'declaration': DECLARATION,
             'name' if operation == 'create_or_alter' else 'confirmation':
             TARGET['display_name']}
    request = {'resource_kind': 'function', 'operation_id': operation,
               'target_resource': TARGET, 'draft': draft,
               '_provider_route': {'database': 'owned'}}
    assert ADMINISTRATION.validate(request) == {'errors': []}
    plan = ADMINISTRATION.plan(request)
    command = ('CREATE OR ALTER' if operation == 'create_or_alter'
               else 'RECREATE')
    assert plan['command_preview']['statements'][0]['source'] == (
        command + ' FUNCTION "F""東京"\n' + DECLARATION)
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    resource = next(r for r in catalog['objects'] if
                    r['resource_kind'] == 'function')
    action = next(o for o in resource['operations'] if
                  o['operation_id'] == operation)
    assert action['target_required'] is (operation == 'recreate')
    assert action['confirmation_required'] is True
    fields = {f['field_id']: f for f in action['form']['fields']}
    assert fields['declaration']['control'] == 'code'
    assert functions.NOTICE in fields['declaration']['help']
    if operation == 'recreate':
        assert action['mutation_class'] == 'destructive'
        assert 'name' not in fields
        assert 'grants' in fields['confirmation']['help']


@pytest.mark.parametrize('change', [
    {'declaration': None}, {'declaration': 1}, {'declaration': ''},
    {'declaration': 'bad\x00text'}, {'declaration': '\ud800'},
    {'confirmation': 'wrong'}, {'name': 'redirect'}, {'cascade': True},
])
def test_invalid_recreate_draft(change):
    draft = {'declaration': DECLARATION,
             'confirmation': TARGET['display_name'], **change}
    with pytest.raises(RelationalClientError):
        functions.compile_operation('recreate', draft, TARGET)


@pytest.mark.parametrize('operation', sorted(functions.OPERATIONS))
@pytest.mark.parametrize('metadata', [
    {'native': {'package': 'PKG'}},
    {'extensions': {'native': {'native': {'package': 'PKG'}}}},
    {'display_path': ['PKG', 'P']},
])
def test_package_members_cannot_be_standalone_targets(operation, metadata):
    target = {**TARGET, **metadata}
    draft = {'declaration': DECLARATION, 'name': TARGET['display_name']} if (
        operation == 'create_or_alter') else {
            'declaration': DECLARATION, 'confirmation': TARGET['display_name']}
    with pytest.raises(RelationalClientError, match='owning package'):
        functions.compile_operation(operation, draft, target)


@pytest.mark.parametrize('target', [None, {}, {'resource_kind': 'procedure'}])
def test_recreate_requires_inspected_function(target):
    with pytest.raises(RelationalClientError):
        functions.compile_operation('recreate', {
            'declaration': DECLARATION}, target)
