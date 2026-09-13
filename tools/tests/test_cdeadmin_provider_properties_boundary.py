"""Execute the real server-properties method without a PostgreSQL driver."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


def test_provider_properties_do_not_acquire_postgresql_manager():
    root = Path(__file__).resolve().parents[2]
    source = root / 'web/pgadmin/browser/server_groups/servers/__init__.py'
    syntax = ast.parse(source.read_text())
    function = next(node for node in ast.walk(syntax)
                    if isinstance(node, ast.FunctionDef)
                    and node.name == 'properties')
    function.decorator_list = []
    server = Mock()
    server.id = 20
    server.tags = None
    server.use_ssh_tunnel = False
    server.endpoint_profile.routes = []
    server.endpoint_profile.runtime_identity.verification_state = 'verified'
    profile = {'workflow': 'provider_endpoint', 'experience_family': 'sqlite',
               'profile_id': 'sqlite-native'}
    get_driver = Mock(side_effect=AssertionError('PostgreSQL driver accessed'))
    namespace = {
        'get_server': lambda _: server, 'get_driver': get_driver,
        'ServerGroup': Mock(), '_cde_registration': lambda _: profile,
        '_is_non_owner': lambda _: False,
        'convert_connection_parameter': lambda _: [],
        'get_db_restriction': lambda *_: [], 'config': SimpleNamespace(
            SERVER_MODE=True), 'ajax_response': lambda value: value,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]),
                 str(source), 'exec'), namespace)
    response = namespace['properties'](Mock(), 1, 20)
    assert response['connected'] is False
    assert response['runtime_verification_state'] == 'verified'
    assert response['cde_endpoint'] is True
    assert response['password'] is None
    assert response['connection_string'] is None
    get_driver.assert_not_called()
