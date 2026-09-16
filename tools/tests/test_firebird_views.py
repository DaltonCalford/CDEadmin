"""View replacement preserves native identity and explicit column order."""
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import views
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine


TARGET = {'resource_kind': 'view', 'display_name': 'V"東京'}


@pytest.mark.parametrize('operation', sorted(views.OPERATIONS))
@pytest.mark.parametrize('columns', [[], [{'name': 'B'}, {'name': 'A"東京'}]])
def test_native_statement_and_column_order(operation, columns):
    draft = {'definition': 'SELECT 1, 2 FROM RDB$DATABASE', 'columns': columns}
    draft['name' if operation == 'create_or_alter' else 'confirmation'] = (
        TARGET['display_name'])
    request = {'resource_kind': 'view', 'operation_id': operation,
               'target_resource': TARGET, 'draft': draft,
               '_provider_route': {'database': 'owned'}}
    assert ADMINISTRATION.validate(request) == {'errors': []}
    plan = ADMINISTRATION.plan(request)
    source = plan['command_preview']['statements'][0]['source']
    prefix = ('CREATE OR ALTER' if operation == 'create_or_alter'
              else 'RECREATE')
    assert source == (prefix + ' VIEW "V""東京"' +
                      (' ("B", "A""東京")' if columns else '') +
                      ' AS\nSELECT 1, 2 FROM RDB$DATABASE')


@pytest.mark.parametrize('change', [
    {'columns': 'A'}, {'columns': [{'name': 'A'}, {'name': 'A'}]},
    {'columns': [None]}, {'columns': [{'name': ''}]},
    {'columns': [{'name': 'A', 'type': 'INT'}]}, {'definition': ''},
    {'definition': None}, {'definition': 1}, {'name': 'other'},
    {'confirmation': 'wrong'}, {'cascade': True},
])
def test_recreate_refuses_invalid_or_redirected_drafts(change):
    draft = {'definition': 'SELECT 1 AS A FROM RDB$DATABASE',
             'confirmation': TARGET['display_name'], **change}
    with pytest.raises(RelationalClientError):
        views.compile_operation('recreate', draft, TARGET)
    assert ADMINISTRATION.validate({
        'resource_kind': 'view', 'operation_id': 'recreate',
        'target_resource': TARGET, 'draft': draft})['errors']


@pytest.mark.parametrize('target', [None, {}, {'resource_kind': 'table'}])
def test_recreate_requires_inspected_view(target):
    with pytest.raises(RelationalClientError):
        views.compile_operation('recreate', {
            'definition': 'SELECT 1 FROM RDB$DATABASE'}, target)


def test_native_query_text_is_not_rewritten():
    query = '/* view */ SELECT 1 AS A FROM RDB$DATABASE -- literal ; comment'
    assert views.compile_operation('create_or_alter', {
        'name': 'V', 'definition': query}).endswith(query)


def test_catalog_exposes_distinct_actions_and_prefill():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    resource = next(r for r in catalog['objects'] if
                    r['resource_kind'] == 'view')
    operations = {o['operation_id']: o for o in resource['operations']}
    assert {'create', 'alter', 'drop', 'grant', 'revoke',
            'create_or_alter', 'recreate'} <= operations.keys()
    for operation in views.OPERATIONS:
        record = operations[operation]
        assert record['target_required'] is (operation == 'recreate')
        assert record['confirmation_required'] is True
        fields = {f['field_id']: f for f in record['form']['fields']}
        assert fields['columns']['submit_unchanged'] is True
        assert fields['columns']['array_editor']['fields'][0]['field_id'] == (
            'name')
        assert fields['definition']['initial_value_path'] == ['definition']
        if operation == 'recreate':
            assert record['mutation_class'] == 'destructive'
            assert 'name' not in fields
            assert 'grants' in fields['confirmation']['help']
