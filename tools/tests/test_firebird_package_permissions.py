"""Package permission forms compile native object-level EXECUTE grants."""
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from tools.cdeadmin_firebird_package_permissions_gate import permission_request
from pgadmin.cdeadmin.providers.firebird.ddl_dialect import generated_dialect


@pytest.mark.parametrize('operation', ['grant', 'revoke'])
@pytest.mark.parametrize('principal_kind', ['USER', 'ROLE'])
@pytest.mark.parametrize('dialect', [1, 3])
def test_package_execute_permission_task(operation, principal_kind, dialect):
    request = {**permission_request(operation, principal_kind),
               '_provider_route': {'database': 'owned'}}
    principal = request['draft']['principal']
    with generated_dialect(dialect):
        assert ADMINISTRATION.validate(request) == {'errors': []}
        plan = ADMINISTRATION.plan(request)
    quote = (lambda value: '"' + value + '"') if dialect == 3 else str
    direction = 'TO' if operation == 'grant' else 'FROM'
    statements = plan['command_preview']['statements']
    assert len(statements) == 1
    assert statements[0]['source'] == (
        f'{operation.upper()} EXECUTE ON PACKAGE {quote("PU")} '
        f'{direction} {principal_kind} {quote(principal)}')


@pytest.mark.parametrize('principal_kind', ['USER', 'ROLE'])
def test_revoke_requires_exact_principal_confirmation(principal_kind):
    request = permission_request('revoke', principal_kind)
    request['draft']['confirmation'] = 'WRONG'
    assert ADMINISTRATION.validate(request)['errors']


@pytest.mark.parametrize('privilege', ['SELECT', 'INSERT', 'UPDATE', 'DELETE'])
def test_package_does_not_advertise_table_privileges(privilege):
    request = permission_request('grant')
    request['draft']['privileges'] = [privilege]
    assert ADMINISTRATION.validate(request)['errors']


@pytest.mark.parametrize('operation', ['grant', 'revoke'])
@pytest.mark.parametrize('dialect', [1, 3])
def test_package_delegation_options_have_distinct_native_sql(
        operation, dialect):
    request = {**permission_request(operation, 'USER'),
               '_provider_route': {'database': 'owned'}}
    option = 'grant_option' if operation == 'grant' else 'grant_option_only'
    request['draft'][option] = True
    with generated_dialect(dialect):
        assert ADMINISTRATION.validate(request) == {'errors': []}
        plan = ADMINISTRATION.plan(request)
    quote = (lambda value: '"' + value + '"') if dialect == 3 else str
    if operation == 'grant':
        expected = (f'GRANT EXECUTE ON PACKAGE {quote("PU")} TO USER '
                    f'{quote("PU_READER")} WITH GRANT OPTION')
    else:
        expected = ('REVOKE GRANT OPTION FOR EXECUTE ON PACKAGE '
                    f'{quote("PU")} '
                    f'FROM USER {quote("PU_READER")}')
    assert plan['command_preview']['statements'][0]['source'] == expected


@pytest.mark.parametrize('operation,option', [
    ('grant', 'grant_option_only'), ('revoke', 'grant_option')])
def test_package_delegation_rejects_wrong_operation_option(operation, option):
    request = permission_request(operation, 'USER')
    request['draft'][option] = True
    assert ADMINISTRATION.validate(request)['errors']
