##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Redis provider-specific key/value object-experience gate tests."""

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

from tools.cdeadmin_redis_object_experience_gate import audit  # noqa: E402
from tools.cdeadmin_redis_live_gate import (  # noqa: E402
    FULL_OBJECT_OPERATIONS, _object_evidence,
)


class RedisObjectExperienceGateTests(unittest.TestCase):

    def test_key_value_declarations_are_structurally_complete(self):
        result = audit()
        coverage = result['coverage']
        self.assertTrue(result['structural_complete'])
        self.assertFalse(result['live_complete'])
        self.assertEqual(0, coverage['undeclared_count'])
        self.assertEqual(0, coverage['blocking_missing_count'])
        self.assertEqual(11, coverage['live_evidence_missing_count'])
        self.assertEqual(113, coverage['live_operation_missing_count'])
        self.assertEqual(
            ['key_value'],
            [item['family_id'] for item in coverage['families']],
        )

    def test_live_evidence_is_red_until_every_declared_operation_passes(self):
        evidence = _object_evidence('partial', {'key': ['inspect']})
        self.assertFalse(evidence['passed'])
        self.assertIn('key', evidence['missing_resource_operations'])
        self.assertNotIn(
            'inspect', evidence['missing_resource_operations']['key']
        )

    def test_live_evidence_closes_exact_declared_operation_matrix(self):
        evidence = _object_evidence('complete', FULL_OBJECT_OPERATIONS)
        self.assertTrue(evidence['passed'])
        self.assertEqual({}, evidence['missing_resource_operations'])
        self.assertFalse(evidence['raw_commands_used_for_provider_operations'])
        self.assertFalse(evidence['common_transaction_finality_interpreted'])

    def test_exact_live_evidence_activates_every_key_value_concept(self):
        evidence = _object_evidence('complete', FULL_OBJECT_OPERATIONS)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'redis-object-evidence.json'
            path.write_text(json.dumps(evidence), encoding='utf-8')
            result = audit([path])
        self.assertTrue(result['live_complete'])
        self.assertEqual(0, result['coverage']['live_evidence_missing_count'])
        self.assertEqual(0, result['coverage']['live_operation_missing_count'])


if __name__ == '__main__':
    unittest.main()
