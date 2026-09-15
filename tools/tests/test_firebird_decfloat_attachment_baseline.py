"""Independent baseline arithmetic and exact native trap inventory checks."""

import decimal
import copy
import json
from pathlib import Path

import firebird.driver as native
import pytest

from tools.cdeadmin_firebird_decfloat_attachment_gate import (
    ROUND_RESULTS, TRAP_PROBES,
)
from pgadmin.cdeadmin.providers.firebird.provider import (
    _route_arguments, _database_create_arguments,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.providers.form_contracts import (
    _database_contract, _DATABASE_SPECS,
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


@pytest.mark.parametrize('mode', list(ROUND_RESULTS))
def test_provider_rounding_config_is_isolated_and_not_a_connect_kwarg(mode):
    route = {'host': 'localhost', 'port': 53050, 'database': 'owned-rounding',
             'decfloat_round': mode}
    before = copy.deepcopy(route)
    args = _route_arguments(route, native)
    assert 'decfloat_round' not in args
    config = native.driver_config.get_database(args['database'])
    assert config.decfloat_round.value is native.DecfloatRound[mode]
    assert route == before
    default_args = _route_arguments({**route, 'decfloat_round': None}, native)
    default_config = native.driver_config.get_database(
        default_args['database'])
    assert default_args['database'] != args['database']
    assert default_config.decfloat_round.value is None
    for alternative in ROUND_RESULTS:
        other = _route_arguments({**route, 'decfloat_round': alternative},
                                 native)
        assert (other['database'] == args['database']) is (alternative == mode)
        assert config.decfloat_round.value is native.DecfloatRound[mode]
    created = _database_create_arguments(
        route, 'localhost/53050:/data/owned-rounding.fdb', {}, native)
    assert created['database'] != args['database']
    assert native.driver_config.get_database(
        created['database']).decfloat_round.value is native.DecfloatRound[mode]


@pytest.mark.parametrize('value', [
    '', 'half_even', 'NONE', 'SERVER_DEFAULT', 0, True, [], {},
])
@pytest.mark.parametrize('module', [None, native])
def test_invalid_rounding_rejected_before_driver_configuration(value, module):
    route = {'host': 'localhost', 'database': 'owned', 'decfloat_round': value}
    with pytest.raises(RelationalClientError, match='rounding'):
        _route_arguments(route, module)
    with pytest.raises(RelationalClientError, match='rounding'):
        _database_create_arguments(route, 'localhost:new', {}, module)


def test_explicit_native_default_removes_the_driver_override():
    route = {'host': 'localhost', 'database': 'owned-default',
             'decfloat_round': 'NATIVE_DEFAULT'}
    args = _route_arguments(route, native)
    config = native.driver_config.get_database(args['database'])
    assert config.decfloat_round.value is None
    assert 'decfloat_round' not in args


def test_native_rounding_choices_and_database_inheritance_are_explicit():
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / 'web/pgadmin/cdeadmin/providers/firebird/'
                           'provider_manifest.json').read_text())
    # Endpoint normalization rejects unknown controls/defaults/options.
    from pgadmin.cdeadmin.endpoints.profiles import _connection_fields
    fields = _connection_fields(manifest['registration'])
    server_field = next(f for f in fields if f['field_id'] == 'decfloat_round')
    expected = set(ROUND_RESULTS) | {'NATIVE_DEFAULT'}
    assert {o['value'] for o in server_field['options']} == expected
    assert server_field['default'] == 'NATIVE_DEFAULT'
    contract = _database_contract('firebird-native',
                                  _DATABASE_SPECS['firebird-native'])
    for action in ('define', 'connect', 'edit'):
        field = next(f for f in contract['forms'][action]['fields']
                     if f['field_id'] == 'decfloat_round')
        assert field['control'] == 'select'
        assert field['default'] == 'SERVER_DEFAULT'
        assert field['inherit_server_value'] == 'SERVER_DEFAULT'
        assert {o['value'] for o in field['options']} == (
            expected | {'SERVER_DEFAULT'})
