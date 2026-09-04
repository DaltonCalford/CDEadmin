##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Semantic-model lifecycle, compiler, lineage and cellset tests."""

from __future__ import annotations

import copy
import json
import sqlite3
import sys
import unittest
from datetime import datetime, timezone
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

from pgadmin.cdeadmin.semantic_models import (  # noqa: E402
    SemanticCompilationUnavailable,
    SemanticModelConflict,
    SemanticModelError,
    SemanticModelService,
    validate_query,
    validate_model,
    analytical_profile,
)
from pgadmin.cdeadmin.semantic_models.compiler import compile_sql  # noqa: E402


def model():
    return {
        'name': 'Sales cube',
        'description': 'Portable sales semantics',
        'sources': [{
            'id': 'sales', 'resource_id': 'table:analytics:sales',
            'relation': ['analytics', 'sales'], 'alias': 'sales',
        }],
        'joins': [],
        'dimensions': [{
            'id': 'region', 'name': 'Region',
            'field': {'source_id': 'sales', 'field': 'region_name'},
            'hierarchies': [{
                'id': 'geography', 'name': 'Geography', 'levels': [{
                    'id': 'region_level', 'name': 'Region',
                    'field': {
                        'source_id': 'sales', 'field': 'region_name',
                    },
                }],
            }],
        }],
        'measures': [{
            'id': 'revenue', 'name': 'Revenue', 'aggregation': 'sum',
            'field': {'source_id': 'sales', 'field': 'amount'},
            'format': '$0.00',
        }],
        'default_filters': [], 'materializations': [{
            'id': 'daily_rollup', 'name': 'Daily rollup',
            'strategy': 'provider_managed', 'enabled': False,
        }],
        'security': {}, 'annotations': {},
    }


def query():
    return {
        'axes': {'rows': ['region_level'], 'columns': [], 'pages': []},
        'measures': ['revenue'], 'filters': [], 'totals': True,
        'limit': 500,
    }


class MemoryRepository:
    def __init__(self):
        self.rows = {}
        self.snapshots = {}

    @staticmethod
    def new_definition(**values):
        return SimpleNamespace(**values)

    def list(self, user_id, endpoint_id):
        return sorted([
            row for row in self.rows.values()
            if row.user_id == user_id and row.endpoint_id == endpoint_id
        ], key=lambda row: row.name)

    def get(self, user_id, endpoint_id, model_id):
        row = self.rows.get(model_id)
        if row and row.user_id == user_id and row.endpoint_id == endpoint_id:
            return row
        return None

    def save(self, row, snapshot):
        self.rows[row.id] = row
        item = SimpleNamespace(
            revision=row.revision, status=row.status, definition=snapshot,
            created_at=datetime.now(timezone.utc),
        )
        self.snapshots.setdefault(row.id, []).append(item)

    def revisions(self, model_id):
        return sorted(
            self.snapshots.get(model_id, []),
            key=lambda item: item.revision, reverse=True,
        )

    def delete(self, row):
        self.rows.pop(row.id)


class SQLProvider:
    @staticmethod
    def compile_semantic_query(model_value, query_value):
        return compile_sql(model_value, query_value, {
            'language_profile': 'test-sql', 'quote_open': '"',
            'supports_rollup': True,
        })


class SemanticModelTests(unittest.TestCase):

    def setUp(self):
        self.repository = MemoryRepository()
        self.service = SemanticModelService(self.repository)

    def test_validation_and_provider_compilation_are_structured(self):
        checked = validate_model(model())
        self.assertEqual(['sales'], checked['_symbols']['source_ids'])
        compiled = self.service.compile(SQLProvider(), model(), query())
        self.assertIn('SUM("sales"."amount")', compiled['source'])
        self.assertIn('GROUP BY ROLLUP', compiled['source'])
        self.assertEqual('test-sql', compiled['language_profile'])

    def test_filter_literals_cannot_escape_generated_sql(self):
        request = query()
        request['filters'] = [{
            'field': {'source_id': 'sales', 'field': 'region_name'},
            'operator': 'eq', 'value': "North'; DROP TABLE sales; --",
        }]
        compiled = self.service.compile(SQLProvider(), model(), request)
        self.assertIn("North''; DROP TABLE sales; --", compiled['source'])
        self.assertNotIn("North'; DROP", compiled['source'])

    def test_filter_operand_shapes_fail_before_provider_compilation(self):
        checked = validate_model(model())
        invalid = (
            ('in', 'North', 'requires an array'),
            ('not_in', 3, 'requires an array'),
            ('between', [1], 'array of two values'),
            ('eq', None, 'requires a value or parameter'),
            ('is_null', None, 'must not contain a value'),
        )
        for operator, value, message in invalid:
            request = query()
            request['filters'] = [{
                'field': {'source_id': 'sales', 'field': 'region_name'},
                'operator': operator,
            }]
            if value is not None or operator == 'is_null':
                request['filters'][0]['value'] = value
            with self.subTest(operator=operator), self.assertRaisesRegex(
                    SemanticModelError, message):
                validate_query(checked, request)

    def test_calculated_measure_uses_structured_expression_tree(self):
        value = model()
        value['measures'].extend([{
            'id': 'units', 'name': 'Units', 'aggregation': 'sum',
            'field': {'source_id': 'sales', 'field': 'units'},
        }, {
            'id': 'average_price', 'name': 'Average price',
            'aggregation': 'none', 'field': None,
            'expression': {
                'operator': 'divide', 'left': {'measure': 'revenue'},
                'right': {'measure': 'units'},
            },
        }])
        request = query()
        request['measures'] = ['average_price']
        compiled = self.service.compile(SQLProvider(), value, request)
        self.assertIn('SUM("sales"."amount")', compiled['source'])
        self.assertIn('NULLIF(SUM("sales"."units"), 0)', compiled['source'])

    def test_provider_dialect_owns_firebird_row_limit_syntax(self):
        compiled = compile_sql(model(), query(), {
            'language_profile': 'firebird-sql', 'quote_open': '"',
            'supports_rollup': False, 'limit_style': 'rows',
        })
        self.assertTrue(compiled['source'].endswith('ROWS 1 TO 500'))
        self.assertNotIn(' LIMIT ', compiled['source'])

    def test_generated_cube_query_executes_on_python_sqlite_345(self):
        value = model()
        value['sources'][0]['relation'] = ['main', 'sales']
        request = query()
        request['totals'] = False
        compiled = compile_sql(value, request, {
            'language_profile': 'sqlite-sql', 'quote_open': '"',
            'supports_rollup': False,
        })
        connection = sqlite3.connect(':memory:')
        try:
            connection.execute(
                'CREATE TABLE sales (region_name TEXT, amount REAL)'
            )
            connection.executemany(
                'INSERT INTO sales VALUES (?, ?)',
                [('North', 10.0), ('North', 12.5), ('South', 5.0)],
            )
            rows = connection.execute(compiled['source']).fetchall()
        finally:
            connection.close()
        self.assertEqual([('North', 22.5), ('South', 5.0)], rows)

    def test_generated_cube_query_executes_on_duckdb_152(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest('DuckDB 1.5.2 is not installed')
        value = model()
        value['sources'][0]['relation'] = ['main', 'sales']
        request = query()
        request['totals'] = False
        compiled = compile_sql(value, request, {
            'language_profile': 'duckdb-sql', 'quote_open': '"',
            'supports_rollup': True,
        })
        connection = duckdb.connect(':memory:')
        try:
            connection.execute(
                'CREATE TABLE sales (region_name VARCHAR, amount DOUBLE)'
            )
            connection.executemany(
                'INSERT INTO sales VALUES (?, ?)',
                [('North', 10.0), ('North', 12.5), ('South', 5.0)],
            )
            rows = connection.execute(compiled['source']).fetchall()
        finally:
            connection.close()
        self.assertEqual([('North', 22.5), ('South', 5.0)], rows)

    def test_lifecycle_is_endpoint_scoped_revisioned_and_publish_safe(self):
        created = self.service.create(7, 'endpoint-one', model())
        self.assertEqual(1, created['revision'])
        self.assertEqual([], self.service.list(7, 'endpoint-two'))
        changed = copy.deepcopy(created['definition'])
        changed['description'] = 'Changed'
        updated = self.service.update(
            7, 'endpoint-one', created['model_id'], 1, changed
        )
        self.assertEqual(2, updated['revision'])
        with self.assertRaises(SemanticModelConflict):
            self.service.update(
                7, 'endpoint-one', created['model_id'], 1, changed
            )
        published = self.service.set_status(
            7, 'endpoint-one', created['model_id'], 2, 'published'
        )
        with self.assertRaises(SemanticModelError):
            self.service.update(
                7, 'endpoint-one', created['model_id'],
                published['revision'], changed,
            )
        self.assertEqual(3, len(self.service.history(
            7, 'endpoint-one', created['model_id']
        )))

    def test_lineage_and_cellset_preserve_axis_and_measure_identity(self):
        lineage = self.service.lineage(model())
        self.assertIn('measure:revenue', {
            item['id'] for item in lineage['nodes']
        })
        cellset = self.service.cellset(model(), query(), [{
            'region_level': 'North', 'revenue': 42.5,
        }])
        coordinates = cellset['cells'][0]['coordinates']
        self.assertEqual('North', coordinates['region_level'])
        self.assertEqual(42.5, cellset['cells'][0]['measures']['revenue'])

    def test_provider_without_compiler_is_explicitly_unavailable(self):
        with self.assertRaises(SemanticCompilationUnavailable):
            self.service.compile(object(), model(), query())

    def test_invalid_source_reference_fails_closed(self):
        value = model()
        value['measures'][0]['field']['source_id'] = 'missing'
        with self.assertRaises(SemanticModelError):
            validate_model(value)

    def test_provider_families_publish_native_analytical_vocabularies(self):
        relational = analytical_profile('relational')
        graph = analytical_profile('graph')
        search = analytical_profile('search-analytic')
        vector = analytical_profile('vector-analytic')
        temporal = analytical_profile('time-series-analytic')
        self.assertIn('fact', relational['source_classifications'])
        self.assertIn('native-edge', graph['relationship_kinds'])
        self.assertIn('keyword-facet', search['dimension_kinds'])
        self.assertIn('similarity-band', vector['dimension_kinds'])
        self.assertIn('window', temporal['dimension_kinds'])
        self.assertNotEqual(
            graph['dimension_kinds'], relational['dimension_kinds']
        )

    def test_complete_product_model_validates_and_extends_lineage(self):
        value = model()
        value['sources'][0].update({
            'source_kind': 'table', 'classification': 'fact',
            'grain': [{'source_id': 'sales', 'field': 'sale_id'}],
        })
        value['dimensions'][0].update({
            'dimension_kind': 'time', 'time_intelligence': {
                'role': 'calendar-time', 'calendar': 'gregorian',
                'timezone': 'UTC', 'fiscal_year_start_month': 1,
            },
        })
        value['measures'][0]['certification'] = {
            'status': 'certified', 'owner': 'Finance',
            'definition': 'Recognized revenue',
        }
        value['parameters'] = [{
            'id': 'region_parameter', 'name': 'Region', 'type': 'string',
            'required': False, 'default': 'North', 'allowed_values': [],
        }]
        value['visualizations'] = [{
            'id': 'revenue_chart', 'name': 'Revenue by region',
            'chart_type': 'bar', 'query': query(),
            'encodings': {'x': 'region_level', 'y': 'revenue'},
        }]
        value['dashboards'] = [{
            'id': 'executive', 'name': 'Executive',
            'cross_filtering': True, 'tiles': [{
                'visualization_id': 'revenue_chart', 'layout': {'x': 0},
            }],
        }]
        value['schedules'] = [{
            'id': 'daily', 'name': 'Daily', 'expression': '0 8 * * *',
            'timezone': 'UTC', 'enabled': True, 'delivery': {},
        }]
        value['reports'] = [{
            'id': 'daily_revenue', 'name': 'Daily revenue',
            'dashboard_id': 'executive', 'schedule_id': 'daily',
            'export_formats': ['json', 'csv'], 'parameters': {},
        }]
        checked = validate_model(value)
        self.assertEqual('fact', checked['sources'][0]['classification'])
        self.assertEqual(
            'certified', checked['measures'][0]['certification']['status']
        )
        lineage = self.service.lineage(value)
        self.assertIn('report:daily_revenue', {
            item['id'] for item in lineage['nodes']
        })
        self.assertIn('published-as', {
            item['kind'] for item in lineage['edges']
        })

    def test_parameters_cross_filters_and_drill_through_compile(self):
        value = model()
        value['parameters'] = [{
            'id': 'region_parameter', 'name': 'Region', 'type': 'string',
            'required': True, 'allowed_values': [],
        }]
        request = query()
        request.update({
            'parameters': {'region_parameter': 'North'},
            'cross_filters': [{
                'field': {'source_id': 'sales', 'field': 'region_name'},
                'operator': 'eq', 'parameter_id': 'region_parameter',
            }],
            'drill': {'mode': 'through', 'target_level': None,
                      'detail_fields': [{
                          'source_id': 'sales', 'field': 'region_name',
                      }]},
        })
        compiled = self.service.compile(SQLProvider(), value, request)
        self.assertNotIn('SUM(', compiled['source'])
        self.assertIn("= 'North'", compiled['source'])
        self.assertIn('detail_1_region_name', compiled['source'])

    def test_parameter_types_and_allowed_values_are_enforced(self):
        value = model()
        value['parameters'] = [{
            'id': 'minimum_sales', 'name': 'Minimum sales',
            'type': 'integer', 'required': False, 'default': 10,
            'allowed_values': [10, 20],
        }]
        checked = validate_model(value)
        self.assertEqual(10, checked['parameters'][0]['default'])
        request = query()
        request['parameters'] = {'minimum_sales': '10'}
        with self.assertRaisesRegex(
                SemanticModelError, 'does not match type integer'):
            validate_query(checked, request)
        request['parameters'] = {'minimum_sales': 30}
        with self.assertRaisesRegex(
                SemanticModelError, 'not allowed'):
            validate_query(checked, request)

    def test_time_intelligence_compiles_declared_range(self):
        value = model()
        value['dimensions'][0]['time_intelligence'] = {
            'role': 'event-time', 'calendar': 'gregorian',
            'timezone': 'UTC', 'fiscal_year_start_month': 1,
        }
        request = query()
        request['time_intelligence'] = {
            'dimension_id': 'region', 'operation': 'range',
            'start': '2026-01-01', 'end': '2026-03-31',
        }
        compiled = self.service.compile(SQLProvider(), value, request)
        self.assertIn(
            '"region_name" BETWEEN \'2026-01-01\' AND \'2026-03-31\'',
            compiled['source'],
        )
        self.assertEqual(
            'range', compiled['projection']['time_intelligence']['operation']
        )

    def test_fiscal_period_to_date_and_period_comparison_are_normalized(self):
        value = model()
        value['dimensions'][0]['time_intelligence'] = {
            'role': 'fiscal-time', 'calendar': 'gregorian',
            'timezone': 'UTC', 'fiscal_year_start_month': 4,
        }
        checked = validate_model(value)
        request = query()
        request['time_intelligence'] = {
            'dimension_id': 'region', 'operation': 'period_to_date',
            'period': 'fiscal_year', 'anchor': '2026-02-15',
        }
        normalized = validate_query(checked, request)['time_intelligence']
        self.assertEqual('2025-04-01', normalized['start'])
        self.assertEqual('2026-02-15', normalized['end'])

        request['time_intelligence'] = {
            'dimension_id': 'region', 'operation': 'period_comparison',
            'period': 'month', 'anchor': '2026-03-15',
        }
        compiled = self.service.compile(SQLProvider(), value, request)
        self.assertIn("THEN 'current'", compiled['source'])
        self.assertIn("THEN 'comparison'", compiled['source'])
        self.assertIn('2026-02-15', compiled['source'])
        self.assertIn('__semantic_period', compiled['source'])

        request['time_intelligence']['anchor'] = '2026-03-31'
        normalized = validate_query(
            validate_model(value), request
        )['time_intelligence']
        self.assertEqual('2026-02-28', normalized['comparison_end'])

    def test_sql_analytical_window_executes_and_preserves_cellset_output(self):
        value = model()
        value['sources'][0]['relation'] = ['main', 'sales']
        request = query()
        request['totals'] = False
        request['windows'] = [{
            'id': 'running_revenue', 'measure_id': 'revenue',
            'operation': 'running_sum', 'partition_by': [],
            'order_by': {'level_id': 'region_level', 'direction': 'asc'},
            'frame_size': 1,
        }]
        compiled = compile_sql(value, request, {
            'language_profile': 'sqlite-sql', 'quote_open': '"',
            'supports_rollup': False,
        })
        connection = sqlite3.connect(':memory:')
        try:
            connection.execute(
                'CREATE TABLE sales (region_name TEXT, amount REAL)'
            )
            connection.executemany(
                'INSERT INTO sales VALUES (?, ?)',
                [('North', 10.0), ('North', 12.5), ('South', 5.0)],
            )
            rows = connection.execute(compiled['source']).fetchall()
        finally:
            connection.close()
        self.assertEqual(
            [('North', 22.5, 22.5), ('South', 5.0, 27.5)], rows
        )
        cellset = self.service.cellset(value, request, [{
            'region_level': 'North', 'revenue': 22.5,
            'running_revenue': 22.5,
        }])
        self.assertIn('running_revenue', cellset['measures'])
        self.assertEqual(
            22.5, cellset['cells'][0]['measures']['running_revenue']
        )

    def test_provider_can_refuse_unadmitted_window_operation(self):
        request = query()
        request['windows'] = [{
            'id': 'running_revenue', 'measure_id': 'revenue',
            'operation': 'running_sum', 'partition_by': [],
            'order_by': {'level_id': 'region_level'}, 'frame_size': 1,
        }]
        with self.assertRaisesRegex(
                SemanticModelError, 'does not admit requested'):
            compile_sql(model(), request, {
                'language_profile': 'limited-sql', 'quote_open': '"',
                'window_operations': (),
            })

    def test_period_boundaries_use_the_declared_iana_timezone(self):
        value = model()
        value['dimensions'][0]['time_intelligence'] = {
            'role': 'event-time', 'calendar': 'gregorian',
            'timezone': 'America/Toronto', 'fiscal_year_start_month': 1,
            'value_type': 'datetime',
        }
        request = query()
        request['time_intelligence'] = {
            'dimension_id': 'region', 'operation': 'period_to_date',
            'period': 'day', 'anchor': '2026-09-04T02:00:00Z',
        }
        normalized = validate_query(
            validate_model(value), request
        )['time_intelligence']
        self.assertEqual('2026-09-03T00:00:00-04:00', normalized['start'])
        self.assertEqual('2026-09-03T22:00:00-04:00', normalized['end'])

        value['dimensions'][0]['time_intelligence']['timezone'] = 'Mars/Base'
        with self.assertRaisesRegex(SemanticModelError, 'timezone is invalid'):
            validate_model(value)

    def test_saved_chart_requires_a_valid_semantic_query(self):
        value = model()
        value['visualizations'] = [{
            'id': 'broken_chart', 'name': 'Broken chart',
            'chart_type': 'bar', 'query': {}, 'encodings': {},
        }]
        with self.assertRaisesRegex(
                SemanticModelError, 'query.measures must not be empty'):
            validate_model(value)

    def test_tenant_filter_is_bound_only_from_trusted_security_context(self):
        value = model()
        value['security'] = {'row_filters': [], 'roles': [],
                             'tenant_filter': {
            'field': {'source_id': 'sales', 'field': 'tenant_id'},
            'principal_claim': 'tenant_id', 'required': True,
        }}
        with self.assertRaisesRegex(
                SemanticModelError, 'tenant identity'):
            self.service.compile(SQLProvider(), value, query())
        compiled = self.service.compile(
            SQLProvider(), value, query(),
            security_context={'claims': {'tenant_id': 'tenant-one'}},
        )
        self.assertIn("tenant_id\" = 'tenant-one'", compiled['source'])

    def test_role_security_fails_closed_without_a_matching_role(self):
        value = model()
        value['security'] = {'row_filters': [], 'tenant_filter': None,
                             'roles': [{
                                 'id': 'finance_policy', 'name': 'finance',
                                 'principal_claim': 'teams', 'filters': [{
                                     'field': {
                                         'source_id': 'sales',
                                         'field': 'region_name',
                                     },
                                     'operator': 'eq', 'value': 'North',
                                 }],
                             }]}
        with self.assertRaisesRegex(
                SemanticModelError, 'no semantic security role'):
            self.service.compile(
                SQLProvider(), value, query(),
                security_context={'claims': {'teams': ['sales']}},
            )
        compiled = self.service.compile(
            SQLProvider(), value, query(),
            security_context={'claims': {'teams': ['finance']}},
        )
        self.assertIn("region_name\" = 'North'", compiled['source'])

    def test_diagnostics_include_stable_reproducibility_fingerprints(self):
        first = self.service.diagnostics(SQLProvider(), model(), query())
        second = self.service.diagnostics(SQLProvider(), model(), query())
        self.assertEqual(
            first['reproducibility']['compiled_digest'],
            second['reproducibility']['compiled_digest'],
        )
        self.assertTrue(first['reproducibility'][
            'exact_data_replay_requires_provider_snapshot'
        ])
        self.assertFalse(first['provider_diagnostics_available'])

    def test_invalid_dashboard_and_report_references_fail_closed(self):
        value = model()
        value['dashboards'] = [{
            'id': 'broken', 'name': 'Broken', 'tiles': [{
                'visualization_id': 'missing', 'layout': {},
            }],
        }]
        with self.assertRaisesRegex(
                SemanticModelError, 'unknown visualization'):
            validate_model(value)


if __name__ == '__main__':
    unittest.main()
