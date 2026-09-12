##########################################################################
# CDEadmin ML / Vector project-asset backend validation gates.
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
    'cdeadmin_ml_vector_permissions', PERMISSION_PATH)
PERMISSION_MODULE = importlib.util.module_from_spec(PERMISSION_SPEC)
PERMISSION_SPEC.loader.exec_module(PERMISSION_MODULE)


def identity_gettext(value):
    return value


PERMISSION_MODULE.gettext = identity_gettext
AllPermissionTypes = PERMISSION_MODULE.AllPermissionTypes
PgAdminPermissions = PERMISSION_MODULE.PgAdminPermissions


RESOURCE = {'schema': 'cdeadmin.resource-ref.v1',
            'canonical': 'milvus://localhost/demo/documents',
            'providerId': 'milvus'}


def ASSET(identity):
    return {'schema': 'cdeadmin.asset-ref.v1',
            'projectId': 'project-one', 'assetId': identity}


def RESULT(identity):
    return {'schema': 'cdeadmin.result-ref.v1', 'id': identity}


MODEL_REF = {'schema': 'cdeadmin.embedding-model-ref.v1',
             'modelId': 'minilm', 'versionId': 'minilm-v1',
             'providerId': 'local-model', 'external': False,
             'providerIdentity': None, 'credentialRef': None,
             'nativeDetails': {'runtime': 'onnx'}}


def content():
    field = {'schema': 'cdeadmin.embedding-field.v1', 'id': 'embedding',
             'name': 'Embedding', 'sourceFields': ['title', 'body'],
             'modelRef': copy.deepcopy(MODEL_REF), 'dimensions': 384,
             'targetField': 'embedding',
             'normalizationTemplate': '{{title}}\n{{body}}',
             'nativeDetails': {'dataType': 'FLOAT_VECTOR'}}
    index = {'schema': 'cdeadmin.vector-index-plan.v1',
             'id': 'documents-hnsw', 'name': 'Documents HNSW',
             'fieldId': 'embedding', 'algorithm': 'HNSW',
             'normalizedMetric': 'cosine', 'nativeMetric': 'COSINE',
             'dimensions': 384,
             'providerConfig': {'M': 16, 'efConstruction': 200},
             'buildMode': 'online', 'description': 'Primary index',
             'extensions': []}
    return {'schema': 'cdeadmin.ml-vector.asset.v1', 'schemaVersion': 1,
            'moduleId': 'cdeadmin.ml_vector', 'name': 'Retrieval',
            'description': 'Document retrieval',
            'vectorDesigns': [{
                'schema': 'cdeadmin.vector-design.v1', 'id': 'documents',
                'name': 'Documents', 'resourceRef': copy.deepcopy(RESOURCE),
                'dimensions': 384, 'fields': [field], 'indexes': [index],
                'description': 'Document vectors',
                'nativeDetails': {'collection': 'documents'},
                'extensions': []}],
            'embeddingPipelines': [{
                'schema': 'cdeadmin.embedding-pipeline.v1',
                'id': 'document-embedding', 'name': 'Document embedding',
                'resourceRef': copy.deepcopy(RESOURCE),
                'sourceFields': ['title', 'body'],
                'normalizationTemplate': '{{title}}\n{{body}}',
                'modelRef': copy.deepcopy(MODEL_REF),
                'outputDimensions': 384, 'targetField': 'embedding',
                'batching': {'size': 32},
                'rateLimitPolicy': {'perSecond': 10},
                'dataSharingPolicy': {'containsSensitiveData': False,
                                      'approvedForSensitiveData': False,
                                      'providerIdentity': 'local-model'},
                'description': 'Local pipeline', 'extensions': []}],
            'modelEntries': [{
                'schema': 'cdeadmin.model-entry.v1', 'id': 'minilm',
                'name': 'MiniLM', 'description': 'Local model',
                'tags': ['embedding'],
                'aliases': [{'id': 'production', 'name': 'production',
                             'versionId': 'minilm-v1'}],
                'versionTags': [{'id': 'minilm-v1-tags',
                                 'versionId': 'minilm-v1',
                                 'tags': ['approved']}],
                'versions': [{
                    'schema': 'cdeadmin.model-version.v1',
                    'id': 'minilm-v1', 'version': '1.0.0',
                    'artifactRef': ASSET('minilm-model'),
                    'originatingRunRef': RESULT('run-one'),
                    'datasetRefs': [ASSET('dataset-one')],
                    'evaluationRefs': [RESULT('evaluation-one')],
                    'deploymentRefs': [], 'contentDigest': 'sha256:example',
                    'description': 'Initial version',
                    'nativeDetails': {'format': 'onnx'}}],
                'extensions': []}],
            'experiments': [{
                'schema': 'cdeadmin.ml-experiment.v1',
                'id': 'experiment-one', 'name': 'Retrieval experiment',
                'inputRefs': [ASSET('dataset-one')],
                'parameters': {'seed': 42},
                'metricDefinitions': {'recall': 'recall@10'},
                'artifactRefs': [],
                'datasetRevisionRef': ASSET('dataset-revision-one'),
                'description': 'Baseline', 'extensions': []}],
            'evaluationCases': [{
                'schema': 'cdeadmin.vector-evaluation-case.v1',
                'id': 'evaluation-one', 'name': 'Retrieval evaluation',
                'vectorDesignId': 'documents', 'indexId': 'documents-hnsw',
                'querySource': {'kind': 'text', 'value': 'example'},
                'filters': {}, 'k': 10,
                'metrics': ['recall@10', 'latency_ms'],
                'groundTruthRef': ASSET('ground-truth-one'),
                'datasetRevisionRef': ASSET('dataset-revision-one'),
                'expected': {'recallAt10': 0.8},
                'description': 'Benchmark', 'extensions': []}],
            'deploymentBindings': [{
                'schema': 'cdeadmin.ml-deployment.v1', 'id': 'dev',
                'name': 'Development', 'environment': 'development',
                'endpointRef': copy.deepcopy(RESOURCE), 'modelId': 'minilm',
                'modelVersionId': 'minilm-v1',
                'vectorDesignId': 'documents', 'indexId': 'documents-hnsw',
                'credentialRef': None, 'policy': {'readOnly': True},
                'config': {'replicas': 1}, 'extensions': []}],
            'extensions': []}


def request():
    return {'asset_type': 'cdeadmin.ml_vector.v1',
            'schema_name': 'cdeadmin.ml_vector.v1', 'schema_version': 1,
            'name': 'Retrieval', 'path': 'ml-vector/retrieval.json',
            'expected_version': 0, 'content': content(),
            'metadata': {'moduleId': 'cdeadmin.ml_vector'},
            'dependency_references': [], 'resource_bindings': [RESOURCE],
            'validation_state': 'valid', 'validation_details': []}


class MLVectorAssetValidationTest(unittest.TestCase):
    def validate(self, value):
        return validate_asset_request(value, 'project-one', 'ml-vector-one')

    def test_accepts_complete_canonical_asset(self):
        result = self.validate(request())
        stored = json.loads(result['content'])
        self.assertEqual(stored['vectorDesigns'][0]['dimensions'], 384)
        self.assertEqual(stored['modelEntries'][0]['versions'][0]['id'],
                         'minilm-v1')

    def test_rejects_wrong_schema_version_module_and_unknown_fields(self):
        for field, value, message in (
                ('schema', 'wrong', 'asset schema'),
                ('schemaVersion', 2, 'version or module'),
                ('moduleId', 'wrong', 'version or module'),
                ('guessed', True, 'unsupported field')):
            item = request()
            item['content'][field] = value
            with self.assertRaisesRegex(ProjectAssetError, message):
                self.validate(item)

    def test_rejects_unknown_nested_fields_and_duplicate_ids(self):
        item = request()
        item['content']['vectorDesigns'][0]['indexes'][0]['guessed'] = True
        with self.assertRaisesRegex(ProjectAssetError, 'unsupported field'):
            self.validate(item)
        item = request()
        item['content']['vectorDesigns'].append(copy.deepcopy(
            item['content']['vectorDesigns'][0]))
        with self.assertRaisesRegex(ProjectAssetError, 'IDs must be unique'):
            self.validate(item)

    def test_rejects_dimension_and_cross_reference_mismatches(self):
        item = request()
        item['content']['vectorDesigns'][0]['indexes'][0]['dimensions'] = 768
        with self.assertRaisesRegex(ProjectAssetError, 'dimensions'):
            self.validate(item)
        item = request()
        item['content']['evaluationCases'][0]['indexId'] = 'missing'
        with self.assertRaisesRegex(ProjectAssetError, 'unknown vector index'):
            self.validate(item)
        item = request()
        item['content']['deploymentBindings'][0]['modelVersionId'] = 'missing'
        with self.assertRaisesRegex(
                ProjectAssetError, 'unknown model version'):
            self.validate(item)

    def test_rejects_unsupported_metrics_and_missing_quality_evidence(self):
        item = request()
        item['content']['vectorDesigns'][0]['indexes'][0][
            'normalizedMetric'] = 'guessed'
        with self.assertRaisesRegex(ProjectAssetError, 'metric'):
            self.validate(item)
        item = request()
        item['content']['evaluationCases'][0]['groundTruthRef'] = None
        with self.assertRaisesRegex(ProjectAssetError, 'ground truth'):
            self.validate(item)

    def test_rejects_unapproved_sensitive_external_models(self):
        item = request()
        pipeline = item['content']['embeddingPipelines'][0]
        pipeline['modelRef']['external'] = True
        pipeline['modelRef']['providerIdentity'] = 'External AI'
        pipeline['dataSharingPolicy']['containsSensitiveData'] = True
        with self.assertRaisesRegex(ProjectAssetError, 'explicit approval'):
            self.validate(item)

    def test_rejects_raw_secrets_anywhere_in_asset(self):
        item = request()
        item['content']['deploymentBindings'][0][
            'config']['password'] = 'hidden'
        with self.assertRaisesRegex(ProjectAssetError, 'secret'):
            self.validate(item)

    def test_registers_all_independent_ml_vector_permissions(self):
        names = {item['name'] for item in PgAdminPermissions().all_permissions}
        self.assertTrue({
            AllPermissionTypes.ml_vector_view,
            AllPermissionTypes.ml_vector_search,
            AllPermissionTypes.ml_vector_edit,
            AllPermissionTypes.ml_vector_build,
            AllPermissionTypes.ml_vector_use_external_model,
            AllPermissionTypes.ml_vector_admin,
        }.issubset(names))


if __name__ == '__main__':
    unittest.main()
