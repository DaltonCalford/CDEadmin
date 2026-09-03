##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Native CDE and model workspace shell tests for CDE-PREP-180."""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.cdeadmin_workspace_shell_gate import (
    CATALOG,
    MGA_INVARIANTS,
    MODEL_WORKSPACES,
    STATES,
    STORY_MANIFEST,
    WorkspaceShellError,
    evaluate,
    load_catalog,
    production_evidence_errors,
    render_story,
)


ROOT = Path(__file__).resolve().parents[2]


class WorkspaceShellTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(ROOT)

    def test_catalog_has_all_required_inert_workspaces(self):
        result = evaluate(ROOT)
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual(18, result['workspace_count'])
        self.assertEqual(10, result['control_plane_count'])
        self.assertEqual(8, result['model_count'])
        self.assertEqual(90, result['fixture_story_count'])
        self.assertEqual(0, result['enabled_action_count'])
        self.assertEqual(0, result['resource_binding_count'])
        self.assertFalse(result['production_ready'])

    def test_fixture_policy_is_persistent_and_non_operational(self):
        policy = self.catalog['fixture_policy']
        self.assertFalse(policy['production'])
        self.assertEqual('fixture_only', policy['activation'])
        self.assertFalse(policy['network_enabled'])
        self.assertFalse(policy['authentication_enabled'])
        self.assertFalse(policy['execution_enabled'])
        self.assertIsNone(policy['live_provider_evidence'])
        self.assertIn('NON-PRODUCTION', policy['persistent_label'])
        self.assertTrue(all(
            self.catalog['accessibility_policy'].values()
        ))

    def test_every_state_story_is_accessible_and_has_exact_reason(self):
        for workspace in self.catalog['workspaces']:
            for state in STATES:
                with self.subTest(
                        workspace=workspace['workspace_id'], state=state):
                    story = render_story(
                        self.catalog, workspace['workspace_id'], state
                    )
                    presentation = story['presentation']
                    self.assertIn('NON-PRODUCTION', story['persistent_label'])
                    self.assertFalse(story['production'])
                    self.assertIn(presentation['role'], {'status', 'alert'})
                    self.assertIn(
                        presentation['aria_live'], {'polite', 'assertive'}
                    )
                    self.assertEqual(
                        presentation['diagnostic_code'],
                        story['action_disposition_reason'],
                    )
                    self.assertIsNone(story['resource_binding'])
                    self.assertIsNone(story['authority_path'])
                    self.assertIsNone(story['runtime_identity'])
                    access = story['accessibility']
                    self.assertEqual('region', access['landmark_role'])
                    self.assertTrue(access['accessible_name'])
                    self.assertEqual(2, access['heading_level'])
                    self.assertEqual('status', access['keyboard_order'][1])
                    for action in story['actions']:
                        self.assertFalse(action['enabled'])
                        self.assertEqual(
                            presentation['diagnostic_code'],
                            action['disabled_reason'],
                        )

    def test_missing_capabilities_remove_or_disable_actions(self):
        removed = disabled = 0
        for workspace in self.catalog['workspaces']:
            behavior = workspace['missing_capability_behavior']
            if behavior == 'remove':
                removed += 1
                self.assertFalse(workspace['actions'])
                self.assertTrue(workspace['removed_action_ids'])
            else:
                disabled += 1
                self.assertEqual('disable', behavior)
                self.assertTrue(workspace['actions'])
                self.assertFalse(workspace['removed_action_ids'])
                self.assertTrue(all(
                    action['enabled'] is False
                    for action in workspace['actions']
                ))
        self.assertGreater(removed, 0)
        self.assertGreater(disabled, 0)

    def test_no_mutation_or_authority_path_is_exposed(self):
        self.assertEqual(
            MGA_INVARIANTS, self.catalog['authority_invariants']
        )
        authority = set(self.catalog['authoritative_search_keys'])
        for workspace in self.catalog['workspaces']:
            self.assertIsNone(workspace['resource_binding'])
            self.assertIsNone(workspace['authority_path'])
            self.assertIsNone(workspace['runtime_identity'])
            self.assertTrue(set(workspace['authority_refs']) <= authority)
            for action in workspace['actions']:
                self.assertIn(action['mutation_class'], {'none', 'read'})
                self.assertFalse(action['enabled'])
        serialized = json.dumps(self.catalog).lower()
        self.assertNotIn('/home/', serialized)
        self.assertNotIn('file://', serialized)

    def test_model_shells_have_accessible_alternative_views(self):
        workspaces = {
            item['workspace_id']: item
            for item in self.catalog['workspaces']
        }
        self.assertEqual(
            set(MODEL_WORKSPACES),
            {key for key in workspaces if key.startswith('model.')},
        )
        self.assertIn(
            'accessible_table', workspaces['model.time_series']['views']
        )
        self.assertIn(
            'accessible_feature_table',
            workspaces['model.spatial']['views'],
        )
        for workspace_id, model in MODEL_WORKSPACES.items():
            self.assertEqual(model, workspaces[workspace_id]['model_family'])
            self.assertGreaterEqual(len(workspaces[workspace_id]['views']), 4)

    def test_story_manifest_defines_the_full_matrix(self):
        manifest = json.loads(
            (ROOT / STORY_MANIFEST).read_text(encoding='utf-8')
        )
        self.assertEqual(90, manifest['story_count'])
        self.assertEqual(18, len(manifest['workspace_ids']))
        self.assertEqual(STATES, set(manifest['states']))
        self.assertFalse(manifest['production'])
        self.assertFalse(manifest['execution_enabled'])
        self.assertIn('NON-PRODUCTION', manifest['persistent_label'])

    def test_fixture_never_becomes_production_even_with_shaped_evidence(self):
        workspace = self.catalog['workspaces'][0]
        self.assertTrue(production_evidence_errors(workspace, None))
        evidence = {
            'workspace_id': workspace['workspace_id'],
            'capability_id': workspace['required_capability'],
            'provider_id': 'org.scratchbird.cde',
            'provider_mode': 'scratchbird_native',
            'capability_state': 'implemented',
            'driver_handoff_state': 'qualified',
            'runtime_identity': {
                'engine_id': 'scratchbird_cde',
                'profile_version': 'future-qualified-profile',
                'artifact_digest': 'a' * 64,
            },
            'evidence_digest': 'b' * 64,
        }
        self.assertEqual(
            [], production_evidence_errors(workspace, evidence)
        )
        self.assertFalse(self.catalog['fixture_policy']['production'])
        self.assertEqual('shell_only', workspace['delivery_state'])
        self.assertEqual('unavailable', workspace['current_state'])

    def test_bad_production_evidence_reports_exact_requirements(self):
        workspace = self.catalog['workspaces'][0]
        errors = production_evidence_errors(workspace, {
            'workspace_id': 'wrong',
            'runtime_identity': {'artifact_digest': 'bad'},
            'evidence_digest': 'bad',
        })
        self.assertGreaterEqual(len(errors), 8)
        self.assertTrue(any('workspace_id' in item for item in errors))
        self.assertTrue(any('runtime identity' in item for item in errors))
        self.assertTrue(any('artifact digest' in item for item in errors))

    def test_unknown_story_inputs_fail_closed(self):
        with self.assertRaisesRegex(WorkspaceShellError, 'unknown workspace'):
            render_story(self.catalog, 'model.missing', 'unavailable')
        with self.assertRaisesRegex(WorkspaceShellError, 'unknown workspace'):
            render_story(self.catalog, 'model.document', 'invented')

    def test_malformed_catalog_exercises_fail_closed_gate(self):
        catalog = copy.deepcopy(self.catalog)
        catalog['schema'] = 'wrong'
        catalog['fixture_policy'].update({
            'production': True,
            'activation': 'production',
            'network_enabled': True,
            'persistent_label': '',
            'production_evidence_requirements': [],
        })
        catalog['accessibility_policy']['reduced_motion_safe'] = False
        catalog['authority_invariants']['transaction_authority'] = 'driver'
        catalog['authoritative_search_keys'].append(
            catalog['authoritative_search_keys'][0]
        )
        workspace = catalog['workspaces'][0]
        workspace.update({
            'delivery_state': 'implemented',
            'current_state': 'ready',
            'supported_states': [],
            'resource_binding': {'invented': True},
            'authority_path': '/home/invented',
            'runtime_identity': {'invented': True},
            'required_capability': 'invalid',
            'authority_refs': ['UNKNOWN'],
            'views': [],
        })
        workspace['actions'][0].update({
            'enabled': True, 'mutation_class': 'admin',
            'disabled_reason': 'wrong', 'label': '',
        })
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            catalog_path = source / CATALOG
            story_path = source / STORY_MANIFEST
            catalog_path.parent.mkdir(parents=True)
            story_path.parent.mkdir(parents=True)
            catalog_path.write_text(json.dumps(catalog), encoding='utf-8')
            shutil.copy2(ROOT / STORY_MANIFEST, story_path)
            result = evaluate(source)
        self.assertFalse(result['valid'])
        self.assertGreaterEqual(len(result['errors']), 15)

    def test_missing_or_non_object_inputs_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            self.assertFalse(evaluate(source)['valid'])
            catalog_path = source / CATALOG
            catalog_path.parent.mkdir(parents=True)
            catalog_path.write_text('[]', encoding='utf-8')
            with self.assertRaisesRegex(
                    WorkspaceShellError, 'must be an object'):
                load_catalog(source)


if __name__ == '__main__':
    unittest.main()
