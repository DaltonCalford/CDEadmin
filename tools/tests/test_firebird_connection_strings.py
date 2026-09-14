"""Address grammar and route isolation without a running database."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird.connection_strings import (
    database_dsn, server_host, service_dsn, target_path,
)
from pgadmin.cdeadmin.providers.firebird.provider import (
    _route_arguments, _server_arguments,
)
from pgadmin.cdeadmin.sdk.relational import (
    RelationalClientError, RelationalDBAPIClient,
)


@pytest.mark.parametrize('host,expected', [
    ('db.example', 'db.example'), ('127.0.0.10', '127.0.0.10'),
    ('::1', '[::1]'), ('[::1]', '[::1]'),
    ('fe80::1%eth0', '[fe80::1%eth0]'),
    ('[127.0.0.10]', '[127.0.0.10]'), ('[db.example]', '[db.example]'),
    (None, None), ('', None),
])
def test_native_host_brackets(host, expected):
    assert server_host(host) == expected


@pytest.mark.parametrize('host', [
    'db/3050', 'db:3050', 'inet://db', 'db:service_mgr',
    '[::1', '::1]', '[[]]', '[]', 'db name', 'db\nname', 123,
])
def test_host_cannot_inject_port_database_or_protocol(host):
    with pytest.raises(RelationalClientError):
        server_host(host)


@pytest.mark.parametrize('path', [
    'inventory', '/srv/data/inventory.fdb', '/srv/data:part/inventory.fdb',
    r'C:\data\inventory.fdb', 'D:/data/inventory.fdb',
])
@pytest.mark.parametrize('host,expected', [
    ('db.example', 'db.example'), ('::1', '[::1]'),
])
def test_filename_colon_never_discards_selected_server(path, host, expected):
    assert database_dsn(path, host, 53050) == expected + '/53050:' + path
    assert database_dsn(path, host, 53050, 'INET6') == (
        'inet6://' + expected + ':53050/' + path)


@pytest.mark.parametrize('value', [
    'other:inventory', 'db.example/3051:inventory',
    'inet://db.example/inventory', 'inet6://[::1]/inventory',
    'db.example:other:inventory',
])
def test_database_cannot_redirect_selected_endpoint(value):
    with pytest.raises(RelationalClientError, match='selected server'):
        target_path(value, 'db.example', 3050)


@pytest.mark.parametrize('value,host,port,path', [
    ('DB.EXAMPLE:inventory', 'db.example', 3050, 'inventory'),
    ('[::1]/53050:/db/one.fdb', '0:0:0:0:0:0:0:1', 53050, '/db/one.fdb'),
    (r'db.example/3050:C:\db\one.fdb', 'db.example', None,
     r'C:\db\one.fdb'),
    ('db.example/fb_db:inventory', 'db.example', 'fb_db', 'inventory'),
])
def test_same_endpoint_legacy_dsn_is_normalized(value, host, port, path):
    assert target_path(value, host, port) == path


@pytest.mark.parametrize('port', [True, 0, -1, 65536, 'a/b', '1:db', ''])
def test_invalid_ports_fail_before_connect(port):
    with pytest.raises(RelationalClientError, match='port'):
        database_dsn('inventory', 'localhost', port)


@pytest.mark.parametrize('protocol', [None, 'INET', 'INET4', 'INET6'])
@pytest.mark.parametrize('path', ['/srv/db:part/one.fdb', r'C:\db\one.fdb'])
def test_driver_configuration_preserves_address_and_path(protocol, path):
    import firebird.driver as driver
    from firebird.driver.core import _connect_helper
    route = {'host': '::1', 'port': 53050, 'database': path,
             'timeout': 5, 'user': 'address_test'}
    if protocol:
        route['protocol'] = protocol
    arguments = _route_arguments(route, driver)
    config = driver.driver_config.get_database(arguments['database'])
    server = driver.driver_config.get_server(config.server.value)
    actual = _connect_helper(
        config.dsn.value, server.host.value, server.port.value,
        config.database.value, config.protocol.value)
    assert actual == database_dsn(path, '::1', 53050, protocol)
    assert config.timeout.value == 5
    assert arguments['user'] == 'address_test'


@pytest.mark.parametrize('protocol', [None, 'INET', 'INET4', 'INET6'])
def test_services_use_the_selected_address_family(protocol):
    import firebird.driver as driver
    route = {'host': '::1', 'port': 53050, 'protocol': protocol}
    with patch('pgadmin.cdeadmin.providers.firebird.provider.'
               '_configure_client_library'):
        result = _server_arguments(route, driver)
    server = driver.driver_config.get_server(result['server'])
    assert server.host.value == service_dsn('::1', 53050, protocol)
    assert server.port.value is None


def test_private_config_identity_does_not_reuse_other_principal_defaults():
    import firebird.driver as driver
    route = {'host': '::1', 'port': 53050, 'database': 'inventory',
             'timeout': 5, 'user': 'one', 'auth_plugin_list': 'Srp256'}
    one = _route_arguments(route, driver)
    two = _route_arguments({**route, 'user': 'two'}, driver)
    three = _route_arguments({**route, 'auth_plugin_list': 'Srp'}, driver)
    assert len({item['database'] for item in (one, two, three)}) == 3


def test_create_uses_ipv6_and_preserves_colon_in_approved_root():
    request = {
        '_provider_route': {'host': '::1', 'port': 53050,
                            'database': '/srv/data:part/old.fdb',
                            'protocol': 'INET6'},
        'draft': {'database_path': '/srv/data:part/new.fdb'},
    }
    compiled = ADMINISTRATION._compile_database_create(request)
    assert compiled['database'] == (
        'inet6://[::1]:53050//srv/data:part/new.fdb')
    assert compiled['endpoint_database'] == '/srv/data:part/new.fdb'


def test_hostless_legacy_dsn_still_delegates_to_native_driver():
    assert _route_arguments({'database': 'db.example:inventory'}) == {
        'database': 'db.example:inventory', 'charset': 'UTF8'}


@pytest.mark.parametrize('codes,expected', [
    ((335544375,), (335544375,)),
    ((335544721, 335544344), (335544721, 335544344)),
    (('private',), ()), ((True,), ()), (tuple(range(1, 34)), ()),
])
def test_connection_failure_keeps_only_bounded_native_codes(codes, expected):
    client = object.__new__(RelationalDBAPIClient)
    client.config = SimpleNamespace(
        profile=SimpleNamespace(engine_id='firebird', engine_name='Firebird'),
        connect_positional=lambda _route: (),
        connect_arguments=lambda _route: {})
    error = RuntimeError('private password and native arguments')
    error.gds_codes = codes
    connector = Mock(side_effect=error)
    with pytest.raises(RelationalClientError) as caught:
        client._invoke_connector({'route': {'host': 'localhost'}}, connector)
    assert caught.value.gds_codes == expected
    assert 'private' not in str(caught.value)
    assert connector.call_count == 1
