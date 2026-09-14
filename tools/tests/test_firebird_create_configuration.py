"""Creation must use the same selected auth/wire/session settings as attach."""

import os
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION  # noqa
from pgadmin.cdeadmin.providers.firebird.provider import (
    _create_client, _database_create_arguments, _route_arguments,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


@pytest.mark.parametrize('trusted', [False, True])
@pytest.mark.parametrize('compression', [False, True])
@pytest.mark.parametrize('protocol', ['INET4', 'INET6'])
def test_create_retains_every_selected_connection_option(
        trusted, compression, protocol):
    import firebird.driver as driver
    route = {'host': '::1' if protocol == 'INET6' else 'localhost',
             'port': 53050, 'user': 'creation-user', 'trusted_auth': trusted,
             'protocol': protocol, 'timeout': 11, 'dummy_packet_interval': 20,
             'wire_crypt': 'Required', 'wire_compression': compression,
             'wire_config': 'WireCryptPlugin=ChaCha64',
             'charset': 'WIN1252', 'role': 'CREATOR', 'no_gc': True,
             'no_db_triggers': True, 'session_time_zone': 'America/Toronto',
             'auth_plugin_list': 'Win_Sspi' if trusted else 'Srp256',
             'dbkey_scope': 'ATTACHMENT'}
    dsn = 'inet6://[::1]:53050//srv/' + uuid.uuid4().hex + '.fdb'
    options = {'page_size': 16384, 'default_charset': 'UTF8',
               'sql_dialect': 1, 'forced_writes': False,
               'reserve_space': False}
    environment = {'ISC_USER': 'unselected-environment-user',
                   'ISC_PASSWORD': 'environment-canary'}
    with patch.dict(os.environ, environment):
        args = _database_create_arguments(route, dsn, options, driver)
    config = driver.driver_config.get_database(args['database'])
    server = driver.driver_config.get_server(config.server.value)
    assert config.dsn.value == dsn
    assert config.database.value is None
    assert server.host.value is None
    assert server.port.value is None
    assert config.user.value is None and config.password.value is None
    assert server.password.value is None
    assert server.user.value == (None if trusted else 'creation-user')
    assert config.trusted_auth.value is trusted
    assert config.timeout.value == 11
    assert config.dummy_packet_interval.value == 20
    assert config.config.value == (
        'WireCrypt=Required\nWireCompression=' + str(compression).lower() +
        '\nWireCryptPlugin=ChaCha64')
    assert config.page_size.value == 16384
    assert config.db_charset.value == 'UTF8'
    assert config.db_sql_dialect.value == 1
    assert config.forced_writes.value is False
    assert config.reserve_space.value is False
    for key in ('charset', 'role', 'no_gc', 'no_db_triggers',
                'session_time_zone', 'auth_plugin_list'):
        assert args[key] == route[key]
    assert args['dbkey_scope'] == driver.DBKeyScope.ATTACHMENT
    assert args['overwrite'] is False
    assert args.get('user') == (None if trusted else 'creation-user')
    assert 'password' not in args


@pytest.mark.parametrize('options', [
    {'page_size': 8192}, {'page_size': 16384},
    {'default_charset': 'UTF8'}, {'default_charset': 'WIN1252'},
])
def test_creation_configuration_does_not_mutate_attachment(options):
    import firebird.driver as driver
    route = {'host': 'localhost', 'port': 53050, 'database': 'source',
             'protocol': 'INET', 'timeout': 10}
    attachment = _route_arguments(route, driver)
    before = driver.driver_config.get_database(attachment['database'])
    dsn = 'localhost/53050:/srv/new.fdb'
    creation = _database_create_arguments(route, dsn, options, driver)
    assert attachment['database'] != creation['database']
    assert before.database.value == 'source'
    again = _database_create_arguments(route, dsn, options, driver)
    assert creation == again


@pytest.mark.parametrize('invalid', [
    {'wire_crypt': 'invalid'}, {'wire_compression': 'false'},
    {'wire_config': {'bad': 'value'}},
])
def test_invalid_configuration_never_becomes_cached_success(invalid):
    import firebird.driver as driver
    route = {'host': 'localhost', 'port': 53050,
             'database': 'source', **invalid}
    for _attempt in range(3):
        with pytest.raises(RelationalClientError):
            _route_arguments(route, driver)
        with pytest.raises(RelationalClientError):
            _database_create_arguments(route, 'localhost:/srv/new.fdb', {},
                                       driver)


def test_sdk_create_does_not_merge_old_username_into_trusted_attachment():
    import firebird.driver as driver
    with patch('pgadmin.cdeadmin.providers.firebird.provider.'
               '_configure_client_library'):
        client = _create_client(SimpleNamespace(acquire_secret=None))
    connection = Mock()
    request = {'route': {
        'host': 'localhost', 'port': 53050, 'user': 'not-selected',
        'trusted_auth': True, 'auth_plugin_list': 'Win_Sspi'},
        'create_options': {'page_size': 16384}}
    with patch.object(driver, 'create_database',
                      return_value=connection) as call:
        result = client.create_database(
            request, 'localhost/53050:/srv/new.fdb',
            'firebird-create-database')
    assert call.call_count == 1
    assert 'user' not in call.call_args.kwargs
    assert 'password' not in call.call_args.kwargs
    assert call.call_args.kwargs['auth_plugin_list'] == 'Win_Sspi'
    assert call.call_args.kwargs['overwrite'] is False
    assert result['driver_returned'] is True
    connection.close.assert_called_once()


def test_unknown_creation_options_fail_without_silently_ignoring_them():
    import firebird.driver as driver
    with pytest.raises(RelationalClientError, match='unsupported'):
        _database_create_arguments({'host': 'localhost'}, 'localhost:new',
                                   {'made_up_native_feature': True}, driver)
