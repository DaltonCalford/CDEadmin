##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

from types import SimpleNamespace

from tools.cdeadmin_firebird_ui_orchestrator import gate_command
from pgadmin.cdeadmin.providers.firebird.provider import ADMINISTRATION
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine
from tools.cdeadmin_provider_object_form_gate import _preview_values


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
    for kind in ('grid', 'query'):
        command = gate_command(
            options(kind), 'http://127.0.0.1:5052', 'sample.fdb'
        )
        assert '--profiles' in command
        assert command[command.index('--profiles') + 1] == '/profiles.json'
        assert '--manifest-output' in command
        assert '--endpoint-password-env' not in command


def test_lifecycle_gate_uses_isolated_config_and_firebird_server_scope():
    value = options('lifecycle')
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
            for field in operation.get('form', {}).get('fields', []):
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
    assert values['System privileges'] == '["USER_MANAGEMENT"]'
