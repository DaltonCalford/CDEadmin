"""Native trap selections, private configuration and default isolation."""

import copy
import itertools
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor

import firebird.driver as native
from firebird.driver.config import DriverConfig
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import _route_arguments
from pgadmin.cdeadmin.providers.firebird.decfloat_traps import (
    TRAP_FIELDS, requested_traps,
)
from pgadmin.cdeadmin.providers.firebird.provider import (
    _database_create_arguments,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.endpoints import EndpointService
from pgadmin.cdeadmin.endpoints.profiles import (
    EndpointRegistrationError, registration_profile,
)


SUBSETS = [selected for count in range(1, 6)
           for selected in itertools.combinations(TRAP_FIELDS, count)]


@lru_cache(maxsize=1)
def firebird_profile():
    return registration_profile('firebird-native')


@pytest.mark.parametrize('selected', SUBSETS)
@pytest.mark.parametrize('creating', [False, True])
def test_every_nonempty_selection_has_an_isolated_private_config(
        monkeypatch, selected, creating):
    config = DriverConfig('owned-trap-preferences')
    config.db_defaults.decfloat_traps.value = [native.DecfloatTraps.INEXACT]
    defaults = config.db_defaults.get_config()
    monkeypatch.setattr(native, 'driver_config', config)
    route = {'host': 'localhost', 'port': 53050,
             'database': '/owned/traps.fdb',
             'decfloat_traps_policy': 'CUSTOM',
             **{field: field in selected for field in TRAP_FIELDS}}
    before = copy.deepcopy(route)

    def configure(value):
        return (_database_create_arguments(
            value, 'localhost/53050:/owned/created.fdb', {}, native)
            if creating else _route_arguments(value, native))

    arguments = configure(route)
    identity = arguments['database']
    private = config.get_database(identity)
    expected = [native.DecfloatTraps[TRAP_FIELDS[field]] for field in selected]
    assert private.decfloat_traps.value == expected
    assert 'decfloat_traps' not in arguments
    assert configure(route)['database'] == identity
    inherited = configure({**route, 'decfloat_traps_policy': 'NATIVE_DEFAULT'})
    assert inherited['database'] != identity
    default_config = config.get_database(inherited['database'])
    assert default_config.decfloat_traps.value is None
    assert private.decfloat_traps.value == expected
    assert config.db_defaults.get_config() == defaults
    assert route == before


@pytest.mark.parametrize('policy', [
    True, False, '', 'SERVER_DEFAULT', [], {}, 1,
])
@pytest.mark.parametrize('creating', [False, True])
def test_invalid_policy_is_rejected_before_driver_access(policy, creating):
    route = {'host': 'localhost', 'database': 'owned',
             'decfloat_traps_policy': policy}
    with pytest.raises(RelationalClientError, match='trap policy'):
        if creating:
            _database_create_arguments(route, 'localhost:owned', {}, None)
        else:
            _route_arguments(route, None)


@pytest.mark.parametrize('field', TRAP_FIELDS)
@pytest.mark.parametrize('value', [None, 0, 1, 'true', [], {}])
def test_custom_selection_requires_real_boolean_values(field, value):
    with pytest.raises(RelationalClientError, match='true or false'):
        requested_traps({'decfloat_traps_policy': 'CUSTOM', field: value})


@pytest.mark.parametrize('fields', [{}, {key: False for key in TRAP_FIELDS}])
def test_empty_custom_selection_never_means_disable_all(fields):
    with pytest.raises(RelationalClientError, match='at least one'):
        requested_traps({'decfloat_traps_policy': 'CUSTOM', **fields})


@pytest.mark.parametrize('policy', [None, 'NATIVE_DEFAULT'])
def test_native_default_ignores_inactive_custom_values(policy):
    assert requested_traps({'decfloat_traps_policy': policy,
                            **{field: True for field in TRAP_FIELDS}}) is None


def test_all_installed_native_traps_are_explicitly_mapped():
    assert set(TRAP_FIELDS.values()) == {
        trap.name for trap in native.DecfloatTraps}


def selection(fields):
    return {'decfloat_traps_policy': 'CUSTOM',
            **{field: field in fields for field in TRAP_FIELDS}}


@pytest.mark.parametrize('parent', SUBSETS)
@pytest.mark.parametrize('child', [
    'SERVER_DEFAULT', 'NATIVE_DEFAULT', *SUBSETS,
])
def test_group_inheritance_does_not_mix_parent_and_child_selections(
        parent, child):
    profile = firebird_profile()
    parent_values = selection(parent)
    child_values = (selection(child) if isinstance(child, tuple) else
                    {'decfloat_traps_policy': child})
    before = copy.deepcopy((parent_values, child_values))
    composed = {**parent_values, **EndpointService._database_route_options(
        profile, child_values)}
    expected = (None if child == 'NATIVE_DEFAULT' else tuple(
        TRAP_FIELDS[field] for field in (
            parent if child == 'SERVER_DEFAULT' else child)))
    assert requested_traps(composed) == expected
    assert (parent_values, child_values) == before


@pytest.mark.parametrize('operation', ['define', 'edit', 'connect'])
@pytest.mark.parametrize('selected', SUBSETS)
def test_database_form_retains_each_exact_custom_selection(
        operation, selected):
    profile = firebird_profile()
    values = EndpointService._database_form_values(
        profile, operation, selection(selected))
    assert requested_traps(values) == tuple(TRAP_FIELDS[f] for f in selected)


@pytest.mark.parametrize('operation', ['define', 'edit', 'connect'])
def test_empty_custom_database_form_is_rejected_before_saving(operation):
    with pytest.raises(EndpointRegistrationError, match='at least one'):
        EndpointService._database_form_values(
            firebird_profile(), operation, selection(()))


@pytest.mark.parametrize('operation', ['define', 'edit'])
@pytest.mark.parametrize('selected', [(), *SUBSETS])
def test_server_form_and_route_validate_custom_selections(operation, selected):
    profile = firebird_profile()
    route = {'host': 'localhost', 'port': 53050,
             'database_create_root': '/owned', **selection(selected)}
    for function, args in (
        (EndpointService._server_form_values,
         (profile, operation, {'name': 'Owned', **route})),
        (EndpointService._validated_route, (profile, {
            (field if field in {'host', 'port'}
             else 'cde_route_' + field): value
            for field, value in route.items()})),
    ):
        if not selected:
            with pytest.raises(EndpointRegistrationError,
                               match='at least one'):
                function(*args)
        else:
            assert requested_traps(function(*args)) == tuple(
                TRAP_FIELDS[field] for field in selected)


@pytest.mark.parametrize('creating', [False, True])
def test_concurrent_custom_selections_do_not_overwrite_each_other(creating):
    def configure(fields):
        route = {'host': 'localhost', 'port': 53050,
                 'database': '/owned/concurrent-traps.fdb',
                 **selection(fields)}
        args = (_database_create_arguments(
            route, 'localhost/53050:/owned/concurrent-create.fdb', {}, native)
            if creating else _route_arguments(route, native))
        return fields, args['database']

    with ThreadPoolExecutor(max_workers=8) as executor:
        configured = list(executor.map(configure, SUBSETS * 3))
    identities = {}
    for fields, identity in configured:
        config = native.driver_config.get_database(identity)
        assert config.decfloat_traps.value == [
            native.DecfloatTraps[TRAP_FIELDS[field]] for field in fields]
        assert identities.setdefault(fields, identity) == identity
    assert len(set(identities.values())) == len(SUBSETS)
