"""Table recreation admission requires native lifecycle and denial proof."""
import copy
import json

import pytest

from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_table_replacement,
)


def inputs():
    document = json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                           'firebird_dialect_5_0_4.json').read_text())
    return document, {
        'schema': 'cdeadmin.firebird-views.v1', 'engine_version': '5.0.4',
        'complete': True, 'owned_container_removed': True, 'failures': [],
        'table_replacement_checks': [
            {'case': case, 'rollback_commit_verified': True,
             'data_and_retention_verified': True,
             'metadata_and_grants_verified': True}
            for case in ('lifecycle-PERSISTENT-None',
                         'lifecycle-GLOBAL TEMPORARY-DELETE ROWS',
                         'lifecycle-GLOBAL TEMPORARY-PRESERVE ROWS')
        ] + [
            {'case': 'dependency-denial', 'native_status_codes': [335544630],
             'original_and_dependent_preserved': True},
            {'case': 'permission-denial', 'table_unchanged': True,
             'native_status_codes': [335544352]},
        ],
        'table_replacement_task_evidence': {
            'visual_admin.table.recreate': {
                'live_execution': 'passed',
                'statements': ['RECREATE TABLE T (ID INTEGER)']}},
    }


def test_merge_is_idempotent_and_nonmutating():
    document, evidence = inputs()
    before = copy.deepcopy(document)
    result = supplement_table_replacement(
        document, evidence, 'a' * 64, 'owned.json')
    assert document == before
    assert result == supplement_table_replacement(
        result, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('change', [
    {'complete': False}, {'owned_container_removed': False},
    {'engine_version': '5.0.3'}, {'table_replacement_checks': []},
    {'failures': ['failure']}, {'table_replacement_task_evidence': {}},
])
def test_incomplete_evidence_rejected(change):
    document, evidence = inputs()
    evidence.update(change)
    with pytest.raises(ValueError):
        supplement_table_replacement(
            document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('index', range(3))
@pytest.mark.parametrize('field', [
    'rollback_commit_verified', 'data_and_retention_verified',
    'metadata_and_grants_verified',
])
def test_each_table_kind_observation_required(index, field):
    document, evidence = inputs()
    del evidence['table_replacement_checks'][index][field]
    with pytest.raises(ValueError, match='lifecycle'):
        supplement_table_replacement(
            document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('index,field,value', [
    (3, 'native_status_codes', []),
    (3, 'original_and_dependent_preserved', False),
    (4, 'native_status_codes', []), (4, 'table_unchanged', False),
])
def test_denial_proof_required(index, field, value):
    document, evidence = inputs()
    evidence['table_replacement_checks'][index][field] = value
    with pytest.raises(ValueError, match='denial'):
        supplement_table_replacement(
            document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('statements', [None, [], [''], ['a', 'b'], [1]])
def test_single_statement_required(statements):
    document, evidence = inputs()
    evidence['table_replacement_task_evidence'][
        'visual_admin.table.recreate']['statements'] = statements
    with pytest.raises(ValueError, match='single native statement'):
        supplement_table_replacement(
            document, evidence, 'a' * 64, 'owned.json')
