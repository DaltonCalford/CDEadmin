"""Targeted inheritance evidence must not masquerade as a full form gate."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import cdeadmin_firebird_database_lifecycle_ui_gate as gate


@pytest.mark.parametrize('scope', ['full', 'inheritance'])
@pytest.mark.parametrize('case_count', [8, 9])
def test_scope_keeps_the_full_gate_default_and_all_inheritance_requirements(
        monkeypatch, tmp_path, scope, case_count):
    monkeypatch.setenv('OWNED_ROUNDING_SECRET', 'fixture-secret')
    driver = Mock()
    monkeypatch.setattr(gate, 'create_driver', Mock(return_value=driver))
    monkeypatch.setattr(gate, '_configure_shared', Mock())
    monkeypatch.setattr(gate, '_load_firebird', Mock())
    monkeypatch.setattr(gate, '_install_menu_trace', Mock())
    monkeypatch.setattr(gate.shared, '_prepare_tree', Mock())
    monkeypatch.setattr(gate.shared, '_refresh_tree', Mock())
    monkeypatch.setattr(gate.shared, '_catalog_forms', Mock(return_value={}))

    def complete(_driver, _wait, _options, _module, _secret, cases, _cleanup):
        cases.extend({'mode': mode} for mode in gate.COMPLETION_ORDER)

    def inherit(_driver, _wait, _options, _module, _secret, result):
        result.update(cases=[{} for _ in range(case_count)],
                      target_setups=[{}, {}, {}])

    completed = Mock(side_effect=complete)
    rounding = Mock(side_effect=lambda *_args, **_kwargs: {'mode': _args[5]})
    rendered = Mock(side_effect=lambda *_args: {
        'mode': _args[4], 'cancellation': {'dialog_dismissed': True},
        'validation': {'state': 'observed'}})
    monkeypatch.setattr(gate, '_complete_cases', completed)
    monkeypatch.setattr(gate, '_rounding_case', rounding)
    monkeypatch.setattr(gate, '_inheritance_cases', inherit)
    monkeypatch.setattr(gate.shared, '_render_case', rendered)
    options = SimpleNamespace(
        scope=scope, password_env='OWNED_ROUNDING_SECRET', timeout=1,
        url='http://owned.invalid', database='owned.fdb', output_root=tmp_path,
        width=1600, height=1000, theme='high-contrast', font_scale=100)
    result = gate.run(options)
    assert result['complete'] is (case_count == 9)
    assert result['scope'] == scope
    assert len(result['completed']) == (7 if scope == 'full' else 0)
    assert len(result['rendered']) == (7 if scope == 'full' else 0)
    assert len(result['rounding']) == (9 if scope == 'full' else 0)
    assert result['schema'] == (
        'cdeadmin.firebird-database-lifecycle-ui-gate.v1' if scope == 'full'
        else 'cdeadmin.firebird-rounding-inheritance-ui-gate.v1')
    assert completed.call_count == (1 if scope == 'full' else 0)
    assert rendered.call_count == (7 if scope == 'full' else 0)
    driver.quit.assert_called_once_with()
