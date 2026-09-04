##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Integrated provider workspace tests for relational endpoint pilots."""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.core import EndpointContext  # noqa: E402
from pgadmin.cdeadmin.data_studio import DataStudioService  # noqa: E402
from pgadmin.cdeadmin.operations import OperationBus  # noqa: E402
from pgadmin.cdeadmin.providers.mysql_family.provider import (  # noqa: E402
    MYSQL_PROFILE,
    MySQLPilotProvider,
)
from pgadmin.cdeadmin.resources import ResourceExplorerService  # noqa: E402
from pgadmin.cdeadmin.results import (  # noqa: E402
    InlineRendererExecutor,
    ResultService,
)
from pgadmin.cdeadmin.workspace import (  # noqa: E402
    ProviderWorkspaceService,
)
from pgadmin.cdeadmin.visual_admin.provider import (  # noqa: E402
    VisualAdminAccessError,
    VisualAdminExecutionError,
)


class Permissions:
    def require(self, _permission, _scope='endpoint'):
        return None


class PilotClient:
    transaction_actions = ('commit', 'rollback')

    def __init__(self):
        self.token = object()
        self.handle = SimpleNamespace(close=lambda: None)
        self.cancelled = False
        self.admin_request = None
        self.resource_count = 1
        self.transaction_action = None

    @staticmethod
    def runtime_identity(_request=None, _handle=None):
        return {
            'engine_id': 'mysql',
            'version': '9.7.0',
            'build_id': 'workspace-test',
            'protocol_id': 'mysql_wire',
        }

    def list_resources(self, _request):
        return [{
            'resource_id': f'database:example-{index}',
            'resource_kind': 'database',
            'display_name': 'example' if index == 0 else f'example-{index}',
            'authority_path': ['database', f'example-{index}'],
            'generation': 'workspace-test',
        } for index in range(self.resource_count)]

    @staticmethod
    def inspect_resource(request):
        return request

    def open_session(self, _request):
        return self.handle

    def execute(self, handle, _request):
        assert handle is self.handle
        return self.token

    def describe_result(self, token):
        assert token is self.token
        return {
            'result_kind': 'tabular',
            'schema': {'columns': [
                {'name': 'answer', 'native_type': 'INTEGER'},
            ]},
            'payload': {
                'rows': [(42,)],
                'rowcount': 1,
                'cancelled': self.cancelled,
            },
            'stream_reference': None,
            'complete': True,
        }

    def cancel(self, token):
        assert token is self.token
        self.cancelled = True
        return True

    @staticmethod
    def describe_transaction(_handle):
        return {
            'driver_observation_only': True,
            'opaque_word': 'active-looking-but-uninterpreted',
            'finality_interpreted_by_common_code': False,
        }

    def control_transaction(self, handle, action):
        assert handle is self.handle
        self.transaction_action = action

    @staticmethod
    def describe_security(_request):
        return {}

    @staticmethod
    def supports_admin_operation(_resource_kind, _operation_id):
        return True

    def plan_admin_operation(self, request):
        self.admin_request = request
        return {
            'command_preview': {'operation': 'provider-test'},
            'provider_payload': {'route_seen': request['_provider_route']},
            'warnings': [],
        }

    @staticmethod
    def apply_admin_operation(_request):
        return {'accepted': True}

    @staticmethod
    def close():
        return None


class Registry:
    def __init__(self, binding):
        self.binding = binding

    def resolve(self, context):
        if context.endpoint_id != self.binding.context.endpoint_id:
            raise RuntimeError('wrong endpoint')
        return self.binding


class EndpointService:
    def __init__(self, registry, context, endpoint, root):
        self.provider_registry = registry
        self._workspace = (context, endpoint, root)

    def workspace(self, _server):
        return self._workspace


def context():
    endpoint_id = uuid.uuid4()

    def namespace(label):
        return str(uuid.uuid5(endpoint_id, label))

    return EndpointContext(
        endpoint_id=str(endpoint_id),
        mode='legacy_native',
        experience_family='mysql',
        provider_id=MYSQL_PROFILE.provider_id,
        provider_version='0.1.0',
        profile_id=MYSQL_PROFILE.profile_id,
        profile_version=MYSQL_PROFILE.exact_version,
        target_adapter_id='mysql-wire-client',
        target_adapter_version='26.7.0',
        pool_namespace=namespace('pool'),
        session_namespace=namespace('session'),
        cache_namespace=namespace('cache'),
        diagnostic_namespace=namespace('diagnostic'),
        effective_permissions=frozenset({
            'network', 'secret_read', 'data_read', 'data_write',
            'administer', 'execute',
        }),
        declared_runtime_family='mysql',
        verified_runtime_family='mysql',
        verified_runtime_version='9.7.0',
        runtime_verification_state='verified',
        runtime_evidence_reference='workspace:test',
    )


class ProviderWorkspaceTests(unittest.TestCase):

    def setUp(self):
        self.context = context()
        self.client = PilotClient()
        provider = MySQLPilotProvider(
            self.context, Permissions(), self.client
        )
        identity = provider._identity()
        self.binding = SimpleNamespace(
            context=self.context,
            instance=provider,
            manifest={
                'identity': identity,
                'contracts': ['ResourceProvider', 'ResultRenderer'],
            },
            require_permission=lambda *_args: None,
        )
        registry = Registry(self.binding)
        endpoint = {
            'identity': identity,
            'endpoint_id': self.context.endpoint_id,
            'mode': self.context.mode,
            'declared_runtime': {'engine_id': 'mysql'},
            'verified_runtime': {'engine_id': 'mysql'},
            'route': {'route_id': 'route-one'},
            'capability_generation': self.context.cache_namespace,
            'extensions': {},
        }
        root = {
            'identity': identity,
            'endpoint_id': self.context.endpoint_id,
            'resource_id': f'endpoint:{self.context.endpoint_id}',
            'identity_kind': 'cdeadmin-endpoint-id',
            'resource_kind': 'server',
            'model_family': 'relational',
            'display_name': 'MySQL test',
            'parent_resource_id': None,
            'display_path': ['MySQL test'],
            'authority_path': ['mysql', 'endpoint'],
            'is_virtual': True,
            'generation': self.context.cache_namespace,
            'capability_ids': [],
            'extensions': {},
        }
        results = ResultService(
            registry, executor=InlineRendererExecutor()
        )
        studio = DataStudioService(registry, result_service=results)
        resources = ResourceExplorerService(registry)
        endpoints = EndpointService(
            registry, self.context, endpoint, root
        )
        self.workspace = ProviderWorkspaceService(
            endpoints, resources, studio, results
        )

    def test_bootstrap_browses_resources_and_languages(self):
        payload = self.workspace.bootstrap(SimpleNamespace())
        self.assertEqual('mysql', payload['endpoint'][
            'verified_runtime_family'
        ])
        self.assertEqual(
            'mysql-sql', payload['languages'][0]['language_profile']
        )
        self.assertEqual(
            'example', payload['resource_page']['items'][0]['display_name']
        )
        self.assertEqual('mysql', payload['visual_admin']['engine_id'])
        self.assertTrue(payload['visual_admin']['provider_driven'])

    def test_resource_paging_is_generation_bound_and_root_scoped(self):
        self.client.resource_count = 501
        first = self.workspace.bootstrap(SimpleNamespace())['resource_page']
        self.assertEqual(500, len(first['items']))
        self.assertIsNotNone(first['next_cursor'])
        second = self.workspace.resource_page(SimpleNamespace(), {
            'continuation': first['next_cursor'],
            'generation': first['generation'],
        })
        self.assertEqual(1, len(second['items']))
        self.assertEqual('example-500', second['items'][0]['display_name'])
        with self.assertRaisesRegex(
                Exception, 'unsupported fields'):
            self.workspace.resource_page(
                SimpleNamespace(), {'parent_resource': {'forged': True}}
            )

    def test_resource_inspection_uses_cached_identity_and_generation(self):
        page = self.workspace.bootstrap(SimpleNamespace())['resource_page']
        resource = page['items'][0]
        inspected = self.workspace.inspect_resource(SimpleNamespace(), {
            'resource_id': resource['resource_id'],
            'generation': page['generation'],
        })
        self.assertEqual(resource['resource_id'], inspected['resource_id'])
        with self.assertRaisesRegex(Exception, 'invalid'):
            self.workspace.inspect_resource(SimpleNamespace(), {
                'resource_id': resource['resource_id'],
                'generation': page['generation'],
                'native': {'forged': True},
            })

    def test_resource_refresh_is_endpoint_scoped_and_generation_bound(self):
        first = self.workspace.bootstrap(SimpleNamespace())['resource_page']
        refreshed = self.workspace.refresh_resources(SimpleNamespace(), {
            'generation': first['generation'],
        })
        self.assertNotEqual(first['generation'], refreshed['generation'])
        self.assertEqual(1, len(refreshed['items']))
        with self.assertRaisesRegex(Exception, 'stale'):
            self.workspace.refresh_resources(SimpleNamespace(), {
                'generation': first['generation'],
            })
        with self.assertRaisesRegex(Exception, 'invalid'):
            self.workspace.refresh_resources(SimpleNamespace(), {
                'generation': refreshed['generation'],
                'endpoint_id': 'forged',
            })

    def test_data_studio_executes_and_renders_provider_rows(self):
        server = SimpleNamespace()
        session = self.workspace.open_session(server, 'mysql-sql')
        occurrence = self.workspace.execute(
            server, session['session_id'], 'SELECT 42'
        )
        response = self.workspace.poll(
            server, occurrence['occurrence_id']
        )
        rendered = response['rendered_result']
        self.assertEqual(
            [{'answer': 42}], rendered['view_model']['rows']
        )
        self.assertEqual(
            'SchemaView/DataGridView', rendered['component_reference']
        )

    def test_transaction_presentation_remains_opaque(self):
        server = SimpleNamespace()
        session = self.workspace.open_session(server, 'mysql-sql')
        presentation = self.workspace.transaction(
            server, session['session_id']
        )
        self.assertEqual(
            'active-looking-but-uninterpreted',
            presentation['provider_payload']['opaque_word'],
        )
        self.assertFalse(
            presentation['provider_payload'][
                'finality_interpreted_by_common_code'
            ]
        )

    def test_transaction_action_is_dispatched_to_provider_client(self):
        server = SimpleNamespace()
        session = self.workspace.open_session(server, 'mysql-sql')
        presentation = self.workspace.transaction_action(
            server, session['session_id'], 'commit'
        )
        self.assertEqual('commit', self.client.transaction_action)
        self.assertEqual(
            MYSQL_PROFILE.transaction_model, presentation['transaction_model']
        )

    def test_retained_result_can_page_export_and_compare(self):
        server = SimpleNamespace()
        session = self.workspace.open_session(server, 'mysql-sql')
        first = self.workspace.execute(
            server, session['session_id'], 'SELECT 42'
        )
        rendered = self.workspace.poll(
            server, first['occurrence_id']
        )['rendered_result']
        result_id = rendered['descriptor']['result_id']
        page = self.workspace.result_page(server, {
            'result_id': result_id, 'cursor': None, 'page_size': 1,
        })
        self.assertEqual([{'answer': 42}], page['view_model']['rows'])
        exported = self.workspace.export_result(server, {
            'result_id': result_id, 'format': 'json',
        })
        self.assertEqual('application/json', exported['media_type'])
        compared = self.workspace.compare_results(server, {
            'left_result_id': result_id, 'right_result_id': result_id,
        })
        self.assertEqual(0, compared['changed_count'])
        self.assertFalse(compared['semantic_equality_inferred'])

    def test_bulk_mutations_are_previewed_and_explicitly_confirmed(self):
        server = SimpleNamespace(user_id=11)
        drafts = [{
            'resource_kind': 'database',
            'operation_id': 'create',
            'target_resource': None,
            'draft': {'name': name, 'options': {}},
        } for name in ('first', 'second')]
        preview = self.workspace.plan_visual_admin_bulk(
            server, {'items': drafts}
        )
        self.assertTrue(preview['ready'])
        self.assertEqual('not-claimed', preview['atomicity'])
        plans = [{
            'plan_id': item['plan']['plan_id'],
            'plan_digest': item['plan']['plan_digest'],
        } for item in preview['plans']]
        with self.assertRaisesRegex(Exception, 'confirmation'):
            self.workspace.apply_visual_admin_bulk(server, {
                'plans': plans, 'confirmed': False,
            })
        applied = self.workspace.apply_visual_admin_bulk(server, {
            'plans': plans, 'confirmed': True,
        })
        self.assertTrue(applied['complete'])
        self.assertEqual(2, applied['applied_count'])
        self.assertFalse(applied['automatic_retry'])

    def test_visual_admin_uses_only_the_server_side_route(self):
        plan = self.workspace.plan_visual_admin(SimpleNamespace(), {
            'resource_kind': 'database',
            'operation_id': 'create',
            'target_resource': None,
            'draft': {'name': 'example', 'options': {}},
            '_provider_route': {'route_id': 'browser-forgery'},
        })
        self.assertEqual(
            {'route_id': 'route-one'},
            self.client.admin_request['_provider_route'],
        )
        self.assertNotIn('route-one', str(plan))

    def test_visual_admin_records_restart_safe_public_audit(self):
        bus = OperationBus()
        self.workspace.operation_bus = bus
        server = SimpleNamespace(user_id=7)
        plan = self.workspace.plan_visual_admin(server, {
            'resource_kind': 'database',
            'operation_id': 'create',
            'target_resource': None,
            'draft': {'name': 'auditdb', 'options': {}},
        })
        result = self.workspace.apply_visual_admin(server, {
            'plan_id': plan['plan_id'],
            'plan_digest': plan['plan_digest'],
            'confirmed': True,
        })
        operation_id = result['control_operation']['operation_id']
        durable = bus.get_provider_audit(
            self.context.endpoint_id, 'user:7', operation_id
        )
        self.assertEqual('create', durable['operation_kind'])
        self.assertNotIn('route-one', repr(bus.store.export_state()))
        listed = self.workspace.visual_admin_operation_action(
            server, 'visual_admin_operation_list', {}
        )
        self.assertTrue(listed['restart_safe_audit'])
        self.assertTrue(listed['items'][0]['durable_audit'])
        self.assertTrue(
            listed['items'][0]['live_provider_handle_available']
        )

        original = self.binding.instance.get_visual_admin_operation
        self.binding.instance.get_visual_admin_operation = lambda _request: (
            (_ for _ in ()).throw(
                VisualAdminAccessError('provider operation is unavailable')
            )
        )
        try:
            recovered = self.workspace.visual_admin_operation_action(
                server, 'visual_admin_operation_get', {
                    'operation_id': operation_id,
                }
            )
        finally:
            self.binding.instance.get_visual_admin_operation = original
        self.assertEqual(operation_id, recovered['operation_id'])
        self.assertTrue(recovered['durable_audit'])
        self.assertFalse(recovered['live_provider_handle_available'])
        self.assertTrue(recovered['restart_safe_audit'])

    def test_unknown_apply_outcome_is_durably_audited(self):
        bus = OperationBus()
        self.workspace.operation_bus = bus
        invalidated = []
        original_invalidate = self.workspace.resource_service.invalidate
        self.workspace.resource_service.invalidate = (
            lambda current: invalidated.append(current.endpoint_id)
        )
        self.addCleanup(
            setattr, self.workspace.resource_service, 'invalidate',
            original_invalidate,
        )
        server = SimpleNamespace(user_id=8)
        plan = self.workspace.plan_visual_admin(server, {
            'resource_kind': 'database',
            'operation_id': 'create',
            'target_resource': None,
            'draft': {'name': 'unknown', 'options': {}},
        })

        def response_lost(_request):
            raise TimeoutError('simulated provider response loss')

        self.client.apply_admin_operation = response_lost
        with self.assertRaises(VisualAdminExecutionError):
            self.workspace.apply_visual_admin(server, {
                'plan_id': plan['plan_id'],
                'plan_digest': plan['plan_digest'],
                'confirmed': True,
            })
        audited = bus.list_provider_audit(
            self.context.endpoint_id, 'user:8'
        )
        self.assertEqual(1, len(audited))
        self.assertTrue(audited[0]['unknown_outcome'])
        self.assertEqual(
            'provider_response_unavailable', audited[0]['stage']
        )
        self.assertFalse(audited[0]['automatic_mutation_retry'])
        self.assertEqual([self.context.endpoint_id], invalidated)

    def test_failed_provider_observation_updates_durable_audit(self):
        bus = OperationBus()
        self.workspace.operation_bus = bus
        server = SimpleNamespace(user_id=9)
        plan = self.workspace.plan_visual_admin(server, {
            'resource_kind': 'database',
            'operation_id': 'create',
            'target_resource': None,
            'draft': {'name': 'observe', 'options': {}},
        })
        result = self.workspace.apply_visual_admin(server, {
            'plan_id': plan['plan_id'],
            'plan_digest': plan['plan_digest'],
            'confirmed': True,
        })
        operation_id = result['control_operation']['operation_id']

        def observation_lost(_request):
            raise TimeoutError('simulated observation response loss')

        self.client.inspect_admin_operation = observation_lost
        with self.assertRaises(VisualAdminExecutionError):
            self.workspace.visual_admin_operation_action(
                server, 'visual_admin_operation_refresh', {
                    'operation_id': operation_id,
                }
            )
        audited = bus.get_provider_audit(
            self.context.endpoint_id, 'user:9', operation_id
        )
        self.assertEqual(
            'observation_response_unavailable', audited['stage']
        )
        self.assertTrue(audited['unknown_outcome'])
        self.assertEqual(
            'observation_response_unavailable',
            audited['events'][-1]['event_kind'],
        )


if __name__ == '__main__':
    unittest.main()
