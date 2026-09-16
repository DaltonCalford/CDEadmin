"""Creation DPB fidelity, initial ownership and refusal before mutation."""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import firebird.driver as native
from firebird.driver.config import DriverConfig
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import _route_arguments
from pgadmin.cdeadmin.providers.firebird.provider import (
    _database_create_arguments, PROFILE,
)
from pgadmin.cdeadmin.providers.firebird.database_creation import (
    CONFIG_FIELDS, create_database,
)
from pgadmin.cdeadmin.sdk.relational import (
    RelationalClientError, RelationalClientConfig, RelationalDBAPIClient,
)
from pgadmin.cdeadmin.security import SecretLease


@pytest.fixture
def fixture(monkeypatch):
    registry = DriverConfig('owned-creation-test')
    monkeypatch.setattr(native, 'driver_config', registry)
    args = _database_create_arguments(
        {'host': '127.0.0.1', 'port': 53050, 'user': 'owner'},
        '127.0.0.1/53050:/owned/new.fdb', {}, native)
    private = registry.get_database(args['database'])
    api = MagicMock()
    module = SimpleNamespace(driver_config=registry, get_api=Mock(
        return_value=api))
    core = SimpleNamespace(
        DPB=Mock(), Connection=Mock(), get_callbacks=Mock(return_value=[]),
        ConnectionHook=native.core.ConnectionHook, FS_ENCODING='latin-1')
    core.DPB.return_value.get_buffer.return_value = b'owned-dpb'
    return module, core, private, args, api.master.get_dispatcher().__enter__()


@pytest.mark.parametrize('workers', [None, 0, 1, 2, 4, 5, 32767])
@pytest.mark.parametrize('utf8', [False, True])
def test_all_config_fields_and_initial_attachment_preserved(
        fixture, workers, utf8):
    module, core, config, args, dispatcher = fixture
    config.parallel_workers.value = workers
    config.utf8filename.value = utf8
    config.sql_dialect.value = 1
    config.db_sql_dialect.value = 3
    callback = object()
    connection = create_database(
        module, core, **{**args, 'charset': 'utf8'}, password='secret',
        role='OWNER', no_gc=True, no_db_triggers=True,
        dbkey_scope=native.DBKeyScope.ATTACHMENT,
        crypt_callback=callback, session_time_zone='UTC',
        auth_plugin_list='Srp256')
    dpb = core.DPB.call_args.kwargs
    assert {key: dpb[key] for key in CONFIG_FIELDS} == {
        key: getattr(config, key).value for key in CONFIG_FIELDS}
    assert dpb['password'] == 'secret'
    assert dpb['user'] == 'owner'
    assert dpb['role'] == 'OWNER'
    assert dpb['charset'] == 'UTF8'
    assert dpb['sql_dialect'] == 3
    assert dpb['no_gc'] and dpb['no_db_triggers']
    assert dpb['dbkey_scope'] == native.DBKeyScope.ATTACHMENT
    assert dpb['session_time_zone'] == 'UTC'
    assert dpb['auth_plugin_list'] == 'Srp256'
    assert dpb['overwrite'] is False
    core.DPB.return_value.get_buffer.assert_called_once_with(for_create=True)
    dispatcher.set_dbcrypt_callback.assert_called_once_with(callback)
    dispatcher.create_database.assert_called_once_with(
        config.dsn.value, b'owned-dpb', 'utf-8' if utf8 else 'latin-1')
    core.Connection.assert_called_once_with(
        dispatcher.create_database.return_value, config.dsn.value,
        b'owned-dpb', 1, 'UTF8')
    assert connection is core.Connection.return_value
    core.get_callbacks.assert_called_once_with(
        core.ConnectionHook.ATTACHED, connection)
    dispatcher.attach_database.assert_not_called()


@pytest.mark.parametrize('value', [True, 1, None, 'false'])
def test_overwrite_refused_before_driver_access(fixture, value):
    module, core, _config, args, _dispatcher = fixture
    with pytest.raises(RelationalClientError, match='overwrite'):
        create_database(module, core, **{**args, 'overwrite': value})
    module.get_api.assert_not_called()
    core.DPB.assert_not_called()


@pytest.mark.parametrize('name', [None, '', 'arbitrary.fdb',
                                  'cde_database_' + 'f' * 24])
def test_no_fallback_to_global_defaults(fixture, name):
    module, core, _config, args, _dispatcher = fixture
    defaults = module.driver_config.db_defaults.get_config()
    with pytest.raises(RelationalClientError, match='configuration'):
        create_database(module, core, **{**args, 'database': name})
    assert module.driver_config.db_defaults.get_config() == defaults
    module.get_api.assert_not_called()


@pytest.mark.parametrize('field,value', [
    ('dsn', None), ('dsn', ''), ('dsn', 'bad\x00target'),
    ('database', 'conflicting.fdb'), ('server', 'missing'),
])
def test_invalid_target_refused(fixture, field, value):
    module, core, config, args, _dispatcher = fixture
    getattr(config, field).value = value
    with pytest.raises(RelationalClientError, match='target'):
        create_database(module, core, **args)
    module.get_api.assert_not_called()


@pytest.mark.parametrize('field,value', [('host', 'other'), ('port', '9999')])
def test_no_server_default_injection(fixture, field, value):
    module, core, config, args, _dispatcher = fixture
    server = module.driver_config.get_server(config.server.value)
    getattr(server, field).value = value
    with pytest.raises(RelationalClientError, match='target'):
        create_database(module, core, **args)
    module.get_api.assert_not_called()


@pytest.mark.parametrize('workers', [-1, 32768, True, False, 1.0, '1', []])
def test_malformed_worker_dpb_refused_before_mutation(
        fixture, monkeypatch, workers):
    module, core, config, args, _dispatcher = fixture
    malformed = SimpleNamespace(
        server=config.server, dsn=config.dsn, database=config.database,
        parallel_workers=SimpleNamespace(value=workers))
    monkeypatch.setattr(module.driver_config, 'get_database',
                        lambda _name: malformed)
    with pytest.raises(RelationalClientError, match='parallel workers'):
        create_database(module, core, **args)
    core.DPB.assert_not_called()
    module.get_api.assert_not_called()


@pytest.mark.parametrize('stage', ['buffer', 'crypt', 'dispatcher_exit'])
def test_failures_around_native_creation_do_not_leak_owner(fixture, stage):
    module, core, _config, args, dispatcher = fixture
    error = RuntimeError('owned failure')
    context = module.get_api.return_value.master.get_dispatcher.return_value
    if stage == 'buffer':
        core.DPB.return_value.get_buffer.side_effect = error
    elif stage == 'crypt':
        dispatcher.set_dbcrypt_callback.side_effect = error
    else:
        context.__exit__.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        create_database(module, core, **args, crypt_callback=object())
    assert caught.value is error
    core.get_callbacks.assert_not_called()
    if stage == 'dispatcher_exit':
        dispatcher.create_database.return_value.detach.assert_called_once()
        assert core.Connection.return_value._att is None
    else:
        dispatcher.create_database.assert_not_called()


@pytest.mark.parametrize('stage', ['callback', 'constructor', 'dispatcher'])
def test_failed_creation_releases_unpublished_attachment(fixture, stage):
    module, core, _config, args, dispatcher = fixture
    error = RuntimeError('owned failure')
    if stage == 'callback':
        core.get_callbacks.return_value = [Mock(side_effect=error)]
    elif stage == 'constructor':
        core.Connection.side_effect = error
    else:
        dispatcher.create_database.side_effect = error
    attachment = dispatcher.create_database.return_value
    with pytest.raises(RuntimeError) as caught:
        create_database(module, core, **args)
    assert caught.value is error
    if stage != 'dispatcher':
        attachment.detach.assert_called_once_with()
    else:
        attachment.detach.assert_not_called()
    if stage == 'callback':
        connection = core.Connection.return_value
        connection._close.assert_called_once_with()
        connection._close_internals.assert_called_once_with()
        assert connection._att is None
    attachment.drop_database.assert_not_called()


def test_attached_hooks_run_in_order_and_return_values_are_ignored(fixture):
    module, core, _config, args, _dispatcher = fixture
    seen = []
    core.get_callbacks.return_value = [
        lambda con: seen.append((1, con)), lambda con: seen.append((2, con))]
    result = create_database(module, core, **args)
    assert seen == [(1, result), (2, result)]


@pytest.mark.parametrize('custom', [False, True])
@pytest.mark.parametrize('failed', [False, True])
def test_sdk_creator_preserves_credential_lease_and_default_connector(
        custom, failed):
    lease = SecretLease(b'owned-test-secret')
    module = SimpleNamespace(connect=Mock(), create_database=Mock())
    creator = Mock()
    chosen = creator if custom else module.create_database
    if failed:
        chosen.side_effect = RuntimeError('owned-test-secret')
    config = RelationalClientConfig(
        profile=PROFILE, module_name='owned-test', version_query='unused',
        connect_arguments=lambda _route: {}, metadata_reader=lambda *_: [],
        database_create_arguments=lambda route, database, options: {
            'database': database, 'overwrite': False},
        database_creator=creator if custom else None,
        credential_argument='password', secret_acquirer=lambda *_: lease)
    client = RelationalDBAPIClient(config, module)
    request = {'route': {'host': 'owned',
                         'credential_reference_id': 'owned-reference',
                         'principal_reference': 'owned-principal'}}
    if failed:
        with pytest.raises(RelationalClientError) as caught:
            client.create_database(request, 'owned.fdb',
                                   'firebird-create-database')
        assert 'owned-test-secret' not in str(caught.value)
    else:
        result = client.create_database(request, 'owned.fdb',
                                        'firebird-create-database')
        assert result['driver_returned'] is True
        chosen.return_value.close.assert_called_once_with()
    chosen.assert_called_once_with(database='owned.fdb', overwrite=False,
                                   password='owned-test-secret')
    assert lease.closed
    assert set(lease._buffer) == {0}
    module.connect.assert_not_called()
    if custom:
        module.create_database.assert_not_called()


@pytest.mark.parametrize('value', [False, 1, 'create', {}])
def test_sdk_creator_must_be_callable(value):
    with pytest.raises(RelationalClientError, match='database_creator'):
        RelationalClientConfig(
            profile=PROFILE, module_name='owned', version_query='unused',
            connect_arguments=lambda _: {}, metadata_reader=lambda *_: [],
            database_creator=value)


@pytest.mark.parametrize('stage', ['close', 'internals', 'detach'])
def test_cleanup_failures_do_not_skip_native_detach(fixture, stage):
    module, core, _config, args, dispatcher = fixture
    core.get_callbacks.return_value = [Mock(side_effect=RuntimeError('hook'))]
    con = core.Connection.return_value
    attachment = dispatcher.create_database.return_value
    target = {'close': con._close, 'internals': con._close_internals,
              'detach': attachment.detach}[stage]
    target.side_effect = RuntimeError('cleanup')
    with pytest.raises(RuntimeError):
        create_database(module, core, **args)
    con._close_internals.assert_called_once_with()
    attachment.detach.assert_called_once_with()
    assert con._att is None


def test_unexposed_worker_setting_does_not_inherit_driver_default(fixture):
    module, _core, _config, _args, _dispatcher = fixture
    module.driver_config.db_defaults.parallel_workers.value = 4
    args = _database_create_arguments(
        {'host': 'owned'}, 'owned:/owned/another.fdb', {}, native)
    assert module.driver_config.get_database(
        args['database']).parallel_workers.value is None


@pytest.mark.parametrize('overrides', [
    {}, {'user': '', 'password': '', 'charset': '', 'role': ''},
    {'user': 'different', 'password': 'leased', 'charset': 'win1252',
     'role': 'different', 'no_gc': True, 'no_db_triggers': True,
     'dbkey_scope': native.DBKeyScope.ATTACHMENT,
     'session_time_zone': 'Europe/Prague', 'auth_plugin_list': 'Srp256'},
])
def test_existing_driver_creation_dpb_parity_except_worker_fix(
        fixture, monkeypatch, overrides):
    module, core, config, args, _dispatcher = fixture
    config.user.value = 'configured-user'
    config.password.value = 'configured-password'
    config.role.value = 'configured-role'
    config.charset.value = 'utf8'
    config.auth_plugin_list.value = 'Srp'
    config.session_time_zone.value = 'UTC'
    config.parallel_workers.value = 4
    config.cache_size.value = 128
    config.db_cache_size.value = 64
    config.timeout.value = 19
    config.dummy_packet_interval.value = 27
    config.config.value = 'WireCrypt=Required\nWireCompression=true'
    config.decfloat_round.value = native.DecfloatRound.DOWN
    config.decfloat_traps.value = [native.DecfloatTraps.INEXACT]
    config.set_bind.value = 'INT128 TO BIGINT'
    config.sweep_interval.value = 50000
    config.page_size.value = 16384
    config.forced_writes.value = False
    config.reserve_space.value = False
    # Compare the installed helper's actual constructor arguments, not a
    # second hand-written list of presumed supported driver options.
    monkeypatch.setattr(native.core, 'driver_config', module.driver_config)
    dpb = Mock()
    monkeypatch.setattr(native.core, 'DPB', dpb)
    monkeypatch.setattr(native.core, '__make_connection', Mock())
    arguments = {'database': args['database'], **overrides}
    native.create_database(**arguments)
    create_database(module, core, **arguments)
    expected = dpb.call_args.kwargs
    observed = core.DPB.call_args.kwargs
    assert observed.pop('parallel_workers') == 4
    assert observed == expected
