##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Tests for the Firebird provider-owned transaction round-trip gate."""

import unittest

from tools.cdeadmin_relational_provider_live_verify import (
    _firebird_transaction_round_trip,
)


class _Provider:
    def __init__(self, scalar_values):
        self.scalar_values = iter(scalar_values)
        self.operations = {}
        self.controls = []

    def execute(self, request):
        operation_id = str(len(self.operations) + 1)
        self.operations[operation_id] = dict(request)
        return {'operation_id': operation_id}

    def describe_result(self, operation):
        request = self.operations[operation['operation_id']]
        rows = []
        if request['source'].startswith('SELECT'):
            rows = [[next(self.scalar_values)]]
        return {
            'complete': True,
            'extensions': {'firebird': {'payload': {'rows': rows}}},
        }

    def control_transaction(self, request):
        self.controls.append(request['action'])
        return {'provider_payload': {
            'driver_observation_only': True,
            'finality_interpreted_by_common_code': False,
        }}


class FirebirdTransactionGateTestCase(unittest.TestCase):
    def test_insert_update_delete_commit_and_rollback_are_verified(self):
        provider = _Provider([0, 11, 11, 22, 1, 0])

        evidence = _firebird_transaction_round_trip(
            provider, {'session_id': 'firebird-session'}
        )

        self.assertEqual(
            ['insert', 'update', 'delete'],
            evidence['mutation_operations'],
        )
        self.assertEqual(
            ['rollback', 'commit', 'rollback', 'commit', 'rollback',
             'commit', 'rollback'],
            provider.controls,
        )
        self.assertEqual(6, len(evidence['observations']))
        self.assertTrue(evidence['provider_finality_authority'])
        self.assertFalse(evidence['common_finality_interpreted'])
        self.assertTrue(evidence['cleanup_verified'])

    def test_incorrect_provider_observation_fails_the_gate(self):
        provider = _Provider([1])

        with self.assertRaisesRegex(
                RuntimeError, 'insert rollback did not restore state'):
            _firebird_transaction_round_trip(
                provider, {'session_id': 'firebird-session'}
            )


if __name__ == '__main__':
    unittest.main()
