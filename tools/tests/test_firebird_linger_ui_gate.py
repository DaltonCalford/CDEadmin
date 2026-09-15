"""Ownership, native evidence, and full case collection for linger UI QA."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import pytest

from tools import cdeadmin_firebird_linger_ui_gate as gate


@pytest.fixture
def owned(tmp_path):
    profile = {'engine': 'firebird', 'host': '127.0.0.1', 'port': 54321,
               'user': 'SYSDBA', 'owned_container_id': 'a' * 64,
               'fixture_kind': 'firebird-no-linger-qualification',
               'database': '/var/lib/firebird/data/owned_repair_linger_13.fdb'}
    options = SimpleNamespace(
        profiles=tmp_path / 'owned.json', host='127.0.0.1',
        firebird_port=54321, user='SYSDBA',
        database='owned_repair_linger_13.fdb',
        database_root='/var/lib/firebird/data', output_root=tmp_path,
        password_env='OWNED_LINGER_PASSWORD', timeout=1, font_scale=100,
        url='http://owned.invalid')
    options.profiles.write_text(json.dumps({'profiles': [profile]}))
    return options, profile


@pytest.mark.parametrize('key,value', [
    ('engine', 'other'), ('host', 'remote.invalid'), ('port', 3050),
    ('user', 'other'), ('fixture_kind', 'unknown'),
    ('database', '/var/lib/firebird/data/user-demo.fdb'), ('database', None),
])
def test_unowned_profile_rejected_before_docker_or_native_calls(
        monkeypatch, owned, key, value):
    options, profile = owned
    options.profiles.write_text(json.dumps({
        'profiles': [{**profile, key: value}]}))
    observer = Mock()
    monkeypatch.setattr(gate, 'owned_database_open_files', observer)
    with pytest.raises(ValueError, match='owned linger fixture'):
        gate.owned_profile(options)
    observer.assert_not_called()


def test_owned_profile_checks_exact_container_and_database(monkeypatch, owned):
    options, profile = owned
    observer = Mock(return_value=0)
    monkeypatch.setattr(gate, 'owned_database_open_files', observer)
    assert gate.owned_profile(options) == profile
    observer.assert_called_once_with('a' * 64, profile['database'])


@pytest.mark.parametrize('count', [0, 2])
def test_profile_count_is_unambiguous(monkeypatch, owned, count):
    options, profile = owned
    options.profiles.write_text(json.dumps({'profiles': [profile] * count}))
    observer = Mock()
    monkeypatch.setattr(gate, 'owned_database_open_files', observer)
    with pytest.raises(ValueError, match='one owned linger profile'):
        gate.owned_profile(options)
    observer.assert_not_called()


@pytest.mark.parametrize('one_failure', [False, True])
@pytest.mark.parametrize('quit_failure', [False, True])
def test_all_six_parent_cases_run_and_cleanup_failure_blocks_completion(
        monkeypatch, owned, one_failure, quit_failure):
    options, profile = owned
    monkeypatch.setenv(options.password_env, 'owned-credential-canary')
    monkeypatch.setattr(gate, 'owned_profile', Mock(return_value=profile))
    monkeypatch.setattr(gate.lifecycle, '_configure_shared', Mock())
    monkeypatch.setattr(gate.lifecycle, '_load_firebird', Mock())
    driver = Mock()
    if quit_failure:
        driver.quit.side_effect = RuntimeError('owned-credential-canary')
    monkeypatch.setattr(gate.lifecycle, 'create_driver',
                        Mock(return_value=driver))
    monkeypatch.setattr(gate, 'WebDriverWait', Mock(return_value=Mock()))
    for name in ('_prepare_tree', '_catalog_forms', '_open_form',
                 'fill_fields', '_submit_target_form', '_close'):
        monkeypatch.setattr(gate.shared, name, Mock())
    monkeypatch.setattr(gate, 'capture', Mock(return_value={}))
    outcomes = [{'passed': True} for _ in range(6)]
    if one_failure:
        outcomes[0] = RuntimeError('owned-credential-canary')
    parent = Mock(side_effect=outcomes)
    monkeypatch.setattr(gate, 'parent_case', parent)
    result = gate.run(options)
    assert parent.call_count == 6
    assert len(result['target_setups']) == 3
    assert len(result['cases']) == (5 if one_failure else 6)
    assert result['complete'] is not (one_failure or quit_failure)
    assert len(result['failures']) == int(one_failure) + int(quit_failure)
    assert 'owned-credential-canary' not in json.dumps(result)
    driver.quit.assert_called_once_with()


@pytest.mark.parametrize('policy,count', [
    ('NATIVE_DEFAULT', 1), ('SUPPRESS', 0),
])
def test_oracle_uses_real_route_composition_and_native_lifetime(
        monkeypatch, owned, policy, count):
    options, profile = owned
    options.database_forms = {'connect': {'fields': [{
        'field_id': 'no_linger', 'inherit_server_value': 'SERVER_DEFAULT'}]}}
    saved_route = {key: profile[key]
                   for key in ('host', 'port', 'user', 'database')}
    saved_route['no_linger'] = policy
    monkeypatch.setattr(gate.lifecycle, '_saved_route', Mock(return_value=[
        SimpleNamespace(id='owned-route', priority=0,
                        configuration=json.dumps(saved_route))]))
    args = Mock(return_value={'database': 'owned-native-config'})
    monkeypatch.setattr(gate.lifecycle, '_route_arguments', args)
    monkeypatch.setattr(gate.lifecycle, '_initialize_connection', Mock())
    observer = Mock(side_effect=[0, count])
    monkeypatch.setattr(gate, 'owned_database_open_files', observer)
    module = Mock()
    module.driver_config.get_database.return_value.no_linger.value = (
        True if policy == 'SUPPRESS' else None)
    handle = MagicMock()
    handle.__enter__.return_value = handle
    handle.cursor.return_value.__enter__.return_value.fetchone.return_value = (
        600,)
    module.connect.return_value = handle
    selected = {'target_id': 'owned-target', 'database': profile['database'],
                'configuration': {'no_linger': 'SERVER_DEFAULT'}}
    result = gate.observe_saved_policy(options, module, 'owned-secret',
                                       selected, profile, policy)
    assert result['open_files_after_detach'] == count
    assert result['stored_linger'] == 600
    assert args.call_args.args[0]['no_linger'] == policy
    assert selected['configuration']['no_linger'] == 'SERVER_DEFAULT'
    assert module.connect.call_count == 2  # Owned reset, then actual probe.
    handle.rollback.assert_called_once_with()


def test_unresolved_policy_never_reaches_native_connection(owned):
    options, profile = owned
    module = Mock()
    with pytest.raises(ValueError, match='Unresolved linger policy'):
        gate.observe_saved_policy(options, module, 'owned-secret', {}, profile,
                                  'SERVER_DEFAULT')
    module.connect.assert_not_called()


def test_other_database_never_reaches_native_connection(owned):
    options, profile = owned
    module = Mock()
    with pytest.raises(ValueError, match='Saved database differs'):
        gate.observe_saved_policy(options, module, 'owned-secret',
                                  {'database': '/unrelated/user.fdb'}, profile,
                                  'NATIVE_DEFAULT')
    module.connect.assert_not_called()
