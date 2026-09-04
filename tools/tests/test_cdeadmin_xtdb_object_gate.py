##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""XTDB provider-specific object-experience gate tests."""

from __future__ import annotations

import sys
import json
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

from tools.cdeadmin_xtdb_live_gate import _object_evidence  # noqa: E402
from tools.cdeadmin_xtdb_object_experience_gate import (  # noqa: E402
    audit, provider_catalog,
)


class XTDBObjectExperienceGateTests(unittest.TestCase):

    OPERATIONS = {
        'cluster': ['inspect'],
        'node': ['inspect'],
        'database': ['create', 'drop', 'inspect'],
        'schema': ['inspect'],
        'table': ['create', 'delete', 'erase', 'insert', 'inspect', 'update'],
        'column': ['inspect'],
        'document': ['delete', 'erase', 'insert', 'inspect', 'update'],
        'entity': ['delete', 'erase', 'insert', 'inspect', 'update'],
        'valid-time': ['inspect'],
        'system-time': ['inspect'],
        'transaction': ['inspect'],
        'transaction-log': ['inspect'],
        'user': ['alter', 'create', 'inspect'],
    }

    def test_dual_provider_declaration_is_structurally_complete(self):
        result = audit()
        coverage = result['coverage']
        self.assertTrue(result['structural_complete'])
        self.assertFalse(result['live_complete'])
        self.assertEqual(0, coverage['undeclared_count'])
        self.assertEqual(0, coverage['blocking_missing_count'])
        self.assertEqual(14, coverage['live_evidence_missing_count'])
        self.assertEqual(39, coverage['live_operation_missing_count'])
        self.assertEqual(
            ['document', 'relational', 'bitemporal'],
            [item['family_id'] for item in coverage['families']],
        )

    def test_exact_operation_evidence_activates_every_applicable_concept(self):
        evidence = _object_evidence('unit-test', self.OPERATIONS)
        self.assertTrue(evidence['passed'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            path.write_text(json.dumps(evidence), encoding='utf-8')
            coverage = provider_catalog([path])['concept_coverage']
        self.assertTrue(coverage['activation_ready'])
        self.assertEqual(0, coverage['live_evidence_missing_count'])
        self.assertEqual(0, coverage['live_operation_missing_count'])

    def test_incomplete_operation_evidence_fails_closed(self):
        operations = dict(self.OPERATIONS)
        operations['entity'] = ['inspect']
        evidence = _object_evidence('unit-test', operations)
        self.assertFalse(evidence['passed'])
        self.assertEqual(
            ['delete', 'erase', 'insert', 'update'],
            evidence['operation_failures']['entity'],
        )


if __name__ == '__main__':
    unittest.main()
