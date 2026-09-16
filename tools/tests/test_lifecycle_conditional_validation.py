"""Lifecycle validation probes must not invent active conditional controls."""

from unittest.mock import Mock

import pytest

from tools import cdeadmin_sqlite_database_lifecycle_ui_gate as gate


def field(required=True):
    return {'field_id': 'pages', 'label': 'Cache pages', 'control': 'number',
            'required': required}


@pytest.mark.parametrize('required,visible', [
    (True, False), (False, False), (False, True),
])
def test_inactive_or_optional_control_is_not_a_required_probe(
        required, visible):
    driver, wait = Mock(), Mock()
    result = gate._validation_observation(
        driver, wait, {'fields': [field(required)]}, 'connect',
        [{'field_id': 'pages', 'visible': visible}])
    assert result['state'] == 'not_applicable'
    wait.until.assert_not_called()
    driver.find_elements.assert_not_called()


def test_missing_observations_are_not_treated_as_hidden():
    with pytest.raises(RuntimeError, match='every field'):
        gate._validation_observation(
            Mock(), Mock(), {'fields': [field()]}, 'connect', [])


@pytest.mark.parametrize('visible', [None, 0, 1, 'false'])
def test_malformed_visibility_cannot_skip_required_validation(visible):
    with pytest.raises(RuntimeError, match='explicit field visibility'):
        gate._validation_observation(
            Mock(), Mock(), {'fields': [field()]}, 'connect',
            [{'field_id': 'pages', 'visible': visible}])


def test_active_custom_required_field_is_still_probed(monkeypatch):
    driver = Mock()
    driver.find_elements.return_value = []
    control, button = Mock(), Mock()
    button.is_enabled.return_value = False
    wait = Mock()
    wait.until.side_effect = [control, button]
    keys = Mock()
    monkeypatch.setattr(gate, 'ActionChains', keys)
    result = gate._validation_observation(
        driver, wait, {'fields': [field()], 'title': 'Connect database'},
        'connect', [{'field_id': 'pages', 'visible': True}])
    assert result['state'] == 'observed'
    assert result['field_id'] == 'pages'
    assert result['provider_operation_executed'] is False
    control.click.assert_called_once()
    keys.assert_called_once_with(driver)
    button.click.assert_not_called()
