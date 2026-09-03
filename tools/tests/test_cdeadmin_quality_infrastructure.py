##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Contract fixtures and quality-infrastructure tests for CDE-PREP-110."""

from __future__ import annotations

import copy
import importlib.util
import json
import socket
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
TOOLS = ROOT / 'tools'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
if 'pgadmin' not in sys.modules:
    pgadmin_package = ModuleType('pgadmin')
    pgadmin_package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = pgadmin_package

from pgadmin.cdeadmin.core import (  # noqa: E402
    EndpointContext,
    ProviderRegistrationError,
    ProviderRegistry,
)

import cdeadmin_provider_testkit as testkit  # noqa: E402
import cdeadmin_quality_gate as quality_gate  # noqa: E402


SCHEMA_PATH = ROOT / testkit.DEFAULT_SCHEMA
RUNTIME_PATH = ROOT / testkit.DEFAULT_RUNTIME
CORPUS_PATH = ROOT / testkit.DEFAULT_CORPUS
MATRIX_PATH = ROOT / testkit.DEFAULT_MATRIX
FIXTURE_ROOT = TOOLS / 'tests/fixtures/cdeadmin_contracts/fixture'
FIXTURE_MANIFEST = FIXTURE_ROOT / 'provider_manifest.json'
FIXTURE_PROVIDER = FIXTURE_ROOT / 'provider.py'
QUALITY_POLICY = TOOLS / 'cdeadmin_quality_policy.json'


def load_module(name, path):
    specification = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


runtime = load_module('cdeadmin_quality_test_runtime', RUNTIME_PATH)


def endpoint(label):
    endpoint_id = uuid.uuid5(uuid.NAMESPACE_URL, f'quality:{label}')

    def child(purpose):
        return str(uuid.uuid5(endpoint_id, purpose))

    return EndpointContext(
        endpoint_id=str(endpoint_id),
        mode='legacy_native',
        experience_family='quality-fixture',
        provider_id='org.cdeadmin.quality.concurrent',
        provider_version='1.0.0',
        profile_id='quality-fixture-native',
        profile_version='1.0.0',
        target_adapter_id='quality-fixture-adapter',
        target_adapter_version='1.0.0',
        pool_namespace=child('pool'),
        session_namespace=child('session'),
        cache_namespace=child('cache'),
        diagnostic_namespace=child('diagnostic'),
        effective_permissions=frozenset({'network'}),
    )


def concurrent_manifest():
    return {
        'identity': {
            'contract_version': '1.1.0',
            'provider_id': 'org.cdeadmin.quality.concurrent',
            'provider_version': '1.0.0',
            'profile_id': 'quality-fixture-native',
            'profile_version': '1.0.0',
            'evidence_reference': 'evidence:cde-prep-110:concurrency',
        },
        'package_type': 'engine',
        'sdk_compatibility': {
            'minimum': '1.0.0',
            'maximum_exclusive': '2.0.0',
        },
        'support_state': 'experimental',
        'enabled': True,
        'fixture': False,
        'production_registration': True,
        'contracts': ['EndpointProvider'],
        'permissions': [{
            'permission_id': 'network',
            'granted': True,
            'scope': ['endpoint'],
        }],
        'required_permissions': ['network'],
        'composition': {
            'experience_families': ['quality-fixture'],
            'target_adapter_ids': ['quality-fixture-adapter'],
        },
        'extension_schema': None,
        'provenance': {'purpose': 'concurrency test only'},
    }


class ContractCorpusTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        cls.corpus = json.loads(CORPUS_PATH.read_text(encoding='utf-8'))
        cls.matrix = json.loads(MATRIX_PATH.read_text(encoding='utf-8'))

    def test_golden_corpus_covers_every_contract_definition(self):
        definitions = set(self.schema['$defs'])
        contracts = {item['contract'] for item in self.corpus['entries']}
        self.assertEqual(definitions, contracts)
        self.assertEqual(15, len(self.corpus['entries']))

    def test_current_and_previous_versions_round_trip_corpus(self):
        for version in ('1.1.0', '1.0.0'):
            self.assertEqual(
                [],
                testkit.evaluate_corpus(
                    runtime, self.schema, self.corpus, version
                ),
                version,
            )

    def test_compatibility_matrix_accepts_v1_and_rejects_next_major(self):
        results, errors = testkit.evaluate_compatibility(
            runtime, self.schema, self.corpus, self.matrix
        )
        self.assertEqual([], errors)
        by_role = {item['role']: item for item in results}
        self.assertEqual(0, by_role['current']['validation_failures'])
        self.assertEqual(0, by_role['previous']['validation_failures'])
        self.assertGreater(
            by_role['unsupported-major']['validation_failures'], 0
        )

    def test_root_identity_rejects_unsupported_major(self):
        identity = copy.deepcopy(self.corpus['entries'][0]['payload'])
        identity['contract_version'] = '2.0.0'
        with self.assertRaises(runtime.ContractValidationError):
            runtime.validate_contract(
                'EnvelopeIdentity', identity, self.schema
            )

    def test_generated_bindings_declare_current_version(self):
        generated = load_module(
            'cdeadmin_quality_test_generated',
            ROOT / 'web/pgadmin/cdeadmin/contracts/v1/generated.py',
        )
        typescript = (
            ROOT / 'web/pgadmin/cdeadmin/static/js/contracts/v1/generated.ts'
        ).read_text(encoding='utf-8')
        self.assertEqual('1.1.0', generated.CONTRACT_VERSION)
        self.assertIn("CONTRACT_VERSION = '1.1.0'", typescript)

    def test_transaction_golden_payload_remains_opaque(self):
        payload = next(
            item['payload'] for item in self.corpus['entries']
            if item['contract'] == 'TransactionPresentation'
        )
        node = self.schema['$defs']['TransactionPresentation'][
            'properties'
        ]['provider_payload']
        self.assertNotIn('properties', node)
        self.assertEqual(
            payload,
            runtime.validate_contract(
                'TransactionPresentation', payload, self.schema
            ),
        )


class FixtureProviderTests(unittest.TestCase):

    def test_provider_testkit_qualifies_network_denied_fixture(self):
        result = testkit.evaluate(
            FIXTURE_MANIFEST,
            FIXTURE_PROVIDER,
            SCHEMA_PATH,
            RUNTIME_PATH,
            CORPUS_PATH,
            MATRIX_PATH,
            deny_network=True,
        )
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual(15, result['golden_dto_count'])
        self.assertTrue(result['network_denied'])

    def test_network_guard_blocks_socket_creation(self):
        with testkit.network_denied():
            with self.assertRaises(testkit.NetworkDeniedError):
                socket.socket()

    def test_fixture_registration_fails_in_production_registry(self):
        registry = ProviderRegistry(
            allowed_module_prefixes=(
                'cdeadmin_quality_fixture_should_not_import.',
            )
        )
        manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding='utf-8'))
        with patch(
            'pgadmin.cdeadmin.core.registry.importlib.import_module'
        ) as importer:
            with self.assertRaises(ProviderRegistrationError):
                registry.register_package(
                    manifest,
                    'cdeadmin_quality_fixture_should_not_import.provider',
                )
        importer.assert_not_called()

    def test_fixture_provider_is_absent_from_production_build_inputs(self):
        policy = json.loads(QUALITY_POLICY.read_text(encoding='utf-8'))
        self.assertEqual(
            [], quality_gate.fixture_violations(ROOT, policy)
        )

    def test_testkit_reports_missing_structural_method(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding='utf-8'))
        self.assertEqual(
            ['EndpointProvider.discover_endpoint is not implemented'],
            testkit.structural_errors(
                schema, manifest, SimpleNamespace(validate_endpoint=lambda: 1)
            ),
        )


class EndpointFaultHarnessTests(unittest.TestCase):

    def test_fault_in_one_endpoint_does_not_corrupt_another(self):
        instances = {}

        class ConcurrentProvider:
            def __init__(self, context, _permissions):
                self.endpoint_id = context.endpoint_id
                self.values = []
                self.lock = threading.Lock()
                instances[context.endpoint_id] = self

            @staticmethod
            def validate_endpoint(request):
                return request

            @staticmethod
            def discover_endpoint(request):
                return request

            def healthy(self):
                with self.lock:
                    self.values.append('healthy')
                    return tuple(self.values)

            def fault(self):
                with self.lock:
                    self.values.append('fault-local')
                raise RuntimeError('injected endpoint-local fault')

        registry = ProviderRegistry(
            allowed_module_prefixes=('cdeadmin_quality_concurrent.',)
        )
        module = SimpleNamespace(create_provider=ConcurrentProvider)
        with patch(
            'pgadmin.cdeadmin.core.registry.importlib.import_module',
            return_value=module,
        ):
            registry.register_package(
                concurrent_manifest(), 'cdeadmin_quality_concurrent.provider'
            )
        failing = endpoint('failing')
        healthy = endpoint('healthy')
        first = registry.resolve(failing).instance
        second = registry.resolve(healthy).instance
        harness = testkit.EndpointConcurrencyHarness()
        results = harness.run((
            testkit.EndpointProbe(failing.endpoint_id, first.fault),
            testkit.EndpointProbe(healthy.endpoint_id, second.healthy),
        ))
        by_endpoint = {item.endpoint_id: item for item in results}
        self.assertEqual(
            'RuntimeError', by_endpoint[failing.endpoint_id].error_type
        )
        self.assertEqual(
            ('healthy',), by_endpoint[healthy.endpoint_id].value
        )
        self.assertEqual(['fault-local'], first.values)
        self.assertEqual(['healthy'], second.values)
        self.assertIsNot(first, second)
        self.assertIs(second, registry.resolve(healthy).instance)

    def test_harness_rejects_duplicate_endpoint_ids(self):
        harness = testkit.EndpointConcurrencyHarness()
        with self.assertRaises(testkit.ProviderTestKitError):
            harness.run((
                testkit.EndpointProbe('duplicate', lambda: 1),
                testkit.EndpointProbe('duplicate', lambda: 2),
            ))


class QualityGateTests(unittest.TestCase):

    def test_live_quality_gate_meets_startup_and_performance_budgets(self):
        policy = json.loads(QUALITY_POLICY.read_text(encoding='utf-8'))
        result = quality_gate.evaluate(ROOT, policy)
        self.assertEqual([], result['violations'])
        self.assertEqual(1366, result['startup_measurement']['route_count'])
        bundle = result['bundle_measurement']
        self.assertIn(bundle['status'], {'not_built', 'measured'})
        if bundle['status'] == 'measured':
            self.assertLessEqual(bundle['bytes'], bundle['maximum_bytes'])
        self.assertEqual(
            5000, result['contract_benchmark']['iterations']
        )

    def test_actual_contract_and_fixture_quality_inputs_are_consistent(self):
        policy = json.loads(QUALITY_POLICY.read_text(encoding='utf-8'))
        self.assertEqual([], quality_gate.contract_violations(ROOT, policy))
        measurements = quality_gate.source_measurements(ROOT, policy)
        self.assertEqual(
            [],
            quality_gate.source_budget_violations(measurements, policy),
        )

    def test_seeded_production_fixture_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            provider = source / 'web/pgadmin/cdeadmin/providers/fixture_bad'
            provider.mkdir(parents=True)
            (provider / 'provider.py').write_text(
                'class NonOperationalFixtureProvider:\n    pass\n',
                encoding='utf-8',
            )
            fixture = source / (
                'tools/tests/fixtures/cdeadmin_contracts/fixture/provider.py'
            )
            fixture.parent.mkdir(parents=True)
            fixture.write_text('import copy\n', encoding='utf-8')
            policy = {
                'production_provider_roots': [
                    'web/pgadmin/cdeadmin/providers'
                ],
                'fixture_provider': fixture.relative_to(source).as_posix(),
                'forbidden_fixture_imports': ['socket'],
            }
            rules = {
                item['rule'] for item in
                quality_gate.fixture_violations(source, policy)
            }
            self.assertIn('fixture-production-exclusion', rules)
            self.assertIn('fixture-production-marker', rules)

    def test_seeded_fixture_network_import_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            production = source / 'production'
            production.mkdir()
            fixture = source / 'test/provider.py'
            fixture.parent.mkdir()
            fixture.write_text('import socket\n', encoding='utf-8')
            policy = {
                'production_provider_roots': ['production'],
                'fixture_provider': 'test/provider.py',
                'forbidden_fixture_imports': ['socket'],
            }
            rules = {
                item['rule'] for item in
                quality_gate.fixture_violations(source, policy)
            }
            self.assertIn('fixture-network-import', rules)

    def test_required_bundle_fails_when_generated_outputs_are_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            policy = {'built_bundle': {
                'roots': ['generated-js', 'generated-css'],
                'baseline_bytes': 100,
                'maximum_bytes': 110,
            }}
            measurement, findings = quality_gate.bundle_measurement(
                source, policy, require_bundle=True
            )
            self.assertEqual('not_built', measurement['status'])
            self.assertEqual(
                ['built-bundle-required'],
                [item['rule'] for item in findings],
            )

    def test_bundle_size_regression_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            output = source / 'generated'
            output.mkdir()
            (output / 'bundle.js').write_bytes(b'x' * 111)
            policy = {'built_bundle': {
                'roots': ['generated'],
                'baseline_bytes': 100,
                'maximum_bytes': 110,
            }}
            measurement, findings = quality_gate.bundle_measurement(
                source, policy, require_bundle=True
            )
            self.assertEqual(111, measurement['bytes'])
            self.assertEqual(
                ['built-bundle-size'],
                [item['rule'] for item in findings],
            )


if __name__ == '__main__':
    unittest.main()
