##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_ui_orchestrator import gate_command
from pgadmin.cdeadmin.providers.firebird.provider import ADMINISTRATION
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine
from tools.cdeadmin_provider_object_form_gate import (
    _enumerate_operations, _preview_values, _workspace_probe,
)


def test_focused_role_probe_traverses_organizational_system_branch():
    class Driver:
        def execute_async_script(self, source, kinds, collect_commands):
            assert "'system-objects'" in source
            assert 'containers.has(kind)' in source
            assert kinds == ['role']
            assert collect_commands is True
            return {'probe': 'recorded'}

    assert _workspace_probe(Driver(), ['role']) == {'probe': 'recorded'}


def test_context_selection_uses_hit_tested_visible_pixels(monkeypatch):
    from tools import cdeadmin_provider_object_form_gate as forms
    driver = Mock()
    driver.execute_script.side_effect = [None, {'x': 149, 'y': 276}]
    wait = SimpleNamespace(until=lambda callback: callback(driver))
    actions = Mock()
    factory = Mock(return_value=actions)
    monkeypatch.setattr(forms, 'ActionBuilder', factory)
    label = object()
    forms._context_click_visible_label(driver, wait, label)
    scripts = [call.args[0] for call in driver.execute_script.call_args_list]
    assert 'scrollIntoView' in scripts[0]
    assert 'overflowX' in scripts[1] and 'overflowY' in scripts[1]
    assert 'elementFromPoint' in scripts[1]
    factory.assert_called_once_with(driver)
    actions.pointer_action.move_to_location.assert_called_once_with(149, 276)
    actions.pointer_action.pointer_down.assert_called_once_with(button=2)
    actions.pointer_action.pointer_up.assert_called_once_with(button=2)
    actions.perform.assert_called_once_with()


def test_form_probe_explicitly_excludes_recursive_context_qualification():
    class Driver:
        def execute_async_script(self, source, kinds, collect_commands):
            assert kinds == ['table']
            assert collect_commands is False
            assert ('context_commands_collected: arguments[1] !== false'
                    in source)
            assert 'Promise.resolve() : walk(database.children_url)' in (
                ' '.join(source.split()))
            return {'context_commands_collected': False}
    assert _workspace_probe(Driver(), ['table'], False) == {
        'context_commands_collected': False}


def test_native_blocked_forms_cannot_disappear_from_qualification_count():
    catalog = {'objects': [{'resource_kind': 'role', 'operations': [
        {'operation_id': 'inspect', 'execution_available': True},
        {'operation_id': 'alter', 'execution_available': False,
         'native_supported': True, 'blockers': ['permission_not_granted']},
        {'operation_id': 'unsupported', 'execution_available': False,
         'native_supported': False},
    ]}]}
    assert [operation['operation_id'] for operation in
            _enumerate_operations(catalog, ['role'])] == [
                'inspect', 'alter', 'unsupported']


def options(kind):
    return SimpleNamespace(
        gate_kind=kind,
        evidence_root='/evidence',
        summary_output='/summary.json',
        manifest_output='/manifest.csv',
        profiles='/profiles.json',
        password_env='FIREBIRD_TEST_PASSWORD',
        browser_binary=None,
        width=1600,
        height=1000,
        timeout=90,
        theme='default',
        font_scale=100,
        resource_kinds=None,
        operation_ids=None,
    )


@pytest.mark.parametrize('kind,filename', [
    ('role', 'cdeadmin_firebird_role_ui_gate.py'),
    ('mapping', 'cdeadmin_firebird_admin_mapping_ui_gate.py'),
    ('mappings', 'cdeadmin_firebird_mappings_ui_gate.py'),
    ('character-metadata', 'cdeadmin_firebird_character_metadata_ui_gate.py'),
    ('external-functions', 'cdeadmin_firebird_external_functions_ui_gate.py'),
    ('blob-filters', 'cdeadmin_firebird_blob_filters_ui_gate.py'),
    ('object-privileges', 'cdeadmin_firebird_object_privileges_ui_gate.py'),
    ('columns', 'cdeadmin_firebird_columns_ui_gate.py'),
    ('table-metadata', 'cdeadmin_firebird_table_metadata_ui_gate.py'),
])
def test_mutation_gate_receives_exact_profile_and_database(kind, filename):
    settings = options(kind)
    settings.database = '/var/lib/firebird/data/sample.fdb'
    command = gate_command(settings, 'http://127.0.0.1:5052', 'sample.fdb')
    assert command[1].endswith(filename)
    assert command[command.index('--profiles') + 1] == '/profiles.json'
    assert command[command.index('--database-path') + 1] == settings.database
    assert command[command.index('--reference-version') + 1] == '5.0.4'
    assert command[command.index('--endpoint-password-env') + 1] == (
        'FIREBIRD_TEST_PASSWORD')


def test_object_gate_uses_exact_firebird_identity_and_secret_reference():
    command = gate_command(options('object'), 'http://127.0.0.1:5052',
                           'sample.fdb')
    assert '--engine-id' in command
    assert command[command.index('--engine-id') + 1] == 'firebird'
    assert command[command.index('--interface-id') + 1] == 'firebird-native'
    assert command[command.index('--reference-version') + 1] == '5.0.4'
    assert command[command.index('--endpoint-password-env') + 1] == (
        'FIREBIRD_TEST_PASSWORD'
    )
    assert '--profiles' not in command


def test_object_gate_forwards_repeatable_focus_filters():
    value = options('object')
    value.resource_kinds = ['view']
    value.operation_ids = ['alter']
    command = gate_command(value, 'http://127.0.0.1:5052', 'sample.fdb')
    assert command[command.index('--resource-kind') + 1] == 'view'
    assert command[command.index('--operation-id') + 1] == 'alter'


def test_data_gates_use_reference_profile_without_secret_argument():
    for kind in ('grid', 'query', 'services', 'backup-history',
                 'logical-volumes'):
        command = gate_command(
            options(kind), 'http://127.0.0.1:5052', 'sample.fdb'
        )
        assert '--profiles' in command
        assert command[command.index('--profiles') + 1] == '/profiles.json'
        assert '--manifest-output' in command
        assert '--endpoint-password-env' not in command
        script_kind = kind.replace('-', '_')
        assert command[1].endswith(
            f'cdeadmin_firebird_{script_kind}_ui_gate.py')


@pytest.mark.parametrize('kind', ['lifecycle', 'inspector-tabs'])
def test_lifecycle_gate_uses_isolated_config_and_firebird_server_scope(kind):
    value = options(kind)
    value.database = '/var/lib/firebird/data/sample.fdb'
    value.host = '127.0.0.1'
    value.firebird_port = 53050
    value.user = 'SYSDBA'
    value.client_library = '/runtime/libfbclient.so.5.0.4'
    command = gate_command(
        value, 'http://127.0.0.1:5052', 'sample.fdb', '/tmp/cdeadmin.db'
    )
    assert command[command.index('--config-db') + 1] == '/tmp/cdeadmin.db'
    assert command[command.index('--database-root') + 1] == (
        '/var/lib/firebird/data'
    )
    assert command[command.index('--firebird-port') + 1] == '53050'
    assert command[command.index('--password-env') + 1] == (
        'FIREBIRD_TEST_PASSWORD'
    )
    assert '--profiles' not in command


def test_properties_gate_uses_exact_database_and_native_client_probe():
    value = options('properties')
    value.database = '/var/lib/firebird/data/sample.fdb'
    value.host = '127.0.0.1'
    value.firebird_port = 53050
    value.user = 'SYSDBA'
    value.client_library = '/runtime/libfbclient.so.5.0.4'
    command = gate_command(
        value, 'http://127.0.0.1:5052', 'sample.fdb', '/tmp/cdeadmin.db'
    )
    assert command[command.index('--database-path') + 1] == (
        '/var/lib/firebird/data/sample.fdb'
    )
    assert command[command.index('--client-library') + 1] == (
        '/runtime/libfbclient.so.5.0.4'
    )
    assert command[command.index('--endpoint-password-env') + 1] == (
        'FIREBIRD_TEST_PASSWORD'
    )


def test_firebird_preview_values_cover_every_required_native_form_field():
    from pgadmin.cdeadmin.visual_admin import ProviderVisualAdministration
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    target = {
        'display_name': 'CDEADMIN_QA_TARGET',
        'extensions': {
            'cdeadmin': {'native_name': 'CDEADMIN_QA_TARGET'},
        },
    }
    missing = []
    for resource in catalog['objects']:
        kind = resource['resource_kind']
        if kind == 'database':
            continue
        for operation in resource.get('operations', []):
            operation_id = operation['operation_id']
            if (kind, operation_id) in {
                    ('table', 'update'), ('table', 'delete')}:
                continue
            values = _preview_values(
                kind, {**operation, 'resource_kind': kind}, target,
                'firebird',
            )
            fields = operation.get('form', {}).get('fields', [])
            draft = {field['field_id']: values.get(
                field['label'], field.get('default')) for field in fields}
            for field in operation.get('form', {}).get('fields', []):
                if not ProviderVisualAdministration._field_active(
                        field, draft):
                    continue
                if field.get('required') and 'default' not in field and (
                        field['label'] not in values):
                    missing.append(
                        f'{kind}.{operation_id}.{field["field_id"]}'
                    )
    assert missing == []


def test_firebird_table_alter_retains_structured_relational_preview():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    operation = next(
        operation
        for resource in catalog['objects']
        if resource['resource_kind'] == 'table'
        for operation in resource['operations']
        if operation['operation_id'] == 'alter'
    )
    values = _preview_values(
        'table', operation,
        {'display_name': 'CUSTOMERS'}, 'firebird',
    )
    assert values['Add columns'] == '[{"name":"ui_note","type":"TEXT"}]'


def test_firebird_role_alter_preview_requests_a_native_change():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    operation = next(
        operation
        for resource in catalog['objects']
        if resource['resource_kind'] == 'role'
        for operation in resource['operations']
        if operation['operation_id'] == 'alter'
    )
    values = _preview_values(
        'role', operation,
        {'display_name': 'CDEADMIN_OPERATOR'}, 'firebird',
    )
    assert values['Replacement system privileges'] == '["USER_MANAGEMENT"]'
