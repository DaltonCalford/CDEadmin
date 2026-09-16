"""Native transport selection must not silently fall back to TCP."""

from unittest.mock import Mock

import firebird.driver as native
from firebird.driver.config import DriverConfig
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION  # noqa
from pgadmin.cdeadmin.providers.firebird import connection_strings as strings
from pgadmin.cdeadmin.providers.firebird import provider
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


@pytest.mark.parametrize('protocol', ['WNET', 'bad', '', True, 1, [], {}])
@pytest.mark.parametrize('scope', ['attach', 'create', 'service', 'dsn'])
def test_invalid_transport_refused_before_driver_access(protocol, scope):
    module = Mock()
    route = {'host': 'localhost', 'port': 53050, 'database': 'owned',
             'protocol': protocol}
    with pytest.raises(RelationalClientError, match='protocol'):
        if scope == 'attach':
            provider._route_arguments(route, module)
        elif scope == 'create':
            provider._database_create_arguments(route, 'localhost:owned',
                                                {}, module)
        elif scope == 'service':
            provider._server_arguments(route, module)
        else:
            strings.database_dsn('owned', 'localhost', 53050, protocol)
    assert not module.mock_calls


@pytest.mark.parametrize('scope', ['attach', 'create', 'service', 'dsn'])
def test_xnet_refuses_non_windows_application_host(monkeypatch, scope):
    monkeypatch.setattr(strings, 'WINDOWS_CLIENT', False)
    module = Mock()
    route = {'database': 'owned', 'protocol': 'XNET'}
    with pytest.raises(RelationalClientError, match='Windows'):
        if scope == 'attach':
            provider._route_arguments(route, module)
        elif scope == 'create':
            provider._database_create_arguments(route, 'xnet://owned',
                                                {}, module)
        elif scope == 'service':
            provider._server_arguments(route, module)
        else:
            strings.database_dsn('owned', protocol='XNET')
    assert not module.mock_calls


@pytest.mark.parametrize('host,port', [
    ('remote', None), ('127.0.0.1', None),
    (None, 3050), ('localhost', 3050)])
def test_xnet_does_not_ignore_remote_address_or_tcp_port(
        monkeypatch, host, port):
    monkeypatch.setattr(strings, 'WINDOWS_CLIENT', True)
    with pytest.raises(RelationalClientError, match='local target'):
        strings.database_dsn('owned', host, port, 'XNET')


@pytest.mark.parametrize('host', [None, '', 'localhost'])
@pytest.mark.parametrize('database', ['owned', r'C:\data\owned.fdb'])
def test_windows_xnet_dsn_mapping_has_no_tcp_address(
        monkeypatch, host, database):
    monkeypatch.setattr(strings, 'WINDOWS_CLIENT', True)
    registry = DriverConfig('owned-xnet')
    monkeypatch.setattr(native, 'driver_config', registry)
    monkeypatch.setattr(provider, '_configure_client_library', Mock())
    route = {'host': host, 'database': database, 'protocol': 'XNET'}
    args = provider._route_arguments(route, native)
    config = registry.get_database(args['database'])
    server = registry.get_server(config.server.value)
    assert config.dsn.value == 'xnet://' + database
    assert config.database.value is None
    assert server.host.value is None and server.port.value is None
    service = provider._server_arguments(route, native)
    service_config = registry.get_server(service['server'])
    assert service_config.host.value == 'xnet://service_mgr'
    assert service_config.port.value is None


@pytest.mark.parametrize('protocol', ['WNET', 'invalid'])
def test_database_creation_compiler_cannot_discard_transport(protocol):
    request = {'_provider_route': {
        'host': 'localhost', 'port': 53050,
        'database_create_root': '/owned', 'protocol': protocol},
        'draft': {'database_path': '/owned/new.fdb'}}
    with pytest.raises(RelationalClientError, match='protocol'):
        ADMINISTRATION._compile_database_create(request)
