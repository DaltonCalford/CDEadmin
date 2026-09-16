"""Owned creation-cache baseline: isolation, boundaries and full inventory."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from firebird.driver.config import DriverConfig

from tools import cdeadmin_firebird_creation_cache_gate as gate


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_creation_gate_collects_modes_and_redacts_failures(
        monkeypatch, cleanup_fails):
    secret = 'owned-creation-cache-secret-canary'
    monkeypatch.setattr(gate.secrets, 'token_urlsafe', lambda _size: secret)
    monkeypatch.setattr(gate, '_configure_client_library',
                        lambda _module: None)
    start = Mock(return_value=('a' * 64).encode())
    monkeypatch.setattr(gate, 'docker', start)
    monkeypatch.setattr(gate, 'published_port', Mock(
        side_effect=RuntimeError(secret)))
    cleanup = Mock(side_effect=RuntimeError(secret) if cleanup_fails else None)
    monkeypatch.setattr(gate, 'remove_owned', cleanup)
    result = gate.run('owned-test-image')
    assert not result['complete']
    assert not result['provider_forms_qualified']
    assert result['driver_defaults_unchanged']
    assert len(result['failures']) == (6 if cleanup_fails else 3)
    assert cleanup.call_count == 3
    assert secret not in json.dumps(result)
    for call, mode in zip(start.call_args_list, gate.SERVER_MODES):
        args = call.args
        assert args[args.index('--publish') + 1] == '127.0.0.1::3050'
        assert args[args.index('--memory') + 1] == '512m'
        assert args[args.index('--memory-swap') + 1] == '512m'
        assert args[args.index('--label') + 1] == (
            'cdeadmin-owned-gate=' + gate.OWNER)
        assert call.kwargs['env']['FIREBIRD_CONF_ServerMode'] == mode
        assert call.kwargs['env']['FIREBIRD_CONF_DefaultDbCachePages'] == '128'
        assert call.kwargs['env']['FIREBIRD_ROOT_PASSWORD'] == secret
        assert secret not in args


@pytest.mark.parametrize('stored', [None, 0, 64])
@pytest.mark.parametrize('requested', [None, 0, 128])
def test_private_creation_configuration_preserves_defaults(requested, stored):
    config = DriverConfig('owned-cache-test')
    config.db_defaults.database.value = '/unrelated.fdb'
    config.server_defaults.host.value = 'unrelated-host'
    config.db_defaults.password.value = 'unrelated-secret-canary'
    before = config.get_config()
    name = gate.private_configuration(SimpleNamespace(driver_config=config),
                                      '127.0.0.1/50000:/owned.fdb',
                                      requested, stored)
    database = config.get_database(name)
    server = config.get_server(database.server.value)
    assert database.dsn.value == '127.0.0.1/50000:/owned.fdb'
    assert database.database.value is None
    assert database.cache_size.value == requested
    assert database.db_cache_size.value == stored
    assert database.user.value is database.password.value is None
    assert server.host.value is server.port.value is None
    assert server.user.value is server.password.value is None
    assert database.sql_dialect.value == database.db_sql_dialect.value == 3
    # Remove only the two records created in this isolated config instance.
    config.databases.value.remove(database)
    config.servers.value.remove(server)
    assert config.get_config() == before


@pytest.mark.parametrize('container,path', [
    ('user-demo', '/var/lib/firebird/data/owned_creation_cache_' + 'a' * 32 +
     '.fdb'),
    ('a' * 64, '/var/lib/firebird/data/user.fdb'),
    ('a' * 64, '/var/lib/firebird/data/../owned.fdb'),
    ('a' * 64, '/'),
])
def test_path_observation_refuses_non_owned_targets(monkeypatch, container,
                                                    path):
    call = Mock()
    monkeypatch.setattr(gate, 'docker', call)
    with pytest.raises(ValueError):
        gate.file_present(container, path)
    call.assert_not_called()


@pytest.mark.parametrize('observation', ['absent', 'present', 'unexpected'])
def test_path_observation_is_exact(monkeypatch, observation):
    path = '/var/lib/firebird/data/owned_creation_cache_' + 'a' * 32 + '.fdb'
    output = {'absent': '', 'present': path, 'unexpected': '/other.fdb'}
    monkeypatch.setattr(gate, 'docker', Mock(
        return_value=output[observation].encode()))
    if observation == 'unexpected':
        with pytest.raises(ValueError):
            gate.file_present('b' * 64, path)
    else:
        assert gate.file_present('b' * 64, path) == (observation == 'present')


@pytest.mark.parametrize('provider,stored_boundaries', [
    (False, False), (True, False), (False, True), (True, True),
])
@pytest.mark.parametrize('fail_case', [False, True])
def test_every_creation_case_runs_and_all_servers_are_cleaned(
        monkeypatch, fail_case, provider, stored_boundaries):
    import firebird.driver as native
    monkeypatch.setattr(native, 'driver_config', DriverConfig('owned-test'))
    handle = MagicMock()
    handle.__enter__.return_value = handle
    handle.cursor.return_value.__enter__.return_value.fetchone.return_value = (
        '5.0.4',)
    monkeypatch.setattr(native, 'connect', Mock(return_value=handle))
    monkeypatch.setattr(gate, '_configure_client_library',
                        lambda _module: None)
    monkeypatch.setattr(gate, 'docker', Mock(return_value=('a' * 64).encode()))
    monkeypatch.setattr(gate, 'published_port', Mock(return_value=50000))
    cleanup = Mock()
    monkeypatch.setattr(gate, 'remove_owned', cleanup)
    seen = []

    def check(_native, _container, _port, _password, mode, requested, stored,
              **options):
        assert options == ({'provider': True} if provider else {})
        seen.append((mode, requested, stored))
        if fail_case and len(seen) == 1:
            raise RuntimeError('secret-canary-not-for-evidence')
        return {'mode': mode, 'requested': requested, 'stored_request': stored}

    monkeypatch.setattr(gate, 'creation_case', check)
    result = gate.run('owned-test-image', provider=provider,
                      stored_boundaries=stored_boundaries)
    total = 24 if stored_boundaries else 81
    assert len(seen) == total
    assert len(set(seen)) == total
    assert len(result['checks']) == total - int(fail_case)
    assert len(result['failures']) == int(fail_case)
    assert result['complete'] is not fail_case
    assert result['provider_mapping_qualified'] is (provider and not fail_case)
    assert result['removed_server_modes'] == list(gate.SERVER_MODES)
    assert result['driver_defaults_unchanged']
    assert cleanup.call_count == 3
    assert 'secret-canary' not in json.dumps(result)


@pytest.mark.parametrize('mode', gate.SERVER_MODES)
@pytest.mark.parametrize('requested', [0, 1, 24])
def test_provider_creation_rejects_invalid_count_before_native_call(
        monkeypatch, mode, requested):
    native, created, reopened = fake_native()
    presence = Mock(return_value=False)
    monkeypatch.setattr(gate, 'file_present', presence)
    result = gate.creation_case(native, 'a' * 64, 50000, 'secret', mode,
                                requested, None, provider=True)
    assert result['rejected_before_native_create']
    assert result['failed_creation_path_absent']
    assert presence.call_count == 2
    native.create_database.assert_not_called()
    native.connect.assert_not_called()
    created.close.assert_not_called()
    reopened.close.assert_not_called()


@pytest.mark.parametrize('stored', [-1, 1, 49, 2147483647])
def test_provider_stored_invalid_refused_before_native(monkeypatch, stored):
    native, _created, _reopened = fake_native()
    monkeypatch.setattr(gate, 'file_present', Mock(return_value=False))
    result = gate.creation_case(native, 'a' * 64, 50000, 'secret', 'Super',
                                128, stored, provider=True)
    assert result['rejected_before_native_create']
    assert result['failed_creation_path_absent']
    native.create_database.assert_not_called()
    native.connect.assert_not_called()


@pytest.mark.parametrize('mode', gate.SERVER_MODES)
@pytest.mark.parametrize('stored', [1, 49, 2147483647])
@pytest.mark.parametrize('wrong_code', [False, True])
def test_stored_boundaries_require_the_exact_native_range_error(
        monkeypatch, mode, stored, wrong_code):
    native, _created, _reopened = fake_native()
    native.create_database.side_effect = native.DatabaseError('owned-secret')
    monkeypatch.setattr(gate, 'status_codes', lambda _error: (
        335545087 if wrong_code else 335545086,))
    monkeypatch.setattr(gate, 'file_present', Mock(return_value=False))
    if wrong_code:
        with pytest.raises(AssertionError):
            gate.creation_case(native, 'a' * 64, 50000, 'secret', mode,
                               None, stored)
    else:
        result = gate.creation_case(native, 'a' * 64, 50000, 'secret', mode,
                                    None, stored)
        assert result['rejected']
        assert result['failed_creation_path_absent']
        assert result['native_status_codes'] == [335545086]
    native.connect.assert_not_called()


def test_stored_matrix_never_requests_dangerous_accepted_allocations():
    assert gate.STORED_BOUNDARIES == (
        None, 0, -1, 1, 49, 50, 64, 2147483647)
    assert all(value is None or value <= 64 or value == 2147483647
               for value in gate.STORED_BOUNDARIES)


@pytest.mark.parametrize('mode', gate.SERVER_MODES)
def test_negative_stored_value_is_driver_rejection_not_native_error(
        monkeypatch, mode):
    native, _created, _reopened = fake_native()
    monkeypatch.setattr(gate, 'file_present', Mock(return_value=False))
    result = gate.creation_case(native, 'a' * 64, 50000, 'secret', mode,
                                None, -1)
    assert result['rejected_before_native_create_by_driver']
    assert result['failed_creation_path_absent']
    assert 'native_status_codes' not in result
    native.create_database.assert_not_called()
    native.connect.assert_not_called()


def test_native_matrix_stays_small_and_covers_omission_zero_and_precedence():
    assert gate.STORED_REQUESTS == (None, 0, 64)
    assert gate.CACHE_REQUESTS == (None, 0, 1, 24, 25, 49, 50, 128, 256)
    assert gate.SERVER_MODES == ('Super', 'SuperClassic', 'Classic')
    assert max(value for value in gate.CACHE_REQUESTS
               if value is not None) == 256


def fake_native():
    class NativeError(Exception):
        pass

    created = MagicMock()
    reopened = MagicMock()
    for handle in (created, reopened):
        handle.info.page_cache_size = 128
        handle.info.get_info.return_value = 0
        cursor = handle.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = lambda h=handle: (
            h.info.page_cache_size,)
    cursor = reopened.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = (
        [(7, 'creation-cache-seven'), (11, 'creation-cache-eleven')])
    native = SimpleNamespace(
        driver_config=DriverConfig('owned-test'), DatabaseError=NativeError,
        DbInfoCode=SimpleNamespace(SET_PAGE_BUFFERS='stored'),
        create_database=Mock(return_value=created),
        connect=Mock(return_value=reopened))
    return native, created, reopened


@pytest.mark.parametrize('mode,requested,stored,allocated', [
    ('Super', 0, None, 128), ('Super', 256, 64, 64),
    ('SuperClassic', 25, 0, 50), ('Classic', 256, 0, 256),
    ('Classic', None, 64, 64), ('SuperClassic', 128, None, 128),
])
def test_creation_case_observes_both_settings_and_committed_rows(
        monkeypatch, mode, requested, stored, allocated):
    native, created, reopened = fake_native()
    created.info.page_cache_size = allocated
    created.info.get_info.return_value = stored or 0
    reopened.info.page_cache_size = stored or 128
    reopened.info.get_info.return_value = stored or 0
    presence = Mock(return_value=False)
    monkeypatch.setattr(gate, 'file_present', presence)
    result = gate.creation_case(native, 'a' * 64, 50000, 'secret', mode,
                                requested, stored)
    assert not result['rejected']
    assert result['committed_rows_preserved']
    assert result['owned_database_removed']
    assert presence.call_count == 2
    created.close.assert_called_once()
    reopened.drop_database.assert_called_once()
    reopened.close.assert_not_called()
    assert native.create_database.call_args.kwargs['overwrite'] is False
    create_name = native.create_database.call_args.args[0]
    reopen_name = native.connect.call_args.args[0]
    assert create_name != reopen_name
    assert native.driver_config.get_database(create_name).cache_size.value == (
        requested)
    assert native.driver_config.get_database(reopen_name).cache_size.value is (
        None)


@pytest.mark.parametrize('phase', ['initial', 'monitor', 'reopened', 'rows',
                                   'drop'])
def test_creation_failure_closes_only_the_current_owned_handle(monkeypatch,
                                                               phase):
    native, created, reopened = fake_native()
    monkeypatch.setattr(gate, 'file_present', Mock(return_value=False))
    if phase == 'initial':
        created.info.page_cache_size = 999
    elif phase == 'monitor':
        cursor = created.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = lambda: (999,)
    elif phase == 'reopened':
        reopened.info.get_info.return_value = 999
    elif phase == 'rows':
        cursor = reopened.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
    else:
        reopened.drop_database.side_effect = RuntimeError('owned-drop-failed')
    with pytest.raises((AssertionError, RuntimeError)):
        gate.creation_case(native, 'a' * 64, 50000, 'secret', 'Super',
                           None, None)
    created.close.assert_called_once()
    if phase in ('initial', 'monitor'):
        native.connect.assert_not_called()
        reopened.close.assert_not_called()
    else:
        reopened.close.assert_called_once()


@pytest.mark.parametrize('residual', [False, True])
def test_rejected_creation_checks_residual_path_without_reopening(
        monkeypatch, residual):
    native, _created, _reopened = fake_native()
    native.create_database.side_effect = native.DatabaseError('secret')
    monkeypatch.setattr(gate, 'status_codes', lambda _error: (335545087,))
    monkeypatch.setattr(gate, 'file_present', Mock(
        side_effect=[False, residual]))
    if residual:
        with pytest.raises(AssertionError):
            gate.creation_case(native, 'a' * 64, 50000, 'secret', 'Classic',
                               24, 64)
    else:
        result = gate.creation_case(native, 'a' * 64, 50000, 'secret',
                                    'Classic', 24, 64)
        assert result['rejected']
        assert result['failed_creation_path_absent']
        assert result['native_status_codes'] == [335545087]
    native.connect.assert_not_called()
