"""The live gate must not hide stale confirmation by retyping the draft."""

from types import SimpleNamespace

import pytest

from tools import cdeadmin_firebird_rename_ui_gate as gate


@pytest.mark.parametrize('case', [
    'valid', 'leaked-confirmation', 'missing-policy', 'changed-command',
])
def test_repreview_gate_checks_policy_and_unmodified_draft(monkeypatch, case):
    state = {'checked': False, 'clicks': 0, 'previews': 0}
    browser = object()
    checkbox = SimpleNamespace(is_selected=lambda: state['checked'])
    button = SimpleNamespace(is_enabled=lambda: state['checked'])
    wait = SimpleNamespace(until=lambda callback: callback(browser))
    first = {'command_preview': {'statements': [{'source': 'DROP TABLE "T"'}]}}
    operation = {'confirmation_required': case != 'missing-policy'}

    def control(_browser, label):
        assert _browser is browser
        return button if label == 'Apply provider plan' else checkbox

    def click(_browser, _wait, item):
        assert item is checkbox
        state['clicks'] += 1
        state['checked'] = True

    def preview(_browser, _wait, observed_operation, values):
        assert observed_operation is operation
        assert values == {}, 'Retyping fields conceals stale confirmation'
        state['previews'] += 1
        state['checked'] = case == 'leaked-confirmation'
        return ({'command_preview': {'statements': []}}
                if case == 'changed-command' else first)

    monkeypatch.setattr(gate, 'visible_named_control', control)
    monkeypatch.setattr(gate, 'click_unobscured', click)
    monkeypatch.setattr(gate, 'plan_preview', preview)
    if case == 'valid':
        observed = gate.repreview_confirmation(browser, wait, operation, first)
        assert observed == (first, checkbox)
        assert not checkbox.is_selected()
        assert not button.is_enabled()
    else:
        with pytest.raises(AssertionError):
            gate.repreview_confirmation(browser, wait, operation, first)
    expected = 0 if case == 'missing-policy' else 1
    assert state['previews'] == state['clicks'] == expected
