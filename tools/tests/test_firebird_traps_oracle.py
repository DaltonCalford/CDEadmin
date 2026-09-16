"""Exact native context decoding and read-only transaction ownership."""

import itertools
from unittest.mock import MagicMock

import pytest

from tools.cdeadmin_firebird_traps_oracle import (
    DEFAULT_TRAPS, NATIVE_NAMES, QUERY, observe_traps,
    first_trapped_condition,
)


SUBSETS = [selected for count in range(6)
           for selected in itertools.combinations(NATIVE_NAMES, count)]


@pytest.mark.parametrize('selected', SUBSETS)
def test_every_native_context_subset(selected):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = [','.join(selected) if selected else 'None']
    assert observe_traps(connection) == sorted(
        NATIVE_NAMES[name] for name in selected)
    cursor.execute.assert_called_once_with(QUERY)
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()
    connection.close.assert_not_called()
    connection.execute_immediate.assert_not_called()


@pytest.mark.parametrize('row', [
    None, [], [None], [1], ['None', 'None'], [''], ['NONE'],
    ['DIVISION_BY_ZERO'], ['Inexact,Inexact'], ['Inexact, Overflow'],
    ['None,Inexact'], ['Unknown'],
])
def test_incomplete_or_unrecognized_state_is_not_accepted(row):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = row
    with pytest.raises(RuntimeError, match='Native trap observation'):
        observe_traps(connection)


def test_default_is_explicit_not_all_conditions():
    assert DEFAULT_TRAPS == (
        'DIVISION_BY_ZERO', 'INVALID_OPERATION', 'OVERFLOW')
    assert set(DEFAULT_TRAPS) < set(NATIVE_NAMES.values())
    assert QUERY.startswith('SELECT ')
    assert QUERY.endswith(' FROM RDB$DATABASE')
    assert ';' not in QUERY


@pytest.mark.parametrize('selected', SUBSETS)
@pytest.mark.parametrize('probe', list(NATIVE_NAMES.values()))
def test_fixed_probe_signals_and_native_error_precedence(selected, probe):
    active = {NATIVE_NAMES[name] for name in selected}
    if probe in ('OVERFLOW', 'UNDERFLOW') and 'INEXACT' in active:
        expected = 'INEXACT'
    else:
        expected = probe if probe in active else None
    assert first_trapped_condition(probe, active) == expected


@pytest.mark.parametrize('probe,active', [
    ('UNKNOWN', []), ('OVERFLOW', ['NONE']), ('INEXACT', ['inexact']),
])
def test_probe_oracle_rejects_unknown_names(probe, active):
    with pytest.raises(ValueError, match='Unknown trap'):
        first_trapped_condition(probe, active)


@pytest.mark.parametrize('mode', ['', None, 'super', 'Unknown', True])
def test_gate_rejects_unknown_modes_before_starting_a_fixture(monkeypatch, mode):
    from tools import cdeadmin_firebird_decfloat_attachment_gate as gate
    docker = MagicMock()
    monkeypatch.setattr(gate, 'docker', docker)
    with pytest.raises(ValueError, match='Unknown owned Firebird server mode'):
        gate.run('firebirdsql/firebird:5.0.4', server_mode=mode)
    docker.assert_not_called()
