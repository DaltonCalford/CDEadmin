##########################################################################
# CDEadmin governed AI Assistant project-asset validation gates.
##########################################################################

from __future__ import annotations

import copy
import importlib.util
import json
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

from pgadmin.cdeadmin.workspace.assets import (  # noqa: E402
    ProjectAssetError,
    validate_asset_request,
)

PERMISSION_PATH = WEB / 'pgadmin' / 'tools' / 'user_management' / (
    'PgAdminPermissions.py')
PERMISSION_SPEC = importlib.util.spec_from_file_location(
    'cdeadmin_ai_permissions', PERMISSION_PATH)
PERMISSION_MODULE = importlib.util.module_from_spec(PERMISSION_SPEC)
PERMISSION_SPEC.loader.exec_module(PERMISSION_MODULE)
PERMISSION_MODULE.gettext = lambda value: value
AllPermissionTypes = PERMISSION_MODULE.AllPermissionTypes
PgAdminPermissions = PERMISSION_MODULE.PgAdminPermissions

RESOURCE = {
    'schema': 'cdeadmin.resource-ref.v1',
    'canonical': 'firebird://localhost/cdeadmin_demo.fdb',
    'providerId': 'firebird',
}


def asset_ref(identity):
    return {'schema': 'cdeadmin.asset-ref.v1',
            'projectId': 'project-one', 'assetId': identity}


def action():
    return {
        'schema': 'cdeadmin.ai-proposed-action.v1', 'id': 'action-one',
        'type': 'proposed_command', 'commandId': 'demo.update',
        'taskType': None, 'assetChangeRef': None,
        'targetRef': copy.deepcopy(RESOURCE),
        'arguments': {'table': 'ASSETS'}, 'rationale': 'Reviewed change',
        'effects': ['updates metadata'], 'permissions': ['db.write'],
        'rollbackNote': 'Restore prior metadata', 'dependencies': [],
        'validation': {'valid': True},
        'evidenceRefs': [copy.deepcopy(RESOURCE)],
        'diff': {'after': 'draft'}, 'extensions': [],
    }


def content():
    return {
        'schema': 'cdeadmin.ai.asset.v1', 'schemaVersion': 1,
        'moduleId': 'cdeadmin.ai', 'name': 'Database assistant',
        'description': 'Governed database help',
        'sessionPolicy': {
            'id': 'database-policy',
            'allowedModes': ['ask', 'explain', 'plan'],
            'defaultMode': 'ask',
            'allowedReadToolIds': ['metadata.describe'],
            'allowedProposalCommandIds': ['demo.update'],
            'maximumPlanSteps': 10, 'requireEvidence': True,
            'nativeDetails': {},
        },
        'savedContextRefs': [{
            'schema': 'cdeadmin.ai-context-scope.v1', 'id': 'database',
            'name': 'Demo database', 'reference': copy.deepcopy(RESOURCE),
            'type': 'database_metadata', 'environment': 'development',
            'sensitivity': 'internal', 'exposure': 'metadata_only',
            'timeRange': {}, 'description': 'Explicit context',
            'nativeDetails': {},
        }],
        'savedPlans': [{
            'schema': 'cdeadmin.ai-action-plan.v1', 'id': 'plan-one',
            'name': 'Reviewed update', 'revision': 1,
            'targetRevision': '42', 'contextScopeIds': ['database'],
            'actions': [action()], 'validation': {'valid': True},
            'rollbackNotes': 'Restore prior metadata',
            'evidenceRefs': [copy.deepcopy(RESOURCE)],
            'description': 'Reviewed plan', 'extensions': [],
        }],
        'modelProfileRef': asset_ref('model-profile-one'),
        'conversationPersistencePolicy': {
            'persistMessages': False, 'storePrompts': False,
            'storeResponses': False, 'retentionDays': None,
            'nativeDetails': {},
        },
        'extensions': [],
    }


def request():
    return {
        'asset_type': 'cdeadmin.ai.v1',
        'schema_name': 'cdeadmin.ai.v1', 'schema_version': 1,
        'name': 'Database assistant', 'path': 'ai/assistant.json',
        'expected_version': 0, 'content': content(),
        'metadata': {'moduleId': 'cdeadmin.ai'},
        'dependency_references': [asset_ref('model-profile-one')],
        'resource_bindings': [RESOURCE], 'validation_state': 'valid',
        'validation_details': [],
    }


class AIAssetValidationTest(unittest.TestCase):
    def validate(self, value):
        return validate_asset_request(value, 'project-one', 'ai-one')

    def test_accepts_complete_canonical_asset(self):
        stored = json.loads(self.validate(request())['content'])
        self.assertEqual(stored['savedPlans'][0]['actions'][0]['commandId'],
                         'demo.update')

    def test_rejects_wrong_identity_and_unknown_fields(self):
        for field, value, message in (
                ('schema', 'wrong', 'asset schema'),
                ('schemaVersion', 2, 'version or module'),
                ('moduleId', 'wrong', 'version or module'),
                ('guessed', True, 'unsupported field')):
            item = request()
            item['content'][field] = value
            with self.assertRaisesRegex(ProjectAssetError, message):
                self.validate(item)

    def test_rejects_implicit_context_and_unknown_plan_context(self):
        item = request()
        item['content']['savedContextRefs'][0]['exposure'] = 'implicit'
        with self.assertRaisesRegex(ProjectAssetError, 'exposure'):
            self.validate(item)
        item = request()
        item['content']['savedPlans'][0]['contextScopeIds'] = ['unknown']
        with self.assertRaisesRegex(ProjectAssetError, 'unknown context'):
            self.validate(item)

    def test_rejects_action_shape_dependency_and_validation_guessing(self):
        item = request()
        item['content']['savedPlans'][0]['actions'][0]['guessed'] = True
        with self.assertRaisesRegex(ProjectAssetError, 'unsupported field'):
            self.validate(item)
        item = request()
        item['content']['savedPlans'][0]['actions'][0][
            'dependencies'] = ['missing']
        with self.assertRaisesRegex(ProjectAssetError, 'unknown dependency'):
            self.validate(item)
        item = request()
        item['content']['savedPlans'][0]['actions'][0][
            'validation'] = {'state': 'probably'}
        with self.assertRaisesRegex(ProjectAssetError, 'boolean valid'):
            self.validate(item)

    def test_rejects_invalid_modes_plan_limits_and_persistence(self):
        item = request()
        item['content']['sessionPolicy']['allowedModes'] = ['invent']
        with self.assertRaisesRegex(ProjectAssetError, 'modes'):
            self.validate(item)
        item = request()
        item['content']['sessionPolicy']['maximumPlanSteps'] = 0
        with self.assertRaisesRegex(ProjectAssetError, 'maximum plan'):
            self.validate(item)
        item = request()
        item['content']['conversationPersistencePolicy'][
            'persistMessages'] = True
        with self.assertRaisesRegex(ProjectAssetError, 'exclude'):
            self.validate(item)

    def test_rejects_raw_secrets_recursively(self):
        item = request()
        item['content']['savedPlans'][0]['actions'][0][
            'arguments']['accessToken'] = 'raw'
        with self.assertRaisesRegex(ProjectAssetError, 'secret'):
            self.validate(item)

    def test_registers_all_independent_ai_permissions(self):
        names = {item['name'] for item in PgAdminPermissions().all_permissions}
        self.assertTrue({
            AllPermissionTypes.ai_use,
            AllPermissionTypes.ai_use_sensitive_metadata,
            AllPermissionTypes.ai_use_sensitive_data,
            AllPermissionTypes.ai_propose,
            AllPermissionTypes.ai_execute_approved,
            AllPermissionTypes.ai_admin,
        }.issubset(names))


if __name__ == '__main__':
    unittest.main()
