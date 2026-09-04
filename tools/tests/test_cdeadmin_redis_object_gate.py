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

import sys
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


if __name__ == '__main__':
    unittest.main()
