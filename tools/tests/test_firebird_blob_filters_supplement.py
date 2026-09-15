"""BLOB-filter activation requires native lifecycle and invocation proof."""

import copy
import json

import pytest

from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_blob_filters,
)


def inputs():
    document = json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                           'firebird_dialect_5_0_4.json').read_text())
    names = [
        'declaration-comment-drop-commit-rollback', 'registered-mnemonic',
        'catalog-recreation-round-trip', 'unknown-mnemonic',
        'below-subtype-range', 'above-subtype-range', 'unsupported-alter',
        'unsupported-execute-grant', 'duplicate-name',
        'duplicate-subtype-pair',
        'missing-entrypoint', 'missing-module_name',
    ] + ['numeric-subtype-' + str(number)
         for number in (-32768, -1, 0, 1, 32767)]
    checks = [{'case': name} for name in names]
    checks.extend({'case': 'native-filter-' + direction, 'bytes_verified': 148,
                   'small_buffer_segment_continuation': True}
                  for direction in ('read', 'write'))
    checks.extend([
        {'case': 'registered-custom-mnemonic',
         'registered_custom_subtype': -79, 'quoted_unicode_mnemonic': True,
         'native_invocation_verified': True},
        {'case': 'restricted-grant-owner-revoke',
         'denied_without_grant': ['create', 'comment', 'drop'],
         'granted_owner_comment_drop': True,
         'revoke_fresh_attachment_denied': True},
        {'case': 'loaded-filter-survives-declaration-drop',
         'catalog_declaration_removed': True,
         'database_filter_cache_retains_loaded_code': True},
    ])
    tasks = {item['task_id']: {'live_execution': 'passed',
                               'statements': item['statements']}
             for item in document['task_templates']
             if item['task_id'].startswith('visual_admin.blob-filter.')}
    assert len(tasks) == 3
    return document, {
        'schema': 'cdeadmin.firebird-blob-filters.v1',
        'engine_version': '5.0.4', 'complete': True,
        'owned_container_removed': True, 'failures': [],
        'checks': checks, 'task_evidence': tasks,
    }


def test_supplement_preserves_other_tasks_and_is_idempotent():
    document, evidence = inputs()
    original = copy.deepcopy(document)
    result = supplement_blob_filters(document, evidence, 'a' * 64,
                                     'owned-test.json')
    assert document == original
    assert len(result['task_templates']) == len(document['task_templates'])
    assert all(item in result['task_templates'] for item in
               document['task_templates'] if item['task_id'] not in
               evidence['task_evidence'])
    assert result == supplement_blob_filters(result, evidence,
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
        supplement_blob_filters(document, evidence, 'a' * 64, 'test.json')


@pytest.mark.parametrize('statements', [None, [], [''], [1], 'raw SQL'])
def test_missing_native_task_statements_are_rejected(statements):
    document, evidence = inputs()
    evidence['task_evidence']['visual_admin.blob-filter.create'][
        'statements'] = statements
    with pytest.raises(ValueError, match='no native statements'):
        supplement_blob_filters(document, evidence, 'a' * 64, 'test.json')


@pytest.mark.parametrize('case,key', [
    ('native-filter-read', 'bytes_verified'),
    ('native-filter-write', 'small_buffer_segment_continuation'),
    ('restricted-grant-owner-revoke', 'denied_without_grant'),
    ('restricted-grant-owner-revoke', 'granted_owner_comment_drop'),
    ('restricted-grant-owner-revoke', 'revoke_fresh_attachment_denied'),
    ('loaded-filter-survives-declaration-drop', 'catalog_declaration_removed'),
    ('loaded-filter-survives-declaration-drop',
     'database_filter_cache_retains_loaded_code'),
    ('registered-custom-mnemonic', 'registered_custom_subtype'),
    ('registered-custom-mnemonic', 'quoted_unicode_mnemonic'),
    ('registered-custom-mnemonic', 'native_invocation_verified'),
])
def test_label_alone_is_not_sufficient_native_proof(case, key):
    document, evidence = inputs()
    next(item for item in evidence['checks'] if item['case'] == case).pop(key)
    with pytest.raises(ValueError, match='not verified'):
        supplement_blob_filters(document, evidence, 'a' * 64, 'test.json')
