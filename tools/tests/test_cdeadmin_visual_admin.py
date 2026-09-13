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
    COVERAGE_SCHEMA,
    ENGINE_EXPERIENCE_FAMILIES,
    EXPERIENCE_SCHEMA,
    EXPERIENCE_REQUIREMENTS,
    PORTFOLIO_ENGINE_IDS,
    ProviderVisualAdministration,
    VisualAdminAccessError,
    VisualAdminExecutionError,
    catalog_for_engine,
    concept_coverage_for_engine,
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
        experience_family=engine_id,
        provider_id=f'org.cdeadmin.{engine_id}',
        profile_id=f'{engine_id}-native',
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

    @staticmethod
    def visual_admin_catalog(catalog):
        database = next(
            item for item in catalog['objects']
            if item['resource_kind'] == 'database'
        )
        create = next(
            item for item in database['operations']
            if item['operation_id'] == 'create'
        )
        create['form'] = {
            'form_id': 'mysql.database.create.test.v1',
            'title': 'Create MySQL database',
            'fields': copy.deepcopy(create['form']['fields']),
        }
        return catalog


class OrderedKeyAdapter(NativeAdapter):
    def __init__(self):
        super().__init__()
        self.row_requests = []

    def read_admin_rows(self, request):
        self.row_requests.append(request)
        return {
            'columns': [
                {'name': 'key', 'engine_type': 'bytes'},
                {'name': 'value', 'engine_type': 'bytes'},
            ],
            'rows': [{'values': {'key': 'bounded', 'value': 'value'}}],
            'editable': True,
            'continuation': None,
        }


class VisualAdministrationCatalogTests(unittest.TestCase):

    def test_choice_fields_reject_nested_values_without_exceptions(self):
        for control, values in (
                ('select', ({}, [], {'value': 'a'}, ['a'])),
                ('multiselect', ([{}], [[]], ['a', {}], [['a']], ['a', 'a']))):
            declaration = control_plane_field(
                'selection', 'Selection', control, True,
                options=[{'value': v, 'label': v} for v in ('a', 'b')])
            for value in values:
                with self.subTest(control=control, value=value):
                    cleaned, error = (
                        ProviderVisualAdministration._validate_field(
                            declaration, value))
                    self.assertIsNone(cleaned)
                    self.assertEqual('choice', error['code'])
                    self.assertEqual('invalid_choice',
                                     ControlPlaneCatalog._field_error(
                                         declaration, value)[0])

    def test_choice_fields_preserve_valid_values_and_selection_order(self):
        for control, values in (('select', ('a', 'b')),
                                ('multiselect', ([], ['a'], ['b', 'a']))):
            declaration = control_plane_field(
                'selection', 'Selection', control,
                options=[{'value': v, 'label': v} for v in ('a', 'b')])
            for value in values:
                cleaned, error = ProviderVisualAdministration._validate_field(
                    declaration, value)
                self.assertIsNone(error)
                self.assertEqual(value, cleaned)
                self.assertIsNone(ControlPlaneCatalog._field_error(
                    declaration, value))
                if isinstance(value, list):
                    self.assertIsNot(value, cleaned)

    def test_structured_editor_rejects_malformed_nested_choice(self):
        choice = control_plane_field('choice', 'Choice', 'select', True,
                                     options=[{'value': 'a', 'label': 'A'}])
        for editor, value in (
                ('array_editor', [{'choice': {'value': 'a'}}]),
                ('object_editor', {'choice': ['a']})):
            declaration = {'field_id': 'settings', 'label': 'Settings',
                           'control': 'json', 'required': True,
                           editor: {'item_kind': 'object', 'fields': [choice]}}
            cleaned, error = ProviderVisualAdministration._validate_field(
                declaration, value)
            self.assertIsNone(cleaned)
            self.assertIsNotNone(error)

    def test_control_plane_collects_all_malformed_choice_errors(self):
        choices = [{'value': 'a', 'label': 'A'}]
        operation = ControlPlaneOperation(
            'cluster', 'configure', 'Configure', 'admin', 'topology_admin', (
                control_plane_field('single', 'Single', 'select', True,
                                    options=choices),
                control_plane_field('multiple', 'Multiple', 'multiselect',
                                    True, options=choices),
            ), target_required=False)
        catalog = ControlPlaneCatalog('test', (operation,))
        result = catalog.validate({
            'resource_kind': 'cluster', 'operation_id': 'configure',
            'draft': {'single': {'value': 'a'}, 'multiple': ['a', ['a']]}})
        self.assertEqual(['single', 'multiple'],
                         [error['field_id'] for error in result['errors']])
        self.assertEqual(['invalid_choice', 'invalid_choice'],
                         [error['code'] for error in result['errors']])

    def test_numeric_fields_reject_nonfinite_values_at_both_boundaries(self):
        declaration = control_plane_field('value', 'Value', 'number', True,
                                          minimum=-10, maximum=10)
        for value in (float('nan'), float('inf'), float('-inf')):
            with self.subTest(value=value):
                cleaned, error = ProviderVisualAdministration._validate_field(
                    declaration, value)
                self.assertIsNone(cleaned)
                self.assertEqual('finite', error['code'])
                self.assertEqual('non_finite',
                                 ControlPlaneCatalog._field_error(
                                     declaration, value)[0])

    def test_numeric_fields_preserve_exact_integers_and_finite_fractions(self):
        declaration = control_plane_field('value', 'Value', 'number', True)
        for value in (0, -1, 0.125, -(2 ** 63), 2 ** 63 - 1, 10 ** 400):
            with self.subTest(value=value):
                cleaned, error = ProviderVisualAdministration._validate_field(
                    declaration, value)
                self.assertIsNone(error)
                self.assertEqual(value, cleaned)
                self.assertEqual(type(value), type(cleaned))
                self.assertIsNone(ControlPlaneCatalog._field_error(
                    declaration, value))
        for value in (True, '1', {}, []):
            self.assertIsNotNone(ProviderVisualAdministration._validate_field(
                declaration, value)[1])
            self.assertIsNotNone(ControlPlaneCatalog._field_error(
                declaration, value))

    def test_nonfinite_numbers_are_rejected_in_structured_editor_records(self):
        number = control_plane_field('value', 'Value', 'number', True)
        for editor, value in (
                ('array_editor', [{'value': float('nan')}]),
                ('object_editor', {'value': float('inf')})):
            declaration = {'field_id': 'settings', 'label': 'Settings',
                           'control': 'json', 'required': True,
                           editor: {'item_kind': 'object', 'fields': [number]}}
            cleaned, error = ProviderVisualAdministration._validate_field(
                declaration, value)
            self.assertIsNone(cleaned)
            self.assertIsNotNone(error)

    def test_firebird_properties_surface_native_metadata_sections(self):
        resources = {
            item['resource_kind']: item
            for item in catalog_for_engine('firebird')['objects']
        }
        expected_ddl = {
            'table', 'view', 'index', 'constraint', 'domain', 'sequence',
            'trigger', 'procedure', 'function', 'package', 'exception',
            'role', 'external-function',
        }
        for kind in expected_ddl:
            self.assertIn('ddl', resources[kind]['editor']['sections'])
        for kind in {'procedure', 'function', 'external-function'}:
            self.assertIn(
                'parameters', resources[kind]['editor']['sections']
            )
        for kind in {'table', 'view'}:
            self.assertIn('columns', resources[kind]['editor']['sections'])
            self.assertIn('data', resources[kind]['editor']['sections'])

    def test_every_object_has_provider_specific_navigator_and_editor(self):
        for engine_id in PORTFOLIO_ENGINE_IDS:
            catalog = catalog_for_engine(engine_id)
            navigator = catalog['navigator']
            editor_suite = catalog['object_editor']
            self.assertEqual(EXPERIENCE_SCHEMA, navigator['schema'])
            self.assertEqual(
                f'cdeadmin.{engine_id}.navigator',
                navigator['navigator_id'],
            )
            self.assertTrue(navigator['hierarchical'])
            self.assertTrue(navigator['authority_path_owned_by_provider'])
            self.assertEqual(EXPERIENCE_SCHEMA, editor_suite['schema'])
            self.assertEqual(
                f'cdeadmin.{engine_id}.editors',
                editor_suite['editor_suite_id'],
            )
            self.assertTrue(editor_suite['provider_validates_drafts'])
            self.assertTrue(editor_suite['provider_plans_native_commands'])
            self.assertTrue(editor_suite['provider_owns_finality'])

            kinds = {item['resource_kind'] for item in catalog['objects']}
            group_ids = {
                item['group_id'] for item in navigator['groups']
            }
            navigator_ids = set()
            editor_ids = set()
            for resource in catalog['objects']:
                kind = resource['resource_kind']
                object_navigator = resource['navigator']
                editor = resource['editor']
                self.assertEqual(
                    f'cdeadmin.{engine_id}.{kind}.navigator',
                    object_navigator['navigator_id'],
                )
                self.assertEqual(
                    f'cdeadmin.{engine_id}.{kind}',
                    object_navigator['icon_id'],
                )
                self.assertEqual(
                    f'cdeadmin.{engine_id}.{kind}.editor',
                    editor['editor_id'],
                )
                self.assertIn(object_navigator['group_id'], group_ids)
                self.assertTrue(set(
                    object_navigator['parent_kinds']
                ).issubset(kinds))
                self.assertIn('properties', editor['sections'])
                self.assertIn('operations', editor['sections'])
                self.assertEqual(
                    [item['operation_id']
                     for item in resource['operations']],
                    editor['provider_planned_operations'],
                )
                navigator_ids.add(object_navigator['navigator_id'])
                editor_ids.add(editor['editor_id'])
            self.assertEqual(len(catalog['objects']), len(navigator_ids))
            self.assertEqual(len(catalog['objects']), len(editor_ids))

    def test_model_family_wins_when_object_names_overlap(self):
        cases = (
            ('postgresql', 'table', 'relations', 'relational-object'),
            ('mongodb', 'collection', 'documents', 'document-object'),
            ('mongodb', 'index', 'documents', 'document-object'),
            ('neo4j', 'node', 'graph-data', 'graph-object'),
            ('neo4j', 'index', 'graph-schema', 'graph-object'),
            ('redis', 'hash', 'keys', 'key-value-object'),
            ('opensearch', 'index', 'search-schema', 'search-object'),
            ('clickhouse', 'table', 'analytics', 'columnar-object'),
            ('duckdb', 'table', 'analytics', 'columnar-object'),
            ('influxdb', 'table', 'analytics', 'time-series-object'),
            ('milvus', 'collection', 'analytics', 'vector-object'),
        )
        for engine_id, kind, group_id, editor_kind in cases:
            resource = next(
                item for item in catalog_for_engine(engine_id)['objects']
                if item['resource_kind'] == kind
            )
            self.assertEqual(group_id, resource['navigator']['group_id'])
            self.assertEqual(editor_kind, resource['editor']['editor_kind'])

    def test_scalar_objects_do_not_inherit_table_data_editors(self):
        for engine_id in PORTFOLIO_ENGINE_IDS:
            if engine_id == 'scratchbird':
                continue
            for resource in catalog_for_engine(engine_id)['objects']:
                if resource['resource_kind'] not in {
                        'sequence', 'domain', 'constraint', 'column',
                        'collation', 'function', 'procedure', 'package'}:
                    continue
                with self.subTest(engine=engine_id,
                                  kind=resource['resource_kind']):
                    self.assertIsNone(resource['editor']['data_presentation'])
                    self.assertNotIn('data', resource['editor']['sections'])

    def test_sequence_state_and_native_data_presentations(self):
        for engine_id, kind, presentation in (
                ('firebird', 'sequence', None),
                ('firebird', 'table', 'structured-grid'),
                ('mongodb', 'collection', 'document-grid'),
                ('redis', 'hash', 'redis-hash'),
                ('neo4j', 'relationship', 'graph-relationship')):
            resource = next(item for item in
                            catalog_for_engine(engine_id)['objects']
                            if item['resource_kind'] == kind)
            self.assertEqual(presentation,
                             resource['editor']['data_presentation'])
            # Explicit provider sections remain authoritative; state is
            # offered only when the provider supplies that observation.

    def test_object_family_coverage_is_explicit_and_fail_closed(self):
        self.assertEqual(set(PORTFOLIO_ENGINE_IDS), set(
            ENGINE_EXPERIENCE_FAMILIES
        ))
        self.assertEqual({
            'relational', 'document', 'graph', 'key_value', 'search',
            'columnar', 'time_series', 'vector', 'wide_column', 'semantic',
            'bitemporal',
        }, set(EXPERIENCE_REQUIREMENTS))
        for engine_id in PORTFOLIO_ENGINE_IDS:
            coverage = catalog_for_engine(engine_id)['concept_coverage']
            self.assertEqual(COVERAGE_SCHEMA, coverage['schema'])
            self.assertFalse(coverage['support_inferred_from_catalog'])
            self.assertEqual(
                list(ENGINE_EXPERIENCE_FAMILIES[engine_id]),
                [item['family_id'] for item in coverage['families']],
            )
            for family in coverage['families']:
                required = EXPERIENCE_REQUIREMENTS[family['family_id']]
                self.assertEqual(
                    list(required),
                    [item['concept_id'] for item in family['concepts']],
                )
                for concept in family['concepts']:
                    self.assertIn(
                        concept['activation_state'],
                        {'supported', 'read_only', 'not_applicable',
                         'undeclared'},
                    )

    def test_catalog_presence_does_not_fabricate_support_declaration(self):
        engine = {
            'engine_id': 'postgresql',
            'objects': [{'resource_kind': 'table'}],
            'concept_declarations': {
                'relational': {
                    'tables': 'supported',
                    'servers': 'invented-status',
                },
            },
        }
        coverage = concept_coverage_for_engine(engine)
        concepts = {
            item['concept_id']: item
            for item in coverage['families'][0]['concepts']
        }
        self.assertEqual('catalogued', concepts['tables']['catalog_state'])
        self.assertEqual('supported', concepts['tables']['activation_state'])
        self.assertFalse(coverage['activation_ready'])
        self.assertGreater(coverage['live_evidence_missing_count'], 0)
        self.assertEqual('missing', concepts['servers']['catalog_state'])
        self.assertEqual('undeclared', concepts['servers']['activation_state'])

    def test_provider_catalog_specialization_refreshes_editor_contract(self):
        class CatalogAdapter(NativeAdapter):
            @staticmethod
            def visual_admin_catalog(catalog):
                database = next(
                    item for item in catalog['objects']
                    if item['resource_kind'] == 'database'
                )
                extra = copy.deepcopy(database['operations'][0])
                extra['operation_id'] = 'provider-extra'
                extra['title'] = 'Provider extra'
                database['operations'].append(extra)
                database['editor']['provider_marker'] = 'preserved'
                return catalog

        descriptor = ProviderVisualAdministration(
            context(), Permissions(), 'mysql', '9.7.0', CatalogAdapter()
        ).descriptor()
        database = next(
            item for item in descriptor['objects']
            if item['resource_kind'] == 'database'
        )
        self.assertEqual(
            [item['operation_id'] for item in database['operations']],
            database['editor']['provider_planned_operations'],
        )
        self.assertEqual('preserved', database['editor']['provider_marker'])

    def test_provider_operation_gate_fails_closed_before_planning(self):
        provider = ProviderVisualAdministration(
            context(), Permissions(), 'mysql', '9.7.0', NativeAdapter(),
            operation_gate=lambda _kind, _operation: False,
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
        self.assertFalse(create['native_supported'])
        self.assertIn('provider_operation_unavailable', create['blockers'])
        with self.assertRaisesRegex(
                VisualAdminAccessError, 'does not support'):
            provider.plan({
                'resource_kind': 'database', 'operation_id': 'create',
                'target_resource': None, 'draft': {'name': 'example'},
            })

    def test_generic_forms_do_not_claim_engine_graphical_coverage(self):
        class GenericAdapter(NativeAdapter):
            visual_admin_catalog = None

        descriptor = ProviderVisualAdministration(
            context(), Permissions(), 'mysql', '9.7.0', GenericAdapter()
        ).descriptor()
        contract = descriptor['graphical_interface']
        self.assertEqual('blocked', contract['activation_state'])
        self.assertGreater(contract['native_operation_count'], 0)
        self.assertLess(
            contract['graphical_operation_count'],
            contract['native_operation_count'],
        )
        database = next(
            item for item in descriptor['objects']
            if item['resource_kind'] == 'database'
        )
        create = next(
            item for item in database['operations']
            if item['operation_id'] == 'create'
        )
        self.assertEqual(
            'common-layout-only', create['graphical_form_authority']
        )
        self.assertFalse(create['graphical_ready'])
        self.assertFalse(create['execution_available'])
        self.assertIn(
            'engine_graphical_form_unavailable', create['blockers']
        )
        with self.assertRaisesRegex(
                VisualAdminAccessError, 'verified graphical form'):
            ProviderVisualAdministration(
                context(), Permissions(), 'mysql', '9.7.0', GenericAdapter()
            ).plan({
                'resource_kind': 'database', 'operation_id': 'create',
                'target_resource': None, 'draft': {
                    'name': 'example', 'options': {},
                },
            })

    def test_provider_replacement_form_counts_as_engine_graphical_ui(self):
        class GraphicalAdapter(NativeAdapter):
            @staticmethod
            def visual_admin_catalog(catalog):
                database = next(
                    item for item in catalog['objects']
                    if item['resource_kind'] == 'database'
                )
                create = next(
                    item for item in database['operations']
                    if item['operation_id'] == 'create'
                )
                create['form'] = {
                    'form_id': 'mysql.database.create.v1',
                    'title': 'Create MySQL database',
                    'fields': [{
                        'field_id': 'name', 'label': 'Database name',
                        'control': 'text', 'required': True,
                    }, {
                        'field_id': 'character_set',
                        'label': 'Default character set',
                        'control': 'select', 'required': True,
                        'options': [{
                            'value': 'utf8mb4', 'label': 'utf8mb4',
                        }],
                    }],
                }
                database['operations'] = [create]
                catalog['objects'] = [database]
                return catalog

            @staticmethod
            def supports_admin_operation(kind, operation):
                return kind == 'database' and operation == 'create'

        descriptor = ProviderVisualAdministration(
            context(), Permissions(), 'mysql', '9.7.0', GraphicalAdapter()
        ).descriptor()
        contract = descriptor['graphical_interface']
        self.assertEqual('passed', contract['activation_state'])
        self.assertEqual(1, contract['native_operation_count'])
        self.assertEqual(1, contract['graphical_operation_count'])
        create = descriptor['objects'][0]['operations'][0]
        self.assertEqual(
            'provider-adapter', create['graphical_form_authority']
        )
        self.assertTrue(create['graphical_ready'])
        self.assertTrue(create['execution_available'])

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
        with self.assertRaisesRegex(
                VisualAdminAccessError, 'verified graphical form'):
            provider.plan({
                'resource_kind': 'database',
                'operation_id': 'create',
                'target_resource': None,
                'draft': {'name': 'example', 'options': {}},
            })

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
                create['form'] = {
                    'form_id': 'mysql.database.create.lifecycle-test.v1',
                    'title': 'Create MySQL database',
                    'fields': copy.deepcopy(create['form']['fields']),
                }
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

    def test_operation_followups_preserve_native_session_binding(self):
        class SessionAdapter(NativeAdapter):
            def __init__(self):
                super().__init__()
                self.handles = []

            @staticmethod
            def visual_admin_catalog(catalog):
                value = NativeAdapter.visual_admin_catalog(catalog)
                database = next(
                    item for item in value['objects']
                    if item['resource_kind'] == 'database'
                )
                create = next(
                    item for item in database['operations']
                    if item['operation_id'] == 'create'
                )
                create.update({
                    'control_plane': True, 'cancellable': True,
                    'post_state_required': True,
                })
                return value

            def _capture(self, request):
                self.handles.append(request.get('_provider_session_handle'))

            def inspect_admin_operation(self, request):
                self._capture(request)
                return {'provider_observation_only': True}

            def cancel_admin_operation(self, request):
                self._capture(request)
                return {'cancel_request_accepted': True}

            def validate_admin_post_state(self, request):
                self._capture(request)
                return {'confirmed': True}

        adapter = SessionAdapter()
        provider = ProviderVisualAdministration(
            context(), Permissions(), 'mysql', '9.7.0', adapter
        )
        plan = provider.plan({
            'resource_kind': 'database', 'operation_id': 'create',
            'draft': {'name': 'session_bound', 'options': {}},
            'session_id': 'native-session',
        })
        execution = {
            'session_id': 'native-session',
            'session_handle': object(),
        }
        applied = provider.apply({
            'plan_id': plan['plan_id'],
            'plan_digest': plan['plan_digest'],
            'session_id': 'native-session',
        }, execution)
        operation_id = applied['control_operation']['operation_id']
        followup = {
            'operation_id': operation_id,
            'session_id': 'native-session',
        }
        provider.refresh_operation(followup, execution)
        provider.cancel_operation(followup, execution)
        provider.validate_operation_post_state(followup, execution)
        self.assertEqual(
            [execution['session_handle']] * 3, adapter.handles
        )
        with self.assertRaises(VisualAdminAccessError):
            provider.refresh_operation({
                'operation_id': operation_id,
                'session_id': 'different-session',
            }, execution)

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

    def test_pre_dispatch_credential_failure_restores_exact_plan(self):
        class CredentialRequired(RuntimeError):
            credential_required_before_dispatch = True

        class CredentialAdapter(NativeAdapter):
            def __init__(self):
                super().__init__()
                self.attempts = 0

            def apply_admin_operation(self, request):
                self.attempts += 1
                if self.attempts == 1:
                    raise CredentialRequired('credential required')
                return super().apply_admin_operation(request)

        adapter = CredentialAdapter()
        provider = ProviderVisualAdministration(
            context(), Permissions(), 'mysql', '9.7.0', adapter
        )
        plan = provider.plan({
            'resource_kind': 'database', 'operation_id': 'create',
            'draft': {'name': 'credential-test', 'options': {}},
        })
        request = {
            'plan_id': plan['plan_id'],
            'plan_digest': plan['plan_digest'],
        }
        with self.assertRaises(CredentialRequired):
            provider.apply(request)
        result = provider.apply(request)
        self.assertEqual(2, adapter.attempts)
        self.assertTrue(result['provider_result']['accepted'])

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

    def test_ordered_key_row_page_preserves_explicit_scan_bounds(self):
        adapter = OrderedKeyAdapter()
        provider = ProviderVisualAdministration(
            context('foundationdb'), Permissions(),
            'foundationdb', '7.3.77', adapter,
        )

        page = provider.read_rows({
            '_provider_route': {'route_id': 'bounded-test'},
            'target_resource': {
                'resource_kind': 'key-range',
                'resource_id': 'foundationdb:key-range:default',
            },
            'start_key': 'alpha',
            'end_key': 'omega',
            'limit': 25,
        })

        self.assertEqual('alpha', adapter.row_requests[0]['start_key'])
        self.assertEqual('omega', adapter.row_requests[0]['end_key'])
        self.assertEqual(25, adapter.row_requests[0]['limit'])
        self.assertEqual(
            'org.cdeadmin.foundationdb-foundationdb-native-admin-key-range',
            page['grid']['grid_id'],
        )


if __name__ == '__main__':
    unittest.main()
