"""UDR replacement uses native declarations and existing safety contracts."""
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird.ddl_dialect import generated_dialect
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine


@pytest.mark.parametrize('kind', ['function', 'procedure'])
@pytest.mark.parametrize('operation', ['create_or_alter', 'recreate'])
@pytest.mark.parametrize('dialect', [1, 3])
@pytest.mark.parametrize('body', ['', " AS ''", " AS '  it''s ; 東京  '"])
def test_udr_form_validation_and_plan(kind, operation, dialect, body):
    name = 'UDR_ROUTINE' if dialect == 1 else 'UDR"東京'
    quoted = name if dialect == 1 else '"UDR""東京"'
    declaration = ('(X INTEGER) RETURNS ' +
                   ('INTEGER' if kind == 'function' else '(Y INTEGER)') +
                   " EXTERNAL NAME 'module!entry' ENGINE UDR" + body)
    target = {'resource_kind': kind, 'display_name': name}
    request = {
        'resource_kind': kind, 'operation_id': operation,
        'target_resource': target,
        '_provider_route': {'database': 'owned'},
        'draft': {'declaration': declaration,
                  'name' if operation == 'create_or_alter' else
                  'confirmation': name}}
    with generated_dialect(dialect):
        assert ADMINISTRATION.validate(request) == {'errors': []}
        plan = ADMINISTRATION.plan(request)
    command = ('CREATE OR ALTER' if operation == 'create_or_alter'
               else 'RECREATE')
    assert plan['command_preview']['statements'][0]['source'] == (
        f'{command} {kind.upper()} {quoted}\n{declaration}')
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    resource = next(item for item in catalog['objects'] if
                    item['resource_kind'] == kind)
    action = next(item for item in resource['operations'] if
                  item['operation_id'] == operation)
    assert action['confirmation_required'] is True
    fields = {item['field_id']: item for item in action['form']['fields']}
    assert fields['declaration']['control'] == 'code'
    assert 'EXTERNAL' in fields['declaration']['help']
    if operation == 'recreate':
        assert action['mutation_class'] == 'destructive'
        assert 'grants' in fields['confirmation']['help']


def test_gate_requires_all_external_replacement_cases():
    from tools.cdeadmin_firebird_catalog_dialect_gate import cases
    external = [case for case in cases() if case[1] in {
        'UFN', 'UFS', 'UPN', 'UPS'}]
    assert len(external) == 4
    assert {case[0] for case in external} == {'function', 'procedure'}
