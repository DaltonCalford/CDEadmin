"""View activation fails closed when native lifecycle evidence is absent."""
import copy
import json

import pytest

from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_views,
)


def inputs():
    document = json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                           'firebird_dialect_5_0_4.json').read_text())
    checks = [{'case': 'lifecycle-' + name,
               'rollback_commit_verified': True,
               'grant_semantics_verified': True,
               'catalog_columns_verified': True}
              for name in ('V_BASE', 'V"東京')]
    checks.extend([
        {'case': 'dependency-denial', 'native_status_codes': [335544630],
         'original_and_dependent_preserved': True},
        {'case': 'permission-denials', 'view_unchanged': True,
         'denials': [{'operation': op, 'native_status_codes': [335544352]}
                     for op in ('create_or_alter', 'recreate')]},
        {'case': 'ordered-columns-cte', 'ordered_columns_verified': True,
         'cte_verified': True},
        {'case': 'invalid-query-pending-work',
         'native_status_codes': [335544569], 'pending_work_preserved': True,
         'rollback_verified': True},
    ])
    tasks = {item['task_id']: {'live_execution': 'passed',
                               'statements': item['statements']}
             for item in document['task_templates'] if item['task_id'] in {
                 'visual_admin.view.create_or_alter',
                 'visual_admin.view.recreate'}}
    return document, {
        'schema': 'cdeadmin.firebird-views.v1', 'engine_version': '5.0.4',
        'complete': True, 'failures': [], 'owned_container_removed': True,
        'checks': checks, 'task_evidence': tasks,
    }


def test_preserves_existing_tasks_and_is_idempotent():
    document, evidence = inputs()
    original = copy.deepcopy(document)
    result = supplement_views(document, evidence, 'a' * 64, 'owned.json')
    assert document == original
    assert len(result['task_templates']) == len(document['task_templates'])
    assert all(item in result['task_templates'] for item in
               document['task_templates'] if item['task_id'] not in
               evidence['task_evidence'])
    assert result == supplement_views(result, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('change', [
    {'complete': False}, {'owned_container_removed': False},
    {'engine_version': '5.0.3'}, {'schema': 'unknown'}, {'checks': []},
    {'failures': ['failed']}, {'task_evidence': {}},
])
def test_incomplete_evidence(change):
    document, evidence = inputs()
    evidence.update(change)
    with pytest.raises(ValueError, match='evidence is incomplete'):
        supplement_views(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('index,field', [
    (i, field) for i in (0, 1) for field in (
        'rollback_commit_verified', 'grant_semantics_verified',
        'catalog_columns_verified')
] + [(2, 'original_and_dependent_preserved'), (3, 'view_unchanged'),
     (4, 'ordered_columns_verified'), (4, 'cte_verified'),
     (5, 'pending_work_preserved'), (5, 'rollback_verified')])
def test_each_lifecycle_observation_required(index, field):
    document, evidence = inputs()
    evidence['checks'][index][field] = False
    with pytest.raises(ValueError, match='lifecycle proof'):
        supplement_views(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('index', [2, 5])
def test_native_error_required(index):
    document, evidence = inputs()
    evidence['checks'][index]['native_status_codes'] = []
    with pytest.raises(ValueError, match='native denial proof'):
        supplement_views(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('change', ['missing', 'operation', 'code'])
def test_permission_observations_required(change):
    document, evidence = inputs()
    denials = evidence['checks'][3]['denials']
    if change == 'missing':
        denials.pop()
    elif change == 'operation':
        denials[0]['operation'] = 'drop'
    else:
        denials[0]['native_status_codes'] = []
    with pytest.raises(ValueError, match='permission proof'):
        supplement_views(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('statements', [
    None, [], [''], [1], 'sql', ['a', 'b']])
def test_single_native_statement_required(statements):
    document, evidence = inputs()
    evidence['task_evidence']['visual_admin.view.recreate'][
        'statements'] = statements
    with pytest.raises(ValueError, match='single native statement'):
        supplement_views(document, evidence, 'a' * 64, 'owned.json')
