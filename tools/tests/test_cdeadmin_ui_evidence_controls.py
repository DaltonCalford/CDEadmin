##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_ui_evidence import (
    _fill_multiple_options, _multiple_values,
    ensure_data_explorer, fill_fields,
)


@pytest.mark.parametrize('value', ['{}', 'null', '[1]', '["A", "A"]'])
def test_multiple_values_reject_invalid_requests(value):
    with pytest.raises(ValueError):
        _multiple_values(value)


@pytest.mark.parametrize('value,expected', [
    ('[]', []), ('["A", "B"]', ['A', 'B']),
])
def test_multiple_values_preserve_explicit_replacement(value, expected):
    assert _multiple_values(value) == expected


def option(value, selected):
    attributes = {'data-value': value, 'aria-selected': str(selected).lower()}
    control = Mock()
    control.is_displayed.return_value = True
    control.get_attribute.side_effect = attributes.get

    def click():
        attributes['aria-selected'] = (
            'false' if attributes['aria-selected'] == 'true' else 'true')

    control.click.side_effect = click
    return control


@pytest.mark.parametrize('values,counts', [
    (['A'], [0, 0]), (['B'], [1, 1]), ([], [1, 0]),
    (['A', 'B'], [0, 1]),
])
def test_multiple_selection_replaces_not_appends(values, counts):
    options = [option('A', True), option('B', False)]
    driver = Mock()
    driver.find_elements.return_value = options
    driver.switch_to = SimpleNamespace(active_element=Mock())
    _fill_multiple_options(driver, values)
    assert [item.click.call_count for item in options] == counts
    driver.switch_to.active_element.send_keys.assert_called_once()


def test_unavailable_selection_does_not_change_existing_values():
    existing = option('A', True)
    driver = Mock()
    driver.find_elements.return_value = [existing]
    with pytest.raises(ValueError):
        _fill_multiple_options(driver, ['UNKNOWN'])
    existing.click.assert_not_called()


def test_field_entry_waits_for_metadata_initialization(monkeypatch):
    control = Mock(tag_name='input')
    control.get_attribute.side_effect = {'type': 'checkbox'}.get
    control.is_enabled.side_effect = [False, True]
    control.is_selected.return_value = False
    monkeypatch.setattr('tools.cdeadmin_ui_evidence.visible_named_control',
                        lambda driver, name: control)
    driver = Mock()
    wait = Mock()

    def poll(callback):
        assert callback(driver) is None
        control.click.assert_not_called()
        return callback(driver)

    wait.until.side_effect = poll
    fill_fields(wait, ['Default role=true'])
    control.click.assert_called_once()


def test_failed_selection_is_reported():
    existing = option('A', False)
    existing.click.side_effect = None
    driver = Mock()
    driver.find_elements.return_value = [existing]
    with pytest.raises(RuntimeError):
        _fill_multiple_options(driver, ['A'])


@pytest.mark.parametrize('initial,requested,clicks', [
    (False, 'true', 1), (True, 'false', 1),
    (True, 'true', 0), (False, 'false', 0),
])
def test_checkbox_uses_click_not_text_events(
        monkeypatch, initial, requested, clicks):
    control = Mock(tag_name='input')
    control.get_attribute.side_effect = {'type': 'checkbox'}.get
    control.is_selected.return_value = initial
    monkeypatch.setattr('tools.cdeadmin_ui_evidence.visible_named_control',
                        lambda driver, name: control)
    driver = Mock()
    wait = Mock()
    wait.until.side_effect = lambda callback: callback(driver)
    fill_fields(wait, ['Default role=' + requested])
    assert control.click.call_count == clicks
    driver.execute_script.assert_not_called()


@pytest.mark.parametrize('initial_open', [True, False])
def test_navigator_is_only_opened_when_closed(initial_open):
    opened = initial_open
    toggle = Mock()
    activity = Mock()
    activity.get_attribute.return_value = 'false'

    def open_navigator():
        nonlocal opened
        opened = True

    activity.click.side_effect = open_navigator
    driver = Mock()
    driver.find_elements.side_effect = lambda by, selector: (
        [activity] if 'activity-activity.data' in selector else
        [toggle] if opened else [])
    wait = Mock()
    wait.until.side_effect = lambda callback: callback(driver)
    ensure_data_explorer(wait)
    assert opened
    assert activity.click.call_count == (0 if initial_open else 1)
