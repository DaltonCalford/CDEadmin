"""Independent baseline arithmetic and exact native trap inventory checks."""

import decimal

import firebird.driver as native
import pytest

from tools.cdeadmin_firebird_decfloat_attachment_gate import (
    ROUND_RESULTS, TRAP_PROBES,
)


@pytest.mark.parametrize('mode,expected', list(ROUND_RESULTS.items()))
def test_rounding_oracle_uses_decimal_not_binary_floats(mode, expected):
    rounding = (decimal.ROUND_05UP if mode == 'REROUND'
                else getattr(decimal, 'ROUND_' + mode))
    actual = tuple(str(decimal.Decimal(value).quantize(
        decimal.Decimal('0.1'), rounding=rounding))
        for value in ('1.25', '-1.25'))
    assert actual == expected


def test_baseline_covers_every_installed_round_mode_and_trap():
    assert set(ROUND_RESULTS) == {item.name for item in native.DecfloatRound}
    assert set(TRAP_PROBES) == {item.name for item in native.DecfloatTraps}
    codes = [value[1] for value in TRAP_PROBES.values()]
    assert len(set(codes)) == 5
    assert codes == list(range(335545139, 335545144))


@pytest.mark.parametrize('expression,code', list(TRAP_PROBES.values()))
def test_probes_are_fixed_read_only_expressions(expression, code):
    assert expression.startswith('CAST(')
    assert ';' not in expression
    assert 'DECFLOAT(16)' in expression
    assert type(code) is int
