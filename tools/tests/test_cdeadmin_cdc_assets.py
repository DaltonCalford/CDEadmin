##########################################################################
# CDEadmin CDC project-asset backend validation gates.
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


SOURCE_REF = {
    'schema': 'cdeadmin.resource-ref.v1',
    'canonical': 'mongodb://cluster/orders',
    'provider': 'mongodb',
}
SINK_REF = {
    'schema': 'cdeadmin.resource-ref.v1',
    'canonical': 'clickhouse://warehouse/orders',
    'provider': 'clickhouse',
}


def cdc_asset():
    content = {
        'schema': 'cdeadmin.cdc.asset.v1',
        'schemaVersion': 1,
        'moduleId': 'cdeadmin.cdc',
        'name': 'Orders CDC',
        'description': 'Order changes',
        'source': {
            'schema': 'cdeadmin.cdc-capture-source.v1',
            'resourceRef': copy.deepcopy(SOURCE_REF),
            'captureMechanism': 'oplog/change_stream',
            'nativeMechanism': 'MongoDB change streams',
            'requiredPrivileges': ['changeStream'],
            'credentialRef': {
                'schema': 'cdeadmin.credential-ref.v1',
                'scheme': 'vault',
                'id': 'source-credential',
            },
            'startPosition': {
                'schema': 'cdeadmin.cdc-start-position.v1',
                'kind': 'latest',
                'value': None,
                'nativeDetails': {},
            },
            'snapshotPolicy': {
                'schema': 'cdeadmin.cdc-snapshot-policy.v1',
                'mode': 'initial',
                'consistency': 'majority',
                'batchSize': None,
                'nativeDetails': {},
            },
            'nativeDetails': {},
        },
        'filters': [],
        'transforms': [{
            'schema': 'cdeadmin.cdc-event-transform.v1',
            'id': 'redact-email',
            'name': 'Redact email',
            'kind': 'redaction',
            'enabled': True,
            'rules': {'path': 'after.email'},
            'schemaMapping': {},
            'nativeDetails': {},
        }],
        'sink': {
            'schema': 'cdeadmin.cdc-sink-binding.v1',
            'resourceRef': copy.deepcopy(SINK_REF),
            'credentialRef': None,
            'serialization': 'JSONEachRow',
            'nativeDetails': {},
        },
        'deliveryPolicy': {
            'schema': 'cdeadmin.cdc-delivery-policy.v1',
            'guarantee': 'at_least_once',
            'deduplication': {'key': 'eventId'},
            'proof': {},
            'nativeDetails': {},
        },
        'schemaEvolutionPolicy': {
            'schema': 'cdeadmin.cdc-evolution-policy.v1',
            'defaultAction': 'pause_and_review',
            'changes': [],
            'nativeDetails': {},
        },
        'alerts': [],
        'visualLayout': {},
        'extensions': {},
    }
    return {
        'asset_type': 'cdeadmin.cdc.v1',
        'schema_name': 'cdeadmin.cdc.v1',
        'schema_version': 1,
        'name': 'Orders CDC',
        'path': 'cdc/orders.json',
        'expected_version': 0,
        'content': content,
        'metadata': {'moduleId': 'cdeadmin.cdc'},
        'dependency_references': [],
        'resource_bindings': [
            copy.deepcopy(SOURCE_REF), copy.deepcopy(SINK_REF)
        ],
        'validation_state': 'valid',
        'validation_details': [],
    }


class CDCAssetValidationTest(unittest.TestCase):
    def test_accepts_complete_canonical_asset(self):
        validated = validate_asset_request(
            cdc_asset(), 'project-one', 'cdc-one'
        )
        self.assertEqual(
            json.loads(validated['content'])['moduleId'], 'cdeadmin.cdc'
        )

    def test_rejects_wrong_schema_and_reference_family(self):
        wrong = cdc_asset()
        wrong['content']['schema'] = 'cdeadmin.etl.asset.v1'
        with self.assertRaisesRegex(ProjectAssetError, 'CDC asset schema'):
            validate_asset_request(wrong, 'project-one', 'cdc-one')
        wrong_ref = cdc_asset()
        wrong_ref['content']['source']['resourceRef'] = {
            'schema': 'cdeadmin.asset-ref.v1',
            'projectId': 'p',
            'assetId': 'a',
        }
        with self.assertRaisesRegex(ProjectAssetError, 'ResourceRef'):
            validate_asset_request(wrong_ref, 'project-one', 'cdc-one')

    def test_rejects_unrecognized_mechanism_and_start_position(self):
        mechanism = cdc_asset()
        mechanism['content']['source']['captureMechanism'] = 'wal'
        with self.assertRaisesRegex(ProjectAssetError, 'capture mechanism'):
            validate_asset_request(mechanism, 'project-one', 'cdc-one')
        position = cdc_asset()
        position['content']['source']['startPosition']['kind'] = 'bookmark'
        with self.assertRaisesRegex(ProjectAssetError, 'start-position kind'):
            validate_asset_request(position, 'project-one', 'cdc-one')

    def test_requires_incremental_snapshot_batch_size(self):
        value = cdc_asset()
        value['content']['source']['snapshotPolicy']['mode'] = 'incremental'
        with self.assertRaisesRegex(ProjectAssetError, 'requires batch size'):
            validate_asset_request(value, 'project-one', 'cdc-one')

    def test_requires_end_to_end_exactly_once_proof(self):
        value = cdc_asset()
        value['content']['deliveryPolicy']['guarantee'] = (
            'exactly_once_proven'
        )
        with self.assertRaisesRegex(ProjectAssetError, 'end-to-end proof'):
            validate_asset_request(value, 'project-one', 'cdc-one')
        value['content']['deliveryPolicy']['proof'] = {
            'sourceCapture': 'e1',
            'transport': 'e2',
            'sinkApplication': 'e3',
        }
        validate_asset_request(value, 'project-one', 'cdc-one')

    def test_rejects_breaking_or_unknown_auto_apply(self):
        for classification in ('breaking', 'unknown'):
            value = cdc_asset()
            value['content']['schemaEvolutionPolicy']['changes'] = [{
                'schema': 'cdeadmin.cdc-schema-change.v1',
                'id': classification,
                'classification': classification,
                'action': 'auto_apply_compatible',
                'description': '',
                'detectedSchemaVersion': None,
                'mapping': {},
                'nativeDetails': {},
            }]
            with self.assertRaisesRegex(ProjectAssetError, 'cannot be auto'):
                validate_asset_request(value, 'project-one', 'cdc-one')

    def test_rejects_duplicate_transform_and_schema_change_ids(self):
        transforms = cdc_asset()
        transforms['content']['transforms'].append(copy.deepcopy(
            transforms['content']['transforms'][0]
        ))
        with self.assertRaisesRegex(
                ProjectAssetError, 'duplicate CDC transforms'):
            validate_asset_request(transforms, 'project-one', 'cdc-one')

    def test_rejects_filter_mapping_collection_confusion(self):
        value = cdc_asset()
        value['content']['filters'] = [copy.deepcopy(
            value['content']['transforms'][0]
        )]
        with self.assertRaisesRegex(
                ProjectAssetError, 'only filter transforms'):
            validate_asset_request(value, 'project-one', 'cdc-one')

    def test_validates_lag_alert_threshold(self):
        value = cdc_asset()
        value['content']['alerts'] = [{
            'schema': 'cdeadmin.cdc-alert.v1',
            'id': 'lag-high',
            'event': 'lag',
            'threshold': {'maximum': 30},
            'enabled': True,
            'nativeDetails': {},
        }]
        validate_asset_request(value, 'project-one', 'cdc-one')
        value['content']['alerts'][0]['threshold'] = {}
        with self.assertRaisesRegex(ProjectAssetError, 'maximum threshold'):
            validate_asset_request(value, 'project-one', 'cdc-one')

    def test_rejects_raw_credentials_anywhere(self):
        value = cdc_asset()
        value['content']['source']['nativeDetails']['password'] = 'forbidden'
        with self.assertRaisesRegex(ProjectAssetError, 'secret field'):
            validate_asset_request(value, 'project-one', 'cdc-one')


if __name__ == '__main__':
    unittest.main()
