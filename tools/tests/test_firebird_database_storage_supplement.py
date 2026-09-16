"""Database storage activation requires each native proof, not a pass flag."""

import copy
import json

import pytest

from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_database_storage,
)
from pgadmin.cdeadmin.providers.firebird.database_storage import OPERATIONS


def inputs():
    document = json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                           'firebird_dialect_5_0_4.json').read_text())
    checks = [{'case': f'files-{multiple}-{explicit}',
               'rollback_verified': True, 'duplicate_rejected': True,
               'data_access_verified': True, 'physical_files_verified': True}
              for multiple in (False, True) for explicit in (False, True)]
    codes = {'duplicate-file-' + item['case'].removeprefix('files-'): 336068774
             for item in checks}
    checks += [{'case': f'backup-{named}', 'mode_rollback_verified': True,
                'physical_delta_verified': True,
                'delta_changes_retained': True}
               for named in (False, True)]
    for suffix in ('False', 'True'):
        codes.update({'already-in-backup-' + suffix: 336068825,
                      'already-out-of-backup-' + suffix: 336068826,
                      'missing-difference-' + suffix: 336068823})
    codes.update({'duplicate-difference': 336068824,
                  'remove-difference-during-backup': 335544832})
    checks += [
        {'case': 'database-alter-permission',
         'permission_admissions': sorted(OPERATIONS),
         'permission_denials': [{'operation': operation,
                                'native_status_codes': [335544352]}
                                for operation in sorted(OPERATIONS)]},
        {'case': 'busy-file-extension', 'native_status_codes': [335544453],
         'other_attachment_preserved': True,
         'explicit_maintenance_retry_passed': True},
        {'case': 'difference-file-safety', 'known_secondary_protected': True,
         'existing_primary_collision_protected': True,
         'caller_pending_work_preserved': True,
         'caller_rollback_verified': True},
        {'case': 'repeated-extension-False',
         'reopened_before_extension': True},
        {'case': 'repeated-extension-True', 'native_status_codes': [335545273],
         'native_backup_extension_defect_reproduced': True,
         'failure_retained_without_automatic_retry': True},
        {'case': 'backup-then-near-extension',
         'backup_before_file_extension': True}]
    return document, {
        'schema': 'cdeadmin.firebird-database-storage.v1',
        'executor': 'provider-native', 'engine_version': '5.0.4',
        'complete': True, 'failures': [], 'owned_container_removed': True,
        'checks': checks,
        'database_cleanup': [{'case': item['case'], 'removed': True}
                             for item in checks],
        'native_denials': [{'case': key, 'native_status_codes': [value]}
                           for key, value in codes.items()],
        'task_evidence': {item['task_id']: {
            'live_execution': 'passed', 'statements': item['statements']}
            for item in document['task_templates'] if item['task_id'] in {
                'visual_admin.database.' + operation
                for operation in OPERATIONS}},
    }


def test_storage_supplement_is_idempotent_and_preserves_other_tasks():
    document, evidence = inputs()
    before = copy.deepcopy(document)
    result = supplement_database_storage(
        document, evidence, 'a' * 64, 'x.json')
    assert document == before
    assert len(result['task_templates']) == len(document['task_templates'])
    assert all(item in result['task_templates'] for item in
               document['task_templates'] if item['task_id'] not in
               evidence['task_evidence'])
    assert result == supplement_database_storage(result, evidence,
                                                 'a' * 64, 'x.json')


@pytest.mark.parametrize('change', [
    {'complete': False}, {'owned_container_removed': False},
    {'executor': 'compiler-native'}, {'engine_version': '5.0.3'},
    {'schema': 'unknown'}, {'checks': []}, {'failures': ['failed']},
    {'task_evidence': {}}, {'database_cleanup': []}, {'native_denials': []},
])
def test_missing_native_proof_cannot_activate_storage_tasks(change):
    document, evidence = inputs()
    evidence.update(change)
    with pytest.raises(ValueError):
        supplement_database_storage(document, evidence, 'a' * 64, 'x.json')


@pytest.mark.parametrize('case,key', [
    ('files-False-False', 'rollback_verified'),
    ('files-True-True', 'physical_files_verified'),
    ('backup-False', 'mode_rollback_verified'),
    ('backup-True', 'delta_changes_retained'),
    ('database-alter-permission', 'permission_denials'),
    ('database-alter-permission', 'permission_admissions'),
    ('busy-file-extension', 'other_attachment_preserved'),
    ('busy-file-extension', 'native_status_codes'),
    ('difference-file-safety', 'known_secondary_protected'),
    ('difference-file-safety', 'existing_primary_collision_protected'),
    ('difference-file-safety', 'caller_pending_work_preserved'),
    ('difference-file-safety', 'caller_rollback_verified'),
    ('repeated-extension-False', 'reopened_before_extension'),
    ('repeated-extension-True', 'native_status_codes'),
    ('repeated-extension-True', 'failure_retained_without_automatic_retry'),
    ('backup-then-near-extension', 'backup_before_file_extension'),
])
def test_individual_native_safety_proofs_are_mandatory(case, key):
    document, evidence = inputs()
    next(item for item in evidence['checks'] if item['case'] == case).pop(key)
    with pytest.raises(ValueError):
        supplement_database_storage(document, evidence, 'a' * 64, 'x.json')
