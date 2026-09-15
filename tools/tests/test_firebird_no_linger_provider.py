"""Native no-linger requests must be explicit, isolated, and inheritable."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import firebird.driver as native
import pytest

import config  # noqa: F401 (application bootstrap before pgadmin imports)
from pgadmin.cdeadmin.endpoints import EndpointService
from pgadmin.cdeadmin.providers.firebird.provider import (
    _route_arguments, _database_create_arguments,
)
from pgadmin.cdeadmin.providers.form_contracts import (
    _database_contract, _DATABASE_SPECS,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def route(mode):
    return {'host': 'localhost', 'port': 53050, 'database': 'owned-linger',
            'no_linger': mode}


@pytest.mark.parametrize('mode,expected', [
    (None, None), ('NATIVE_DEFAULT', None), ('SUPPRESS', True),
])
def test_no_linger_uses_private_driver_configuration(mode, expected):
    source = route(mode)
    before = copy.deepcopy(source)
    args = _route_arguments(source, native)
    assert 'no_linger' not in args
    config = native.driver_config.get_database(args['database'])
    assert config.no_linger.value is expected
    for alternative in ('NATIVE_DEFAULT', 'SUPPRESS'):
        other = _route_arguments(route(alternative), native)
        assert (other['database'] == args['database']) is (alternative == mode)
        assert config.no_linger.value is expected
    assert source == before


@pytest.mark.parametrize('value', [
    '', 'suppress', 'SERVER_DEFAULT', 0, 1, True, False, [], {},
])
@pytest.mark.parametrize('module', [None, native])
def test_invalid_linger_policy_is_rejected_before_configuration(value, module):
    with pytest.raises(RelationalClientError, match='linger'):
        _route_arguments(route(value), module)
    with pytest.raises(RelationalClientError, match='linger'):
        _database_create_arguments(route(value), 'localhost:new', {}, module)


def test_creation_does_not_claim_an_existing_cache_attachment_override():
    args = _database_create_arguments(
        route('SUPPRESS'), 'localhost/53050:/owned/new.fdb', {}, native)
    config = native.driver_config.get_database(args['database'])
    assert config.no_linger.value is None


def contract():
    return _database_contract('firebird-native',
                              _DATABASE_SPECS['firebird-native'])


def test_engine_forms_distinguish_inheritance_from_native_default():
    path = Path(__file__).resolve().parents[2] / (
        'web/pgadmin/cdeadmin/providers/firebird/provider_manifest.json')
    from pgadmin.cdeadmin.endpoints.profiles import _connection_fields
    fields = _connection_fields(json.loads(path.read_text())['registration'])
    field = next(item for item in fields if item['field_id'] == 'no_linger')
    assert field['default'] == 'NATIVE_DEFAULT'
    assert {item['value'] for item in field['options']} == {
        'NATIVE_DEFAULT', 'SUPPRESS'}
    for action in ('define', 'connect', 'edit'):
        field = next(item for item in contract()['forms'][action]['fields']
                     if item['field_id'] == 'no_linger')
        assert field['default'] == 'SERVER_DEFAULT'
        assert field['inherit_server_value'] == 'SERVER_DEFAULT'
        assert {item['value'] for item in field['options']} == {
            'NATIVE_DEFAULT', 'SUPPRESS', 'SERVER_DEFAULT'}
        assert 'shared' in field['help']
        assert 'stored' in field['help']
        assert 'SuperClassic' in field['help']


@pytest.mark.parametrize('parent', ['NATIVE_DEFAULT', 'SUPPRESS'])
@pytest.mark.parametrize('target', [
    'SERVER_DEFAULT', 'NATIVE_DEFAULT', 'SUPPRESS',
])
def test_database_override_composes_before_native_mapping(parent, target):
    target_options = {'no_linger': target}
    actual = {**route(parent), **EndpointService._database_route_options(
        {'form_contract': {'database': contract()}}, target_options)}
    expected = parent if target == 'SERVER_DEFAULT' else target
    args = _route_arguments(actual, native)
    config = native.driver_config.get_database(args['database'])
    assert config.no_linger.value is (True if expected == 'SUPPRESS' else None)
    assert target_options == {'no_linger': target}


def test_simultaneous_profiles_do_not_change_each_others_linger_policy():
    def observe(mode):
        for _ in range(120):
            args = _route_arguments(route(mode), native)
            assert native.driver_config.get_database(
                args['database']).no_linger.value is (
                    True if mode == 'SUPPRESS' else None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(observe, ('NATIVE_DEFAULT', 'SUPPRESS')))
