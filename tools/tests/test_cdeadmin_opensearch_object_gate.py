##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Tests for the OpenSearch object-experience gate."""

import json
import tempfile
import unittest
from pathlib import Path

from tools.cdeadmin_opensearch_object_experience_gate import audit
from tools.cdeadmin_opensearch_object_experience_gate import provider_catalog


class OpenSearchObjectExperienceGateTestCase(unittest.TestCase):

    def test_structural_catalog_covers_every_search_concept(self):
        result = audit()
        self.assertTrue(result['structural_complete'])
        self.assertFalse(result['live_complete'])
        coverage = result['coverage']
        self.assertEqual(0, coverage['undeclared_count'])
        self.assertEqual(0, coverage['blocking_missing_count'])
        required_kinds = {
            'index', 'mapping', 'settings', 'alias', 'index-template',
            'component-template', 'ingest-pipeline', 'shard',
            'reindex-operation', 'snapshot', 'ingest-processor',
            'query-profile',
        }
        for resource in provider_catalog()['objects']:
            if resource['resource_kind'] not in required_kinds:
                continue
            for operation in resource['operations']:
                labels = {
                    field['label'] for field in operation['form']['fields']
                }
                self.assertNotIn('Native JSON definition', labels)

    def test_exact_live_evidence_activates_catalog(self):
        operations = {
            'index': [
                'alter', 'create', 'delete', 'drop', 'insert', 'inspect',
                'update',
            ],
            'mapping': ['alter', 'create', 'inspect'],
            'settings': ['alter', 'inspect'],
            'alias': ['alter', 'create', 'drop', 'inspect'],
            'index-template': ['alter', 'create', 'drop', 'inspect'],
            'component-template': ['alter', 'create', 'drop', 'inspect'],
            'ingest-pipeline': ['alter', 'create', 'drop', 'inspect'],
            'shard': ['inspect'],
            'reindex-operation': ['execute'],
            'snapshot': ['execute', 'inspect'],
            'ingest-processor': ['alter', 'create', 'drop', 'inspect'],
            'query-profile': ['execute'],
        }
        evidence = {
            'schema': 'cdeadmin.provider-object-live-evidence.v1',
            'engine_id': 'opensearch',
            'exact_profile': '3.6.0',
            'passed': True,
            'passed_resource_operations': operations,
            'concepts': {'search': {
                'indices': {'status': 'passed', 'operations': {
                    'index': operations['index']}},
                'mappings': {'status': 'passed', 'operations': {
                    'mapping': operations['mapping']}},
                'settings': {'status': 'passed', 'operations': {
                    'settings': operations['settings']}},
                'aliases': {'status': 'passed', 'operations': {
                    'alias': operations['alias']}},
                'templates': {'status': 'passed', 'operations': {
                    'index-template': operations['index-template'],
                    'component-template': operations['component-template'],
                }},
                'pipelines': {'status': 'passed', 'operations': {
                    'ingest-pipeline': operations['ingest-pipeline']}},
                'shards_and_replicas': {
                    'status': 'passed', 'operations': {
                        'shard': operations['shard']}},
                'reindex_operations': {
                    'status': 'passed', 'operations': {
                        'reindex-operation': operations[
                            'reindex-operation']}},
                'snapshots': {'status': 'passed', 'operations': {
                    'snapshot': operations['snapshot']}},
                'ingest_processors': {
                    'status': 'passed', 'operations': {
                        'ingest-processor': operations['ingest-processor']}},
                'query_profiling': {
                    'status': 'passed', 'operations': {
                        'query-profile': operations['query-profile']}},
            }},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'live.json'
            path.write_text(json.dumps(evidence), encoding='utf-8')
            result = audit([path])
        self.assertTrue(result['live_complete'])
        self.assertEqual(
            0, result['coverage']['live_operation_missing_count']
        )


if __name__ == '__main__':
    unittest.main()
