##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-driven visual administration framework tests."""

from __future__ import annotations

import copy
import sys
import unittest
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

from pgadmin.cdeadmin.visual_admin import (  # noqa: E402
    ControlPlaneCatalog,
    ControlPlaneOperation,
    PORTFOLIO_ENGINE_IDS,
    ProviderVisualAdministration,
    VisualAdminAccessError,
    VisualAdminExecutionError,
    catalog_for_engine,
    portfolio_summary,
    control_plane_field,
)


class Permissions:
    def __init__(self, allowed=None):
        self.allowed = frozenset(allowed or {
            'data_read', 'data_write', 'administer',
        })
        self.required = []

    def allows(self, permission, _scope='endpoint'):
        return permission in self.allowed

    def require(self, permission, scope='endpoint'):
        self.required.append((permission, scope))
        if permission not in self.allowed:
            raise VisualAdminAccessError('permission is not granted')


def context(engine_id='mysql', verified=True):
    return SimpleNamespace(
        endpoint_id='2575de74-2cac-4a68-b9bd-d361d7505d8b',
        mode='legacy_native',
        runtime_verification_state='verified' if verified else 'unverified',
        verified_runtime_family=engine_id if verified else None,
        declared_runtime_family=engine_id,
        effective_permissions=frozenset({
            'data_read', 'data_write', 'administer',
        }),
    )


class NativeAdapter:
    def __init__(self):
        self.applied = []
        self.validated = []

    def validate_admin_operation(self, request):
        self.validated.append(request)
        return {'errors': []} if request['draft'].get('name') != 'forbidden' \
            else {'errors': [{
                'field_id': 'name', 'code': 'native',
                'message': 'Native name is unavailable.',
            }]}

    @staticmethod
    def plan_admin_operation(request):
        return {
            'command_preview': {
                'native_operation': 'create_database',
                'name': request['draft']['name'],
            },
            'provider_payload': {'opaque_handle': 'native-plan-one'},
            'warnings': [],
            'receipt': {'planned_by': 'test-native-adapter'},
        }

    def apply_admin_operation(self, request):
        self.applied.append(request)
        return {'accepted': True, 'native_state': 'provider-owned'}


class VisualAdministrationCatalogTests(unittest.TestCase):

    def test_control_plane_catalog_adds_exact_typed_operation(self):
        declaration = ControlPlaneOperation(
            'cluster', 'rebalance', 'Rebalance cluster', 'admin',
            'topology_admin', (
                control_plane_field(
                    'max_moves', 'Maximum moves', 'number', True,
                    minimum=1, maximum=100,
                ),
            ), impact_scope='cluster', long_running=True,
            cancellable=True,
        )
        catalog = ControlPlaneCatalog('mysql', (declaration,)).apply({
            'objects': [{'resource_kind': 'cluster', 'operations': []}],
        })
        operation = catalog['objects'][0]['operations'][0]
        self.assertEqual('rebalance', operation['operation_id'])
        self.assertEqual(['topology_admin'],
                         operation['required_permissions'])
        self.assertTrue(operation['provider_finality_authority'])
        self.assertFalse(operation['automatic_mutation_retry'])

    def test_control_plane_catalog_validates_declared_field_contracts(self):
        declaration = ControlPlaneOperation(
            'cluster', 'rebalance', 'Rebalance cluster', 'admin',
            'topology_admin', (
                control_plane_field(
                    'max_moves', 'Maximum moves', 'number', True,
                    minimum=1, maximum=100,
                ),
                control_plane_field(
                    'policy', 'Policy', 'select', True,
                    options=[
                        {'value': 'safe', 'label': 'Safe'},
                        {'value': 'fast', 'label': 'Fast'},
                    ],
                ),
                control_plane_field(
                    'labels', 'Labels', 'json', False,
                    json_type='array',
                ),
            ), impact_scope='cluster',
        )
        catalog = ControlPlaneCatalog('test', (declaration,))
        errors = catalog.validate({
            'resource_kind': 'cluster', 'operation_id': 'rebalance',
            'target_resource': {'resource_kind': 'cluster'},
            'draft': {
                'max_moves': 0, 'policy': 'DROP CLUSTER',
                'labels': {'not': 'an array'},
            },
        })['errors']
        self.assertEqual(
            ['below_minimum', 'invalid_choice', 'invalid_type'],
            [item['code'] for item in errors],
        )
        self.assertEqual([], catalog.validate({
            'resource_kind': 'cluster', 'operation_id': 'rebalance',
            'target_resource': {'resource_kind': 'cluster'},
            'draft': {
                'max_moves': 10, 'policy': 'safe', 'labels': ['ssd'],
            },
        })['errors'])

    def test_all_required_engine_catalogs_are_present_and_expanded(self):
        summary = portfolio_summary()
        self.assertEqual(26, summary['engine_count'])
        self.assertEqual(25, summary['reference_engine_count'])
        self.assertEqual(1, summary['native_engine_count'])
        self.assertEqual(sorted(PORTFOLIO_ENGINE_IDS), summary['engine_ids'])
        self.assertGreaterEqual(summary['object_count'], 400)
        self.assertGreaterEqual(summary['operation_count'], 1900)
        for engine_id in PORTFOLIO_ENGINE_IDS:
            catalog = catalog_for_engine(engine_id)
            self.assertEqual(engine_id, catalog['engine_id'])
            self.assertTrue(catalog['objects'])
            self.assertTrue(all(
                resource['operations'] for resource in catalog['objects']
            ))
            self.assertTrue(all(
                operation['form']['fields'] is not None
                for resource in catalog['objects']
                for operation in resource['operations']
            ))

    def test_provider_without_native_planner_is_visible_but_blocked(self):
        provider = ProviderVisualAdministration(
            context(), Permissions(), 'mysql', '9.7.0'
        )
        descriptor = provider.descriptor()
        database = next(
            item for item in descriptor['objects']
            if item['resource_kind'] == 'database'
        )
        create = next(
            item for item in database['operations']
            if item['operation_id'] == 'create'
        )
        self.assertTrue(create['form_available'])
        self.assertFalse(create['planning_available'])
        self.assertIn(
            'provider_native_planner_unavailable', create['blockers']
        )
        plan = provider.plan({
            'resource_kind': 'database',
            'operation_id': 'create',
            'target_resource': None,
            'draft': {'name': 'example', 'options': {}},
        })
        self.assertEqual('blocked', plan['state'])
        self.assertFalse(plan['execution_available'])

    def test_forms_validate_types_unknown_fields_and_native_rules(self):
        adapter = NativeAdapter()
        provider = ProviderVisualAdministration(
            context(), Permissions(), 'mysql', '9.7.0', adapter
        )
        invalid = provider.validate({
            'resource_kind': 'database',
            'operation_id': 'create',
            'draft': {
                'name': 'example', 'options': '{bad-json}', 'unknown': True,
            },
        })
        self.assertFalse(invalid['valid'])
        self.assertEqual({'unknown_fields', 'json'}, {
            error['code'] for error in invalid['errors']
        })
        native = provider.validate({
            'resource_kind': 'database',
            'operation_id': 'create',
            'draft': {'name': 'forbidden', 'options': {}},
        })
        self.assertFalse(native['valid'])
        self.assertEqual('native', native['errors'][0]['code'])

    def test_runtime_validator_admits_only_unique_multiselect_choices(self):
        field = {
            'field_id': 'privileges', 'label': 'Privileges',
            'control': 'multiselect', 'required': True,
            'options': [
                {'value': 'SELECT', 'label': 'Select'},
                {'value': 'INSERT', 'label': 'Insert'},
            ],
        }
        value, error = ProviderVisualAdministration._validate_field(
            field, ['SELECT', 'INSERT']
        )
        self.assertEqual(['SELECT', 'INSERT'], value)
        self.assertIsNone(error)
        for invalid in (
            'SELECT', ['SELECT', 'SELECT'], ['SELECT', 'EXECUTE'],
        ):
            _value, error = ProviderVisualAdministration._validate_field(
                field, invalid
            )
            self.assertIsNotNone(error)

    def test_endpoint_route_reaches_provider_native_validation(self):
        adapter = NativeAdapter()
        provider = ProviderVisualAdministration(
            context(), Permissions(), 'mysql', '9.7.0', adapter
        )
        value = provider.validate({
            'resource_kind': 'database',
            'operation_id': 'create',
            'draft': {'name': 'example', 'options': {}},
            '_provider_route': {'host': 'database.internal', 'port': 3306},
        })
        self.assertTrue(value['valid'])
        self.assertEqual(
            {'host': 'database.internal', 'port': 3306},
            adapter.validated[-1]['_provider_route'],
        )

    def test_native_plan_receipt_is_single_use_and_tamper_resistant(self):
        adapter = NativeAdapter()
        provider = ProviderVisualAdministration(
            context(), Permissions(), 'mysql', '9.7.0', adapter
        )
        plan = provider.plan({
            'resource_kind': 'database',
            'operation_id': 'create',
            'draft': {'name': 'example', 'options': {}},
        })
        self.assertEqual('ready', plan['state'])
        self.assertEqual(
            'create_database', plan['command_preview']['native_operation']
        )
        applied = provider.apply({
            'plan_id': plan['plan_id'],
            'plan_digest': plan['plan_digest'],
            'confirmed': False,
        })
        self.assertTrue(applied['provider_result']['accepted'])
        self.assertFalse(
            applied['transaction_finality_interpreted_by_common_code']
        )
        self.assertEqual(
            'provider_response_recorded',
            applied['control_operation']['stage'],
        )
        recorded = provider.list_operations()['items']
        self.assertEqual(1, len(recorded))
        self.assertFalse(recorded[0]['automatic_mutation_retry'])
        observed = provider.refresh_operation({
            'operation_id': recorded[0]['operation_id'],
        })
        self.assertEqual(
            'provider_observation_unavailable',
            observed['observation_blocker'],
        )
        post_state = provider.validate_operation_post_state({
            'operation_id': recorded[0]['operation_id'],
        })
        self.assertFalse(post_state['post_state']['confirmed'])
        with self.assertRaises(VisualAdminAccessError):
            provider.apply({
                'plan_id': plan['plan_id'],
                'plan_digest': plan['plan_digest'],
            })

    def test_control_operation_events_and_cancel_dispatch_are_bounded(self):
        class LifecycleAdapter(NativeAdapter):
            def __init__(self):
                super().__init__()
                self.cancellations = 0

            @staticmethod
            def visual_admin_catalog(catalog):
                value = copy.deepcopy(catalog)
                database = next(
                    item for item in value['objects']
                    if item['resource_kind'] == 'database'
                )
                create = next(
                    item for item in database['operations']
                    if item['operation_id'] == 'create'
                )
                create.update({
                    'control_plane': True,
                    'long_running': True,
                    'cancellable': True,
                    'post_state_required': True,
                })
                return value

            @staticmethod
            def inspect_admin_operation(_request):
                return {
                    'provider_observation_only': True,
                    'provider_state': 'running',
                }

            def cancel_admin_operation(self, _request):
                self.cancellations += 1
                return {
                    'provider_response_observed': True,
                    'cancel_request_accepted': True,
                }

            @staticmethod
            def validate_admin_post_state(_request):
                return {
                    'confirmed': False,
                    'reason': 'provider_reports_work_in_progress',
                }

        adapter = LifecycleAdapter()
        provider = ProviderVisualAdministration(
            context(), Permissions(), 'mysql', '9.7.0', adapter
        )
        plan = provider.plan({
            'resource_kind': 'database', 'operation_id': 'create',
            'draft': {'name': 'events', 'options': {}},
        })
        applied = provider.apply({
            'plan_id': plan['plan_id'],
            'plan_digest': plan['plan_digest'],
            'confirmed': False,
        })
        operation_id = applied['control_operation']['operation_id']
        provider.refresh_operation({'operation_id': operation_id})
        first_cancel = provider.cancel_operation({
            'operation_id': operation_id,
        })
        second_cancel = provider.cancel_operation({
            'operation_id': operation_id,
        })
        final = provider.validate_operation_post_state({
            'operation_id': operation_id,
        })
        self.assertEqual(1, adapter.cancellations)
        self.assertEqual(first_cancel, second_cancel)
        self.assertEqual('post_state_unconfirmed', final['stage'])
        self.assertEqual(list(
            range(1, final['last_event_sequence'] + 1)
        ), [event['sequence'] for event in final['events']])
        self.assertEqual([
            'dispatch_started',
            'provider_response_recorded',
            'provider_observation_recorded',
            'cancel_request_dispatched',
            'cancel_response_recorded',
            'post_state_unconfirmed',
        ], [event['event_kind'] for event in final['events']])
        self.assertTrue(all(
            event['provider_finality_inferred'] is False
            for event in final['events']
        ))
        self.assertFalse(final['automatic_mutation_retry'])

    def test_lost_provider_response_is_recorded_and_never_retried(self):
        class FailedAdapter(NativeAdapter):
            def __init__(self):
                super().__init__()
                self.attempts = 0

            def apply_admin_operation(self, _request):
                self.attempts += 1
                raise TimeoutError('simulated response loss')

        adapter = FailedAdapter()
        provider = ProviderVisualAdministration(
            context(), Permissions(), 'mysql', '9.7.0', adapter
        )
        plan = provider.plan({
            'resource_kind': 'database', 'operation_id': 'create',
            'draft': {'name': 'unknown', 'options': {}},
        })
        with self.assertRaises(VisualAdminExecutionError) as raised:
            provider.apply({
                'plan_id': plan['plan_id'],
                'plan_digest': plan['plan_digest'],
            })
        operation = raised.exception.operation
        self.assertEqual('provider_response_unavailable', operation['stage'])
        self.assertTrue(operation['unknown_outcome'])
        self.assertEqual(1, adapter.attempts)
        self.assertEqual([
            'dispatch_started', 'provider_response_unavailable',
        ], [item['event_kind'] for item in operation['events']])
        with self.assertRaises(VisualAdminAccessError):
            provider.apply({
                'plan_id': plan['plan_id'],
                'plan_digest': plan['plan_digest'],
            })
        self.assertEqual(1, adapter.attempts)

    def test_unverified_runtime_cannot_apply_a_retained_plan(self):
        adapter = NativeAdapter()
        provider = ProviderVisualAdministration(
            context(verified=False), Permissions(), 'mysql', '9.7.0', adapter
        )
        plan = provider.plan({
            'resource_kind': 'database',
            'operation_id': 'create',
            'draft': {'name': 'example', 'options': {}},
        })
        with self.assertRaisesRegex(
            VisualAdminAccessError, 'verified runtime identity'
        ):
            provider.apply({
                'plan_id': plan['plan_id'],
                'plan_digest': plan['plan_digest'],
            })


if __name__ == '__main__':
    unittest.main()
