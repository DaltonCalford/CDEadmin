##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_ui_form_gate import assert_field_label_geometry


@pytest.mark.parametrize('geometry', [
    None,
    {'label': {'height': 12, 'bottom': 5},
     'input': {'top': 0, 'bottom': 32}, 'shrink': True},
    {'label': {'height': 24, 'bottom': 10},
     'input': {'top': 0, 'bottom': 64}, 'shrink': True},
    {'label': {'height': 18, 'bottom': 24},
     'input': {'top': 0, 'bottom': 32}, 'shrink': False},
])
def test_valid_label_positions(geometry):
    driver = Mock()
    driver.execute_script.return_value = geometry
    assert assert_field_label_geometry(driver, Mock()) == geometry


@pytest.mark.parametrize('height,bottom,shrink', [
    (18, 35, False), (12, 15, True), (0, 0, True),
])
def test_clipped_or_overlapping_label_is_not_qualified(height, bottom, shrink):
    driver = Mock()
    driver.execute_script.return_value = {
        'label': {'height': height, 'bottom': bottom},
        'input': {'top': 0, 'bottom': 32}, 'shrink': shrink,
    }
    with pytest.raises(RuntimeError):
        assert_field_label_geometry(driver, Mock())


def test_fixed_pixel_label_at_enlarged_root_is_not_qualified():
    driver = Mock()
    driver.execute_script.return_value = {
        'label': {'height': 10.5, 'bottom': 5},
        'input': {'top': 0, 'bottom': 64}, 'shrink': True,
        'label_font_size': 14, 'root_font_size': 32,
    }
    with pytest.raises(RuntimeError, match='text scale'):
        assert_field_label_geometry(driver, Mock())
