##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider registry and endpoint-context tests for CDE-PREP-040."""

from __future__ import annotations

import copy
import sys
import unittest
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))

# Import CDEadmin subpackages without executing pgAdmin's full application
# package, whose optional runtime dependencies are outside this focused test.
if 'pgadmin' not in sys.modules:
    pgadmin_package = ModuleType('pgadmin')
    pgadmin_package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = pgadmin_package
if 'pgadmin.utils' not in sys.modules:
    pgadmin_utils_package = ModuleType('pgadmin.utils')
    pgadmin_utils_package.__path__ = [str(WEB / 'pgadmin/utils')]
    sys.modules['pgadmin.utils'] = pgadmin_utils_package
if 'config' not in sys.modules:
    sys.modules['config'] = ModuleType('config')

from pgadmin.cdeadmin.core import (  # noqa: E402
    EndpointContext,
    EndpointContextError,
    ProviderPermissionError,
    ProviderRegistrationError,
    ProviderRegistry,
    ProviderUnavailableError,
    current_endpoint_context,
    endpoint_scope,
    init_app,
    registry_for_app,
)


PROVIDER_ID = 'org.example.multi_engine'
PROVIDER_VERSION = '1.2.3'
EXPERIENCE = 'relational-workbench'
ADAPTER = 'wire-compatible-target'


def manifest():
    return {
        'identity': {
            'contract_version': '1.0.0',
            'provider_id': PROVIDER_ID,
            'provider_version': PROVIDER_VERSION,
            'profile_id': 'example-profile',
            'profile_version': '1.0.0',
            'evidence_reference': 'cde-prep-040:test-provider',
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
        'permissions': [
            {
                'permission_id': 'network',
                'granted': True,
                'scope': ['endpoint'],
            },
            {
                'permission_id': 'execute',
                'granted': True,
                'scope': ['endpoint'],
            },
            {
                'permission_id': 'filesystem',
                'granted': False,
                'scope': [],
            },
        ],
        'required_permissions': ['network'],
        'composition': {
            'experience_families': [EXPERIENCE],
            'target_adapter_ids': [ADAPTER],
        },
        'extension_schema': None,
        'provenance': {'purpose': 'CDE-PREP-040 unit test'},
    }


def endpoint(
    label='one',
    permissions=('network',),
    experience=EXPERIENCE,
    adapter=ADAPTER,
    provider_version=PROVIDER_VERSION,
    legacy_driver_type=None,
):
    endpoint_id = uuid.uuid5(uuid.NAMESPACE_URL, f'endpoint:{label}')

    def namespace(purpose):
        return str(uuid.uuid5(endpoint_id, f'namespace:{purpose}'))

    return EndpointContext(
        endpoint_id=str(endpoint_id),
        mode='legacy_native',
        experience_family=experience,
        provider_id=PROVIDER_ID,
        provider_version=provider_version,
        profile_id='example-profile',
        profile_version='1.0.0',
        target_adapter_id=adapter,
        target_adapter_version='1.0.0',
        pool_namespace=namespace('pool'),
        session_namespace=namespace('session'),
        cache_namespace=namespace('cache'),
        diagnostic_namespace=namespace('diagnostic'),
        effective_permissions=frozenset(permissions),
        legacy_driver_type=legacy_driver_type,
    )


class FakeProvider:
    def __init__(self, context, permissions):
        self.context = context
        self.permissions = permissions
        self.closed = False
        self.driver = object()

    def get_legacy_driver(self, _driver_type):
        return self.driver

    def validate_endpoint(self, request):
        return request

    def discover_endpoint(self, request):
        return request

    def close(self):
        self.closed = True


def provider_module(factory=None):
    return SimpleNamespace(
        create_provider=factory or (
            lambda context, permissions: FakeProvider(context, permissions)
        )
    )


class EndpointContextTests(unittest.TestCase):

    def test_context_validates_distinct_uuid_namespaces(self):
        value = endpoint()
        self.assertEqual(20, len(value.isolation_key))
        duplicate = value.__dict__.copy()
        duplicate['cache_namespace'] = value.pool_namespace
        with self.assertRaises(EndpointContextError):
            EndpointContext(**duplicate)

    def test_context_builds_from_persistence_identity(self):
        value = endpoint()
        identity = {
            'endpoint_id': value.endpoint_id,
            'endpoint_mode': value.mode,
            'experience_family': value.experience_family,
            'provider_id': value.provider_id,
            'provider_version': value.provider_version,
            'profile_id': value.profile_id,
            'profile_version': value.profile_version,
            'target_adapter_id': value.target_adapter_id,
            'target_adapter_version': value.target_adapter_version,
            'pool_namespace': value.pool_namespace,
            'session_namespace': value.session_namespace,
            'cache_namespace': value.cache_namespace,
            'diagnostic_namespace': value.diagnostic_namespace,
        }
        rebuilt = EndpointContext.from_identity(
            identity,
            effective_permissions=('network',),
            legacy_driver_type='legacy-test',
        )
        self.assertEqual(value.isolation_key, rebuilt.isolation_key)
        self.assertEqual('legacy-test', rebuilt.legacy_driver_type)

    def test_endpoint_scope_is_nested_and_restored(self):
        first = endpoint('first')
        second = endpoint('second')
        self.assertIsNone(current_endpoint_context())
        with endpoint_scope(first):
            self.assertIs(first, current_endpoint_context())
            with endpoint_scope(second):
                self.assertIs(second, current_endpoint_context())
            self.assertIs(first, current_endpoint_context())
        self.assertIsNone(current_endpoint_context())


class ProviderRegistryTests(unittest.TestCase):

    def setUp(self):
        self.registry = ProviderRegistry(
            allowed_module_prefixes=('cdeadmin_test_providers.',)
        )

    def register(self, value=None, module=None):
        module = module or provider_module()
        with patch(
            'pgadmin.cdeadmin.core.registry.importlib.import_module',
            return_value=module,
        ):
            return self.registry.register_package(
                value or manifest(), 'cdeadmin_test_providers.example'
            )

    def test_application_registry_is_idempotent_and_app_scoped(self):
        first_app = SimpleNamespace(extensions={})
        second_app = SimpleNamespace(extensions={})
        first = init_app(first_app)
        self.assertIs(first, init_app(first_app))
        self.assertIs(first, registry_for_app(first_app))
        self.assertIsNot(first, init_app(second_app))

    def test_registry_default_admits_current_1_1_provider_contract(self):
        value = manifest()
        value['identity']['contract_version'] = '1.1.0'
        value['sdk_compatibility']['minimum'] = '1.1.0'
        registration = self.register(value=value)
        self.assertEqual('active', registration.state)

    def test_import_failure_is_quarantined_and_redacted(self):
        with patch(
            'pgadmin.cdeadmin.core.registry.importlib.import_module',
            side_effect=RuntimeError('SECRET IMPORT DETAIL'),
        ):
            registration = self.registry.register_package(
                manifest(), 'cdeadmin_test_providers.broken'
            )
        self.assertEqual('quarantined', registration.state)
        status = self.registry.status()[0]
        self.assertEqual(
            'CDE_PROVIDER_IMPORT_FAILED', status['diagnostic_code']
        )
        self.assertEqual('RuntimeError', status['error_type'])
        self.assertNotIn('SECRET', repr(status))
        with self.assertRaises(ProviderUnavailableError):
            self.registry.resolve(endpoint())

    def test_module_outside_admitted_root_is_rejected_before_import(self):
        with patch(
            'pgadmin.cdeadmin.core.registry.importlib.import_module'
        ) as importer:
            with self.assertRaises(ProviderRegistrationError):
                self.registry.register_package(manifest(), 'os.path')
        importer.assert_not_called()

    def test_fixture_and_nonproduction_manifests_are_rejected(self):
        fixture = manifest()
        fixture['fixture'] = True
        fixture['enabled'] = False
        fixture['production_registration'] = False
        fixture['support_state'] = 'deferred'
        fixture['permissions'] = []
        with self.assertRaises(ProviderRegistrationError):
            self.registry.register_package(
                fixture, 'cdeadmin_test_providers.fixture'
            )

    def test_provider_cannot_request_permission_absent_from_manifest(self):
        value = manifest()
        value['required_permissions'] = ['filesystem']
        with patch(
            'pgadmin.cdeadmin.core.registry.importlib.import_module'
        ) as importer:
            with self.assertRaises(ProviderPermissionError):
                self.registry.register_package(
                    value, 'cdeadmin_test_providers.overreach'
                )
        importer.assert_not_called()

    def test_unknown_or_duplicate_permissions_fail_before_import(self):
        unknown = manifest()
        unknown['permissions'].append({
            'permission_id': 'future-root-authority',
            'granted': True,
            'scope': ['endpoint'],
        })
        duplicate = manifest()
        duplicate['permissions'].append(copy.deepcopy(
            duplicate['permissions'][0]
        ))
        with patch(
            'pgadmin.cdeadmin.core.registry.importlib.import_module'
        ) as importer:
            with self.assertRaises(ProviderRegistrationError):
                self.registry.register_package(
                    unknown, 'cdeadmin_test_providers.unknown_permission'
                )
            with self.assertRaises(ProviderRegistrationError):
                self.registry.register_package(
                    duplicate, 'cdeadmin_test_providers.duplicate_permission'
                )
        importer.assert_not_called()

    def test_unknown_package_or_inactive_support_state_fails_closed(self):
        unknown_package = manifest()
        unknown_package['package_type'] = 'future-package'
        inactive = manifest()
        inactive['support_state'] = 'future-support-state'
        with self.assertRaises(ProviderRegistrationError):
            self.registry.register_package(
                unknown_package, 'cdeadmin_test_providers.unknown_package'
            )
        with self.assertRaises(ProviderRegistrationError):
            self.registry.register_package(
                inactive, 'cdeadmin_test_providers.inactive'
            )

    def test_endpoint_cannot_expand_manifest_permissions(self):
        self.register()
        with self.assertRaises(ProviderPermissionError):
            self.registry.resolve(
                endpoint(permissions=('network', 'filesystem'))
            )

    def test_endpoint_policy_must_grant_package_minimum(self):
        self.register()
        with self.assertRaises(ProviderPermissionError):
            self.registry.resolve(endpoint(permissions=()))

    def test_permission_guard_fails_closed_by_permission_and_scope(self):
        self.register()
        binding = self.registry.resolve(
            endpoint(permissions=('network', 'execute'))
        )
        binding.require_permission('network')
        with self.assertRaises(ProviderPermissionError):
            binding.require_permission('filesystem')
        with self.assertRaises(ProviderPermissionError):
            binding.require_permission('network', 'global')

    def test_experience_and_target_adapter_are_composed_independently(self):
        self.register()
        with self.assertRaises(ProviderUnavailableError):
            self.registry.resolve(endpoint(experience='other-experience'))
        with self.assertRaises(ProviderUnavailableError):
            self.registry.resolve(endpoint(adapter='other-adapter'))

    def test_exact_provider_version_is_required(self):
        self.register()
        with self.assertRaises(ProviderUnavailableError):
            self.registry.resolve(endpoint(provider_version=None))
        with self.assertRaises(ProviderUnavailableError):
            self.registry.resolve(endpoint(provider_version='1.2.4'))

    def test_two_endpoints_never_share_provider_instances(self):
        self.register()
        first = self.registry.resolve(endpoint('first'))
        first_again = self.registry.resolve(endpoint('first'))
        second = self.registry.resolve(endpoint('second'))
        self.assertIs(first, first_again)
        self.assertIsNot(first, second)
        self.assertIsNot(first.instance, second.instance)
        self.assertNotEqual(
            first.context.isolation_key, second.context.isolation_key
        )

    def test_permission_sets_never_share_provider_bindings(self):
        self.register()
        verification = self.registry.resolve(
            endpoint('same', permissions=('network',))
        )
        workspace = self.registry.resolve(endpoint(
            'same', permissions=('network', 'execute')
        ))
        self.assertIsNot(verification, workspace)
        self.assertIsNot(verification.instance, workspace.instance)
        self.assertNotEqual(
            verification.context.isolation_key,
            workspace.context.isolation_key,
        )

    def test_factory_failure_quarantines_provider_without_error_detail(self):
        def fail_factory(_context, _permissions):
            raise RuntimeError('SECRET FACTORY DETAIL')

        registration = self.register(module=provider_module(fail_factory))
        with self.assertRaises(ProviderUnavailableError):
            self.registry.resolve(endpoint())
        self.assertEqual('quarantined', registration.state)
        self.assertNotIn('SECRET', repr(self.registry.status()))

    def test_missing_declared_interface_quarantines_provider(self):
        registration = self.register(
            module=provider_module(
                lambda _context, _permissions: object()
            )
        )
        with self.assertRaises(ProviderUnavailableError):
            self.registry.resolve(endpoint())
        self.assertEqual('quarantined', registration.state)
        self.assertEqual(
            'CDE_PROVIDER_FACTORY_FAILED',
            self.registry.status()[0]['diagnostic_code'],
        )

    def test_quarantine_and_unload_close_all_endpoint_instances(self):
        registration = self.register()
        first = self.registry.resolve(endpoint('first')).instance
        second = self.registry.resolve(endpoint('second')).instance
        self.registry.quarantine(PROVIDER_ID, PROVIDER_VERSION)
        self.assertTrue(first.closed)
        self.assertTrue(second.closed)
        self.assertEqual({}, registration.bindings)
        with self.assertRaises(ProviderUnavailableError):
            self.registry.resolve(endpoint('first'))

        other_registry = ProviderRegistry(
            allowed_module_prefixes=('cdeadmin_test_providers.',)
        )
        self.registry = other_registry
        registration = self.register()
        provider = self.registry.resolve(endpoint()).instance
        self.registry.unload(PROVIDER_ID, PROVIDER_VERSION)
        self.assertTrue(provider.closed)
        self.assertEqual('unloaded', registration.state)


class LegacyDriverFacadeTests(unittest.TestCase):

    def test_existing_get_driver_call_uses_unchanged_legacy_registry(self):
        from pgadmin.utils import driver

        legacy = object()
        with patch.object(driver.DriverRegistry, 'get', return_value=legacy):
            self.assertIs(legacy, driver.get_driver('legacy-test'))

    def test_explicit_legacy_marker_allows_contextual_fallback(self):
        from pgadmin.utils import driver

        legacy = object()
        context = endpoint(legacy_driver_type='legacy-test')
        with patch.object(driver.DriverRegistry, 'get', return_value=legacy):
            self.assertIs(
                legacy,
                driver.get_driver('legacy-test', endpoint_context=context),
            )

    def test_context_without_matching_binding_fails_closed(self):
        from pgadmin.utils import driver

        context = endpoint(legacy_driver_type=None)
        with patch.object(driver.DriverRegistry, 'get') as legacy_get:
            with self.assertRaises(ProviderUnavailableError):
                driver.get_driver('legacy-test', endpoint_context=context)
        legacy_get.assert_not_called()

    def test_registered_provider_supplies_explicit_compatibility_driver(self):
        from pgadmin.utils import driver

        registry = ProviderRegistry(
            allowed_module_prefixes=('cdeadmin_test_providers.',)
        )
        module = provider_module()
        with patch(
            'pgadmin.cdeadmin.core.registry.importlib.import_module',
            return_value=module,
        ):
            registry.register_package(
                manifest(), 'cdeadmin_test_providers.example'
            )
        app = SimpleNamespace(extensions={
            'cdeadmin_provider_registry': registry
        })
        context = endpoint(legacy_driver_type='legacy-test')
        expected = registry.resolve(context).instance.driver
        with patch.object(
                driver.DriverRegistry, 'load_modules') as legacy_load, \
                patch.object(driver.DriverRegistry, 'get') as legacy_get:
            actual = driver.get_driver(
                'legacy-test', app=app, endpoint_context=context
            )
        self.assertIs(expected, actual)
        legacy_load.assert_not_called()
        legacy_get.assert_not_called()


class SourceIntegrationTests(unittest.TestCase):

    def test_create_app_initializes_registry_before_legacy_drivers(self):
        source = (WEB / 'pgadmin/__init__.py').read_text(encoding='utf-8')
        security = source.index('init_cde_security(app)')
        cde = source.index(
            'init_cdeadmin(app, cde_security.secrets)'
        )
        legacy = source.index('driver.init_app(app)')
        self.assertLess(security, cde)
        self.assertLess(cde, legacy)

    def test_common_registry_has_no_transaction_transition_authority(self):
        sources = ' '.join(
            path.read_text(encoding='utf-8').lower()
            for path in (WEB / 'pgadmin/cdeadmin/core').glob('*.py')
        )
        forbidden = (
            'def commit',
            'def rollback',
            'def savepoint',
            'def decide_visibility',
            'journal_mode',
            'pragma ',
        )
        for token in forbidden:
            self.assertNotIn(token, sources)


if __name__ == '__main__':
    unittest.main()
