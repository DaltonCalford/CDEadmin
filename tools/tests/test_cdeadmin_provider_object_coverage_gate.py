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
        self.assertEqual(494, result['concept_count'])
        self.assertEqual(
            result['concept_count'],
            result['catalogued_count'] +
            result['external_surface_count'] +
            result['not_applicable_count'],
        )
        self.assertEqual(result['concept_count'], result['declared_count'])
        self.assertEqual(0, result['undeclared_count'])
        self.assertEqual(0, result['blocking_missing_count'])
        self.assertEqual(
            result['native_graphical_operation_count'],
            result['graphical_operation_count'],
        )
        self.assertEqual(2015, result['graphical_operation_count'])
        self.assertEqual(0, result['activation_permission_failure_count'])
        self.assertEqual(0, result['provider_identity_failure_count'])
        self.assertEqual(0, result['shared_semantics_failure_count'])
        self.assertEqual(0, result['support_inference_failure_count'])
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

        broken = copy.deepcopy(self.catalogs)
        graphical = broken['neo4j-native']['descriptor'][
            'graphical_interface'
        ]
        graphical['activation_state'] = 'blocked'
        graphical['graphical_operation_count'] -= 1
        graphical['missing_operations'] = [{
            'resource_kind': 'node', 'operation_id': 'update',
        }]
        failed = audit(broken)
        self.assertFalse(failed['complete'])
        self.assertIn(
            'neo4j-native:graphical-form-missing:node.update',
            failed['failures'],
        )

        broken = copy.deepcopy(self.catalogs)
        broken['neo4j-native']['descriptor']['graphical_interface'][
            'shared_engine_semantics_allowed'
        ] = True
        failed = audit(broken)
        self.assertFalse(failed['complete'])
        self.assertIn(
            'neo4j-native:shared-engine-semantics-admitted',
            failed['failures'],
        )

        broken = copy.deepcopy(self.catalogs)
        broken['firebird-native']['granted_permissions'].remove(
            'maintenance_admin'
        )
        failed = audit(broken)
        self.assertFalse(failed['complete'])
        self.assertGreater(
            failed['activation_permission_failure_count'], 0
        )
        self.assertTrue(any(
            value.startswith(
                'firebird-native:operation-permission-unadmitted:'
                'database.repair_database:maintenance_admin'
            )
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

    def test_mysql_family_metrics_are_graphically_reachable(self):
        for profile_id in ('mysql-native', 'mariadb-native'):
            objects = {
                item['resource_kind']: item
                for item in self.catalogs[profile_id]['descriptor']['objects']
            }
            self.assertIn('metric', objects)
            self.assertEqual(
                ['inspect'],
                [
                    operation['operation_id']
                    for operation in objects['metric']['operations']
                ],
            )


if __name__ == '__main__':
    unittest.main()
