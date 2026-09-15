"""Require isolated native UDF evidence before publishing executable tasks."""

import copy
import json

import pytest

from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_external_functions,
)


def inputs():
    document = json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                           'firebird_dialect_5_0_4.json').read_text())
    names = ['create-alter-comment-drop-rollback-recreation',
             'dependent-view-drop-denied',
             'restricted-lifecycle-execute-grant-revoke',
             'argument-boundary-0', 'argument-boundary-15',
             'blob-return-argument-boundary',
             'missing-module_name', 'missing-entrypoint']
    names += ['declaration-recreation-' + str(index) for index in range(25)]
    names += ['native-mechanism-' + name for name in (
        'REFERENCE', 'NULL', 'DESCRIPTOR', 'RETURN_DESCRIPTOR', 'FREE_IT',
        'DESCRIPTOR_FREE_IT', 'PARAMETER', 'PARAMETER_REFERENCE_NATIVE_LIMIT',
        'CSTRING', 'SCALAR_ARRAY')]
    tasks = {item['task_id']: {'live_execution': 'passed',
                               'statements': item['statements']}
             for item in document['task_templates']
             if item['task_id'].startswith('visual_admin.external-function.')}
    assert len(tasks) == 4
    return document, {
        'schema': 'cdeadmin.firebird-external-functions.v1',
        'engine_version': '5.0.4', 'complete': True,
        'owned_container_removed': True, 'failures': [],
        'checks': [{'case': name} for name in names], 'task_evidence': tasks,
    }


def test_native_supplement_preserves_other_tasks_and_is_idempotent():
    document, evidence = inputs()
    original = copy.deepcopy(document)
    result = supplement_external_functions(document, evidence, 'a' * 64,
                                           'owned-test.json')
    assert document == original
    assert len(result['task_templates']) == len(document['task_templates'])
    assert all(item in result['task_templates'] for item in
               document['task_templates'] if item['task_id'] not in
               evidence['task_evidence'])
    assert result == supplement_external_functions(result, evidence,
                                                   'a' * 64, 'owned-test.json')


@pytest.mark.parametrize('change', [
    {'complete': False}, {'owned_container_removed': False},
    {'engine_version': '5.0.3'}, {'schema': 'unknown'},
    {'failures': ['failed']}, {'checks': []}, {'task_evidence': {}},
])
def test_incomplete_native_evidence_is_never_activated(change):
    document, evidence = inputs()
    evidence.update(change)
    with pytest.raises(ValueError, match='evidence is incomplete'):
        supplement_external_functions(
            document, evidence, 'a' * 64, 'test.json')


@pytest.mark.parametrize('statements', [None, [], [''], [1], 'raw SQL'])
def test_missing_native_task_statements_are_rejected(statements):
    document, evidence = inputs()
    evidence['task_evidence']['visual_admin.external-function.create'][
        'statements'] = statements
    with pytest.raises(ValueError, match='no native statements'):
        supplement_external_functions(
            document, evidence, 'a' * 64, 'test.json')
