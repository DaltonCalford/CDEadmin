"""Stored creation buffers remain separate from attachment requests."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import firebird.driver as native
from firebird.driver.config import DriverConfig

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird.provider import (
    _database_create_arguments,
)
from pgadmin.cdeadmin.providers.form_contracts import _DATABASE_SPECS
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


@pytest.mark.parametrize('stored', [None, 0, 50, 64, 131072, 2147483646])
@pytest.mark.parametrize('attachment', [None, 128, 256])
def test_stored_override_is_private_and_separate(
        monkeypatch, stored, attachment):
    config = DriverConfig('owned-stored-creation')
    config.db_defaults.db_cache_size.value = 1024
    before = config.db_defaults.get_config()
    monkeypatch.setattr(native, 'driver_config', config)
    route = {'host': 'localhost', 'port': 53050,
             'attachment_cache_policy': (
                 'NATIVE_DEFAULT' if attachment is None else 'CUSTOM'),
             'attachment_cache_pages': attachment}
    options = {} if stored is None else {'stored_page_buffers': stored}
    args = _database_create_arguments(
        route, 'localhost/53050:/owned/new.fdb', options, native)
    private = config.get_database(args['database'])
    assert private.db_cache_size.value == stored
    assert private.cache_size.value == attachment
    assert args['overwrite'] is False
    assert 'db_cache_size' not in args
    assert config.db_defaults.get_config() == before


def request(value):
    return {'resource_kind': 'database', 'operation_id': 'create',
            'target_resource': None,
            'draft': {'name': 'owned', 'stored_page_buffers': value},
            '_provider_route': {'host': 'localhost', 'port': 53050,
                                'database_create_root': '/owned'}}


@pytest.mark.parametrize('value', [-1, 1, 49, 2147483647, True, False,
                                   64.0, 64.5, '64', [], {}])
def test_invalid_values_fail_validation_and_driver_mapping(value):
    errors = ADMINISTRATION.validate(request(value))['errors']
    assert any(item['code'] == 'invalid_firebird_stored_page_buffers'
               for item in errors)
    with pytest.raises(RelationalClientError):
        _database_create_arguments(
            {'host': 'localhost'}, 'localhost:/owned/new.fdb',
            {'stored_page_buffers': value}, native)
    with pytest.raises(RelationalClientError):
        ADMINISTRATION.plan(request(value))


@pytest.mark.parametrize('value', [None, 0, 50, 64, 131072, 2147483646])
def test_confirmed_plan_preserves_omission_and_explicit_zero(value):
    plan = ADMINISTRATION.plan(request(value))
    options = plan['provider_payload']['compiled']['create_options']
    if value is None:
        assert 'stored_page_buffers' not in options
    else:
        assert options['stored_page_buffers'] == value


def test_both_creation_forms_have_the_same_complete_buffer_contract():
    fields = _DATABASE_SPECS['firebird-native']['create_fields']
    connection = next(item for item in fields
                      if item['field_id'] == 'stored_page_buffers')
    catalog = json.loads((Path(__file__).resolve().parents[2] / 'web' /
                          'pgadmin/cdeadmin/visual_admin/'
                          'portfolio_catalog.json').read_text())
    administration = next(
        item for item in catalog['forms']['firebird_database_create']['fields']
        if item['field_id'] == 'stored_page_buffers')
    assert connection == administration
    assert connection['required'] is False
    assert 'default' not in connection
    assert connection['integer'] is True
    assert '131072' in connection['help']
    assert 'attachment' in connection['help']


@pytest.mark.parametrize('value', [None, 0, 50, 64])
def test_apply_forwards_exact_planned_create_options_without_mutating(value):
    plan = ADMINISTRATION.plan(request(value))
    before = json.dumps(plan, sort_keys=True)
    client = Mock()
    client.create_database.return_value = {'driver_returned': True}
    result = ADMINISTRATION.apply(client, plan)
    compiled = plan['provider_payload']['compiled']
    client.create_database.assert_called_once_with(
        {'route': plan['provider_payload']['route'],
         'create_options': compiled['create_options']},
        compiled['database'], 'firebird-create-database')
    assert result['accepted'] is True
    assert result['endpoint_database_target']['database'] == '/owned/owned.fdb'
    assert json.dumps(plan, sort_keys=True) == before
