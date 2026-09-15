"""Exact server/target rounding precedence through the real route composer."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import firebird.driver as native
import pytest

from tools.cdeadmin_firebird_decfloat_attachment_gate import (
    CONNECTION_ROUND_RESULTS,
)
from pgadmin.cdeadmin.endpoints import EndpointService
from pgadmin.cdeadmin.providers.firebird.provider import _route_arguments
from pgadmin.cdeadmin.providers.firebird.provider import (
    _database_create_arguments,
)


MODES = [None, *CONNECTION_ROUND_RESULTS]


@pytest.mark.parametrize('server_mode', MODES)
@pytest.mark.parametrize('database_mode', MODES)
@pytest.mark.parametrize('explicit_target', [False, True])
def test_rounding_precedence_preserves_saved_profile_identity(
        server_mode, database_mode, explicit_target):
    base = {'host': 'localhost', 'port': 53050}
    if server_mode is not None:
        base['decfloat_round'] = server_mode
    database_options = ({} if database_mode is None else
                        {'decfloat_round': database_mode})
    route = SimpleNamespace(id='rounding-route', priority=0,
                            configuration=json.dumps(base))
    target = SimpleNamespace(id='rounding-target', active=True,
                             database='/owned/rounding.fdb',
                             configuration=json.dumps(database_options))
    endpoint = SimpleNamespace(routes=[route], secret_references=[],
                               database_targets=[target])
    service = EndpointService(SimpleNamespace(), SimpleNamespace(
        secrets=SimpleNamespace(register_resolver=lambda *_args: None)))
    profile = {'profile_id': 'firebird-native', 'requires_secret': False,
               'database_targeting': {'multiple': True}}
    before = (route.configuration, target.configuration)
    options = ({'database_override': target.database,
                'database_options': database_options} if explicit_target
               else {})
    composed, reference = service._route_and_reference(
        SimpleNamespace(user_id=7), endpoint, profile, **options)
    expected = database_mode if database_mode is not None else server_mode
    assert composed.get('decfloat_round') == expected
    assert composed['database'] == target.database
    assert composed['route_id'] == route.id
    assert reference is None
    # Force private configuration for the completely omitted-setting case too.
    arguments = _route_arguments({**composed, 'timeout': 2}, native)
    config = native.driver_config.get_database(arguments['database'])
    assert config.decfloat_round.value is (
        None if expected in (None, 'NATIVE_DEFAULT')
        else native.DecfloatRound[expected])
    assert (route.configuration, target.configuration) == before


@pytest.mark.parametrize('creating', [False, True])
def test_concurrent_rounding_profiles_do_not_overwrite_each_other(creating):
    def configure(mode):
        route = {'host': 'localhost', 'port': 53050,
                 'database': '/owned/concurrent-rounding.fdb',
                 'user': 'SYSDBA', 'timeout': 3}
        if mode is not None:
            route['decfloat_round'] = mode
        before = dict(route)
        arguments = (_database_create_arguments(
            route, 'localhost/53050:/owned/concurrent-created.fdb',
            {'page_size': 16384}, native) if creating else
            _route_arguments(route, native))
        assert route == before
        return mode, arguments['database']

    with ThreadPoolExecutor(max_workers=8) as executor:
        configured = list(executor.map(configure, MODES * 24))
    identities = {}
    for mode, identity in configured:
        config = native.driver_config.get_database(identity)
        assert config.decfloat_round.value is (
            None if mode in (None, 'NATIVE_DEFAULT') else
            native.DecfloatRound[mode])
        if creating:
            assert config.page_size.value == 16384
        assert identities.setdefault(mode, identity) == identity
    # Every saved preference has a stable private configuration; no mode
    # accidentally reuses a different mode's mutable driver configuration.
    assert len(set(identities.values())) == len(MODES)
