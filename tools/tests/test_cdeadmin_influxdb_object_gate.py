##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Tests for the InfluxDB time-series and semantic object gate."""

import unittest

from tools.cdeadmin_influxdb_object_experience_gate import audit


class InfluxDBObjectExperienceGateTestCase(unittest.TestCase):

    def test_structural_catalog_covers_both_experience_families(self):
        result = audit()
        self.assertTrue(result['structural_complete'])
        self.assertFalse(result['live_complete'])
        coverage = result['coverage']
        self.assertEqual(0, coverage['undeclared_count'])
        self.assertEqual(0, coverage['blocking_missing_count'])
        self.assertEqual(
            {'time_series', 'semantic'},
            {item['family_id'] for item in coverage['families']},
        )


if __name__ == '__main__':
    unittest.main()
