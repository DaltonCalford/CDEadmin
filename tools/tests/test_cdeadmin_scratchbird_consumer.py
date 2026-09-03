##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""ScratchBird consumer-port and driver-handoff tests for CDE-PREP-190."""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import unittest
from pathlib import Path

from tools.cdeadmin_scratchbird_consumer import (
    CONTRACT,
    FIXTURE_MANIFEST,
    METHODS,
    MGA_PRESENTATION,
    ConsumerFacade,
    ConsumerPortError,
    FixtureAdapterRefusal,
    ScratchBirdConsumerPort,
    bind_adapter,
    evaluate,
    load_contract,
    load_fixture_manifest,
    negotiate_manifest,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_SOURCE = ROOT / FIXTURE_MANIFEST.parent / 'fixture_adapter.py'
CONSUMER_SOURCE = ROOT / 'tools/cdeadmin_scratchbird_consumer.py'


def load_fixture_adapter():
    spec = importlib.util.spec_from_file_location(
        'cdeadmin_fixture_adapter', FIXTURE_SOURCE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FixtureScratchBirdAdapter


class CandidateAdapter:
    """A shaped handoff double, never a real driver adapter."""

    def __init__(self, manifest):
        self.manifest = copy.deepcopy(manifest)
        self.calls = []
        self.response_overrides = {}

    def adapter_manifest(self):
        return copy.deepcopy(self.manifest)

    def __getattr__(self, name):
        if name not in METHODS:
            raise AttributeError(name)

        def invoke(request):
            self.calls.append((name, request['request_id']))
            response = {
                'schema': 'cdeadmin.scratchbird-response.v1',
                'method': name,
                'request_id': request['request_id'],
                'outcome': 'ok',
                'payload': {},
                'diagnostics': [],
            }
            if name == 'transaction_presentation':
                response['payload']['authority_invariants'] = copy.deepcopy(
                    MGA_PRESENTATION
                )
                response['payload']['boundary_state'] = 'TX_BOUNDARY_ACTIVE'
                response['payload']['finality_state'] = 'unknown'
            response.update(self.response_overrides.get(name, {}))
            return response

        return invoke


class ScratchBirdConsumerPortTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.contract = load_contract(ROOT)
        cls.fixture_manifest = load_fixture_manifest(ROOT)

    def test_checked_in_contract_and_fixture_are_valid_but_not_production(
            self):
        result = evaluate(ROOT)
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual(13, result['method_count'])
        self.assertEqual('1.0.0', result['adapter_version'])
        self.assertEqual('pending_upstream_handoff', result['handoff_state'])
        self.assertFalse(result['production_ready'])
        self.assertFalse(result['network_enabled'])
        self.assertFalse(result['authentication_enabled'])
        self.assertFalse(result['execution_enabled'])

    def test_fixture_is_structurally_complete_and_every_method_refuses(self):
        fixture = load_fixture_adapter()()
        negotiated = bind_adapter(fixture, self.contract)
        self.assertIsInstance(fixture, ScratchBirdConsumerPort)
        self.assertEqual(METHODS, negotiated.methods)
        for method in METHODS:
            with self.subTest(method=method):
                with self.assertRaises(FixtureAdapterRefusal) as caught:
                    getattr(fixture, method)({'request_id': f'req-{method}'})
                self.assertEqual(
                    f'CDE_SB_FIXTURE_{method.upper()}_REFUSED',
                    caught.exception.code,
                )
        self.assertEqual(13, len(fixture.calls))

    def test_fixture_cannot_authenticate_connect_or_execute(self):
        fixture = load_fixture_adapter()()
        facade = ConsumerFacade(fixture, self.contract)
        for method in ('connect', 'authenticate', 'execute'):
            with self.subTest(method=method):
                with self.assertRaises(FixtureAdapterRefusal):
                    facade.invoke(method, {'request_id': f'req-{method}'})
        manifest = fixture.adapter_manifest()
        self.assertFalse(manifest['network_enabled'])
        self.assertFalse(manifest['authentication_enabled'])
        self.assertFalse(manifest['execution_enabled'])
        self.assertIsNone(manifest['driver_package_import'])

    def test_every_production_method_has_a_handoff_acceptance_path(self):
        adapter = CandidateAdapter(self.fixture_manifest)
        facade = ConsumerFacade(adapter, self.contract)
        for method in METHODS:
            with self.subTest(method=method):
                response = facade.invoke(
                    method, {'request_id': f'acceptance-{method}'}
                )
                self.assertEqual(method, response['method'])
                self.assertEqual('ok', response['outcome'])
        self.assertEqual(
            [(method, f'acceptance-{method}') for method in METHODS],
            adapter.calls,
        )

    def test_version_negotiation_is_bounded_and_contract_is_exact(self):
        for admitted in ('1.0.0', '1.2.3', '1.999.0'):
            manifest = copy.deepcopy(self.fixture_manifest)
            manifest['adapter_version'] = admitted
            self.assertEqual(
                admitted,
                negotiate_manifest(manifest, self.contract).adapter_version,
            )
        for refused in ('0.9.9', '2.0.0', '2.1.0', '1.0', 'v1.0.0', ''):
            manifest = copy.deepcopy(self.fixture_manifest)
            manifest['adapter_version'] = refused
            with self.subTest(version=refused):
                with self.assertRaisesRegex(
                        ConsumerPortError,
                        'CDE_SB_ADAPTER_VERSION_UNSUPPORTED'):
                    negotiate_manifest(manifest, self.contract)
        manifest = copy.deepcopy(self.fixture_manifest)
        manifest['consumer_contract_version'] = '1.0.1'
        with self.assertRaisesRegex(
                ConsumerPortError,
                'CDE_SB_CONSUMER_CONTRACT_UNSUPPORTED'):
            negotiate_manifest(manifest, self.contract)

    def test_unknown_mode_schema_or_method_set_fails_closed(self):
        mutations = (
            ('schema', 'invented'),
            ('provider_mode', 'postgresql'),
            ('methods', list(METHODS[:-1])),
            ('methods', list(reversed(METHODS))),
            ('methods', list(METHODS) + ['commit']),
        )
        for field, value in mutations:
            manifest = copy.deepcopy(self.fixture_manifest)
            manifest[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(ConsumerPortError):
                    negotiate_manifest(manifest, self.contract)

    def test_binding_rejects_absent_or_non_callable_advertised_method(self):
        adapter = CandidateAdapter(self.fixture_manifest)
        adapter.execute = None
        with self.assertRaisesRegex(
                ConsumerPortError, 'CDE_SB_ADAPTER_BINDING_INVALID'):
            bind_adapter(adapter, self.contract)
        with self.assertRaisesRegex(
                ConsumerPortError, 'CDE_SB_ADAPTER_BINDING_INVALID'):
            bind_adapter(object(), self.contract)

    def test_request_and_response_identity_cannot_drift(self):
        adapter = CandidateAdapter(self.fixture_manifest)
        facade = ConsumerFacade(adapter, self.contract)
        with self.assertRaisesRegex(
                ConsumerPortError, 'CDE_SB_REQUEST_INVALID'):
            facade.invoke('execute', {})
        adapter.response_overrides['execute'] = {'request_id': 'changed'}
        with self.assertRaisesRegex(
                ConsumerPortError, 'CDE_SB_RESPONSE_IDENTITY_MISMATCH'):
            facade.invoke('execute', {'request_id': 'original'})
        adapter.response_overrides['execute'] = {'method': 'navigate'}
        with self.assertRaisesRegex(
                ConsumerPortError, 'CDE_SB_RESPONSE_IDENTITY_MISMATCH'):
            facade.invoke('execute', {'request_id': 'changed'})

    def test_malformed_or_unadvertised_response_fails_closed(self):
        adapter = CandidateAdapter(self.fixture_manifest)
        facade = ConsumerFacade(adapter, self.contract)
        with self.assertRaisesRegex(
                ConsumerPortError, 'CDE_SB_METHOD_REFUSED'):
            facade.invoke('commit', {'request_id': 'no-authority'})
        for replacement in (
                {'schema': 'unknown'},
                {'outcome': 'invented'},
                {'payload': []},
                {'diagnostics': {}},
        ):
            adapter.response_overrides['diagnostics'] = replacement
            with self.subTest(replacement=replacement):
                with self.assertRaises(ConsumerPortError):
                    facade.invoke('diagnostics', {'request_id': 'malformed'})

    def test_transaction_surface_is_presentation_only_and_mga_bound(self):
        self.assertTrue(
            self.contract['transaction_policy']['presentation_only']
        )
        self.assertFalse(
            self.contract['transaction_policy']['control_methods_exposed']
        )
        self.assertFalse(
            self.contract['transaction_policy']['automatic_replay']
        )
        self.assertEqual(
            MGA_PRESENTATION, self.contract['authority_invariants']
        )
        self.assertFalse(
            {'begin', 'commit', 'rollback', 'savepoint', 'replay'} &
            set(METHODS)
        )
        adapter = CandidateAdapter(self.fixture_manifest)
        facade = ConsumerFacade(adapter, self.contract)
        adapter.response_overrides['transaction_presentation'] = {
            'payload': {'authority_invariants': {
                **MGA_PRESENTATION, 'consumer_interprets_finality': True,
            }}
        }
        with self.assertRaisesRegex(
                ConsumerPortError, 'CDE_SB_TRANSACTION_AUTHORITY_INVALID'):
            facade.invoke(
                'transaction_presentation', {'request_id': 'txn-view'}
            )

    def test_cancellation_is_occurrence_scoped_and_never_implies_finality(
            self):
        contract = self.contract
        self.assertEqual(
            'occurrence_cancellation',
            contract['method_contracts']['cancel']['surface'],
        )
        self.assertFalse(
            contract['authority_invariants']['cancellation_implies_rollback']
        )
        self.assertEqual(
            'durable_transaction_inventory',
            contract['authority_invariants']['finality_source'],
        )

    def test_secrets_and_raw_driver_handles_are_outside_the_contract(self):
        self.assertTrue(
            self.contract['secret_policy']['credentials_are_ephemeral_inputs']
        )
        self.assertFalse(
            self.contract['secret_policy']['raw_secrets_in_responses']
        )
        self.assertFalse(
            self.contract['identity_policy']['raw_driver_handles_exposed']
        )
        serialized = json.dumps(self.contract).lower()
        self.assertNotIn('/home/', serialized)
        self.assertNotIn('file://', serialized)

    def test_consumer_and_fixture_do_not_import_driver_or_network_modules(
            self):
        forbidden_roots = {
            'scratchbird', 'socket', 'ssl', 'urllib', 'http', 'requests',
            'asyncio',
        }
        for source in (CONSUMER_SOURCE, FIXTURE_SOURCE):
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
                self.assertFalse(forbidden_roots & imported)
        source_text = CONSUMER_SOURCE.read_text(encoding='utf-8')
        for private_driver_field in (
                '_txn_id', '_socket', '_parameters', '_last_notice'):
            self.assertNotIn(private_driver_field, source_text)

    def test_schema_and_contract_method_inventories_are_consistent(self):
        schema = json.loads(
            (ROOT / 'tools/cdeadmin_scratchbird_consumer.schema.json')
            .read_text(encoding='utf-8')
        )
        self.assertEqual(
            'cdeadmin.scratchbird-consumer-contract.v1',
            schema['properties']['schema']['const'],
        )
        self.assertEqual(set(METHODS), set(self.contract['method_contracts']))
        self.assertEqual(
            list(METHODS), self.fixture_manifest['methods']
        )
        self.assertEqual(CONTRACT, Path(
            'tools/cdeadmin_scratchbird_consumer_contract.json'
        ))


if __name__ == '__main__':
    unittest.main()
