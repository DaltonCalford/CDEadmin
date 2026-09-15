"""Reject clipped or hidden selections in real-browser connection evidence."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools import cdeadmin_firebird_database_lifecycle_ui_gate as gate


def control():
    return dict(width=280, content_width=280, height=48, content_height=48,
                white_space='normal')


@pytest.mark.parametrize('layout', [None, {}, {'controls': []}])
def test_missing_grid_is_not_readability_evidence(layout):
    with pytest.raises(RuntimeError, match='selected values are clipped'):
        gate._validate_selected_value_layout(layout)


@pytest.mark.parametrize('changes', [
    {'width': 0}, {'height': 0}, {'content_width': 282},
    {'content_height': 50}, {'white_space': 'nowrap'},
])
def test_every_select_must_be_visible_and_unclipped(changes):
    invalid = {**control(), **changes}
    with pytest.raises(RuntimeError, match='selected values are clipped'):
        gate._validate_selected_value_layout(
            {'controls': [control(), invalid, control()]})


@pytest.mark.parametrize('extra', [0, 0.5, 1])
def test_subpixel_rounding_tolerance(extra):
    selection = {**control(), 'content_width': 280 + extra,
                 'content_height': 48 + extra}
    gate._validate_selected_value_layout({'controls': [selection, control()]})


@pytest.mark.parametrize('server', [False, True])
@pytest.mark.parametrize('clipped', [False, True])
def test_capture_preserves_numeric_evidence_even_when_clipped(
        monkeypatch, tmp_path, server, clipped):
    layout = {'columns': '1048px', 'root_font_pixels': 48, 'controls': [
        {**control(), 'content_width': 282 if clipped else 280}]}
    driver = Mock()
    driver.execute_script.side_effect = [None, layout]
    field = Mock()
    field.screenshot.side_effect = lambda path: bool(
        Path(path).write_bytes(b'owned test image'))
    wait = Mock()
    wait.until.return_value = field
    context = Mock(return_value='owned-context-digest')
    pages = Mock(return_value=['owned-full-form-page'])
    monkeypatch.setattr(gate, 'screenshot', context)
    monkeypatch.setattr(gate, 'screenshot_form_pages', pages)
    evidence = {}
    if clipped:
        with pytest.raises(RuntimeError, match='selected values are clipped'):
            gate._rounding_capture(driver, wait, tmp_path, evidence,
                                   server=server)
        field.screenshot.assert_not_called()
        pages.assert_not_called()
    else:
        gate._rounding_capture(driver, wait, tmp_path, evidence, server=server)
        assert evidence['selected_value_layout'] == layout
        assert evidence['form_pages'] == ['owned-full-form-page']
        selector = pages.call_args.kwargs['selector']
        assert selector == (
            '[role="dialog"] [data-form-id]' if server else
            '[role="dialog"] section[aria-label="Engine database form"]')
    saved = json.loads((tmp_path / 'selected-value-layout.json').read_text())
    assert saved == layout
