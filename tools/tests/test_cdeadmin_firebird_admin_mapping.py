##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Exact Firebird administrator-mapping command and presentation boundaries."""

import json
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import (
    ADMINISTRATION, _admin_mapping_state,
)
from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_admin_mapping,
)
from pgadmin.cdeadmin.context_menu import resource_context_actions
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine
from pgadmin.cdeadmin.visual_admin import ProviderVisualAdministration


def request(action='SET', name='RDB$ADMIN'):
    return {'resource_kind': 'role', 'operation_id': 'configure_admin_mapping',
            '_provider_route': {'database': 'example.fdb'},
            'target_resource': {'resource_id': 'role:' + name,
                                'resource_kind': 'role', 'display_name': name},
            'draft': {'mapping_action': action}}


@pytest.mark.parametrize('action', ['SET', 'DROP'])
def test_exact_statement(action):
    result = ADMINISTRATION.plan(request(action))
    assert result['command_preview']['statements'][0]['source'] == (
        f'ALTER ROLE "RDB$ADMIN" {action} AUTO ADMIN MAPPING')


@pytest.mark.parametrize('action,name', [
    ('SET', 'rdb$admin'), ('SET', 'READER'), ('SET', 'RDB$ADMIN";'),
    ('DROP GLOBAL', 'RDB$ADMIN'), ('', 'RDB$ADMIN'), (True, 'RDB$ADMIN'),
    (['SET'], 'RDB$ADMIN'),
])
def test_no_target_or_command_substitution(action, name):
    with pytest.raises(RelationalClientError):
        ADMINISTRATION.plan(request(action, name))


@pytest.mark.parametrize('name,valid', [
    ('RDB$ADMIN', True), ('READER', False),
])
def test_public_validation_enforces_target_restriction(name, valid):
    context = SimpleNamespace(
        endpoint_id='test', mode='legacy_native',
        runtime_verification_state='verified',
        verified_runtime_family='firebird', declared_runtime_family='firebird',
        effective_permissions=frozenset({'data_read', 'administer'}))
    client = SimpleNamespace(
        visual_admin_catalog=ADMINISTRATION.catalog,
        supports_admin_operation=ADMINISTRATION.supports,
        validate_admin_operation=ADMINISTRATION.validate,
        plan_admin_operation=ADMINISTRATION.plan,
        apply_admin_operation=lambda value: {'accepted': True})
    visual = ProviderVisualAdministration(context, Mock(), 'firebird',
                                          '5.0.4', client)
    result = visual.validate(request(name=name))
    assert result['valid'] is valid
    if not valid:
        assert any(error['code'] == 'operation_target_not_supported'
                   for error in result['errors'])


@pytest.mark.parametrize('name,system,visible', [
    ('RDB$ADMIN', True, True), ('READER', False, False),
    ('OTHER_SYSTEM', True, False),
])
def test_popup_exempts_only_explicit_named_operation(name, system, visible):
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    for item in catalog['objects']:
        for operation in item['operations']:
            operation['execution_available'] = True
    target = request(name=name)['target_resource']
    target['extensions'] = {'firebird': {'native': {'system_object': system}}}
    commands = resource_context_actions({'engine_id': 'firebird'}, target,
                                        catalog)
    ids = {item['command_id'] for item in commands}
    assert ('resource.firebird.role.configure_admin_mapping' in ids) == visible
    if system:
        assert 'resource.firebird.role.drop' not in ids
        assert 'resource.firebird.role.alter' not in ids


@pytest.mark.parametrize('row,canonical', [
    (None, False),
    (('P', 'Win_Sspi', None, 'Predefined_Group', 'DOMAIN_ANY_RID_ADMINS',
      1, 'RDB$ADMIN'), True),
    (('P', 'OTHER', None, 'Predefined_Group', 'DOMAIN_ANY_RID_ADMINS',
      1, 'RDB$ADMIN'), False),
])
def test_inspection_separates_presence_and_definition(row, canonical):
    cursor = Mock()
    cursor.fetchone.return_value = row
    result = _admin_mapping_state(cursor)
    assert result['available'] is True
    assert result['present'] == (row is not None)
    assert result['canonical'] is canonical
    assert result['windows_authentication_verified'] is False


def test_unavailable_catalog_is_not_reported_as_absent():
    cursor = Mock()
    cursor.execute.side_effect = RuntimeError('denied')
    result = _admin_mapping_state(cursor)
    assert result['available'] is False
    assert 'present' not in result


def evidence():
    statements = {}
    for action in ('SET', 'DROP'):
        statements[action] = (
            f'ALTER ROLE "RDB$ADMIN" {action} AUTO ADMIN MAPPING')
    return {
        'status': 'passed', 'engine_version': '5.0.4', 'failures': [],
        'fixture_removed': True, 'temporary_user_removed': True,
        'checks': ['set-rollback-restores-absence',
                   'set-commit-and-replace-idempotent',
                   'system-role-inspector-no-fabricated-create',
                   'drop-rollback-restores-mapping',
                   'drop-commit-removes-mapping',
                   'absent-drop-native-error-no-state-change',
                   'uncommitted-close-discards-mapping',
                   'unprivileged-SET-denied', 'unprivileged-DROP-denied'],
        'statements': statements,
    }


def contract():
    return json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                       'firebird_dialect_5_0_4.json').read_text())


def test_valid_evidence_preserves_prior_tasks_and_is_idempotent():
    document = contract()
    result = supplement_admin_mapping(document, evidence(), 'a' * 64,
                                      'live.json')
    assert len(result['task_templates']) == 62
    assert all(task in result['task_templates'] for task in
               document['task_templates'] if not task['task_id'].endswith(
                   'configure_admin_mapping'))
    assert result == supplement_admin_mapping(result, evidence(), 'a' * 64,
                                              'live.json')


@pytest.mark.parametrize('field,value', [
    ('status', 'failed'), ('fixture_removed', False),
    ('temporary_user_removed', False),
    ('engine_version', '4.0.6'), ('checks', []), ('statements', {}),
    ('failures', [{'error': 'denied'}]),
])
def test_unclean_evidence_cannot_activate_task(field, value):
    document = contract()
    record = evidence()
    record[field] = value
    with pytest.raises(ValueError):
        supplement_admin_mapping(document, record, 'a' * 64,
                                 'live.json')
