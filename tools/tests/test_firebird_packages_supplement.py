"""Package activation requires exact native lifecycle/dependency evidence."""

import copy
import json

import pytest

from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_packages,
)
from pgadmin.cdeadmin.providers.firebird import packages


def inputs():
    document = json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                           'firebird_dialect_5_0_4.json').read_text())
    checks = [{'case': 'lifecycle-' + prefix + security,
               'header_body_separation': True,
               'rollback_commit_verified': True}
              for prefix in ('PK_', 'PK"東京_')
              for security in ('INHERIT', 'INVOKER', 'DEFINER')]
    checks += [{'case': 'create-or-alter-versus-recreate'}]
    checks += [{'case': name} for name in (
        'failed-body-borrowed-task-savepoint',
        'failed-body-owned-transaction')]
    checks += [{'case': 'dependency-denial-' + operation}
               for operation in ('alter', 'drop', 'recreate')]
    task_ids = {'visual_admin.package.' + operation for operation in
                packages.OPERATIONS - {'inspect'}}
    tasks = {item['task_id']: {'live_execution': 'passed',
                               'statements': item['statements']}
             for item in document['task_templates']
             if item['task_id'] in task_ids}
    return document, {
        'schema': 'cdeadmin.firebird-packages.v1', 'engine_version': '5.0.4',
        'complete': True, 'failures': [], 'owned_container_removed': True,
        'checks': checks, 'task_evidence': tasks,
        'atomicity_checks': [{
            'borrowed': borrowed, 'pending_work_preserved': True,
            'native_status_codes': [335544569]}
            for borrowed in (True, False)],
        'native_denials': [{'operation': operation,
                           'native_status_codes': [335544630]}
                           for operation in ('alter', 'drop', 'recreate')],
    }


def test_package_activation_preserves_other_tasks_and_is_idempotent():
    document, evidence = inputs()
    original = copy.deepcopy(document)
    result = supplement_packages(document, evidence, 'a' * 64, 'owned.json')
    assert document == original
    assert len(result['task_templates']) == len(document['task_templates'])
    assert all(item in result['task_templates'] for item in
               document['task_templates'] if item['task_id'] not in
               evidence['task_evidence'])
    assert result == supplement_packages(result, evidence, 'a' * 64,
                                         'owned.json')


@pytest.mark.parametrize('change', [
    {'complete': False}, {'owned_container_removed': False},
    {'engine_version': '5.0.3'}, {'schema': 'unknown'}, {'checks': []},
    {'failures': ['failed']}, {'task_evidence': {}},
])
def test_incomplete_package_lifecycle_is_not_activated(change):
    document, evidence = inputs()
    evidence.update(change)
    with pytest.raises(ValueError, match='evidence is incomplete'):
        supplement_packages(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('field', ['header_body_separation',
                                   'rollback_commit_verified'])
def test_missing_transaction_scope_is_rejected(field):
    document, evidence = inputs()
    evidence['checks'][0][field] = False
    with pytest.raises(ValueError, match='transaction proof'):
        supplement_packages(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('statements', [[], None, [''], [1], 'raw SQL'])
def test_package_task_statements_are_required(statements):
    document, evidence = inputs()
    evidence['task_evidence']['visual_admin.package.create'][
        'statements'] = statements
    with pytest.raises(ValueError, match='lacks native statements'):
        supplement_packages(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('change', [
    'missing', 'wrong-operation', 'wrong-status'])
def test_dependency_denials_must_be_real_native_observations(change):
    document, evidence = inputs()
    if change == 'missing':
        evidence['native_denials'] = []
    elif change == 'wrong-operation':
        evidence['native_denials'][0]['operation'] = 'create'
    else:
        evidence['native_denials'][0]['native_status_codes'] = [335544352]
    with pytest.raises(ValueError, match='dependency proof'):
        supplement_packages(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('change', [
    'missing', 'same-owner', 'lost-work', 'wrong-error'])
def test_failed_package_body_cannot_be_qualified_without_atomicity(change):
    document, evidence = inputs()
    if change == 'missing':
        evidence['atomicity_checks'] = []
    elif change == 'same-owner':
        evidence['atomicity_checks'][1]['borrowed'] = True
    elif change == 'lost-work':
        evidence['atomicity_checks'][0]['pending_work_preserved'] = False
    else:
        evidence['atomicity_checks'][0]['native_status_codes'] = [335544352]
    with pytest.raises(ValueError, match='atomicity proof'):
        supplement_packages(document, evidence, 'a' * 64, 'owned.json')
