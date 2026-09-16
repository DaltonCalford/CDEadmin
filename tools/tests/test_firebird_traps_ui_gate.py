"""Trap browser evidence must reflect native state and fail closed."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from tools import cdeadmin_firebird_traps_ui_gate as gate


@pytest.mark.parametrize('policy', gate.LABELS)
def test_fill_is_dialog_scoped_and_only_sets_active_checkboxes(
        monkeypatch, policy):
    fill = Mock()
    monkeypatch.setattr(gate.shared, 'fill_fields', fill)
    wait = Mock()
    gate.fill(wait, policy, gate.TARGET_TRAPS)
    assert fill.call_count == (2 if policy == 'CUSTOM' else 1)
    for call in fill.call_args_list:
        assert call.kwargs['control_root'] is gate.cache.active_form
    if policy == 'CUSTOM':
        assert fill.call_args_list[1].args[1] == [
            'Trap division by zero=false', 'Trap inexact results=true',
            'Trap invalid operations=false', 'Trap overflow=false',
            'Trap underflow=true']


def test_capture_waits_for_reopened_form_loading(monkeypatch, tmp_path):
    scope = Mock()
    monkeypatch.setattr(gate.cache, 'active_form', Mock(return_value=scope))
    label_calls = []

    def control(_scope, label):
        if label != gate.LABEL:
            return None
        label_calls.append(label)
        return (None if len(label_calls) == 1 else
                SimpleNamespace(text='Native default'))

    def until(callback):
        assert callback(None) is None  # Initial spinner: no field yet.
        return callback(None)

    monkeypatch.setattr(gate.shared, 'visible_named_control', control)
    monkeypatch.setattr(gate.cache.linger, 'capture', Mock(return_value={}))
    proof = gate.capture(Mock(), SimpleNamespace(until=until),
                         tmp_path, 'NATIVE_DEFAULT')
    assert proof['selections'] == {}
    assert len(label_calls) == 2


@pytest.mark.parametrize('policy', gate.LABELS)
@pytest.mark.parametrize('defect', [None, 'policy', 'checkbox'])
def test_capture_checks_actual_controls_before_recording_proof(
        monkeypatch, tmp_path, policy, defect):
    scope = Mock()
    monkeypatch.setattr(gate.cache, 'active_form', Mock(return_value=scope))

    def control(_scope, label):
        assert _scope is scope
        if label == gate.LABEL:
            return SimpleNamespace(text=(
                'Other' if defect == 'policy' else gate.LABELS[policy]))
        if policy != 'CUSTOM':
            return Mock() if defect == 'checkbox' else None
        checked = label in ('Trap inexact results', 'Trap underflow')
        return SimpleNamespace(is_selected=lambda: (
            not checked if defect == 'checkbox' else checked))

    monkeypatch.setattr(gate.shared, 'visible_named_control', control)
    capture = Mock(return_value={})
    monkeypatch.setattr(gate.cache.linger, 'capture', capture)
    wait = SimpleNamespace(until=lambda callback: callback(None))
    if defect:
        with pytest.raises(RuntimeError, match='differs|visible'):
            gate.capture(Mock(), wait, tmp_path, policy, gate.TARGET_TRAPS)
    else:
        result = gate.capture(Mock(), wait, tmp_path, policy,
                              gate.TARGET_TRAPS)
        assert len(result['selections']) == (5 if policy == 'CUSTOM' else 0)
        assert capture.call_count == (6 if policy == 'CUSTOM' else 1)


@pytest.mark.parametrize('expected', [None, gate.TARGET_TRAPS])
@pytest.mark.parametrize('defect', [None, 'route', 'target', 'dpb',
                                    'initial', 'disabled', 'reset'])
def test_native_observer_refuses_wrong_route_dpb_or_session_state(
        monkeypatch, expected, defect):
    profile = {'host': '127.0.0.1', 'port': 54321,
               'database': '/var/lib/firebird/data/owned_cache.fdb'}
    options = SimpleNamespace(host='127.0.0.1', firebird_port=54321,
                              database_forms={'connect': {'fields': []}})
    route = dict(profile)
    if defect == 'route':
        route['host'] = 'remote.invalid'
    monkeypatch.setattr(gate.lifecycle, '_saved_route', Mock(return_value=[
        SimpleNamespace(id='owned-route', priority=0,
                        configuration=json.dumps(route))]))
    monkeypatch.setattr(gate.lifecycle, '_route_arguments',
                        Mock(return_value={'database': 'owned-config'}))
    module = MagicMock()
    module.driver_config.get_database.return_value.decfloat_traps.value = (
        [SimpleNamespace(name='OTHER')] if defect == 'dpb' else
        None if expected is None else [SimpleNamespace(name=name)
                                       for name in expected])
    handle = MagicMock()
    module.connect.return_value.__enter__.return_value = handle
    native = sorted(gate.DEFAULT_TRAPS if expected is None else expected)
    values = [native, [], native]
    for index, name in enumerate(('initial', 'disabled', 'reset')):
        if defect == name:
            values[index] = ['OTHER']
    monkeypatch.setattr(gate, 'observe_traps', Mock(side_effect=values))
    selected = {'target_id': 'owned-target', 'configuration': {},
                'database': 'wrong.fdb' if defect == 'target' else
                profile['database']}
    if defect:
        with pytest.raises((RuntimeError, ValueError, AssertionError)):
            gate.observe(options, module, 'secret', selected,
                         profile, expected)
    else:
        result = gate.observe(options, module, 'secret', selected,
                              profile, expected)
        assert result['initial'] == result['reset'] == native
        assert result['explicit_empty_sql'] == []
        assert handle.rollback.call_count == 3
    if defect in ('route', 'target', 'dpb'):
        module.connect.assert_not_called()
    else:
        module.connect.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize('fail_case', [False, True])
@pytest.mark.parametrize('fail_cleanup', [False, True])
def test_failure_collection_cleanup_and_redaction(
        monkeypatch, tmp_path, fail_case, fail_cleanup):
    options = SimpleNamespace(
        password_env='OWNED_TRAP_TEST_PASSWORD', timeout=1, font_scale=100,
        url='http://owned.invalid', config_db=tmp_path / 'isolated.db',
        output_root=tmp_path, database='owned_cache.fdb')
    monkeypatch.setenv(options.password_env, 'owned-secret-canary')
    monkeypatch.setattr(gate.cache, 'owned_profile', Mock(return_value={}))
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
    monkeypatch.setattr(gate, 'WebDriverWait', lambda *_: SimpleNamespace(
        until=lambda callback: callback(driver)))
    state = {'mode': 'edit', 'edit': {}, 'server_edit': {}}

    def open_form(_driver, _wait, mode, *_args):
        state['mode'] = mode

    def fill(_wait, policy, selected):
        state[state['mode']] = {'decfloat_traps_policy': policy}

    monkeypatch.setattr(gate, 'fill', fill)
    monkeypatch.setattr(gate.shared, '_open_form', open_form)
    monkeypatch.setattr(gate.shared, 'visible_named_control', Mock())
    for name in ('_prepare_tree', '_catalog_forms',
                 '_submit_target_form', '_close'):
        monkeypatch.setattr(gate.shared, name, Mock())
    monkeypatch.setattr(gate.shared, '_target_rows', lambda _: [{
        'display_name': options.database, 'target_id': 'owned-target',
        'configuration': state['edit']}])
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
