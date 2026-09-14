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
