##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider navigator and object-editor coverage gate tests."""

from __future__ import annotations

import unittest

from tools.cdeadmin_provider_object_coverage_gate import audit


class ProviderObjectCoverageGateTests(unittest.TestCase):

    def test_inventory_is_complete_in_scope_and_fail_closed(self):
        result = audit()
        self.assertEqual(26, result['engine_count'])
        self.assertGreater(result['concept_count'], 200)
        self.assertEqual(
            result['concept_count'],
            result['catalogued_count'] + result['missing_catalog_count'],
        )
        self.assertEqual(
            result['concept_count'],
            result['declared_count'] + result['undeclared_count'],
        )
        self.assertFalse(result['complete'])
        self.assertGreater(result['undeclared_count'], 0)
        self.assertTrue(any(
            value.endswith(':undeclared') for value in result['failures']
        ))

    def test_inventory_preserves_specialized_provider_families(self):
        result = audit()

        def families(engine):
            return {
                item['family_id']
                for item in result['engines'][engine]['concepts']
            }
        self.assertIn('columnar', families('clickhouse'))
        self.assertIn('time_series', families('influxdb'))
        self.assertIn('vector', families('milvus'))
        self.assertIn('wide_column', families('cassandra'))
        self.assertIn('graph', families('neo4j'))
        self.assertIn('search', families('opensearch'))


if __name__ == '__main__':
    unittest.main()
