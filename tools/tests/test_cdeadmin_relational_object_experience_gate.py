##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Primary relational provider object-experience gate tests."""

from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from tools.cdeadmin_relational_object_experience_gate import (
    audit, provider_catalogs,
)
from pgadmin.cdeadmin.visual_admin import LiveEvidenceError


class RelationalObjectExperienceGateTests(unittest.TestCase):

    def test_primary_relational_declarations_are_complete(self):
        result = audit()
        self.assertEqual(6, result['engine_count'])
        self.assertTrue(result['structural_complete'])
        self.assertEqual([], result['structural_failures'])
        for coverage in result['engines'].values():
            self.assertTrue(coverage['declaration_ready'])
            self.assertEqual(0, coverage['undeclared_count'])
            self.assertEqual(0, coverage['blocking_missing_count'])

    def test_live_activation_remains_fail_closed(self):
        result = audit()
        self.assertFalse(result['live_complete'])
        self.assertEqual(
            sorted(result['engines']), result['live_failures']
        )
        for coverage in result['engines'].values():
            self.assertGreater(coverage['live_evidence_missing_count'], 0)

    def test_exact_operation_evidence_activates_only_covered_engine(self):
        catalog = provider_catalogs()['sqlite']
        concepts = {}
        for family_id, declarations in catalog[
                'concept_declarations'].items():
            family = {}
            for concept_id, declaration in declarations.items():
                if not isinstance(declaration, dict) or declaration.get(
                        'status') not in {'supported', 'read_only'}:
                    continue
                family[concept_id] = {
                    'status': 'passed',
                    'operations': declaration.get(
                        'operation_obligations', {}),
                }
            if family:
                concepts[family_id] = family
        evidence = {
            'schema': 'cdeadmin.provider-object-live-evidence.v1',
            'engine_id': 'sqlite',
            'exact_profile': '3.53.0',
            'run_id': 'unit-exact-operation-evidence',
            'concepts': concepts,
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'sqlite.json'
            path.write_text(json.dumps(evidence), encoding='utf-8')
            result = audit([path])
        self.assertTrue(result['engines']['sqlite']['activation_ready'])
        self.assertNotIn('sqlite', result['live_failures'])
        self.assertFalse(result['live_complete'])
        self.assertIn('duckdb', result['live_failures'])

    def test_undeclared_operation_evidence_is_rejected(self):
        evidence = {
            'schema': 'cdeadmin.provider-object-live-evidence.v1',
            'engine_id': 'sqlite',
            'exact_profile': '3.53.0',
            'run_id': 'unit-invalid-operation-evidence',
            'concepts': {'relational': {'tables': {
                'status': 'passed',
                'operations': {'table': ['vacuum-the-universe']},
            }}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'sqlite.json'
            path.write_text(json.dumps(evidence), encoding='utf-8')
            with self.assertRaises(LiveEvidenceError):
                audit([path])

    def test_external_surface_evidence_requires_identity_and_digest(self):
        catalog = provider_catalogs()['postgresql']
        declaration = catalog['concept_declarations'][
            'relational']['tables']
        evidence = {
            'schema': 'cdeadmin.provider-object-live-evidence.v1',
            'engine_id': 'postgresql',
            'exact_profile': '18.3',
            'run_id': 'unit-missing-surface-binding',
            'concepts': {'relational': {'tables': {
                'status': 'passed',
                'operations': declaration['operation_obligations'],
            }}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'postgresql.json'
            path.write_text(json.dumps(evidence), encoding='utf-8')
            with self.assertRaisesRegex(
                    LiveEvidenceError, 'surface does not match'):
                audit([path])
            evidence['surface_id'] = declaration['external_surface']
            path.write_text(json.dumps(evidence), encoding='utf-8')
            with self.assertRaisesRegex(
                    LiveEvidenceError, 'no valid digest'):
                audit([path])


if __name__ == '__main__':
    unittest.main()
