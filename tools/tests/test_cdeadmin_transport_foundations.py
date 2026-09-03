##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Protocol and embedded-runtime foundation tests for CDE-PREP-140."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace


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

from pgadmin.cdeadmin.core import EndpointContext  # noqa: E402
from pgadmin.cdeadmin.security import (  # noqa: E402
    SecretReference,
    SecurityService,
)
from pgadmin.cdeadmin.transports import (  # noqa: E402
    BubblewrapSandbox,
    EmbeddedHelperHost,
    EmbeddedRuntimeError,
    EmbeddedRuntimeGrant,
    EndpointFaultStore,
    HelperInvocation,
    HelperResult,
    ProtocolBoundary,
    ProtocolBoundaryRegistry,
    ProtocolSelection,
    SandboxCapabilities,
    TransportError,
    TransportRequest,
    TransportResponse,
    TransportUnavailableError,
    load_protocol_selections,
    load_selection_document,
)

import cdeadmin_transport_gate as gate  # noqa: E402


SELECTION_PATH = (
    WEB / 'pgadmin/cdeadmin/transports/protocol_client_selections.json'
)


def endpoint(label='one'):
    endpoint_id = uuid.uuid5(uuid.NAMESPACE_URL, f'transport:{label}')

    def child(purpose):
        return str(uuid.uuid5(endpoint_id, purpose))

    return EndpointContext(
        endpoint_id=str(endpoint_id),
        mode='legacy_native',
        experience_family='postgresql',
        provider_id='org.example.transport',
        provider_version='1.0.0',
        profile_id='postgresql-native',
        profile_version='18.3',
        target_adapter_id='postgresql-wire-adapter',
        target_adapter_version='1.0.0',
        pool_namespace=child('pool'),
        session_namespace=child('session'),
        cache_namespace=child('cache'),
        diagnostic_namespace=child('diagnostic'),
        effective_permissions=frozenset({'secret_read', 'network'}),
        declared_runtime_family='postgresql',
        runtime_identity_generation='generation-one',
    )


def secret(context, reference_id=None):
    return SecretReference(
        reference_id=reference_id or str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f'transport-secret:{context.endpoint_id}',
        )),
        endpoint_id=context.endpoint_id,
        endpoint_mode=context.mode,
        secret_kind='database_password',
        storage_kind='test',
        resolver_id='test.resolver',
        locator='vault:test:transport',
        allowed_purposes=frozenset({'connect'}),
        authority_scope='legacy_engine_auth',
    )


def request(context, reference, operation='operation-one'):
    return TransportRequest(
        endpoint_id=context.endpoint_id,
        endpoint_mode=context.mode,
        provider_id=context.provider_id,
        operation_id=operation,
        protocol_id='postgresql_wire',
        frames=(b'opaque-frame',),
        principal_id='principal:one',
        credential_reference_id=reference.reference_id,
        attributes={'frame_count': 1},
    )


class ProtocolSelectionTests(unittest.TestCase):

    def test_selection_document_covers_all_exact_profiles(self):
        document = load_selection_document(SELECTION_PATH)
        self.assertEqual(14, len(document['protocols']))
        self.assertEqual(25, len(document['engine_profiles']))
        observed = {
            row['engine_id']: row['reference_profile']
            for row in document['engine_profiles']
        }
        self.assertEqual(gate.REQUIRED_PROFILES, observed)

    def test_all_required_protocol_boundaries_are_loadable(self):
        selections = load_protocol_selections(SELECTION_PATH)
        self.assertEqual(gate.REQUIRED_PROTOCOLS, set(selections))
        self.assertEqual(
            ('3.2.13; python < 3.10', '3.3.4; python >= 3.10'),
            selections['postgresql_wire'].client_versions,
        )

    def test_unselected_clients_record_no_implied_version(self):
        document = load_selection_document(SELECTION_PATH)
        rows = [
            row for row in document['protocols']
            if row['client']['state'] == 'boundary_only_unselected'
        ]
        self.assertTrue(rows)
        for row in rows:
            self.assertIsNone(row['client']['package'])
            self.assertEqual([], row['client']['versions'])

    def test_engine_profiles_are_server_implementation_neutral(self):
        document = load_selection_document(SELECTION_PATH)
        for row in document['engine_profiles']:
            self.assertNotIn('scratchbird_emulation', row)

    def test_invalid_selected_client_requires_exact_version(self):
        with self.assertRaises(TransportError):
            ProtocolSelection(
                'example', 'tcp_binary', 'example framing',
                'endpoint', 'selected_installed', 'example', (),
            )


class ProtocolBoundaryTests(unittest.TestCase):

    def setUp(self):
        self.context = endpoint()
        self.reference = secret(self.context)
        self.security = SecurityService()
        self.secret_canary = b'transport-secret-canary'
        self.security.secrets.register_resolver(
            'test.resolver', lambda *_args: self.secret_canary
        )
        self.security.secrets.register_reference(self.reference)
        self.selection = load_protocol_selections()[
            'postgresql_wire'
        ]

    def test_secret_lease_is_scoped_to_one_exchange_and_zeroized(self):
        observed = {}

        class Client:
            def exchange(inner, value, lease, isolation_key):
                observed['lease'] = lease
                observed['secret'] = lease.use(bytes)
                observed['isolation_key'] = isolation_key
                return TransportResponse(
                    value.endpoint_id,
                    value.operation_id,
                    value.protocol_id,
                    (b'opaque-result',),
                )

        boundary = ProtocolBoundary(
            self.context, self.selection, Client(), self.security
        )
        response = boundary.exchange(request(
            self.context, self.reference
        ))
        self.assertEqual((b'opaque-result',), response.frames)
        self.assertEqual(self.secret_canary, observed['secret'])
        self.assertTrue(observed['lease'].closed)
        self.assertNotIn(
            self.secret_canary.decode('ascii'), repr(boundary.faults())
        )

    def test_cross_endpoint_request_fails_before_secret_resolution(self):
        calls = []
        self.security.secrets.register_resolver = lambda *_args: None
        other = endpoint('other')
        other_reference = secret(other, self.reference.reference_id)
        boundary = ProtocolBoundary(
            self.context,
            self.selection,
            SimpleNamespace(exchange=lambda *_args: calls.append(True)),
            self.security,
        )
        with self.assertRaises(TransportError):
            boundary.exchange(request(other, other_reference))
        self.assertEqual([], calls)

    def test_fault_in_one_endpoint_does_not_contaminate_another(self):
        other = endpoint('other')
        other_reference = secret(other)
        self.security.secrets.register_reference(other_reference)
        store = EndpointFaultStore()

        class Failing:
            @staticmethod
            def exchange(*_args):
                raise RuntimeError('fault-secret-canary')

        class Healthy:
            @staticmethod
            def exchange(value, *_args):
                return TransportResponse(
                    value.endpoint_id, value.operation_id,
                    value.protocol_id, (b'healthy',),
                )

        failed = ProtocolBoundary(
            self.context, self.selection, Failing(), self.security, store
        )
        healthy = ProtocolBoundary(
            other, self.selection, Healthy(), self.security, store
        )
        with self.assertRaises(TransportError):
            failed.exchange(request(self.context, self.reference))
        result = healthy.exchange(request(other, other_reference, 'two'))
        self.assertEqual((b'healthy',), result.frames)
        self.assertEqual(1, len(failed.faults()))
        self.assertEqual(0, len(healthy.faults()))
        self.assertNotIn('fault-secret-canary', repr(failed.faults()))

    def test_response_identity_crossing_is_rejected_and_recorded(self):
        other = endpoint('other')

        class Client:
            @staticmethod
            def exchange(value, *_args):
                return TransportResponse(
                    other.endpoint_id, value.operation_id,
                    value.protocol_id, (),
                )

        boundary = ProtocolBoundary(
            self.context, self.selection, Client(), self.security
        )
        with self.assertRaises(TransportError):
            boundary.exchange(request(self.context, self.reference))
        self.assertEqual(
            'TransportIsolationError', boundary.faults()[0].error_type
        )

    def test_registry_refuses_unselected_client(self):
        registry = ProtocolBoundaryRegistry(
            load_protocol_selections(), self.security
        )
        with self.assertRaises(TransportUnavailableError):
            registry.register_client('mysql_wire', lambda *_args: object())

    def test_registry_bindings_are_endpoint_isolated(self):
        registry = ProtocolBoundaryRegistry(
            load_protocol_selections(), self.security
        )

        class Client:
            @staticmethod
            def exchange(value, *_args):
                return TransportResponse(
                    value.endpoint_id, value.operation_id,
                    value.protocol_id, (),
                )

        registry.register_client(
            'postgresql_wire', lambda *_args: Client()
        )
        first = registry.bind(self.context, 'postgresql_wire')
        self.assertIs(first, registry.bind(
            self.context, 'postgresql_wire'
        ))
        other = endpoint('other')
        self.assertIsNot(first, registry.bind(other, 'postgresql_wire'))
        self.assertEqual(1, registry.unload_endpoint(self.context))
        self.assertEqual(0, registry.unload_endpoint(self.context))


class EmbeddedRuntimeTests(unittest.TestCase):

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.executable = self.root / 'sqlite3-3.53.0'
        self.executable.write_bytes(b'fake exact sqlite helper')
        self.executable.chmod(0o700)
        self.working = self.root / 'work'
        self.working.mkdir()
        self.read_only = self.root / 'readonly'
        self.read_only.mkdir()
        self.context = endpoint('embedded')

    def tearDown(self):
        self.temporary.cleanup()

    def grant(self, **changes):
        values = {
            'endpoint_id': self.context.endpoint_id,
            'endpoint_mode': 'legacy_native',
            'engine_id': 'sqlite',
            'engine_version': '3.53.0',
            'executable': str(self.executable),
            'executable_sha256': hashlib.sha256(
                self.executable.read_bytes()
            ).hexdigest(),
            'working_directory': str(self.working),
            'read_paths': (str(self.read_only), str(self.executable)),
            'write_paths': (str(self.working),),
            'network_policy': 'deny',
            'memory_bytes': 67108864,
            'cpu_seconds': 2,
            'wall_seconds': 2.0,
            'process_count': 1,
            'open_file_count': 16,
            'environment': {'LC_ALL': 'C'},
        }
        values.update(changes)
        return EmbeddedRuntimeGrant(**values)

    def test_complete_sandbox_runs_only_exact_endpoint_grant(self):
        class Sandbox:
            capabilities = SandboxCapabilities(True, True, True, True)

            @staticmethod
            def run(_grant, _invocation):
                return HelperResult(0, b'opaque', b'')

        host = EmbeddedHelperHost(Sandbox())
        invocation = HelperInvocation(
            self.context.endpoint_id, ('--batch',), b'opaque-input'
        )
        self.assertEqual(b'opaque', host.invoke(
            self.grant(), invocation
        ).output_bytes)
        other = endpoint('other')
        with self.assertRaises(TransportError):
            host.invoke(
                self.grant(), HelperInvocation(other.endpoint_id, ())
            )

    def test_incomplete_sandbox_is_refused(self):
        sandbox = SimpleNamespace(
            capabilities=SandboxCapabilities(True, False, True, True),
            run=lambda *_args: None,
        )
        with self.assertRaises(TransportUnavailableError):
            EmbeddedHelperHost(sandbox)

    def test_network_and_overbroad_filesystem_grants_are_refused(self):
        with self.assertRaises(EmbeddedRuntimeError):
            self.grant(network_policy='allow')
        with self.assertRaises(EmbeddedRuntimeError):
            self.grant(read_paths=('/',))
        with self.assertRaises(EmbeddedRuntimeError):
            self.grant(read_paths=(str(Path.home()),))

    def test_wrong_engine_version_and_digest_fail_closed(self):
        with self.assertRaises(EmbeddedRuntimeError):
            self.grant(engine_version='3.45.1')
        grant = self.grant(executable_sha256='0' * 64)
        with self.assertRaises(EmbeddedRuntimeError):
            grant.verify_executable()

    def test_forbidden_environment_authority_is_rejected(self):
        with self.assertRaises(EmbeddedRuntimeError):
            self.grant(environment={'HOME': str(self.root)})

    def test_bubblewrap_command_contains_required_isolation(self):
        invocation = HelperInvocation(
            self.context.endpoint_id, ('--batch',), b''
        )
        command = BubblewrapSandbox().command(self.grant(), invocation)
        for item in (
            '--unshare-all', '--clearenv', '--ro-bind', '--bind',
            '--chdir',
        ):
            self.assertIn(item, command)
        self.assertEqual(str(self.executable), command[-2])
        self.assertEqual('--batch', command[-1])


class TransportPolicyGateTests(unittest.TestCase):

    def test_repository_policy_gate_passes(self):
        result = gate.evaluate(ROOT)
        self.assertEqual([], result['violations'])
        self.assertEqual(14, result['protocol_count'])
        self.assertEqual(25, result['engine_profile_count'])

    def test_gate_detects_semantic_authority_method(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = root / 'web/pgadmin/cdeadmin/transports'
            transport.mkdir(parents=True)
            transport.joinpath('bad.py').write_text(
                'def commit():\n    return True\n', encoding='utf-8'
            )
            violations = gate.source_violations(transport)
            self.assertEqual('semantic-authority-method', violations[0][
                'rule'
            ])

    def test_selection_json_is_machine_readable(self):
        document = json.loads(SELECTION_PATH.read_text(encoding='utf-8'))
        self.assertEqual(
            'cdeadmin.protocol-client-selections.v1', document['schema']
        )


if __name__ == '__main__':
    unittest.main()
