"""Packaged write routines remain native PSQL, not split client scripts."""
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from tools.cdeadmin_firebird_package_writes_gate import BODY, HEADER, MODES
from pgadmin.cdeadmin.providers.firebird.ddl_dialect import generated_dialect


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('dialect', [1, 3])
def test_packaged_writes_keep_native_body_and_header(mode, dialect):
    name = 'WRITE_' + mode
    request = {'resource_kind': 'package', 'operation_id': 'create',
               '_provider_route': {'database': 'owned'},
               'draft': {'name': name, 'header': HEADER, 'body': BODY,
                         'sql_security': mode}}
    with generated_dialect(dialect):
        assert ADMINISTRATION.validate(request) == {'errors': []}
        statements = ADMINISTRATION.plan(request)['command_preview'][
            'statements']
    quoted = name if dialect == 1 else '"' + name + '"'
    security = '' if mode == 'INHERIT' else ' SQL SECURITY ' + mode
    assert [item['source'] for item in statements] == [
        f'CREATE PACKAGE {quoted}{security} AS {HEADER}',
        f'CREATE PACKAGE BODY {quoted} AS {BODY}']


@pytest.mark.parametrize('operation', ['grant', 'revoke'])
def test_underlying_dml_rights_do_not_grant_package_execution(operation):
    draft = {'object_type': 'TABLE', 'object_name': 'WRITE_DATA',
             'privileges': ['INSERT', 'UPDATE', 'DELETE'],
             'principal_kind': 'USER', 'principal': 'WRITE_READER'}
    if operation == 'revoke':
        draft['confirmation'] = 'WRITE_READER'
    request = {'resource_kind': 'privilege', 'operation_id': operation,
               '_provider_route': {'database': 'owned'}, 'draft': draft}
    assert ADMINISTRATION.validate(request) == {'errors': []}
    statements = ADMINISTRATION.plan(request)['command_preview']['statements']
    direction = 'TO' if operation == 'grant' else 'FROM'
    assert [item['source'] for item in statements] == [
        f'{operation.upper()} INSERT, UPDATE, DELETE ON TABLE "WRITE_DATA" '
        f'{direction} USER "WRITE_READER"']


@pytest.mark.parametrize('operation', ['create_body', 'replace_body'])
@pytest.mark.parametrize('dialect', [1, 3])
def test_failure_body_keeps_native_exception_and_statement_boundaries(
        operation, dialect):
    request = {'resource_kind': 'package', 'operation_id': operation,
               '_provider_route': {'database': 'owned'},
               'target_resource': {'resource_kind': 'package',
                                   'display_name': 'WRITE_DEFINER'},
               'draft': {'body': BODY}}
    with generated_dialect(dialect):
        assert ADMINISTRATION.validate(request) == {'errors': []}
        statements = ADMINISTRATION.plan(request)['command_preview'][
            'statements']
    name = 'WRITE_DEFINER' if dialect == 1 else '"WRITE_DEFINER"'
    prefix = 'CREATE' if operation == 'create_body' else 'RECREATE'
    assert [item['source'] for item in statements] == [
        f'{prefix} PACKAGE BODY {name} AS {BODY}']


@pytest.mark.parametrize('operation', ['grant', 'revoke'])
def test_exception_usage_is_separate_from_table_write_rights(operation):
    draft = {'object_type': 'EXCEPTION', 'object_name': 'WRITE_FAILURE',
             'privileges': ['USAGE'], 'principal_kind': 'USER',
             'principal': 'WRITE_READER'}
    if operation == 'revoke':
        draft['confirmation'] = 'WRITE_READER'
    request = {'resource_kind': 'privilege', 'operation_id': operation,
               '_provider_route': {'database': 'owned'}, 'draft': draft}
    assert ADMINISTRATION.validate(request) == {'errors': []}
    statements = ADMINISTRATION.plan(request)['command_preview']['statements']
    direction = 'TO' if operation == 'grant' else 'FROM'
    assert [item['source'] for item in statements] == [
        f'{operation.upper()} USAGE ON EXCEPTION "WRITE_FAILURE" '
        f'{direction} USER "WRITE_READER"']


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('operation', ['create', 'recreate'])
def test_handled_error_blocks_are_not_split_or_rewritten(mode, operation):
    name = 'WRITE_' + mode
    draft = {'header': HEADER, 'body': BODY, 'sql_security': mode,
             'name' if operation == 'create' else 'confirmation': name}
    request = {'resource_kind': 'package', 'operation_id': operation,
               '_provider_route': {'database': 'owned'}, 'draft': draft,
               'target_resource': {'resource_kind': 'package',
                                   'display_name': name}}
    assert ADMINISTRATION.validate(request) == {'errors': []}
    statements = ADMINISTRATION.plan(request)['command_preview']['statements']
    assert len(statements) == 2
    assert statements[1]['source'] == (
        f'CREATE PACKAGE BODY "{name}" AS {BODY}')
    assert 'WHEN GDSCODE unique_key_violation DO' in BODY
    assert 'WHEN EXCEPTION WRITE_FAILURE DO' in BODY
