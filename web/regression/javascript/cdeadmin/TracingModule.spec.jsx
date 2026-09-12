import React from 'react';
import Ajv2020 from 'ajv/dist/2020';
import {CommandRegistry} from 'sources/cdeadmin_ui/commands/CommandRegistry';
import {
  CapabilityRegistry, ContributionRegistry, ServiceRegistry,
} from 'sources/cdeadmin_ui/platform/PlatformRegistry';
import {PLATFORM_SERVICE_IDS, registerCorePlatformServices} from 'sources/cdeadmin_ui/platform/PlatformServices';
import {ModuleRegistry} from 'sources/cdeadmin_ui/platform/ModuleRegistry';
import {SurfaceRegistry} from 'sources/cdeadmin_ui/platform/SurfaceRegistry';
import {ToolFactoryRegistry} from 'sources/cdeadmin_ui/workspace/ToolRegistry';
import {MODULE_MANIFEST_SCHEMA, SCREEN_SPEC_SCHEMA} from 'sources/cdeadmin_ui';
import {
  TRACING_ASSET_TYPE, TRACING_MODULE_ID, TRACING_SERVICE_ID,
  TracingWorkspace, registerTracingModule,
} from 'sources/cdeadmin_ui/modules/tracing';
import manifest from 'sources/cdeadmin_ui/modules/tracing/specification/09_tracing.manifest.json';
import primary from 'sources/cdeadmin_ui/modules/tracing/specification/09_tracing_primary.screen.json';
import searchScreen from 'sources/cdeadmin_ui/modules/tracing/specification/09_tracing_01_trace_search.screen.json';
import waterfallScreen from 'sources/cdeadmin_ui/modules/tracing/specification/09_tracing_02_trace_waterfall.screen.json';
import inspectorScreen from 'sources/cdeadmin_ui/modules/tracing/specification/09_tracing_03_span_inspector.screen.json';
import mapScreen from 'sources/cdeadmin_ui/modules/tracing/specification/09_tracing_04_service_resource_map.screen.json';
import correlationScreen from 'sources/cdeadmin_ui/modules/tracing/specification/09_tracing_05_query_correlation.screen.json';
import ingestionScreen from 'sources/cdeadmin_ui/modules/tracing/specification/09_tracing_06_ingestion_sampling.screen.json';
import {definition} from './TracingTestUtils';

function architecture(client={asset: jest.fn(), update: jest.fn(), create: jest.fn()}) {
  const services = new ServiceRegistry(); registerCorePlatformServices(services);
  services.register({id: 'project.assets', factory: () => client});
  const registries = {services, capabilities: new CapabilityRegistry(),
    surfaces: new SurfaceRegistry(new ToolFactoryRegistry()), commands: new CommandRegistry(),
    inspector: new ContributionRegistry('inspector'), toolbox: new ContributionRegistry('toolbox'),
    status: new ContributionRegistry('status'), activity: new ContributionRegistry('activity'),
    bottom: new ContributionRegistry('bottom')};
  const modules = new ModuleRegistry(registries); registerTracingModule({modules, services});
  return {...registries, modules, client};
}

describe('Distributed Tracing module registration', () => {
  test('validates the exact manifest and all seven screen contracts', () => {
    const ajv = new Ajv2020({strict: false});
    const validateManifest = ajv.compile(MODULE_MANIFEST_SCHEMA);
    expect(validateManifest(manifest)).toBe(true); expect(validateManifest.errors).toBeNull();
    const validateScreen = ajv.compile(SCREEN_SPEC_SCHEMA);
    for(const item of [primary, searchScreen, waterfallScreen, inspectorScreen,
      mapScreen, correlationScreen, ingestionScreen]) {
      expect(validateScreen(item)).toBe(true); expect(validateScreen.errors).toBeNull();
      expect(item.spec_gaps).toEqual([]);
    }
  });

  test('keeps exact identities, counts, tasks, events and drag safety', () => {
    expect(manifest).toMatchObject({moduleId: TRACING_MODULE_ID, specVersion: '1.0',
      committedFirstParty: true, assetTypes: [TRACING_ASSET_TYPE]});
    expect(manifest.surfaces).toHaveLength(6); expect(manifest.commands).toHaveLength(8);
    expect(manifest.taskTypes).toEqual(['trace.search.large', 'trace.aggregate.service_map',
      'trace.export']);
    expect(manifest.events).toEqual(['trace.ingested', 'trace.source.degraded',
      'trace.retention.expired']);
    expect(manifest.rules.directProviderMutationFromDragDrop).toBe(false);
  });

  test('registers every surface, command and shell contribution', async () => {
    const host = architecture(); await host.modules.activate(TRACING_MODULE_ID);
    expect(host.surfaces.list({assetType: TRACING_ASSET_TYPE})).toHaveLength(6);
    expect(host.commands.list().map((item) => item.id))
      .toEqual(manifest.commands.map((item) => item.id));
    expect(host.activity.resolve().map((item) => item.id)).toEqual(['activity.tracing']);
    expect(host.inspector.resolve({surfaceId: 'tracing.trace_search'})).toHaveLength(1);
    expect(host.bottom.resolve({surfaceId: 'tracing.trace_waterfall'})).toHaveLength(4);
    expect(host.status.resolve({surfaceId: 'tracing.span_inspector'})).toHaveLength(1);
  });

  test('registers the complete runtime and all shared task runners', async () => {
    const host = architecture(); await host.modules.activate(TRACING_MODULE_ID);
    const service = await host.services.resolve(TRACING_SERVICE_ID);
    expect(service).toEqual(expect.objectContaining({ingest: expect.any(Function),
      searchTraces: expect.any(Function), searchLarge: expect.any(Function),
      aggregateMap: expect.any(Function), export: expect.any(Function), save: expect.any(Function)}));
    const tasks = await host.services.resolve(PLATFORM_SERVICE_IDS.TASKS);
    expect([...tasks.runners.keys()].sort()).toEqual(['trace.aggregate.service_map',
      'trace.export', 'trace.search.large']);
  });

  test('restores the exact authored asset into every detachable surface', async () => {
    const client = {asset: jest.fn().mockResolvedValue({asset_type: TRACING_ASSET_TYPE,
      asset_id: 'trace-one', content: definition()}), update: jest.fn(), create: jest.fn()};
    const host = architecture(client); await host.modules.activate(TRACING_MODULE_ID);
    for(const surface of manifest.surfaces) {
      const descriptor = host.surfaces.descriptor(`tracing.${surface.id}`, {
        toolInstanceId: surface.id, projectId: 'p', restoreRef: 'trace-one'});
      const element = await host.surfaces.restore(descriptor,
        {services: {'project.assets': client}, commands: host.commands});
      expect(React.isValidElement(element)).toBe(true); expect(element.type).toBe(TracingWorkspace);
      expect(element.props.surface).toBe(surface.id);
    }
    expect(client.asset).toHaveBeenCalledTimes(6);
  });

  test('enforces all five tracing permissions independently', async () => {
    const host = architecture(); await host.modules.activate(TRACING_MODULE_ID);
    const service = await host.services.resolve(TRACING_SERVICE_ID);
    const session = service.create({content: definition()}); const viewOnly = {
      tracing: service, currentUser: {permissions: ['trace.view']}};
    for(const [id, args] of [
      ['trace.export.otel', {}], ['trace.source.configure', {source: definition().sourceConfigs[0]}],
      ['trace.sampling.update', {policy: definition().samplingPolicies[0]}]]) {
      await expect(host.commands.execute(id, {sessionId: session.id, ...args}, viewOnly))
        .rejects.toMatchObject({code: 'permission_denied'});
    }
    expect(host.commands.get('trace.open').permission).toEqual(['trace.view']);
  });

  test('rejects undocumented and malformed command arguments', async () => {
    const host = architecture(); await host.modules.activate(TRACING_MODULE_ID);
    const service = await host.services.resolve(TRACING_SERVICE_ID);
    const session = service.create({content: definition()}); const context = {tracing: service,
      currentUser: {permissions: ['trace.view', 'trace.export', 'trace.configure_source', 'trace.admin']}};
    await expect(host.commands.execute('trace.search', {sessionId: session.id, guessed: true}, context))
      .rejects.toMatchObject({code: 'invalid_arguments'});
    await expect(host.commands.execute('trace.search', {sessionId: session.id, pageSize: 0}, context))
      .rejects.toMatchObject({code: 'invalid_arguments'});
    await expect(host.commands.execute('trace.resource.open', {sessionId: session.id,
      traceId: 'one'}, context)).rejects.toMatchObject({code: 'invalid_arguments'});
  });

  test('blocks dirty close and atomically removes contributions', async () => {
    const host = architecture(); await host.modules.activate(TRACING_MODULE_ID);
    const descriptor = host.surfaces.descriptor('tracing.trace_search', {toolInstanceId: 'one'});
    await expect(host.surfaces.toolRegistry.canClose(descriptor, {dirty: true}))
      .resolves.toEqual({allowed: false, reason: 'Save or discard tracing asset changes first.'});
    await host.modules.deactivate(TRACING_MODULE_ID);
    expect(host.commands.list()).toEqual([]); expect(host.surfaces.list()).toEqual([]);
  });
});
