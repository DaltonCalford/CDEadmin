##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""FoundationDB provider-specific key/value experience gate tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
for path in (ROOT, WEB):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from tools.cdeadmin_foundationdb_object_experience_gate import (  # noqa: E402
    audit, provider_catalog,
)


class FoundationDBObjectExperienceGateTests(unittest.TestCase):

    @staticmethod
    def _complete_evidence():
        concepts = {}
        for family in provider_catalog()['concept_coverage']['families']:
            results = {}
            for concept in family['concepts']:
                if concept['declared_status'] == 'supported':
                    results[concept['concept_id']] = {
                        'status': 'passed',
                        'operations': concept['operation_obligations'],
                    }
            if results:
                concepts[family['family_id']] = results
        return {
            'schema': 'cdeadmin.provider-object-live-evidence.v1',
            'engine_id': 'foundationdb',
            'exact_profile': '7.3.77',
            'run_id': 'foundationdb-unit-complete',
            'concepts': concepts,
        }

    def test_key_value_declarations_are_structurally_complete(self):
        result = audit()
        coverage = result['coverage']
        self.assertTrue(result['structural_complete'])
        self.assertFalse(result['live_complete'])
        self.assertEqual(0, coverage['undeclared_count'])
        self.assertEqual(0, coverage['blocking_missing_count'])
        self.assertEqual(
            ['key_value'],
            [item['family_id'] for item in coverage['families']],
        )

    def test_redis_only_concepts_are_explicitly_not_applicable(self):
        concepts = {
            item['concept_id']: item
            for item in provider_catalog()['concept_coverage'][
                'families'
            ][0]['concepts']
        }
        for concept_id in (
            'ttl_inspection', 'expiration_management', 'streams', 'pubsub',
            'consumer_groups', 'modules', 'acls',
        ):
            with self.subTest(concept_id=concept_id):
                self.assertEqual(
                    'not_applicable',
                    concepts[concept_id]['declared_status'],
                )

    def test_exact_complete_live_evidence_activates_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'foundationdb-live.json'
            path.write_text(
                json.dumps(self._complete_evidence()), encoding='utf-8'
            )
            result = audit([path])
        self.assertTrue(result['live_complete'])
        self.assertEqual(0, result['coverage'][
            'live_operation_missing_count'
        ])


if __name__ == '__main__':
    unittest.main()
