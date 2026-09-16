"""Exception activation requires native lifecycle and denial evidence."""
import copy
import json

import pytest

from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_exceptions,
)


def inputs():
    document = json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                           'firebird_dialect_5_0_4.json').read_text())
    return document, {
        'schema': 'cdeadmin.firebird-views.v1', 'engine_version': '5.0.4',
        'complete': True, 'owned_container_removed': True, 'failures': [],
        'exception_checks': [
            {'case': 'lifecycle-' + name, 'rollback_commit_verified': True,
             'grant_semantics_verified': True}
            for name in ('EX_BASE', 'EX"東京')
        ] + [
            {'case': 'dependency-denial', 'native_status_codes': [335544630],
             'original_and_dependent_preserved': True},
            {'case': 'permission-denials', 'exception_unchanged': True,
             'denials': [{'operation': op, 'native_status_codes': [335544352]}
                         for op in ('create_or_alter', 'recreate')]},
        ],
        'exception_task_evidence': {
            'visual_admin.exception.' + op: {
                'live_execution': 'passed', 'statements': [statement]}
            for op, statement in (
                ('create_or_alter', 'CREATE OR ALTER EXCEPTION E \'message\''),
                ('recreate', 'RECREATE EXCEPTION E \'message\''))},
    }


def test_evidence_merge_is_nonmutating_idempotent():
    document, evidence = inputs()
    original = copy.deepcopy(document)
    result = supplement_exceptions(document, evidence, 'a' * 64, 'owned.json')
    assert document == original
    assert result == supplement_exceptions(
        result, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('change', [
    {'complete': False}, {'owned_container_removed': False},
    {'engine_version': '5.0.3'}, {'exception_checks': []},
    {'failures': ['failure']}, {'exception_task_evidence': {}},
])
def test_incomplete_evidence_rejected(change):
    document, evidence = inputs()
    evidence.update(change)
    with pytest.raises(ValueError):
        supplement_exceptions(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('index,field,value', [
    (0, 'rollback_commit_verified', False),
    (0, 'grant_semantics_verified', False),
    (1, 'rollback_commit_verified', False),
    (1, 'grant_semantics_verified', False),
    (2, 'original_and_dependent_preserved', False),
    (2, 'native_status_codes', []),
    (3, 'exception_unchanged', False), (3, 'denials', []),
])
def test_required_native_observation(index, field, value):
    document, evidence = inputs()
    evidence['exception_checks'][index][field] = value
    with pytest.raises(ValueError):
        supplement_exceptions(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('statements', [None, [], [''], ['a', 'b'], [1]])
def test_single_statement_required(statements):
    document, evidence = inputs()
    evidence['exception_task_evidence'][
        'visual_admin.exception.recreate']['statements'] = statements
    with pytest.raises(ValueError, match='single native statement'):
        supplement_exceptions(document, evidence, 'a' * 64, 'owned.json')
