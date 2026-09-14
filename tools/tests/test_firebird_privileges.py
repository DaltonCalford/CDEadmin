##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird.privileges import (
    compile_privilege, OBJECT_PRIVILEGES, DDL_CLASSES, PRINCIPAL_KINDS,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine


def draft(**values):
    return {'principal': 'User', 'principal_kind': 'USER',
            'object_type': 'TABLE', 'object_name': 'T',
            'privileges': ['SELECT'], **values}


@pytest.mark.parametrize('operation', ['grant', 'revoke'])
@pytest.mark.parametrize('kind,values', list(OBJECT_PRIVILEGES.items()))
def test_exact_object_privilege_clauses(operation, kind, values):
    value = draft(object_type=kind, privileges=list(values),
                  confirmation='User')
    expected_kind = 'TABLE' if kind == 'VIEW' else kind
    direction = 'TO' if operation == 'grant' else 'FROM'
    assert compile_privilege(operation, value) == (
        operation.upper() + ' ' + ', '.join(values) + ' ON ' +
        expected_kind + ' "T" ' + direction + ' USER "User"')


@pytest.mark.parametrize('kind', PRINCIPAL_KINDS)
def test_explicit_grantee_kind_and_public(kind):
    value = draft(principal_kind=kind,
                  principal='' if kind == 'PUBLIC' else 'User')
    expected = 'PUBLIC' if kind == 'PUBLIC' else kind + ' "User"'
    assert compile_privilege('grant', value).endswith(' TO ' + expected)


@pytest.mark.parametrize('kind', DDL_CLASSES)
def test_class_grants_use_no_on_clause(kind):
    assert compile_privilege('grant', {
        'principal': 'U', 'principal_kind': 'ROLE',
        'privilege_scope': 'ddl_class', 'ddl_class': kind,
        'ddl_privileges': ['CREATE', 'ALTER ANY', 'DROP ANY'],
    }) == f'GRANT CREATE, ALTER ANY, DROP ANY {kind} TO ROLE "U"'


def test_column_names_are_quoted_separately_and_do_not_imply_schema():
    assert compile_privilege('grant', draft(
        object_name='T.with.dot', privileges=['UPDATE', 'REFERENCES'],
        update_columns=[{'name': 'A"B'}],
        reference_columns=[{'name': 'V.with.dot'}],
        grant_option=True, grantor='Owner',
    )) == ('GRANT UPDATE ("A""B"), REFERENCES ("V.with.dot") '
           'ON TABLE "T.with.dot" TO USER "User" WITH GRANT OPTION '
           'GRANTED BY USER "Owner"')


def test_revoke_grant_option_keeps_explicit_grantor():
    assert compile_privilege('revoke', draft(
        confirmation='User', grant_option_only=True, grantor='Owner',
    )) == ('REVOKE GRANT OPTION FOR SELECT ON TABLE "T" FROM USER "User" '
           'GRANTED BY USER "Owner"')


def test_database_all_is_not_expanded_to_create_database():
    assert compile_privilege('grant', {
        'privilege_scope': 'database', 'database_privileges': ['ALL'],
        'principal': 'U', 'grant_option': True,
    }) == 'GRANT ALL DATABASE TO USER "U" WITH GRANT OPTION'


def test_all_objects_revoke_has_its_own_grammar():
    assert compile_privilege('revoke', {
        'privilege_scope': 'all_objects', 'principal_kind': 'ROLE',
        'principal': 'R', 'confirmation': 'R',
    }) == 'REVOKE ALL ON ALL FROM ROLE "R"'


@pytest.mark.parametrize('change', [
    {'object_type': 'DOMAIN'}, {'object_type': 'CHARACTER SET'},
    {'object_type': 'COLLATION'}, {'object_type': 'ROLE'},
    {'object_type': {}}, {'privileges': ['TRUNCATE']},
    {'privileges': ['EXECUTE']}, {'privileges': ['SELECT(V)']},
    {'privileges': ['ALL', 'UPDATE']}, {'privileges': ['SELECT', 'SELECT']},
    {'privileges': []}, {'privileges': 'SELECT'}, {'privileges': [{}]},
    {'principal_kind': 'SERVER'}, {'principal': ''},
    {'grant_option': 'true'}, {'grant_option_only': True},
    {'object_name': 'bad\x00name'}, {'grantor': 'bad\x00name'},
    {'update_columns': [{'name': 'V'}]},
    {'reference_columns': [{'name': 'V'}]},
    {'update_columns': 'V'}, {'update_columns': ['V']},
    {'privileges': ['UPDATE'], 'update_columns': [{'name': 'V'}] * 2},
    {'ddl_privileges': ['CREATE']}, {'database_privileges': ['CREATE']},
])
def test_inapplicable_or_malformed_privileges_fail_before_execution(change):
    with pytest.raises(RelationalClientError):
        compile_privilege('grant', draft(**change))


@pytest.mark.parametrize('change', [
    {'principal_kind': 'GROUP'}, {'principal_kind': 'PROCEDURE'},
    {'grantor': 'SYSDBA'}, {'grant_option': True},
])
def test_create_database_native_restrictions(change):
    with pytest.raises(RelationalClientError):
        compile_privilege('grant', {
            'privilege_scope': 'database', 'database_privileges': ['CREATE'],
            'principal': 'U', **change})


@pytest.mark.parametrize('confirmation', [None, '', 'user', 'Other'])
def test_revoke_requires_exact_grantee_confirmation(confirmation):
    with pytest.raises(RelationalClientError):
        compile_privilege('revoke', draft(confirmation=confirmation))


def test_provider_uses_native_forms_and_validates_before_planning():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    descriptor = next(item for item in catalog['objects'] if
                      item['resource_kind'] == 'privilege')
    for operation in ('grant', 'revoke'):
        form = next(item['form'] for item in descriptor['operations'] if
                    item['operation_id'] == operation)
        assert form['form_id'] == 'firebird.privilege.' + operation
        fields = {item['field_id']: item for item in form['fields']}
        assert fields['privileges']['control'] == 'multiselect'
        assert fields['update_columns']['array_editor']['item_kind'] == (
            'object')
        assert len(fields) == len(form['fields'])
    request = {'resource_kind': 'privilege', 'operation_id': 'grant',
               'draft': draft(object_type='VIEW')}
    assert ADMINISTRATION.validate(request) == {'errors': []}
    assert ADMINISTRATION._compile(request)['statements'][0]['source'] == (
        'GRANT SELECT ON TABLE "T" TO USER "User"')
    request['draft']['privileges'] = ['SELECT(V)']
    assert ADMINISTRATION.validate(request)['errors']
    with pytest.raises(RelationalClientError):
        ADMINISTRATION._compile(request)


def test_multiple_grantees_share_one_native_statement():
    values = draft(additional_grantees=[
        {'kind': 'ROLE', 'name': 'Role.With.Dot'}, {'kind': 'PUBLIC'}])
    assert compile_privilege('grant', values) == (
        'GRANT SELECT ON TABLE "T" TO USER "User", ROLE "Role.With.Dot", '
        'PUBLIC')
    assert compile_privilege('revoke', {
        **values, 'confirmation': 'User, Role.With.Dot, PUBLIC'}) == (
        'REVOKE SELECT ON TABLE "T" FROM USER "User", ROLE "Role.With.Dot", '
        'PUBLIC')
    with pytest.raises(RelationalClientError):
        compile_privilege('revoke', {**values, 'confirmation': 'User'})


@pytest.mark.parametrize('additional', [
    None, 'User', ['User'], [{}], [{'name': 'User'}],
    [{'kind': 'USER', 'name': 'User'}], [{'kind': 'PUBLIC', 'name': 'Other'}],
    [{'kind': 'SERVER', 'name': 'User'}],
    [{'kind': 'USER', 'name': 'U', 'extra': 'no'}],
])
def test_malformed_or_duplicate_grantees_rejected(additional):
    with pytest.raises(RelationalClientError):
        compile_privilege('grant', draft(additional_grantees=additional))


def test_create_database_restrictions_cover_every_grantee():
    with pytest.raises(RelationalClientError):
        compile_privilege('grant', {
            'principal': 'U', 'privilege_scope': 'database',
            'database_privileges': ['CREATE'],
            'additional_grantees': [{'kind': 'PUBLIC'}]})
