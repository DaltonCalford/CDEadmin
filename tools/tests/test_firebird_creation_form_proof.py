"""Stored-buffer layout evidence is scoped and does not create a database."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import cdeadmin_firebird_database_lifecycle_ui_gate as gate
from tools import cdeadmin_firebird_linger_ui_gate as linger


@pytest.mark.parametrize('dialog_count', [0, 1, 2])
@pytest.mark.parametrize('capture_fails', [False, True])
@pytest.mark.parametrize('quit_fails', [False, True])
def test_proof_requires_one_dialog_and_collects_every_value(
        monkeypatch, tmp_path, dialog_count, capture_fails, quit_fails):
    monkeypatch.setenv('OWNED_FORM_SECRET', 'test-secret')
    monkeypatch.setattr(gate, '_configure_shared', Mock())
    driver = Mock()
    if quit_fails:
        driver.quit.side_effect = RuntimeError('cleanup failure')
    dialog = Mock()
    dialog.is_displayed.return_value = True
    driver.find_elements.return_value = [dialog] * dialog_count
    monkeypatch.setattr(gate, 'create_driver', Mock(return_value=driver))
    for name in ('_prepare_tree', '_open_form', 'fill_fields',
                 '_close_with_escape'):
        monkeypatch.setattr(gate.shared, name, Mock())
    control = Mock()
    control.get_attribute.side_effect = lambda _name: (
        gate.shared.fill_fields.call_args.args[1][0].split('=', 1)[1])
    monkeypatch.setattr(gate.shared, 'visible_named_control',
                        Mock(return_value=control))
    monkeypatch.setattr(gate.shared, '_target_rows', Mock(return_value=[]))
    capture = Mock(side_effect=RuntimeError('secret-canary')
                   if capture_fails else None, return_value={})
    monkeypatch.setattr(linger, 'capture', capture)
    monkeypatch.setattr(gate, 'screenshot_form_pages', Mock(return_value=[]))
    options = SimpleNamespace(
        password_env='OWNED_FORM_SECRET', timeout=1, config_db='owned.db',
        database='owned.fdb', url='http://127.0.0.1:50000',
        output_root=tmp_path)
    result = gate.creation_form_proof(options)
    assert options.endpoint_password_env == 'OWNED_FORM_SECRET'
    driver.set_script_timeout.assert_called_once_with(120)
    success = dialog_count == 1 and not capture_fails
    assert result['complete'] is (success and not quit_fails)
    assert len(result['failures']) == (0 if success else 3) + quit_fails
    assert result['mutations_requested'] is False
    assert gate.shared.fill_fields.call_count == 3
    if dialog_count == 1:
        assert capture.call_count == 3
        for call in capture.call_args_list:
            assert call.kwargs['scope'] is dialog
            assert call.kwargs['label'] == 'Stored database page buffers'
    else:
        capture.assert_not_called()
    driver.quit.assert_called_once()


def test_configuration_read_failure_still_closes_browser(monkeypatch):
    monkeypatch.setenv('OWNED_FORM_SECRET', 'test-secret')
    monkeypatch.setattr(gate, '_configure_shared', Mock())
    driver = Mock()
    monkeypatch.setattr(gate, 'create_driver', Mock(return_value=driver))
    monkeypatch.setattr(gate.shared, '_target_rows', Mock(
        side_effect=RuntimeError('private-config-failure')))
    result = gate.creation_form_proof(SimpleNamespace(
        password_env='OWNED_FORM_SECRET', config_db='owned.db'))
    assert not result['complete']
    assert result['failures'][0]['phase'] == 'setup'
    assert result['failures'][0]['error_type'] == 'RuntimeError'
    assert result['failures'][0]['locations']
    driver.quit.assert_called_once()
