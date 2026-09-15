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


@pytest.mark.parametrize('obscured', [False, True])
def test_context_command_scrolls_menu_and_requires_pointer_hit(
        monkeypatch, obscured):
    from tools import cdeadmin_ui_evidence as evidence
    from selenium.common.exceptions import TimeoutException

    item = Mock()
    item.is_enabled.return_value = True
    driver = Mock()
    driver.execute_script.side_effect = [
        {'commands': []}, item, None, not obscured]
    monkeypatch.setattr(evidence, '_context_pointer', Mock())
    monkeypatch.setattr(evidence, 'complete_endpoint_prompt',
                        Mock(return_value=False))
    monkeypatch.setattr(evidence, 'visible_menu_label',
                        Mock(return_value=item))

    def until(callback):
        value = callback(driver)
        if not value:
            raise TimeoutException()
        return value

    wait = SimpleNamespace(until=until)
    if obscured:
        with pytest.raises(TimeoutException):
            evidence.invoke_context_action(wait, driver, object(), ['Task'])
        item.click.assert_not_called()
    else:
        evidence.invoke_context_action(wait, driver, object(), ['Task'])
        item.click.assert_called_once_with()
    scripts = [call.args[0] for call in driver.execute_script.call_args_list]
    assert 'menu.scrollTop' in scripts[-2]
    assert 'scrollIntoView' not in scripts[-2]
    assert 'elementFromPoint' in scripts[-1]


@pytest.mark.parametrize('reachable', [False, True])
def test_context_pointer_waits_for_visible_hit_without_synthetic_click(
        monkeypatch, reachable):
    from tools import cdeadmin_ui_evidence as evidence
    from selenium.common.exceptions import TimeoutException

    driver = Mock()
    driver.execute_script.return_value = (
        {'x': 250, 'y': 90} if reachable else None)
    actions = Mock()
    actions.context_click.return_value = actions
    factory = Mock(return_value=actions)
    monkeypatch.setattr(evidence, 'ActionChains', factory)

    def until(callback):
        value = callback(driver)
        if not value:
            raise TimeoutException()
        return value

    target = object()
    if reachable:
        evidence._context_pointer(SimpleNamespace(until=until), driver, target)
        movement = actions.w3c_actions.pointer_action.move_to_location
        movement.assert_called_once_with(250, 90)
        actions.context_click.assert_called_once_with()
        actions.perform.assert_called_once_with()
    else:
        with pytest.raises(TimeoutException):
            evidence._context_pointer(
                SimpleNamespace(until=until), driver, target)
        factory.assert_not_called()
    script, supplied = driver.execute_script.call_args.args
    assert supplied is target
    for required in ('elementFromPoint', 'isConnected',
                     'overflowX', 'overflowY'):
        assert required in script
    assert 'dispatchEvent' not in script
    assert '.click()' not in script
    assert 'scrollIntoView' not in script


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


@pytest.mark.parametrize('dynamic', [False, True])
def test_record_fields_are_resolved_in_the_requested_row(monkeypatch, dynamic):
    control = Mock(tag_name='input')
    control.get_attribute.side_effect = {'type': 'checkbox'}.get
    control.is_enabled.return_value = True
    control.is_selected.return_value = False
    root = SimpleNamespace(row='second dimension')
    locate = Mock(return_value=control)
    monkeypatch.setattr('tools.cdeadmin_ui_evidence.visible_named_control',
                        locate)
    driver = Mock()
    wait = Mock()
    wait.until.side_effect = lambda callback: callback(driver)
    fill_fields(wait, ['Selected=true'], control_root=(
        lambda current: root) if dynamic else root)
    locate.assert_called_once_with(root, 'Selected')
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
