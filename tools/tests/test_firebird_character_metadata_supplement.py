"""Live character-metadata evidence must not replace unrelated task proof."""

import copy
import json

import pytest

from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_character_metadata,
)


def inputs():
    document = json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                           'firebird_dialect_5_0_4.json').read_text())
    names = [f'flags-create-drop-rollback-recreation-{mask}'
             for mask in range(8)] + [
        'collation-comment-set-clear-rollback',
        'character-set-comment-set-clear-rollback',
        'charset-default-commit-rollback', 'inherited-flags',
        'external-implementation', 'numeric-sort', 'inherited-numeric-sort',
        'duplicate-last-wins', 'empty-removes-inherited',
        'invalid-numeric-sort', 'mismatched-character-set',
        'missing-external-implementation', 'missing-same-name-implementation',
        'dependent-column-drop-denied', 'unprivileged-create-denied',
        'unprivileged-default-change-denied',
        'unprivileged-charset-comment-denied',
        'unprivileged-collation-comment-denied', 'unprivileged-drop-denied',
        'granted-create-owner-comment-drop', 'revoked-create-denied',
        'same-name-installed-implementation',
        'default-applies-only-new-columns',
        'invalid-default-preserves-current-ASCII',
        'invalid-default-preserves-current-OWNED_ABSENT']
    tasks = {
        'visual_admin.collation.create':
            'CREATE COLLATION "C" FOR "UTF8" FROM "UNICODE"',
        'visual_admin.collation.drop': 'DROP COLLATION "C"',
        'visual_admin.collation.comment': 'COMMENT ON COLLATION "C" IS NULL',
        'visual_admin.character-set.alter':
            'ALTER CHARACTER SET "UTF8" SET DEFAULT COLLATION "UTF8"',
        'visual_admin.character-set.comment':
            'COMMENT ON CHARACTER SET "UTF8" IS NULL',
    }
    return document, {
        'schema': 'cdeadmin.firebird-character-metadata.v1',
        'engine_version': '5.0.4', 'complete': True,
        'owned_container_removed': True, 'failures': [],
        'checks': [{'case': name, **(
            {'expected_native_rejection': 336068830, 'created': False}
            if name in {'flags-create-drop-rollback-recreation-4',
                        'flags-create-drop-rollback-recreation-5'} else {})}
            for name in names],
        'task_evidence': {key: {'live_execution': 'passed',
                                'statements': [sql]}
                          for key, sql in tasks.items()},
    }


def test_supplement_preserves_other_tasks_and_is_idempotent():
    document, evidence = inputs()
    original = copy.deepcopy(document)
    result = supplement_character_metadata(document, evidence, 'a' * 64,
                                           'owned-test.json')
    assert document == original
    assert {item['task_id'] for item in result['task_templates']} == {
        item['task_id'] for item in document['task_templates']}
    affected = set(evidence['task_evidence'])
    assert all(item in result['task_templates'] for item in
               document['task_templates'] if item['task_id'] not in affected)
    assert result == supplement_character_metadata(result, evidence,
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
        supplement_character_metadata(
            document, evidence, 'a' * 64, 'test.json')


@pytest.mark.parametrize('statements', [None, [], [''], [1], 'raw SQL'])
def test_missing_native_task_statements_are_rejected(statements):
    document, evidence = inputs()
    evidence['task_evidence']['visual_admin.collation.create'][
        'statements'] = statements
    with pytest.raises(ValueError, match='no native statements'):
        supplement_character_metadata(
            document, evidence, 'a' * 64, 'test.json')
