##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Resource graph and common explorer tests for CDE-PREP-060."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
FIXTURE = (
    ROOT / 'tools/tests/fixtures/cdeadmin_resources/'
    'non_operational_story.json'
)
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    pgadmin_package = ModuleType('pgadmin')
    pgadmin_package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = pgadmin_package

from pgadmin.cdeadmin.core import (  # noqa: E402
    EndpointContext,
    ProviderPermissionError,
)
from pgadmin.cdeadmin.resources import (  # noqa: E402
    ResourceAccessError,
    ResourceCommandContribution,
    ResourceExplorerService,
    ResourceGraphError,
    ResourceInspectorContribution,
    ResourceRef,
    StaleResourceGenerationError,
    init_app,
    service_for_app,
)
from pgadmin.cdeadmin.security import (  # noqa: E402
    RuntimeIdentityClaim,
    SecurityService,
)


PROVIDER_ID = 'org.example.resources'
PROVIDER_VERSION = '1.0.0'


def context(label='one', permissions=('data_read',)):
    endpoint_id = uuid.uuid5(uuid.NAMESPACE_URL, f'resources:{label}')

    def namespace(purpose):
        return str(uuid.uuid5(endpoint_id, purpose))

    return EndpointContext(
        endpoint_id=str(endpoint_id),
        mode='legacy_native',
        experience_family='relational',
        provider_id=PROVIDER_ID,
        provider_version=PROVIDER_VERSION,
        profile_id='resource-test',
        profile_version='1.0.0',
        target_adapter_id='test-adapter',
        target_adapter_version='1.0.0',
        pool_namespace=namespace('pool'),
        session_namespace=namespace('session'),
        cache_namespace=namespace('cache'),
        diagnostic_namespace=namespace('diagnostic'),
        effective_permissions=frozenset(permissions),
    )


def identity():
    return {
        'contract_version': '1.0.0',
        'provider_id': PROVIDER_ID,
        'provider_version': PROVIDER_VERSION,
        'profile_id': 'resource-test',
        'profile_version': '1.0.0',
        'evidence_reference': 'cde-prep-060:test-provider',
    }


def resource(ctx, resource_id, *, parent_id=None, name=None,
             kind='table', capabilities=('catalog.read',)):
    return {
        'identity': identity(),
        'endpoint_id': ctx.endpoint_id,
        'resource_id': resource_id,
        'identity_kind': 'provider-opaque',
        'resource_kind': kind,
        'model_family': 'relational',
        'display_name': name or resource_id,
        'parent_resource_id': parent_id,
        'display_path': [name or resource_id],
        'authority_path': ['not', 'interpreted'],
        'is_virtual': False,
        'generation': ctx.cache_namespace,
        'capability_ids': list(capabilities),
        'extensions': {'provider': {'opaque': True}},
    }


def parent(ctx, label='parent'):
    return resource(
        ctx,
        f'opaque::{label}/?do-not-parse=true',
        kind='schema',
        capabilities=(),
    )


class FakePermissions:
    def __init__(self, allowed=('data_read',)):
        self.allowed = frozenset(allowed)
        self.require_calls = []

    def require(self, permission, scope='endpoint'):
        self.require_calls.append((permission, scope))
        if permission not in self.allowed:
            raise ProviderPermissionError('withheld')

    def allows(self, permission, scope='endpoint'):
        return permission in self.allowed


class FakeProvider:
    def __init__(self, ctx, count=5):
        self.ctx = ctx
        self.count = count
        self.list_calls = 0
        self.inspect_calls = 0
        self.change_identity = False

    def list_resources(self, request):
        self.list_calls += 1
        return [
            resource(
                self.ctx,
                f'opaque:item/{index}?value=:[]',
                parent_id=request['resource_id'],
                name=f'item {index}',
            )
            for index in range(self.count)
        ]

    def inspect_resource(self, request):
        self.inspect_calls += 1
        result = copy.deepcopy(request)
        if self.change_identity:
            result['resource_id'] = 'opaque:changed'
        result['extensions']['inspected'] = True
        return result

    @staticmethod
    def resource_contributions():
        def inspect(binding, item):
            return binding.instance.inspect_resource(item)

        def invoke(binding, item, payload):
            return {
                'resource_id': item['resource_id'],
                'payload': dict(payload),
                'provider': binding.instance,
            }

        return {
            'inspectors': (
                ResourceInspectorContribution(
                    'catalog.inspector', frozenset({'table'}), inspect
                ),
            ),
            'commands': (
                ResourceCommandContribution(
                    'catalog.inspect', 'Inspect', 'catalog.read',
                    frozenset({'table'}), mutation_class='read',
                    required_permission='data_read', invoke=invoke,
                ),
                ResourceCommandContribution(
                    'catalog.drop', 'Drop', 'catalog.drop',
                    frozenset({'table'}), mutation_class='destructive',
                    required_permission='administer', invoke=invoke,
                ),
            ),
        }


class FakeBinding:
    def __init__(self, provider, permissions=None):
        self.instance = provider
        self.permissions = permissions or FakePermissions()
        self.manifest = {
            'identity': identity(),
            'contracts': ['ResourceProvider'],
        }

    def require_permission(self, permission, scope='endpoint'):
        self.permissions.require(permission, scope)


class FakeRegistry:
    def __init__(self, bindings):
        self.bindings = bindings

    def resolve(self, ctx):
        return self.bindings[ctx.endpoint_id]


class ResourceRefTests(unittest.TestCase):

    def test_resource_id_is_retained_as_one_opaque_string(self):
        ctx = context()
        value = 'engine://catalog/a/b?x=1#not-common-syntax'
        ref = ResourceRef(ctx.endpoint_id, value)
        self.assertEqual(value, ref.resource_id)

    def test_resource_ref_rejects_invalid_endpoint_or_empty_identity(self):
        with self.assertRaises(ResourceGraphError):
            ResourceRef('not-a-uuid', 'resource')
        with self.assertRaises(ResourceGraphError):
            ResourceRef(str(uuid.uuid4()), ' ')


class ResourceExplorerTests(unittest.TestCase):

    def setUp(self):
        self.ctx = context()
        self.provider = FakeProvider(self.ctx)
        self.binding = FakeBinding(self.provider)
        self.registry = FakeRegistry({
            self.ctx.endpoint_id: self.binding,
        })
        self.service = ResourceExplorerService(self.registry)
        self.parent = parent(self.ctx)

    def first_page(self, **kwargs):
        return self.service.list_page(
            self.ctx, self.parent, page_size=2, **kwargs
        )

    def test_paging_uses_opaque_cursor_and_cached_provider_result(self):
        first = self.first_page()
        second = self.first_page(cursor=first.next_cursor)
        third = self.first_page(cursor=second.next_cursor)
        self.assertEqual(2, len(first.items))
        self.assertEqual(2, len(second.items))
        self.assertEqual(1, len(third.items))
        self.assertIsNone(third.next_cursor)
        self.assertEqual(5, first.total_count)
        self.assertEqual(1, self.provider.list_calls)

    def test_cursor_cannot_cross_parent_or_endpoint(self):
        first = self.first_page()
        with self.assertRaises(ResourceAccessError):
            self.service.list_page(
                self.ctx, parent(self.ctx, 'other'),
                page_size=2, cursor=first.next_cursor,
            )
        other = context('other')
        other_provider = FakeProvider(other)
        self.registry.bindings[other.endpoint_id] = FakeBinding(
            other_provider
        )
        with self.assertRaises(ResourceAccessError):
            self.service.list_page(
                other, parent(other), page_size=2,
                cursor=first.next_cursor,
            )

    def test_invalid_cursor_and_page_sizes_fail_closed(self):
        for page_size in (0, 501, True, '2'):
            with self.subTest(page_size=page_size):
                with self.assertRaises(ResourceAccessError):
                    self.service.list_page(
                        self.ctx, self.parent, page_size=page_size
                    )
        with self.assertRaises(ResourceAccessError):
            self.first_page(cursor='%%%')
        with self.assertRaises(ResourceAccessError):
            self.first_page(cursor='W10')

    def test_invalidation_rejects_stale_generation_and_cursor(self):
        first = self.first_page()
        self.service.invalidate(self.ctx)
        with self.assertRaises(StaleResourceGenerationError):
            self.first_page(expected_generation=first.generation)
        with self.assertRaises(StaleResourceGenerationError):
            self.first_page(cursor=first.next_cursor)

    def test_endpoint_cache_state_is_not_shared(self):
        first = self.first_page()
        other = context('other')
        other_provider = FakeProvider(other, count=1)
        self.registry.bindings[other.endpoint_id] = FakeBinding(
            other_provider
        )
        second = self.service.list_page(other, parent(other), page_size=2)
        self.assertNotEqual(first.generation, second.generation)
        self.assertEqual(1, other_provider.list_calls)
        self.service.invalidate(other)
        self.assertEqual(first.generation, self.service.cache.generation(
            self.ctx
        ))

    def test_unauthorized_resource_listing_fails_closed(self):
        self.binding.permissions = FakePermissions(allowed=())
        with self.assertRaises(ProviderPermissionError):
            self.first_page()
        self.assertEqual(0, self.provider.list_calls)

    def test_provider_children_must_match_endpoint_parent_and_identity(self):
        original = self.provider.list_resources

        def wrong_endpoint(request):
            item = original(request)[0]
            item['endpoint_id'] = str(uuid.uuid4())
            return [item]

        self.provider.list_resources = wrong_endpoint
        with self.assertRaises(ResourceAccessError):
            self.first_page()
        self.provider.list_resources = original
        self.service.invalidate(self.ctx)

        def wrong_parent(request):
            item = original(request)[0]
            item['parent_resource_id'] = 'another-parent'
            return [item]

        self.provider.list_resources = wrong_parent
        with self.assertRaises(ResourceAccessError):
            self.first_page()

    def test_provider_resource_dto_is_redacted_before_cache_and_output(self):
        original = self.provider.list_resources

        def sensitive_extension(request):
            rows = original(request)
            rows[0]['extensions']['access_token'] = 'resource-token-canary'
            return rows

        self.provider.list_resources = sensitive_extension
        page = self.first_page()
        self.assertEqual(
            '[REDACTED]', page.items[0]['extensions']['access_token']
        )
        self.assertNotIn('resource-token-canary', repr(page.to_dict()))

    def test_unknown_resource_cannot_be_inspected_or_invoked(self):
        unknown = ResourceRef(self.ctx.endpoint_id, 'opaque:unknown')
        with self.assertRaises(ResourceAccessError):
            self.service.inspect(self.ctx, unknown)
        with self.assertRaises(ResourceAccessError):
            self.service.invoke(self.ctx, unknown, 'catalog.inspect')

    def test_inspector_preserves_identity_and_supports_contribution(self):
        page = self.first_page()
        ref = ResourceRef(
            self.ctx.endpoint_id, page.items[0]['resource_id']
        )
        direct = self.service.inspect(self.ctx, ref)
        contributed = self.service.inspect(
            self.ctx, ref, inspector_id='catalog.inspector'
        )
        self.assertTrue(direct['extensions']['inspected'])
        self.assertTrue(contributed['extensions']['inspected'])
        self.provider.change_identity = True
        with self.assertRaises(ResourceAccessError):
            self.service.inspect(self.ctx, ref)

    def test_context_menu_requires_capability_and_permission(self):
        page = self.first_page()
        ref = ResourceRef(
            self.ctx.endpoint_id, page.items[0]['resource_id']
        )
        actions = self.service.context_menu(self.ctx, ref)
        self.assertEqual(['catalog.inspect'], [
            action.command_id for action in actions
        ])
        result = self.service.invoke(
            self.ctx, ref, 'catalog.inspect', {'tab': 'properties'}
        )
        self.assertEqual({'tab': 'properties'}, result['payload'])
        with self.assertRaises(ResourceAccessError):
            self.service.invoke(self.ctx, ref, 'catalog.drop')

    def test_destructive_action_requires_verified_runtime_identity(self):
        self.binding.permissions = FakePermissions(
            allowed=('data_read', 'administer')
        )
        original = self.provider.list_resources

        def destructive_capability(request):
            rows = original(request)
            for row in rows:
                row['capability_ids'].append('catalog.drop')
            return rows

        self.provider.list_resources = destructive_capability
        page = self.first_page()
        ref = ResourceRef(
            self.ctx.endpoint_id, page.items[0]['resource_id']
        )
        self.assertNotIn('catalog.drop', {
            action.command_id
            for action in self.service.context_menu(self.ctx, ref)
        })
        claim = RuntimeIdentityClaim(
            endpoint_id=self.ctx.endpoint_id,
            endpoint_mode=self.ctx.mode,
            declared_runtime_family='relational',
            verification_state='verified',
            verified_runtime_family='relational',
            verified_runtime_version='1.0.0',
            evidence_reference='runtime:test-resource-explorer',
            generation='runtime-generation-one',
        )
        verified = SecurityService().admit_runtime(self.ctx, claim)
        self.assertIn('catalog.drop', {
            action.command_id
            for action in self.service.context_menu(verified, ref)
        })

    def test_explorer_node_has_mode_profile_and_evidence_badges(self):
        page = self.first_page()
        ref = ResourceRef(
            self.ctx.endpoint_id, page.items[0]['resource_id']
        )
        node = self.service.explorer_node(self.ctx, ref)
        self.assertEqual(
            {'endpoint-mode', 'profile', 'evidence'},
            {badge.badge_id for badge in node.badges},
        )
        self.assertFalse(node.non_production)


class FixtureStoryTests(unittest.TestCase):

    def setUp(self):
        endpoint_id = '11111111-1111-4111-8111-111111111111'

        def namespace(purpose):
            return str(uuid.uuid5(uuid.UUID(endpoint_id), purpose))

        self.ctx = EndpointContext(
            endpoint_id=endpoint_id,
            mode='scratchbird_native',
            experience_family='fixture',
            provider_id='org.cdeadmin.fixture.non_operational',
            provider_version='1.0.0',
            profile_id='fixture-only',
            profile_version='1.0.0',
            target_adapter_id='fixture-none',
            target_adapter_version='1.0.0',
            pool_namespace=namespace('pool'),
            session_namespace=namespace('session'),
            cache_namespace=namespace('cache'),
            diagnostic_namespace=namespace('diagnostic'),
        )
        self.service = ResourceExplorerService(FakeRegistry({}))

    def test_fixture_nodes_are_visibly_non_production_and_inert(self):
        nodes = self.service.load_fixture_story(self.ctx, FIXTURE)
        self.assertEqual(2, len(nodes))
        for node in nodes:
            self.assertTrue(node.non_production)
            self.assertEqual((), node.actions)
            self.assertIn('NON-PRODUCTION FIXTURE', [
                badge.label for badge in node.badges
            ])

    def test_fixture_story_without_marker_is_rejected(self):
        payload = json.loads(FIXTURE.read_text(encoding='utf-8'))
        del payload['resources'][0]['extensions']['cdeadmin_fixture']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'story.json'
            path.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaises(ResourceAccessError):
                self.service.load_fixture_story(self.ctx, path)


class ResourceApplicationTests(unittest.TestCase):

    def test_application_service_is_idempotent_and_required(self):
        app = SimpleNamespace(extensions={})
        registry = FakeRegistry({})
        first = init_app(app, registry)
        self.assertIs(first, init_app(app, registry))
        self.assertIs(first, service_for_app(app))
        with self.assertRaises(ResourceGraphError):
            service_for_app(SimpleNamespace(extensions={}))


if __name__ == '__main__':
    unittest.main()
