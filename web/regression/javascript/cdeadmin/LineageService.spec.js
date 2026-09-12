/////////////////////////////////////////////////////////////
// Data Lineage orchestration, task, provider and audit gates.
/////////////////////////////////////////////////////////////

import {
  DiagnosticsService, FederatedSearchService, PlatformEventService,
  RelationshipGraphService, TaskExecutionService,
} from 'sources/cdeadmin_ui/platform';
import {
  LineageAdapterRegistry, LineageService,
} from 'sources/cdeadmin_ui/modules/lineage';

const now = () => '2026-09-11T12:00:00Z';
const scope = (providerId='firebird') => ({schema: 'cdeadmin.resource-ref.v1', provider: providerId,
  canonical: `cde-resource://${providerId}/local/database/demo`});
const ref = (id) => ({schema: 'cdeadmin.external-ref.v1', id});
const evidence = (id, origin='provider_declared', confidence=0.9) => ({id, origin,
  confidence, capturedAt: now(), details: {source: id}});
const node = (id, kind='table') => ({id, kind, ref: ref(id), namespace: 'demo',
  name: id, nativeDetails: {}});
const edge = (id, from, to, origin='provider_declared', updates={}) => ({id, from, to,
  type: 'reads', origin, evidence: [evidence(`ev-${id}`, origin)], fieldLineage: [],
  nativeDetails: {}, ...updates});

function result(value, updates={}) {
  return {supportState: 'supported_native', providerVersion: '5.0.4',
    evidence: {runtime: 'verified'}, warnings: [], nativeDetails: {},
    readCapabilities: ['graph'], writeCapabilities: [],
    discoveryCapabilities: ['declared_relationships'], nativeMechanisms: ['system_catalog'],
    versionConstraints: ['>=5.0'], limitations: [], runtimeEvidence: {live: true},
    value, ...updates};
}

function adapter() {
  return {discoverDeclaredRelationships: jest.fn(async () => result({
    nodes: [node('source'), node('target')], edges: [edge('fk', 'source', 'target',
      'provider_declared', {nativeDetails: {structuralRelationship: 'foreign_key'}})]})),
  parseProviderLineage: jest.fn(async () => result({nodes: [node('source'), node('target')],
    edges: [edge('parsed', 'source', 'target', 'parsed_query')]})),
  resolveNativeResource: jest.fn(async (identity) => result(identity)),
  fetchExecutionEvidence: jest.fn(async () => result({nodes: [node('source'), node('target')],
    edges: [edge('execution', 'source', 'target', 'observed_trace')]})),
  supportsFieldLineage: jest.fn(async () => result(true))};
}

function harness({withAdapter=true}={}) {
  const tasks = new TaskExecutionService({now}); const relationships = new RelationshipGraphService();
  const search = new FederatedSearchService(); const events = new PlatformEventService();
  const diagnostics = new DiagnosticsService(); const adapters = new LineageAdapterRegistry();
  const native = adapter(); if(withAdapter) adapters.register('firebird', native);
  const projectAssets = {saveAsset: jest.fn(async () => ({version: 2})), asset: jest.fn()};
  const service = new LineageService({tasks, relationships, search, events, diagnostics,
    adapters, projectAssets, now});
  return {service, tasks, relationships, search, events, diagnostics, adapters,
    native, projectAssets};
}

async function refreshed(h) {
  const session = h.service.create({scopeRefs: [scope()]});
  const task = h.service.refresh(session.id); await h.tasks.wait(task.id);
  return h.service.get(session.id);
}

describe('LineageAdapterRegistry', () => {
  it('requires every normative provider responsibility and never infers support', () => {
    const registry = new LineageAdapterRegistry();
    expect(() => registry.register('firebird', {})).toThrow('discoverDeclaredRelationships');
    expect(registry.get('firebird')).toBeNull();
    registry.register('firebird', adapter());
    expect(registry.list()).toEqual(['firebird']);
    expect(() => registry.register('firebird', adapter())).toThrow('already registered');
  });
});

describe('LineageService', () => {
  it('runs discovery, parsing and reconciliation as dependent shared tasks', async () => {
    const h = harness(); const scans = []; const created = [];
    h.events.subscribe('lineage.scan.completed', (value) => scans.push(value));
    h.events.subscribe('lineage.edge.created', (value) => created.push(value));
    const session = await refreshed(h);
    expect(session.graph.nodes).toHaveLength(2); expect(session.graph.edges).toHaveLength(2);
    expect(session.graph.edges.find((item) => item.origin === 'provider_declared').type)
      .toBe('structural_reference');
    expect(session.graph.edges.find((item) => item.origin === 'parsed_query').type).toBe('reads');
    expect(h.tasks.list({type: 'lineage.discovery.scan'})).toHaveLength(1);
    expect(h.tasks.list({type: 'lineage.parsing.extract'})).toHaveLength(1);
    expect(h.tasks.list({type: 'lineage.reconcile'})).toHaveLength(1);
    expect(scans).toHaveLength(1); expect(created).toHaveLength(2);
    expect(session.state).toBe('ready');
    expect(h.relationships.snapshot().edges).toHaveLength(2);
  });

  it('emits edge expiry when refreshed evidence no longer contains a relationship', async () => {
    const h = harness(); const expired = [];
    h.events.subscribe('lineage.edge.expired', (value) => expired.push(value));
    let session = await refreshed(h);
    h.native.discoverDeclaredRelationships.mockResolvedValueOnce(result({
      nodes: [node('source'), node('target')], edges: [],
    }, {nativeDetails: {emptyReason: 'No declared relationships'}}));
    h.native.parseProviderLineage.mockResolvedValueOnce(result({
      nodes: [node('source'), node('target')], edges: [],
    }, {nativeDetails: {emptyReason: 'No parsed relationships'}}));
    const task = h.service.refresh(session.id); await h.tasks.wait(task.id);
    session = h.service.get(session.id);
    expect(session.graph.edges).toEqual([]); expect(expired).toHaveLength(2);
    expect(h.relationships.snapshot().edges).toEqual([]);
  });

  it('represents a missing adapter response as unknown/partial, never unsupported success', async () => {
    const h = harness({withAdapter: false}); const session = h.service.create({
      scopeRefs: [scope('unknown-provider')]});
    const task = h.service.refresh(session.id); await h.tasks.wait(task.id);
    const value = h.service.get(session.id);
    expect(value.state).toBe('partial');
    expect(value.ingestion[0]).toMatchObject({supportState: 'unknown'});
    expect(value.problems[0]).toMatchObject({code: 'capability_missing'});
  });

  it('applies provider inference and evidence-retention policies without inventing support',
    async () => {
      const h = harness();
      h.native.discoverDeclaredRelationships.mockResolvedValueOnce(result({
        nodes: [node('source'), node('target')], unresolvedIdentifiers: ['MISSING_TABLE'],
        edges: [edge('old', 'source', 'target', 'provider_declared', {evidence: [{
          ...evidence('ev-old'), capturedAt: '2020-01-01T00:00:00Z',
        }]})],
      }));
      let session = h.service.create({scopeRefs: [scope()], sourcePolicies: [{
        id: 'firebird-policy', providerId: 'firebird',
        sourcePriority: ['provider_declared'], inferenceEnabled: false, retentionDays: 30,
      }]});
      const task = h.service.refresh(session.id); await h.tasks.wait(task.id);
      session = h.service.get(session.id);
      expect(session.graph.edges).toHaveLength(1);
      expect(session.graph.edges[0]).toMatchObject({presentationState: 'stale',
        nativeDetails: {providerId: 'firebird'}});
      expect(session.problems).toEqual(expect.arrayContaining([
        expect.objectContaining({code: 'unresolved_lineage', message: 'MISSING_TABLE'}),
      ]));
    });

  it('executes native resolution, runtime evidence and field support contracts exactly', async () => {
    const h = harness();
    await expect(h.service.resolveNativeResource('firebird', {catalog: 'SOURCE'}))
      .resolves.toMatchObject({supportState: 'supported_native',
        discoveryCapabilities: ['declared_relationships']});
    await expect(h.service.fetchExecutionEvidence('firebird', {runId: 'run-one'}))
      .resolves.toMatchObject({value: {nodes: expect.any(Array), edges: expect.any(Array)}});
    await expect(h.service.fieldLineageSupport('firebird'))
      .resolves.toMatchObject({value: true});
    expect(h.native.resolveNativeResource).toHaveBeenCalledWith({catalog: 'SOURCE'});
    expect(h.native.fetchExecutionEvidence).toHaveBeenCalledWith({runId: 'run-one'});
    expect(h.native.supportsFieldLineage).toHaveBeenCalledTimes(1);
  });

  it('requires the complete explicit adapter result envelope', async () => {
    const h = harness(); h.native.supportsFieldLineage.mockResolvedValueOnce({
      supportState: 'supported_native', providerVersion: '5.0.4',
      evidence: {}, warnings: [], nativeDetails: {}, value: true,
    });
    await expect(h.service.fieldLineageSupport('firebird'))
      .rejects.toThrow('readCapabilities[]');
  });

  it('rejects malformed and unexplained empty provider success', async () => {
    const h = harness(); h.native.discoverDeclaredRelationships.mockResolvedValueOnce(result({
      nodes: [], edges: []}));
    const session = h.service.create({scopeRefs: [scope()]});
    const task = h.service.refresh(session.id);
    await expect(h.tasks.wait(task.id)).rejects.toThrow('unexplained empty');
    expect(h.service.get(session.id).state).toBe('runtime_failure');
  });

  it('retries transient provider failures and records explicit cancellation recovery', async () => {
    const h = harness(); h.native.discoverDeclaredRelationships
      .mockRejectedValueOnce(new Error('transient catalog failure'));
    let session = h.service.create({scopeRefs: [scope()]});
    let task = h.service.refresh(session.id, {retry: {maximum: 1}});
    await expect(h.tasks.wait(task.id)).resolves.toEqual(expect.any(Object));
    expect(h.native.discoverDeclaredRelationships).toHaveBeenCalledTimes(2);
    expect(h.tasks.list({type: 'lineage.discovery.scan'})[0].retry.attempt).toBe(2);

    session = h.service.create({id: 'cancel-lineage', scopeRefs: [scope()]});
    task = h.service.refresh(session.id);
    expect(h.tasks.cancel(task.id)).toBe(true);
    await expect(h.tasks.wait(task.id)).rejects.toMatchObject({name: 'AbortError'});
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(h.service.get(session.id)).toMatchObject({state: 'ready',
      problems: expect.arrayContaining([expect.objectContaining({code: 'task_cancelled'})])});
  });

  it('performs bounded trace and background impact with stable result references', async () => {
    const h = harness(); let session = await refreshed(h);
    session = h.service.trace(session.id, {nodeId: 'source', direction: 'out', depth: 3});
    expect(session.traversal.nodes.map((item) => item.id)).toEqual(['source', 'target']);
    const task = h.service.runImpact(session.id, {nodeId: 'source', depth: 20});
    await h.tasks.wait(task.id); session = h.service.get(session.id);
    expect(session.impact.affectedRefs).toHaveLength(1);
    expect(h.tasks.view(task.id).resultRefs[0].id).toContain('lineage-impact');
  });

  it('curates without deleting evidence and only suppresses inference-only edges', async () => {
    const h = harness(); let session = await refreshed(h);
    const parsed = session.graph.edges.find((item) => item.origin === 'parsed_query');
    session = await h.service.curateEdge(session.id, {edgeId: parsed.id,
      type: 'derives', note: 'Reviewed with owner'});
    const curated = session.content.curatedEdges[0];
    expect(curated.evidence.map((item) => item.origin)).toEqual(['parsed_query', 'user_curated']);
    expect(session.dirty).toBe(true); expect(session.history.at(-1).action).toBe('curate');
    const declared = session.graph.edges.find((item) => item.origin === 'provider_declared');
    await expect(h.service.suppressInference(session.id, {edgeId: declared.id}))
      .rejects.toThrow('inference-only');
    const inference = session.graph.edges.find((item) => item.origin === 'parsed_query');
    const suppressed = await h.service.suppressInference(session.id, {edgeId: inference.id});
    expect(suppressed.content.suppressedInferenceRules).toHaveLength(1);
  });

  it('captures and compares exact temporal snapshots without mutating the graph', async () => {
    const h = harness(); let session = await refreshed(h);
    session = await h.service.snapshot(session.id, {snapshotId: 'before'});
    await h.service.curateEdge(session.id, {edgeId: session.graph.edges[0].id,
      type: 'depends_on', note: 'changed'});
    session = await h.service.snapshot(session.id, {snapshotId: 'after'});
    session = h.service.compareSnapshots(session.id, {leftId: 'before', rightId: 'after'});
    expect(session.snapshots).toHaveLength(2);
    expect(session.snapshotDiff.edges.added.length).toBeGreaterThan(0);
  });

  it('imports and losslessly exports OpenLineage through a task', async () => {
    const h = harness(); const session = h.service.create({});
    const event = {eventType: 'COMPLETE', eventTime: now(), producer: 'producer',
      schemaURL: 'schema', run: {runId: 'run'}, job: {namespace: 'demo', name: 'job'},
      inputs: [{namespace: 'demo', name: 'input', facets: {unknown: {keep: true}}}],
      outputs: [{namespace: 'demo', name: 'output'}]};
    const task = h.service.importOpenLineage(session.id, {events: event});
    await h.tasks.wait(task.id); const updated = h.service.get(session.id);
    expect(updated.graph.nodes).toHaveLength(3); expect(updated.imports).toHaveLength(1);
    expect(JSON.parse(h.service.exportOpenLineage(session.id))).toEqual([event]);
  });

  it('produces a non-authoritative DDN projection with stable source bindings', async () => {
    const h = harness(); const session = await refreshed(h);
    expect(h.service.openDDN(session.id)).toMatchObject({authoritative: false,
      source: {moduleId: 'cdeadmin.lineage', graphRevision: session.graph.revision}});
  });

  it('saves with optimistic asset version and indexes permission-filtered search', async () => {
    const h = harness(); const session = await refreshed(h);
    await h.service.save(session.id, {projectId: 'p', assetId: 'a', name: 'Lineage',
      path: 'lineage/a.json', expectedVersion: 1});
    expect(h.projectAssets.saveAsset).toHaveBeenCalledWith('p', 'a',
      expect.objectContaining({expected_version: 1, asset_type: 'cdeadmin.lineage.v1'}));
    expect((await h.search.search('source')).total).toBe(1);
    expect((await h.search.search('source', {context: {permissions: []}})).total).toBe(0);
    expect(h.service.get(session.id).dirty).toBe(false);
  });

  it('preserves project state and emits durable diagnostics on failure', () => {
    const h = harness(); const session = h.service.create({scopeRefs: [scope()]});
    h.service.reportError(session.id, new Error('provider offline'), 'provider_unavailable');
    expect(h.service.get(session.id)).toMatchObject({state: 'disconnected',
      content: session.content, error: 'provider offline'});
    expect(h.diagnostics.list({origin: 'cdeadmin.lineage'})[0])
      .toMatchObject({code: 'provider_unavailable'});
  });
});
