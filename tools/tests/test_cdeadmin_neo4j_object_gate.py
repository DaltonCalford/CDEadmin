##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Neo4j provider-specific graph object-experience gate tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
for path in (ROOT, WEB):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from tools.cdeadmin_neo4j_live_gate import (  # noqa: E402
    COMMUNITY_OBJECT_OPERATIONS, FULL_OBJECT_OPERATIONS, _object_evidence,
)
from tools.cdeadmin_neo4j_gds_live_gate import (  # noqa: E402
    object_evidence as gds_object_evidence,
)
from tools.cdeadmin_neo4j_enterprise_live_gate import (  # noqa: E402
    EXPECTED_IMAGE_ID,
    object_evidence as enterprise_object_evidence,
)
from tools.cdeadmin_neo4j_object_experience_gate import (  # noqa: E402
    audit, provider_catalog,
)


class Neo4jObjectExperienceGateTests(unittest.TestCase):

    def test_graph_provider_declaration_is_structurally_complete(self):
        result = audit()
        coverage = result['coverage']
        self.assertTrue(result['structural_complete'])
        self.assertFalse(result['live_complete'])
        self.assertEqual(0, coverage['undeclared_count'])
        self.assertEqual(0, coverage['blocking_missing_count'])
        self.assertEqual(11, coverage['live_evidence_missing_count'])
        self.assertEqual(31, coverage['live_operation_missing_count'])
        self.assertEqual(
            ['graph'],
            [item['family_id'] for item in coverage['families']],
        )

    def test_community_evidence_records_only_proven_operations(self):
        evidence = _object_evidence(
            'unit-community', COMMUNITY_OBJECT_OPERATIONS
        )
        self.assertFalse(evidence['passed'])
        self.assertEqual(
            ['alter', 'create', 'drop'],
            evidence['operation_failures']['database'],
        )
        self.assertEqual(
            ['alter', 'execute', 'inspect'],
            evidence['operation_failures']['server'],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            path.write_text(json.dumps(evidence), encoding='utf-8')
            coverage = provider_catalog([path])['concept_coverage']
        self.assertFalse(coverage['activation_ready'])
        self.assertEqual(2, coverage['live_evidence_missing_count'])
        self.assertEqual(9, coverage['live_operation_missing_count'])

    def test_enterprise_and_gds_evidence_can_complete_the_gate(self):
        evidence = _object_evidence(
            'unit-full', FULL_OBJECT_OPERATIONS, 'a' * 64
        )
        self.assertTrue(evidence['passed'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            path.write_text(json.dumps(evidence), encoding='utf-8')
            coverage = provider_catalog([path])['concept_coverage']
        self.assertTrue(coverage['activation_ready'])
        self.assertEqual(0, coverage['live_evidence_missing_count'])
        self.assertEqual(0, coverage['live_operation_missing_count'])

    def test_gds_evidence_is_digest_bound_and_composes_with_community(self):
        community = _object_evidence(
            'unit-community', COMMUNITY_OBJECT_OPERATIONS
        )
        gds = gds_object_evidence(
            'unit-gds', 'b' * 64, {'create', 'inspect', 'drop'}
        )
        self.assertTrue(gds['passed'])
        self.assertEqual(
            'neo4j-graph-data-science-plugin', gds['surface_id']
        )
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / 'community.json'
            second = Path(directory) / 'gds.json'
            first.write_text(json.dumps(community), encoding='utf-8')
            second.write_text(json.dumps(gds), encoding='utf-8')
            coverage = provider_catalog([first, second])['concept_coverage']
        self.assertFalse(coverage['activation_ready'])
        self.assertEqual(1, coverage['live_evidence_missing_count'])
        self.assertEqual(6, coverage['live_operation_missing_count'])

    def test_enterprise_evidence_closes_community_and_gds_coverage(self):
        documents = (
            _object_evidence(
                'unit-community', COMMUNITY_OBJECT_OPERATIONS
            ),
            gds_object_evidence(
                'unit-gds', 'b' * 64, {'create', 'inspect', 'drop'}
            ),
            enterprise_object_evidence(
                'unit-enterprise', EXPECTED_IMAGE_ID, {
                    'database': {'create', 'inspect', 'alter', 'drop'},
                    'server': {'inspect', 'alter', 'execute'},
                },
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, document in enumerate(documents):
                path = Path(directory) / f'evidence-{index}.json'
                path.write_text(json.dumps(document), encoding='utf-8')
                paths.append(path)
            coverage = provider_catalog(paths)['concept_coverage']
        self.assertTrue(coverage['activation_ready'])
        self.assertEqual(0, coverage['live_evidence_missing_count'])
        self.assertEqual(0, coverage['live_operation_missing_count'])


if __name__ == '__main__':
    unittest.main()
