"""Package lifecycle compilation preserves native header/body boundaries."""

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import packages
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.navigator import resource_operation_allowed
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine
from pgadmin.cdeadmin.context_menu import (
    resource_context_actions, resource_group_context_actions,
)


TARGET = {'resource_kind': 'package', 'display_name': 'PK"東京'}
QUOTED = '"PK""東京"'
HEADER = 'BEGIN FUNCTION F RETURNS INTEGER; END'
BODY = 'BEGIN FUNCTION F RETURNS INTEGER AS BEGIN RETURN 1; END END'


@pytest.mark.parametrize('security', ['INHERIT', 'INVOKER', 'DEFINER'])
@pytest.mark.parametrize('with_body', [False, True])
def test_creation_allows_header_only_and_optional_separate_body(
        security, with_body):
    draft = {'name': TARGET['display_name'], 'header': HEADER,
             'sql_security': security}
    if with_body:
        draft['body'] = BODY
    result = packages.compile_operation('create', draft)
    clause = '' if security == 'INHERIT' else ' SQL SECURITY ' + security
    expected = ['CREATE PACKAGE ' + QUOTED + clause + ' AS ' + HEADER] + (
        ['CREATE PACKAGE BODY ' + QUOTED + ' AS ' + BODY] if with_body else [])
    assert result == expected


@pytest.mark.parametrize('security', ['INHERIT', 'INVOKER', 'DEFINER'])
def test_alter_header_never_silently_replaces_body(security):
    result = packages.compile_operation('alter', {
        'header': HEADER, 'sql_security': security}, TARGET)
    clause = '' if security == 'INHERIT' else ' SQL SECURITY ' + security
    assert result == ['ALTER PACKAGE ' + QUOTED + clause + ' AS ' + HEADER]


@pytest.mark.parametrize('operation,prefix', [
    ('create_body', 'CREATE'), ('replace_body', 'RECREATE')])
def test_body_only_tasks_do_not_change_header_or_sql_security(
        operation, prefix):
    assert packages.compile_operation(operation, {'body': BODY}, TARGET) == [
        prefix + ' PACKAGE BODY ' + QUOTED + ' AS ' + BODY]


@pytest.mark.parametrize('operation,body', [
    ('drop', ''), ('drop_body', 'BODY ')])
def test_drop_confirms_exact_target_and_preserves_native_scope(
        operation, body):
    assert packages.compile_operation(operation, {
        'confirmation': TARGET['display_name']}, TARGET) == [
        'DROP PACKAGE ' + body + QUOTED]
    for name in ('PK', '', None, QUOTED):
        with pytest.raises(RelationalClientError, match='Confirm'):
            packages.compile_operation(operation, {'confirmation': name},
                                       TARGET)


@pytest.mark.parametrize('comment,sql', [
    ('', 'NULL'), ("Owner's notes 東京", "'Owner''s notes 東京'")])
def test_comment_and_creation_comment_preserve_unicode(comment, sql):
    expected = 'COMMENT ON PACKAGE ' + QUOTED + ' IS ' + sql
    assert packages.compile_operation('comment', {
        'description': comment}, TARGET) == [expected]
    assert packages.compile_operation('create', {
        'name': TARGET['display_name'], 'header': 'BEGIN END',
        'description': comment})[-1] == expected


@pytest.mark.parametrize('operation', sorted(
    packages.OPERATIONS - {'inspect'}))
def test_unknown_fields_cannot_change_task_scope(operation):
    with pytest.raises(RelationalClientError, match='Unknown.*fields'):
        packages.compile_operation(operation, {'statement': 'DROP DATABASE'},
                                   TARGET)


@pytest.mark.parametrize('operation', sorted(packages.OPERATIONS - {
    'inspect', 'create', 'create_or_alter'}))
@pytest.mark.parametrize('target', [None, {}, {
    'resource_kind': 'function', 'display_name': 'F',
    'native': {'package': 'PK'}}])
def test_members_and_missing_targets_cannot_be_used_as_package(
        operation, target):
    with pytest.raises(RelationalClientError, match='inspected package'):
        packages.compile_operation(operation, {}, target)


@pytest.mark.parametrize('bad', [
    None, '', ' ', 1, False, {}, [], '\x00', '\ud800'])
def test_missing_or_invalid_header_and_body_are_rejected(bad):
    with pytest.raises(RelationalClientError):
        packages.compile_operation('create', {'name': 'PK', 'header': bad})
    with pytest.raises(RelationalClientError):
        packages.compile_operation('create_body', {'body': bad}, TARGET)


@pytest.mark.parametrize('operation', sorted(
    packages.OPERATIONS - {'inspect'}))
def test_forms_have_exact_task_identity_and_no_compound_mutation(operation):
    form = packages.form(operation, ADMINISTRATION._field)
    fields = {item['field_id']: item for item in form['fields']}
    assert form['form_id'] == 'firebird.package.' + operation
    assert len(fields) == len(form['fields'])
    if operation == 'alter':
        assert set(fields) == {'header', 'sql_security'}
        assert 'marked invalid' in fields['header']['help']
    if operation == 'create':
        assert fields['body']['required'] is False
    if operation in {'create_body', 'replace_body'}:
        assert set(fields) == {'body'}
    if operation in {'create', 'alter'}:
        assert fields['sql_security']['initial_value_path'] == [
            'package_sql_security']
    assert 'definition' not in fields


def test_native_source_comments_and_literals_are_not_rewritten():
    value = "/* package */ BEGIN /* definition */ END -- end of header"
    assert packages.compile_operation('create', {
        'name': 'PK', 'header': value}) == ['CREATE PACKAGE "PK" AS ' + value]


@pytest.mark.parametrize('kind', [
    'function', 'procedure', 'external-function'])
@pytest.mark.parametrize('operation', ['alter', 'drop'])
@pytest.mark.parametrize('identity', [
    {'native': {'package': 'PK'}},
    {'extensions': {'firebird': {'native': {'package': 'PK'}}}},
    {'display_path': ['PK', 'F']},
])
def test_packaged_member_cannot_bypass_native_mutation_guard(
        kind, operation, identity):
    target = {'resource_kind': kind, 'display_name': 'F', **identity}
    with pytest.raises(RelationalClientError, match='owning package'):
        packages.validate_member_operation(kind, operation, target)
    request = {'resource_kind': kind, 'operation_id': operation,
               'target_resource': target, 'draft': {},
               '_provider_route': {'host': '127.0.0.1'}}
    assert ADMINISTRATION.validate(request)['errors'][0]['code'] == (
        'package_member_requires_owner')
    with pytest.raises(RelationalClientError, match='owning package'):
        ADMINISTRATION.plan(request)


@pytest.mark.parametrize('kind', [
    'function', 'procedure', 'external-function'])
@pytest.mark.parametrize('operation', ['alter', 'drop'])
def test_standalone_routine_with_dot_in_its_name_is_not_a_package(
        kind, operation):
    packages.validate_member_operation(kind, operation, {
        'resource_kind': kind, 'display_name': 'F.with.dot',
        'display_path': ['F.with.dot']})


@pytest.mark.parametrize('operation', [
    'inspect', 'comment', 'grant', 'revoke'])
def test_package_member_inspection_comment_and_security_are_not_fabricated_ddl(
        operation):
    packages.validate_member_operation('function', operation, {
        'native': {'package': 'PK'}, 'display_path': ['PK', 'F']})


@pytest.mark.parametrize('allowed', [[], ['inspect'], ['inspect', 'grant'],
                                     None, 'inspect', {}])
def test_provider_resource_admission_honors_only_explicit_operation_arrays(
        allowed):
    target = {'native': {'administration': {'allowed_operations': allowed}}}
    for operation in ('inspect', 'grant', 'drop'):
        expected = allowed is None or (
            isinstance(allowed, list) and operation in allowed)
        assert resource_operation_allowed(target, operation) is expected


@pytest.mark.parametrize('bad', ['unknown', None, [], {}, False])
def test_invalid_package_form_operation_fails_closed(bad):
    with pytest.raises(RelationalClientError):
        packages.form(bad, ADMINISTRATION._field)


def test_upsert_and_recreate_are_distinct_native_semantics():
    assert packages.compile_operation('create_or_alter', {
        'name': 'PK', 'header': 'BEGIN END'}) == [
        'CREATE OR ALTER PACKAGE "PK" AS BEGIN END']
    assert packages.compile_operation('recreate', {
        'confirmation': TARGET['display_name'], 'header': HEADER,
        'body': BODY, 'sql_security': 'DEFINER'}, TARGET) == [
        'RECREATE PACKAGE ' + QUOTED + ' SQL SECURITY DEFINER AS ' + HEADER,
        'CREATE PACKAGE BODY ' + QUOTED + ' AS ' + BODY]
    with pytest.raises(RelationalClientError, match='Confirm'):
        packages.compile_operation('recreate', {'header': HEADER}, TARGET)


def menu_catalog():
    value = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    for item in value['objects']:
        for operation in item['operations']:
            operation['execution_available'] = True
    return value


@pytest.mark.parametrize('kind', ['function', 'procedure'])
def test_member_popup_hides_standalone_ddl_and_opens_exact_owner(kind):
    owner = {'resource_id': 'pkg-id', 'resource_kind': 'package',
             'display_name': 'PK'}
    selected = {'resource_id': 'member-id', 'resource_kind': kind,
                'display_name': 'F', 'display_path': ['PK', 'F'],
                'native': {'package': 'PK', 'administration': {
                    'allowed_operations': ['inspect', 'grant', 'revoke'],
                    'definition_owner': owner}}}
    actions = resource_context_actions({'engine_id': 'firebird'}, selected,
                                       menu_catalog(),
                                       database_target_id='owned-db')
    linked = next(item for item in actions if item['command_id'].endswith(
        '.definition_owner'))
    assert linked['arguments'] == {
        'resource_id': 'pkg-id', 'resource_kind': 'package',
        'operation_id': 'inspect', 'tab': 'object',
        'database_target_id': 'owned-db'}
    assert linked['enabled'] is True
    assert not any(item['arguments'].get('operation_id') in {'alter', 'drop'}
                   for item in actions)


@pytest.mark.parametrize('kind', ['function', 'procedure'])
def test_new_member_uses_parent_header_not_standalone_creation(kind):
    parent = {'resource_id': 'pkg-id', 'resource_kind': 'package',
              'display_name': 'PK', 'native': {'child_definition_tasks': {
                  kind: {'operation_id': 'alter',
                         'label': 'New ' + kind + ' in package header'}}}}
    actions = resource_group_context_actions(
        {'engine_id': 'firebird'}, kind, menu_catalog(),
        database_target_id='owned-db', parent_resource=parent)
    assert len(actions) == 1
    assert actions[0]['arguments'] == {
        'resource_id': 'pkg-id', 'resource_kind': 'package',
        'operation_id': 'alter', 'tab': 'object',
        'database_target_id': 'owned-db'}
    assert resource_group_context_actions(
        {'engine_id': 'firebird'}, kind, menu_catalog(),
        parent_resource=parent, system=True) == []
