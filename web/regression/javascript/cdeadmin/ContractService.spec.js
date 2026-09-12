/////////////////////////////////////////////////////////////
// Data Contract provider, task, lifecycle and persistence gates.
/////////////////////////////////////////////////////////////

import {
  DiagnosticsService, FederatedSearchService, PlatformEventService,
  RelationshipGraphService, TaskExecutionService,
} from 'sources/cdeadmin_ui/platform';
import {
  ContractAdapterRegistry, ContractService, validateContractProviderResult,
} from 'sources/cdeadmin_ui/modules/data_contract';

const now = () => '2026-09-11T14:00:00Z';
const resource = (provider='mongodb') => ({schema: 'cdeadmin.resource-ref.v1', provider,
  canonical: `cde-resource://${provider}/local/database/sales/collection/orders`});
const element = () => ({id: 'orders', name: 'Orders', logicalType: 'document',
  description: 'Order document', constraints: {}, physicalDefinition: {}, extensions: {}});
const binding = (provider='mongodb') => ({id: 'orders-production', elementId: 'orders',
  targetRef: resource(provider), environment: 'production', bindingStatus: 'observed',
  observedRevision: 'catalog:41', nativeDetails: {collection: 'orders'}});
const content = (updates={}) => ({name: 'Orders contract', contractVersion: '1.0.0',
  status: 'draft', domain: 'sales', description: 'Governed order documents',
  elements: [element()], bindings: [binding()], qualityObligations: [],
  sla: [{id: 'freshness', measure: 'freshness', target: 5, comparison: '<=', unit: 'minutes',
    window: {rolling: '15m'}, description: 'Freshness'}], team: [], roles: [], servers: [],
  authoritativeDefinitions: [], extensions: {}, ...updates});

function envelope(value, updates={}) {
  return {supportState: 'supported_native', providerVersion: '8.2.6',
    evidence: {serverBuild: '8.2.6'}, warnings: [], nativeDetails: {},
    readCapabilities: ['map', 'compare', 'observe', 'classify'], writeCapabilities: [],
    discoveryCapabilities: ['schema'], nativeMechanisms: ['listCollections'],
    versionConstraints: ['8.2.6'], limitations: [], runtimeEvidence: {live: true},
    value, ...updates};
}

const observation = (compliant=true, detail='') => ({compliant,
  details: detail ? [detail] : [], evidence: [{revision: 'catalog:42'}]});
const drift = (state='in_sync') => ({state, differences: state === 'in_sync' ? [] : [{
  path: '/elements/orders', kind: 'changed', contractValue: 'document',
  providerValue: 'timeseries', evidence: {revision: 'catalog:42'}}],
observedRevision: 'catalog:42'});

function adapter({compliant=true, driftState='in_sync'}={}) {
  return {
    mapResourceToContractSchema: jest.fn(async () => envelope({elements: [{
      id: 'orders', logicalType: 'document'}], observedRevision: 'catalog:42'})),
    compareContractToResource: jest.fn(async () => envelope({
      compliance: observation(compliant, compliant ? '' : 'Provider schema changed'),
      drift: drift(driftState)})),
    observeSlaMetric: jest.fn(async () => envelope(observation(true))),
    validateClassification: jest.fn(async () => envelope(observation(true))),
  };
}

function harness({withAdapter=true, providerOptions, commands=null}={}) {
  const tasks = new TaskExecutionService({now}); const relationships = new RelationshipGraphService();
  const search = new FederatedSearchService(); const events = new PlatformEventService();
  const diagnostics = new DiagnosticsService(); const adapters = new ContractAdapterRegistry();
  const native = adapter(providerOptions); if(withAdapter) adapters.register('mongodb', native);
  const projectAssets = {saveAsset: jest.fn(async () => ({version: 3}))};
  const service = new ContractService({tasks, relationships, search, events, diagnostics,
    adapters, projectAssets, commands, now});
  return {service, tasks, relationships, search, events, diagnostics, adapters,
    native, projectAssets};
}

describe('Contract provider admission', () => {
  it('requires every minimum interface operation and never infers support', () => {
    const registry = new ContractAdapterRegistry();
    expect(() => registry.register('mongodb', {})).toThrow('mapResourceToContractSchema');
    expect(registry.get('mongodb')).toBeNull(); registry.register('mongodb', adapter());
    expect(registry.list()).toEqual(['mongodb']);
    expect(() => registry.register('mongodb', adapter())).toThrow('already registered');
  });

  it('requires the complete evidence envelope and rejects empty or secret-bearing success', () => {
    expect(() => validateContractProviderResult({supportState: 'supported_native'},
      'mongodb', 'compare')).toThrow('warnings[]');
    expect(() => validateContractProviderResult(envelope(undefined), 'mongodb', 'compare'))
      .toThrow('empty success');
    expect(() => validateContractProviderResult(envelope({}, {nativeDetails: {password: 'x'}}),
      'mongodb', 'compare')).toThrow('Raw credential');
  });

  it.each(['supported_native', 'supported_via_cdeadmin',
    'supported_via_external_adapter'])('preserves the explicit %s support class', (supportState) => {
    expect(validateContractProviderResult(envelope({mapped: true}, {supportState}),
      'mongodb', 'compare')).toMatchObject({supportState, value: {mapped: true},
      runtimeEvidence: {live: true}});
  });

  it.each(['read_only', 'partial', 'unsupported', 'unknown'])(
    'does not promote the explicit %s support class to success', (supportState) => {
      expect(validateContractProviderResult(envelope({mapped: false}, {supportState}),
        'mongodb', 'compare')).toMatchObject({supportState});
    });
});

describe('ContractService', () => {
  it('creates, validates, edits and binds without losing logical object semantics', () => {
    const h = harness(); let session = h.service.create(content());
    expect(session.validation.valid).toBe(true);
    session = h.service.replaceDefinition(session.id, {...session.content,
      description: 'Revised governed documents'});
    expect(session).toMatchObject({dirty: true,
      content: {elements: [expect.objectContaining({logicalType: 'document'})]}});
    session = h.service.bindResource(session.id, {...binding(), id: 'orders-test',
      environment: 'test'});
    expect(session.content.bindings).toHaveLength(2);
    expect(h.relationships.snapshot().edges).toEqual(expect.arrayContaining([
      expect.objectContaining({relation: 'binds_to', origin: 'project_declared'}),
    ]));
  });

  it('removes stale relationship nodes and edges when authored links change or unload', () => {
    const h = harness(); const session = h.service.create(content({elements: [{...element(),
      authoritativeDefinitionRefs: [{schema: 'cdeadmin.external-ref.v1', id: 'glossary:orders'}]}],
    authoritativeDefinitions: [{id: 'orders-api', type: 'openapi',
      uri: 'https://example.invalid/orders.yaml'}], qualityObligations: [{
      id: 'quality-one', elementId: 'orders', severity: 'critical',
      qualityRef: {schema: 'cdeadmin.asset-ref.v1', projectId: 'p', assetId: 'quality'},
      threshold: {}, description: ''}]}));
    expect(h.relationships.snapshot().edges.map((item) => item.relation).sort()).toEqual([
      'binds_to', 'defined_by', 'documented_by', 'governed_by_quality',
    ]);
    h.service.replaceDefinition(session.id, {...session.content, bindings: [],
      qualityObligations: [], authoritativeDefinitions: [], elements: [element()]});
    let graph = h.relationships.snapshot();
    expect(graph.edges).toEqual([]);
    expect(graph.nodes.map((item) => item.id)).toEqual([
      `contract:${session.id}`, `contract:${session.id}:element:orders`,
    ]);
    h.service.dispose(); graph = h.relationships.snapshot();
    expect(graph).toMatchObject({nodes: [], edges: []});
  });

  it('imports ODCS through a shared task, preserves its version, and never auto-activates', async () => {
    const h = harness(); const session = h.service.create({});
    const task = h.service.importODCS(session.id, {apiVersion: 'v3.1', kind: 'DataContract',
      name: 'Events', version: '7', status: 'active', domain: 'event',
      description: 'Event documents', schema: [{id: 'event', name: 'Event', type: 'document'}]});
    await h.tasks.wait(task.id); await Promise.resolve(); const imported = h.service.get(session.id);
    expect(imported).toMatchObject({dirty: true, content: {contractVersion: '7',
      status: 'proposed', elements: [expect.objectContaining({logicalType: 'document'})]},
    interoperability: {apiVersion: 'v3.1'}});
  });

  it('compares metadata without overwriting the authored contract and emits drift', async () => {
    const h = harness({providerOptions: {driftState: 'provider_changed'}}); const changes = [];
    h.events.subscribe('contract.drift.detected', (event) => changes.push(event));
    const session = h.service.create(content()); const before = session.content;
    const comparison = await h.service.syncMetadata(session.id, {bindingId: 'orders-production'});
    expect(comparison.comparisons[0]).toMatchObject({drift: {state: 'provider_changed'},
      mappedSchema: {elements: [expect.objectContaining({logicalType: 'document'})]}});
    expect(h.service.get(session.id).content).toEqual(before);
    expect(changes).toHaveLength(1);
  });

  it('runs live compliance as a task while keeping structural validity independent', async () => {
    const h = harness({providerOptions: {compliant: false, driftState: 'provider_changed'}});
    const failed = []; h.events.subscribe('contract.compliance.failed', (event) => failed.push(event));
    const session = h.service.create(content()); const task = h.service.compliance(session.id);
    const run = await h.tasks.wait(task.id); await Promise.resolve();
    expect(run.summary).toMatchObject({compliant: false, partial: true});
    expect(run.summary.dimensions.find((item) => item.dimension === 'contract_structure'))
      .toMatchObject({compliant: true});
    expect(run.summary.dimensions.find((item) => item.dimension === 'schema'))
      .toMatchObject({compliant: false});
    expect(failed).toHaveLength(1);
    expect(h.tasks.view(task.id).resultRefs[0]).toMatchObject({id: run.id});
  });

  it('fails closed for unregistered providers and preserves authored state', async () => {
    const h = harness({withAdapter: false}); const session = h.service.create(content({
      bindings: [binding('unregistered')]})); const task = h.service.compliance(session.id);
    await expect(h.tasks.wait(task.id)).rejects.toThrow('capability_missing'); await Promise.resolve();
    expect(h.service.get(session.id)).toMatchObject({state: 'partial', content: session.content,
      providerStatuses: [expect.objectContaining({supportState: 'unknown'})]});
  });

  it('requires valid structure and activation authority, and audits reverse transitions', () => {
    const h = harness(); const invalid = h.service.create({name: 'Invalid'});
    expect(() => h.service.setStatus(invalid.id, 'active', {currentUser: {
      permissions: ['contract.activate']}})).toThrow('configuration_invalid');
    const session = h.service.create(content({id: 'valid'}));
    expect(() => h.service.setStatus(session.id, 'active')).toThrow('permission_denied');
    let active = h.service.setStatus(session.id, 'active', {currentUser: {
      permissions: ['contract.activate']}});
    expect(active.content.status).toBe('active');
    expect(() => h.service.setStatus(session.id, 'draft')).toThrow('audit reason');
    active = h.service.setStatus(session.id, 'draft', {reason: 'Schema redesign'});
    expect(active.history.at(-1)).toMatchObject({direction: 'reverse', reason: 'Schema redesign'});
  });

  it('creates immutable named versions and compares exact source paths', () => {
    const h = harness(); const session = h.service.create(content());
    h.service.replaceDefinition(session.id, {...session.content, description: 'Changed'});
    const next = h.service.createVersion(session.id, '2.0.0');
    expect(next.versions.map((item) => item.contractVersion)).toEqual(['1.0.0', '2.0.0']);
    expect(h.service.compareVersions(session.id, '1.0.0', '2.0.0').versionDiff)
      .toMatchObject({identical: false});
    expect(() => h.service.createVersion(session.id, '2.0.0')).toThrow('conflict');
  });

  it('exports declared ODCS safely, saves optimistically and contributes typed search', async () => {
    const h = harness(); const session = h.service.create(content({extensions: {
      'org.opendatacontractstandard.import': {apiVersion: 'v3.1', kind: 'DataContract',
        unknownTopLevel: {pricing: {currency: 'CAD'}}}}}));
    expect(h.service.exportODCS(session.id)).toMatchObject({apiVersion: 'v3.1',
      document: {pricing: {currency: 'CAD'}}});
    await h.service.save(session.id, {projectId: 'p', assetId: 'orders', name: 'Orders',
      path: 'contracts/orders.json', expectedVersion: 2});
    expect(h.projectAssets.saveAsset).toHaveBeenCalledWith('p', 'orders',
      expect.objectContaining({asset_type: 'cdeadmin.contract.v1', expected_version: 2}));
    expect(h.service.get(session.id).dirty).toBe(false);
    expect((await h.search.search('Order document')).groups.flatMap((group) => group.results))
      .toEqual(expect.arrayContaining([expect.objectContaining({type: 'contract.element'})]));
    expect((await h.search.search('Order document', {context: {permissions: []}})).total).toBe(0);
  });

  it('preserves dirty authored state when optimistic persistence conflicts', async () => {
    const h = harness(); const session = h.service.create(content());
    h.service.replaceDefinition(session.id, {...session.content, description: 'Unsaved revision'});
    h.projectAssets.saveAsset.mockRejectedValueOnce(new Error('conflict: expected version 4'));
    await expect(h.service.save(session.id, {projectId: 'p', assetId: 'orders', name: 'Orders',
      path: 'contracts/orders.json', expectedVersion: 4})).rejects.toThrow('conflict');
    expect(h.service.get(session.id)).toMatchObject({dirty: true,
      content: {description: 'Unsaved revision'}});
  });

  it('registers metadata comparison tasks and supports explicit shared cancellation', async () => {
    const h = harness(); const session = h.service.create(content());
    const task = h.service.compareMetadata(session.id, {bindingId: 'orders-production'},
      {currentUser: {id: 'user-42'}});
    expect(h.tasks.view(task.id).audit).toEqual({moduleId: 'cdeadmin.contract',
      operation: 'contract.metadata.compare', actor: 'user-42', environments: ['production'],
      confirmationReference: null});
    expect(await h.tasks.wait(task.id)).toMatchObject({comparisons: expect.any(Array)});
    const next = h.service.compareMetadata(session.id, {bindingId: 'orders-production'});
    expect(h.service.cancel(session.id, next.id)).toBe(true);
    await expect(h.tasks.wait(next.id)).rejects.toMatchObject({name: 'AbortError'});
  });

  it('maps provider and recovery failures to distinct durable states and diagnostics', () => {
    const h = harness(); const session = h.service.create(content());
    h.service.reportError(session.id, new Error('offline'), 'provider_unavailable');
    expect(h.service.get(session.id)).toMatchObject({state: 'disconnected',
      content: session.content, error: 'offline'});
    expect(h.diagnostics.list({origin: 'cdeadmin.contract'})[0])
      .toMatchObject({code: 'provider_unavailable'});
  });
});
