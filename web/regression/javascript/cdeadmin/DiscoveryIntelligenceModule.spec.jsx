/////////////////////////////////////////////////////////////
// Activated Discovery Intelligence module and authority gates.
/////////////////////////////////////////////////////////////

import React from 'react';
import {CommandRegistry} from 'sources/cdeadmin_ui/commands/CommandRegistry';
import {CapabilityRegistry, ContributionRegistry, PermissionRegistry,
  ServiceRegistry} from 'sources/cdeadmin_ui/platform/PlatformRegistry';
import {registerCorePlatformServices} from
  'sources/cdeadmin_ui/platform/PlatformServices';
import {ModuleRegistry} from 'sources/cdeadmin_ui/platform/ModuleRegistry';
import {SurfaceRegistry} from 'sources/cdeadmin_ui/platform/SurfaceRegistry';
import {ToolFactoryRegistry} from
  'sources/cdeadmin_ui/workspace/ToolRegistry';
import {AIReadToolRegistry} from
  'sources/cdeadmin_ui/modules/ai_interface/AIToolPublication';
import {DISCOVERY_COMMAND_CATALOG, DISCOVERY_INTELLIGENCE_MANIFEST,
  DISCOVERY_INTELLIGENCE_MODULE_ID, DISCOVERY_PERMISSION_CATALOG} from
  'sources/cdeadmin_ui/specifications/ai_discovery_zero_grey';
import {DISCOVERY_ADMINISTRATION_ASSET_TYPES,
  DISCOVERY_ENGAGEMENT_ASSET_TYPES, DISCOVERY_KNOWLEDGE_ASSET_TYPES,
  DiscoveryInterfaceWorkspace,
  DISCOVERY_INTELLIGENCE_RUNTIME_SERVICE_ID,
  registerDiscoveryIntelligenceModule} from
  'sources/cdeadmin_ui/modules/discovery_intelligence';

function projectAssets() {
  const stored = new Map(); const history = new Map();
  return {stored,
    saveAsset: jest.fn(async (projectId, assetId, request) => {
      const key = `${projectId}/${assetId}`; const prior = stored.get(key);
      if((prior?.version ?? 0) !== request.expected_version) {
        throw new Error('project asset conflict');
      }
      const value = {...request, project_id: projectId, asset_id: assetId,
        version: (prior?.version ?? 0) + 1};
      stored.set(key, value); history.set(key,
        [...(history.get(key) ?? []), value]); return value;
    }),
    asset: jest.fn(async (projectId, assetId) => stored.get(
      `${projectId}/${assetId}`)),
    revisions: jest.fn(async (projectId, assetId) => history.get(
      `${projectId}/${assetId}`) ?? []),
    deleteAsset: jest.fn(async (projectId, assetId) => stored.delete(
      `${projectId}/${assetId}`)),
  };
}

function architecture(options={}) {
  const services = new ServiceRegistry(); registerCorePlatformServices(services);
  const client = projectAssets(); services.register({id: 'project.assets',
    factory: () => client});
  const registries = {services, capabilities: new CapabilityRegistry(),
    surfaces: new SurfaceRegistry(new ToolFactoryRegistry()),
    commands: new CommandRegistry(), permissions: new PermissionRegistry(),
    inspector: new ContributionRegistry('inspector'),
    toolbox: new ContributionRegistry('toolbox'),
    status: new ContributionRegistry('status'),
    activity: new ContributionRegistry('activity'),
    bottom: new ContributionRegistry('bottom')};
  const modules = new ModuleRegistry(registries);
  registerDiscoveryIntelligenceModule({modules, services, options});
  return {...registries, modules, client};
}

function discoverySecurity(overrides={}) {
  return {admitDocument: () => true, admitAlias: () => true,
    admitSignal: () => true, admitFacet: () => true,
    admitGraphEdge: () => true, admitBusinessKnowledge: () => true,
    exposeCanonicalRef: () => true, ...overrides};
}

function indexItem() {
  return {kind: 'resource', reference: {provider: 'firebird',
    connection: 'local', scope: 'APP', kind: 'table',
    nativeIdentity: 'CUSTOMER', revision: 'metadata-1'}, document: {
    schemaVersion: 1, documentId: 'firebird-customer',
    entityClass: 'LIVE_RESOURCE', nativeKind: 'table',
    connectionEnvironment: 'test', name: 'CUSTOMER',
    qualifiedNames: ['APP.CUSTOMER'], authorizedAliases: [],
    description: 'Customer master data.', businessTerms: ['Customer'],
    synonyms: ['Client'], domainRefs: [], ownerRefs: ['team:data'],
    stewardRefs: [], tags: [], classification: 'INTERNAL', schemaSummary: {},
    nativeMetadataSummary: {}, trustSignals: {}, qualitySignals: {},
    freshnessSignals: {}, usageSignals: {}, lineageSignals: {},
    contractSignals: {}, certifications: [], deprecationState: null,
    accessState: {state: 'AVAILABLE'}, searchText: 'Customer master data',
    embeddingRefs: [], facetValues: {provider: 'firebird',
      nativeKind: 'table'}, updatedAt: '2026-09-12T12:00:00.000Z',
    sourceRevision: 'metadata-1', indexRevision: 'revision-1'}};
}

describe('Discovery Intelligence activation', () => {
  test('registers all exact commands and permissions', async () => {
    const host = architecture();
    await host.modules.activate(DISCOVERY_INTELLIGENCE_MODULE_ID);
    expect(host.commands.list().map(({id, task, authority}) =>
      ({id, task, authority}))).toEqual(DISCOVERY_COMMAND_CATALOG.commands.map(
      ({id, task, authority}) => ({id, task, authority})));
    expect(host.commands.list()).toHaveLength(50);
    expect(host.permissions.list({moduleId:
      DISCOVERY_INTELLIGENCE_MODULE_ID}).map(({id, description}) =>
      [id, description])).toEqual([...DISCOVERY_PERMISSION_CATALOG.permissions]
      .sort((left, right) => left[0].localeCompare(right[0])));
    expect(host.commands.list().every((command) =>
      command.permission.length === 1)).toBe(true);
  });

  test('activates all screens and shared-shell contributions', async () => {
    const host = architecture();
    await host.modules.activate(DISCOVERY_INTELLIGENCE_MODULE_ID);
    expect(host.surfaces.list()).toHaveLength(
      DISCOVERY_INTELLIGENCE_MANIFEST.screenCount);
    expect(host.activity.resolve().map(({id}) => id)).toEqual([
      'activity.discovery-intelligence']);
    expect(host.inspector.resolve({surfaceId:
      'cdeadmin.discovery_intelligence.discovery_home'})).toHaveLength(1);
    expect(host.bottom.resolve({surfaceId:
      'cdeadmin.discovery_intelligence.discovery_home'})).toHaveLength(1);
    expect(host.status.resolve({surfaceId:
      'cdeadmin.discovery_intelligence.discovery_home'})).toHaveLength(1);
    for(const surface of host.surfaces.list()) {
      const descriptor = host.surfaces.descriptor(surface.id,
        {toolInstanceId: surface.id});
      const element = await host.surfaces.restore(descriptor,
        {commands: host.commands});
      expect(React.isValidElement(element)).toBe(true);
      expect(element.type).toBe(DiscoveryInterfaceWorkspace);
      expect(element.props.screenId).toBe(surface.id);
    }
  });

  test('composes all required services and all nine project asset types',
    async () => {
      const host = architecture();
      await host.modules.activate(DISCOVERY_INTELLIGENCE_MODULE_ID);
      const runtime = await host.services.resolve(
        DISCOVERY_INTELLIGENCE_RUNTIME_SERVICE_ID);
      expect(Object.keys(runtime.platformServices)).toEqual(
        DISCOVERY_INTELLIGENCE_MANIFEST.platformServices);
      const assetTypes = [...Object.keys(DISCOVERY_KNOWLEDGE_ASSET_TYPES),
        ...Object.keys(DISCOVERY_ENGAGEMENT_ASSET_TYPES),
        ...Object.keys(DISCOVERY_ADMINISTRATION_ASSET_TYPES)];
      expect(assetTypes.sort()).toEqual(
        [...DISCOVERY_INTELLIGENCE_MANIFEST.assetTypes].sort());
      expect(Object.keys(runtime.assetAuthorities).sort()).toEqual(
        [...DISCOVERY_INTELLIGENCE_MANIFEST.assetTypes].sort());
      expect(runtime.screenData(
        'cdeadmin.discovery_intelligence.discovery_home').entityClasses)
        .toContain('LIVE_RESOURCE');
    });

  test('publishes and searches a real security-trimmed index revision', async () => {
    const host = architecture();
    await host.modules.activate(DISCOVERY_INTELLIGENCE_MODULE_ID);
    const runtime = await host.services.resolve(
      DISCOVERY_INTELLIGENCE_RUNTIME_SERVICE_ID);
    const discoverySecurityAuthority = discoverySecurity();
    await host.commands.execute('discovery.index.publish_revision', {input: {
      revision: 'revision-1', items: [indexItem()], embeddings: {},
      priorDocumentCount: 0, mandatorySource: false,
      deletionApproval: false}}, {discoveryIntelligence: runtime,
      discoverySecurity: discoverySecurityAuthority,
      currentUser: {permissions: ['discovery.manage_index']}});
    const result = await host.commands.execute('discovery.search.run', {
      input: {text: 'customer', pageSize: 25}}, {
      discoveryIntelligence: runtime,
      discoverySecurity: discoverySecurityAuthority,
      currentUser: {permissions: ['discovery.use']}});
    expect(result.results).toEqual([expect.objectContaining({
      name: 'CUSTOMER', provider: 'firebird'})]);
    expect(runtime.documents.resolve(result.results[0].canonicalRef))
      .toMatchObject({documentId: 'firebird-customer'});
  });

  test('enforces permission and capability gates without advertising adapters',
    async () => {
      const host = architecture();
      await host.modules.activate(DISCOVERY_INTELLIGENCE_MODULE_ID);
      const runtime = await host.services.resolve(
        DISCOVERY_INTELLIGENCE_RUNTIME_SERVICE_ID);
      await expect(host.commands.execute('discovery.search.run',
        {input: {text: 'customer'}}, {discoveryIntelligence: runtime,
          currentUser: {permissions: []}})).rejects.toMatchObject({
        code: 'permission_denied'});
      expect(runtime.capabilities()).toMatchObject({
        providerProvisioning: false, curationMutation: false,
        certificationReview: false, indexSourceAdapters: [],
        visibilityTesting: false, aiAssistance: false,
        queryLauncher: false, sbsqlLauncher: false});
      for(const commandId of ['discovery.access.provision',
        'discovery.index.refresh', 'discovery.curation.resolve',
        'discovery.visibility.test', 'discovery.open.sbsql',
        'discovery.ask_ai']) expect(host.commands.resolve(commandId, {
        discoveryIntelligence: runtime, currentUser: {permissions:
          [host.commands.get(commandId).permission[0]]}})).toMatchObject({
        visible: true, enabled: false});
    });

  test('publishes governed AI read tools only with real AI and egress authorities',
    async () => {
      const readTools = new AIReadToolRegistry();
      const host = architecture({aiInterface: {
        capabilities: () => ({discoveryAssistance: true}),
        requestDiscoveryAnalysis: jest.fn(),
        requestDiscoveryEnrichment: jest.fn(),
        toolCatalog: {readTools}}, aiEgress: {admit: () => true,
        trim: async (_toolId, value) => value,
        classify: () => 'INTERNAL'}});
      await host.modules.activate(DISCOVERY_INTELLIGENCE_MODULE_ID);
      expect(readTools.tools.size).toBe(8);
      await host.modules.deactivate(DISCOVERY_INTELLIGENCE_MODULE_ID);
      expect(readTools.tools.size).toBe(0);
    });

  test('runs declared background commands through TaskService', async () => {
    const host = architecture();
    await host.modules.activate(DISCOVERY_INTELLIGENCE_MODULE_ID);
    const runtime = await host.services.resolve(
      DISCOVERY_INTELLIGENCE_RUNTIME_SERVICE_ID);
    const first = indexItem(); const second = indexItem();
    second.reference = {...second.reference, nativeIdentity: 'ORDER'};
    second.document = {...second.document, documentId: 'firebird-order',
      name: 'ORDER', qualifiedNames: ['APP.ORDER'],
      searchText: 'Customer order'};
    await runtime.index.publish({revision: 'revision-1', items: [first, second]},
      {authorization: {}});
    const sourceRef = runtime.identities.create(first.reference).canonical;
    const targetRef = runtime.identities.create(second.reference).canonical;
    runtime.graph.publish({revision: 'graph-1', edges: [{edgeId: 'edge-1',
      fromRef: sourceRef, toRef: targetRef, type: 'depends_on', weight: 1,
      directed: true, evidence: {source: 'metadata'}, revision: 'graph-1'}]});
    const discoverySecurityAuthority = discoverySecurity();
    const task = await host.commands.execute('discovery.related.find', {
      input: {canonicalRefs: [sourceRef], limit: 10}}, {
      discoveryIntelligence: runtime,
      discoverySecurity: discoverySecurityAuthority,
      currentUser: {id: 'analyst', permissions: ['discovery.use']}});
    const tasks = await host.services.resolve('platform.tasks');
    await expect(tasks.wait(task.id)).resolves.toMatchObject({rows: [{
      entity: {canonicalRef: targetRef, name: 'ORDER'}}]});
  });

  test('removes module contributions on deactivation', async () => {
    const host = architecture();
    await host.modules.activate(DISCOVERY_INTELLIGENCE_MODULE_ID);
    await host.modules.deactivate(DISCOVERY_INTELLIGENCE_MODULE_ID);
    expect(host.commands.list()).toHaveLength(0);
    expect(host.surfaces.list()).toHaveLength(0);
    expect(host.permissions.list({moduleId:
      DISCOVERY_INTELLIGENCE_MODULE_ID})).toHaveLength(0);
    expect(host.activity.resolve()).toHaveLength(0);
  });
});
