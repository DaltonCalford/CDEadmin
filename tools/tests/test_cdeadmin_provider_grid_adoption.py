##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Exhaustive activation gate for built-in provider grid workspaces."""

from __future__ import annotations

import importlib
import json
import sys
import unittest
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    pgadmin_package = ModuleType('pgadmin')
    pgadmin_package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = pgadmin_package

from pgadmin.cdeadmin.grid_contract import (  # noqa: E402
    GRID_RESULT_SCHEMA,
    GRID_WORKSPACE_SCHEMA,
    normalize_admin_page,
    normalize_columns,
    result_grid_contract,
    workspace_grid_contract,
)
from pgadmin.cdeadmin.providers import BUILTIN_PACKAGES  # noqa: E402
from pgadmin.cdeadmin.visual_admin.catalog import (  # noqa: E402
    catalog_for_engine,
)


class ProviderGridAdoptionTests(unittest.TestCase):
    """Prevent a provider package from bypassing grid/native-view gates."""

    def test_every_builtin_provider_passes_workspace_adoption_gates(self):
        self.assertEqual(26, len(BUILTIN_PACKAGES))
        self.assertFalse(any(
            'scratchbird' in manifest_path.lower()
            for manifest_path, _module_name in BUILTIN_PACKAGES
        ))
        provider_root = WEB / 'pgadmin/cdeadmin/providers'
        observed = set()
        for manifest_path, module_name in BUILTIN_PACKAGES:
            manifest = json.loads(
                (provider_root / manifest_path).read_text(encoding='utf-8')
            )
            identity = manifest['identity']
            profile = self._profile(module_name, identity['profile_id'])
            context = self._context(identity, manifest)
            catalog = catalog_for_engine(
                getattr(profile, 'engine_id', 'postgresql')
            )
            contract = workspace_grid_contract(
                context, SimpleNamespace(profile=profile), catalog,
            )
            label = identity['profile_id']
            observed.add(label)
            self.assertEqual(GRID_WORKSPACE_SCHEMA, contract['schema'], label)
            self.assertEqual('passed', contract['adoption_state'], label)
            self.assertEqual(
                'passed', contract['runtime_gate']['state'], label
            )
            self.assertTrue(contract['grid_id_prefix'], label)
            self.assertTrue(all(
                gate['state'] == 'passed'
                for gate in contract['activation_gates']
            ), label)
            if contract['native_view_required']:
                self.assertTrue(contract['native_view_available'], label)
                self.assertNotEqual(
                    'SchemaView/DataGridView',
                    contract['component_reference'], label,
                )
        self.assertEqual(len(BUILTIN_PACKAGES), len(observed))

    def test_result_columns_are_stable_typed_and_export_safe(self):
        columns = normalize_columns([
            {'name': 'id', 'type': 'BIGINT'},
            {'name': 'id', 'type': 'UUID'},
            {'name': 'api_token', 'type': 'VARCHAR'},
            {'name': 'payload', 'type': 'JSONB'},
            {'name': 'nullable_count', 'type': 'Nullable(Int64)'},
            {'name': 'embedding', 'type': 'FloatVector'},
        ], redact_keys={'api_token'})
        self.assertEqual(
            ['id', 'id#2', 'api_token', 'payload', 'nullable_count',
             'embedding'],
            [column['key'] for column in columns],
        )
        self.assertEqual('number', columns[0]['cell_type'])
        self.assertEqual('json', columns[3]['cell_type'])
        self.assertEqual('number', columns[4]['cell_type'])
        self.assertEqual('json', columns[5]['cell_type'])
        self.assertFalse(columns[2]['exportable'])
        self.assertTrue(all(column['read_only'] for column in columns))

    def test_result_and_admin_pages_declare_interaction_authority(self):
        identity = {
            'provider_id': 'org.cdeadmin.test', 'profile_id': 'test-native',
        }
        result = {
            'identity': identity, 'result_kind': 'tabular',
            'complete': False, 'continuation': 'provider-cursor',
        }
        contract = result_grid_contract(
            result, 'SchemaView/DataGridView', [{'name': 'value'}],
            [{'value': 1}],
        )
        self.assertEqual(GRID_RESULT_SCHEMA, contract['schema'])
        self.assertEqual('provider', contract['interactions']['page'])
        self.assertTrue(contract['interactions']['cancellation'])

        context = SimpleNamespace(
            provider_id='org.cdeadmin.test', profile_id='test-native',
            experience_family='relational',
        )
        page = normalize_admin_page({
            'columns': [{'name': 'value', 'type': 'INTEGER'}],
            'rows': [{'values': {'value': 1}, 'identity_token': 'opaque'}],
            'editable': True, 'continuation': None,
        }, context, SimpleNamespace(client=SimpleNamespace(
            transaction_actions=('commit', 'rollback'),
        )), 'table')
        self.assertEqual(GRID_RESULT_SCHEMA, page['grid']['schema'])
        self.assertEqual('client', page['grid']['interactions']['page'])
        self.assertEqual(
            'provider', page['grid']['mutation_lifecycle']['commit']
        )
        self.assertTrue(
            page['grid']['mutation_lifecycle']['safe_delete_confirmation']
        )

    @staticmethod
    def _profile(module_name, profile_id):
        if profile_id == 'postgresql-native':
            return SimpleNamespace(
                model_family='postgresql', result_kind='tabular',
                result_component_reference='SchemaView/DataGridView',
            )
        module = importlib.import_module(module_name)
        profiles = {
            value.profile_id: value for value in vars(module).values()
            if value.__class__.__name__ == 'PilotProfile'
        }
        return profiles[profile_id]

    @staticmethod
    def _context(identity, manifest):
        family = manifest['composition']['experience_families'][0]
        return SimpleNamespace(
            endpoint_id=str(uuid.uuid4()),
            provider_id=identity['provider_id'],
            profile_id=identity['profile_id'],
            experience_family=family,
            runtime_verification_state='verified',
        )


if __name__ == '__main__':
    unittest.main()
