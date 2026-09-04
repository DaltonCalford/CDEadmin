##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Tests for the Cassandra wide-column object gate."""

import unittest

from tools.cdeadmin_cassandra_object_experience_gate import audit


class CassandraObjectExperienceGateTestCase(unittest.TestCase):

    def test_structural_catalog_covers_every_wide_column_concept(self):
        result = audit()
        self.assertTrue(result['structural_complete'])
        self.assertFalse(result['live_complete'])
        coverage = result['coverage']
        self.assertEqual(0, coverage['undeclared_count'])
        self.assertEqual(0, coverage['blocking_missing_count'])
        self.assertEqual(6, len(coverage['families'][0]['concepts']))


if __name__ == '__main__':
    unittest.main()
