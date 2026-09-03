##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Tests for the fail-closed live connection matrix gate."""

from __future__ import annotations

import copy
import unittest

from tools.cdeadmin_connection_live_matrix_gate import (
    LiveMatrixError,
    audit,
    initialize_matrix,
)
from pgadmin.cdeadmin.endpoints import registration_profiles


def matrix(state='not_run'):
    return {
        'schema': 'cdeadmin.connection-live-matrix.v1',
        'profiles': {
            profile['profile_id']: {
                category: {'state': state, 'evidence': []}
                for category in profile['connection_capabilities'][
                    'categories']
            }
            for profile in registration_profiles()
        },
    }


class ConnectionLiveMatrixGateTests(unittest.TestCase):

    def test_initializer_never_manufactures_a_pass(self):
        value = initialize_matrix()
        states = {
            item['state']
            for categories in value['profiles'].values()
            for item in categories.values()
        }
        self.assertNotIn('passed', states)
        self.assertEqual({'not_applicable', 'not_run'}, states)

    def test_unrun_matrix_fails_closed(self):
        result = audit(matrix())
        self.assertFalse(result['complete'])
        self.assertEqual(26, result['profile_count'])
        self.assertEqual(364, result['category_count'])

    def test_passed_category_requires_evidence(self):
        value = matrix()
        value['profiles']['mongodb-native']['authentication']['state'] = (
            'passed'
        )
        with self.assertRaisesRegex(LiveMatrixError, 'without evidence'):
            audit(value)

    def test_not_applicable_must_match_provider_declaration(self):
        value = matrix()
        value['profiles']['mongodb-native']['authentication']['state'] = (
            'not_applicable'
        )
        with self.assertRaisesRegex(LiveMatrixError, 'not declared'):
            audit(value)

    def test_complete_matrix_accepts_declared_na_categories(self):
        value = matrix('passed')
        for profile in registration_profiles():
            declarations = profile['connection_capabilities']['categories']
            for category, declaration in declarations.items():
                item = value['profiles'][profile['profile_id']][category]
                if declaration['state'] == 'not_applicable':
                    item['state'] = 'not_applicable'
                else:
                    item['evidence'] = ['qualification.json']
        result = audit(copy.deepcopy(value))
        self.assertTrue(result['complete'])


if __name__ == '__main__':
    unittest.main()
