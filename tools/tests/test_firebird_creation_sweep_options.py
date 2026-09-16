"""Creation sweep threshold has exact bounds and no attachment side effects."""

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


def request(value):
    return {'resource_kind': 'database', 'operation_id': 'create',
            'target_resource': None,
            'draft': {'name': 'owned', 'sweep_interval': value},
            '_provider_route': {'host': 'localhost', 'port': 53050,
                                'database_create_root': '/owned'}}


@pytest.mark.parametrize('interval', [None, 0, 1, 20000, 50000, 2147483647])
@pytest.mark.parametrize('stored', [None, 0, 64])
def test_private_sweep_option_does_not_leak_global_defaults(
        monkeypatch, interval, stored):
    config = DriverConfig('owned-creation-sweep')
    config.db_defaults.sweep_interval.value = 30000
    before = config.db_defaults.get_config()
    monkeypatch.setattr(native, 'driver_config', config)
    route = {'host': 'localhost', 'port': 53050,
             'attachment_cache_policy': 'CUSTOM',
             'attachment_cache_pages': 128}
    options = {'stored_page_buffers': stored}
    if interval is not None:
        options['sweep_interval'] = interval
    args = _database_create_arguments(
        route, 'localhost/53050:/owned/new.fdb', options, native)
    private = config.get_database(args['database'])
    assert private.sweep_interval.value == interval
    assert private.db_cache_size.value == stored
    assert private.cache_size.value == 128
    assert args['overwrite'] is False
    assert 'sweep_interval' not in args
    assert config.db_defaults.get_config() == before


@pytest.mark.parametrize('value', [-1, 2147483648, True, False,
                                   20000.0, 1.5, '20000', [], {}])
def test_invalid_interval_is_refused_at_every_boundary(value):
    errors = ADMINISTRATION.validate(request(value))['errors']
    assert any(item['code'] == 'invalid_firebird_sweep_interval'
               for item in errors)
    with pytest.raises(RelationalClientError):
        ADMINISTRATION.plan(request(value))
    with pytest.raises(RelationalClientError):
        _database_create_arguments(
            {'host': 'localhost'}, 'localhost:/owned/new.fdb',
            {'sweep_interval': value}, native)


@pytest.mark.parametrize('value', [None, 0, 1, 20000, 50000, 2147483647])
def test_plan_and_apply_keep_omission_distinct_from_zero(value):
    plan = ADMINISTRATION.plan(request(value))
    before = json.dumps(plan, sort_keys=True)
    compiled = plan['provider_payload']['compiled']
    if value is None:
        assert 'sweep_interval' not in compiled['create_options']
    else:
        assert compiled['create_options']['sweep_interval'] == value
    client = Mock()
    client.create_database.return_value = {'driver_returned': True}
    ADMINISTRATION.apply(client, plan)
    client.create_database.assert_called_once_with(
        {'route': plan['provider_payload']['route'],
         'create_options': compiled['create_options']},
        compiled['database'], 'firebird-create-database')
    assert json.dumps(plan, sort_keys=True) == before


def test_creation_contracts_agree_on_exact_sweep_semantics():
    connection = next(
        item for item in _DATABASE_SPECS['firebird-native']['create_fields']
        if item['field_id'] == 'sweep_interval')
    catalog = json.loads((Path(__file__).resolve().parents[2] / 'web' /
                          'pgadmin/cdeadmin/visual_admin/'
                          'portfolio_catalog.json').read_text())
    administration = next(
        item for item in catalog['forms']['firebird_database_create']['fields']
        if item['field_id'] == 'sweep_interval')
    assert connection == administration
    assert not connection['required']
    assert 'default' not in connection
    assert connection['minimum'] == 0
    assert connection['maximum'] == 2147483647
    assert connection['integer'] is True
    assert 'not manual sweep or garbage collection' in connection['help']
