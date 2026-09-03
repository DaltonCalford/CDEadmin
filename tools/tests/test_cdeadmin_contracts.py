##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Contract, compatibility, and provider SDK tests for CDE-PREP-020."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = ROOT / 'web/pgadmin/cdeadmin/contracts/v1'
SCHEMA_PATH = CONTRACT_ROOT / 'contract.schema.json'
GENERATED_PATH = CONTRACT_ROOT / 'generated.py'
TYPESCRIPT_PATH = (
    ROOT / 'web/pgadmin/cdeadmin/static/js/contracts/v1/generated.ts'
)
FIXTURES = Path(__file__).parent / 'fixtures/cdeadmin_contracts'


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f'cannot load {path}')
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


runtime = load_module('cdeadmin_contract_runtime_test',
                      CONTRACT_ROOT / 'runtime.py')
generated = load_module('cdeadmin_generated_contract_test', GENERATED_PATH)
generator = load_module('cdeadmin_contract_generator_test',
                        ROOT / 'tools/cdeadmin_generate_contracts.py')
contract_kit = load_module('cdeadmin_contract_kit_test',
                           ROOT / 'tools/cdeadmin_contract_kit.py')


def identity(**overrides):
    value = {
        'contract_version': '1.0.0',
        'provider_id': 'org.example.provider',
        'provider_version': '1.2.3',
        'profile_id': 'example-native',
        'profile_version': '4.5.6',
        'evidence_reference': 'evidence:current',
    }
    value.update(overrides)
    return value


def capability(**overrides):
    value = {
        'identity': identity(),
        'capability_id': 'resource.drop',
        'support_state': 'implemented',
        'mutation_class': 'destructive',
        'enabled': True,
        'required_permissions': ['administer'],
        'scope': ['resource'],
    }
    value.update(overrides)
    return value


class PostgreSQLStructuralProvider:
    """A no-I/O adapter used only to prove Python structural compatibility."""

    def validate_endpoint(self, request):
        return request

    def discover_endpoint(self, request):
        return request

    def get_capabilities(self, request):
        return []

    def list_resources(self, request):
        return []

    def inspect_resource(self, request):
        return request

    def open_session(self, request):
        return request

    def describe_transaction(self, request):
        return request

    def execute(self, request):
        return request

    def cancel(self, request):
        return request

    def describe_result(self, request):
        return request

    def translate_diagnostic(self, request):
        return request

    def get_events(self, request):
        return []

    def get_operation(self, request):
        return request

    def get_evidence(self, request):
        return request


class NonOperationalFixtureProvider:
    """A deliberately inert fixture with no connection or execution surface."""

    def validate_endpoint(self, request):
        return request

    def discover_endpoint(self, request):
        return request


class CDEadminContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

    def load_manifest(self, name):
        path = FIXTURES / name / 'provider_manifest.json'
        return json.loads(path.read_text(encoding='utf-8'))

    def assert_structural_provider(self, manifest, provider):
        interfaces = self.schema['x-cdeadmin-provider-interfaces']
        for interface_name in manifest['contracts']:
            for method_name in interfaces[interface_name]:
                self.assertTrue(callable(getattr(provider, method_name, None)))

    def test_schema_contains_all_v1_domain_contracts(self):
        required = {
            'Endpoint', 'Capability', 'Resource', 'Session',
            'TransactionPresentation', 'Execution', 'Result', 'Diagnostic',
            'Event', 'Operation', 'Evidence', 'Parity', 'ProviderManifest',
            'ProviderPermission',
        }
        self.assertTrue(required.issubset(self.schema['$defs']))

    def test_exchange_contracts_carry_versioned_evidence_identity(self):
        exchanges = {
            'Endpoint', 'Capability', 'Resource', 'Session',
            'TransactionPresentation', 'Execution', 'Result', 'Diagnostic',
            'Event', 'Operation', 'Evidence', 'Parity', 'ProviderManifest',
        }
        for name in exchanges:
            definition = self.schema['$defs'][name]
            self.assertIn('identity', definition['required'], name)
            self.assertEqual(
                '#/$defs/EnvelopeIdentity',
                definition['properties']['identity']['$ref'],
                name,
            )

    def test_generated_python_and_typescript_are_current(self):
        self.assertEqual(
            GENERATED_PATH.read_text(encoding='utf-8'),
            generator.render_python(self.schema),
        )
        self.assertEqual(
            TYPESCRIPT_PATH.read_text(encoding='utf-8'),
            generator.render_typescript(self.schema),
        )

    def test_generated_dto_preserves_additional_fields(self):
        dto = generated.EnvelopeIdentity(
            contract_version='1.0.0',
            provider_id='org.example.provider',
            provider_version='1.2.3',
            profile_id='example-native',
            profile_version='4.5.6',
            evidence_reference='evidence:current',
            additional_fields={'future_identity': {'generation': 7}},
        )
        self.assertEqual(
            {'generation': 7}, dto.to_dict()['future_identity']
        )

    def test_validation_preserves_unknown_fields_and_enums(self):
        value = {
            'identity': identity(future_identity='preserved'),
            'result_id': 'result-1',
            'execution_id': 'execution-1',
            'result_kind': 'future_multimodel_result',
            'schema': {},
            'stream_reference': None,
            'complete': True,
            'future_result_control': {'mode': 'preserved'},
        }
        validated = runtime.validate_contract('Result', value, self.schema)
        self.assertEqual(value, validated)
        self.assertIsNot(value, validated)

    def test_unknown_or_unrecognized_capability_fails_closed(self):
        known = {'resource.drop'}
        self.assertTrue(runtime.admit_capability(capability(), known))
        self.assertFalse(runtime.admit_capability(
            capability(capability_id='resource.future_destroy'), known
        ))
        self.assertFalse(runtime.admit_capability(
            capability(mutation_class='future_mutation'), known
        ))
        self.assertFalse(runtime.admit_capability(
            capability(support_state='future_state'), known
        ))
        self.assertFalse(runtime.admit_capability(
            capability(required_permissions=[]), known
        ))

    def test_transaction_payload_is_opaque_and_round_trips(self):
        payload = {
            'identity': identity(),
            'session_id': 'session-1',
            'transaction_model': 'provider-native-mga',
            'provider_payload': {
                'provider_boundary': {'opaque': ['value', 42]},
                'finality': {'provider_defined': True},
            },
            'authority_reference': 'provider-authority:opaque',
        }
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

    def test_unsupported_major_version_is_rejected(self):
        value = capability(identity=identity(contract_version='2.0.0'))
        with self.assertRaises(runtime.ContractValidationError):
            runtime.validate_contract('Capability', value, self.schema)

    def test_missing_required_field_is_rejected(self):
        value = capability()
        del value['capability_id']
        with self.assertRaises(runtime.ContractValidationError):
            runtime.validate_contract('Capability', value, self.schema)

    def test_postgresql_manifest_and_provider_pass_contract_kit(self):
        manifest = self.load_manifest('postgresql')
        validated = runtime.validate_contract(
            'ProviderManifest', manifest, self.schema
        )
        self.assertEqual(manifest, validated)
        self.assert_structural_provider(
            manifest, PostgreSQLStructuralProvider()
        )
        result = contract_kit.evaluate(
            FIXTURES / 'postgresql/provider_manifest.json',
            SCHEMA_PATH,
            CONTRACT_ROOT / 'runtime.py',
        )
        self.assertTrue(result['valid'], result['errors'])

    def test_inert_fixture_manifest_and_provider_pass_contract_kit(self):
        manifest = self.load_manifest('fixture')
        runtime.validate_contract('ProviderManifest', manifest, self.schema)
        self.assert_structural_provider(
            manifest, NonOperationalFixtureProvider()
        )
        result = contract_kit.evaluate(
            FIXTURES / 'fixture/provider_manifest.json',
            SCHEMA_PATH,
            CONTRACT_ROOT / 'runtime.py',
        )
        self.assertTrue(result['valid'], result['errors'])

    def test_fixture_manifest_cannot_gain_authority(self):
        manifest = self.load_manifest('fixture')
        manifest['permissions'][0]['granted'] = True
        with self.assertRaises(runtime.ContractValidationError):
            runtime.validate_contract(
                'ProviderManifest', manifest, self.schema
            )
        manifest = self.load_manifest('fixture')
        manifest['enabled'] = True
        with self.assertRaises(runtime.ContractValidationError):
            runtime.validate_contract(
                'ProviderManifest', manifest, self.schema
            )

    def test_contract_kit_rejects_unknown_interface(self):
        manifest = self.load_manifest('fixture')
        manifest['contracts'].append('FuturePrivilegedProvider')
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory) / 'provider_manifest.json'
            temporary.write_text(
                json.dumps(manifest), encoding='utf-8'
            )
            result = contract_kit.evaluate(
                temporary, SCHEMA_PATH, CONTRACT_ROOT / 'runtime.py'
            )
            self.assertFalse(result['valid'])

    def test_validation_returns_defensive_deep_copy(self):
        original = capability(extensions={'future': [1, 2]})
        validated = runtime.validate_contract(
            'Capability', original, self.schema
        )
        validated['extensions']['future'].append(3)
        self.assertEqual([1, 2], original['extensions']['future'])


if __name__ == '__main__':
    unittest.main()
