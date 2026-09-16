"""Cache browser checks refuse user fixtures and observe saved routes."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from tools import cdeadmin_firebird_cache_ui_gate as gate


@pytest.fixture
def owned(tmp_path):
    profile = {'engine': 'firebird', 'host': '127.0.0.1', 'port': 54321,
               'user': 'SYSDBA', 'owned_container_id': 'a' * 64,
               'fixture_kind': 'firebird-cache-qualification',
               'database': '/var/lib/firebird/data/owned_cache.fdb'}
    options = SimpleNamespace(
        profiles=tmp_path / 'owned.json', host='127.0.0.1',
        firebird_port=54321, user='SYSDBA', database='owned_cache.fdb',
        database_root='/var/lib/firebird/data')
    options.profiles.write_text(json.dumps({'profiles': [profile]}))
    return options, profile


@pytest.mark.parametrize('key,value', [
    ('engine', 'other'), ('host', 'remote.invalid'), ('port', 3050),
    ('user', 'other'), ('fixture_kind', 'unknown'),
    ('database', '/var/lib/firebird/data/user-demo.fdb'), ('database', None),
    ('owned_container_id', 'user-demo'),
])
def test_wrong_profile_never_calls_docker(monkeypatch, owned, key, value):
    options, profile = owned
    options.profiles.write_text(json.dumps({
        'profiles': [{**profile, key: value}]}))
    docker = Mock()
    monkeypatch.setattr(gate, 'docker', docker)
    with pytest.raises(ValueError, match='owned cache fixture'):
        gate.owned_profile(options)
    docker.assert_not_called()


@pytest.mark.parametrize('owner', [gate.OWNER, 'unrelated'])
def test_owner_label_required(monkeypatch, owned, owner):
    options, profile = owned
    docker = Mock(return_value=owner.encode())
    monkeypatch.setattr(gate, 'docker', docker)
    if owner == gate.OWNER:
        assert gate.owned_profile(options) == profile
    else:
        with pytest.raises(ValueError, match='ownership'):
            gate.owned_profile(options)
    assert docker.call_args.args[-1] == 'a' * 64
    assert docker.call_args.args[0] == 'inspect'


@pytest.mark.parametrize('custom', [False, True])
def test_capture_includes_visible_page_count_without_inventing_hidden_field(
        monkeypatch, tmp_path, custom):
    driver, wait = Mock(), Mock()
    scope = Mock()
    driver.find_elements.return_value = [scope]
    lookup = Mock(return_value=Mock() if custom else None)
    monkeypatch.setattr(gate.shared, 'visible_named_control', lookup)
    capture = Mock(side_effect=[{'policy': True}, {'count': True}])
    monkeypatch.setattr(gate.linger, 'capture', capture)
    proof = gate.capture(driver, wait, tmp_path)
    assert capture.call_count == (2 if custom else 1)
    assert capture.call_args_list[0].kwargs == {
        'label': gate.LABEL, 'scope': scope}
    lookup.assert_called_once_with(scope, gate.PAGES)
    if custom:
        assert proof['page_count'] == {'count': True}
        assert capture.call_args_list[1].args[-1] == tmp_path / 'page-count'
        assert capture.call_args_list[1].kwargs == {
            'label': gate.PAGES, 'scope': scope}
    else:
        assert 'page_count' not in proof


@pytest.mark.parametrize('visible', [0, 2])
def test_capture_refuses_missing_or_ambiguous_active_forms(visible):
    driver = Mock()
    hidden = Mock()
    hidden.is_displayed.return_value = False
    driver.find_elements.return_value = [hidden] + [Mock() for _ in range(
        visible)]
    with pytest.raises(RuntimeError, match='one visible'):
        gate.active_form(driver)


@pytest.mark.parametrize('expected', [None, 128, 256, 512])
@pytest.mark.parametrize('defect', [None, 'dpb', 'stored', 'allocation',
                                    'monitor', 'route'])
def test_native_observer_distinguishes_every_cache_observation(
        monkeypatch, owned, expected, defect):
    options, profile = owned
    options.database_forms = {'connect': {'fields': []}}
    route = {key: profile[key] for key in ('host', 'port', 'user', 'database')}
    if defect == 'route':
        route['host'] = 'other.invalid'
    monkeypatch.setattr(gate.lifecycle, '_saved_route', Mock(return_value=[
        SimpleNamespace(id='owned-route', priority=0,
                        configuration=json.dumps(route))]))
    args = Mock(return_value={'database': 'owned-native-config'})
    monkeypatch.setattr(gate.lifecycle, '_route_arguments', args)
    module = Mock()
    module.driver_config.get_database.return_value.cache_size.value = (
        999 if defect == 'dpb' else expected)
    handle = MagicMock()
    handle.__enter__.return_value = handle
    handle.info.page_cache_size = (
        999 if defect == 'allocation' else expected or 128)
    handle.info.get_info.return_value = 64 if defect == 'stored' else 0
    handle.cursor.return_value.__enter__.return_value.fetchone.return_value = (
        999 if defect == 'monitor' else expected or 128,)
    module.connect.return_value = handle
    selected = {'target_id': 'owned-target', 'database': profile['database'],
                'configuration': {}}
    if defect:
        with pytest.raises((RuntimeError, ValueError)):
            gate.observe(options, module, 'secret',
                         selected, profile, expected)
    else:
        result = gate.observe(options, module, 'secret', selected,
                              profile, expected)
        assert result['stored_pages'] == 0
        assert result['allocated_pages'] == expected or (
            expected is None and result['allocated_pages'] == 128)
        assert result['native_dpb_verified']
    if defect in ('route', 'dpb'):
        module.connect.assert_not_called()
    else:
        handle.__exit__.assert_called_once()


@pytest.mark.parametrize('fail_case', [False, True])
@pytest.mark.parametrize('fail_cleanup', [False, True])
def test_all_browser_cases_collected_and_quit_failure_is_not_success(
        monkeypatch, owned, fail_case, fail_cleanup, tmp_path):
    options, profile = owned
    options.password_env = 'OWNED_CACHE_TEST_PASSWORD'
    options.timeout = 1
    options.font_scale = 100
    options.url = 'http://owned.invalid'
    options.config_db = tmp_path / 'isolated.db'
    options.output_root = tmp_path
    monkeypatch.setenv(options.password_env, 'owned-secret-canary')
    monkeypatch.setattr(gate, 'owned_profile', Mock(return_value=profile))
    monkeypatch.setattr(gate.lifecycle, '_configure_shared', Mock())
    monkeypatch.setattr(gate.lifecycle, '_load_firebird', Mock())
    driver = Mock()
    driver.save_screenshot.return_value = False
    driver.find_elements.return_value = [SimpleNamespace(
        is_displayed=lambda: True, text='Endpoint profile saved.')]
    if fail_cleanup:
        driver.quit.side_effect = RuntimeError('owned-secret-canary')
    monkeypatch.setattr(gate.lifecycle, 'create_driver',
                        Mock(return_value=driver))
    monkeypatch.setattr(gate, 'WebDriverWait', lambda *_args: SimpleNamespace(
        until=lambda callback: callback(driver)))
    state = {'mode': 'edit', 'edit': {}, 'server_edit': {}}

    def open_form(_driver, _wait, mode, *_args):
        state['mode'] = mode

    def fill(_wait, policy, pages):
        state[state['mode']] = {'attachment_cache_policy': policy,
                                'attachment_cache_pages': pages}

    def control(_driver, name):
        config = state[state['mode']]
        if name == gate.PAGES:
            return (SimpleNamespace(get_attribute=lambda _key: str(
                config['attachment_cache_pages']))
                if config['attachment_cache_policy'] == 'CUSTOM' else None)
        if name == gate.LABEL:
            return SimpleNamespace(text=gate.LABELS[
                config['attachment_cache_policy']])
        return SimpleNamespace(click=lambda: None)

    monkeypatch.setattr(gate, 'fill', fill)
    monkeypatch.setattr(gate.shared, '_open_form', open_form)
    monkeypatch.setattr(gate.shared, 'visible_named_control', control)
    for name in ('_prepare_tree', '_catalog_forms',
                 '_submit_target_form', '_close'):
        monkeypatch.setattr(gate.shared, name, Mock())
    monkeypatch.setattr(gate.shared, '_target_rows', lambda _path: [{
        'display_name': options.database, 'target_id': 'owned-target',
        'configuration': state['edit']}])
    monkeypatch.setattr(gate.lifecycle, '_saved_route', lambda *_args: [
        SimpleNamespace(configuration=json.dumps(state['server_edit']))])
    monkeypatch.setattr(gate, 'capture', Mock(return_value={}))
    outcomes = [{'native_dpb_verified': True} for _ in range(9)]
    if fail_case:
        outcomes[0] = RuntimeError('owned-secret-canary')
    observe = Mock(side_effect=outcomes)
    monkeypatch.setattr(gate, 'observe', observe)
    result = gate.run(options)
    assert observe.call_count == 9
    assert len(result['cases']) == 9 - int(fail_case)
    assert len(result['target_setups']) == 3
    assert len(result['failures']) == int(fail_case) + int(fail_cleanup)
    assert result['complete'] is not (fail_case or fail_cleanup)
    assert 'owned-secret-canary' not in json.dumps(result)
    driver.quit.assert_called_once()
