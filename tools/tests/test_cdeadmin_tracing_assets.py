##########################################################################
# CDEadmin Distributed Tracing project-asset backend validation gates.
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


def tracing_content():
    return {
        'schema': 'cdeadmin.tracing.asset.v1',
        'schemaVersion': 1,
        'moduleId': 'cdeadmin.tracing',
        'name': 'Reference tracing',
        'description': 'OTLP and provider-native trace configuration',
        'savedSearches': [{
            'schema': 'cdeadmin.tracing-saved-search.v1',
            'id': 'errors',
            'name': 'Errors',
            'filters': {'status': 'ERROR'},
            'pageSize': 100,
            'sort': {'startTime': 'desc'},
            'description': 'Recent errors',
        }],
        'savedViews': [{
            'schema': 'cdeadmin.tracing-saved-view.v1',
            'id': 'waterfall',
            'name': 'Waterfall',
            'kind': 'waterfall',
            'searchId': 'errors',
            'layout': {},
            'observedWindow': None,
            'description': 'Trace waterfall',
        }],
        'sourceConfigs': [{
            'schema': 'cdeadmin.trace-source.v1',
            'id': 'postgresql',
            'name': 'PostgreSQL native traces',
            'sourceType': 'provider_native',
            'enabled': True,
            'endpoint': None,
            'providerId': 'postgresql',
            'resourceRef': {
                'schema': 'cdeadmin.resource-ref.v1',
                'canonical': 'postgresql://cluster/database',
                'provider': 'postgresql',
            },
            'credentialRef': {
                'schema': 'cdeadmin.credential-ref.v1',
                'id': 'credential-one',
            },
            'transport': {'tls': True},
            'sensitivityPolicyId': 'safe',
            'nativeDetails': {},
        }],
        'samplingPolicies': [{
            'schema': 'cdeadmin.tracing-sampling-policy.v1',
            'id': 'safe',
            'name': 'Safe policy',
            'rate': 1,
            'tailCriteria': {'errors': True},
            'sensitiveAttributePolicy': {
                'captureRawStatements': False,
                'capturePayloads': False,
            },
            'retention': {'days': 7},
            'enabled': True,
        }],
        'retentionPolicyRef': {
            'schema': 'cdeadmin.external-ref.v1',
            'id': 'retention://seven-days',
        },
        'extensions': {},
    }


def tracing_asset():
    return {
        'asset_type': 'cdeadmin.tracing.v1',
        'schema_name': 'cdeadmin.tracing.v1',
        'schema_version': 1,
        'name': 'Reference tracing',
        'path': 'tracing/reference.json',
        'expected_version': 0,
        'content': tracing_content(),
        'metadata': {'moduleId': 'cdeadmin.tracing'},
        'dependency_references': [],
        'resource_bindings': [],
        'validation_state': 'valid',
        'validation_details': [],
    }


class TracingAssetValidationTest(unittest.TestCase):
    def validate(self, value):
        return validate_asset_request(
            value, 'project-one', 'tracing-one')

    def test_accepts_complete_canonical_asset(self):
        result = self.validate(tracing_asset())
        content = json.loads(result['content'])
        self.assertEqual(content['moduleId'], 'cdeadmin.tracing')
        self.assertEqual(
            content['sourceConfigs'][0]['sourceType'],
            'provider_native',
        )

    def test_rejects_wrong_schema_and_module(self):
        for field, value, message in (
                ('schema', 'cdeadmin.replication.asset.v1', 'asset schema'),
                ('moduleId', 'cdeadmin.replication', 'version or module')):
            request = tracing_asset()
            request['content'][field] = value
            with self.assertRaisesRegex(ProjectAssetError, message):
                self.validate(request)

    def test_rejects_unknown_source_and_view_kinds(self):
        request = tracing_asset()
        request['content']['sourceConfigs'][0]['sourceType'] = 'guessed'
        with self.assertRaisesRegex(ProjectAssetError, 'source type'):
            self.validate(request)
        request = tracing_asset()
        request['content']['savedViews'][0]['kind'] = 'guessed'
        with self.assertRaisesRegex(ProjectAssetError, 'view kind'):
            self.validate(request)

    def test_requires_exact_source_specific_fields(self):
        request = tracing_asset()
        request['content']['sourceConfigs'][0]['resourceRef'] = None
        with self.assertRaisesRegex(
                ProjectAssetError, 'provider and resource'):
            self.validate(request)
        request = tracing_asset()
        request['content']['sourceConfigs'][0].update({
            'sourceType': 'otlp_http',
            'resourceRef': None,
            'providerId': None,
            'endpoint': None,
        })
        with self.assertRaisesRegex(ProjectAssetError, 'requires an endpoint'):
            self.validate(request)

    def test_rejects_dangling_authored_references(self):
        for collection, field, message in (
                ('savedViews', 'searchId', 'unknown search'),
                ('sourceConfigs', 'sensitivityPolicyId',
                 'unknown sensitivity policy')):
            request = tracing_asset()
            request['content'][collection][0][field] = 'missing'
            with self.assertRaisesRegex(ProjectAssetError, message):
                self.validate(request)

    def test_rejects_invalid_sampling_rates_and_page_sizes(self):
        for rate in (-0.1, 1.1, float('inf'), True, '1'):
            request = tracing_asset()
            request['content']['samplingPolicies'][0]['rate'] = rate
            with self.assertRaisesRegex(ProjectAssetError, 'sampling rate'):
                self.validate(request)
        request = tracing_asset()
        request['content']['savedSearches'][0]['pageSize'] = 1001
        with self.assertRaisesRegex(ProjectAssetError, 'pageSize'):
            self.validate(request)

    def test_rejects_duplicate_stable_ids(self):
        request = tracing_asset()
        request['content']['sourceConfigs'].append(copy.deepcopy(
            request['content']['sourceConfigs'][0]))
        with self.assertRaisesRegex(ProjectAssetError, 'IDs must be unique'):
            self.validate(request)

    def test_rejects_raw_credentials_anywhere(self):
        request = tracing_asset()
        request['content']['extensions']['accessToken'] = 'forbidden'
        with self.assertRaisesRegex(ProjectAssetError, 'secret field'):
            self.validate(request)


if __name__ == '__main__':
    unittest.main()
