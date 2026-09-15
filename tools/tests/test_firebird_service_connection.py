"""Services UTF-8 marker, secret isolation and optional security context."""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import RelationalClientError
from pgadmin.cdeadmin.providers.firebird.provider import _server_arguments
from pgadmin.cdeadmin.providers.firebird.service_connection import (
    connect_service, notify_attached,
)


def setup_connector():
    module, core = Mock(), Mock()
    config = SimpleNamespace(**{
        name: SimpleNamespace(value=value) for name, value in {
            'host': 'inet://localhost:53050/service_mgr',
            'user': 'default-user', 'trusted_auth': False,
            'config': 'WireCrypt=Required', 'auth_plugin_list': 'Srp256',
        }.items()})
    module.driver_config.get_server.return_value = config
    core.SPB_ATTACH.return_value.get_buffer.return_value = b'initial'
    core.XpbKind.SPB_ATTACH = 3
    core.SPBItem.UTF8_FILENAME = 118
    builder = Mock()
    builder.get_buffer.return_value = b'utf8-marked'
    module.get_api.return_value.util.get_xpb_builder.return_value = (
        MagicMock(__enter__=Mock(return_value=builder)))
    dispatcher = Mock()
    module.get_api.return_value.master.get_dispatcher.return_value = (
        MagicMock(__enter__=Mock(return_value=dispatcher)))
    return module, core, config, builder, dispatcher


@pytest.mark.parametrize('trusted', [False, True])
def test_attachment_uses_explicit_utf8_and_never_config_password(trusted):
    module, core, config, builder, dispatcher = setup_connector()
    config.trusted_auth.value = trusted
    crypt = Mock()
    result = connect_service(
        module, core, server='private', user='é', password='密-secret',
        expected_db='/owned/東京.fdb', role='rôle', crypt_callback=crypt)
    core.SPB_ATTACH.assert_called_once_with(
        user='é', password='密-secret', trusted_auth=trusted,
        config='WireCrypt=Required', auth_plugin_list='Srp256',
        expected_db='/owned/東京.fdb', role='rôle',
        encoding='utf-8', errors='strict')
    builder.insert_tag.assert_called_once_with(118)
    core.Server.assert_called_once_with(
        None, b'utf8-marked', config.host.value, 'utf-8', 'strict')
    dispatcher.set_dbcrypt_callback.assert_called_once_with(crypt)
    dispatcher.attach_service_manager.assert_called_once_with(
        config.host.value, b'utf8-marked')
    assert result._svc is dispatcher.attach_service_manager.return_value
    core.get_callbacks.assert_not_called()


def test_native_attach_is_not_attempted_when_text_cannot_be_encoded():
    module, core, _config, _builder, dispatcher = setup_connector()
    core.SPB_ATTACH.return_value.get_buffer.side_effect = ValueError('text')
    with pytest.raises(ValueError):
        connect_service(module, core, server='private')
    dispatcher.attach_service_manager.assert_not_called()


@pytest.mark.parametrize('role', [
    'ROLE -shut full', '"Role with spaces"', 'ROLE\x00extra',
    'ROLE\nextra', 'ROLE\textra', False, 12,
])
def test_service_role_cannot_split_into_utility_switches(role):
    module, core, _config, _builder, dispatcher = setup_connector()
    with pytest.raises(RelationalClientError, match='role transport'):
        connect_service(module, core, server='private', role=role)
    core.SPB_ATTACH.assert_not_called()
    dispatcher.attach_service_manager.assert_not_called()


@pytest.mark.parametrize('bad', ['missing', 'address'])
def test_unconfigured_service_target_is_rejected(bad):
    module, core, config, _builder, dispatcher = setup_connector()
    if bad == 'missing':
        module.driver_config.get_server.return_value = None
    else:
        config.host.value = 'not-a-service-address'
    with pytest.raises(RelationalClientError):
        connect_service(module, core, server='private')
    dispatcher.attach_service_manager.assert_not_called()


def test_attachment_hooks_use_the_driver_registry_once_each():
    core, handle = Mock(), Mock()
    callbacks = [Mock(), Mock()]
    core.get_callbacks.return_value = callbacks
    notify_attached(core, handle)
    core.get_callbacks.assert_called_once_with(
        core.ServerHook.ATTACHED, handle)
    for callback in callbacks:
        callback.assert_called_once_with(handle)


@pytest.mark.parametrize('context', [None, '', ' ', '/owned/東京.fdb', 'alias'])
def test_service_security_context_is_optional_and_not_a_database(context):
    import firebird.driver as driver
    route = {'host': 'localhost', 'port': 53050,
             'service_expected_database': context}
    with patch('pgadmin.cdeadmin.providers.firebird.provider.'
               '_configure_client_library'):
        result = _server_arguments(route, driver)
    assert result.get('expected_db') == ((context or '').strip() or None)
    assert 'database' not in result


@pytest.mark.parametrize('context', [False, 42, [], {}, 'bad\x00path'])
def test_invalid_security_context_fails_before_native_configuration(context):
    module = Mock()
    with pytest.raises(RelationalClientError):
        _server_arguments({'service_expected_database': context}, module)
    module.driver_config.get_server.assert_not_called()
