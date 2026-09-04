##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Tests for the YugabyteDB YCQL strict object-experience gate."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from cdeadmin_yugabytedb_ycql_object_experience_gate import audit  # noqa: E402


class YugabyteDBYCQLObjectGateTests(unittest.TestCase):

    @staticmethod
    def evidence():
        return {
            'schema': 'cdeadmin.provider-object-live-evidence.v1',
            'engine_id': 'yugabytedb',
            'exact_profile': '2025.2.2.2',
            'surface_id': 'cdeadmin.yugabytedb.control-plane',
            'surface_sha256': 'a' * 64,
            'concepts': {'wide_column': {
                'keyspaces': {'status': 'passed', 'operations': {
                    'keyspace': ['inspect', 'create', 'alter', 'drop'],
                }},
                'tables': {'status': 'passed', 'operations': {
                    'table': [
                        'inspect', 'create', 'alter', 'insert', 'update',
                        'delete', 'drop',
                    ],
                }},
                'columns': {'status': 'passed', 'operations': {
                    'column': ['inspect', 'create', 'rename', 'drop'],
                }},
                'types': {'status': 'passed', 'operations': {
                    'user-defined-type': ['inspect', 'create', 'drop'],
                }},
                'materialized_views': {
                    'status': 'passed', 'operations': {},
                },
                'replication_and_compaction': {
                    'status': 'passed', 'operations': {},
                },
            }},
        }

    def test_structural_gate_requires_live_evidence(self):
        result = audit()
        self.assertTrue(result['structural_complete'])
        self.assertFalse(result['live_complete'])

    def test_complete_exact_evidence_activates_all_concepts(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / 'ycql-object-live.json'
            artifact.write_text(
                json.dumps(self.evidence()), encoding='utf-8'
            )
            result = audit([artifact])
        self.assertTrue(result['structural_complete'])
        self.assertTrue(result['live_complete'])
        self.assertEqual(
            0, result['coverage']['blocking_missing_count']
        )


if __name__ == '__main__':
    unittest.main()
