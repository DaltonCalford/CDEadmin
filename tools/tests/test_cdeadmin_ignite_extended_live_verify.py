##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Tests for Apache Ignite extended live-evidence projection."""

import unittest

from tools.cdeadmin_ignite_extended_live_verify import object_evidence


class IgniteExtendedLiveVerifierTests(unittest.TestCase):

    def test_projects_only_operations_admitted_by_each_concept(self):
        def record(kind, operation):
            return {
                'resource_kind': kind,
                'operation_id': operation,
                'accepted': True,
                'post_state_confirmed': True,
            }
        evidence = object_evidence({
            'status': 'passed',
            'operations': [
                record('user', 'inspect'),
                record('user', 'create'),
                record('cache', 'inspect'),
                record('cache', 'insert'),
                record('cache', 'validate_indexes'),
                record('cache-template', 'inspect'),
                record('data-region', 'inspect'),
                record('snapshot', 'create'),
                record('snapshot', 'inspect'),
            ],
            'cleanup': [record('cache', 'drop')],
        })
        concepts = evidence['concepts']
        self.assertEqual(
            ['create', 'inspect'],
            concepts['relational']['roles_and_grants'][
                'operations']['user'],
        )
        key_value = concepts['key_value']
        self.assertEqual(
            ['inspect'], key_value['key_browsing']['operations']['cache'])
        self.assertEqual(
            ['insert'],
            key_value['data_type_editing']['operations']['cache'],
        )
        cluster = key_value['sentinel_or_cluster_state']['operations']
        self.assertEqual(
            ['drop', 'inspect', 'validate_indexes'], cluster['cache'])
        self.assertEqual(['inspect'], cluster['cache-template'])
        self.assertEqual(['inspect'], cluster['data-region'])
        self.assertEqual(['create', 'inspect'], cluster['snapshot'])


if __name__ == '__main__':
    unittest.main()
