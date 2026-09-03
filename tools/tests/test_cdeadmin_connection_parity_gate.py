##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Connection parity gate tests."""

from __future__ import annotations

import unittest

from tools.cdeadmin_connection_parity_gate import audit


class ConnectionParityGateTests(unittest.TestCase):

    def test_gate_covers_every_active_engine_interface_profile(self):
        result = audit()
        self.assertEqual(26, result['profile_count'])
        self.assertEqual(26, len({
            item['profile_id'] for item in result['profiles']
        }))

    def test_gate_requires_every_profile_to_be_complete(self):
        result = audit()
        self.assertTrue(result['complete'])
        self.assertEqual([], result['incomplete_profiles'])
        for profile in result['profiles']:
            self.assertEqual([], profile['incomplete_categories'])


if __name__ == '__main__':
    unittest.main()
