"""Packaged external routines retain native code and package ownership."""
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from tools.cdeadmin_firebird_catalog_dialect_gate import (
    udr_package_body, udr_package_header,
)
from pgadmin.cdeadmin.providers.firebird import functions, procedures
from pgadmin.cdeadmin.providers.firebird.ddl_dialect import generated_dialect
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


@pytest.mark.parametrize('operation', [
    'create', 'recreate', 'create_body', 'replace_body'])
@pytest.mark.parametrize('dialect', [1, 3])
def test_package_udr_code_is_not_split_at_member_semicolons(
        operation, dialect):
    body = udr_package_body()
    draft = {'body': body}
    if operation in {'create', 'recreate'}:
        draft['header'] = udr_package_header()
        draft['name' if operation == 'create' else 'confirmation'] = 'PU'
    request = {
        'resource_kind': 'package', 'operation_id': operation,
        '_provider_route': {'database': 'owned'}, 'draft': draft,
        'target_resource': {'resource_kind': 'package', 'display_name': 'PU'}}
    with generated_dialect(dialect):
        assert ADMINISTRATION.validate(request) == {'errors': []}
        plan = ADMINISTRATION.plan(request)
    statements = plan['command_preview']['statements']
    assert len(statements) == (2 if operation in {'create', 'recreate'} else 1)
    name = 'PU' if dialect == 1 else '"PU"'
    verb = 'RECREATE' if operation == 'replace_body' else 'CREATE'
    assert statements[-1]['source'] == f'{verb} PACKAGE BODY {name} AS {body}'
    assert "AS 'opaque ; it''s preserved';" in statements[-1]['source']


@pytest.mark.parametrize('kind,compiler', [
    ('function', functions), ('procedure', procedures)])
@pytest.mark.parametrize('operation', ['create_or_alter', 'recreate'])
def test_external_package_member_requires_owning_package(
        kind, compiler, operation):
    target = {'resource_kind': kind, 'display_name': 'F', 'native': {
        'package': 'PU', 'engine_name': 'UDR',
        'entrypoint': 'udrcpp_example!sum_args'}}
    draft = {'declaration': "EXTERNAL NAME 'ignored' ENGINE UDR",
             'name' if operation == 'create_or_alter' else 'confirmation': 'F'}
    with pytest.raises(RelationalClientError, match='owning package'):
        compiler.compile_operation(operation, draft, target)
