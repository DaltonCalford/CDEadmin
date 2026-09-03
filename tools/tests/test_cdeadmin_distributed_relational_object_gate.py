##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Distributed/hybrid relational object-experience gate tests."""

from __future__ import annotations

import unittest

from tools.cdeadmin_distributed_relational_object_experience_gate import (
    ADMINISTRATIONS,
    audit,
)


class DistributedRelationalObjectGateTests(unittest.TestCase):

    def test_gate_covers_every_relational_distributed_provider(self):
        result = audit()
        self.assertEqual(set(ADMINISTRATIONS), set(result['engines']))
        self.assertEqual(6, result['engine_count'])

    def test_cockroach_declaration_is_complete_but_live_fail_closed(self):
        result = audit()
        coverage = result['engines']['cockroachdb']
        self.assertTrue(coverage['declaration_ready'])
        self.assertEqual(0, coverage['undeclared_count'])
        self.assertEqual(0, coverage['blocking_missing_count'])
        self.assertFalse(coverage['activation_ready'])
        self.assertIn('cockroachdb', result['live_failures'])

    def test_dolt_declaration_is_exact_and_complete(self):
        result = audit()
        coverage = result['engines']['dolt']
        self.assertTrue(coverage['declaration_ready'])
        self.assertEqual(0, coverage['undeclared_count'])
        self.assertEqual(0, coverage['blocking_missing_count'])
        concepts = {
            concept['concept_id']: concept
            for family in coverage['families']
            for concept in family['concepts']
        }
        self.assertEqual('supported', concepts['triggers']['declared_status'])
        self.assertEqual(
            'supported', concepts['jobs_and_events']['declared_status']
        )
        for concept_id in (
            'materialized_views', 'domains', 'types', 'sequences',
            'functions', 'extensions_and_plugins', 'partitions',
            'tablespaces_and_filespaces',
        ):
            self.assertEqual(
                'not_applicable', concepts[concept_id]['declared_status']
            )


if __name__ == '__main__':
    unittest.main()
