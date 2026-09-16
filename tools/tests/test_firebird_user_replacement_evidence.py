"""Account activation needs authentication, rollback and redaction proof."""
import copy
import json

import pytest

from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_user_replacement,
)


def inputs():
    document = json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                           'firebird_dialect_5_0_4.json').read_text())
    return document, {
        'schema': 'cdeadmin.firebird-views.v1', 'engine_version': '5.0.4',
        'complete': True, 'owned_container_removed': True, 'failures': [],
        'user_replacement_checks': [
            {'case': case, 'rollback_commit_verified': True, field: True}
            for case, field in (
                ('creation', 'authentication_verified'),
                ('alteration', 'rotation_state_admin_tags_verified'),
                ('recreation', 'authentication_and_name_grants_verified'))
        ] + [{'case': 'permission-denials',
              'other_user_authentication_preserved': True,
              'denials': [{'operation': op, 'native_status_codes': [335544352]}
                          for op in ('create_or_alter', 'recreate')]}],
        'user_replacement_task_evidence': {
            'visual_admin.user.' + op: {
                'live_execution': 'passed', 'credentials_redacted': True,
                'statements': [command + ' USER U PASSWORD <redacted>']}
            for op, command in (('create_or_alter', 'CREATE OR ALTER'),
                                ('recreate', 'RECREATE'))},
    }


def merge(document, evidence):
    return supplement_user_replacement(document, evidence, 'a' * 64,
                                       'owned.json')


def test_nonmutating_idempotent_merge():
    document, evidence = inputs()
    before = copy.deepcopy(document)
    result = merge(document, evidence)
    assert document == before
    assert result == merge(result, evidence)


@pytest.mark.parametrize('change', [
    {'complete': False}, {'engine_version': '5.0.3'},
    {'failures': ['failure']},
    {'owned_container_removed': False}, {'user_replacement_checks': []},
    {'user_replacement_task_evidence': {}},
])
def test_incomplete_evidence(change):
    document, evidence = inputs()
    evidence.update(change)
    with pytest.raises(ValueError):
        merge(document, evidence)


@pytest.mark.parametrize('index,field', [
    (0, 'rollback_commit_verified'), (0, 'authentication_verified'),
    (1, 'rollback_commit_verified'), (1, 'rotation_state_admin_tags_verified'),
    (2, 'rollback_commit_verified'),
    (2, 'authentication_and_name_grants_verified'),
    (3, 'other_user_authentication_preserved'), (3, 'denials'),
])
def test_required_observation(index, field):
    document, evidence = inputs()
    del evidence['user_replacement_checks'][index][field]
    with pytest.raises(ValueError):
        merge(document, evidence)


@pytest.mark.parametrize('op', ['create_or_alter', 'recreate'])
def test_credential_redaction_required(op):
    document, evidence = inputs()
    evidence['user_replacement_task_evidence'][
        'visual_admin.user.' + op]['credentials_redacted'] = False
    with pytest.raises(ValueError, match='redaction'):
        merge(document, evidence)


@pytest.mark.parametrize('statements', [None, [], [''], [1], ['a', 'b']])
def test_single_statement_required(statements):
    document, evidence = inputs()
    evidence['user_replacement_task_evidence'][
        'visual_admin.user.recreate']['statements'] = statements
    with pytest.raises(ValueError, match='single native statement'):
        merge(document, evidence)
