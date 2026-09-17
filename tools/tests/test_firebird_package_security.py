"""Package-level SQL SECURITY does not inject settings into members."""
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from tools.cdeadmin_firebird_package_security_gate import (
    BODY, HEADER, MODES, package_request,
)
from pgadmin.cdeadmin.providers.firebird.ddl_dialect import generated_dialect


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('dialect', [1, 3])
def test_package_security_is_applied_only_to_header(mode, dialect):
    request = {**package_request(mode),
               '_provider_route': {'database': 'owned'}}
    with generated_dialect(dialect):
        assert ADMINISTRATION.validate(request) == {'errors': []}
        plan = ADMINISTRATION.plan(request)
    name = 'SEC_' + mode
    if dialect == 3:
        name = '"' + name + '"'
    clause = '' if mode == 'INHERIT' else ' SQL SECURITY ' + mode
    statements = plan['command_preview']['statements']
    assert [item['source'] for item in statements] == [
        f'CREATE PACKAGE {name}{clause} AS {HEADER}',
        f'CREATE PACKAGE BODY {name} AS {BODY}']


@pytest.mark.parametrize('mode', ['INVOKER', 'DEFINER'])
def test_database_default_is_an_explicit_separate_task(mode):
    request = {'resource_kind': 'database', 'operation_id': 'alter',
               'target_resource': {'resource_kind': 'database',
                                   'display_name': 'owned'},
               '_provider_route': {'database': 'owned'},
               'draft': {'default_sql_security': mode}}
    assert ADMINISTRATION.validate(request) == {'errors': []}
    statements = ADMINISTRATION.plan(request)['command_preview']['statements']
    assert len(statements) == 1
    assert statements[0]['source'] == (
        'ALTER DATABASE SET DEFAULT SQL SECURITY ' + mode)
