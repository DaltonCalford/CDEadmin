"""QA credential entry must not mistake an editor for authentication."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from selenium.common.exceptions import (
    StaleElementReferenceException, TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement

from tools import cdeadmin_ui_evidence as evidence


def prompt(*, visible=True, enabled=True):
    password = Mock(spec=WebElement)
    password.is_displayed.return_value = True
    password.is_enabled.return_value = enabled
    button = Mock()
    button.is_displayed.return_value = True
    button.is_enabled.return_value = enabled
    dialog = Mock()
    dialog.is_displayed.return_value = visible
    dialog.find_elements.side_effect = lambda _by, selector: (
        [password] if selector.startswith('input') else [button])
    title = Mock()
    title.find_element.return_value = dialog
    return SimpleNamespace(title=title, dialog=dialog, password=password,
                           button=button)


def immediate_wait(monkeypatch):
    def create(driver, _timeout):
        def until(callback):
            result = callback(driver)
            if not result:
                raise TimeoutException()
            return result
        return SimpleNamespace(until=until)
    monkeypatch.setattr(evidence, 'WebDriverWait', create)


def test_editor_password_is_never_selected_or_changed(monkeypatch):
    immediate_wait(monkeypatch)
    driver = Mock()
    # The page can contain arbitrary password inputs and an unrelated OK.
    editor_password = Mock()
    driver.find_elements.return_value = []
    driver.find_element.return_value = editor_password
    assert evidence.complete_endpoint_prompt(driver, 'fixture-secret') is False
    by, selector = driver.find_elements.call_args.args
    assert by == By.CSS_SELECTOR
    assert selector.split(', ') == [
        '#cdeadmin-modal-title-id-verify-endpoint',
        '#cdeadmin-modal-title-id-connect-server']
    driver.find_element.assert_not_called()
    editor_password.send_keys.assert_not_called()


@pytest.mark.parametrize('visible,enabled', [(False, True), (True, False)])
def test_hidden_or_disabled_prompt_is_not_changed(
        monkeypatch, visible, enabled):
    immediate_wait(monkeypatch)
    form = prompt(visible=visible, enabled=enabled)
    driver = Mock()
    driver.find_elements.return_value = [form.title]
    assert evidence.complete_endpoint_prompt(driver, 'fixture-secret') is False
    form.password.clear.assert_not_called()
    form.password.send_keys.assert_not_called()
    form.button.click.assert_not_called()


def test_confirmation_is_scoped_to_the_same_recognized_dialog(monkeypatch):
    immediate_wait(monkeypatch)
    form = prompt()
    driver = Mock()
    driver.find_elements.return_value = [form.title]
    form.button.click.side_effect = lambda: setattr(
        form.password.is_displayed, 'return_value', False)
    assert evidence.complete_endpoint_prompt(driver, 'fixture-secret') is True
    form.title.find_element.assert_called_once_with(
        By.XPATH, 'ancestor::*[@role="dialog"][1]')
    form.password.clear.assert_called_once_with()
    form.password.send_keys.assert_called_once_with('fixture-secret')
    form.button.click.assert_called_once_with()
    selectors = [call.args[1]
                 for call in form.dialog.find_elements.call_args_list]
    assert selectors == [
        'input[type="password"][autocomplete="current-password"]',
        'button[data-test="save"]']


def test_missing_credential_fails_before_any_field_change(monkeypatch):
    immediate_wait(monkeypatch)
    form = prompt()
    driver = Mock()
    driver.find_elements.return_value = [form.title]
    with pytest.raises(RuntimeError, match='provide'):
        evidence.complete_endpoint_prompt(driver, None)
    form.password.clear.assert_not_called()
    form.password.send_keys.assert_not_called()
    form.button.click.assert_not_called()


def test_multiple_prompts_are_rejected_without_credential_entry(monkeypatch):
    immediate_wait(monkeypatch)
    forms = [prompt(), prompt()]
    driver = Mock()
    driver.find_elements.return_value = [form.title for form in forms]
    with pytest.raises(RuntimeError, match='Multiple'):
        evidence.complete_endpoint_prompt(driver, 'fixture-secret')
    for form in forms:
        form.password.send_keys.assert_not_called()
        form.button.click.assert_not_called()


@pytest.mark.parametrize('duplicated', ['password', 'button'])
def test_ambiguous_controls_are_rejected(duplicated):
    form = prompt()
    form.dialog.find_elements.side_effect = lambda _by, selector: (
        [form.password] * (2 if duplicated == 'password' else 1)
        if selector.startswith('input') else
        [form.button] * (2 if duplicated == 'button' else 1))
    driver = Mock()
    driver.find_elements.return_value = [form.title]
    with pytest.raises(RuntimeError, match='Ambiguous'):
        evidence._endpoint_prompt_controls(driver)
    form.password.send_keys.assert_not_called()


def test_stale_prompt_can_be_rediscovered_without_typing():
    title = Mock()
    title.find_element.side_effect = StaleElementReferenceException()
    driver = Mock()
    driver.find_elements.return_value = [title]
    assert evidence._endpoint_prompt_controls(driver) is False


@pytest.mark.parametrize('missing', ['password', 'button'])
def test_partial_dialog_does_not_authorize_credential_entry(missing):
    form = prompt()
    form.dialog.find_elements.side_effect = lambda _by, selector: (
        ([] if missing == 'password' else [form.password])
        if selector.startswith('input') else
        ([] if missing == 'button' else [form.button]))
    driver = Mock()
    driver.find_elements.return_value = [form.title]
    assert evidence._endpoint_prompt_controls(driver) is False
    form.password.send_keys.assert_not_called()
