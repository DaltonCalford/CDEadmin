"""Firebird cache requests are explicit, private and jointly inherited."""

import json
from pathlib import Path

import firebird.driver as native
from firebird.driver.config import DriverConfig
import pytest

import config  # noqa: F401
from pgadmin.cdeadmin.endpoints import EndpointService
from pgadmin.cdeadmin.endpoints.profiles import EndpointRegistrationError
from pgadmin.cdeadmin.providers.firebird.provider import (
    _route_arguments, _database_create_arguments,
)
from pgadmin.cdeadmin.providers.form_contracts import (
    _database_contract, _DATABASE_SPECS,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def route(policy='CUSTOM', pages=256):
    return {'host': 'localhost', 'port': 53050, 'database': 'owned-cache',
            'attachment_cache_policy': policy,
            'attachment_cache_pages': pages}


def contract():
    return _database_contract('firebird-native',
                              _DATABASE_SPECS['firebird-native'])


@pytest.mark.parametrize('creation', [False, True])
@pytest.mark.parametrize('policy,pages,expected', [
    (None, 256, None), ('NATIVE_DEFAULT', 256, None),
    ('CUSTOM', 25, 25), ('CUSTOM', 49, 49), ('CUSTOM', 256, 256),
    ('CUSTOM', 2147483646, 2147483646),
])
def test_cache_maps_to_private_dpb_not_connection_keyword(
        creation, policy, pages, expected):
    source = route(policy, pages)
    args = (_database_create_arguments(
        source, 'localhost/53050:/owned/new.fdb', {}, native) if creation else
        _route_arguments(source, native))
    assert 'cache_size' not in args
    assert 'attachment_cache_pages' not in args
    registered = native.driver_config.get_database(args['database'])
    assert registered is not None
    assert registered.cache_size.value == expected
    assert registered.db_cache_size.value is None
    assert source == route(policy, pages)


@pytest.mark.parametrize('pages', [None, '', '128', 0, 1, 24, -1, True,
                                   False, 128.5, 128.0, 2147483647, [], {}])
@pytest.mark.parametrize('module', [None, native])
def test_invalid_custom_pages_fail_before_connect_or_create(pages, module):
    with pytest.raises(RelationalClientError, match='cache'):
        _route_arguments(route('CUSTOM', pages), module)
    with pytest.raises(RelationalClientError, match='cache'):
        _database_create_arguments(route('CUSTOM', pages), 'owned', {}, module)


@pytest.mark.parametrize('policy', ['', 'custom', 'SERVER_DEFAULT', True,
                                    False, 0, [], {}])
def test_unresolved_or_unknown_cache_policy_is_not_native_default(policy):
    with pytest.raises(RelationalClientError, match='cache'):
        _route_arguments(route(policy), native)


@pytest.mark.parametrize('parent', [128, 256])
@pytest.mark.parametrize('policy', [None, 'SERVER_DEFAULT', 'NATIVE_DEFAULT',
                                    'CUSTOM'])
def test_inheritance_never_keeps_a_stale_child_page_count(parent, policy):
    target = {'attachment_cache_pages': 512}
    if policy is not None:
        target['attachment_cache_policy'] = policy
    copied = dict(target)
    effective = {**route('CUSTOM', parent),
                 **EndpointService._database_route_options(
                     {'form_contract': {'database': contract()}}, target)}
    expected = parent if policy in (None, 'SERVER_DEFAULT') else (
        None if policy == 'NATIVE_DEFAULT' else 512)
    args = _route_arguments(effective, native)
    registered = native.driver_config.get_database(args['database'])
    assert registered.cache_size.value == expected
    assert target == copied


def test_cache_form_contracts_keep_policy_and_pages_separate_from_storage():
    path = Path(__file__).resolve().parents[2] / (
        'web/pgadmin/cdeadmin/providers/firebird/provider_manifest.json')
    from pgadmin.cdeadmin.endpoints.profiles import _connection_fields
    fields = _connection_fields(json.loads(path.read_text())['registration'])
    by_id = {item['field_id']: item for item in fields}
    assert by_id['attachment_cache_policy']['default'] == 'NATIVE_DEFAULT'
    assert by_id['attachment_cache_pages']['minimum'] == 25
    for action in ('define', 'connect', 'edit'):
        fields = {item['field_id']: item for item in
                  contract()['forms'][action]['fields']}
        policy = fields['attachment_cache_policy']
        assert policy['default'] == 'SERVER_DEFAULT'
        assert policy['inherit_server_value'] == 'SERVER_DEFAULT'
        assert policy['inherit_server_fields'] == ['attachment_cache_pages']
        pages = fields['attachment_cache_pages']
        assert pages['required'] and pages['integer']
        assert pages['visible_when'] == {
            'field_id': 'attachment_cache_policy', 'equals': 'CUSTOM'}
        assert 'stored' in pages['help'] and 'SuperServer' in pages['help']


@pytest.mark.parametrize('policy', ['SERVER_DEFAULT', 'NATIVE_DEFAULT'])
def test_inactive_database_page_request_is_neither_required_nor_defaulted(
        policy):
    result = EndpointService._database_form_values(
        {'form_contract': {'database': contract()}}, 'connect',
        {'attachment_cache_policy': policy})
    assert result['attachment_cache_policy'] == policy
    assert 'attachment_cache_pages' not in result


@pytest.mark.parametrize('pages', ['', None, 24, 25.5, True])
def test_database_form_rejects_invalid_active_page_count(pages):
    with pytest.raises(EndpointRegistrationError):
        EndpointService._database_form_values(
            {'form_contract': {'database': contract()}}, 'connect', {
                'attachment_cache_policy': 'CUSTOM',
                'attachment_cache_pages': pages})


@pytest.mark.parametrize('creation', [False, True])
def test_changed_cache_has_private_identity_without_mutating_old_config(
        monkeypatch, creation):
    config = DriverConfig('owned-cache-identity')
    config.db_defaults.database.value = '/unrelated.fdb'
    config.db_defaults.password.value = 'unrelated-secret-canary'
    config.db_defaults.cache_size.value = 1024
    monkeypatch.setattr(native, 'driver_config', config)
    before = (config.db_defaults.get_config(),
              config.server_defaults.get_config())

    def mapped(policy, pages):
        source = route(policy, pages)
        return (_database_create_arguments(
            source, 'localhost/53050:/owned/new.fdb', {}, native) if creation
            else _route_arguments(source, native))

    first = mapped('CUSTOM', 128)
    second = mapped('CUSTOM', 256)
    default = mapped('NATIVE_DEFAULT', 256)
    assert len({args['database'] for args in (first, second, default)}) == 3
    assert mapped('CUSTOM', 128) == first
    assert config.get_database(first['database']).cache_size.value == 128
    assert config.get_database(second['database']).cache_size.value == 256
    assert config.get_database(default['database']).cache_size.value is None
    assert (config.db_defaults.get_config(),
            config.server_defaults.get_config()) == before
