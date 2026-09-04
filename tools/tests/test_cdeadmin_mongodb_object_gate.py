##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""MongoDB provider-specific object-experience gate tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from tools.cdeadmin_mongodb_object_experience_gate import (  # noqa: E402
    audit, provider_catalog,
)


class MongoDBObjectExperienceGateTests(unittest.TestCase):

    def test_exact_provider_declaration_is_structurally_complete(self):
        result = audit()
        coverage = result['coverage']
        self.assertTrue(result['structural_complete'])
        self.assertFalse(result['live_complete'])
        self.assertEqual(0, coverage['undeclared_count'])
        self.assertEqual(0, coverage['blocking_missing_count'])
        self.assertEqual(9, coverage['live_evidence_missing_count'])
        self.assertEqual(48, coverage['live_operation_missing_count'])

    def test_all_document_concepts_have_provider_owned_operations(self):
        coverage = provider_catalog()['concept_coverage']
        concepts = {
            item['concept_id']: item
            for family in coverage['families']
            for item in family['concepts']
        }
        self.assertEqual({
            'databases', 'collections', 'documents', 'validation_rules',
            'indexes', 'views', 'aggregation_pipelines',
            'users_and_roles', 'replica_sets_and_sharding',
        }, set(concepts))
        self.assertTrue(all(
            item['declared_status'] == 'supported'
            for item in concepts.values()
        ))
        self.assertEqual(
            ['execute', 'inspect'],
            concepts['aggregation_pipelines']['operation_obligations'][
                'aggregation-pipeline'
            ],
        )


if __name__ == '__main__':
    unittest.main()
