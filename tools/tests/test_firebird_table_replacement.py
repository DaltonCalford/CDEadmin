"""Structured native table recreation is explicit and destructive."""
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine


def request(**change):
    return {'resource_kind': 'table', 'operation_id': 'recreate',
            '_provider_route': {'database': 'owned'},
            'target_resource': {'resource_kind': 'table',
                                'display_name': 'T"東京'},
            'draft': {'confirmation': 'T"東京',
                      'columns': [{'name': 'ID', 'column_mode': 'STORED',
                                   'data_type': 'INTEGER'}],
                      **change}}


@pytest.mark.parametrize('kind,retention,prefix', [
    ('PERSISTENT', None, 'RECREATE TABLE'),
    ('GLOBAL TEMPORARY', 'DELETE ROWS', 'RECREATE GLOBAL TEMPORARY TABLE'),
    ('GLOBAL TEMPORARY', 'PRESERVE ROWS', 'RECREATE GLOBAL TEMPORARY TABLE'),
    ('EXTERNAL', None, 'RECREATE TABLE'),
])
def test_native_kind_forms(kind, retention, prefix):
    values = {'table_type': kind, 'sql_security': 'DEFINER'}
    if retention:
        values['on_commit'] = retention
    if kind == 'EXTERNAL':
        values['external_file'] = '/owned/table.dat'
    req = request(**values)
    assert ADMINISTRATION.validate(req) == {'errors': []}
    plan = ADMINISTRATION.plan(req)
    statements = plan['command_preview']['statements']
    assert len(statements) == 1
    assert statements[0]['source'].startswith(prefix + ' "T""東京"')
    assert 'SQL SECURITY DEFINER' in statements[0]['source']
    if retention:
        assert 'ON COMMIT ' + retention in statements[0]['source']
    if kind == 'EXTERNAL':
        assert "EXTERNAL FILE '/owned/table.dat'" in statements[0]['source']


@pytest.mark.parametrize('values', [
    {'confirmation': 'wrong'}, {'name': 'redirect'}, {'options': {}},
    {'columns': []}, {'columns': None}, {'columns': [1]},
    {'constraints': {}}, {'constraints': [1]},
    {'table_type': 'LOCAL TEMPORARY'},
    {'table_type': 'PERSISTENT', 'on_commit': 'DELETE ROWS'},
    {'table_type': 'GLOBAL TEMPORARY', 'publication': 'ENABLE'},
    {'table_type': 'EXTERNAL'}, {'sql_security': 'INVALID'},
])
def test_invalid_inputs(values):
    assert ADMINISTRATION.validate(request(**values))['errors']


@pytest.mark.parametrize('target', [None, {}, {'resource_kind': 'view'}])
def test_requires_inspected_table(target):
    req = request()
    req['target_resource'] = target
    assert ADMINISTRATION.validate(req)['errors']


@pytest.mark.parametrize('flag', [
    'type', 'nullable', 'default', 'primary_key', 'unique'])
def test_native_columns_reject_ignored_generic_flags(flag):
    req = request()
    req['draft']['columns'][0][flag] = True
    assert ADMINISTRATION.validate(req)['errors']


@pytest.mark.parametrize('name', ['', 'x' * 64, 'bad\x00name', '\ud800'])
def test_invalid_target_names(name):
    req = request(confirmation=name)
    req['target_resource']['display_name'] = name
    assert ADMINISTRATION.validate(req)['errors']


def test_engine_owned_destructive_form():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    table = next(r for r in catalog['objects'] if
                 r['resource_kind'] == 'table')
    action = next(o for o in table['operations'] if
                  o['operation_id'] == 'recreate')
    assert action['mutation_class'] == 'destructive'
    assert action['target_required'] is True
    assert action['confirmation_required'] is True
    fields = {f['field_id']: f for f in action['form']['fields']}
    assert 'name' not in fields
    assert fields['columns']['required'] is True
    assert 'Existing rows' in fields['confirmation']['help']
    assert fields['on_commit']['visible_when']['equals'] == 'GLOBAL TEMPORARY'
    assert fields['external_file']['visible_when']['equals'] == 'EXTERNAL'
