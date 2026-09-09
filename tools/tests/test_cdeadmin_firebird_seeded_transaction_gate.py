##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Tests for the packaged Firebird seeded-object transaction gate."""

import json
import tempfile
import unittest
from pathlib import Path

from tools.cdeadmin_firebird_seeded_transaction_gate import (
    SEEDED_OBJECTS,
    TABLE_CASES,
    _inspect_seeded_objects,
    _load_profile,
    _session_reference,
)


class _InspectionProvider:
    def __init__(self):
        self.inspected = []

    @staticmethod
    def list_resources(_request):
        return [
            {
                'resource_kind': kind,
                'display_name': name,
                'resource_id': f'firebird:{kind}:{name}',
            }
            for kind, names in SEEDED_OBJECTS.items()
            for name in names
        ]

    def inspect_resource(self, request):
        self.inspected.append(request['resource_id'])
        return {
            'resource_kind': request['resource_id'].split(':')[1],
        }


class FirebirdSeededTransactionGateTestCase(unittest.TestCase):
    def test_profile_inherits_document_host(self):
        document = {
            'host': '127.0.0.1',
            'profiles': [{
                'engine': 'firebird', 'port': 53050,
                'database': '/demo.fdb', 'user': 'SYSDBA',
                'password': 'not-exported',
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profiles.json'
            path.write_text(json.dumps(document), encoding='utf-8')

            profile = _load_profile(path)

        self.assertEqual('127.0.0.1', profile['host'])
        self.assertEqual('firebird', profile['engine'])

    def test_seeded_inventory_and_editable_tables_are_complete(self):
        self.assertEqual(
            {'CUSTOMERS', 'ASSETS', 'WORK_ORDERS'},
            SEEDED_OBJECTS['table'],
        )
        self.assertEqual(
            SEEDED_OBJECTS['table'],
            {case['table'] for case in TABLE_CASES},
        )

    def test_seeded_objects_are_opened_through_provider_inspection(self):
        provider = _InspectionProvider()

        evidence = _inspect_seeded_objects(provider, {'route': {}})

        expected_count = sum(len(names) for names in SEEDED_OBJECTS.values())
        self.assertEqual(expected_count, len(provider.inspected))
        self.assertEqual(set(SEEDED_OBJECTS), set(evidence))

    def test_session_evidence_is_redacted(self):
        session_id = 'private-provider-session-id'

        reference = _session_reference({'session_id': session_id})

        self.assertNotIn(session_id, reference)
        self.assertEqual(16, len(reference))


if __name__ == '__main__':
    unittest.main()
