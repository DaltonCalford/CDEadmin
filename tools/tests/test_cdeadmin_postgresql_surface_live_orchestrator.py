##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""PostgreSQL preserved-surface exact-runtime gate tests."""

import unittest

from tools.cdeadmin_postgresql_surface_live_orchestrator import (
    IMAGE, PROFILE, _object_evidence, _published_port,
)
from pgadmin.cdeadmin.providers.postgresql.preserved_surface import (
    PRESERVED_SURFACE_CONCEPTS, SURFACE_ID, audit_preserved_surface,
    concept_declarations,
)


class PostgreSQLSurfaceLiveOrchestratorTests(unittest.TestCase):

    def test_exact_reference_image_is_pinned(self):
        self.assertEqual('postgres:18.3', IMAGE)
        self.assertEqual('18.3', PROFILE)

    def test_published_port_parser_accepts_ipv4_and_ipv6(self):
        self.assertEqual(54321, _published_port('127.0.0.1:54321\n'))
        self.assertEqual(54322, _published_port('[::1]:54322\n'))
        with self.assertRaisesRegex(RuntimeError, 'usable database port'):
            _published_port('not-published\n')

    def test_every_preserved_concept_has_operations_and_assets(self):
        audit = audit_preserved_surface('.')
        self.assertTrue(audit['passed'])
        self.assertEqual([], audit['missing_asset_groups'])
        self.assertEqual(21, audit['concept_count'])
        self.assertEqual(
            set(PRESERVED_SURFACE_CONCEPTS),
            set(concept_declarations()),
        )
        for concept in audit['concepts'].values():
            self.assertEqual('passed', concept['status'])
            self.assertTrue(concept['operations'])
            self.assertTrue(all(
                group['count']
                for group in concept['asset_groups'].values()
            ))

    def test_object_evidence_binds_runtime_and_surface(self):
        surface = audit_preserved_surface('.')
        runtime = {
            'server_version': '18.3',
            'server_version_num': '180003',
            'driver': 'psycopg',
            'driver_version': '3.3.4',
        }
        evidence = _object_evidence(surface, runtime, 'test-run')
        self.assertEqual('postgresql', evidence['engine_id'])
        self.assertEqual(SURFACE_ID, evidence['surface_id'])
        self.assertEqual(
            surface['surface_sha256'], evidence['surface_sha256']
        )
        self.assertEqual(
            set(PRESERVED_SURFACE_CONCEPTS),
            set(evidence['concepts']['relational']),
        )
        self.assertFalse(evidence['credential_values_exported'])


if __name__ == '__main__':
    unittest.main()
