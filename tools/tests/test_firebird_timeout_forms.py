"""Timeout forms must reject values that native initialization cannot use."""

from functools import lru_cache

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION  # noqa
from pgadmin.cdeadmin.endpoints import EndpointService
from pgadmin.cdeadmin.endpoints.profiles import (
    EndpointRegistrationError, registration_profile,
)


@lru_cache(maxsize=1)
def profile():
    return registration_profile('firebird-native')


FIELDS = [
    ('transaction_lock_timeout', -1, 32767),
    ('statement_timeout_ms', 0, 2147483647),
    ('session_idle_timeout_seconds', 0, 2147483647),
]


@pytest.mark.parametrize('operation', ['define', 'edit', 'connect'])
@pytest.mark.parametrize('key,minimum,maximum', FIELDS)
@pytest.mark.parametrize('kind', ['fraction', 'boolean', 'low', 'high'])
def test_database_timeout_refuses_invalid_value(
        operation, key, minimum, maximum, kind):
    value = {'fraction': 1.5, 'boolean': True,
             'low': minimum - 1, 'high': maximum + 1}[kind]
    with pytest.raises(EndpointRegistrationError):
        EndpointService._database_form_values(
            profile(), operation, {key: value})


@pytest.mark.parametrize('operation', ['define', 'edit', 'connect'])
@pytest.mark.parametrize('key,minimum,maximum', FIELDS)
@pytest.mark.parametrize('kind', ['minimum', 'zero', 'maximum', 'whole_float'])
def test_database_timeout_normalizes_exact_integer(
        operation, key, minimum, maximum, kind):
    value = {'minimum': minimum, 'zero': 0,
             'maximum': maximum, 'whole_float': 12.0}[kind]
    values = EndpointService._database_form_values(
        profile(), operation, {key: value})
    assert type(values[key]) is int
    assert values[key] == value


@pytest.mark.parametrize('operation', ['define', 'edit', 'connect'])
@pytest.mark.parametrize('key,minimum,maximum', FIELDS)
def test_omitted_timeout_preserves_existing_default_semantics(
        operation, key, minimum, maximum):
    values = EndpointService._database_form_values(profile(), operation, {})
    if key == 'transaction_lock_timeout':
        assert values[key] == -1
    else:
        assert key not in values
