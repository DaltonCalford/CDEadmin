"""Saved worker evidence distinguishes native caps, zero and RESET state."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from tools import cdeadmin_firebird_parallel_ui_gate as gate


@pytest.mark.parametrize('expected', [None, 0, 2, 32767])
@pytest.mark.parametrize('defect', [None, 'dpb', 'route', 'initial', 'reset'])
def test_observer_checks_owned_route_and_each_native_stage(
        monkeypatch, expected, defect):
    profile = {'host': '127.0.0.1', 'port': 54321, 'user': 'SYSDBA',
               'database': '/var/lib/firebird/data/owned_cache.fdb'}
    options = SimpleNamespace(host=profile['host'], firebird_port=54321,
                              database_forms={'connect': {'fields': []}})
    route = dict(profile)
    if defect == 'route':
        route['host'] = 'wrong.invalid'
    monkeypatch.setattr(gate.lifecycle, '_saved_route', Mock(return_value=[
        SimpleNamespace(id='owned-route', priority=0,
                        configuration=json.dumps(route))]))
    monkeypatch.setattr(gate.lifecycle, '_route_arguments', Mock(
        return_value={'database': 'owned-config'}))
    module = Mock()
    module.driver_config.get_database.return_value.parallel_workers.value = (
        9 if defect == 'dpb' else expected)
    handle = MagicMock()
    handle.__enter__.return_value = handle
    cursor = handle.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [('ParallelWorkers', '1'),
                                    ('MaxParallelWorkers', '4')]
    effective = 1 if expected is None else min(expected, 4)
    cursor.fetchone.side_effect = [
        (99 if defect == 'initial' else effective,), (effective,),
        (99 if defect == 'reset' else effective,)]
    module.connect.return_value = handle
    selected = {'target_id': 'owned', 'database': profile['database'],
                'configuration': {}}
    if defect:
        with pytest.raises((ValueError, RuntimeError)):
            gate.observe(options, module, 'secret',
                         selected, profile, expected)
        if defect in ('route', 'dpb'):
            module.connect.assert_not_called()
    else:
        result = gate.observe(options, module, 'secret', selected, profile,
                              expected)
        assert result['effective_workers'] == [effective] * 3
        assert result['requested_workers'] == expected
        assert result['native_dpb_verified']
        assert result['actual_task_worker_counts_qualified'] is False
        handle.execute_immediate.assert_called_once_with('ALTER SESSION RESET')
        assert handle.rollback.call_count == 3


@pytest.mark.parametrize('policy', gate.LABELS)
def test_only_active_count_is_filled_in_visible_dialog(monkeypatch, policy):
    fill = Mock()
    monkeypatch.setattr(gate.shared, 'fill_fields', fill)
    gate.fill(Mock(), policy, 0)
    assert fill.call_count == (2 if policy == 'CUSTOM' else 1)
    for call in fill.call_args_list:
        assert call.kwargs['control_root'] is gate.cache.active_form
    if policy == 'CUSTOM':
        assert fill.call_args_list[-1].args[1] == [gate.COUNT + '=0']


def test_capture_waits_for_form_and_includes_visible_zero(
        monkeypatch, tmp_path):
    scope = Mock()
    monkeypatch.setattr(gate.cache, 'active_form', Mock(return_value=scope))
    monkeypatch.setattr(gate.shared, 'visible_named_control', Mock(
        return_value=SimpleNamespace(text='0')))
    capture = Mock(side_effect=lambda *_args, **_kwargs: {'visible': True})
    monkeypatch.setattr(gate.cache.linger, 'capture', capture)
    wait = Mock()
    wait.until.side_effect = lambda callback: callback(None)
    result = gate.capture(Mock(), wait, tmp_path)
    wait.until.assert_called_once()
    assert result['requested_count'] == {'visible': True}
    assert capture.call_count == 2
    assert all(call.kwargs['scope']
               is scope for call in capture.call_args_list)
