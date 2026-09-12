##########################################################################
# CDEadmin API Designer project-asset backend validation gates.
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


RESOURCE = {
    'schema': 'cdeadmin.resource-ref.v1',
    'canonical': 'firebird://localhost/example/orders',
    'providerId': 'firebird',
}
CREDENTIAL = {
    'schema': 'cdeadmin.credential-ref.v1',
    'id': 'credential-one',
    'providerId': 'credential-vault',
    'scope': 'api:orders',
    'displayName': 'Orders service credential',
}


def extension(identity='x-owner'):
    return {'id': identity, 'name': identity, 'value': {'team': 'data'}}


def api_content():
    return {
        'schema': 'cdeadmin.api.asset.v1',
        'schemaVersion': 1,
        'moduleId': 'cdeadmin.api',
        'profile': 'openapi_http',
        'externalSpecVersion': '3.2.0',
        'info': {'title': 'Orders API', 'version': '1.0.0'},
        'servers': [{
            'schema': 'cdeadmin.api-server.v1',
            'id': 'production',
            'name': 'Production',
            'environment': 'production',
            'urlTemplate': 'https://api.example.test/{region}',
            'protocol': 'https',
            'credentialRef': copy.deepcopy(CREDENTIAL),
            'variables': {'region': 'ca-central-1'},
            'tlsPolicyRef': {
                'schema': 'cdeadmin.asset-ref.v1',
                'projectId': 'project-one',
                'assetId': 'tls-policy-one',
            },
            'description': 'Production endpoint',
            'extensions': [],
        }],
        'operations': [{
            'schema': 'cdeadmin.api-operation.v1',
            'id': 'list-orders',
            'name': 'List orders',
            'method': 'GET',
            'path': '/orders',
            'summary': 'List orders',
            'description': 'Returns orders',
            'parameters': [{
                'schema': 'cdeadmin.api-parameter.v1',
                'id': 'limit',
                'name': 'limit',
                'location': 'query',
                'required': False,
                'description': 'Maximum rows',
                'schemaRef': None,
                'definition': {'type': 'integer'},
                'extensions': [],
            }],
            'requestSchemaRef': None,
            'responses': [{
                'schema': 'cdeadmin.api-response.v1',
                'id': '200',
                'status': '200',
                'description': 'Orders',
                'schemaRef': 'order',
                'headers': {},
                'examples': {'one': {'id': 1}},
                'extensions': [],
            }],
            'binding': {
                'schema': 'cdeadmin.api-binding.v1',
                'id': 'orders-binding',
                'type': 'provider_resource_read',
                'targetRef': copy.deepcopy(RESOURCE),
                'mode': 'read',
                'queryAssetRef': None,
                'command': None,
                'inputMap': {'limit': 'limit'},
                'outputMap': {'rows': 'body'},
                'nativeDetails': {'providerOperation': 'select'},
                'extensions': [],
            },
            'securityRequirementIds': ['oauth'],
            'policyIds': ['rate-limit'],
            'tags': ['orders'],
            'deprecated': False,
            'idempotent': True,
            'callbacks': {},
            'extensions': [],
        }],
        'channels': [],
        'messages': [],
        'schemas': [{
            'schema': 'cdeadmin.api-schema.v1',
            'id': 'order',
            'name': 'Order',
            'kind': 'object',
            'definition': {'type': 'object'},
            'fields': [{
                'id': 'id',
                'name': 'id',
                'definition': {'type': 'integer'},
                'required': True,
                'description': 'Order identity',
            }],
            'physicalBindings': [copy.deepcopy(RESOURCE)],
            'description': 'Order response',
            'extensions': [],
        }],
        'security': [{
            'schema': 'cdeadmin.api-security.v1',
            'id': 'oauth',
            'name': 'OAuth',
            'type': 'oauth2',
            'scheme': None,
            'location': None,
            'parameterName': None,
            'flows': {'source': '{"clientCredentials":true}'},
            'scopes': {'orders:read': 'Read orders'},
            'credentialRef': copy.deepcopy(CREDENTIAL),
            'description': 'OAuth client credentials',
            'extensions': [],
        }],
        'policies': [{
            'schema': 'cdeadmin.api-policy.v1',
            'id': 'rate-limit',
            'name': 'Rate limit',
            'type': 'rate_limit',
            'config': {'requests': 100},
            'extensions': [],
        }],
        'tests': [{
            'schema': 'cdeadmin.api-test.v1',
            'id': 'live-list',
            'name': 'Live list',
            'operationId': 'list-orders',
            'channelId': None,
            'environmentId': 'production',
            'mode': 'live',
            'parameters': {'limit': 10},
            'headers': {'accept': 'application/json'},
            'body': {},
            'expected': {'status': 200},
            'sensitiveHeaderNames': ['authorization'],
            'extensions': [],
        }],
        'deploymentBindings': [{
            'schema': 'cdeadmin.api-deployment.v1',
            'id': 'gateway-production',
            'name': 'Production gateway',
            'environment': 'production',
            'targetRef': {
                'schema': 'cdeadmin.asset-ref.v1',
                'projectId': 'project-one',
                'assetId': 'gateway-one',
            },
            'gatewayProfile': 'generic_http_gateway',
            'credentialRef': copy.deepcopy(CREDENTIAL),
            'config': {'stage': 'production'},
            'extensions': [],
        }],
        'externalReferences': [{
            'id': 'common-schema',
            'uri': 'https://specs.example.test/common.json',
            'provenance': {'importedAt': '2026-09-12T00:00:00Z'},
            'contentDigest': 'sha256:example',
        }],
        'extensions': [extension()],
    }


def api_asset():
    return {
        'asset_type': 'cdeadmin.api.v1',
        'schema_name': 'cdeadmin.api.v1',
        'schema_version': 1,
        'name': 'Orders API',
        'path': 'apis/orders.json',
        'expected_version': 0,
        'content': api_content(),
        'metadata': {'moduleId': 'cdeadmin.api'},
        'dependency_references': [],
        'resource_bindings': [],
        'validation_state': 'valid',
        'validation_details': [],
    }


class APIAssetValidationTest(unittest.TestCase):
    def validate(self, value):
        return validate_asset_request(value, 'project-one', 'api-one')

    def test_accepts_complete_canonical_asset(self):
        result = self.validate(api_asset())
        content = json.loads(result['content'])
        self.assertEqual(content['profile'], 'openapi_http')
        self.assertEqual(content['operations'][0]['id'], 'list-orders')

    def test_accepts_each_explicit_api_profile(self):
        for profile in (
                'openapi_http', 'asyncapi_event', 'graphql_schema',
                'rpc_extension'):
            request = api_asset()
            request['content']['profile'] = profile
            self.validate(request)

    def test_rejects_wrong_schema_version_module_and_profile(self):
        for field, value, message in (
                ('schema', 'wrong', 'asset schema'),
                ('schemaVersion', 2, 'version or module'),
                ('moduleId', 'wrong', 'version or module'),
                ('profile', 'guessed', 'profile')):
            request = api_asset()
            request['content'][field] = value
            with self.assertRaisesRegex(ProjectAssetError, message):
                self.validate(request)

    def test_rejects_unknown_root_and_nested_fields(self):
        request = api_asset()
        request['content']['guessed'] = True
        with self.assertRaisesRegex(ProjectAssetError, 'unsupported field'):
            self.validate(request)
        request = api_asset()
        request['content']['operations'][0]['guessed'] = True
        with self.assertRaisesRegex(ProjectAssetError, 'unsupported field'):
            self.validate(request)

    def test_rejects_duplicate_stable_ids(self):
        request = api_asset()
        request['content']['operations'].append(copy.deepcopy(
            request['content']['operations'][0]))
        with self.assertRaisesRegex(ProjectAssetError, 'IDs must be unique'):
            self.validate(request)

    def test_rejects_unknown_method_binding_type_and_mode(self):
        for path, value, message in (
                (('method',), 'FETCH', 'method'),
                (('binding', 'type'), 'guessed', 'binding type'),
                (('binding', 'mode'), 'guessed', 'binding mode')):
            request = api_asset()
            target = request['content']['operations'][0]
            if len(path) == 1:
                target[path[0]] = value
            else:
                target[path[0]][path[1]] = value
            with self.assertRaisesRegex(ProjectAssetError, message):
                self.validate(request)

    def test_rejects_dangling_cross_references(self):
        cases = (
            ('requestSchemaRef', 'missing', 'request schema'),
            ('securityRequirementIds', ['missing'], 'unknown security'),
            ('policyIds', ['missing'], 'unknown policy'),
        )
        for field, value, message in cases:
            request = api_asset()
            request['content']['operations'][0][field] = value
            with self.assertRaisesRegex(ProjectAssetError, message):
                self.validate(request)
        request = api_asset()
        request['content']['tests'][0]['environmentId'] = 'missing'
        with self.assertRaisesRegex(ProjectAssetError, 'unknown server'):
            self.validate(request)

    def test_requires_credential_references_in_credential_slots(self):
        request = api_asset()
        request['content']['servers'][0]['credentialRef'] = copy.deepcopy(
            RESOURCE)
        with self.assertRaisesRegex(ProjectAssetError, 'unsupported'):
            self.validate(request)
        request = api_asset()
        request['content']['security'][0]['credentialRef']['password'] = (
            'forbidden')
        with self.assertRaisesRegex(ProjectAssetError, 'unsupported field'):
            self.validate(request)

    def test_rejects_raw_credentials_anywhere(self):
        request = api_asset()
        request['content']['operations'][0]['callbacks'] = {
            'accessToken': 'forbidden',
        }
        with self.assertRaisesRegex(ProjectAssetError, 'secret field'):
            self.validate(request)

    def test_rejects_invalid_parameter_and_test_semantics(self):
        request = api_asset()
        request['content']['operations'][0]['parameters'][0][
            'location'] = 'database'
        with self.assertRaisesRegex(ProjectAssetError, 'location'):
            self.validate(request)
        request = api_asset()
        request['content']['tests'][0]['mode'] = 'unsafe'
        with self.assertRaisesRegex(ProjectAssetError, 'mode'):
            self.validate(request)

    def test_rejects_wrong_route_level_schema_name(self):
        request = api_asset()
        request['schema_name'] = 'cdeadmin.api.asset.v1'
        with self.assertRaisesRegex(ProjectAssetError, 'schema name'):
            self.validate(request)


if __name__ == '__main__':
    unittest.main()
