"""The browser properties oracle reads stored buffers independently."""

from types import SimpleNamespace
from unittest.mock import Mock

import firebird.driver as native
import pytest

from tools import cdeadmin_firebird_properties_ui_gate as gate


@pytest.mark.parametrize('stored', [0, 64])
def test_properties_oracle_does_not_infer_stored_setting_from_allocation(
        monkeypatch, tmp_path, stored):
    library = tmp_path / 'owned-client.so'
    library.touch()
    monkeypatch.setenv('CDEADMIN_FIREBIRD_CLIENT_LIBRARY', str(library))
    monkeypatch.setattr(native.fbapi, 'has_api', lambda: True)
    handle = Mock()
    handle.info.page_cache_size = 128
    handle.info.get_info.return_value = stored
    handle.cursor.return_value.fetchone.side_effect = [
        ('/owned.fdb', 8192, 13, 1, 3, 0, 1, 1, 20000, 0, 0, 0),
        ('UTF8', 'UTF8'),
    ]
    monkeypatch.setattr(native, 'connect', Mock(return_value=handle))
    options = SimpleNamespace(client_library=library, host='127.0.0.1',
                              firebird_port=50000, database_path='/owned.fdb',
                              user='SYSDBA')
    observed = gate._native(options, 'owned-secret')
    handle.info.get_info.assert_called_once_with(
        native.DbInfoCode.SET_PAGE_BUFFERS)
    assert observed['stored_page_buffers'] == str(stored)
    expected = gate._expected(observed, options)
    assert expected['Firebird storage and durability'][
        'Stored page-buffer override (0 uses server default)'] == str(stored)
    handle.close.assert_called_once()


@pytest.mark.parametrize('overflow', [False, True])
def test_cache_row_proof_measures_each_term_and_value(
        monkeypatch, tmp_path, overflow):
    driver = Mock()
    layout = [{'width': 180, 'content_width': 220 if overflow else 180,
               'height': 60, 'content_height': 60},
              {'width': 240, 'content_width': 240,
               'height': 60, 'content_height': 60}]
    driver.execute_script.side_effect = [None, layout] * 3
    capture = Mock(return_value='owned-image-hash')
    monkeypatch.setattr(gate, 'screenshot', capture)
    evidence = gate.cache_row_evidence(
        driver, SimpleNamespace(output_root=tmp_path))
    assert len(evidence) == 3
    assert all(item['no_text_overflow'] is not overflow for item in evidence)
    assert driver.find_element.call_count == 3
    assert capture.call_count == 3
    assert all(call.kwargs == {'reset_scroll': False}
               for call in capture.call_args_list)


@pytest.mark.parametrize('passed', [False, True])
def test_property_gate_exit_code_matches_summary(
        monkeypatch, tmp_path, passed):
    options = SimpleNamespace(summary_output=tmp_path / 'summary.json')
    monkeypatch.setattr(gate, 'arguments', Mock(return_value=options))
    monkeypatch.setattr(gate, 'run', Mock(return_value={
        'passed': passed, 'property_group_count': 9,
        'rendered_property_count': 42}))
    assert gate.main([]) == (0 if passed else 1)
