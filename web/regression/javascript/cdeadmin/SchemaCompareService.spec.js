/////////////////////////////////////////////////////////////
// Schema Comparison orchestration, task, event and apply gates.
/////////////////////////////////////////////////////////////

import {
  DiagnosticsService, FederatedSearchService, PlatformEventService,
  RelationshipGraphService, TaskExecutionService,
} from 'sources/cdeadmin_ui/platform';
import {
  SchemaCompareAdapterRegistry, SchemaCompareService,
} from 'sources/cdeadmin_ui/modules/schema_compare';

function ref(provider, name) {
  return {schema: 'cdeadmin.resource-ref.v1', provider, connection: 'local', scope: '/',
    kind: 'database', nativeIdentity: name,
    canonical: `cde-resource://${provider}/local/%2F/database/${name}`};
}

function snapshot(sourceRef, id, names) {
  return {schema: 'cdeadmin.schema-compare.snapshot.v1', schemaVersion: 1,
    snapshotId: id, sourceRef, revision: `${id}-r1`, capturedAt: '2026-09-11T12:00:00Z',
    providerId: sourceRef.provider, providerVersion: '5.0.4',
    supportState: 'supported_native', evidence: {catalog: true}, warnings: [],
    nativeDetails: {}, objects: names.map((name) => ({id: `${id}-${name}`, kind: 'table',
      qualifiedName: name, normalized: {name}, native: {name}, children: [],
      dependencies: []}))};
}

function response(value, updates={}) {
  return {supportState: 'supported_native', providerVersion: '5.0.4',
    evidence: {tested: true}, warnings: [], nativeDetails: {}, value, ...updates};
}

function adapter(snapshots, calls) {
  return {
    captureSchemaSnapshot: jest.fn(async (reference) => response(snapshots[reference.canonical])),
    normalizeForCompare: jest.fn(async (value) => response(value)),
    compareNativeProperties: jest.fn(async () => response({comparable: true})),
    renderChangeOperation: jest.fn(async (operation) => response({
      nativeStatement: `${operation.action.toUpperCase()} ${operation.qualifiedName}`,
    })),
    validateChangePlan: jest.fn(async (plan) => response({valid: true,
      targetRevision: 'target-r1', partialSelectionValidated: plan.partialSelection,
      applySupported: true,
      materializedOperationIds: plan.operations.map((item) => item.id)})),
    applyChangePlan: jest.fn(async (plan) => {
      calls.push(plan.id); return response({auditRef: 'audit:schema-compare:1',
        operationResults: plan.operations.map((item) => ({id: item.id, applied: true}))});
    }),
  };
}

function harness() {
  const tasks = new TaskExecutionService({now: () => '2026-09-11T12:00:00Z'});
  const relationships = new RelationshipGraphService();
  const search = new FederatedSearchService(); const events = new PlatformEventService();
  const diagnostics = new DiagnosticsService();
  const projectAssets = {asset: jest.fn(), saveAsset: jest.fn(async () => ({version: 1}))};
  const adapters = new SchemaCompareAdapterRegistry(); const calls = [];
  const leftRef = ref('firebird', 'left'); const rightRef = ref('firebird', 'right');
  const snapshots = {[leftRef.canonical]: snapshot(leftRef, 'left', ['A', 'B']),
    [rightRef.canonical]: snapshot(rightRef, 'right', ['A'])};
  const native = adapter(snapshots, calls); adapters.register('firebird', native);
  const service = new SchemaCompareService({adapters, tasks, relationships, search,
    projectAssets, events, diagnostics, now: () => '2026-09-11T12:00:00Z'});
  return {service, tasks, relationships, search, events, diagnostics, projectAssets,
    adapters, native, calls, leftRef, rightRef};
}

describe('SchemaCompareAdapterRegistry', () => {
  it('requires the complete minimum adapter contract without inferred support', () => {
    const registry = new SchemaCompareAdapterRegistry();
    expect(() => registry.register('firebird', {})).toThrow('captureSchemaSnapshot');
    expect(() => registry.get('firebird')).toThrow('no explicit');
  });
});

describe('SchemaCompareService', () => {
  it('captures, compares, emits events, indexes search and records relationships', async () => {
    const h = harness(); const completed = [];
    h.events.subscribe('schema_compare.completed', (event) => completed.push(event));
    const session = h.service.create({leftRef: h.leftRef, rightRef: h.rightRef});
    const result = await h.service.run(session.id);
    expect(result.counts.identical).toBe(1); expect(result.counts.left_only).toBe(1);
    expect(h.native.captureSchemaSnapshot).toHaveBeenCalledTimes(2);
    expect(h.tasks.list({type: 'schema_compare.scan'})).toHaveLength(1);
    expect(h.tasks.list({type: 'schema_compare.diff'})).toHaveLength(1);
    expect(completed).toHaveLength(1);
    expect(h.relationships.snapshot().edges).toHaveLength(2);
    expect((await h.search.search('B')).total).toBe(1);
    expect(h.service.get(session.id).state).toBe('ready');
  });

  it('persists explicit mapping decisions and makes prior results stale', async () => {
    const h = harness(); const changed = [];
    h.events.subscribe('schema_compare.mapping.changed', (event) => changed.push(event));
    const session = h.service.create({leftRef: h.leftRef, rightRef: h.rightRef});
    await h.service.run(session.id);
    await h.service.setMapping(session.id, {mappingId: 'm1', leftId: 'left-B',
      rightId: 'right-A', category: 'behavioral_difference', accepted: true});
    expect(h.service.get(session.id)).toMatchObject({state: 'stale', result: null});
    expect(h.service.get(session.id).content.acceptedMappings).toHaveLength(1);
    expect(changed).toHaveLength(1);
  });

  it('generates, validates and applies only the exact explicit live target plan', async () => {
    const h = harness(); const applied = [];
    h.events.subscribe('schema_compare.apply.completed', (event) => applied.push(event));
    const session = h.service.create({leftRef: h.leftRef, rightRef: h.rightRef});
    await h.service.run(session.id);
    await expect(h.service.generatePlan(session.id, {targetRef: ref('firebird', 'other')}))
      .rejects.toThrow('exactly identify');
    const plan = await h.service.generatePlan(session.id, {targetRef: h.rightRef});
    expect(plan.operations).toHaveLength(1);
    await expect(h.service.applyPlan(session.id)).rejects.toThrow('must pass validation');
    const validation = await h.service.validatePlan(session.id);
    expect(validation.valid).toBe(true);
    const result = await h.service.applyPlan(session.id);
    expect(result.auditRef).toBe('audit:schema-compare:1');
    expect(h.calls).toEqual([plan.id]); expect(applied).toHaveLength(1);
    expect(h.service.get(session.id).state).toBe('stale');
  });

  it('requires exact confirmation for destructive plans and durable audit evidence', async () => {
    const h = harness();
    const session = h.service.create({leftRef: h.rightRef, rightRef: h.leftRef});
    await h.service.run(session.id);
    const plan = await h.service.generatePlan(session.id, {targetRef: h.leftRef});
    expect(plan.destructive).toBe(true); await h.service.validatePlan(session.id);
    await expect(h.service.applyPlan(session.id, {confirmation: 'wrong'}))
      .rejects.toThrow('exact plan ID');
    h.native.applyChangePlan.mockResolvedValueOnce(response({operationResults: []}));
    await expect(h.service.applyPlan(session.id, {confirmation: plan.id}))
      .rejects.toThrow('audit reference');
  });

  it('revalidates partial selections and exports versioned provider-native output', async () => {
    const h = harness();
    const session = h.service.create({leftRef: h.leftRef, rightRef: h.rightRef});
    const comparison = await h.service.run(session.id);
    const chosen = comparison.differences.find((item) => item.classification === 'left_only');
    await h.service.generatePlan(session.id, {targetRef: h.rightRef,
      selectedDiffIds: [chosen.id]});
    const validation = await h.service.validatePlan(session.id);
    expect(validation.partialSelectionValidated).toBe(true);
    expect(JSON.parse(h.service.exportPlan(session.id))).toMatchObject({
      schema: 'cdeadmin.schema-compare.export.v1', authenticationMaterial: 'none',
    });
  });

  it('saves optimistically, rejects unsupported asset sources and reports diagnostics', async () => {
    const h = harness(); const session = h.service.create({
      leftRef: h.leftRef, rightRef: h.rightRef,
    });
    await h.service.save(session.id, {schemaVersion: 1, projectId: 'p', assetId: 'a',
      assetType: 'cdeadmin.schema_compare.v1', assetVersion: 0,
      path: 'compare/a.json', displayName: 'A'});
    expect(h.projectAssets.saveAsset).toHaveBeenCalledWith('p', 'a',
      expect.objectContaining({expected_version: 0}));
    const bad = h.service.create({leftRef: {schemaVersion: 1, projectId: 'p',
      assetId: 'x', assetType: 'unknown.asset', assetVersion: 1,
      path: 'x.json', displayName: 'X'}, rightRef: h.rightRef});
    h.projectAssets.asset.mockResolvedValue({asset_type: 'unknown.asset'});
    await expect(h.service.scan(bad.id)).rejects.toThrow('no snapshot resolver');
    h.service.reportError(bad.id, new Error('failed'), 'provider_unavailable');
    expect(h.diagnostics.list({origin: 'cdeadmin.schema_compare'})
      .map((item) => item.code).sort()).toEqual(['provider_unavailable', 'task_failed']);
  });
});
