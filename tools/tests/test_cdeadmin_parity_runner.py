##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Parity-ledger and differential-runner tests for CDE-PREP-170."""

from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools.cdeadmin_parity_runner import (
    ActualEngineExecutor,
    ParityError,
    build_dashboard,
    compare_observations,
    evaluate,
    execute_reference_scenario,
    execute_scratchbird_scenario,
    make_evidence,
    validate_delta,
    validate_evidence,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'tools/tests/fixtures/cdeadmin_parity'
NOW = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)


def load(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))


def evidence(side='reference', profile='18.3', observed=None,
             expires=None, authority=None,
             scenario_id='relational_projection'):
    observed = observed or '2026-08-30T12:00:00Z'
    expires = expires or '2026-09-15T12:00:00Z'
    record = {
        'evidence_id': f'ev:{side}:postgresql:{scenario_id}',
        'engine_id': 'postgresql',
        'reference_profile': profile,
        'scenario_id': scenario_id,
        'side': side,
        'observed_at': observed,
        'expires_at': expires,
        'digests': {
            key: 'a' * 64 for key in load(
                'tools/cdeadmin_parity_policy.json'
            )['evidence']['required_digest_fields']
        },
        'runtime_identity': {
            'engine_id': 'postgresql',
            'reference_profile': profile,
            'runtime_artifact_digest': 'b' * 64,
        },
    }
    if authority:
        record['scratchbird_authority'] = authority
    return make_evidence(record)


class FakeExecutor:

    def __init__(self, profile='18.3', digest='b' * 64):
        self.profile = profile
        self.digest = digest

    def runtime_identity(self):
        return {
            'engine_id': 'postgresql',
            'reference_profile': self.profile,
            'runtime_artifact_digest': self.digest,
        }

    def execute_scenario(self, scenario):
        return {'kind': scenario['expected_reference']['observation_kind'],
                'value': 1}


class ParityStructureTests(unittest.TestCase):

    def test_initial_ledger_accounts_for_truthful_not_run_state(self):
        result = evaluate(ROOT, now=NOW)
        self.assertTrue(result['valid'], result['errors'])
        self.assertFalse(result['release_ready'])
        self.assertEqual(25, result['profile_count'])
        self.assertEqual(18, result['scenario_count'])
        dashboard = result['dashboard']
        self.assertEqual(154, dashboard['expected_cells'])
        self.assertEqual(154, dashboard['reference']['not_run'])
        self.assertEqual(
            154, dashboard['scratchbird_emulation']['not_run']
        )
        self.assertEqual(0, dashboard['implemented_parity_cells'])
        self.assertFalse(
            dashboard['refusals_count_as_implemented_parity']
        )
        self.assertTrue(any('25/25' in item
                            for item in result['release_blockers']))

    def test_profiles_executors_and_ledger_use_exact_release_matrix(self):
        corpus = load('tools/cdeadmin_reference_corpus.json')
        executors = load('tools/cdeadmin_actual_engine_executors.json')
        ledger = load(
            'tools/tests/fixtures/cdeadmin_parity/parity_ledger.json'
        )
        profiles = {
            (row['engine_id'], row['reference_profile'])
            for row in corpus['profiles']
        }
        self.assertEqual(25, len(profiles))
        self.assertEqual(profiles, {
            (row['engine_id'], row['reference_profile'])
            for row in executors['executors']
        })
        self.assertEqual(profiles, {
            (row['engine_id'], row['reference_profile'])
            for row in ledger['profile_scopes']
        })
        self.assertTrue(all(
            row['state'] == 'blocked_exact_runtime_not_admitted'
            for row in executors['executors']
        ))

    def test_scenarios_are_cross_model_provider_operations(self):
        catalog = load(
            'tools/tests/fixtures/cdeadmin_parity/scenario_catalog.json'
        )
        self.assertGreaterEqual(len({
            row['model'] for row in catalog['scenarios']
        }), 10)
        self.assertTrue(all(row['applies_to']
                            for row in catalog['scenarios']))
        serialized = json.dumps(catalog)
        self.assertNotIn('/home/', serialized)
        self.assertNotIn('SELECT ', serialized)
        for scenario in catalog['scenarios']:
            for operation in scenario['operations']:
                self.assertEqual(
                    {'operation_key', 'arguments'}, set(operation)
                )

    def test_transaction_scenarios_preserve_authority_boundaries(self):
        catalog = load(
            'tools/tests/fixtures/cdeadmin_parity/scenario_catalog.json'
        )
        transaction_rows = [
            row for row in catalog['scenarios']
            if 'transaction_observation' in row
        ]
        self.assertGreaterEqual(len(transaction_rows), 4)
        for row in transaction_rows:
            boundary = row['transaction_observation']
            self.assertEqual(
                'provider_native_opaque', boundary['reference_authority']
            )
            self.assertEqual(
                'scratchbird_engine_mga', boundary['scratchbird_authority']
            )
            self.assertFalse(boundary['common_runner_interprets_finality'])
        protocol_methods = {
            name for name, value in ActualEngineExecutor.__dict__.items()
            if inspect.isfunction(value) and not name.startswith('__')
        }
        self.assertEqual(
            {'runtime_identity', 'execute_scenario'}, protocol_methods
        )


class TypedComparatorTests(unittest.TestCase):

    def test_equivalent_typed_model_shapes(self):
        values = [
            ('scalar', 7),
            ('table', {'columns': ['id'], 'rows': [[1], [2]]}),
            ('document', [{'id': 'd1', 'payload': {'active': True}}]),
            ('graph', {'nodes': ['n1'], 'edges': []}),
            ('vector', [{'id': 'v1', 'score': 0.75}]),
            ('timeseries', [{'at': '2026-01-01T00:00:00Z', 'value': 1}]),
            ('search', [{'id': 's1', 'score': 1.0}]),
            ('state', {'visible': False}),
            ('metadata', {'fields': [{'name': 'id', 'type': 'integer'}]}),
            ('diagnostic', {'code': 'operation_not_permitted'}),
        ]
        for kind, value in values:
            with self.subTest(kind=kind):
                observation = {'kind': kind, 'value': value}
                self.assertEqual(
                    'equivalent',
                    compare_observations(observation, observation).outcome,
                )

    def test_comparator_rejects_type_coercion_and_model_coercion(self):
        self.assertEqual('mismatch', compare_observations(
            {'kind': 'scalar', 'value': 1},
            {'kind': 'scalar', 'value': 1.0},
        ).outcome)
        self.assertEqual('mismatch', compare_observations(
            {'kind': 'document', 'value': []},
            {'kind': 'table', 'value': []},
        ).outcome)
        self.assertEqual('mismatch', compare_observations(
            {'value': 1}, {'value': 1},
        ).outcome)

    def test_only_bounded_policy_deltas_are_acceptable(self):
        result = compare_observations(
            {'kind': 'scalar', 'value': 1.0},
            {'kind': 'scalar', 'value': 1.001},
            {'class': 'numeric_tolerance', 'bound': 0.01,
             'detail': 'provider numeric precision'},
        )
        self.assertEqual('acceptable_delta', result.outcome)
        self.assertEqual('mismatch', compare_observations(
            {'kind': 'scalar', 'value': 1.0},
            {'kind': 'scalar', 'value': 1.1},
            {'class': 'numeric_tolerance', 'bound': 0.01,
             'detail': 'provider numeric precision'},
        ).outcome)
        self.assertEqual('acceptable_delta', compare_observations(
            {'kind': 'document', 'value': [1, 2]},
            {'kind': 'document', 'value': [2, 1]},
            {'class': 'unordered_collection', 'bound': None,
             'detail': 'unordered result contract'},
        ).outcome)

    def test_manual_or_unbounded_deltas_do_not_count(self):
        policy = load('tools/cdeadmin_parity_policy.json')
        self.assertTrue(validate_delta({
            'class': 'manual_review', 'bound': 'ticket',
            'detail': 'not machine proven',
        }, policy))
        self.assertTrue(validate_delta({
            'class': 'numeric_tolerance', 'bound': None,
            'detail': 'missing bound',
        }, policy))


class EvidenceAndExecutionTests(unittest.TestCase):

    def setUp(self):
        self.policy = load('tools/cdeadmin_parity_policy.json')
        corpus = load('tools/cdeadmin_reference_corpus.json')
        self.profile = next(
            row for row in corpus['profiles']
            if row['engine_id'] == 'postgresql'
        )
        catalog = load(
            'tools/tests/fixtures/cdeadmin_parity/scenario_catalog.json'
        )
        self.scenario = next(
            row for row in catalog['scenarios']
            if row['scenario_id'] == 'relational_projection'
        )

    def test_current_content_addressed_evidence_is_valid(self):
        self.assertEqual([], validate_evidence(
            evidence(), self.profile, self.scenario, self.policy, NOW,
        ))

    def test_wrong_version_and_stale_evidence_fail_closed(self):
        wrong = validate_evidence(
            evidence(profile='17.0'), self.profile, self.scenario,
            self.policy, NOW,
        )
        self.assertTrue(any('wrong version' in item for item in wrong))
        stale = validate_evidence(
            evidence(observed='2026-06-01T00:00:00Z',
                     expires='2026-07-01T00:00:00Z'),
            self.profile, self.scenario, self.policy, NOW,
        )
        self.assertTrue(any('stale or expired' in item for item in stale))

    def test_tampered_or_incomplete_digest_fails_closed(self):
        record = evidence()
        record['digests']['provider'] = 'bad'
        errors = validate_evidence(
            record, self.profile, self.scenario, self.policy, NOW,
        )
        self.assertTrue(any('provider' in item for item in errors))
        self.assertTrue(any('does not match' in item for item in errors))

    def test_evidence_rejects_bad_time_identity_and_missing_fields(self):
        future = evidence(observed='2026-09-01T00:00:00Z')
        errors = validate_evidence(
            future, self.profile, self.scenario, self.policy, NOW,
        )
        self.assertTrue(any('future' in item for item in errors))
        invalid = evidence()
        invalid['observed_at'] = 'not-a-time'
        invalid['runtime_identity']['engine_id'] = 'mysql'
        invalid['runtime_identity']['runtime_artifact_digest'] = 'bad'
        invalid = make_evidence(invalid)
        errors = validate_evidence(
            invalid, self.profile, self.scenario, self.policy, NOW,
        )
        self.assertTrue(any('invalid UTC' in item for item in errors))
        self.assertTrue(any('identity engine' in item for item in errors))
        self.assertTrue(any('artifact digest' in item for item in errors))
        incomplete = evidence()
        incomplete.pop('expires_at')
        self.assertTrue(validate_evidence(
            incomplete, self.profile, self.scenario, self.policy, NOW,
        ))

    def test_reference_execution_requires_admission_and_exact_identity(self):
        blocked = {'state': 'blocked_exact_runtime_not_admitted'}
        with self.assertRaisesRegex(ParityError, 'not admitted'):
            execute_reference_scenario(
                FakeExecutor(), blocked, self.profile, self.scenario
            )
        ready = {'state': 'ready_exact_runtime_admitted'}
        with self.assertRaisesRegex(ParityError, 'wrong reference'):
            execute_reference_scenario(
                FakeExecutor(profile='17.0'), ready, self.profile,
                self.scenario,
            )
        observation = execute_reference_scenario(
            FakeExecutor(), ready, self.profile, self.scenario,
        )
        self.assertEqual('table', observation['kind'])

    def test_scratchbird_execution_is_disabled_pending_handoff(self):
        with self.assertRaisesRegex(ParityError, 'disabled pending handoff'):
            execute_scratchbird_scenario(self.scenario)

    def test_transaction_evidence_requires_exact_mga_assertions(self):
        catalog = load(
            'tools/tests/fixtures/cdeadmin_parity/scenario_catalog.json'
        )
        scenario = next(
            row for row in catalog['scenarios']
            if row['scenario_id'] == 'transaction_rollback_visibility'
        )
        record = evidence(
            side='scratchbird_emulation',
            scenario_id='transaction_rollback_visibility',
        )
        errors = validate_evidence(
            record, self.profile, scenario, self.policy, NOW,
        )
        self.assertTrue(any('authority' in item for item in errors))
        record = evidence(
            side='scratchbird_emulation',
            scenario_id='transaction_rollback_visibility',
            authority={
                'authority_class': 'scratchbird_mga',
                'execution_boundary': 'engine_sblr_uuid',
                'finality_source': 'durable_transaction_inventory',
            },
        )
        self.assertEqual([], validate_evidence(
            record, self.profile, scenario, self.policy, NOW,
        ))

    def test_observed_result_with_current_evidence_is_structurally_valid(self):
        ledger = load(
            'tools/tests/fixtures/cdeadmin_parity/parity_ledger.json'
        )
        reference = evidence()
        scratchbird = evidence(side='scratchbird_emulation')
        ledger['evidence'] = [reference, scratchbird]
        ledger['results'] = [{
            'engine_id': 'postgresql',
            'reference_profile': '18.3',
            'scenario_id': 'relational_projection',
            'reference': {
                'outcome': 'observed',
                'evidence_id': reference['evidence_id'],
                'observation': {'kind': 'table', 'value': []},
            },
            'scratchbird_emulation': {
                'outcome': 'equivalent',
                'evidence_id': scratchbird['evidence_id'],
                'observation': {'kind': 'table', 'value': []},
            },
        }]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'ledger.json'
            path.write_text(json.dumps(ledger), encoding='utf-8')
            result = evaluate(ROOT, path, NOW)
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual(1, result['dashboard']['implemented_parity_cells'])
        self.assertEqual(1, result['dashboard']['reference_complete_cells'])

    def test_malformed_results_fail_closed(self):
        ledger = load(
            'tools/tests/fixtures/cdeadmin_parity/parity_ledger.json'
        )
        ledger['results'] = [{
            'engine_id': 'neo4j',
            'reference_profile': 'wrong',
            'scenario_id': 'relational_projection',
            'reference': {'outcome': 'expected_refusal'},
            'scratchbird_emulation': {
                'outcome': 'acceptable_delta'
            },
            'semantic_delta': {
                'class': 'manual_review', 'bound': 'ticket',
                'detail': 'not evidence',
            },
        }]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'ledger.json'
            path.write_text(json.dumps(ledger), encoding='utf-8')
            result = evaluate(ROOT, path, NOW)
        self.assertFalse(result['valid'])
        serialized = ' '.join(result['errors'])
        self.assertIn('not applicable', serialized)
        self.assertIn('wrong version', serialized)
        self.assertIn('lacks evidence', serialized)
        self.assertIn('cannot count', serialized)

    def test_refusal_is_accounted_but_never_implemented_parity(self):
        ledger = load(
            'tools/tests/fixtures/cdeadmin_parity/parity_ledger.json'
        )
        policy = load('tools/cdeadmin_parity_policy.json')
        corpus = load('tools/cdeadmin_reference_corpus.json')
        catalog = load(
            'tools/tests/fixtures/cdeadmin_parity/scenario_catalog.json'
        )
        profile_map = {row['engine_id']: row for row in corpus['profiles']}
        scenario_map = {
            row['scenario_id']: row for row in catalog['scenarios']
        }
        ledger['results'] = [{
            'engine_id': 'postgresql',
            'reference_profile': '18.3',
            'scenario_id': 'security_destructive_refusal',
            'reference': {'outcome': 'expected_refusal'},
            'scratchbird_emulation': {'outcome': 'refused'},
        }]
        dashboard = build_dashboard(
            ledger, profile_map, scenario_map, policy,
        )
        self.assertEqual(1, dashboard['reference']['expected_refusal'])
        self.assertEqual(1, dashboard['scratchbird_emulation']['refused'])
        self.assertEqual(0, dashboard['implemented_parity_cells'])

    def test_wrong_version_ledger_fails_structural_gate(self):
        ledger = load(
            'tools/tests/fixtures/cdeadmin_parity/parity_ledger.json'
        )
        ledger['profile_scopes'][0]['reference_profile'] = 'wrong'
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'ledger.json'
            path.write_text(json.dumps(ledger), encoding='utf-8')
            result = evaluate(ROOT, path, NOW)
        self.assertFalse(result['valid'])
        self.assertTrue(any('exact profiles' in item
                            for item in result['errors']))


if __name__ == '__main__':
    unittest.main()
