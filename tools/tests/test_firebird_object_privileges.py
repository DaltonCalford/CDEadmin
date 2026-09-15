"""Object-bound rights never inherit a client-provided target or SQL scope."""

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import object_privileges as bound
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from tools.cdeadmin_firebird_object_privileges_gate import (
    ADMINISTRATION as QUALIFICATION,
)
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine
from pgadmin.cdeadmin.context_menu import resource_context_actions


def target(kind, name='Object"東京', native=None):
    return {'resource_kind': kind, 'display_name': name,
            'display_path': ['Relation', name] if kind == 'column' else [name],
            'native': native or {}}


def draft(kind, **values):
    return {'principal': 'Reader', 'principal_kind': 'USER',
            'privileges': list(bound.allowed_privileges(kind)[:1]), **values}


@pytest.mark.parametrize('kind', bound.KINDS)
@pytest.mark.parametrize('operation', ['grant', 'revoke'])
def test_exact_object_binding(kind, operation):
    source = bound.compile_operation(
        kind, operation, draft(kind, **(
            {'confirmation': 'Reader'} if operation == 'revoke' else {})),
        target(kind))
    assert source.startswith(operation.upper() + ' ')
    if kind == 'column':
        assert 'UPDATE ("Object""東京") ON TABLE "Relation"' in source
    else:
        native_kind = 'TABLE' if kind == 'view' else bound.KINDS[kind]
        assert f'ON {native_kind} "Object""東京"' in source
    assert source.endswith('USER "Reader"')


@pytest.mark.parametrize('kind', ['function', 'procedure'])
def test_packaged_member_explicitly_targets_package(kind):
    selected = target(kind, native={'package': 'Package"東京'})
    assert bound.compile_operation(kind, 'grant', draft(kind), selected) == (
        'GRANT EXECUTE ON PACKAGE "Package""東京" TO USER "Reader"')
    # The admitted resource DTO and raw driver catalog retain identical intent.
    selected['extensions'] = {'firebird': {'native': selected.pop('native')}}
    assert 'ON PACKAGE' in bound.compile_operation(
        kind, 'grant', draft(kind), selected)


@pytest.mark.parametrize('kind', bound.KINDS)
@pytest.mark.parametrize('injected', [
    {'object_name': 'Other'}, {'object_type': 'DATABASE'},
    {'privilege_scope': 'all_objects'}, {'ddl_class': 'TABLE'},
    {'database_privileges': ['ALL']}, {'options': {}}, {'source': 'SQL'},
])
def test_draft_cannot_redirect_or_expand_native_scope(kind, injected):
    with pytest.raises(RelationalClientError, match='Unknown'):
        bound.compile_operation(kind, 'grant', draft(kind, **injected),
                                target(kind))


@pytest.mark.parametrize('kind', [
    'blob-filter', 'domain', 'collation', 'character-set', 'role',
    'trigger', 'index', 'constraint', 'database', None, [],
])
def test_non_grantable_classes_have_no_fabricated_object_grants(kind):
    with pytest.raises(RelationalClientError):
        bound.allowed_privileges(kind)


@pytest.mark.parametrize('selected', [
    None, {}, {'resource_kind': 'view', 'display_name': 'T'},
    {'resource_kind': 'table', 'display_name': ''},
])
def test_missing_or_mismatched_inspection_is_rejected(selected):
    with pytest.raises(RelationalClientError):
        bound.compile_operation('table', 'grant', draft('table'), selected)


@pytest.mark.parametrize('path', [
    None, [], ['Only'], ['R', 'Other'], ['db', 'R', 'C'], 'R.C',
])
def test_column_requires_exact_native_relation_path(path):
    selected = dict(target('column', 'C'), display_path=path)
    with pytest.raises(RelationalClientError):
        bound.compile_operation('column', 'grant', draft('column'), selected)


def test_column_rights_remain_column_local():
    selected = target('column', 'C')
    assert bound.compile_operation('column', 'grant', draft(
        'column', privileges=['UPDATE', 'REFERENCES']), selected) == (
        'GRANT UPDATE ("C"), REFERENCES ("C") ON TABLE "Relation" '
        'TO USER "Reader"')
    for values in ({'privileges': ['SELECT']}, {'privileges': ['ALL']},
                   {'update_columns': []}, {'reference_columns': []}):
        with pytest.raises(RelationalClientError):
            bound.compile_operation('column', 'grant', draft(
                'column', **values), selected)


@pytest.mark.parametrize('kind', ['table', 'view'])
def test_relation_column_lists_and_multiple_grantees(kind):
    source = bound.compile_operation(kind, 'grant', draft(
        kind, privileges=['UPDATE', 'REFERENCES'],
        update_columns=[{'name': 'V"x'}],
        reference_columns=[{'name': 'C'}], grant_option=True,
        grantor='Owner', additional_grantees=[{'kind': 'ROLE', 'name': 'R'}]),
        target(kind, 'T'))
    assert source == (
        'GRANT UPDATE ("V""x"), REFERENCES ("C") ON TABLE "T" '
        'TO USER "Reader", ROLE "R" WITH GRANT OPTION GRANTED BY USER "Owner"')


def test_only_grant_option_can_be_revoked_without_revoking_usage():
    assert bound.compile_operation('sequence', 'revoke', draft(
        'sequence', grant_option_only=True, confirmation='Reader'),
        target('sequence', 'S')) == (
        'REVOKE GRANT OPTION FOR USAGE ON SEQUENCE "S" FROM USER "Reader"')


@pytest.mark.parametrize('operation', ['alter', None, [], {}])
def test_invalid_operations_fail_with_provider_diagnostic(operation):
    with pytest.raises(RelationalClientError):
        bound.compile_operation('table', operation, draft('table'),
                                target('table'))
    with pytest.raises(RelationalClientError):
        bound.form('table', operation, ADMINISTRATION._field)


@pytest.mark.parametrize('kind', bound.KINDS)
@pytest.mark.parametrize('operation', ['grant', 'revoke'])
def test_visual_fields_only_describe_selected_objects_rights(kind, operation):
    form = bound.form(kind, operation, ADMINISTRATION._field)
    assert form['form_id'] == f'firebird.{kind}.{operation}'
    fields = {item['field_id']: item for item in form['fields']}
    assert not set(fields) & {'object_name', 'object_type', 'privilege_scope',
                              'ddl_class', 'definition', 'options'}
    assert [item['value'] for item in fields['privileges']['options']] == list(
        bound.allowed_privileges(kind))
    assert ('update_columns' in fields) == (kind in {'table', 'view'})
    assert ('confirmation' in fields) == (operation == 'revoke')
    assert 'visible_when' in fields['principal']
    assert all('visible_when' not in item for key, item in fields.items()
               if key != 'principal')
    if kind in {'function', 'procedure'}:
        assert 'entire package' in fields['privileges']['help']


@pytest.mark.parametrize('kind', bound.KINDS)
@pytest.mark.parametrize('operation', ['grant', 'revoke'])
def test_provider_forms_previews_and_navigator_preserve_selected_target(
        kind, operation):
    selected = dict(target(kind), resource_id='owned-object')
    values = draft(kind, **(
        {'confirmation': 'Reader'} if operation == 'revoke' else {}))
    request = {'resource_kind': kind, 'operation_id': operation,
               'target_resource': selected, 'draft': values,
               '_provider_route': {'host': '127.0.0.1', 'port': 53050,
                                   'database': '/owned.fdb', 'user': 'SYSDBA'}}
    assert QUALIFICATION.validate(request) == {'errors': []}
    plan = QUALIFICATION.plan(request)
    assert plan['command_preview']['statements'][0]['source'] == (
        bound.compile_operation(kind, operation, values, selected))
    catalog = QUALIFICATION.catalog(catalog_for_engine('firebird'))
    descriptor = next(item for item in catalog['objects'] if
                      item['resource_kind'] == kind)
    task = next(item for item in descriptor['operations'] if
                item['operation_id'] == operation)
    assert task['form']['form_id'] == f'firebird.{kind}.{operation}'
    assert task['target_required'] is True
    task['execution_available'] = True
    actions = resource_context_actions({'engine_id': 'firebird'}, selected,
                                       catalog, database_target_id='owned-db')
    command = next(item for item in actions if item['command_id'] ==
                   f'resource.firebird.{kind}.{operation}')
    assert command['enabled'] is True
    assert command['arguments'] == {
        'resource_id': 'owned-object', 'database_target_id': 'owned-db',
        'tab': 'administration', 'operation_id': operation,
        'resource_kind': kind}
    assert QUALIFICATION.validate(dict(request, draft=dict(
        values, object_name='Other')))['errors'][0]['code'] == (
        'invalid_object_privilege')
