"""Cache comparison requires complete, typed, independently matching runs."""
from itertools import product

import pytest

from tools.cdeadmin_firebird_routine_cache_gate import compare_records


def records():
    return [{'mode': mode, 'history': history, 'warm': warm,
             'before': 1 if warm else None, 'after': 3, 'fresh': 3}
            for mode, history, warm in product(
                ('direct', 'savepoint', 'provider'),
                ('commit', 'create_rollback', 'alter_rollback'),
                (False, True))]


def test_complete_matching_matrix():
    assert compare_records(records()) == {
        'complete': True, 'divergences': [], 'stale_cases': []}


def test_native_staleness_is_observed_not_a_provider_divergence():
    matrix = records()
    for row in matrix:
        if row['history'] == 'create_rollback' and row['warm']:
            row['after'] = 0
    result = compare_records(matrix)
    assert result['complete'] is True
    assert len(result['stale_cases']) == 3
    assert result['divergences'] == []


@pytest.mark.parametrize('field', ['before', 'after', 'fresh'])
def test_provider_divergence_fails(field):
    matrix = records()
    matrix[-1][field] = 99
    result = compare_records(matrix)
    assert result['complete'] is False
    assert result['divergences'] == [matrix[-1]]


def test_wrong_fresh_result_fails_even_when_all_paths_match():
    matrix = records()
    for row in matrix:
        row['fresh'] = 0
    assert compare_records(matrix)['complete'] is False


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'unknown', 'type'])
def test_incomplete_or_malformed_matrix_rejected(change):
    matrix = records()
    if change == 'missing':
        matrix.pop()
    elif change == 'duplicate':
        matrix[-1] = dict(matrix[0])
    elif change == 'unknown':
        matrix[0]['mode'] = 'unknown'
    else:
        matrix[0]['fresh'] = True
    with pytest.raises(ValueError):
        compare_records(matrix)
