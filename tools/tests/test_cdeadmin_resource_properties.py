##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Tests for truthful common provider Properties sections."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.resources.properties import (  # noqa: E402
    ResourcePropertiesError,
    normalize_resource_properties,
)


class ResourcePropertiesTests(unittest.TestCase):

    def test_infers_only_sections_with_provider_evidence(self):
        source = {
            'definition': {'name': 'orders'},
            'dependencies': [],
            'stats': {'rows': 4},
        }
        result = normalize_resource_properties(source)
        self.assertEqual([
            'properties', 'definition', 'dependencies', 'statistics',
            'operations',
        ], result['property_sections'])
        self.assertNotIn('privileges', result['property_sections'])
        self.assertEqual(source['definition'], result['definition'])

    def test_explicit_empty_supported_section_is_preserved(self):
        result = normalize_resource_properties({
            'property_sections': [
                'properties', 'dependencies', 'dependents', 'operations',
            ],
            'dependencies': [],
            'dependents': [],
        })
        self.assertEqual([
            'properties', 'dependencies', 'dependents', 'operations',
        ], result['property_sections'])

    def test_rejects_invented_or_ambiguous_declarations(self):
        invalid = (
            {'property_sections': 'properties'},
            {'property_sections': ['properties', 'not-real', 'operations']},
            {'property_sections': ['properties', 'operations', 'operations']},
            {'property_sections': ['properties']},
        )
        for native in invalid:
            with self.subTest(native=native):
                with self.assertRaises(ResourcePropertiesError):
                    normalize_resource_properties(native)

    def test_input_is_not_mutated(self):
        source = {'columns': [{'name': 'id'}]}
        result = normalize_resource_properties(source)
        result['columns'][0]['name'] = 'changed'
        self.assertEqual('id', source['columns'][0]['name'])


if __name__ == '__main__':
    unittest.main()
