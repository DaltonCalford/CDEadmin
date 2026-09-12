/////////////////////////////////////////////////////////////
// ETL provider admission, tasks, preview, run and recovery gates.
/////////////////////////////////////////////////////////////

import {
  DiagnosticsService, FederatedSearchService, PlatformEventService,
  RelationshipGraphService, TaskExecutionService,
} from 'sources/cdeadmin_ui/platform';
import {
  ETLAdapterRegistry, ETLService, validateETLProviderResult,
} from 'sources/cdeadmin_ui/modules/etl';

const now = () => '2026-09-11T17:00:00Z';
const resource = (provider, name) => ({schema: 'cdeadmin.resource-ref.v1', provider,
  canonical: `cde-resource://${provider}/local/database/demo/${name}`});
const port = (id, direction) => ({id, name: id, direction, mode: 'batch', schemaState: 'known',
  fields: [{id: 'id', name: 'ID', semanticType: 'integer', nullable: false, nativeDetails: {}}]});
const node = (id, kind, provider, referenceName) => ({id, name: id, kind, config: {},
  resourceRef: resource(provider, referenceName), ports: kind === 'source' ?
    [port('out', 'output')] : [port('in', 'input')], capabilityRequirements: [],
  executionPreference: kind === 'source' ? 'source_pushdown' : 'target_pushdown',
  checkpointEnabled: kind === 'source', nativeDetails: {}});
const content = (updates={}) => ({name: 'Firebird to MongoDB', description: 'Cross-engine ETL',
  mode: 'batch', parameters: [], nodes: [node('source', 'source', 'firebird', 'SOURCE'),
    node('sink', 'sink', 'mongodb', 'TARGET')], edges: [{id: 'flow', fromNodeId: 'source',
    fromPort: 'out', toNodeId: 'sink', toPort: 'in', mappingPolicy: 'explicit', mappings: [],
    deliveryGuarantee: 'at_least_once', partitioning: {}, ordering: {}, nativeDetails: {}}],
  deployments: [{id: 'dev', name: 'Development', environment: 'development', bindings: [],
    parameterBindings: {}, resourceLimits: {}, nativeDetails: {}}], schedules: [], tests: [],
  visualLayout: {}, extensions: {}, ...updates});

function envelope(value, updates={}) {
  return {supportState: 'supported_native', providerVersion: '1.0',
    evidence: {server: 'live'}, warnings: [], nativeDetails: {},
    readCapabilities: ['source', 'preview'], writeCapabilities: ['sink'],
    discoveryCapabilities: ['schema'], nativeMechanisms: ['native'],
    versionConstraints: ['1.0+'], limitations: [], runtimeEvidence: {connected: true},
    value, ...updates};
}

function adapter({failSinkOnce=false, previewWrites=false, deferredPreview=false}={}) {
  let failed = false; let release;
  const previewGate = deferredPreview ? new Promise((resolve) => { release = resolve; }) : null;
  return {release,
    getSourceCapabilities: jest.fn(async () => envelope({modes: ['batch']})),
    getSinkCapabilities: jest.fn(async () => envelope({modes: ['batch']})),
    planPushdown: jest.fn(async (nodeValue) => envelope({location:
      nodeValue.kind === 'source' ? 'source_pushdown' : 'target_pushdown',
    reason: 'Provider-native stage', providerPlan: {nodeId: nodeValue.id}, warnings: []})),
    previewRead: jest.fn(async () => { if(previewGate) await previewGate;
      return envelope({state: 'succeeded', rows: 2, bytes: 20,
        output: [{id: 1}, {id: 2}], written: previewWrites,
        deliveryGuarantee: 'at_least_once'}); }),
    executeNativeStage: jest.fn(async ({node: nodeValue}) => {
      if(failSinkOnce && nodeValue.kind === 'sink' && !failed) {
        failed = true; throw new Error('provider_unavailable: target disconnected');
      }
      return envelope({state: 'succeeded', rows: 2, bytes: 20,
        output: [{id: 1}, {id: 2}], checkpoint: nodeValue.kind === 'source' ?
          {partition: 0, offset: 2} : null, partition: nodeValue.kind === 'source' ? 0 : null,
        written: nodeValue.kind === 'sink', deliveryGuarantee:
        nodeValue.kind === 'source' ? 'exactly_once' : 'at_least_once', lineage: []});
    }),
  };
}

function harness({providers=true, firebird=adapter(), mongodb=adapter()}={}) {
  const tasks = new TaskExecutionService({now}); const relationships = new RelationshipGraphService();
  const search = new FederatedSearchService(); const events = new PlatformEventService();
  const diagnostics = new DiagnosticsService(); const adapters = new ETLAdapterRegistry();
  if(providers) { adapters.register('firebird', firebird); adapters.register('mongodb', mongodb); }
  const projectAssets = {saveAsset: jest.fn(async () => ({version: 3}))};
  const service = new ETLService({tasks, relationships, search, events, diagnostics,
    adapters, projectAssets, now});
  return {service, tasks, relationships, search, events, diagnostics, adapters,
    projectAssets, firebird, mongodb};
}

describe('ETL provider admission', () => {
  it('requires every minimum adapter operation and does not infer support', () => {
    const registry = new ETLAdapterRegistry();
    expect(() => registry.register('firebird', {})).toThrow('getSourceCapabilities');
    registry.register('firebird', adapter()); expect(registry.list()).toEqual(['firebird']);
    expect(() => registry.register('firebird', adapter())).toThrow('already registered');
  });

  it('requires complete evidence and rejects empty or secret-bearing success', () => {
    expect(() => validateETLProviderResult({supportState: 'supported_native'},
      'firebird', 'preview')).toThrow('warnings[]');
    expect(() => validateETLProviderResult(envelope(undefined), 'firebird', 'preview'))
      .toThrow('empty success');
    expect(() => validateETLProviderResult(envelope({}, {nativeDetails: {password: 'x'}}),
      'firebird', 'preview')).toThrow('Raw credential');
  });

  it.each(['supported_native', 'supported_via_cdeadmin',
    'supported_via_external_adapter'])('preserves %s support and evidence', (supportState) => {
    expect(validateETLProviderResult(envelope({ok: true}, {supportState}), 'firebird', 'plan'))
      .toMatchObject({supportState, runtimeEvidence: {connected: true}});
  });

  it.each(['read_only', 'partial', 'unsupported', 'unknown'])(
    'never promotes %s support to native success', (supportState) => {
      expect(validateETLProviderResult(envelope({ok: false}, {supportState}),
        'firebird', 'plan')).toMatchObject({supportState});
    });
});

describe('ETLService', () => {
  it('creates, edits and connects typed nodes with declared relationship provenance', () => {
    const h = harness(); const session = h.service.create({name: 'Draft'});
    h.service.addNode(session.id, node('source', 'source', 'firebird', 'SOURCE'));
    h.service.addNode(session.id, node('sink', 'sink', 'mongodb', 'TARGET'));
    const value = h.service.connectEdge(session.id, content().edges[0]);
    expect(value).toMatchObject({dirty: true, validation: {valid: true}});
    expect(h.relationships.snapshot().edges).toEqual(expect.arrayContaining([
      expect.objectContaining({relation: 'reads', origin: 'etl_declared'}),
      expect.objectContaining({relation: 'writes', origin: 'etl_declared'}),
      expect.objectContaining({relation: 'transforms', origin: 'etl_declared'}),
    ]));
  });

  it('rejects incompatible port modes before modifying the authored graph', () => {
    const h = harness(); const value = content({nodes: [
      {...content().nodes[0], ports: [{...content().nodes[0].ports[0], mode: 'batch'}]},
      {...content().nodes[1], ports: [{...content().nodes[1].ports[0], mode: 'stream'}]}],
    edges: []}); const session = h.service.create(value);
    expect(() => h.service.connectEdge(session.id, content().edges[0])).toThrow('incompatible');
    expect(h.service.get(session.id).content.edges).toEqual([]);
  });

  it('marks loss before deployment and requires explicit acknowledgment', () => {
    const h = harness(); const mapping = {id: 'loss', source: 'id', target: 'id',
      conversion: 'narrow', nullPolicy: 'preserve', nullable: true,
      lossy: true, lossAcknowledged: false, nativeDetails: {}};
    const session = h.service.create(content()); const changed = h.service.editMappings(
      session.id, 'flow', [mapping]
    );
    expect(changed.validation).toMatchObject({valid: true, deployable: false});
    expect(() => h.service.deploy(session.id, {deploymentId: 'dev'}))
      .toThrow('explicit acknowledgment');
  });

  it('validates deployment capabilities and provider pushdown as a shared task', async () => {
    const h = harness(); const session = h.service.create(content());
    const task = h.service.deploy(session.id, {deploymentId: 'dev'}, {currentUser: {id: 'operator'},
      confirmationReference: 'approval-1'});
    expect(h.tasks.view(task.id).audit).toMatchObject({moduleId: 'cdeadmin.etl',
      operation: 'etl.deploy.validation', actor: 'operator', environment: 'development',
      confirmationReference: 'approval-1'});
    const result = await h.tasks.wait(task.id); await Promise.resolve();
    expect(result).toMatchObject({state: 'validated', plans: [
      expect.objectContaining({location: 'source_pushdown'}),
      expect.objectContaining({location: 'target_pushdown'})]});
    expect(h.firebird.getSourceCapabilities).toHaveBeenCalledTimes(1);
    expect(h.mongodb.getSinkCapabilities).toHaveBeenCalledTimes(1);
  });

  it('requires explicit provider authority for transforms instead of inferring a neighbor', async () => {
    const h = harness(); const source = content().nodes[0]; const sink = content().nodes[1];
    const transform = {id: 'map', name: 'map', kind: 'map', config: {}, resourceRef: null,
      ports: [port('in', 'input'), port('out', 'output')], capabilityRequirements: [],
      executionPreference: 'cdeadmin_runtime', checkpointEnabled: false, nativeDetails: {}};
    const session = h.service.create(content({nodes: [source, transform, sink], edges: [
      {...content().edges[0], id: 'source-map', toNodeId: 'map'},
      {...content().edges[0], id: 'map-sink', fromNodeId: 'map'}]}));
    const task = h.service.deploy(session.id, {deploymentId: 'dev'});
    await expect(h.tasks.wait(task.id)).rejects.toThrow('unbound node');
    expect(h.mongodb.planPushdown).not.toHaveBeenCalled();
  });

  it('runs bounded preview and never calls or writes the sink', async () => {
    const h = harness(); const session = h.service.create(content());
    const task = h.service.preview(session.id, {limit: 2, streamScope: {partition: 0}});
    const preview = await h.tasks.wait(task.id); await Promise.resolve();
    expect(preview).toMatchObject({limit: 2, sideEffects: false,
      stages: [expect.objectContaining({nodeId: 'source', rows: 2}),
        expect.objectContaining({nodeId: 'sink', state: 'proposed', written: false})]});
    expect(h.mongodb.previewRead).not.toHaveBeenCalled();
    expect(h.mongodb.executeNativeStage).not.toHaveBeenCalled();
  });

  it('rejects unbounded, over-budget and unscoped stream previews', () => {
    const h = harness(); const session = h.service.create(content());
    expect(() => h.service.preview(session.id, {limit: 10001})).toThrow('1 through 10000');
    const streaming = h.service.create({...content(), id: 'stream', mode: 'streaming',
      nodes: [{...content().nodes[0], ports: [{...content().nodes[0].ports[0], mode: 'stream'}]},
        {...content().nodes[1], ports: [{...content().nodes[1].ports[0], mode: 'stream'}]}],
      edges: [{...content().edges[0]}]});
    expect(() => h.service.preview(streaming.id, {limit: 10})).toThrow('explicit time');
  });

  it('executes stages, reports weakest guarantee and emits observed lineage', async () => {
    const h = harness(); const started = []; const completed = []; const lineage = [];
    h.events.subscribe('etl.run.started', (event) => started.push(event));
    h.events.subscribe('etl.run.completed', (event) => completed.push(event));
    h.events.subscribe('etl.lineage.emitted', (event) => lineage.push(event));
    const session = h.service.create(content()); const deployment = h.service.deploy(
      session.id, {deploymentId: 'dev'}
    ); await h.tasks.wait(deployment.id); await Promise.resolve();
    const task = h.service.startRun(session.id, {deploymentId: 'dev', parameters: {batch: 1}},
      {currentUser: {id: 'runner'}, confirmationReference: 'approved'});
    const run = await h.tasks.wait(task.id); await Promise.resolve();
    expect(run).toMatchObject({state: 'succeeded', deliveryGuarantee: 'at_least_once',
      stages: [expect.objectContaining({nodeId: 'source'}),
        expect.objectContaining({nodeId: 'sink', written: true})]});
    expect(started).toHaveLength(1); expect(completed.at(-1).payload.state).toBe('succeeded');
    expect(lineage).toHaveLength(1);
    expect(lineage[0].payload.origin).toBe('observed_trace');
  });

  it('retries a failed stage under its explicit node error policy', async () => {
    const mongodb = adapter({failSinkOnce: true}); const h = harness({mongodb});
    const value = content({nodes: [content().nodes[0], {...content().nodes[1], errorRoute: {
      action: 'retry', maximumAttempts: 1, retryPolicy: {backoff: 'provider'},
      targetRef: null, nativeDetails: {}}}]});
    const session = h.service.create(value);
    await h.tasks.wait(h.service.deploy(session.id, {deploymentId: 'dev'}).id);
    const run = await h.tasks.wait(h.service.startRun(session.id, {deploymentId: 'dev'}).id);
    expect(run).toMatchObject({state: 'succeeded', stages: [expect.any(Object),
      expect.objectContaining({nodeId: 'sink', attempts: 2})]});
    expect(mongodb.executeNativeStage).toHaveBeenCalledTimes(2);
  });

  it.each(['reject', 'dead_letter'])('routes stage failure through %s policy', async (action) => {
    const mongodb = adapter({failSinkOnce: true}); const h = harness({mongodb});
    const targetRef = action === 'dead_letter' ? resource('mongodb', 'DEAD_LETTER') : null;
    const value = content({nodes: [content().nodes[0], {...content().nodes[1], errorRoute: {
      action, maximumAttempts: 0, retryPolicy: {}, targetRef, nativeDetails: {}}}]});
    const session = h.service.create(value);
    await h.tasks.wait(h.service.deploy(session.id, {deploymentId: 'dev'}).id);
    const run = await h.tasks.wait(h.service.startRun(session.id, {deploymentId: 'dev'}).id);
    expect(run).toMatchObject({state: 'succeeded', stages: [expect.any(Object),
      expect.objectContaining({nodeId: 'sink', state: 'succeeded_with_warnings',
        errorAction: action})]});
    if(action === 'dead_letter') expect(mongodb.executeNativeStage.mock.calls[1][0])
      .toMatchObject({resourceRef: targetRef, errorRoute: {failedNodeId: 'sink'}});
  });

  it('mirrors schema, quality and schedule asset references with declared origin', () => {
    const h = harness(); const qualityRef = {schema: 'cdeadmin.asset-ref.v1', projectId: 'p',
      assetId: 'quality-orders'}; const contractRef = {schema: 'cdeadmin.asset-ref.v1',
      projectId: 'p', assetId: 'contract-orders'};
    const source = {...content().nodes[0], ports: [{...content().nodes[0].ports[0],
      schemaRef: contractRef}]};
    const quality = {id: 'quality', name: 'quality', kind: 'quality_gate',
      config: {qualityRef}, resourceRef: resource('firebird', 'SOURCE'),
      ports: [port('in', 'input'), port('out', 'output')], capabilityRequirements: [],
      executionPreference: 'source_pushdown', checkpointEnabled: false, nativeDetails: {}};
    h.service.create(content({nodes: [source, quality, content().nodes[1]], edges: [
      {...content().edges[0], id: 'source-quality', toNodeId: 'quality'},
      {...content().edges[0], id: 'quality-sink', fromNodeId: 'quality'}],
    schedules: [{id: 'after-contract', name: 'After contract', enabled: true,
      trigger: 'dependency', expression: 'completed', timezone: 'UTC', deploymentId: 'dev',
      dependencyRefs: [contractRef], parameters: {}, nativeDetails: {}}]}));
    expect(h.relationships.snapshot().edges).toEqual(expect.arrayContaining([
      expect.objectContaining({relation: 'governs', origin: 'etl_declared'}),
      expect.objectContaining({relation: 'validates', origin: 'etl_declared'}),
      expect.objectContaining({relation: 'depends_on', origin: 'etl_declared'}),
    ]));
  });

  it('resumes from a checkpoint without replaying completed stages', async () => {
    const firebird = adapter(); const mongodb = adapter({failSinkOnce: true});
    const h = harness({firebird, mongodb}); const session = h.service.create(content());
    await h.tasks.wait(h.service.deploy(session.id, {deploymentId: 'dev'}).id);
    const failedTask = h.service.startRun(session.id, {deploymentId: 'dev'});
    await expect(h.tasks.wait(failedTask.id)).rejects.toThrow('target disconnected');
    await Promise.resolve(); const failed = h.service.get(session.id).runs[0];
    expect(failed).toMatchObject({state: 'failed', checkpoint: {nodeId: 'source'}});
    const sourceCalls = firebird.executeNativeStage.mock.calls.length;
    const resumed = await h.tasks.wait(h.service.resumeRun(session.id, {runId: failed.id}).id);
    expect(resumed).toMatchObject({state: 'succeeded', resumedFrom: failed.id,
      stages: [expect.objectContaining({nodeId: 'source', resumedWithoutReplay: true}),
        expect.objectContaining({nodeId: 'sink'})]});
    expect(firebird.executeNativeStage).toHaveBeenCalledTimes(sourceCalls);
  });

  it('cancels through TaskService and preserves authored content', async () => {
    const firebird = adapter({deferredPreview: true}); const h = harness({firebird});
    const session = h.service.create(content()); const before = session.content;
    const task = h.service.preview(session.id, {limit: 2, streamScope: {partition: 0}});
    expect(h.service.cancel(session.id, task.id)).toBe(true); firebird.release();
    await expect(h.tasks.wait(task.id)).rejects.toMatchObject({name: 'AbortError'});
    expect(h.service.get(session.id).content).toEqual(before);
  });

  it('fails closed for missing providers and distinguishes unavailable state', async () => {
    const h = harness({providers: false}); const session = h.service.create(content());
    const task = h.service.deploy(session.id, {deploymentId: 'dev'});
    await expect(h.tasks.wait(task.id)).rejects.toThrow('capability_missing');
    await Promise.resolve(); expect(h.service.get(session.id)).toMatchObject({state: 'partial',
      providerStatuses: [expect.objectContaining({supportState: 'unknown'})]});
    h.service.reportError(session.id, new Error('offline'), 'provider_unavailable');
    expect(h.service.get(session.id).state).toBe('disconnected');
  });

  it('saves optimistically, preserves dirty state on conflict and searches by type', async () => {
    const h = harness(); const session = h.service.create(content());
    h.service.replaceDefinition(session.id, {...session.content, description: 'Edited'});
    await h.service.save(session.id, {projectId: 'p', assetId: 'etl', name: 'ETL',
      path: 'etl/orders.json', expectedVersion: 2});
    expect(h.projectAssets.saveAsset).toHaveBeenCalledWith('p', 'etl',
      expect.objectContaining({asset_type: 'cdeadmin.etl.v1', expected_version: 2}));
    expect(h.service.get(session.id).dirty).toBe(false);
    h.service.replaceDefinition(session.id, {...session.content, description: 'Again'});
    h.projectAssets.saveAsset.mockRejectedValueOnce(new Error('conflict: expected version 3'));
    await expect(h.service.save(session.id, {projectId: 'p', assetId: 'etl', name: 'ETL',
      path: 'etl/orders.json', expectedVersion: 3})).rejects.toThrow('conflict');
    expect(h.service.get(session.id).dirty).toBe(true);
    expect((await h.search.search('Firebird', {context: {permissions: ['etl.view']}})).total)
      .toBeGreaterThan(0);
    expect((await h.search.search('Firebird', {context: {permissions: []}})).total).toBe(0);
  });

  it('removes ETL-owned relationship nodes and edges on disposal', () => {
    const h = harness(); h.service.create(content()); expect(h.relationships.snapshot().nodes.length)
      .toBeGreaterThan(0); h.service.dispose(); expect(h.relationships.snapshot())
      .toMatchObject({nodes: [], edges: []});
  });
});
