"""Trigger activation requires native lifecycle and denial evidence."""
import copy
import json

import pytest

from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_triggers,
)


def inputs():
    document = json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                           'firebird_dialect_5_0_4.json').read_text())
    return document, {
        'schema': 'cdeadmin.firebird-views.v1', 'engine_version': '5.0.4',
        'complete': True, 'owned_container_removed': True, 'failures': [],
        'trigger_checks': [
            {'case': 'lifecycle-' + name, 'rollback_commit_verified': True,
             'execution_and_security_verified': True,
             'comment_semantics_verified': True,
             'privilege_semantics_verified': True, 'inactive_verified': True}
            for name in ('relation', 'database', 'ddl')
        ] + [
            {'case': 'permission-denials', 'trigger_unchanged': True,
             'denials': [{'operation': op, 'native_status_codes': [335544352]}
                         for op in ('create_or_alter', 'recreate')]},
        ],
        'trigger_task_evidence': {
            'visual_admin.trigger.' + op: {
                'live_execution': 'passed', 'statements': [statement]}
            for op, statement in (
                ('create_or_alter', 'CREATE OR ALTER TRIGGER T '
                 'INACTIVE ON CONNECT AS BEGIN END'),
                ('recreate', 'RECREATE TRIGGER T '
                 'INACTIVE ON CONNECT AS BEGIN END'))},
    }


def test_evidence_merge_is_nonmutating_idempotent():
    document, evidence = inputs()
    original = copy.deepcopy(document)
    result = supplement_triggers(document, evidence, 'a' * 64, 'owned.json')
    assert document == original
    assert result == supplement_triggers(
        result, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('change', [
    {'complete': False}, {'owned_container_removed': False},
    {'engine_version': '5.0.3'}, {'trigger_checks': []},
    {'failures': ['failure']}, {'trigger_task_evidence': {}},
])
def test_incomplete_evidence_rejected(change):
    document, evidence = inputs()
    evidence.update(change)
    with pytest.raises(ValueError):
        supplement_triggers(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('index,field,value', [
    (0, 'rollback_commit_verified', False),
    (0, 'execution_and_security_verified', False),
    (1, 'rollback_commit_verified', False),
    (1, 'privilege_semantics_verified', False),
    (2, 'comment_semantics_verified', False),
    (2, 'rollback_commit_verified', False),
    (3, 'trigger_unchanged', False), (3, 'denials', []),
])
def test_required_native_observation(index, field, value):
    document, evidence = inputs()
    evidence['trigger_checks'][index][field] = value
    with pytest.raises(ValueError):
        supplement_triggers(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('index', range(3))
@pytest.mark.parametrize('field', [
    'rollback_commit_verified', 'execution_and_security_verified',
    'comment_semantics_verified', 'privilege_semantics_verified',
    'inactive_verified',
])
def test_each_event_class_observation_is_required(index, field):
    document, evidence = inputs()
    del evidence['trigger_checks'][index][field]
    with pytest.raises(ValueError, match='lifecycle'):
        supplement_triggers(document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('statements', [None, [], [''], ['a', 'b'], [1]])
def test_single_statement_required(statements):
    document, evidence = inputs()
    evidence['trigger_task_evidence'][
        'visual_admin.trigger.recreate']['statements'] = statements
    with pytest.raises(ValueError, match='single native statement'):
        supplement_triggers(document, evidence, 'a' * 64, 'owned.json')
