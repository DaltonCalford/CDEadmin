"""Native SUSPEND stays inside the provider's package body statement."""
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from tools.cdeadmin_firebird_suspend_gate import BODY, HEADER
from pgadmin.cdeadmin.providers.firebird.ddl_dialect import generated_dialect


@pytest.mark.parametrize('dialect', [1, 3])
@pytest.mark.parametrize('security', ['INHERIT', 'INVOKER', 'DEFINER'])
def test_suspend_is_not_a_client_side_transaction_boundary(dialect, security):
    request = {'resource_kind': 'package', 'operation_id': 'create',
               '_provider_route': {'database': 'owned'},
               'draft': {'name': 'SUSPEND_PACKAGE', 'header': HEADER,
                         'body': BODY, 'sql_security': security}}
    with generated_dialect(dialect):
        assert ADMINISTRATION.validate(request) == {'errors': []}
        statements = ADMINISTRATION.plan(request)['command_preview'][
            'statements']
    assert len(statements) == 2
    name = 'SUSPEND_PACKAGE' if dialect == 1 else '"SUSPEND_PACKAGE"'
    assert statements[1]['source'] == f'CREATE PACKAGE BODY {name} AS {BODY}'
