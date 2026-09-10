import hashlib
import json

from tools.cdeadmin_reference_engine_object_experience_gate import evaluate


def _write(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reference_gate_accepts_complete_provider_and_aggregate_evidence(
        tmp_path):
    direct = tmp_path / 'direct.json'
    direct_digest = _write(direct, {
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': 'alpha', 'exact_profile': '1.0', 'passed': True,
        'missing_resource_operations': [], 'operation_failures': [],
        'raw_commands_used_for_provider_operations': False,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpreted': False,
    })
    aggregate = tmp_path / 'aggregate.json'
    _write(aggregate, {
        'schema': 'cdeadmin.relational-object-experience-gate.v1',
        'structural_complete': True, 'live_complete': True,
        'structural_failures': [], 'live_failures': [],
        'engines': {'beta': {
            'declaration_ready': True, 'activation_ready': True,
            'blocking_missing_count': 0, 'families': [],
        }},
    })
    index = tmp_path / 'index.json'
    _write(index, {'profiles': {
        'alpha-native': {'path': 'direct.json', 'sha256': direct_digest},
        'beta-native': {'path': 'aggregate.json'},
    }})
    profiles = {
        'alpha-native': {
            'engine_id': 'alpha', 'provider_id': 'a',
            'profile_version': '1.0',
        },
        'beta-native': {
            'engine_id': 'beta', 'provider_id': 'b',
            'profile_version': '2.0',
        },
    }

    result = evaluate(index, profiles=profiles)

    assert result['complete'] is True
    assert result['passed_profile_count'] == 2


def test_reference_gate_fails_closed_on_missing_and_incomplete_evidence(
        tmp_path):
    evidence = tmp_path / 'failed.json'
    _write(evidence, {
        'schema': 'cdeadmin.example-object-gate.v1',
        'structural_complete': True, 'live_complete': False,
        'coverage': {
            'declaration_ready': True, 'activation_ready': False,
            'blocking_missing_count': 0, 'families': [],
        },
    })
    index = tmp_path / 'index.json'
    _write(index, {'profiles': {'alpha-native': 'failed.json'}})
    profiles = {
        'alpha-native': {
            'engine_id': 'alpha', 'provider_id': 'a',
            'profile_version': '1.0',
        },
        'beta-native': {
            'engine_id': 'beta', 'provider_id': 'b',
            'profile_version': '2.0',
        },
    }

    result = evaluate(index, profiles=profiles)

    assert result['complete'] is False
    assert result['failed_profile_count'] == 2
    assert 'beta-native:evidence-missing' in result['failures']
    assert 'alpha-native:live-gate-failed' in result['failures']
    assert 'alpha-native:activation-not-ready' in result['failures']
