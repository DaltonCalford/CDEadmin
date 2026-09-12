import {
  MLVectorAdapterRegistry, MLVectorService, validateMLVectorProviderResult,
} from 'sources/cdeadmin_ui/modules/ml_vector/MLVectorService';
import {
  FederatedSearchService, RelationshipGraphService, TaskExecutionService,
} from 'sources/cdeadmin_ui/platform/CoordinationServices';
import {mlVectorDefinition, user, vectorResource} from './MLVectorTestUtils';

function providerResult(value, overrides={}) { return {supportState: 'supported_native',
  providerVersion: 'milvus-test', evidence: {observed: true}, warnings: [], nativeDetails: {},
  readCapabilities: ['vector.search'], writeCapabilities: ['vector.index', 'embedding.write'],
  discoveryCapabilities: ['vector.capabilities'], nativeMechanisms: ['provider-adapter'],
  versionConstraints: [], limitations: [], runtimeEvidence: {observed: true}, value, ...overrides}; }
function capabilities() { return {vector_storage: true, vector_dimensions: true,
  supported_metrics: [{normalized: 'cosine', native: 'COSINE'}],
  supported_index_families: [{id: 'HNSW', label: 'HNSW', metrics: ['COSINE'],
    buildModes: ['online'], parameterSchema: {additionalProperties: false, fields: [
      {id: 'M', label: 'M', type: 'integer', required: true, minimum: 2, maximum: 64},
      {id: 'efConstruction', label: 'Build breadth', type: 'integer', required: true,
        minimum: 8, maximum: 1000}]}, nativeDetails: {}}], filtering: true, hybrid_search: true,
  multi_vector: false, quantization: true, index_build_online: true, max_dimensions: 4096,
  nativeDetails: {source: 'live'}}; }
function adapter(calls=[]) { const record = (name, value) => async (input) => {
  calls.push({name, input}); return providerResult(typeof value === 'function' ? value(input) : value); };
return {describeVectorCapabilities: record('describeVectorCapabilities', capabilities()),
  validateIndexConfig: record('validateIndexConfig', {valid: true}),
  prepareVectorSearch: record('prepareVectorSearch', {queryPlanId: 'plan-one'}),
  executeVectorSearch: record('executeVectorSearch', {rows: [{id: 'one', score: 0.99}], total: 1}),
  explainVectorSearch: record('explainVectorSearch', {nativePlan: 'VECTOR INDEX SEARCH'}),
  buildVectorIndex: record('buildVectorIndex', {state: 'built'}),
  resolveExternalModel: record('resolveExternalModel', {runtime: 'onnx', model: 'minilm'}),
  runEmbedding: record('runEmbedding', {processed: 12, failed: 0}),
  runEvaluation: record('runEvaluation', {metrics: {'recall@10': 0.92, latency_ms: 3.1},
    latencyMs: 3.1, throughput: 322, indexSizeBytes: 4096, buildTimeMs: 50,
    evidence: {datasetRevision: 'one'}}),
  importModel: record('importModel', mlVectorDefinition().modelEntries[0])}; }
function fixture(options={}) { const tasks = new TaskExecutionService({now: () => '2026-09-12T00:00:00Z'});
  const relationships = new RelationshipGraphService(); const search = new FederatedSearchService();
  const adapters = new MLVectorAdapterRegistry(); const calls = [];
  if(options.register !== false) adapters.register('milvus', options.adapter ?? adapter(calls));
  const service = new MLVectorService({tasks, relationships, search, adapters,
    projectAssets: options.projectAssets, events: options.events,
    now: () => '2026-09-12T00:00:00Z'});
  return {service, tasks, relationships, search, adapters, calls}; }
function searchRequest(overrides={}) { return {designId: 'documents', indexId: 'documents-hnsw',
  queryText: 'database administration', filters: {tenant: 'one'}, k: 10,
  outputFields: ['title'], ...overrides}; }

describe('ML / Vector service', () => {
  test('admits only complete exact adapters and evidence envelopes', () => {
    const registry = new MLVectorAdapterRegistry();
    expect(() => registry.register('partial', {describeVectorCapabilities() {}})).toThrow('validateIndexConfig');
    expect(() => validateMLVectorProviderResult({supportState: 'supported_native'}, 'p', 'op'))
      .toThrow('warnings');
    expect(() => validateMLVectorProviderResult({...providerResult({ok: true}), guessed: true}, 'p', 'op'))
      .toThrow('unsupported field guessed');
  });

  test('validates provider-advertised index controls and preserves evidence', async () => {
    const {service, calls} = fixture(); const session = service.create({content: mlVectorDefinition()});
    const result = await service.validateIndex(session.id, 'documents', 'documents-hnsw');
    expect(result).toMatchObject({providerId: 'milvus', local: {valid: true}, provider: {valid: true}});
    expect(calls.map((item) => item.name)).toEqual(['describeVectorCapabilities', 'validateIndexConfig']);
    expect(service.get(session.id).runtime.capabilitySnapshots[0]).toMatchObject({providerId: 'milvus',
      capabilities: {max_dimensions: 4096}, evidence: {observed: true}});
  });

  test('never infers support for an unregistered provider', async () => {
    const {service} = fixture({register: false}); const session = service.create({content: mlVectorDefinition()});
    await expect(service.validateIndex(session.id, 'documents', 'documents-hnsw')).rejects.toThrow('unknown');
    expect(service.get(session.id).providerStatuses[0]).toMatchObject({supportState: 'unknown'});
  });

  test('runs bounded search and provider-native explain through separate paths', async () => {
    const {service, tasks, calls} = fixture(); const session = service.create({content: mlVectorDefinition()});
    const task = service.searchVectors(session.id, searchRequest(), {currentUser: user()});
    await expect(tasks.wait(task.id)).resolves.toMatchObject({rows: [{id: 'one', score: 0.99}], total: 1,
      truncated: false});
    await expect(service.explainSearch(session.id, searchRequest())).resolves.toMatchObject({
      plan: {nativePlan: 'VECTOR INDEX SEARCH'}});
    expect(calls.map((item) => item.name)).toEqual(expect.arrayContaining([
      'prepareVectorSearch', 'executeVectorSearch', 'explainVectorSearch']));
  });

  test('runs embedding with referenced models and blocks unapproved sensitive external use', async () => {
    const {service, tasks} = fixture(); let session = service.create({content: mlVectorDefinition()});
    let task = service.runEmbedding(session.id, 'document-embedding', {}, {currentUser: user()});
    await expect(tasks.wait(task.id)).resolves.toMatchObject({result: {processed: 12, failed: 0}});
    const definition = mlVectorDefinition(); const pipeline = definition.embeddingPipelines[0];
    const external = mlVectorDefinition({embeddingPipelines: [{...pipeline, modelRef: {...pipeline.modelRef,
      external: true, providerIdentity: 'External AI'}, dataSharingPolicy: {...pipeline.dataSharingPolicy,
      containsSensitiveData: true, approvedForSensitiveData: true}}]});
    session = service.create({id: 'external', content: external}); task = service.runEmbedding(
      session.id, 'document-embedding', {}, {currentUser: {id: 'reader', permissions: ['ml_vector.edit']}});
    await expect(tasks.wait(task.id)).rejects.toThrow('ml_vector.use_external_model');
  });

  test('evaluates against explicit ground truth and dataset revision', async () => {
    const {service, tasks} = fixture(); const session = service.create({content: mlVectorDefinition()});
    const task = service.runEvaluation(session.id, 'retrieval-evaluation', {}, {currentUser: user()});
    const result = await tasks.wait(task.id); expect(result).toMatchObject({evaluationId: 'retrieval-evaluation',
      metrics: {metrics: {'recall@10': 0.92}, latencyMs: 3.1},
      groundTruthRef: {assetId: 'ground-truth-one'}, datasetRevisionRef: {assetId: 'dataset-revision-one'}});
  });

  test('keeps model versions immutable while tags and confirmed aliases remain mutable associations', () => {
    const {service} = fixture(); const session = service.create({content: mlVectorDefinition()});
    const version = service.get(session.id).content.modelEntries[0].versions[0];
    service.tagVersion(session.id, 'minilm', 'minilm-v1', ['reviewed'], {currentUser: user()});
    expect(service.get(session.id).content.modelEntries[0].versions[0]).toEqual(version);
    expect(() => service.setAlias(session.id, 'minilm', 'production', 'minilm-v1'))
      .toThrow('target-bound');
    service.setAlias(session.id, 'minilm', 'production', 'minilm-v1', {confirmationRef: 'approval',
      modelId: 'minilm', versionId: 'minilm-v1'}, {currentUser: user()});
    expect(service.get(session.id).content.modelEntries[0].aliases).toEqual([
      {id: 'production', name: 'production', versionId: 'minilm-v1'}]);
  });

  test('executes separately declared index-build and model-import task contracts', async () => {
    const {service, tasks} = fixture(); const session = service.create({content: mlVectorDefinition()});
    let task = service.buildIndex(session.id, 'documents', 'documents-hnsw', {
      confirmationRef: 'approval', environment: 'test', connection: `resource:${vectorResource.canonical}`});
    await expect(tasks.wait(task.id)).resolves.toMatchObject({result: {state: 'built'}});
    const empty = service.create({id: 'model-import', content: mlVectorDefinition({modelEntries: [],
      deploymentBindings: []})}); task = service.importModel(empty.id, {artifactRef: 'asset-one'},
      {providerId: 'milvus'}, {currentUser: user()});
    await expect(tasks.wait(task.id)).resolves.toMatchObject({modelId: 'minilm'});
    expect(service.get(empty.id).runtime.modelImports).toHaveLength(1);
  });

  test('contributes permission-filtered search and typed relationships', async () => {
    const {service, search, relationships} = fixture(); service.create({id: 'retrieval',
      content: mlVectorDefinition()}); const found = await search.search('documents',
      {context: {permissions: ['ml_vector.view']}});
    expect(found.groups[0].results.map((item) => item.type)).toEqual(expect.arrayContaining([
      'ml_vector.design', 'ml_vector.index']));
    expect((await search.search('retrieval', {context: {permissions: ['ml_vector.view']}}))
      .groups[0].results.map((item) => item.type)).toContain('ml_vector.asset');
    expect(relationships.snapshot().edges).toContainEqual(expect.objectContaining({
      origin: 'cdeadmin.ml_vector', relation: 'vector_storage'}));
    expect((await search.search('documents', {context: {permissions: []}})).total).toBe(0);
  });

  test('preserves dirty authored state on optimistic persistence conflicts', async () => {
    const projectAssets = {update: jest.fn().mockRejectedValue(new Error('asset conflict'))};
    const {service} = fixture({projectAssets}); const session = service.create({content: mlVectorDefinition()});
    await expect(service.save(session.id, {projectId: 'p', assetId: 'a', expectedVersion: 1}))
      .rejects.toThrow('asset conflict');
    expect(service.get(session.id)).toMatchObject({dirty: true, state: 'runtime_failure'});
  });
});
