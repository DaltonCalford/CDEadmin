"""Shadow activation fails closed without native filesystem/security proof."""

import copy
import json

import pytest

from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_shadows,
)


def inputs():
    document = json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                           'firebird_dialect_5_0_4.json').read_text())
    checks = [{'case': f'{mode}-{conditional}-{multiple}-{preserve}',
               'create_rollback': True, 'drop_rollback': True,
               'filesystem_verified': True, 'metadata_replay_verified': True,
               'provider_catalog_verified': True}
              for mode in ('AUTO', 'MANUAL')
              for conditional in (False, True)
              for multiple in (False, True)
              for preserve in (False, True)]
    checks += [{'case': name} for name in (
        'maximum-shadow-number', 'duplicate-number',
        'conflicting-server-paths', 'native-parser-boundaries',
        'database-alter-permission')]
    codes = {
        'duplicate-number': 336068773, 'same-database': 336068774,
        'existing-shadow-file': 336068774, 'zero-number': 335544712,
        'large-number': 335544699, 'negative-length': 335544634,
        'large-length': 335544634, 'missing-file-start': 335544632,
    }
    return document, {
        'schema': 'cdeadmin.firebird-shadows.v1', 'engine_version': '5.0.4',
        'complete': True, 'failures': [], 'owned_container_removed': True,
        'checks': checks,
        'task_evidence': {item['task_id']: {
            'live_execution': 'passed', 'statements': item['statements']}
            for item in document['task_templates']
            if item['task_id'].startswith('visual_admin.shadow.')},
        'native_denials': [{'case': key, 'native_status_codes': [value]}
                           for key, value in codes.items()],
        'permission_denials': [{'operation': operation,
                               'native_status_codes': [335544352]}
                               for operation in ('create', 'drop')],
        'permission_admissions': ['create', 'drop'],
        'storage_catalog': {
            'backup_transition_verified': True, 'rollback_verified': True,
            'workspace_normalization_verified': True},
        'filename_safety': {
            'exact_catalog_path': True, 'provider_delete_blocked': True,
            'preserve_file_verified': True,
            'native_trimmed_path_deleted': True,
            'native_exact_path_retained': True},
    }


def test_shadow_supplement_is_idempotent_and_preserves_other_tasks():
    document, evidence = inputs()
    original = copy.deepcopy(document)
    result = supplement_shadows(document, evidence, 'a' * 64, 'owned.json')
    assert document == original
    assert len(result['task_templates']) == len(document['task_templates'])
    assert all(item in result['task_templates'] for item in
               document['task_templates'] if item['task_id'] not in
               evidence['task_evidence'])
    assert result == supplement_shadows(result, evidence, 'a' * 64,
                                        'owned.json')


@pytest.mark.parametrize('change', [
    {'complete': False}, {'owned_container_removed': False},
    {'engine_version': '5.0.3'}, {'schema': 'unknown'}, {'checks': []},
    {'failures': ['failed']}, {'task_evidence': {}},
])
def test_incomplete_evidence_cannot_activate_shadow_tasks(change):
    document, evidence = inputs()
    evidence.update(change)
    with pytest.raises(ValueError, match='evidence is incomplete'):
        supplement_shadows(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('field', [
    'create_rollback', 'drop_rollback', 'filesystem_verified',
    'metadata_replay_verified', 'provider_catalog_verified'])
@pytest.mark.parametrize('value', [False, None, 'true', 1])
def test_each_native_lifecycle_proof_is_required(field, value):
    document, evidence = inputs()
    evidence['checks'][0][field] = value
    with pytest.raises(ValueError, match='transaction proof missing'):
        supplement_shadows(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('key', ['native_denials', 'permission_denials'])
@pytest.mark.parametrize('change', ['missing', 'wrong-status', 'duplicate'])
def test_denials_require_exact_operations_and_status_codes(key, change):
    document, evidence = inputs()
    if change == 'missing':
        evidence[key] = []
    elif change == 'wrong-status':
        evidence[key][0]['native_status_codes'] = [335544569]
    else:
        evidence[key][0] = evidence[key][1]
    with pytest.raises(ValueError, match='proof missing'):
        supplement_shadows(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('admissions', [[], ['create'], ['drop'],
                                        ['create', 'create']])
def test_permission_success_requires_both_create_and_drop(admissions):
    document, evidence = inputs()
    evidence['permission_admissions'] = admissions
    with pytest.raises(ValueError, match='permission proof missing'):
        supplement_shadows(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('key', [
    'backup_transition_verified', 'rollback_verified',
    'workspace_normalization_verified'])
def test_storage_catalog_proof_is_required(key):
    document, evidence = inputs()
    evidence['storage_catalog'][key] = False
    with pytest.raises(ValueError, match='workspace proof missing'):
        supplement_shadows(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('key', [
    'exact_catalog_path', 'provider_delete_blocked', 'preserve_file_verified',
    'native_trimmed_path_deleted', 'native_exact_path_retained'])
def test_filename_safety_proof_is_required(key):
    document, evidence = inputs()
    evidence['filename_safety'][key] = False
    with pytest.raises(ValueError, match='filename safety proof missing'):
        supplement_shadows(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('statements', [None, [], [''], [1], 'SQL'])
def test_native_statement_evidence_is_required(statements):
    document, evidence = inputs()
    evidence['task_evidence']['visual_admin.shadow.create'][
        'statements'] = statements
    with pytest.raises(ValueError, match='lacks native statements'):
        supplement_shadows(document, evidence, 'a' * 64, 'owned.json')
