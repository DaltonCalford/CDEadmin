"""An independent oracle must distinguish all eight native rounding modes."""

from unittest.mock import MagicMock
import decimal

import pytest

from tools.cdeadmin_firebird_rounding_oracle import (
    INPUTS, MODES, QUERY, expected_rounding, observe_rounding,
)


def test_all_modes_have_different_arithmetic_vectors():
    vectors = [tuple(expected_rounding(mode)['values']) for mode in MODES]
    assert len(set(vectors)) == 8
    assert expected_rounding('NATIVE_DEFAULT') == expected_rounding('HALF_UP')
    assert len(INPUTS) == 8


def test_oracle_is_independent_of_the_callers_decimal_context():
    expected = expected_rounding('HALF_UP')
    with decimal.localcontext() as context:
        context.prec = 1
        context.traps[decimal.Inexact] = True
        context.traps[decimal.Rounded] = True
        assert expected_rounding('HALF_UP') == expected
        assert context.prec == 1
        assert context.traps[decimal.Inexact]
        assert context.traps[decimal.Rounded]


@pytest.mark.parametrize('mode', MODES)
def test_observer_preserves_transaction_ownership(mode):
    expected = expected_rounding(mode)
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = [mode, *expected['values']]
    assert observe_rounding(connection) == expected
    cursor.execute.assert_called_once_with(QUERY)
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()
    connection.close.assert_not_called()
    assert QUERY.startswith('SELECT ')
    assert QUERY.endswith(' FROM RDB$DATABASE')
    assert ';' not in QUERY


@pytest.mark.parametrize('row', [None, [], ['HALF_UP'], [1] * 10])
def test_incomplete_observation_is_not_accepted(row):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = row
    with pytest.raises(RuntimeError, match='incomplete'):
        observe_rounding(connection)


@pytest.mark.parametrize('mode', ['', 'HALF_ODD', 'half_up', None])
def test_unknown_mode_is_not_guessed(mode):
    with pytest.raises(ValueError):
        expected_rounding(mode)
