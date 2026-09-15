"""Native scope, namespace, transaction and access activation proofs."""

import copy
import json
from pathlib import Path

import pytest

from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    supplement_object_privileges,
)
from pgadmin.cdeadmin.providers.firebird import object_privileges as bound


def inputs():
    root = Path(__file__).resolve().parents[2]
    document = json.loads((root / 'web/pgadmin/cdeadmin/providers/firebird/'
                          'firebird_dialect_5_0_4.json').read_text())
    targets = {'table': 'T', 'view': 'VW', 'procedure': 'P', 'function': 'F',
               'package': 'PK', 'sequence': 'S', 'exception': 'E',
               'external-function': 'EF', 'column': 'C'}
    cases = {f'{kind}-{name}-{value}' for kind, name in targets.items()
             for value in bound.allowed_privileges(kind)}
    cases |= {
        'table-T-UPDATE,REFERENCES-column-list',
        'view-VW-UPDATE,REFERENCES-column-list', 'table-T"東京-SELECT',
        'column-C.with.dot-UPDATE,REFERENCES',
    } | {f'{kind}-{targets[kind]}-EXECUTE-package-{package}'
         for kind in ('function', 'procedure') for package in ('PK', 'PK2')}
    tasks = {f'visual_admin.{kind}.{action}' for kind in targets
             for action in ('grant', 'revoke')}
    namespaces = []
    for kind, name, prefix in (('function', 'F', 'V_'),
                               ('procedure', 'P', 'CALL_')):
        for package, suffix in ((None, 'GLOBAL'), ('PK', 'PACKAGE'),
                                ('PK2', 'PACKAGE2')):
            namespaces.append({
                'kind': kind, 'name': name, 'package': package,
                'expected_caller': prefix + suffix,
                'observed_callers': [prefix + suffix],
                'authority_path': [package, kind, name] if package else
                [kind, name]})
    evidence = {
        'schema': 'cdeadmin.firebird-object-privileges.v1',
        'engine_version': '5.0.4', 'complete': True, 'failures': [],
        'owned_container_removed': True, 'catalog_namespaces_verified': True,
        'catalog_namespaces': namespaces,
        'checks': [dict(case=name, **{key: True for key in (
            'grant_rollback_verified', 'revoke_rollback_verified',
            'committed_roundtrip_verified', 'native_target_resolution_verified'
        )}) for name in sorted(cases)],
        'permission_checks': [{
            'case': f'effective-{kind}-{state}', 'fresh_attachment': True,
            'accepted': state == 'granted', 'native_status_codes': []
            if state == 'granted' else [335544352],
        } for kind in ('table', 'view', 'procedure', 'function', 'package',
                       'sequence', 'column')
            for state in ('before', 'granted', 'revoked')],
        'task_evidence': {item['task_id']: {
            'live_execution': 'passed', 'statements': item['statements']}
            for item in document['task_templates']
            if item['task_id'] in tasks},
    }
    assert len(evidence['checks']) == 28
    assert len(evidence['task_evidence']) == 18
    return document, evidence


def test_complete_native_evidence_is_idempotent_and_preserves_other_tasks():
    document, evidence = inputs()
    original = copy.deepcopy(document)
    value = supplement_object_privileges(document, evidence, 'a' * 64,
                                         'owned.json')
    assert original == document
    assert len(value['task_templates']) == 94
    assert all(item in value['task_templates'] for item in
               document['task_templates'] if item['task_id'] not in
               evidence['task_evidence'])
    assert supplement_object_privileges(value, evidence, 'a' * 64,
                                        'owned.json') == value


@pytest.mark.parametrize('change', [
    {'complete': False}, {'owned_container_removed': False},
    {'engine_version': '5.0.3'}, {'schema': 'other'},
    {'failures': [{'case': 'failed'}]}, {'checks': []},
    {'task_evidence': {}}, {'catalog_namespaces_verified': False},
    {'catalog_namespaces': []}, {'permission_checks': []},
])
def test_incomplete_or_dirty_native_runs_cannot_activate_tasks(change):
    document, evidence = inputs()
    evidence.update(change)
    with pytest.raises(ValueError):
        supplement_object_privileges(
            document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('key', [
    'grant_rollback_verified', 'revoke_rollback_verified',
    'committed_roundtrip_verified', 'native_target_resolution_verified',
])
def test_each_scoped_transaction_postcondition_is_required(key):
    document, evidence = inputs()
    evidence['checks'][0][key] = False
    with pytest.raises(ValueError, match='transaction/target'):
        supplement_object_privileges(
            document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('change', [
    {'accepted': True}, {'fresh_attachment': False},
    {'native_status_codes': []}, {'native_status_codes': [335544569]},
])
def test_denials_require_fresh_connections_and_native_permission_error(change):
    document, evidence = inputs()
    evidence['permission_checks'][0].update(change)
    with pytest.raises(ValueError, match='native access'):
        supplement_object_privileges(
            document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('change', [
    {'observed_callers': ['Other']}, {'expected_caller': 'Other'},
    {'name': 'Other'}, {'authority_path': ['PK', 'function', 'F']},
])
def test_namespace_observations_must_match_exact_expected_identity(change):
    document, evidence = inputs()
    evidence['catalog_namespaces'][0].update(change)
    with pytest.raises(ValueError, match='namespaces'):
        supplement_object_privileges(
            document, evidence, 'a' * 64, 'owned.json')


@pytest.mark.parametrize('source', [None, [], [''], [False], 'GRANT SELECT'])
def test_task_statements_must_be_nonempty_native_statement_lists(source):
    document, evidence = inputs()
    task = evidence['task_evidence']['visual_admin.table.grant']
    task['statements'] = source
    with pytest.raises(ValueError, match='native statements'):
        supplement_object_privileges(
            document, evidence, 'a' * 64, 'owned.json')
