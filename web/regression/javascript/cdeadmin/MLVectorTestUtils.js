import {createMLVectorContent} from 'sources/cdeadmin_ui/modules/ml_vector/contracts';

export const vectorResource = Object.freeze({schema: 'cdeadmin.resource-ref.v1',
  canonical: 'milvus://localhost/demo/documents', providerId: 'milvus'});
export const assetRef = (id) => ({schema: 'cdeadmin.asset-ref.v1', projectId: 'project-one', assetId: id});
export const resultRef = (id) => ({schema: 'cdeadmin.result-ref.v1', id});
export const modelRef = Object.freeze({schema: 'cdeadmin.embedding-model-ref.v1', modelId: 'minilm',
  versionId: 'minilm-v1', providerId: 'local-model', external: false, providerIdentity: null,
  credentialRef: null, nativeDetails: {runtime: 'onnx'}});
export function mlVectorDefinition(overrides={}) {
  return createMLVectorContent({name: 'Document retrieval', description: 'Vector retrieval design',
    vectorDesigns: [{id: 'documents', name: 'Documents', resourceRef: vectorResource, dimensions: 384,
      fields: [{id: 'embedding', name: 'Embedding', sourceFields: ['title', 'body'], modelRef,
        dimensions: 384, targetField: 'embedding', normalizationTemplate: '{{title}}\n{{body}}',
        nativeDetails: {dataType: 'FLOAT_VECTOR'}}], indexes: [{id: 'documents-hnsw',
        name: 'Documents HNSW', fieldId: 'embedding', algorithm: 'HNSW', normalizedMetric: 'cosine',
        nativeMetric: 'COSINE', dimensions: 384, providerConfig: {M: 16, efConstruction: 200},
        buildMode: 'online', description: 'Primary retrieval index', extensions: []}],
      description: 'Document vectors', nativeDetails: {collection: 'documents'}, extensions: []}],
    embeddingPipelines: [{id: 'document-embedding', name: 'Document embedding',
      resourceRef: vectorResource, sourceFields: ['title', 'body'],
      normalizationTemplate: '{{title}}\n{{body}}', modelRef, outputDimensions: 384,
      targetField: 'embedding', batching: {size: 32}, rateLimitPolicy: {perSecond: 10},
      dataSharingPolicy: {containsSensitiveData: false, approvedForSensitiveData: false,
        providerIdentity: 'local-model'}, description: 'Local embedding pipeline', extensions: []}],
    modelEntries: [{id: 'minilm', name: 'MiniLM', description: 'Local embedding model', tags: ['embedding'],
      aliases: [{id: 'production', name: 'production', versionId: 'minilm-v1'}],
      versionTags: [{id: 'minilm-v1-tags', versionId: 'minilm-v1', tags: ['approved']}],
      versions: [{id: 'minilm-v1', version: '1.0.0', artifactRef: assetRef('minilm-model'),
        originatingRunRef: resultRef('experiment-run-one'), datasetRefs: [assetRef('dataset-one')],
        evaluationRefs: [resultRef('evaluation-one')], deploymentRefs: [],
        contentDigest: 'sha256:example', description: 'Initial version', nativeDetails: {format: 'onnx'}}],
      extensions: []}],
    experiments: [{id: 'retrieval-experiment', name: 'Retrieval experiment', inputRefs: [assetRef('dataset-one')],
      parameters: {seed: 42}, metricDefinitions: {recallAt10: 'recall@10'}, artifactRefs: [],
      datasetRevisionRef: assetRef('dataset-revision-one'), description: 'Baseline', extensions: []}],
    evaluationCases: [{id: 'retrieval-evaluation', name: 'Retrieval evaluation', vectorDesignId: 'documents',
      indexId: 'documents-hnsw', querySource: {kind: 'text', value: 'example'}, filters: {}, k: 10,
      metrics: ['recall@10', 'latency_ms'], groundTruthRef: assetRef('ground-truth-one'),
      datasetRevisionRef: assetRef('dataset-revision-one'), expected: {recallAt10: 0.8},
      description: 'Retrieval benchmark', extensions: []}],
    deploymentBindings: [{id: 'retrieval-dev', name: 'Retrieval development', environment: 'development',
      endpointRef: vectorResource, modelId: 'minilm', modelVersionId: 'minilm-v1',
      vectorDesignId: 'documents', indexId: 'documents-hnsw', credentialRef: null,
      policy: {readOnly: true}, config: {replicas: 1}, extensions: []}], extensions: [], ...overrides});
}
export function user() { return {id: 'ml-user', permissions: ['ml_vector.view', 'ml_vector.search',
  'ml_vector.edit', 'ml_vector.build', 'ml_vector.use_external_model', 'ml_vector.admin']}; }
