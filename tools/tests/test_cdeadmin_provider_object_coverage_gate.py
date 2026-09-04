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

import copy
import unittest

from tools.cdeadmin_provider_object_coverage_gate import (
    audit, provider_catalogs,
)


class ProviderObjectCoverageGateTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.catalogs = provider_catalogs()

    def test_inventory_is_structurally_complete_and_fail_closed(self):
        result = audit(self.catalogs)
        self.assertEqual(26, result['profile_count'])
        self.assertEqual(35, result['family_slice_count'])
        self.assertEqual(466, result['concept_count'])
        self.assertEqual(
            result['concept_count'],
            result['catalogued_count'] +
            result['external_surface_count'] +
            result['not_applicable_count'],
        )
        self.assertEqual(result['concept_count'], result['declared_count'])
        self.assertEqual(0, result['undeclared_count'])
        self.assertEqual(0, result['blocking_missing_count'])
        self.assertEqual([], result['failures'])
        self.assertTrue(result['complete'])
        self.assertEqual(
            ['scratchbird'], result['scope']['deferred_engine_ids']
        )
        self.assertEqual(
            'delegated-to-strict-provider-engine-gates',
            result['scope']['live_activation'],
        )

        broken = copy.deepcopy(self.catalogs)
        declaration = broken['neo4j-native']['descriptor'][
            'concept_declarations']['graph']['nodes']
        declaration['status'] = 'unfinished'
        from pgadmin.cdeadmin.visual_admin import enrich_engine_experience
        broken['neo4j-native']['descriptor'] = enrich_engine_experience(
            broken['neo4j-native']['descriptor']
        )
        failed = audit(broken)
        self.assertFalse(failed['complete'])
        self.assertTrue(any(
            value.endswith('graph.nodes:undeclared')
            for value in failed['failures']
        ))

    def test_inventory_preserves_specialized_provider_families(self):
        result = audit(self.catalogs)

        def families(profile_id):
            return {
                item['family_id']
                for item in result['profiles'][profile_id]['concepts']
            }
        self.assertIn('columnar', families('clickhouse-native'))
        self.assertIn('time_series', families('influxdb-native'))
        self.assertIn('vector', families('milvus-native'))
        self.assertIn('wide_column', families('cassandra-native'))
        self.assertIn('graph', families('neo4j-native'))
        self.assertIn('search', families('opensearch-native'))
        self.assertEqual(
            {'wide_column'}, families('yugabytedb-ycql')
        )
        self.assertEqual(
            {'relational'}, families('yugabytedb-native')
        )


if __name__ == '__main__':
    unittest.main()
