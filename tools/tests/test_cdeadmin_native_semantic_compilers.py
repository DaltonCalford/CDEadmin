##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-native document, graph, and search semantic compiler tests."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.mongodb.semantic import (  # noqa: E402
    compile_mongodb_aggregation,
)
from pgadmin.cdeadmin.providers.neo4j.semantic import (  # noqa: E402
    compile_neo4j_cypher,
)
from pgadmin.cdeadmin.providers.opensearch.semantic import (  # noqa: E402
    compile_opensearch_aggregation,
)
from pgadmin.cdeadmin.search.opensearch import OpenSearchClient  # noqa: E402
from pgadmin.cdeadmin.semantic_models import (  # noqa: E402
    SemanticModelError, validate_model,
)


def query(measures, rows=None):
    return {
        'axes': {'rows': rows or [], 'columns': [], 'pages': []},
        'measures': measures, 'filters': [], 'cross_filters': [],
        'parameters': {}, 'totals': False, 'limit': 100,
    }


class NativeSemanticCompilerTests(unittest.TestCase):

    def test_mongodb_compiles_nested_paths_and_calculated_measure(self):
        model = {
            'name': 'Document sales', 'semantic_family': 'document',
            'sources': [{
                'id': 'sales', 'resource_id': 'mongodb:sales',
                'relation': ['analytics', 'sales'], 'alias': 'sales',
                'source_kind': 'collection',
                'classification': 'document-set', 'grain': [],
                'provider_config': {},
            }],
            'dimensions': [{
                'id': 'region', 'name': 'Region',
                'dimension_kind': 'document-path',
                'field': {'source_id': 'sales',
                          'field': 'customer.region'},
                'hierarchies': [{
                    'id': 'geography', 'name': 'Geography', 'levels': [{
                        'id': 'region_level', 'name': 'Region',
                        'field': {'source_id': 'sales',
                                  'field': 'customer.region'},
                    }],
                }],
            }],
            'measures': [{
                'id': 'revenue', 'name': 'Revenue', 'aggregation': 'sum',
                'measure_kind': 'aggregate',
                'field': {'source_id': 'sales', 'field': 'amount'},
            }, {
                'id': 'orders', 'name': 'Orders', 'aggregation': 'count',
                'measure_kind': 'document-count', 'field': None,
            }, {
                'id': 'priced_orders', 'name': 'Priced orders',
                'aggregation': 'count', 'measure_kind': 'aggregate',
                'field': {'source_id': 'sales', 'field': 'amount'},
            }, {
                'id': 'average_order', 'name': 'Average order',
                'aggregation': 'none', 'measure_kind': 'calculated',
                'expression': {
                    'operator': 'divide',
                    'left': {'measure': 'revenue'},
                    'right': {'measure': 'orders'},
                },
            }],
        }
        compiled = compile_mongodb_aggregation(
            model, query(
                ['average_order', 'priced_orders'], ['region_level']
            )
        )
        source = json.loads(compiled['source'])
        self.assertEqual('aggregate', source['operation'])
        self.assertEqual('analytics', source['database'])
        self.assertEqual('$customer.region', source['pipeline'][0][
            '$group']['_id']['region_level'])
        serialized = json.dumps(source['pipeline'])
        self.assertIn('$sum', serialized)
        self.assertIn('$cond', serialized)
        self.assertIn('$type', serialized)

    def test_neo4j_compiles_native_edge_and_parameterized_filter(self):
        model = {
            'name': 'Graph sales', 'semantic_family': 'graph',
            'sources': [{
                'id': 'people', 'resource_id': 'neo4j:Person',
                'relation': ['neo4j', 'Person'], 'alias': 'people',
                'source_kind': 'node', 'classification': 'node-set',
                'grain': [], 'provider_config': {'label': 'Person'},
            }],
            'dimensions': [{
                'id': 'country', 'name': 'Country',
                'dimension_kind': 'property',
                'field': {'source_id': 'people', 'field': 'country'},
                'hierarchies': [{
                    'id': 'location', 'name': 'Location', 'levels': [{
                        'id': 'country_level', 'name': 'Country',
                        'field': {'source_id': 'people', 'field': 'country'},
                    }],
                }],
            }],
            'measures': [{
                'id': 'person_count', 'name': 'People',
                'aggregation': 'count', 'measure_kind': 'path-count',
                'field': None,
            }],
            'default_filters': [{
                'field': {'source_id': 'people', 'field': 'active'},
                'operator': 'eq', 'value': True,
            }],
        }
        compiled = compile_neo4j_cypher(
            model, query(['person_count'], ['country_level'])
        )
        self.assertIn('MATCH (`people`:`Person`)', compiled['source'])
        self.assertIn('count(*) AS `person_count`', compiled['source'])
        self.assertIn('`people`.`active` = $semantic_1', compiled['source'])
        self.assertEqual({'semantic_1': True}, compiled['parameters'])

    def test_opensearch_compiles_and_flattens_composite_buckets(self):
        model = {
            'name': 'Search sales', 'semantic_family': 'search',
            'sources': [{
                'id': 'sales', 'resource_id': 'opensearch:sales',
                'relation': ['sales-*'], 'alias': 'sales',
                'source_kind': 'index',
                'classification': 'indexed-document-set', 'grain': [],
                'provider_config': {},
            }],
            'dimensions': [{
                'id': 'region', 'name': 'Region',
                'dimension_kind': 'keyword-facet',
                'field': {'source_id': 'sales', 'field': 'region.keyword'},
                'hierarchies': [{
                    'id': 'facets', 'name': 'Facets', 'levels': [{
                        'id': 'region_facet', 'name': 'Region',
                        'field': {'source_id': 'sales',
                                  'field': 'region.keyword'},
                    }],
                }],
            }],
            'measures': [{
                'id': 'revenue', 'name': 'Revenue', 'aggregation': 'sum',
                'measure_kind': 'bucket-metric',
                'field': {'source_id': 'sales', 'field': 'amount'},
            }, {
                'id': 'documents', 'name': 'Documents',
                'aggregation': 'count', 'measure_kind': 'document-count',
                'field': None,
            }, {
                'id': 'priced_documents', 'name': 'Priced documents',
                'aggregation': 'count', 'measure_kind': 'bucket-metric',
                'field': {'source_id': 'sales', 'field': 'amount'},
            }],
        }
        compiled = compile_opensearch_aggregation(
            model, query(
                ['revenue', 'documents', 'priced_documents'],
                ['region_facet'],
            )
        )
        source = json.loads(compiled['source'])
        composite = source['aggs']['semantic_rows']['composite']
        self.assertEqual('region.keyword', composite['sources'][0][
            'region_facet']['terms']['field'])
        self.assertEqual('sales-*', compiled['parameters']['index'])
        self.assertEqual(
            {'field': 'amount'},
            source['aggs']['semantic_rows']['aggs'][
                'priced_documents']['value_count'],
        )
        rows = OpenSearchClient._semantic_aggregation_rows({
            'aggregations': {'semantic_rows': {'buckets': [{
                'key': {'region_facet': 'north'}, 'doc_count': 3,
                'revenue': {'value': 42.5},
            }]}}
        }, ['region_facet'], ['documents'])
        self.assertEqual([{
            'region_facet': 'north', 'revenue': 42.5, 'documents': 3,
        }], rows)

    def test_calculated_measure_cycles_fail_during_model_validation(self):
        model = {
            'name': 'Cycle', 'semantic_family': 'document',
            'sources': [{
                'id': 'items', 'resource_id': 'mongodb:items',
                'relation': ['db', 'items'], 'alias': 'items',
                'source_kind': 'collection',
                'classification': 'document-set', 'grain': [],
            }],
            'measures': [{
                'id': 'left_metric', 'name': 'Left', 'aggregation': 'none',
                'measure_kind': 'calculated', 'expression': {
                    'operator': 'add', 'left': {'measure': 'right_metric'},
                    'right': {'literal': 1},
                },
            }, {
                'id': 'right_metric', 'name': 'Right', 'aggregation': 'none',
                'measure_kind': 'calculated', 'expression': {
                    'operator': 'add', 'left': {'measure': 'left_metric'},
                    'right': {'literal': 1},
                },
            }],
        }
        with self.assertRaisesRegex(
                SemanticModelError, 'cycle is unsupported'):
            validate_model(model)


if __name__ == '__main__':
    unittest.main()
