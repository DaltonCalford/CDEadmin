"""Portfolio gate tests for provider-aware semantic analytics."""

from __future__ import annotations

import unittest

from tools.cdeadmin_provider_object_coverage_gate import provider_catalogs
from tools.cdeadmin_semantic_analytics_gate import audit
from pgadmin.cdeadmin.semantic_models.profiles import analytical_profile


class SemanticAnalyticsGateTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.result = audit(provider_catalogs())

    def test_all_reference_profiles_have_recognized_family_profiles(self):
        self.assertTrue(self.result['complete'], self.result['failures'])
        self.assertEqual(26, self.result['profile_count'])
        self.assertGreaterEqual(self.result['semantic_family_count'], 8)
        self.assertEqual(23, self.result['required_capability_count'])
        self.assertEqual(3, self.result['native_compiler_count'])
        self.assertEqual({
            'mongodb-native': 'mongodb-aggregation',
            'neo4j-native': 'neo4j-cypher',
            'opensearch-native': 'opensearch-composite-aggregation',
        }, self.result['native_compilers'])
        self.assertEqual([], self.result['failures'])
        self.assertEqual(
            ['scratchbird'], self.result['scope']['deferred_engine_ids']
        )

    def test_non_relational_families_keep_distinct_dimensions(self):
        profiles = self.result['profiles']
        self.assertIn(
            'native-edge', analytical_profile('graph')['relationship_kinds']
        )
        self.assertEqual(
            'vector', profiles['milvus-native']['semantic_family']
        )
        self.assertEqual(
            'search', profiles['opensearch-native']['semantic_family']
        )
        self.assertEqual(
            'document', profiles['mongodb-native']['semantic_family']
        )


if __name__ == '__main__':
    unittest.main()
