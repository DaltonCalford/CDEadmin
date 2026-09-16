"""Required numeric zero is present; false/empty non-numeric values are not."""

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION  # noqa
from pgadmin.cdeadmin.endpoints.profiles import (
    EndpointRegistrationError, provider_route_options,
)


def profile(control='number', minimum=0):
    return {'connection_fields': [{
        'field_id': 'owned', 'route_key': 'owned', 'label': 'Owned field',
        'control': control, 'required': True, 'minimum': minimum,
        'maximum': 32767, 'integer': True}]}


@pytest.mark.parametrize('value', [0, 0.0, 1, 32767])
def test_required_numeric_value_is_retained(value):
    result = provider_route_options(profile(), {'cde_route_owned': value})
    assert result['owned'] == value
    assert type(result['owned']) is int


@pytest.mark.parametrize('value', [True, False, '', None, -1, 32768, 1.5])
def test_presence_fix_does_not_relax_type_or_bounds(value):
    with pytest.raises(EndpointRegistrationError):
        provider_route_options(profile(), {'cde_route_owned': value})


@pytest.mark.parametrize('control,value', [('boolean', False), ('text', '')])
def test_required_nonnumeric_false_or_empty_remains_invalid(control, value):
    with pytest.raises(EndpointRegistrationError):
        provider_route_options(profile(control), {'cde_route_owned': value})


def test_zero_remains_invalid_when_provider_requires_positive_value():
    with pytest.raises(EndpointRegistrationError, match='range'):
        provider_route_options(profile(minimum=1), {'cde_route_owned': 0})
