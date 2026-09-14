##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import tables
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.visual_admin import ProviderVisualAdministration
from tools import cdeadmin_firebird_tables_gate as gate


@pytest.mark.parametrize('first_code', [0, 1])
def test_external_browser_profile_is_private_removed_and_both_runs_collected(
        tmp_path, monkeypatch, first_code):
    visited = []
    secret = 'owned-fixture-password'

    def launch(command, **kwargs):
        profile = Path(command[command.index('--profiles') + 1])
        assert profile.stat().st_mode & 0o777 == 0o600
        saved = json.loads(profile.read_text())['profiles'][0]
        assert saved['password'] == secret
        assert all(secret not in argument for argument in command)
        assert kwargs['env']['CDEADMIN_FIREBIRD_DEMO_PASSWORD'] == secret
        visited.append(profile)
        code = first_code if len(visited) == 1 else 0
        return SimpleNamespace(returncode=code)

    monkeypatch.setattr(gate.subprocess, 'run', launch)
    monkeypatch.setenv('CDEADMIN_FIREBIRD_CLIENT_LIBRARY', '/fixture/lib.so')
    result = {'failures': []}
    gate.external_browser(
        {'database': '/fixture/db', 'port': 3050, 'password': secret},
        SimpleNamespace(output=tmp_path / 'result.json',
                        browser_config_db='/fixture/config.db',
                        desktop_user='fixture@example.invalid'), result)
    assert len(visited) == 2
    assert all(not path.exists() for path in visited)
    assert result['browser_profiles_removed'] is True
    assert len(result['failures']) == int(first_code != 0)


def request(operation, **draft):
    return {'resource_kind': 'table', 'operation_id': operation,
            '_provider_route': {'database': 'example.fdb'},
            'target_resource': {'display_name': 'T', 'display_path': ['T']},
            'draft': draft}


@pytest.mark.parametrize('security', ['INHERIT', 'INVOKER', 'DEFINER'])
@pytest.mark.parametrize('publication', ['DEFAULT', 'ENABLE', 'DISABLE'])
def test_persistent_security_and_publication(security, publication):
    plan = ADMINISTRATION.plan(request(
        'create', name='T', table_type='PERSISTENT', sql_security=security,
        publication=publication, columns=[{
            'name': 'V', 'column_mode': 'STORED', 'data_type': 'INTEGER'}]))
    sql = plan['command_preview']['statements'][0]['source']
    assert sql.startswith('CREATE TABLE "T" (\n  "V" INTEGER\n)')
    assert ('SQL SECURITY' in sql) is (security != 'INHERIT')
    assert ('PUBLICATION' in sql) is (publication != 'DEFAULT')
    if publication != 'DEFAULT':
        assert sql.endswith(publication + ' PUBLICATION')


@pytest.mark.parametrize('security', ['INHERIT', 'INVOKER', 'DEFINER'])
@pytest.mark.parametrize('retention', ['DELETE ROWS', 'PRESERVE ROWS'])
def test_temporary_table_uses_comma_separated_native_options(
        security, retention):
    sql = tables.create('T', ['"V" INTEGER'], {
        'table_type': 'GLOBAL TEMPORARY', 'on_commit': retention,
        'sql_security': security})
    expected = 'CREATE GLOBAL TEMPORARY TABLE "T" (\n  "V" INTEGER\n)'
    expected += ' ON COMMIT ' + retention
    if security != 'INHERIT':
        expected += ', SQL SECURITY ' + security
    assert sql == expected


def test_external_file_is_literal_not_identifier_or_client_upload():
    assert tables.create('a"b', ['V INTEGER'], {
        'table_type': 'EXTERNAL', 'external_file': "/data/it's a file"}) == (
            'CREATE TABLE "a""b" EXTERNAL FILE \'/data/it\'\'s a file\' '
            '(\n  V INTEGER\n)')


def test_external_create_plan_warns_about_native_file_effects():
    plan = ADMINISTRATION.plan(request(
        'create', name='T', table_type='EXTERNAL', external_file='/data/t',
        columns=[{'name': 'V', 'column_mode': 'STORED',
                  'data_type': 'INTEGER'}]))
    assert 'not undone by rollback' in plan['warnings'][0]
    assert 'not the file' in plan['warnings'][0]


@pytest.mark.parametrize('target', [
    {'native': {'relation_type': 2}},
    {'extensions': {'firebird': {'native': {'relation_type': '2'}}}},
])
def test_external_existing_target_warns_using_native_catalog_metadata(target):
    assert tables.warnings({'resource_kind': 'table',
                            'target_resource': target})
    assert tables.warnings({'resource_kind': 'view',
                            'target_resource': target}) == []


@pytest.mark.parametrize('options', [
    {'table_type': 'LOCAL TEMPORARY'}, {'table_type': 'EXTERNAL'},
    {'table_type': 'EXTERNAL', 'external_file': 3},
    {'table_type': 'GLOBAL TEMPORARY', 'publication': 'ENABLE'},
    {'table_type': 'GLOBAL TEMPORARY', 'on_commit': 'DROP'},
    {'external_file': '/data/no'}, {'on_commit': 'DELETE ROWS'},
    {'sql_security': 'OWNER'}, {'publication': True},
])
def test_native_attribute_combinations_reject_invalid_input(options):
    with pytest.raises(RelationalClientError):
        tables.create('T', ['V INTEGER'], options)


@pytest.mark.parametrize('security,clause', [
    ('INHERIT', 'DROP SQL SECURITY'),
    ('INVOKER', 'ALTER SQL SECURITY INVOKER'),
    ('DEFINER', 'ALTER SQL SECURITY DEFINER'),
])
def test_alter_attributes_compile_independent_statements(security, clause):
    plan = ADMINISTRATION.plan(request(
        'alter', sql_security=security, publication='DISABLE'))
    assert [item['source'] for item in
            plan['command_preview']['statements']] == [
                'ALTER TABLE "T" ' + clause,
                'ALTER TABLE "T" DISABLE PUBLICATION']


@pytest.mark.parametrize('qualifier', ['parent', 'schema', 'database'])
def test_firebird_table_rejects_schema_qualifiers(qualifier):
    with pytest.raises(RelationalClientError, match='qualifiers'):
        ADMINISTRATION.plan(request(
            'create', name='T', options={qualifier: 'other'},
            columns=[{'name': 'V', 'type': 'INTEGER'}]))


def test_create_form_shows_only_applicable_native_attributes():
    fields = {item['field_id']: item for item in
              ADMINISTRATION._form('table', 'create')['fields']}
    assert 'parent' not in fields
    for kind in tables.TABLE_TYPES:
        active = {key for key, field in fields.items() if
                  ProviderVisualAdministration._field_active(
                      field, {'table_type': kind})}
        assert ('external_file' in active) is (kind == 'EXTERNAL')
        assert ('on_commit' in active) is (kind == 'GLOBAL TEMPORARY')
        assert ('publication' in active) is (kind != 'GLOBAL TEMPORARY')
