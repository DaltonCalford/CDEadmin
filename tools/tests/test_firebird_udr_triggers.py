"""Provider trigger forms retain native external declarations and safety."""
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from tools.cdeadmin_firebird_udr_triggers_gate import declaration
from pgadmin.cdeadmin.providers.firebird.ddl_dialect import generated_dialect
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine


@pytest.mark.parametrize('operation', ['create_or_alter', 'recreate'])
@pytest.mark.parametrize('active', [True, False])
@pytest.mark.parametrize('dialect', [1, 3])
def test_external_trigger_declaration_is_one_native_task(
        operation, active, dialect):
    draft = {'declaration': declaration(active),
             'confirmation' if operation == 'recreate' else 'name': 'UDR_COPY'}
    request = {'resource_kind': 'trigger', 'operation_id': operation,
               '_provider_route': {'database': 'owned'}, 'draft': draft,
               'target_resource': {'resource_kind': 'trigger',
                                   'display_name': 'UDR_COPY'}}
    with generated_dialect(dialect):
        assert ADMINISTRATION.validate(request) == {'errors': []}
        plan = ADMINISTRATION.plan(request)
    command = 'RECREATE' if operation == 'recreate' else 'CREATE OR ALTER'
    name = 'UDR_COPY' if dialect == 1 else '"UDR_COPY"'
    statements = plan['command_preview']['statements']
    assert len(statements) == 1
    assert statements[0]['source'] == (
        f'{command} TRIGGER {name}\n' + declaration(active))
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    resource = next(item for item in catalog['objects'] if
                    item['resource_kind'] == 'trigger')
    action = next(item for item in resource['operations'] if
                  item['operation_id'] == operation)
    assert action['confirmation_required'] is True
    fields = {item['field_id']: item for item in action['form']['fields']}
    assert fields['declaration']['control'] == 'code'
    assert 'EXTERNAL' in fields['declaration']['help']
    if operation == 'recreate':
        assert action['mutation_class'] == 'destructive'
        assert fields['confirmation']['required'] is True
