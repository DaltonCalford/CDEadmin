/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {
  CAPABILITY_STATES, CapabilityRegistry, ContributionRegistry,
  DiagnosticsService, DIAGNOSTIC_STATES, PlatformEventService,
  PlatformRegistryError, ServiceRegistry, WorkbenchContextService,
} from 'sources/cdeadmin_ui/platform/PlatformRegistry';
import {SurfaceRegistry} from 'sources/cdeadmin_ui/platform/SurfaceRegistry';
import {ModuleRegistry, MODULE_STATES} from
  'sources/cdeadmin_ui/platform/ModuleRegistry';
import {CommandRegistry} from 'sources/cdeadmin_ui/commands/CommandRegistry';
import {ToolFactoryRegistry} from 'sources/cdeadmin_ui/workspace/ToolRegistry';

describe('CDEadmin platform service registry', () => {
  it('activates dependencies once and disposes them in reverse order', async () => {
    const order = [];
    const registry = new ServiceRegistry();
    registry.register({id: 'metadata', version: '1.2.0', factory: () => ({
      name: 'metadata', dispose: () => order.push('metadata'),
    })});
    registry.register({
      id: 'search', version: '2.0.0', dependencies: ['metadata'],
      factory: ({services}) => ({metadata: services.metadata,
        dispose: () => order.push('search')}),
    });
    const first = await registry.resolve('search');
    const second = await registry.resolve('search');
    expect(first).toBe(second);
    expect(first.metadata.name).toBe('metadata');
    await registry.dispose();
    expect(order).toEqual(['search', 'metadata']);
  });

  it('rejects duplicates, cycles, missing and empty implementations', async () => {
    const registry = new ServiceRegistry();
    registry.register({id: 'first', dependencies: ['second'], factory: () => ({})});
    registry.register({id: 'second', dependencies: ['first'], factory: () => ({})});
    await expect(registry.resolve('first')).rejects.toMatchObject({
      code: 'dependency_cycle',
    });
    expect(() => registry.register({id: 'first', factory: () => ({})}))
      .toThrow(PlatformRegistryError);
    await expect(registry.resolve('missing')).rejects.toMatchObject({code: 'not_found'});
    const empty = new ServiceRegistry();
    empty.register({id: 'empty', factory: () => null});
    await expect(empty.resolve('empty')).rejects.toMatchObject({
      code: 'activation_failed',
    });
  });

  it('prevents deactivating a service used by an active dependent', async () => {
    const registry = new ServiceRegistry();
    registry.register({id: 'base', factory: () => ({})});
    registry.register({id: 'dependent', dependencies: ['base'], factory: () => ({})});
    await registry.resolve('dependent');
    await expect(registry.deactivate('base')).rejects.toMatchObject({
      code: 'dependency_active',
    });
    await registry.deactivate('dependent');
    await expect(registry.deactivate('base')).resolves.toBe(true);
  });
});

describe('capability, contribution, event and diagnostic authorities', () => {
  it('never converts absent capabilities into positive support', () => {
    const registry = new CapabilityRegistry();
    registry.replace('endpoint.firebird.1', {
      'admin.backup': {state: 'available', evidence: {probe: 'live'}},
      'admin.cluster': false,
    }, {provider: 'firebird'});
    expect(registry.resolve('endpoint.firebird.1', 'admin.backup')).toMatchObject({
      state: CAPABILITY_STATES.AVAILABLE,
      evidence: {provider: 'firebird', probe: 'live'},
    });
    expect(registry.resolve('endpoint.firebird.1', 'admin.cluster').state)
      .toBe(CAPABILITY_STATES.UNAVAILABLE);
    expect(registry.resolve('endpoint.firebird.1', 'history.mga').state)
      .toBe(CAPABILITY_STATES.UNKNOWN);
    expect(() => registry.require('endpoint.firebird.1', ['history.mga']))
      .toThrow(PlatformRegistryError);
  });

  it('orders and filters inspector/toolbox-style contributions', () => {
    const registry = new ContributionRegistry('inspector');
    const remove = registry.register({
      id: 'inspector.storage', moduleId: 'module.storage', priority: 20,
      when: (context) => context.storage,
    });
    registry.register({
      id: 'inspector.overview', moduleId: 'module.core', priority: 10,
    });
    expect(registry.resolve({storage: true}).map((item) => item.id)).toEqual([
      'inspector.overview', 'inspector.storage',
    ]);
    expect(registry.resolve({storage: false})).toHaveLength(1);
    remove();
    expect(registry.resolve({storage: true})).toHaveLength(1);
  });

  it('authorizes, sequences and broadcasts typed platform events', async () => {
    const service = new PlatformEventService({
      authorize: (_type, _payload, context) => context.allowed === true,
    });
    const exact = jest.fn();
    const all = jest.fn();
    const unsubscribe = service.subscribe('asset.changed', exact);
    service.subscribe('platform.any', all);
    await expect(service.publish('asset.changed', {assetId: 'one'}, {
      allowed: true, origin: 'project.assets',
    })).resolves.toMatchObject({sequence: 1, origin: 'project.assets'});
    expect(exact).toHaveBeenCalledTimes(1);
    expect(all).toHaveBeenCalledTimes(1);
    unsubscribe();
    await service.publish('asset.changed', {}, {allowed: true});
    expect(exact).toHaveBeenCalledTimes(1);
    await expect(service.publish('asset.changed', {}, {allowed: false}))
      .rejects.toMatchObject({code: 'event_forbidden'});
  });

  it('deduplicates and transitions actionable diagnostics', () => {
    const service = new DiagnosticsService();
    const listener = jest.fn();
    const unsubscribe = service.subscribe(listener);
    const input = {id: 'ddn.parse.example', origin: 'cdeadmin.ddn',
      severity: 'error', message: 'Unexpected token', code: 'DDN001'};
    service.report(input);
    expect(service.report(input).occurrences).toBe(2);
    service.transition('ddn.parse.example', DIAGNOSTIC_STATES.ACKNOWLEDGED);
    expect(service.list({state: 'acknowledged'})).toHaveLength(1);
    service.transition('ddn.parse.example', DIAGNOSTIC_STATES.RESOLVED);
    expect(service.list({state: 'open'})).toHaveLength(0);
    expect(listener).toHaveBeenCalledTimes(4);
    unsubscribe();
    service.report({...input, id: 'ddn.parse.other'});
    expect(listener).toHaveBeenCalledTimes(4);
  });

  it('publishes immutable active-workbench context snapshots', () => {
    const service = new WorkbenchContextService({surfaceId: ''});
    const listener = jest.fn();
    const unsubscribe = service.subscribe(listener);
    expect(service.update({surfaceId: 'diagram.ddn.viewer'})).toEqual({
      surfaceId: 'diagram.ddn.viewer',
    });
    expect(Object.isFrozen(service.snapshot())).toBe(true);
    expect(listener).toHaveBeenCalledWith({surfaceId: 'diagram.ddn.viewer'});
    service.replace({surfaceId: 'query.sql'});
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    service.update({surfaceId: 'query.native'});
    expect(listener).toHaveBeenCalledTimes(2);
  });
});

describe('surface and module hosting architecture', () => {
  function architecture() {
    const services = new ServiceRegistry();
    const capabilities = new CapabilityRegistry();
    const tools = new ToolFactoryRegistry();
    const surfaces = new SurfaceRegistry(tools);
    const commands = new CommandRegistry();
    const registries = {
      services, capabilities, surfaces, commands,
      inspector: new ContributionRegistry('inspector'),
      toolbox: new ContributionRegistry('toolbox'),
      status: new ContributionRegistry('status'),
      activity: new ContributionRegistry('activity'),
    };
    return {...registries, modules: new ModuleRegistry(registries)};
  }

  it('creates content-minimized descriptors and restores via registered factories', () => {
    const tools = new ToolFactoryRegistry();
    const surfaces = new SurfaceRegistry(tools);
    const restore = jest.fn((_descriptor, context) => context.content);
    const remove = surfaces.register({
      id: 'diagram.example.viewer', moduleId: 'module.example', restore,
      assetTypes: ['diagram'], readOnly: true, detachable: true,
      duplicable: true,
    });
    const descriptor = surfaces.descriptor('diagram.example.viewer', {
      toolInstanceId: 'viewer-1', restoreRef: 'asset-1', projectId: 'project-1',
    });
    expect(descriptor).toMatchObject({
      toolKind: 'diagram.example.viewer', restoreRef: 'asset-1',
      projectId: 'project-1', capabilities: {detachable: true, duplicable: true},
    });
    expect(JSON.stringify(descriptor)).not.toContain('content');
    expect(surfaces.restore(descriptor, {content: 'restored'})).toBe('restored');
    remove();
    expect(surfaces.list()).toHaveLength(0);
    expect(tools.has('diagram.example.viewer')).toBe(false);
  });

  it('activates all module contributions atomically and removes them', async () => {
    const host = architecture();
    host.services.register({id: 'assets', factory: () => ({ready: true})});
    host.capabilities.replace('endpoint.one', {'metadata.graph': true});
    const deactivate = jest.fn();
    host.modules.register({
      id: 'module.graph', title: 'Graph', serviceRequirements: ['assets'],
      capabilityRequirements: ['metadata.graph'],
      contributions: {
        surfaces: [{id: 'data.graph', restore: () => 'graph'}],
        commands: [{id: 'graph.open', label: 'Open graph', execute: () => true}],
        inspector: [{id: 'inspector.graph'}],
        toolbox: [{id: 'toolbox.graph'}],
        status: [{id: 'status.graph'}],
        activity: [{id: 'activity.graph'}],
      },
      activate: ({services}) => ({assets: services.assets}),
      deactivate,
    });
    const runtime = await host.modules.activate('module.graph', {
      capabilityScopeId: 'endpoint.one',
    });
    expect(runtime.assets.ready).toBe(true);
    expect(host.modules.state('module.graph').state).toBe(MODULE_STATES.ACTIVE);
    expect(host.surfaces.get('data.graph').id).toBe('data.graph');
    expect(host.commands.has('graph.open')).toBe(true);
    expect(host.inspector.resolve()).toHaveLength(1);
    await host.modules.deactivate('module.graph');
    expect(deactivate).toHaveBeenCalledTimes(1);
    expect(host.commands.has('graph.open')).toBe(false);
    expect(host.surfaces.list()).toHaveLength(0);
  });

  it('rolls back partial contributions when activation fails', async () => {
    const host = architecture();
    host.modules.register({
      id: 'module.broken',
      contributions: {
        surfaces: [{id: 'broken.surface', restore: () => null}],
        commands: [{id: 'broken.run', execute: () => null}],
      },
      activate: () => { throw new Error('broken module'); },
    });
    await expect(host.modules.activate('module.broken')).rejects
      .toThrow('broken module');
    expect(host.modules.state('module.broken').state).toBe(MODULE_STATES.FAILED);
    expect(host.surfaces.list()).toHaveLength(0);
    expect(host.commands.has('broken.run')).toBe(false);
  });

  it('rejects unstable module versions and dependency identifiers before registration', () => {
    const host = architecture();
    expect(() => host.modules.register({id: 'module.bad-version', version: 'latest'}))
      .toThrow('semantic versioning');
    expect(() => host.modules.register({id: 'module.bad-service',
      serviceRequirements: ['Not A Service']})).toThrow('Service requirement is invalid');
    expect(() => host.modules.register({id: 'module.bad-capability',
      capabilityRequirements: 'metadata.graph'})).toThrow('must be an array');
    expect(host.modules.list()).toHaveLength(0);
  });
});
