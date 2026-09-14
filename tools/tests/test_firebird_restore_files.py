"""Visual restore lists retain the exact ordered native filename array."""
import copy
import json

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine
from pgadmin.cdeadmin.visual_admin.provider import ProviderVisualAdministration


def field():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    database = next(item for item in catalog['objects']
                    if item['resource_kind'] == 'database')
    operation = next(item for item in database['operations']
                     if item['operation_id'] == 'restore_physical')
    return next(item for item in operation['form']['fields']
                if item['field_id'] == 'backup_files')


@pytest.mark.parametrize('serialized', [False, True])
def test_visual_and_legacy_serialized_lists_preserve_exact_order(serialized):
    declaration = field()
    assert declaration['array_editor']['item_kind'] == 'string'
    assert declaration['default'] == []
    paths = ['/owned/東京 full.nbk', '/owned/next incremental.nbk']
    raw = json.dumps(paths) if serialized else copy.deepcopy(paths)
    admitted, error = ProviderVisualAdministration._validate_field(
        declaration, raw)
    assert error is None
    assert admitted == paths
    request = {'engine_id': 'firebird', 'resource_kind': 'database',
               'operation_id': 'restore_physical',
               'draft': {'backup_files': admitted,
                         'restore_database': '/owned/new.fdb'},
               '_provider_route': {'database': '/owned/source.fdb'}}
    assert not ADMINISTRATION.validate(request)['errors']
    plan = ADMINISTRATION.plan(request)
    assert plan['provider_payload']['compiled']['options']['backup_files'] == (
        paths)
    admitted.reverse()
    assert plan['provider_payload']['compiled']['options']['backup_files'] == (
        paths)


@pytest.mark.parametrize('value', [
    [], [''], ['  '], [None], [1], [False], [{}], [['nested']], {},
    'bad JSON'])
def test_invalid_list_is_not_admitted(value):
    admitted, error = ProviderVisualAdministration._validate_field(
        field(), value)
    assert admitted is None
    assert error is not None


@pytest.mark.parametrize('path', [
    '/owned/x\x00.nbk', '/owned/x\n.nbk', '/owned/x\r.nbk'])
def test_native_filename_rules_remain_after_visual_list_admission(path):
    admitted, error = ProviderVisualAdministration._validate_field(
        field(), [path])
    assert error is None
    errors = ADMINISTRATION._validate_firebird_service('restore_physical', {
        'backup_files': admitted, 'restore_database': '/owned/new.fdb'})
    assert any(item['code'] == 'invalid_firebird_backup_files'
               for item in errors)
