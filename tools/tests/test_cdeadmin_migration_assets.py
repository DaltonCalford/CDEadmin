##########################################################################
# CDEadmin Migration Planning project-asset backend validation gates.
##########################################################################

from __future__ import annotations

import copy
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


SOURCE = {
    'schema': 'cdeadmin.resource-ref.v1',
    'canonical': 'firebird://source/operations',
    'provider': 'firebird',
}
TARGET = {
    'schema': 'cdeadmin.resource-ref.v1',
    'canonical': 'postgresql://target/operations',
    'provider': 'postgresql',
}


def runbook_step(identity, action, dependencies=None):
    return {
        'schema': 'cdeadmin.migration-runbook-step.v1',
        'id': identity,
        'label': action,
        'action': action,
        'dependencies': dependencies or [],
        'preconditions': [],
        'expectedEvidence': {'complete': True},
        'providerMutation': True,
        'nativeDetails': {},
    }


def migration_content():
    return {
        'schema': 'cdeadmin.migration.asset.v1',
        'schemaVersion': 1,
        'moduleId': 'cdeadmin.migration',
        'name': 'Operations migration',
        'description': 'Firebird to PostgreSQL',
        'sourceBinding': copy.deepcopy(SOURCE),
        'targetBinding': copy.deepcopy(TARGET),
        'strategy': 'offline',
        'phase': 'design',
        'assessment': {
            'schema': 'cdeadmin.migration-assessment.v1',
            'id': 'assessment-one',
            'sourceRevision': 'source-one',
            'targetRevision': 'target-one',
            'findings': [{
                'schema': 'cdeadmin.migration-finding.v1',
                'id': 'orders',
                'sourceRef': copy.deepcopy(SOURCE),
                'targetRef': copy.deepcopy(TARGET),
                'objectKind': 'table',
                'category': 'supported_exact',
                'evidenceSource': 'provider_adapter',
                'evidence': {'rule': 'table'},
                'message': 'Exact table mapping',
                'blocker': False,
                'waived': False,
                'waiverRef': None,
                'nativeDetails': {'sourceType': 'INTEGER'},
            }],
            'estimates': {'rows': 2},
            'evidence': {'observed': True},
            'completedAt': '2026-09-12T00:00:00Z',
            'nativeDetails': {},
        },
        'mappingSet': [{
            'schema': 'cdeadmin.migration-mapping.v1',
            'id': 'orders-map',
            'sourceRef': copy.deepcopy(SOURCE),
            'targetRef': copy.deepcopy(TARGET),
            'mappingKind': 'type',
            'category': 'supported_exact',
            'decision': 'accepted',
            'sourceNativeType': 'INTEGER',
            'targetNativeType': 'integer',
            'expression': None,
            'lossy': False,
            'lossAcknowledged': False,
            'behaviorChange': False,
            'evidence': {'rule': 'integer'},
            'nativeDetails': {},
        }],
        'schemaPlan': {
            'schema': 'cdeadmin.migration-schema-plan.v1',
            'id': 'schema-one',
            'operations': [{
                'schema': 'cdeadmin.migration-schema-operation.v1',
                'id': 'create-orders',
                'action': 'create',
                'sourceRef': copy.deepcopy(SOURCE),
                'targetRef': copy.deepcopy(TARGET),
                'dependencies': [],
                'preconditions': [{'kind': 'absent'}],
                'rollback': [{'action': 'drop'}],
                'risk': 'low',
                'nativeDefinition': {'statement': 'CREATE TABLE orders'},
            }],
            'schemaCompareRef': {
                'schema': 'cdeadmin.asset-ref.v1',
                'projectId': 'project-one',
                'assetId': 'comparison-one',
            },
            'expectedTargetRevision': 'target-one',
            'nativeDetails': {},
        },
        'dataMovePlan': {
            'schema': 'cdeadmin.migration-data-move-plan.v1',
            'id': 'move-one',
            'units': [{
                'schema': 'cdeadmin.migration-copy-unit.v1',
                'id': 'orders-copy',
                'sourceRef': copy.deepcopy(SOURCE),
                'targetRef': copy.deepcopy(TARGET),
                'partition': {'range': 'all'},
                'orderingKey': ['id'],
                'batchSize': 1000,
                'parallelism': 1,
                'restartPolicy': 'resume_committed',
                'transformRef': None,
                'nativeDetails': {},
            }],
            'concurrency': 1,
            'consistencyBoundary': {'snapshot': 'source-one'},
            'errorPolicy': {'stop': True},
            'nativeDetails': {},
        },
        'cdcPlanRef': None,
        'validationPlan': [{
            'schema': 'cdeadmin.migration-verification.v1',
            'id': 'orders-count',
            'type': 'row_document_counts',
            'sourceRef': copy.deepcopy(SOURCE),
            'targetRef': copy.deepcopy(TARGET),
            'canonicalization': None,
            'policy': {'equal': True},
            'blocking': True,
            'qualityAssetRef': None,
            'nativeDetails': {},
        }],
        'cutoverPlan': {
            'schema': 'cdeadmin.migration-cutover-plan.v1',
            'id': 'cutover-one',
            'steps': [runbook_step('freeze', 'Freeze writes'),
                      runbook_step('endpoint-switch', 'Switch endpoint',
                                   ['freeze'])],
            'cdcLagThreshold': None,
            'armWindowMinutes': 30,
            'targetEnvironment': 'production',
            'targetConnection': 'target-one',
            'endpointChange': {'route': 'target'},
            'postChecks': [{'check': 'readable'}],
            'nativeDetails': {},
        },
        'rollbackPlan': {
            'schema': 'cdeadmin.migration-rollback-plan.v1',
            'id': 'rollback-one',
            'classification': 'fully_automatable',
            'pointOfNoReturn': 'endpoint-switch',
            'deadline': None,
            'conditions': [{'when': 'verification_failed'}],
            'steps': [runbook_step('unfreeze', 'Unfreeze writes')],
            'recoverability': {'source': True},
            'nativeDetails': {},
        },
        'waivers': [],
        'extensions': {},
    }


def migration_asset():
    return {
        'asset_type': 'cdeadmin.migration.v1',
        'schema_name': 'cdeadmin.migration.v1',
        'schema_version': 1,
        'name': 'Operations migration',
        'path': 'migration/operations.json',
        'expected_version': 0,
        'content': migration_content(),
        'metadata': {'moduleId': 'cdeadmin.migration'},
        'dependency_references': [],
        'resource_bindings': [],
        'validation_state': 'valid',
        'validation_details': [],
    }


class MigrationAssetValidationTest(unittest.TestCase):
    def validate(self, value):
        return validate_asset_request(
            value, 'project-one', 'migration-one')

    def test_accepts_complete_canonical_asset(self):
        result = self.validate(migration_asset())
        content = json.loads(result['content'])
        self.assertEqual(content['moduleId'], 'cdeadmin.migration')
        self.assertEqual(content['strategy'], 'offline')

    def test_rejects_wrong_schema_module_phase_and_strategy(self):
        for field, value, message in (
                ('schema', 'wrong', 'asset schema'),
                ('moduleId', 'wrong', 'version or module'),
                ('phase', 'guessed', 'phase'),
                ('strategy', 'guessed', 'strategy')):
            request = migration_asset()
            request['content'][field] = value
            with self.assertRaisesRegex(ProjectAssetError, message):
                self.validate(request)

    def test_requires_source_and_target_after_discovery(self):
        request = migration_asset()
        request['content']['sourceBinding'] = None
        with self.assertRaisesRegex(
                ProjectAssetError, 'require source and target'):
            self.validate(request)

    def test_requires_cdc_reference_for_online_strategy(self):
        request = migration_asset()
        request['content']['strategy'] = 'online_with_cdc'
        with self.assertRaisesRegex(ProjectAssetError, 'CDC plan'):
            self.validate(request)

    def test_rejects_unknown_assessment_semantics(self):
        for field, value, message in (
                ('category', 'guessed', 'category'),
                ('evidenceSource', 'guessed', 'evidence source')):
            request = migration_asset()
            request['content']['assessment']['findings'][0][field] = value
            with self.assertRaisesRegex(ProjectAssetError, message):
                self.validate(request)

    def test_rejects_dangling_schema_dependencies(self):
        request = migration_asset()
        request['content']['schemaPlan']['operations'][0][
            'dependencies'] = ['missing']
        with self.assertRaisesRegex(ProjectAssetError, 'unavailable'):
            self.validate(request)

    def test_requires_hash_canonicalization(self):
        request = migration_asset()
        request['content']['validationPlan'][0][
            'type'] = 'deterministic_hashes'
        with self.assertRaisesRegex(ProjectAssetError, 'canonicalization'):
            self.validate(request)

    def test_rejects_invalid_cutover_and_rollback_policy(self):
        request = migration_asset()
        request['content']['cutoverPlan']['armWindowMinutes'] = 0
        with self.assertRaisesRegex(ProjectAssetError, 'arm window'):
            self.validate(request)

    def test_rejects_runbook_dependency_errors_and_unknown_point(self):
        request = migration_asset()
        request['content']['cutoverPlan']['steps'][0][
            'dependencies'] = ['missing']
        with self.assertRaisesRegex(ProjectAssetError, 'unavailable'):
            self.validate(request)
        request = migration_asset()
        request['content']['cutoverPlan']['steps'][0][
            'dependencies'] = ['endpoint-switch']
        with self.assertRaisesRegex(ProjectAssetError, 'dependency cycle'):
            self.validate(request)
        request = migration_asset()
        request['content']['rollbackPlan']['pointOfNoReturn'] = 'missing'
        with self.assertRaisesRegex(ProjectAssetError, 'point of no return'):
            self.validate(request)
        request = migration_asset()
        request['content']['cutoverPlan']['steps'] = []
        request['content']['rollbackPlan']['pointOfNoReturn'] = None
        with self.assertRaisesRegex(ProjectAssetError, 'at least one step'):
            self.validate(request)
        request = migration_asset()
        request['content']['rollbackPlan']['steps'] = []
        with self.assertRaisesRegex(ProjectAssetError, 'at least one step'):
            self.validate(request)
        request = migration_asset()
        request['content']['rollbackPlan']['deadline'] = 'sometime'
        with self.assertRaisesRegex(ProjectAssetError, 'ISO date-time'):
            self.validate(request)
        request = migration_asset()
        request['content']['rollbackPlan']['classification'] = 'guessed'
        with self.assertRaisesRegex(ProjectAssetError, 'classification'):
            self.validate(request)

    def test_rejects_duplicate_stable_ids(self):
        request = migration_asset()
        request['content']['mappingSet'].append(copy.deepcopy(
            request['content']['mappingSet'][0]))
        with self.assertRaisesRegex(ProjectAssetError, 'IDs must be unique'):
            self.validate(request)

    def test_rejects_unknown_content_instead_of_silently_dropping_it(self):
        request = migration_asset()
        request['content']['futureField'] = {'unsupported': True}
        with self.assertRaisesRegex(ProjectAssetError, 'unsupported field'):
            self.validate(request)
        request = migration_asset()
        request['content']['cutoverPlan']['steps'][0]['guessed'] = True
        with self.assertRaisesRegex(ProjectAssetError, 'unsupported field'):
            self.validate(request)

    def test_rejects_raw_credentials_anywhere(self):
        request = migration_asset()
        request['content']['extensions']['accessToken'] = 'forbidden'
        with self.assertRaisesRegex(ProjectAssetError, 'secret field'):
            self.validate(request)


if __name__ == '__main__':
    unittest.main()
