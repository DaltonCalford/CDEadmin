##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Distributed/hybrid relational object-experience gate tests."""

from __future__ import annotations

import unittest

from tools.cdeadmin_distributed_relational_object_experience_gate import (
    ADMINISTRATIONS,
    audit,
)


class DistributedRelationalObjectGateTests(unittest.TestCase):

    def test_gate_covers_every_relational_distributed_provider(self):
        result = audit()
        self.assertEqual(set(ADMINISTRATIONS), set(result['engines']))
        self.assertEqual(6, result['engine_count'])

    def test_cockroach_declaration_is_complete_but_live_fail_closed(self):
        result = audit()
        coverage = result['engines']['cockroachdb']
        self.assertTrue(coverage['declaration_ready'])
        self.assertEqual(0, coverage['undeclared_count'])
        self.assertEqual(0, coverage['blocking_missing_count'])
        self.assertFalse(coverage['activation_ready'])
        self.assertIn('cockroachdb', result['live_failures'])


if __name__ == '__main__':
    unittest.main()
