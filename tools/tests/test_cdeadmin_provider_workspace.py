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

import base64
import sys
import unittest
import uuid
from dataclasses import replace
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
    MYSQL_ADMINISTRATION,
    MYSQL_PROFILE,
    MySQLPilotProvider,
)
from pgadmin.cdeadmin.resources import ResourceExplorerService  # noqa: E402
from pgadmin.cdeadmin.results import (  # noqa: E402
    InlineRendererExecutor,
    ResultService,
)
from pgadmin.cdeadmin.semantic_models import SemanticModelService  # noqa: E402
from pgadmin.cdeadmin.workspace import (  # noqa: E402
    ProviderWorkspaceError,
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
        self.session_closed = False

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

    def close_session(self, handle):
        assert handle is self.handle
        self.session_closed = True
        return {
            'connection_released': True,
            'finality_interpreted_by_common_code': False,
        }

    @staticmethod
    def describe_security(_request):
        return {}

    @staticmethod
    def supports_admin_operation(_resource_kind, _operation_id):
        return True

    @staticmethod
    def visual_admin_catalog(catalog):
        return MYSQL_ADMINISTRATION.catalog(catalog)

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
        self.database_catalog_value = {
            'targets': [{
                'target_id': 'target-one',
                'display_name': 'example',
                'database': 'example',
                'active': True,
            }],
        }

    def workspace(self, _server, database_target_id=Ellipsis):
        self.database_target_id = database_target_id
        return self._workspace

    def database_catalog(self, _server):
        return self.database_catalog_value

    @staticmethod
    def route_catalog(_server):
        return {
            'server_forms': {'forms': {}},
            'routes': [],
        }


class DeliveryService:
    def __init__(self):
        self.request = None

    @staticmethod
    def catalog():
        return {'manual_delivery': True, 'profiles': [{
            'profile_id': 'archive', 'kind': 's3', 'label': 'Archive',
        }], 'automatic_scheduling': False}

    def deliver(self, user_id, endpoint_id, request):
        self.request = (user_id, endpoint_id, request)
        return {
            'state': 'delivered', 'occurrence_id': str(uuid.uuid4()),
            'automatic_retry': False,
        }

    @staticmethod
    def list(_user_id, _endpoint_id):
        return [{'state': 'delivered'}]


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
        self.provider = provider
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
        self.endpoints = endpoints
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
        self.assertEqual(
            'cdeadmin.operational-workspace.v1',
            payload['operational_workspace']['schema'],
        )
        self.assertEqual(
            'mysql', payload['operational_workspace']['engine_id']
        )
        self.assertFalse(payload['operational_workspace']['distributed'])
        self.assertEqual(
            27, len(payload['operational_workspace']['facets'])
        )
        self.assertEqual(
            'cdeadmin.provider-grid-workspace.v1',
            payload['grid_workspace']['schema'],
        )
        self.assertEqual(
            'passed', payload['grid_workspace']['adoption_state']
        )
        self.assertEqual(
            'passed', payload['grid_workspace']['runtime_gate']['state']
        )

    def test_service_scoped_bootstrap_does_not_attach_to_database(self):
        descriptor = self.provider.visual_admin_descriptor()
        database_descriptor = next(
            item for item in descriptor['objects']
            if item['resource_kind'] == 'database'
        )
        operation = database_descriptor['operations'][0]
        operation.update({
            'operation_id': 'bring_online',
            'workspace_scope': 'server_service',
        })
        self.provider.visual_admin_descriptor = lambda: descriptor
        self.workspace.resource_service = SimpleNamespace(
            list_page=lambda *_args, **_kwargs: self.fail(
                'service-scoped bootstrap must not list database resources'
            )
        )
        payload = self.workspace.bootstrap(
            SimpleNamespace(name='localhost'),
            database_target_id='target-one',
            focused_operation_id='bring_online',
        )
        target = payload['resource_page']['items'][0]
        self.assertEqual('database', target['resource_kind'])
        self.assertEqual('example', target['display_name'])
        self.assertTrue(
            target['extensions']['cdeadmin']['service_scope_only']
        )
        self.assertEqual(
            'target-one',
            target['extensions']['cdeadmin']['database_target_id'],
        )
        self.assertEqual('target-one', self.endpoints.database_target_id)

    def test_focused_operation_must_be_declared_by_provider(self):
        with self.assertRaisesRegex(
                ProviderWorkspaceError, 'operation is unavailable'):
            self.workspace.bootstrap(
                SimpleNamespace(), database_target_id='target-one',
                focused_operation_id='invented-operation',
            )

    def test_provider_session_is_explicitly_closed_in_database_scope(self):
        server = SimpleNamespace()
        opened = self.workspace.open_session(
            server, 'mysql-sql', 'target-one'
        )
        closed = self.workspace.close_session(
            server, opened['session_id'], 'target-one'
        )
        self.assertTrue(closed['provider_closed'])
        self.assertTrue(self.client.session_closed)
        self.assertEqual('target-one', self.endpoints.database_target_id)

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
        session = self.workspace.open_session(
            server, 'mysql-sql', 'target-one'
        )
        occurrence = self.workspace.execute(
            server, session['session_id'], 'SELECT 42',
            database_target_id='target-one',
        )
        self.assertEqual('target-one', self.endpoints.database_target_id)
        response = self.workspace.poll(
            server, occurrence['occurrence_id'],
            database_target_id='target-one',
        )
        self.assertEqual('target-one', self.endpoints.database_target_id)
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
        spreadsheet = self.workspace.export_result(server, {
            'result_id': result_id, 'format': 'xlsx',
        })
        self.assertEqual(
            'application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.sheet', spreadsheet['media_type']
        )
        self.assertTrue(base64.b64decode(
            spreadsheet['content_base64']
        ).startswith(b'PK'))
        pdf = self.workspace.export_result(server, {
            'result_id': result_id, 'format': 'pdf',
        })
        self.assertEqual('application/pdf', pdf['media_type'])
        self.assertTrue(base64.b64decode(
            pdf['content_base64']
        ).startswith(b'%PDF-'))
        compared = self.workspace.compare_results(server, {
            'left_result_id': result_id, 'right_result_id': result_id,
        })
        self.assertEqual(0, compared['changed_count'])
        self.assertFalse(compared['semantic_equality_inferred'])

    def test_authenticated_owner_can_deliver_a_bounded_retained_export(self):
        delivery = DeliveryService()
        self.workspace.report_delivery_service = delivery
        server = SimpleNamespace(user_id=11)
        session = self.workspace.open_session(server, 'mysql-sql')
        executed = self.workspace.execute(
            server, session['session_id'], 'SELECT 42'
        )
        rendered = self.workspace.poll(
            server, executed['occurrence_id']
        )['rendered_result']
        result_id = rendered['descriptor']['result_id']
        result = self.workspace.deliver_result(server, {
            'request_key': str(uuid.uuid4()), 'result_id': result_id,
            'format': 'pdf', 'profile_id': 'archive',
            'target': {'object_name': 'report.pdf'},
        })
        self.assertEqual('delivered', result['state'])
        self.assertEqual(11, delivery.request[0])
        self.assertEqual(self.context.endpoint_id, delivery.request[1])
        self.assertTrue(delivery.request[2]['content'].startswith(b'%PDF-'))
        self.assertEqual('application/pdf', delivery.request[2]['media_type'])
        self.assertEqual(
            [{'state': 'delivered'}],
            self.workspace.list_result_deliveries(server)['items'],
        )

    def test_bulk_mutations_are_previewed_and_explicitly_confirmed(self):
        server = SimpleNamespace(user_id=11)
        drafts = [{
            'resource_kind': 'database',
            'operation_id': 'create',
            'target_resource': None,
            'draft': {'name': name},
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
            'draft': {'name': 'example'},
            '_provider_route': {'route_id': 'browser-forgery'},
        })
        self.assertEqual(
            {'route_id': 'route-one'},
            self.client.admin_request['_provider_route'],
        )
        self.assertNotIn('route-one', str(plan))

    def test_visual_admin_scopes_a_database_operation_to_its_target(self):
        self.workspace.validate_visual_admin(SimpleNamespace(), {
            'resource_kind': 'database',
            'operation_id': 'inspect',
            'target_resource': {
                'resource_id': 'database-target:one',
                'resource_kind': 'database',
                'display_name': 'one',
                'extensions': {'cdeadmin': {
                    'database_target_id': 'target-one',
                }},
            },
            'draft': {},
        })
        self.assertEqual('target-one', self.endpoints.database_target_id)

    def test_visual_admin_accepts_explicit_database_target_context(self):
        self.workspace.plan_visual_admin(SimpleNamespace(), {
            'resource_kind': 'database',
            'operation_id': 'create',
            'target_resource': None,
            'database_target_id': 'target-one',
            'draft': {'name': 'example'},
        })
        self.assertEqual('target-one', self.endpoints.database_target_id)
        self.assertNotIn('database_target_id', self.client.admin_request)

    def test_visual_admin_applies_explicit_scope_to_discovered_resource(self):
        self.workspace.plan_visual_admin(SimpleNamespace(), {
            'resource_kind': 'database',
            'operation_id': 'inspect',
            'target_resource': {
                'resource_id': 'mysql-database:example',
                'resource_kind': 'database',
                'display_name': 'example',
            },
            'database_target_id': 'target-one',
            'draft': {},
        })
        self.assertEqual(
            'target-one',
            self.client.admin_request['target_resource']['extensions'][
                'cdeadmin'
            ]['database_target_id'],
        )

    def test_visual_admin_rejects_invalid_database_target_context(self):
        with self.assertRaisesRegex(
                ProviderWorkspaceError, 'database target identity'):
            self.workspace.plan_visual_admin(SimpleNamespace(), {
                'resource_kind': 'database',
                'operation_id': 'create',
                'target_resource': None,
                'database_target_id': None,
                'draft': {'name': 'example'},
            })

    def test_visual_admin_records_restart_safe_public_audit(self):
        bus = OperationBus()
        self.workspace.operation_bus = bus
        server = SimpleNamespace(user_id=7)
        plan = self.workspace.plan_visual_admin(server, {
            'resource_kind': 'database',
            'operation_id': 'create',
            'target_resource': None,
            'draft': {'name': 'auditdb'},
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

    def test_registration_failure_keeps_receipt_and_invalidates(self):
        from unittest.mock import Mock
        for action, field, method in (
                ('register_created_database', 'endpoint_database_target',
                 'retain_created_database'),
                ('remove_dropped_database_registration',
                 'dropped_endpoint_database_target',
                 'delete_database_target')):
            for fails in (False, True):
                with self.subTest(action=action, fails=fails):
                    native = {'accepted': True, field: {
                        'database': '/owned/test.fdb',
                        'target_id': 'target-one',
                        'confirmation': '/owned/test.fdb'}}
                    self.binding.instance.apply_visual_admin = Mock(
                        return_value={'provider_result': native})
                    callback = Mock(return_value={'targets': []})
                    if fails:
                        callback.side_effect = RuntimeError('secret detail')
                    setattr(self.endpoints, method, callback)
                    invalidate = Mock()
                    self.workspace.resource_service.invalidate = invalidate
                    result = self.workspace.apply_visual_admin(
                        SimpleNamespace(user_id=7), {})
                    self.assertEqual(native, result['provider_result'])
                    callback.assert_called_once()
                    invalidate.assert_called_once_with(self.context)
                    self.assertEqual(
                        1,
                        self.binding.instance.apply_visual_admin.call_count)
                    if fails:
                        follow_up = result['workspace_follow_up'][0]
                        self.assertEqual(action, follow_up['action'])
                        self.assertEqual('failed', follow_up['state'])
                        self.assertFalse(follow_up['automatic_mutation_retry'])
                        self.assertNotIn('secret detail', repr(result))
                        self.assertNotIn('database_targets', result)
                    else:
                        self.assertNotIn('workspace_follow_up', result)
                        self.assertEqual({'targets': []},
                                         result['database_targets'])

    def test_visual_admin_invalidation_uses_execution_database_scope(self):
        original = self.endpoints.workspace
        resolved = []
        invalidated = []
        invoked = []

        def scoped_workspace(server, database_target_id=Ellipsis):
            current, endpoint, root = original(server, database_target_id)
            resolved.append(database_target_id)
            namespace = str(uuid.uuid5(
                uuid.UUID(current.cache_namespace), str(database_target_id)
            ))
            return replace(current, cache_namespace=namespace), endpoint, root

        self.endpoints.workspace = scoped_workspace
        self.workspace.resource_service.invalidate = (
            lambda current: invalidated.append(current.cache_namespace)
        )
        methods = {
            'apply': 'apply_visual_admin',
            'visual_admin_operation_cancel': 'cancel_visual_admin_operation',
            'visual_admin_operation_post_state':
                'validate_visual_admin_post_state',
        }
        for action, method in methods.items():
            for scope in ('explicit', 'resource', 'default'):
                for fails in (False, True):
                    with self.subTest(action=action, scope=scope, fails=fails):
                        resolved.clear()
                        invalidated.clear()
                        invoked.clear()
                        request = {'_provider_route': {'forged': True}}
                        if scope == 'explicit':
                            request['database_target_id'] = 'target-two'
                        elif scope == 'resource':
                            request['target_resource'] = {
                                'extensions': {'cdeadmin': {
                                    'database_target_id': 'target-two',
                                }},
                            }

                        def callback(payload):
                            invoked.append(payload)
                            if fails:
                                raise VisualAdminExecutionError('lost', {})
                            return {}

                        setattr(self.binding.instance, method, callback)

                        def execute():
                            if action == 'apply':
                                return self.workspace.apply_visual_admin(
                                    SimpleNamespace(user_id=7), request
                                )
                            operation_action = (
                                self.workspace.visual_admin_operation_action
                            )
                            return operation_action(
                                SimpleNamespace(user_id=7), action, request
                            )

                        if fails:
                            with self.assertRaises(VisualAdminExecutionError):
                                execute()
                        else:
                            execute()
                        target = (Ellipsis if scope == 'default'
                                  else 'target-two')
                        self.assertEqual([target], resolved)
                        expected = str(uuid.uuid5(
                            uuid.UUID(self.context.cache_namespace),
                            str(target),
                        ))
                        self.assertEqual([expected], invalidated)
                        self.assertNotIn(
                            self.context.cache_namespace, invalidated
                        )
                        self.assertEqual(1, len(invoked))
                        self.assertEqual(
                            {'route_id': 'route-one'},
                            invoked[0]['_provider_route'],
                        )

    def test_invalid_apply_scope_never_executes_or_invalidates(self):
        calls = []
        self.binding.instance.apply_visual_admin = lambda payload: (
            calls.append(payload)
        )
        self.workspace.resource_service.invalidate = lambda current: (
            calls.append(current)
        )
        for request in (
                {'database_target_id': ''},
                {'database_target_id': None},
                {'database_target_id': 'one', 'target_resource': {
                    'extensions': {'cdeadmin': {'database_target_id': 'two'}},
                }}):
            with self.subTest(request=request):
                with self.assertRaises(ProviderWorkspaceError):
                    self.workspace.apply_visual_admin(
                        SimpleNamespace(user_id=7), request
                    )
        self.assertEqual([], calls)

    def test_apply_preserves_other_database_cache_generations(self):
        original = self.endpoints.workspace
        contexts = {}
        for target in (Ellipsis, 'one', 'two'):
            contexts[target] = replace(
                self.context, cache_namespace=str(uuid.uuid5(
                    uuid.UUID(self.context.cache_namespace), str(target)
                ))
            )

        def scoped(server, database_target_id=Ellipsis):
            _context, endpoint, root = original(server, database_target_id)
            return contexts[database_target_id], endpoint, root

        self.endpoints.workspace = scoped
        self.binding.instance.apply_visual_admin = lambda _payload: {}
        pages = {
            target: self.workspace.resource_page(SimpleNamespace(), {
                **({'database_target_id': target}
                   if target is not Ellipsis else {}),
            }) for target in contexts
        }
        self.workspace.apply_visual_admin(SimpleNamespace(user_id=7), {
            'database_target_id': 'two',
        })
        for target, current in contexts.items():
            generation = self.workspace.resource_service.cache.generation(
                current
            )
            if target == 'two':
                self.assertNotEqual(pages[target]['generation'], generation)
            else:
                self.assertEqual(pages[target]['generation'], generation)

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
            'draft': {'name': 'unknown'},
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
            'draft': {'name': 'observe'},
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

    def test_semantic_result_reproducibility_without_finality_claim(self):
        self.binding.instance.profile = replace(
            self.binding.instance.profile,
            semantic_sql_dialect={
                'contract_complete': True,
                'language_profile': 'mysql-sql',
                'quote_open': '`', 'quote_close': '`',
                'supports_rollup': False, 'limit_style': 'limit',
                'true_literal': 'TRUE', 'false_literal': 'FALSE',
                'time_operations': (
                    'as_of', 'range', 'period_to_date',
                    'period_comparison',
                ),
                'window_operations': (
                    'running_sum', 'moving_sum', 'moving_average', 'lag',
                    'delta', 'percent_change', 'rank', 'dense_rank',
                ),
            },
        )
        self.workspace.semantic_model_service = SemanticModelService(
            repository=object()
        )
        server = SimpleNamespace(user_id=21)
        definition = {
            'name': 'Count rows', 'semantic_family': 'relational',
            'sources': [{
                'id': 'items', 'resource_id': 'table:items',
                'relation': ['items'], 'alias': 'items',
                'source_kind': 'table', 'classification': 'fact',
                'grain': [], 'provider_config': {},
            }],
            'joins': [], 'relationships': [], 'dimensions': [],
            'measures': [{
                'id': 'row_count', 'name': 'Rows', 'aggregation': 'count',
                'field': None, 'measure_kind': 'aggregate',
            }],
        }
        request = {'axes': {'rows': [], 'columns': [], 'pages': []},
                   'measures': ['row_count'], 'filters': [], 'limit': 10}
        executed = self.workspace.semantic_model_action(
            server, 'semantic_query_execute', {
                'definition': definition, 'query': request,
            }
        )
        rendered = self.workspace.poll(
            server, executed['occurrence']['occurrence_id']
        )['rendered_result']
        reproducibility = rendered['reproducibility']
        self.assertEqual(64, len(reproducibility['rendered_page_digest']))
        self.assertTrue(
            reproducibility['exact_data_replay_requires_provider_snapshot']
        )
        self.assertFalse(
            reproducibility['common_layer_infers_snapshot_or_finality']
        )

    def test_workspace_rejects_cross_family_semantic_model(self):
        self.workspace.semantic_model_service = SemanticModelService(
            repository=object()
        )
        with self.assertRaisesRegex(
                ProviderWorkspaceError, 'does not match'):
            self.workspace.semantic_model_action(
                SimpleNamespace(user_id=22), 'semantic_model_validate', {
                    'definition': {
                        'name': 'Wrong family', 'semantic_family': 'graph',
                    },
                }
            )


if __name__ == '__main__':
    unittest.main()
