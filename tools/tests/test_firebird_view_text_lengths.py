"""View warnings report native metadata only; never rewrite source or types."""
import copy

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION  # noqa
from pgadmin.cdeadmin.providers.firebird.views import metadata_warnings


def column(**changes):
    return {'name': 'V', 'field_type': '14', 'character_set': 'UTF8',
            'field_length': '12', 'character_length': '3', **changes}


@pytest.mark.parametrize('length,characters', [
    (3, None), ('3', None), (4, None), (12, 2), (12, 'unknown'),
    (12, True), (12, 0), (12, -1), (12, '0'),
])
def test_inconsistent_utf8_lengths_are_disclosed(length, characters):
    value = [column(field_length=length, character_length=characters)]
    original = copy.deepcopy(value)
    warnings = metadata_warnings(value)
    assert len(warnings) == 1
    assert 'View column V' in warnings[0]
    assert 'may reject' in warnings[0]
    assert 'not rewritten' in warnings[0]
    assert value == original


@pytest.mark.parametrize('changes', [
    {}, {'field_length': 4, 'character_length': 1},
    {'field_type': '37', 'field_length': 3, 'character_length': None},
    {'character_set': 'ISO8859_1', 'field_length': 3},
    {'character_set': None}, {'field_type': None},
    {'field_length': None}, {'field_length': True},
    {'field_length': 'unknown'}, {'field_length': -1}, {'field_length': 0},
])
def test_other_or_unavailable_metadata_does_not_imply_failure(changes):
    assert metadata_warnings([column(**changes)]) == []


@pytest.mark.parametrize('value', [
    None, {}, 'unknown', [], [None, 1, 'field']])
def test_missing_columns_do_not_crash_catalog(value):
    assert metadata_warnings(value) == []


def test_distinct_columns_and_exact_names_are_retained():
    values = [column(name='Mixed "東京', field_length=3),
              column(name='GOOD'), column(name='OTHER', character_length=None)]
    warnings = metadata_warnings(values)
    assert len(warnings) == 2
    assert 'Mixed "東京' in warnings[0]
    assert 'OTHER' in warnings[1]
