##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Security, secret, and mode-isolation tests for CDE-PREP-100."""

from __future__ import annotations

import copy
import sys
import unittest
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    pgadmin_package = ModuleType('pgadmin')
    pgadmin_package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = pgadmin_package

from pgadmin.cdeadmin.core import (  # noqa: E402
    EndpointContext,
    ProviderPermissionError,
    ProviderRegistry,
    ProviderUnavailableError,
)
from pgadmin.cdeadmin.security import (  # noqa: E402
    EndpointSecretService,
    IsolationPolicyError,
    RuntimeIdentityClaim,
    RuntimeIdentityError,
    SecretAccessError,
    SecretReference,
    SecurityPolicyError,
    SecurityService,
    init_app,
    redact,
    redact_text,
    service_for_app,
)


PROVIDER_ID = 'org.example.security-provider'
PROVIDER_VERSION = '1.0.0'


def endpoint(
    mode='legacy_native', label='one', permissions=('secret_read',),
    endpoint_id=None, namespaces=None,
):
    endpoint_id = endpoint_id or str(uuid.uuid5(
        uuid.NAMESPACE_URL, f'security:{label}'
    ))
    endpoint_uuid = uuid.UUID(endpoint_id)

    def namespace(purpose):
        return str(uuid.uuid5(endpoint_uuid, f'namespace:{purpose}'))

    namespaces = namespaces or {
        purpose: namespace(purpose)
        for purpose in ('pool', 'session', 'cache', 'diagnostic')
    }
    profile = {
        'legacy_native': 'postgresql-native',
        'scratchbird_native': 'scratchbird-native',
    }[mode]
    declared = {
        'legacy_native': 'postgresql',
        'scratchbird_native': 'scratchbird',
    }[mode]
    return EndpointContext(
        endpoint_id=endpoint_id,
        mode=mode,
        experience_family=declared,
        provider_id=PROVIDER_ID,
        provider_version=PROVIDER_VERSION,
        profile_id=profile,
        profile_version='1.0.0',
        target_adapter_id=f'{mode}-adapter',
        target_adapter_version='1.0.0',
        pool_namespace=namespaces['pool'],
        session_namespace=namespaces['session'],
        cache_namespace=namespaces['cache'],
        diagnostic_namespace=namespaces['diagnostic'],
        effective_permissions=frozenset(permissions),
        declared_runtime_family=declared,
    )


def authority_scope(mode):
    return {
        'legacy_native': 'legacy_engine_auth',
        'scratchbird_native': 'scratchbird_native_auth',
    }[mode]


def secret_reference(context, reference_id=None):
    reference_id = reference_id or str(uuid.uuid5(
        uuid.NAMESPACE_URL, 'cdeadmin-security:secret-reference-one'
    ))
    return SecretReference(
        reference_id=reference_id,
        endpoint_id=context.endpoint_id,
        endpoint_mode=context.mode,
        secret_kind='database_password',
        storage_kind='test_vault',
        resolver_id='test.resolver',
        locator='vault://private/locator-do-not-export',
        allowed_purposes=frozenset({'connect'}),
        authority_scope=authority_scope(context.mode),
    )


def verified_claim(context, family=None):
    if family is None:
        family = (
            'scratchbird'
            if context.mode != 'legacy_native' else 'postgresql'
        )
    return RuntimeIdentityClaim(
        endpoint_id=context.endpoint_id,
        endpoint_mode=context.mode,
        declared_runtime_family=context.declared_runtime_family,
        verification_state='verified',
        verified_runtime_family=family,
        verified_runtime_version='1.0.0',
        evidence_reference='runtime-attestation:test',
        generation='runtime-generation-one',
    )


class RedactionTests(unittest.TestCase):

    def test_recursive_redaction_preserves_opaque_references(self):
        value = redact({
            'password': 'password-canary',
            'nested': {
                'client_secret': 'client-secret-canary',
                'secret_reference': 'vault:item:one',
                'evidence_reference': 'evidence:item:one',
            },
            'rows': [{'session_token': 'session-token-canary'}],
        })
        self.assertEqual('[REDACTED]', value['password'])
        self.assertEqual('[REDACTED]', value['nested']['client_secret'])
        self.assertEqual(
            'vault:item:one', value['nested']['secret_reference']
        )
        self.assertEqual(
            'evidence:item:one', value['nested']['evidence_reference']
        )
        self.assertNotIn('session-token-canary', repr(value))

    def test_log_text_redacts_uri_assignment_bearer_and_canary(self):
        text = (
            'postgres://user:pass@example.invalid/db '
            'password=visible Bearer abc.def.ghi explicit-canary'
        )
        result = redact_text(text, ('explicit-canary',))
        for secret in ('user:pass', 'visible', 'abc.def.ghi',
                       'explicit-canary'):
            self.assertNotIn(secret, result)
        self.assertIn('[REDACTED]', result)

    def test_all_safe_export_surfaces_use_same_redaction_policy(self):
        service = SecurityService()
        value = {
            'api_key': 'api-key-canary',
            'message': 'authorization=auth-canary explicit-canary',
        }
        outputs = (
            service.safe_dto(value),
            service.safe_diagnostic(value, ('explicit-canary',)),
            service.safe_telemetry(value, ('explicit-canary',)),
            service.safe_evidence_export(value, ('explicit-canary',)),
        )
        for output in outputs:
            self.assertNotIn('api-key-canary', repr(output))
            self.assertNotIn('auth-canary', repr(output))
        for output in outputs[1:]:
            self.assertNotIn('explicit-canary', repr(output))


class EndpointSecretServiceTests(unittest.TestCase):

    def setUp(self):
        self.context = endpoint()
        self.service = EndpointSecretService()
        self.calls = []

        def resolver(locator, context, purpose):
            self.calls.append((locator, context.mode, purpose))
            return b'resolved-secret-canary'

        self.service.register_resolver('test.resolver', resolver)
        self.reference = secret_reference(self.context)
        self.service.register_reference(self.reference)

    def test_secret_scope_must_match_declared_endpoint_mode(self):
        with self.assertRaises(SecurityPolicyError):
            SecretReference(
                reference_id=str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    'cdeadmin-security:invalid-mode-scope',
                )),
                endpoint_id=self.context.endpoint_id,
                endpoint_mode='scratchbird_emulated_legacy',
                secret_kind='database_password',
                storage_kind='test',
                resolver_id='test.resolver',
                locator='vault:item',
                allowed_purposes=frozenset({'connect'}),
                authority_scope='scratchbird_native_auth',
            )

    def test_cross_endpoint_and_cross_mode_injection_never_resolves(self):
        other = endpoint(label='other')
        native = endpoint(
            mode='scratchbird_native',
            endpoint_id=self.context.endpoint_id,
            namespaces={
                'pool': self.context.pool_namespace,
                'session': self.context.session_namespace,
                'cache': self.context.cache_namespace,
                'diagnostic': self.context.diagnostic_namespace,
            },
        )
        for context in (other, native):
            with self.assertRaises(SecretAccessError):
                self.service.acquire(
                    self.reference.reference_id, context,
                    'principal:one', 'connect',
                )
        self.assertEqual([], self.calls)

    def test_legacy_engine_credential_never_enters_native_mode(self):
        compatibility = endpoint(mode='legacy_native')
        service = EndpointSecretService()
        calls = []
        service.register_resolver(
            'test.resolver',
            lambda *_args: calls.append(True) or b'secret',
        )
        reference = secret_reference(compatibility)
        service.register_reference(reference)
        native = endpoint(
            mode='scratchbird_native',
            endpoint_id=compatibility.endpoint_id,
            namespaces={
                'pool': compatibility.pool_namespace,
                'session': compatibility.session_namespace,
                'cache': compatibility.cache_namespace,
                'diagnostic': compatibility.diagnostic_namespace,
            },
        )
        with self.assertRaises(SecretAccessError):
            service.acquire(
                reference.reference_id, native,
                'principal:one', 'connect',
            )
        self.assertEqual([], calls)

    def test_purpose_and_permission_are_both_required(self):
        no_permission = endpoint(permissions=())
        for context, purpose in (
            (self.context, 'native-control'),
            (no_permission, 'connect'),
        ):
            with self.assertRaises(SecretAccessError):
                self.service.acquire(
                    self.reference.reference_id, context,
                    'principal:one', purpose,
                )
        self.assertEqual([], self.calls)

    def test_lease_is_zeroized_and_value_never_enters_repr(self):
        lease = self.service.acquire(
            self.reference.reference_id, self.context,
            'principal:one', 'connect',
        )
        with lease:
            observed = lease.use(lambda view: bytes(view))
            self.assertEqual(b'resolved-secret-canary', observed)
            self.assertNotIn('resolved-secret-canary', repr(lease))
        self.assertTrue(lease.closed)
        self.assertEqual({0}, set(lease._buffer))
        with self.assertRaises(SecretAccessError):
            lease.use(bytes)

    def test_snapshot_audit_and_failures_never_export_secret_or_locator(self):
        with self.service.acquire(
            self.reference.reference_id, self.context,
            'principal:one', 'connect',
        ):
            pass
        snapshot = self.service.snapshot()
        audit = self.service.audit_events()
        for exported in (snapshot, audit, repr(self.reference)):
            self.assertNotIn('resolved-secret-canary', repr(exported))
            self.assertNotIn('locator-do-not-export', repr(exported))
        self.assertIn('locator_digest', snapshot[0])

    def test_resolver_error_detail_is_not_exported(self):
        service = EndpointSecretService()
        service.register_resolver(
            'test.resolver',
            lambda *_args: (_ for _ in ()).throw(
                RuntimeError('resolver-secret-detail')
            ),
        )
        service.register_reference(self.reference)
        with self.assertRaisesRegex(SecretAccessError, 'resolution failed'):
            service.acquire(
                self.reference.reference_id, self.context,
                'principal:one', 'connect',
            )
        self.assertNotIn(
            'resolver-secret-detail', repr(service.audit_events())
        )

    def test_empty_secret_is_refused(self):
        service = EndpointSecretService()
        service.register_resolver('test.resolver', lambda *_args: b'')
        service.register_reference(self.reference)
        with self.assertRaisesRegex(SecretAccessError, 'resolution failed'):
            service.acquire(
                self.reference.reference_id, self.context,
                'principal:one', 'connect',
            )

    def test_expected_secret_kind_is_enforced_before_resolution(self):
        reference = SecretReference(
            reference_id=str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                'cdeadmin-security:wrong-secret-kind',
            )),
            endpoint_id=self.context.endpoint_id,
            endpoint_mode=self.context.mode,
            secret_kind='api_token',
            storage_kind='test_vault',
            resolver_id='test.resolver',
            locator='vault://wrong-kind',
            allowed_purposes=frozenset({'connect'}),
            authority_scope='legacy_engine_auth',
        )
        self.service.register_reference(reference)
        with self.assertRaises(SecretAccessError):
            self.service.acquire(
                reference.reference_id, self.context,
                'principal:one', 'connect',
                expected_kind='database_password',
            )
        self.assertEqual([], self.calls)


class RuntimeIdentityPolicyTests(unittest.TestCase):

    def setUp(self):
        self.service = SecurityService()

    def test_actual_legacy_engine_cannot_silently_switch_to_scratchbird(self):
        context = endpoint(mode='legacy_native')
        with self.assertRaises(RuntimeIdentityError):
            self.service.admit_runtime(
                context, verified_claim(context, family='scratchbird')
            )

    def test_runtime_claim_cannot_switch_endpoint_mode(self):
        context = endpoint(mode='legacy_native')
        claim = verified_claim(context)
        changed = copy.copy(claim)
        object.__setattr__(
            changed, 'endpoint_mode', 'scratchbird_native'
        )
        with self.assertRaises(RuntimeIdentityError):
            self.service.admit_runtime(context, changed)

    def test_runtime_claim_cannot_rewrite_declared_engine_family(self):
        context = endpoint(mode='legacy_native')
        claim = verified_claim(context)
        changed = copy.copy(claim)
        object.__setattr__(changed, 'declared_runtime_family', 'mysql')
        object.__setattr__(changed, 'verified_runtime_family', 'mysql')
        with self.assertRaises(RuntimeIdentityError):
            self.service.admit_runtime(context, changed)

    def test_same_advertised_profile_has_no_server_implementation_field(self):
        reference = endpoint(mode='legacy_native', label='reference')
        compatible = endpoint(mode='legacy_native', label='compatible')
        reference = self.service.admit_runtime(
            reference, verified_claim(reference)
        )
        compatible = self.service.admit_runtime(
            compatible, verified_claim(compatible)
        )
        self.assertEqual(reference.profile_id, compatible.profile_id)
        self.assertEqual(
            reference.verified_runtime_family,
            compatible.verified_runtime_family,
        )
        self.assertFalse(hasattr(reference, 'verified_emulation_profile_id'))

    def test_native_mode_requires_scratchbird_identity(self):
        context = endpoint(mode='scratchbird_native')
        admitted = self.service.admit_runtime(
            context, verified_claim(context)
        )
        self.assertEqual('scratchbird', admitted.verified_runtime_family)
        with self.assertRaises(RuntimeIdentityError):
            self.service.admit_runtime(
                context,
                verified_claim(context, family='postgresql')
            )

    def test_destructive_action_fails_closed_until_runtime_verified(self):
        context = endpoint(mode='legacy_native')
        self.assertTrue(self.service.authorize(context, 'read'))
        with self.assertRaises(RuntimeIdentityError):
            self.service.authorize(context, 'destructive')
        verified = self.service.admit_runtime(
            context, verified_claim(context)
        )
        self.assertTrue(self.service.authorize(verified, 'destructive'))

    def test_legacy_authentication_never_grants_native_control(self):
        legacy = endpoint(mode='legacy_native')
        legacy = self.service.admit_runtime(
            legacy, verified_claim(legacy)
        )
        with self.assertRaises(RuntimeIdentityError):
            self.service.authorize(
                legacy, 'admin', 'scratchbird_native_control'
            )

    def test_native_control_requires_verified_native_endpoint(self):
        native = endpoint(mode='scratchbird_native')
        with self.assertRaises(RuntimeIdentityError):
            self.service.authorize(
                native, 'admin', 'scratchbird_native_control'
            )
        native = self.service.admit_runtime(native, verified_claim(native))
        self.assertTrue(self.service.authorize(
            native, 'admin', 'scratchbird_native_control'
        ))

    def test_unknown_authority_scope_fails_closed(self):
        context = endpoint()
        with self.assertRaises(SecurityPolicyError):
            self.service.authorize(context, 'read', 'future-authority')


class IsolationAndSnapshotTests(unittest.TestCase):

    def setUp(self):
        self.service = SecurityService()

    def test_same_endpoint_namespaces_are_still_separated_by_mode(self):
        legacy = endpoint(mode='legacy_native')
        namespaces = {
            'pool': legacy.pool_namespace,
            'session': legacy.session_namespace,
            'cache': legacy.cache_namespace,
            'diagnostic': legacy.diagnostic_namespace,
        }
        native = endpoint(
            mode='scratchbird_native',
            endpoint_id=legacy.endpoint_id,
            namespaces=namespaces,
        )
        legacy_key = self.service.isolation_key(
            legacy, 'pool', 'principal:one', 'credential:legacy'
        )
        native_key = self.service.isolation_key(
            native, 'pool', 'principal:one', 'credential:legacy'
        )
        self.assertNotEqual(legacy_key, native_key)

    def test_principal_credential_generation_and_purpose_change_keys(self):
        context = endpoint()
        values = {
            self.service.isolation_key(
                context, 'pool', 'principal:one', 'credential:one', 'g1'
            ),
            self.service.isolation_key(
                context, 'pool', 'principal:two', 'credential:one', 'g1'
            ),
            self.service.isolation_key(
                context, 'pool', 'principal:one', 'credential:two', 'g1'
            ),
            self.service.isolation_key(
                context, 'pool', 'principal:one', 'credential:one', 'g2'
            ),
            self.service.isolation_key(
                context, 'session', 'principal:one', 'credential:one', 'g1'
            ),
        }
        self.assertEqual(5, len(values))

    def test_pool_and_session_require_credential_reference(self):
        context = endpoint()
        for purpose in ('pool', 'session'):
            with self.assertRaises(IsolationPolicyError):
                self.service.isolation_key(
                    context, purpose, 'principal:one'
                )
        with self.assertRaises(IsolationPolicyError):
            self.service.isolation_key(
                context, 'future-purpose', 'principal:one'
            )

    def test_runtime_verification_changes_provider_and_cache_identity(self):
        context = endpoint()
        verified = self.service.admit_runtime(
            context, verified_claim(context)
        )
        self.assertNotEqual(context.isolation_key, verified.isolation_key)
        before = self.service.isolation_key(
            context, 'cache', 'principal:one'
        )
        after = self.service.isolation_key(
            verified, 'cache', 'principal:one'
        )
        self.assertNotEqual(before, after)

    def test_capability_snapshot_is_endpoint_mode_and_runtime_bound(self):
        context = endpoint()
        snapshot = self.service.capability_snapshot(
            context, 'capability-generation-one',
            {'catalog.read'}, {'data_read'},
        ).to_dict()
        self.assertEqual(context.endpoint_id, snapshot['endpoint_id'])
        self.assertEqual(context.mode, snapshot['endpoint_mode'])
        self.assertEqual(64, len(snapshot['runtime_identity_digest']))
        self.assertNotIn('password', repr(snapshot).casefold())


def provider_manifest():
    return {
        'identity': {
            'contract_version': '1.0.0',
            'provider_id': PROVIDER_ID,
            'provider_version': PROVIDER_VERSION,
            'profile_id': 'security-test',
            'profile_version': '1.0.0',
            'evidence_reference': 'cde-prep-100:test-provider',
        },
        'package_type': 'engine',
        'sdk_compatibility': {
            'minimum': '1.0.0', 'maximum_exclusive': '2.0.0',
        },
        'support_state': 'experimental',
        'enabled': True,
        'fixture': False,
        'production_registration': True,
        'contracts': ['EndpointProvider'],
        'permissions': [
            {
                'permission_id': 'network', 'granted': True,
                'scope': ['endpoint'],
            },
            {
                'permission_id': 'filesystem', 'granted': False,
                'scope': [],
            },
        ],
        'required_permissions': ['network'],
        'composition': {
            'experience_families': ['postgresql'],
            'target_adapter_ids': ['legacy_native-adapter'],
        },
        'extension_schema': None,
        'provenance': {'purpose': 'CDE-PREP-100 security test'},
    }


class SecurityProvider:
    def __init__(self, _context, permissions):
        self.permissions = permissions
        self.closed = False

    @staticmethod
    def validate_endpoint(request):
        return request

    @staticmethod
    def discover_endpoint(request):
        return request

    def close(self):
        self.closed = True


class ProviderQuarantineTests(unittest.TestCase):

    def registry_and_binding(self):
        registry = ProviderRegistry(
            allowed_module_prefixes=('cdeadmin_security_test.',)
        )
        module = SimpleNamespace(create_provider=SecurityProvider)
        with patch(
            'pgadmin.cdeadmin.core.registry.importlib.import_module',
            return_value=module,
        ):
            registry.register_package(
                provider_manifest(), 'cdeadmin_security_test.provider'
            )
        context = endpoint(permissions=('network',))
        return registry, registry.resolve(context)

    def test_required_permission_violation_quarantines_and_closes(self):
        registry, binding = self.registry_and_binding()
        provider = binding.instance
        with self.assertRaises(ProviderPermissionError):
            binding.require_permission('filesystem')
        status = registry.status()[0]
        self.assertEqual('quarantined', status['state'])
        self.assertEqual(
            'CDE_PROVIDER_PERMISSION_VIOLATION',
            status['diagnostic_code'],
        )
        self.assertTrue(provider.closed)
        self.assertEqual(0, status['endpoint_binding_count'])

    def test_optional_permission_probe_does_not_quarantine(self):
        registry, binding = self.registry_and_binding()
        self.assertFalse(binding.permissions.allows('filesystem'))
        self.assertEqual('active', registry.status()[0]['state'])
        self.assertFalse(binding.instance.closed)

    def test_factory_cannot_catch_violation_and_continue_binding(self):
        candidates = []

        class CatchingProvider(SecurityProvider):
            def __init__(self, context, permissions):
                try:
                    permissions.require('filesystem')
                except ProviderPermissionError:
                    pass
                super().__init__(context, permissions)
                candidates.append(self)

        registry = ProviderRegistry(
            allowed_module_prefixes=('cdeadmin_security_test.',)
        )
        module = SimpleNamespace(create_provider=CatchingProvider)
        with patch(
            'pgadmin.cdeadmin.core.registry.importlib.import_module',
            return_value=module,
        ):
            registry.register_package(
                provider_manifest(), 'cdeadmin_security_test.catching'
            )
        with self.assertRaises(ProviderUnavailableError):
            registry.resolve(endpoint(permissions=('network',)))
        status = registry.status()[0]
        self.assertEqual('quarantined', status['state'])
        self.assertEqual(
            'CDE_PROVIDER_PERMISSION_VIOLATION',
            status['diagnostic_code'],
        )
        self.assertEqual(0, status['endpoint_binding_count'])
        self.assertEqual(1, len(candidates))
        self.assertTrue(candidates[0].closed)


class ApplicationIntegrationTests(unittest.TestCase):

    def test_security_service_initialization_is_idempotent(self):
        app = SimpleNamespace(extensions={})
        first = init_app(app)
        self.assertIs(first, init_app(app))
        self.assertIs(first, service_for_app(app))


if __name__ == '__main__':
    unittest.main()
