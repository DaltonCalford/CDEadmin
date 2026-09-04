##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Tests for TiKV full control-plane live evidence construction."""

import unittest

from tools.cdeadmin_tikv_control_plane_live_verify import (
    _api_v2_raw_region_key, _memcmp_encode, object_evidence,
)


class TiKVControlPlaneLiveVerifierTests(unittest.TestCase):

    def test_memcomparable_region_key_encoding(self):
        self.assertEqual(
            b'12345678\xff' + (b'\x00' * 8) + b'\xf7',
            _memcmp_encode(b'12345678'),
        )
        self.assertTrue(
            _api_v2_raw_region_key('key').startswith('720000006b657900')
        )

    def test_region_and_rule_operations_qualify_replication(self):
        evidence = object_evidence({
            'passed': True, 'started_at': 1,
            'resource_kinds': [
                'cluster', 'store', 'region', 'peer', 'scheduler',
                'configuration', 'placement-rule',
            ],
            'operations': [
                {'operation': 'region.transfer_leader'},
                {'operation': 'region.add_peer'},
                {'operation': 'placement-rule.create'},
                {'operation': 'cluster.config.replica-schedule-limit'},
                {'operation': 'keyspace.create'},
            ],
        })
        concepts = evidence['concepts']['key_value']
        replication = concepts['replication']['operations']
        self.assertIn('transfer_leader', replication['region'])
        self.assertIn('add_peer', replication['region'])
        self.assertIn('create', replication['placement-rule'])
        cluster = concepts['sentinel_or_cluster_state']['operations']
        self.assertIn('transfer_leader', cluster['region'])
        self.assertNotIn('keyspace', cluster)
        self.assertNotIn('config.replica-schedule-limit', cluster['cluster'])


if __name__ == '__main__':
    unittest.main()
