"""Sequence activation requires native lifecycle and failure observations."""

import copy
import json

import pytest

from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_sequences,
)
from pgadmin.cdeadmin.providers.firebird import sequences


def inputs():
    document = json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                           'firebird_dialect_5_0_4.json').read_text())
    checks = [{'case': f'lifecycle-{step}-{quoted}'}
              for step in (1, -1, -2147483647, 2147483647)
              for quoted in (False, True)]
    checks += [{'case': name} for name in (
        'int64-minimum', 'int64-maximum', 'create-or-alter-versus-recreate',
        'set-current-rollback', 'restart-next-rollback', 'dependency-drop',
        'dependency-recreate', 'concurrent-consumers',
        'parser-increment-boundaries',
        'usage-does-not-authorize-administration')]
    task_ids = {'visual_admin.sequence.' + operation for operation in
                sequences.OPERATIONS - {'inspect'}}
    return document, {
        'schema': 'cdeadmin.firebird-sequences.v1', 'engine_version': '5.0.4',
        'complete': True, 'failures': [], 'owned_container_removed': True,
        'checks': checks,
        'task_evidence': {item['task_id']: {
            'live_execution': 'passed', 'statements': item['statements']}
            for item in document['task_templates']
            if item['task_id'] in task_ids},
        'dependency_denials': [{'operation': operation,
                               'native_status_codes': [335544630]}
                               for operation in ('drop', 'recreate')],
        'parser_denials': [{'increment': value,
                           'native_status_codes': [335544634]}
                           for value in ('-2147483648', '2147483648')],
        'permission_denials': [{'operation': operation,
                               'native_status_codes': [335544352]}
                               for operation in (
            'alter', 'set_current', 'drop', 'recreate')],
        'rollback_observations': [{'operation': operation, 'before': '10',
                                   'after_rollback': '10',
                                   'pending_consumption_rollback': True,
                                   'pending_consumption_commit': (
                                       '102' if operation == 'set_current'
                                       else '100')}
                                  for operation in ('alter', 'set_current')],
        'concurrent_consumption': {'attachments': 4, 'unique_values': 200,
                                   'rolled_back_consumption_retained': True},
    }


def test_sequence_activation_is_idempotent_and_preserves_other_tasks():
    document, evidence = inputs()
    original = copy.deepcopy(document)
    result = supplement_sequences(document, evidence, 'a' * 64, 'owned.json')
    assert document == original
    assert len(result['task_templates']) == len(document['task_templates'])
    assert all(item in result['task_templates'] for item in
               document['task_templates'] if item['task_id'] not in
               evidence['task_evidence'])
    assert result == supplement_sequences(result, evidence, 'a' * 64,
                                          'owned.json')


@pytest.mark.parametrize('change', [
    {'complete': False}, {'owned_container_removed': False},
    {'engine_version': '5.0.3'}, {'schema': 'unknown'}, {'checks': []},
    {'failures': ['failed']}, {'task_evidence': {}},
])
def test_incomplete_sequence_evidence_cannot_activate_tasks(change):
    document, evidence = inputs()
    evidence.update(change)
    with pytest.raises(ValueError, match='evidence is incomplete'):
        supplement_sequences(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('key', ['dependency_denials', 'parser_denials',
                                 'permission_denials'])
@pytest.mark.parametrize('change', ['missing', 'wrong-status', 'duplicate'])
def test_denials_require_exact_operations_and_native_codes(key, change):
    document, evidence = inputs()
    if change == 'missing':
        evidence[key] = []
    elif change == 'wrong-status':
        evidence[key][0]['native_status_codes'] = [335544569]
    else:
        evidence[key][0] = evidence[key][1]
    with pytest.raises(ValueError, match='proof missing'):
        supplement_sequences(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('change', [
    'missing', 'wrong-result', 'duplicate', 'local-rollback', 'local-commit'])
def test_assignment_rollback_requires_both_current_and_next_proofs(change):
    document, evidence = inputs()
    if change == 'missing':
        evidence['rollback_observations'] = []
    elif change == 'wrong-result':
        evidence['rollback_observations'][0]['after_rollback'] = '100'
    elif change == 'local-rollback':
        evidence['rollback_observations'][0][
            'pending_consumption_rollback'] = False
    elif change == 'local-commit':
        evidence['rollback_observations'][0][
            'pending_consumption_commit'] = '10'
    else:
        evidence['rollback_observations'][0] = (
            evidence['rollback_observations'][1])
    with pytest.raises(ValueError, match='rollback proof missing'):
        supplement_sequences(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('field,value', [('attachments', 1),
                                         ('unique_values', 199),
                                         ('rolled_back_consumption_retained',
                                         False)])
def test_concurrent_consumption_must_not_be_rolled_back(field, value):
    document, evidence = inputs()
    evidence['concurrent_consumption'][field] = value
    with pytest.raises(ValueError, match='consumption proof missing'):
        supplement_sequences(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('statements', [None, [], [''], [1], 'SQL'])
def test_task_statement_proof_is_required(statements):
    document, evidence = inputs()
    evidence['task_evidence']['visual_admin.sequence.create'][
        'statements'] = statements
    with pytest.raises(ValueError, match='lacks native statements'):
        supplement_sequences(document, evidence, 'a' * 64, 'owned.json')
