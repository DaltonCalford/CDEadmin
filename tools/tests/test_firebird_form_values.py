"""Browser form drivers preserve JSON types instead of Python repr text."""
import json
from unittest.mock import Mock

import pytest

from tools import cdeadmin_firebird_ui_form_gate as gate


@pytest.mark.parametrize('control,value', [
    ('multiselect', ['SEQUENCE', 'IN_PLACE']), ('multiselect', []),
    ('json', {'caption': '東京', 'enabled': True}), ('json', [1, None]),
    ('json', None), ('json', False),
])
def test_typed_values_are_encoded_as_json(monkeypatch, control, value):
    fill = Mock()
    monkeypatch.setattr(gate, 'fill_fields', fill)
    fields = [{'field_id': 'options', 'label': 'Options', 'control': control}]
    root = object()
    gate.fill_form_values(None, None, fields, {'Options': value}, root)
    args, kwargs = fill.call_args
    assert json.loads(args[1][0].split('=', 1)[1]) == value
    assert kwargs['control_root'] is root


@pytest.mark.parametrize('value', ['["SEQUENCE"]', 'invalid JSON', ''])
def test_explicit_text_is_retained_for_validation_tests(monkeypatch, value):
    fill = Mock()
    monkeypatch.setattr(gate, 'fill_fields', fill)
    fields = [{'field_id': 'options', 'label': 'Options', 'control': 'json'}]
    gate.fill_form_values(None, None, fields, {'Options': value})
    assert fill.call_args.args[1] == ['Options=' + value]


@pytest.mark.parametrize('record', [False, True])
@pytest.mark.parametrize('present', [False, True])
def test_inactive_field_absence_is_verified_not_silently_skipped(
        monkeypatch, record, present):
    driver = Mock()
    wait = Mock(_driver=driver)
    field = {'field_id': 'conditional', 'label': 'Conditional',
             'control': 'json' if record else 'text',
             'visible_when': {'field_id': 'enabled', 'equals': True}}
    if record:
        field['array_editor'] = {'item_kind': 'string'}
        driver.find_elements.return_value = [
            Mock(is_displayed=Mock(return_value=present))]
    else:
        monkeypatch.setattr(gate, 'visible_named_control', Mock(
            return_value=Mock() if present else None))
    if present:
        with pytest.raises(RuntimeError, match='inactive field'):
            gate.assert_form_controls(wait, [field])
    else:
        observed = gate.assert_form_controls(wait, [field])
        assert observed == [{'field_id': 'conditional', 'label': 'Conditional',
                             'control': field['control'], 'visible': False,
                             'absence_verified': True}]
    wait.until.assert_not_called()


def test_activation_uses_supplied_draft_and_requires_accessible_control(
        monkeypatch):
    driver = Mock()
    control = Mock(accessible_name='Conditional', tag_name='input')
    control.is_enabled.return_value = True
    control.get_attribute.return_value = 'textbox'
    wait = Mock(_driver=driver)
    wait.until.return_value = control
    geometry = Mock(return_value={'verified': True})
    monkeypatch.setattr(gate, 'assert_field_label_geometry', geometry)
    field = {'field_id': 'conditional', 'label': 'Conditional',
             'control': 'text',
             'visible_when': {'field_id': 'enabled', 'equals': True}}
    observed = gate.assert_form_controls(wait, [field], {'enabled': True})
    assert observed[0]['visible'] is True
    assert observed[0]['accessible_name'] == 'Conditional'
    geometry.assert_called_once_with(driver, control)


def test_invalid_visibility_is_not_treated_as_hidden():
    field = {'field_id': 'bad', 'label': 'Bad', 'control': 'text',
             'visible_when': {'unknown': True}}
    with pytest.raises(ValueError, match='Unknown field visibility'):
        gate.assert_form_controls(Mock(_driver=Mock()), [field])


@pytest.mark.parametrize('control,default', [
    ('boolean', False), ('text', ''), ('select', ''), ('multiselect', [])])
def test_initial_defaults_match_visible_scalar_controls(
        monkeypatch, control, default):
    seen = []

    def visibility(_field, draft):
        seen.append(draft['controller'])
        return False

    monkeypatch.setattr(gate, '_draft_field_visible', visibility)
    monkeypatch.setattr(gate, 'visible_named_control', Mock(return_value=None))
    gate.assert_form_controls(Mock(_driver=Mock()), [{
        'field_id': 'controller', 'label': 'Controller', 'control': control}])
    assert seen == [default]
