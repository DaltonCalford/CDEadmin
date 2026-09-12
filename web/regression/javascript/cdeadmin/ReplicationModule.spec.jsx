import React from 'react';
import Ajv2020 from 'ajv/dist/2020';
import {CommandRegistry} from 'sources/cdeadmin_ui/commands/CommandRegistry';
import {
  CapabilityRegistry, ContributionRegistry, ServiceRegistry,
} from 'sources/cdeadmin_ui/platform/PlatformRegistry';
import {
  PLATFORM_SERVICE_IDS, registerCorePlatformServices,
} from 'sources/cdeadmin_ui/platform/PlatformServices';
import {ModuleRegistry} from 'sources/cdeadmin_ui/platform/ModuleRegistry';
import {SurfaceRegistry} from 'sources/cdeadmin_ui/platform/SurfaceRegistry';
import {ToolFactoryRegistry} from 'sources/cdeadmin_ui/workspace/ToolRegistry';
import {MODULE_MANIFEST_SCHEMA, SCREEN_SPEC_SCHEMA} from 'sources/cdeadmin_ui';
import {
  REPLICATION_ASSET_TYPE, REPLICATION_MODULE_ID, REPLICATION_SERVICE_ID,
  ReplicationWorkspace, registerReplicationModule,
} from 'sources/cdeadmin_ui/modules/replication';
import manifest from 'sources/cdeadmin_ui/modules/replication/specification/10_replication.manifest.json';
import primary from 'sources/cdeadmin_ui/modules/replication/specification/10_replication_primary.screen.json';
import topologyScreen from 'sources/cdeadmin_ui/modules/replication/specification/10_replication_01_topology_explorer.screen.json';
import participantScreen from 'sources/cdeadmin_ui/modules/replication/specification/10_replication_02_participant_inspector.screen.json';
import linkScreen from 'sources/cdeadmin_ui/modules/replication/specification/10_replication_03_replication_link_inspector.screen.json';
import lagScreen from 'sources/cdeadmin_ui/modules/replication/specification/10_replication_04_lag_history.screen.json';
import failoverScreen from 'sources/cdeadmin_ui/modules/replication/specification/10_replication_05_failover_planner.screen.json';
import eventsScreen from 'sources/cdeadmin_ui/modules/replication/specification/10_replication_06_events.screen.json';
import {definition} from './ReplicationTestUtils';

function architecture(client={asset: jest.fn(), update: jest.fn(), create: jest.fn()}) {
  const services = new ServiceRegistry(); registerCorePlatformServices(services);
  services.register({id: 'project.assets', factory: () => client});
  const registries = {services, capabilities: new CapabilityRegistry(),
    surfaces: new SurfaceRegistry(new ToolFactoryRegistry()), commands: new CommandRegistry(),
    inspector: new ContributionRegistry('inspector'), toolbox: new ContributionRegistry('toolbox'),
    status: new ContributionRegistry('status'), activity: new ContributionRegistry('activity'),
    bottom: new ContributionRegistry('bottom')};
  const modules = new ModuleRegistry(registries);
  registerReplicationModule({modules, services});
  return {...registries, modules, client};
}

describe('Replication Topology module registration', () => {
  it('validates the exact manifest and all seven screen contracts', () => {
    const ajv = new Ajv2020({strict: false});
    const validateManifest = ajv.compile(MODULE_MANIFEST_SCHEMA);
    expect(validateManifest(manifest)).toBe(true); expect(validateManifest.errors).toBeNull();
    const validateScreen = ajv.compile(SCREEN_SPEC_SCHEMA);
    for(const screen of [primary, topologyScreen, participantScreen, linkScreen, lagScreen,
      failoverScreen, eventsScreen]) {
      expect(validateScreen(screen)).toBe(true); expect(validateScreen.errors).toBeNull();
      expect(screen.spec_gaps).toEqual([]);
      const commands = new Set(manifest.commands.map((item) => item.id));
      expect([...screen.entry_commands, ...screen.toolbar_commands].filter(
        (id) => !commands.has(id))).toEqual([]);
    }
  });

  it('keeps identities, counts, tasks, events and drag safety exact', () => {
    expect(manifest).toMatchObject({moduleId: REPLICATION_MODULE_ID, specVersion: '1.0',
      committedFirstParty: true, assetTypes: [REPLICATION_ASSET_TYPE]});
    expect(manifest.surfaces).toHaveLength(6); expect(manifest.commands).toHaveLength(8);
    expect(manifest.taskTypes).toEqual(['replication.discovery',
      'replication.failover.validation', 'replication.failover.execution']);
    expect(manifest.events).toEqual(['replication.role.changed', 'replication.link.degraded',
      'replication.lag.threshold', 'replication.failover.completed']);
    expect(manifest.rules.directProviderMutationFromDragDrop).toBe(false);
  });

  it('registers all commands, surfaces and shell contributions', async () => {
    const host = architecture(); await host.modules.activate(REPLICATION_MODULE_ID);
    expect(host.surfaces.list({assetType: REPLICATION_ASSET_TYPE})).toHaveLength(6);
    expect(host.commands.list().map((item) => item.id))
      .toEqual(manifest.commands.map((item) => item.id));
    expect(host.activity.resolve().map((item) => item.id)).toEqual(['activity.replication']);
    expect(host.inspector.resolve({surfaceId: 'replication.topology_explorer'})).toHaveLength(1);
    expect(host.bottom.resolve({surfaceId: 'replication.lag_history'})).toHaveLength(4);
    expect(host.status.resolve({surfaceId: 'replication.events'})).toHaveLength(1);
  });

  it('registers the full runtime and all three shared task runners', async () => {
    const host = architecture(); await host.modules.activate(REPLICATION_MODULE_ID);
    const service = await host.services.resolve(REPLICATION_SERVICE_ID);
    expect(service).toEqual(expect.objectContaining({refresh: expect.any(Function),
      controlLink: expect.any(Function), upsertPlan: expect.any(Function),
      validateFailover: expect.any(Function), armFailover: expect.any(Function),
      executeFailover: expect.any(Function), createSnapshot: expect.any(Function),
      save: expect.any(Function)}));
    const tasks = await host.services.resolve(PLATFORM_SERVICE_IDS.TASKS);
    expect([...tasks.runners.keys()].sort()).toEqual(['replication.discovery',
      'replication.failover.execution', 'replication.failover.validation']);
  });

  it('restores the exact asset into all six detachable surfaces', async () => {
    const client = {asset: jest.fn().mockResolvedValue({asset_type: REPLICATION_ASSET_TYPE,
      asset_id: 'replication-one', content: definition()}), update: jest.fn(), create: jest.fn()};
    const host = architecture(client); await host.modules.activate(REPLICATION_MODULE_ID);
    for(const surface of manifest.surfaces) {
      const descriptor = host.surfaces.descriptor(`replication.${surface.id}`, {
        toolInstanceId: surface.id, projectId: 'p', restoreRef: 'replication-one'});
      const element = await host.surfaces.restore(descriptor, {
        services: {'project.assets': client}, commands: host.commands});
      expect(React.isValidElement(element)).toBe(true);
      expect(element.type).toBe(ReplicationWorkspace);
      expect(element.props.surface).toBe(surface.id);
    }
    expect(client.asset).toHaveBeenCalledTimes(6);
  });

  it('enforces view, control, planning and execute permissions independently', async () => {
    const host = architecture(); await host.modules.activate(REPLICATION_MODULE_ID);
    const service = await host.services.resolve(REPLICATION_SERVICE_ID);
    const session = service.create({content: definition()});
    const context = {replication: service, currentUser: {permissions: ['replication.view']}};
    for(const [id, args] of [
      ['replication.link.pause', {topologyId: 'topology-one', linkId: 'link-one'}],
      ['replication.failover.plan', {plan: definition().failoverPlans[0]}],
      ['replication.failover.execute', {planId: 'plan-one'}]]) {
      await expect(host.commands.execute(id, {sessionId: session.id, ...args}, context))
        .rejects.toMatchObject({code: 'permission_denied'});
    }
    expect(host.commands.get('replication.topology.refresh').permission)
      .toEqual(['replication.view']);
  });

  it('rejects undocumented and malformed command arguments', async () => {
    const host = architecture(); await host.modules.activate(REPLICATION_MODULE_ID);
    const service = await host.services.resolve(REPLICATION_SERVICE_ID);
    const session = service.create({content: definition()});
    const context = {replication: service, currentUser: {permissions: [
      'replication.view', 'replication.control', 'replication.plan_failover',
      'replication.execute_failover']}};
    await expect(host.commands.execute('replication.topology.refresh', {sessionId: session.id,
      topologyId: 'topology-one', guessed: true}, context)).rejects.toMatchObject({
      code: 'invalid_arguments'});
    await expect(host.commands.execute('replication.topology.refresh', {sessionId: session.id,
      topologyId: 'topology-one', retry: {maximum: 9}}, context)).rejects.toMatchObject({
      code: 'invalid_arguments'});
    await expect(host.commands.execute('replication.failover.validate', {sessionId: session.id},
      context)).rejects.toMatchObject({code: 'invalid_arguments'});
    await expect(host.commands.execute('replication.snapshot.create', {sessionId: session.id,
      topologyId: 'topology-one', snapshotId: 7}, context)).rejects.toMatchObject({
      code: 'invalid_arguments'});
  });

  it('blocks dirty close and atomically removes contributions', async () => {
    const host = architecture(); await host.modules.activate(REPLICATION_MODULE_ID);
    const descriptor = host.surfaces.descriptor('replication.topology_explorer', {
      toolInstanceId: 'one'});
    await expect(host.surfaces.toolRegistry.canClose(descriptor, {dirty: true}))
      .resolves.toEqual({allowed: false,
        reason: 'Save or discard replication asset changes first.'});
    await host.modules.deactivate(REPLICATION_MODULE_ID);
    expect(host.commands.list()).toEqual([]); expect(host.surfaces.list()).toEqual([]);
    expect(host.activity.resolve()).toEqual([]);
  });
});
