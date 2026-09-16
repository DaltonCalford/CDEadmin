"""Worker requests preserve zero, native defaults and grouped inheritance."""

import copy
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import firebird.driver as native
from firebird.driver.config import DriverConfig
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import _route_arguments
from pgadmin.cdeadmin.providers.firebird.provider import (
    _database_create_arguments,
)
from pgadmin.cdeadmin.providers.firebird.parallel_workers import (
    requested_workers,
)
from pgadmin.cdeadmin.endpoints import EndpointService
from pgadmin.cdeadmin.endpoints.profiles import (
    registration_profile, EndpointRegistrationError,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


VALUES = [0, 1, 2, 4, 5, 32767]


@lru_cache(maxsize=1)
def profile():
    return registration_profile('firebird-native')


def selection(value):
    return {'parallel_workers_policy': (
        'NATIVE_DEFAULT' if value is None else 'CUSTOM'),
        'parallel_workers': value}


@pytest.mark.parametrize('creation', [False, True])
def test_parallel_private_configuration_and_concurrent_isolation(
        monkeypatch, creation):
    registry = DriverConfig('owned-workers-test')
    registry.db_defaults.parallel_workers.value = 4
    monkeypatch.setattr(native, 'driver_config', registry)
    defaults = registry.db_defaults.get_config()

    def mapped(value):
        route = {'host': 'localhost', 'port': 53050,
                 'database': '/owned/workers.fdb', **selection(value)}
        before = copy.deepcopy(route)
        args = (_database_create_arguments(
            route, 'localhost/53050:/owned/new.fdb', {}, native) if creation
            else _route_arguments(route, native))
        assert route == before
        assert 'parallel_workers' not in args
        private = registry.get_database(args['database'])
        assert private.parallel_workers.value == value
        return args['database']

    with ThreadPoolExecutor(max_workers=8) as pool:
        names = list(pool.map(mapped, [None, *VALUES] * 4))
    assert len(set(names)) == 7
    assert names[:7] == names[7:14] == names[14:21] == names[21:]
    assert registry.db_defaults.get_config() == defaults


@pytest.mark.parametrize('value', [None, -1, 32768, True, False,
                                   1.0, '1', '', [], {}])
@pytest.mark.parametrize('creation', [False, True])
def test_malformed_requests_refused_before_driver_access(value, creation):
    route = {'parallel_workers_policy': 'CUSTOM', 'parallel_workers': value}
    with pytest.raises(RelationalClientError, match='parallel workers'):
        if creation:
            _database_create_arguments(route, 'owned', {}, None)
        else:
            _route_arguments(route, None)


@pytest.mark.parametrize('policy', ['', 'SERVER_DEFAULT', True, False, 1,
                                    'custom', [], {}])
def test_invalid_or_unresolved_policy_never_becomes_native_default(policy):
    with pytest.raises(RelationalClientError, match='policy'):
        requested_workers({'parallel_workers_policy': policy})


@pytest.mark.parametrize('parent', [None, *VALUES])
@pytest.mark.parametrize('child', ['SERVER_DEFAULT', None, *VALUES])
def test_group_inheritance_does_not_mix_stale_child_values(parent, child):
    child_values = ({'parallel_workers_policy': child, 'parallel_workers': 123}
                    if child == 'SERVER_DEFAULT' else selection(child))
    before = copy.deepcopy(child_values)
    effective = {
        **selection(parent),
        **EndpointService._database_route_options(profile(), child_values)}
    assert requested_workers(effective) == (
        parent if child == 'SERVER_DEFAULT' else child)
    assert child_values == before


@pytest.mark.parametrize('operation', ['define', 'edit', 'connect'])
@pytest.mark.parametrize('value', VALUES)
def test_database_form_retains_exact_request(operation, value):
    validated = EndpointService._database_form_values(
        profile(), operation, selection(value))
    assert requested_workers(validated) == value


@pytest.mark.parametrize('operation', ['define', 'edit', 'connect'])
@pytest.mark.parametrize('value', [-1, 32768, True, 1.5, 'invalid', [], {}])
def test_database_form_refuses_invalid_request(operation, value):
    with pytest.raises(EndpointRegistrationError):
        EndpointService._database_form_values(
            profile(), operation, selection(value))


@pytest.mark.parametrize('policy', ['SERVER_DEFAULT', 'NATIVE_DEFAULT'])
def test_inactive_form_count_is_not_saved(policy):
    values = EndpointService._database_form_values(profile(), 'connect', {
        'parallel_workers_policy': policy})
    assert values['parallel_workers_policy'] == policy
    assert 'parallel_workers' not in values
    with pytest.raises(EndpointRegistrationError, match='unavailable'):
        EndpointService._database_form_values(profile(), 'connect', {
            'parallel_workers_policy': policy, 'parallel_workers': 123})


@pytest.mark.parametrize('value', VALUES)
@pytest.mark.parametrize('operation', ['define', 'edit'])
def test_server_form_and_route_retain_explicit_zero_and_bounds(
        value, operation):
    selected = {'host': 'localhost', 'port': 53050,
                'database_create_root': '/owned', **selection(value)}
    saved = EndpointService._server_form_values(
        profile(), operation, {'name': 'Owned', **selected})
    assert saved['parallel_workers'] == value
    validated = EndpointService._validated_route(profile(), {
        (name if name in {'host', 'port'} else 'cde_route_' + name): val
        for name, val in selected.items()})
    assert validated['parallel_workers'] == value
