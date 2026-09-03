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
    validate_model,
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


if __name__ == '__main__':
    unittest.main()
