"""Native rename seeds carry actual, distinct array values, not NULLs."""

import pytest

from tools.cdeadmin_firebird_rename_ui_gate import array_seed


@pytest.mark.parametrize('row', [7, 11])
def test_column_seed_preserves_integer_boundaries_and_row_identity(row):
    assert array_seed(False, row) == [-2147483648, row, 2147483647]


@pytest.mark.parametrize('row', [7, 11])
def test_domain_seed_matches_nondefault_multidimensional_bounds(row):
    values = array_seed(True, row)
    assert len(values) == 6
    assert all(len(axis) == 2 for axis in values)
    assert values[0] == [row * 100 - 19, row * 100 - 18]
    assert values[-1] == [row * 100 + 31, row * 100 + 32]
    assert len({item for axis in values for item in axis}) == 12


def test_array_seed_has_no_shared_mutable_rows_or_calls():
    value = array_seed(True, 7)
    value[0][0] = None
    assert value[1][0] == 691
    assert array_seed(True, 7)[0][0] == 681
    assert array_seed(True, 11)[0][0] == 1081
