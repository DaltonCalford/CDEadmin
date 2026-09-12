import {
  normalizeEvaluationResult, normalizeSearchRequest, validateIndexForCapabilities,
  validateMLVectorDefinition, validateVectorCapabilities,
} from 'sources/cdeadmin_ui/modules/ml_vector/MLVectorEngine';
import {mlVectorDefinition} from './MLVectorTestUtils';

function capabilities(overrides={}) { return {vector_storage: true, vector_dimensions: true,
  supported_metrics: [{normalized: 'cosine', native: 'COSINE'}],
  supported_index_families: [{id: 'HNSW', label: 'HNSW', metrics: ['COSINE'],
    buildModes: ['online'], parameterSchema: {additionalProperties: false, fields: [
      {id: 'M', label: 'M', type: 'integer', required: true, minimum: 2, maximum: 64},
      {id: 'efConstruction', label: 'Construction effort', type: 'integer', required: true,
        minimum: 8, maximum: 2048}]}, nativeDetails: {providerName: 'HNSW'}}], filtering: true,
  hybrid_search: false, multi_vector: false, quantization: false, index_build_online: true,
  max_dimensions: 32768, nativeDetails: {source: 'provider'}, ...overrides}; }

describe('ML / Vector engine', () => {
  test('admits explicit capability and parameter schemas', () => {
    expect(validateVectorCapabilities(capabilities())).toMatchObject({vector_storage: true,
      supported_index_families: [{id: 'HNSW', parameterSchema: {additionalProperties: false}}]});
    expect(() => validateVectorCapabilities(capabilities({quantization: 'maybe'}))).toThrow('boolean');
  });
  test('shows and validates only provider-advertised index parameters', () => {
    const index = mlVectorDefinition().vectorDesigns[0].indexes[0];
    expect(validateIndexForCapabilities(index, capabilities())).toMatchObject({valid: true,
      familyId: 'HNSW', nativeMetric: 'COSINE'});
    expect(() => validateIndexForCapabilities({...index, providerConfig: {...index.providerConfig,
      probes: 10}}, capabilities())).toThrow('not advertised');
    expect(() => validateIndexForCapabilities({...index, algorithm: 'IVF_FLAT'}, capabilities()))
      .toThrow('not advertised');
  });
  test('rejects mismatched provider metric normalization and dimensional limits', () => {
    const index = mlVectorDefinition().vectorDesigns[0].indexes[0];
    expect(() => validateIndexForCapabilities({...index, normalizedMetric: 'euclidean_l2'}, capabilities()))
      .toThrow('normalization');
    expect(() => validateIndexForCapabilities({...index, dimensions: 40000}, capabilities()))
      .toThrow('maximum');
  });
  test('blocks unapproved sensitive data from external embedding models', () => {
    const content = JSON.parse(JSON.stringify(mlVectorDefinition())); const pipeline = content.embeddingPipelines[0];
    pipeline.modelRef.external = true; pipeline.modelRef.providerIdentity = 'remote.example';
    pipeline.dataSharingPolicy.containsSensitiveData = true;
    expect(validateMLVectorDefinition(content)).toMatchObject({valid: false,
      errors: [expect.stringContaining('not approved')]});
    pipeline.dataSharingPolicy.approvedForSensitiveData = true;
    expect(validateMLVectorDefinition(content).valid).toBe(true);
  });
  test('accepts exactly one bounded vector or text search source', () => {
    expect(normalizeSearchRequest({designId: 'documents', indexId: 'documents-hnsw',
      queryVector: [0.1, 0.2], queryText: null, filters: {}, k: 10, outputFields: ['title']}))
      .toMatchObject({k: 10, queryVector: [0.1, 0.2]});
    expect(() => normalizeSearchRequest({designId: 'documents', indexId: 'index', queryVector: [1],
      queryText: 'both', filters: {}, k: 1, outputFields: []})).toThrow('exactly one');
  });
  test('requires all evaluation metrics with finite values', () => {
    const evaluation = mlVectorDefinition().evaluationCases[0];
    expect(normalizeEvaluationResult({metrics: {'recall@10': 0.9, latency_ms: 5}, latencyMs: 5,
      throughput: 200, indexSizeBytes: 1000, buildTimeMs: 20, evidence: {observed: true}}, evaluation))
      .toMatchObject({metrics: {'recall@10': 0.9}});
    expect(() => normalizeEvaluationResult({metrics: {'recall@10': 0.9}, latencyMs: 5,
      throughput: 200, indexSizeBytes: null, buildTimeMs: null, evidence: {}}, evaluation))
      .toThrow('latency_ms');
  });
});
