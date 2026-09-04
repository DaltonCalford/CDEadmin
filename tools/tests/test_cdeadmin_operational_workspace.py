##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider operational workspace contract and portfolio gate tests."""

from __future__ import annotations

import copy
import unittest

from tools.cdeadmin_operational_workspace_gate import audit
from tools.cdeadmin_provider_object_coverage_gate import provider_catalogs
from pgadmin.cdeadmin.visual_admin.operational_workspace import (
    FACETS, OPERATIONAL_WORKSPACE_SCHEMA, OperationalWorkspaceError,
    build_operational_workspace,
)


class OperationalWorkspaceTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.catalogs = provider_catalogs()

    def test_every_profile_has_complete_explicit_workspace_facets(self):
        result = audit(self.catalogs)
        self.assertTrue(result['complete'], result['failures'])
        self.assertEqual(26, result['profile_count'])
        self.assertEqual(27, result['general_facet_count'])
        self.assertEqual(11, result['distributed_facet_count'])
        self.assertEqual([], result['failures'])
        self.assertTrue(result['integration_complete'])
        self.assertEqual(
            ['scratchbird'], result['scope']['deferred_engine_ids']
        )
        self.assertGreater(result['state_counts']['operational'], 0)
        self.assertGreater(result['state_counts']['unavailable'], 0)

    def test_projection_never_invents_operations_or_retry_authority(self):
        catalog = self.catalogs['cockroachdb-native']['descriptor']
        workspace = build_operational_workspace(catalog)
        self.assertEqual(OPERATIONAL_WORKSPACE_SCHEMA, workspace['schema'])
        self.assertTrue(workspace['distributed'])
        self.assertFalse(
            workspace['execution_contract']['automatic_mutation_retry']
        )
        declared = {
            (item['resource_kind'], operation['operation_id'])
            for item in catalog['objects']
            for operation in item.get('operations', [])
        }
        projected = {
            (operation['resource_kind'], operation['operation_id'])
            for facet in workspace['facets']
            for operation in facet['operations']
        }
        self.assertTrue(projected.issubset(declared))
        self.assertIn('topology', {
            item['facet_id'] for item in workspace['facets']
        })

    def test_single_node_provider_has_general_facets_only(self):
        catalog = self.catalogs['sqlite-native']['descriptor']
        workspace = build_operational_workspace(catalog)
        self.assertFalse(workspace['distributed'])
        self.assertEqual(27, len(workspace['facets']))
        self.assertNotIn('topology', {
            item['facet_id'] for item in workspace['facets']
        })

    def test_unavailable_facets_are_explicit(self):
        catalog = copy.deepcopy(
            self.catalogs['sqlite-native']['descriptor']
        )
        catalog['objects'] = []
        workspace = build_operational_workspace(catalog)
        self.assertTrue(all(
            item['catalog_state'] == 'unavailable' and
            item['unavailable_reason']
            for item in workspace['facets']
        ))

    def test_invalid_catalog_fails_closed(self):
        with self.assertRaises(OperationalWorkspaceError):
            build_operational_workspace({'engine_id': 'broken'})
        self.assertEqual(38, len(FACETS))


if __name__ == '__main__':
    unittest.main()
