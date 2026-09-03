##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Upstream acceptance-suite tests for CDE-PREP-200."""

from __future__ import annotations

import ast
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tools.cdeadmin_upstream_acceptance import (
    CATALOG,
    EXPECTED_AUTHORITIES,
    EXPECTED_SUITES,
    REFERENCE_CORPUS,
    SCENARIO_CATALOG,
    AcceptanceError,
    canonical_digest,
    definition_errors,
    evaluate_definitions,
    expand_cases,
    load_candidate,
    load_json,
    main,
    make_evidence,
    run_candidate,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / 'tools/tests/fixtures/cdeadmin_upstream_acceptance'
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


class LiveCandidate:
    """In-memory live-candidate double for runner acceptance tests."""

    def __init__(self):
        self.manifest = {
            'schema': 'cdeadmin.upstream-candidate-manifest.v1',
            'candidate_protocol_version': '1.0.0',
            'candidate_id': 'scratchbird-live-candidate-test-double',
            'candidate_class': 'live_upstream_candidate',
            'production_candidate': True,
            'execution_backend': 'scratchbird_native_live',
            'network_enabled': True,
            'authority_invariants': copy.deepcopy(EXPECTED_AUTHORITIES),
            'backend_assertions': {
                'native_scratchbird_backend': True,
                'public_driver_api_only': True,
                'embedded_backend_substitute': False,
                'donor_backend_substitute': False,
                'donor_reference_transport': 'actual_donor_listener',
            },
            'dependencies': {
                'D2_DRIVER': {
                    'state': 'candidate_delivered',
                    'version': '1.0.0-candidate',
                    'artifact_digest': 'a' * 64,
                    'signature_verified': True,
                },
                'D3_CDE': {
                    'state': 'candidate_delivered',
                    'version': '1.0.0-candidate',
                    'artifact_digest': 'b' * 64,
                    'signature_verified': True,
                },
                'D5_EMULATION': {
                    'state': 'candidate_delivered',
                    'version': '1.0.0-candidate',
                    'artifact_digest': 'c' * 64,
                    'signature_verified': True,
                },
            },
        }
        self.status_by_case = {}
        self.body_mutator = None
        self.raise_for = set()
        self.calls = []

    def candidate_manifest(self):
        return copy.deepcopy(self.manifest)

    def execute_case(self, case):
        self.calls.append(case['case_id'])
        if case['case_id'] in self.raise_for:
            raise RuntimeError('candidate failure')
        status = self.status_by_case.get(case['case_id'], 'passed')
        body = {
            'schema': 'cdeadmin.acceptance-case-evidence.v1',
            'case_id': case['case_id'],
            'status': status,
            'observed_at': NOW.isoformat(),
            'expires_at': (NOW + timedelta(days=7)).isoformat(),
            'assertions': {
                assertion: status == 'passed'
                for assertion in case['assertions']
            },
            'runtime_identity': {
                'execution_backend': 'scratchbird_native_live',
                'driver_artifact_digest': 'a' * 64,
            },
            'diagnostics': [] if status == 'passed' else ['upstream failure'],
        }
        if case.get('transaction_sensitive'):
            body['scratchbird_authority'] = copy.deepcopy(
                EXPECTED_AUTHORITIES
            )
        if case['suite_id'] == 'exact_donor_listener_differential':
            body.update({
                'scenario_id': case['scenario_id'],
                'source_packet_digest': case['source_packet_digest'],
                'reference_runtime': {
                    'engine_id': case['engine_id'],
                    'reference_profile': case['reference_profile'],
                    'transport': 'actual_donor_listener',
                    'runtime_artifact_digest': 'd' * 64,
                },
                'scratchbird_runtime': {
                    'engine_id': 'scratchbird',
                    'emulated_engine_id': case['engine_id'],
                    'transport': 'scratchbird_emulation_listener',
                    'runtime_artifact_digest': 'e' * 64,
                },
                'embedded_backend_substitute': False,
                'donor_backend_substitute': False,
            })
        if self.body_mutator:
            self.body_mutator(body, case)
        return make_evidence(body)


class UpstreamAcceptanceTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.catalog = load_json(ROOT / CATALOG)
        cls.reference = load_json(ROOT / REFERENCE_CORPUS)
        cls.scenarios = load_json(ROOT / SCENARIO_CATALOG)
        cls.cases = expand_cases(
            cls.catalog, cls.reference, cls.scenarios
        )

    def test_definitions_cover_all_required_suites_and_cases(self):
        result = evaluate_definitions(ROOT)
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual(7, result['suite_count'])
        self.assertEqual(54, result['fixed_case_count'])
        self.assertEqual(25, result['differential_profile_count'])
        self.assertEqual(18, result['differential_scenario_count'])
        self.assertEqual(154, result['differential_cell_count'])
        self.assertEqual(208, result['expanded_case_count'])
        self.assertFalse(result['production_ready'])

    def test_every_failure_has_an_upstream_contract_and_dependency_owner(self):
        self.assertEqual(208, len(self.cases))
        self.assertEqual(EXPECTED_SUITES, {
            item['suite_id'] for item in self.cases
        })
        for case in self.cases:
            with self.subTest(case=case['case_id']):
                self.assertTrue(case['owner_contract'])
                self.assertTrue(case['owner_search_key'])
                self.assertIn(
                    case['owner_dependency'],
                    {'D2_DRIVER', 'D3_CDE', 'D5_EMULATION'},
                )
                self.assertGreaterEqual(len(case['assertions']), 3)

    def test_exact_donor_matrix_matches_active_25_profile_corpus(self):
        expected = {
            'apache_ignite': '2.17.0', 'cassandra': '5.0.8',
            'clickhouse': '25.12.10.7-stable',
            'cockroachdb': '26.1.3', 'dolt': '1.86.6',
            'duckdb': '1.5.2', 'firebird': '5.0.4',
            'foundationdb': '7.3.77', 'immudb': '1.11.0',
            'influxdb': '3.9.0', 'mariadb': '12.2.2',
            'milvus': '2.6.5', 'mongodb': '8.2.6',
            'mysql': '9.7.0', 'neo4j': '2026.04.0',
            'opensearch': '3.6.0',
            'opensearch_sql_ppl': '3.6.0-sql-ppl',
            'postgresql': '18.3', 'redis': '8.6.2',
            'sqlite': '3.53.0', 'tidb': '8.5.6', 'tikv': '8.5.6',
            'vitess': '23.0.3', 'xtdb': '2.1.0',
            'yugabytedb': '2025.2.2.2',
        }
        self.assertEqual(expected, {
            item['engine_id']: item['reference_profile']
            for item in self.reference['profiles']
        })
        differential = [
            item for item in self.cases
            if item['suite_id'] == 'exact_donor_listener_differential'
        ]
        self.assertEqual(154, len(differential))
        self.assertEqual(set(expected), {
            item['engine_id'] for item in differential
        })

    def test_catalog_explicitly_rejects_deprecated_and_substitute_paths(self):
        policy = self.catalog['execution_policy']
        self.assertEqual('forbidden', policy['embedded_backend'])
        self.assertEqual('forbidden', policy['donor_backend_substitute'])
        self.assertEqual(
            'actual_donor_listener', policy['required_donor_transport']
        )
        reference_policy = self.reference['policy']
        self.assertEqual(
            'deprecated_non_authoritative_not_used',
            reference_policy['deprecated_clone_catalog_disposition'],
        )
        self.assertEqual('forbidden', reference_policy[
            'frozen_clone_substitution'
        ])

    def test_contract_fixture_needs_explicit_admission(self):
        adapter = load_candidate(
            FIXTURE_ROOT / 'candidate_adapter.py',
            load_json(FIXTURE_ROOT / 'fixture_config.json'),
        )
        result = run_candidate(
            adapter, ROOT,
            {'corrected_driver_package_api_provenance'},
            allow_contract_fixture=False,
            now=datetime.now(timezone.utc),
        )
        self.assertFalse(result['valid'])
        self.assertFalse(result['qualifying'])
        self.assertIn(
            'contract fixture requires explicit test admission',
            result['errors'],
        )

    def test_contract_fixture_validates_shape_but_cannot_promote(self):
        adapter = load_candidate(
            FIXTURE_ROOT / 'candidate_adapter.py',
            load_json(FIXTURE_ROOT / 'fixture_config.json'),
        )
        result = run_candidate(
            adapter, ROOT,
            {'corrected_driver_package_api_provenance'},
            allow_contract_fixture=True,
            now=datetime.now(timezone.utc),
        )
        self.assertTrue(result['valid'], result['errors'])
        self.assertFalse(result['qualifying'])
        self.assertEqual(8, result['case_count'])
        self.assertEqual(8, result['counts']['fixture_observed'])
        self.assertEqual(0, result['counts']['passed'])
        self.assertEqual(0, result['promoted_capabilities'])
        self.assertTrue(all(
            item['state'] == 'not_promoted'
            for item in result['promotions']
        ))

    def test_live_candidate_can_run_all_cases_and_promote_from_evidence(self):
        adapter = LiveCandidate()
        result = run_candidate(adapter, ROOT, now=NOW)
        self.assertTrue(result['valid'], result['errors'])
        self.assertTrue(result['qualifying'])
        self.assertEqual(208, result['case_count'])
        self.assertEqual(208, result['counts']['passed'])
        self.assertEqual(32, result['promoted_capabilities'])
        self.assertTrue(all(
            item['state'] == 'promoted' and
            item['evidence_set_digest'] is not None and
            item['manual_override'] is False
            for item in result['promotions']
        ))

    def test_selected_suite_requires_only_its_own_upstream_dependency(self):
        adapter = LiveCandidate()
        del adapter.manifest['dependencies']['D3_CDE']
        del adapter.manifest['dependencies']['D5_EMULATION']
        result = run_candidate(
            adapter, ROOT,
            {'corrected_driver_package_api_provenance'}, now=NOW
        )
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual(8, result['case_count'])
        self.assertEqual(1, result['promoted_capabilities'])

    def test_bad_candidate_backend_or_dependency_fails_before_execution(self):
        mutations = (
            ('execution_backend', 'postgresql'),
            ('candidate_protocol_version', '2.0.0'),
            ('production_candidate', False),
        )
        for field, value in mutations:
            adapter = LiveCandidate()
            adapter.manifest[field] = value
            with self.subTest(field=field):
                result = run_candidate(
                    adapter, ROOT,
                    {'corrected_driver_package_api_provenance'}, now=NOW
                )
                self.assertFalse(result['valid'])
                self.assertFalse(result['qualifying'])
                self.assertFalse(adapter.calls)
        adapter = LiveCandidate()
        adapter.manifest['dependencies']['D2_DRIVER'][
            'signature_verified'
        ] = False
        result = run_candidate(
            adapter, ROOT,
            {'corrected_driver_package_api_provenance'}, now=NOW
        )
        self.assertFalse(result['valid'])
        self.assertTrue(any('signature' in item for item in result['errors']))

    def test_failed_case_reports_exact_owner_and_blocks_promotion(self):
        adapter = LiveCandidate()
        failed = 'navigator.identity.uuid'
        adapter.status_by_case[failed] = 'failed'
        result = run_candidate(
            adapter, ROOT,
            {'navigator_authorization_uuid_generation'}, now=NOW
        )
        failure = next(item for item in result['results']
                       if item['case_id'] == failed)
        self.assertEqual('failed', failure['status'])
        self.assertEqual('navigator', failure['owner_contract'])
        self.assertEqual('D3_CDE', failure['owner_dependency'])
        self.assertEqual(
            'SB_SPEC_STRUCTURE_TREE_AND_NAVIGATOR_CONFORMANCE',
            failure['owner_search_key'],
        )
        self.assertEqual(0, result['promoted_capabilities'])
        self.assertFalse(result['valid'])

    def test_refused_or_blocked_live_case_fails_the_gate(self):
        for status in ('refused', 'blocked'):
            adapter = LiveCandidate()
            adapter.status_by_case['driver.package.sbom'] = status
            result = run_candidate(
                adapter, ROOT,
                {'corrected_driver_package_api_provenance'}, now=NOW
            )
            with self.subTest(status=status):
                self.assertFalse(result['valid'])
                self.assertEqual(1, result['counts'][status])
                self.assertEqual(0, result['promoted_capabilities'])

    def test_candidate_exception_is_collected_without_stopping_suite(self):
        adapter = LiveCandidate()
        adapter.raise_for.add('driver.package.sbom')
        result = run_candidate(
            adapter, ROOT,
            {'corrected_driver_package_api_provenance'}, now=NOW
        )
        self.assertEqual(8, len(adapter.calls))
        self.assertEqual(1, result['counts']['failed'])
        self.assertEqual(7, result['counts']['passed'])
        failure = next(item for item in result['results']
                       if item['status'] == 'failed')
        self.assertIn('RuntimeError', failure['diagnostics'][0])

    def test_identity_digest_and_freshness_drift_fail_closed(self):
        mutations = (
            lambda body, case: body.update({'case_id': 'changed'}),
            lambda body, case: body.update({
                'observed_at': (NOW - timedelta(days=31)).isoformat()
            }),
            lambda body, case: body.update({'assertions': {}}),
        )
        for mutation in mutations:
            adapter = LiveCandidate()
            adapter.body_mutator = mutation
            result = run_candidate(
                adapter, ROOT,
                {'corrected_driver_package_api_provenance'}, now=NOW
            )
            with self.subTest(mutation=mutation):
                self.assertEqual(8, result['counts']['failed'])
                self.assertEqual(0, result['promoted_capabilities'])

    def test_mga_authority_drift_fails_transaction_cases(self):
        adapter = LiveCandidate()

        def weaken(body, case):
            if case.get('transaction_sensitive'):
                body['scratchbird_authority'][
                    'driver_finality_authority'
                ] = True

        adapter.body_mutator = weaken
        result = run_candidate(
            adapter, ROOT,
            {'mga_transaction_boundaries_and_finality'}, now=NOW
        )
        self.assertEqual(12, result['counts']['failed'])
        self.assertTrue(all(
            item['owner_search_key'] ==
            'DRIVER-CORE-MGA-AWARE-SESSION-AND-TRANSACTION-BEHAVIOR'
            for item in result['results']
        ))
        self.assertEqual(0, result['promoted_capabilities'])

    def test_donor_suite_requires_actual_listener_and_no_substitute(self):
        adapter = LiveCandidate()

        def substitute(body, case):
            body['reference_runtime']['transport'] = 'embedded_process'
            body['embedded_backend_substitute'] = True

        adapter.body_mutator = substitute
        result = run_candidate(
            adapter, ROOT,
            {'exact_donor_listener_differential'}, now=NOW
        )
        self.assertEqual(154, result['counts']['failed'])
        self.assertEqual(0, result['promoted_capabilities'])
        self.assertTrue(all(
            item['owner_dependency'] == 'D5_EMULATION'
            for item in result['results']
        ))

    def test_unknown_suite_or_malformed_adapter_is_rejected(self):
        with self.assertRaisesRegex(AcceptanceError, 'unknown suites'):
            run_candidate(LiveCandidate(), ROOT, {'invented'}, now=NOW)
        with self.assertRaisesRegex(AcceptanceError, 'manifest must be'):
            run_candidate(
                type('Bad', (), {
                    'candidate_manifest': lambda self: [],
                    'execute_case': lambda self, case: {},
                })(),
                ROOT, {'corrected_driver_package_api_provenance'}, now=NOW,
            )

    def test_definition_mutations_report_structural_errors(self):
        catalog = copy.deepcopy(self.catalog)
        catalog['authority_invariants']['automatic_replay'] = True
        catalog['suites'][0]['cases'][0]['assertions'] = []
        catalog['suites'][0]['cases'][1]['case_id'] = \
            catalog['suites'][0]['cases'][0]['case_id']
        errors = definition_errors(catalog, self.reference, self.scenarios)
        self.assertTrue(any('authority' in item for item in errors))
        self.assertTrue(any('assertions' in item for item in errors))
        self.assertTrue(any('duplicate' in item for item in errors))

    def test_schema_catalog_and_promotion_policy_are_consistent(self):
        schema = load_json(
            ROOT / 'tools/cdeadmin_upstream_acceptance.schema.json'
        )
        self.assertEqual(
            self.catalog['schema'], schema['properties']['schema']['const']
        )
        self.assertEqual(
            EXPECTED_AUTHORITIES, self.catalog['authority_invariants']
        )
        promotion = self.catalog['promotion_policy']
        self.assertTrue(promotion['all_capability_cases_must_pass'])
        self.assertFalse(promotion['fixture_evidence_promotes'])
        self.assertFalse(promotion['stale_evidence_promotes'])
        self.assertFalse(promotion['manual_override'])

    def test_runner_and_fixture_have_no_driver_or_network_imports(self):
        sources = (
            ROOT / 'tools/cdeadmin_upstream_acceptance.py',
            FIXTURE_ROOT / 'candidate_adapter.py',
        )
        forbidden = {'scratchbird', 'socket', 'ssl', 'sqlite3', 'requests'}
        for source in sources:
            tree = ast.parse(source.read_text(encoding='utf-8'))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(
                        alias.name.split('.')[0] for alias in node.names
                    )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split('.')[0])
            with self.subTest(source=source.name):
                self.assertFalse(forbidden & imported)

    def test_evidence_digest_is_content_addressed(self):
        body = {'case_id': 'example', 'status': 'passed'}
        evidence = make_evidence(body)
        self.assertEqual(
            canonical_digest(body), evidence['evidence_digest']
        )
        changed = dict(evidence)
        changed['status'] = 'failed'
        self.assertNotEqual(
            canonical_digest({
                key: value for key, value in changed.items()
                if key != 'evidence_digest'
            }),
            changed['evidence_digest'],
        )

    def test_cli_runs_definition_fixture_and_missing_config_paths(self):
        output = io.StringIO()
        with patch('sys.argv', [
                'acceptance', '--source', str(ROOT), '--json']):
            with redirect_stdout(output):
                self.assertEqual(0, main())
        self.assertEqual(208, json.loads(output.getvalue())[
            'expanded_case_count'
        ])

        output = io.StringIO()
        with patch('sys.argv', [
                'acceptance', '--source', str(ROOT),
                '--candidate-adapter', str(
                    FIXTURE_ROOT / 'candidate_adapter.py'
                ),
                '--candidate-config', str(
                    FIXTURE_ROOT / 'fixture_config.json'
                ),
                '--allow-contract-fixture',
                '--suite', 'corrected_driver_package_api_provenance',
                '--json',
        ]):
            with redirect_stdout(output):
                self.assertEqual(0, main())
        fixture_result = json.loads(output.getvalue())
        self.assertEqual(8, fixture_result['counts']['fixture_observed'])

        output = io.StringIO()
        with patch('sys.argv', [
                'acceptance', '--source', str(ROOT),
                '--candidate-adapter', str(
                    FIXTURE_ROOT / 'candidate_adapter.py'
                ),
        ]):
            with redirect_stdout(output):
                self.assertEqual(2, main())
        self.assertIn('--candidate-config is required', output.getvalue())

    def test_document_and_candidate_failure_paths_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = root / 'invalid.json'
            invalid.write_text('{', encoding='utf-8')
            with self.assertRaisesRegex(AcceptanceError, 'cannot load'):
                load_json(invalid)
            array = root / 'array.json'
            array.write_text('[]', encoding='utf-8')
            with self.assertRaisesRegex(AcceptanceError, 'JSON object'):
                load_json(array)

        class BrokenManifest:
            def candidate_manifest(self):
                raise RuntimeError('manifest unavailable')

            def execute_case(self, case):
                return {}

        with self.assertRaisesRegex(AcceptanceError, 'manifest failed'):
            run_candidate(
                BrokenManifest(), ROOT,
                {'corrected_driver_package_api_provenance'}, now=NOW,
            )

        adapter = LiveCandidate()
        adapter.execute_case = lambda case: []
        result = run_candidate(
            adapter, ROOT,
            {'corrected_driver_package_api_provenance'}, now=NOW,
        )
        self.assertEqual(8, result['counts']['failed'])


if __name__ == '__main__':
    unittest.main()
