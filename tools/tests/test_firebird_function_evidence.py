"""Function activation requires execution and security observations too."""
import copy

import pytest

from tools.tests.test_firebird_exception_evidence import (
    inputs as exception_inputs,
)
from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    supplement_functions,
)


def inputs():
    document, old = exception_inputs()
    evidence = {key: value for key, value in old.items()
                if not key.startswith('exception_')}
    checks = copy.deepcopy(old['exception_checks'])
    for index, name in enumerate(('F_BASE', 'F"東京')):
        checks[index]['case'] = 'lifecycle-' + name
        checks[index]['execution_and_security_verified'] = True
        checks[index]['deterministic_verified'] = True
    checks[-1]['function_unchanged'] = checks[-1].pop('exception_unchanged')
    evidence['function_checks'] = checks
    evidence['function_task_evidence'] = {
        'visual_admin.function.' + op: {
            'live_execution': 'passed', 'statements': [sql]}
        for op, sql in (
            ('create_or_alter', 'CREATE OR ALTER FUNCTION F RETURNS INTEGER '
             'AS BEGIN RETURN 1; END'),
            ('recreate', 'RECREATE FUNCTION F RETURNS INTEGER '
             'AS BEGIN RETURN 1; END'))}
    return document, evidence


def test_merge_is_idempotent_and_nonmutating():
    document, evidence = inputs()
    original = copy.deepcopy(document)
    result = supplement_functions(document, evidence, 'b' * 64, 'owned.json')
    assert document == original
    assert result == supplement_functions(
        result, evidence, 'b' * 64, 'owned.json')


@pytest.mark.parametrize('change', [
    {'complete': False}, {'failures': ['failure']},
    {'owned_container_removed': False}, {'engine_version': '5.0.3'},
    {'function_checks': []}, {'function_task_evidence': {}},
])
def test_incomplete_native_evidence(change):
    document, evidence = inputs()
    evidence.update(change)
    with pytest.raises(ValueError):
        supplement_functions(document, evidence, 'b' * 64, 'owned.json')


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('field', [
    'rollback_commit_verified', 'grant_semantics_verified',
    'execution_and_security_verified', 'deterministic_verified'])
def test_each_lifecycle_observation_required(index, field):
    document, evidence = inputs()
    evidence['function_checks'][index][field] = False
    with pytest.raises(ValueError, match='lifecycle'):
        supplement_functions(document, evidence, 'b' * 64, 'owned.json')


@pytest.mark.parametrize('index,field,value', [
    (2, 'native_status_codes', []),
    (2, 'original_and_dependent_preserved', False),
    (3, 'function_unchanged', False), (3, 'denials', []),
])
def test_denial_proof_required(index, field, value):
    document, evidence = inputs()
    evidence['function_checks'][index][field] = value
    with pytest.raises(ValueError):
        supplement_functions(document, evidence, 'b' * 64, 'owned.json')
