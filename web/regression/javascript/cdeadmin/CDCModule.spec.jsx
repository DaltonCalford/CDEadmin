/////////////////////////////////////////////////////////////
// CDC manifest, commands, tasks, permissions and host gates.
/////////////////////////////////////////////////////////////

import React from 'react';
import Ajv2020 from 'ajv/dist/2020';
import {CommandRegistry} from 'sources/cdeadmin_ui/commands/CommandRegistry';
import {CapabilityRegistry, ContributionRegistry,
  ServiceRegistry} from 'sources/cdeadmin_ui/platform/PlatformRegistry';
import {PLATFORM_SERVICE_IDS,
  registerCorePlatformServices} from 'sources/cdeadmin_ui/platform/PlatformServices';
import {ModuleRegistry} from 'sources/cdeadmin_ui/platform/ModuleRegistry';
import {SurfaceRegistry} from 'sources/cdeadmin_ui/platform/SurfaceRegistry';
import {ToolFactoryRegistry} from 'sources/cdeadmin_ui/workspace/ToolRegistry';
import {MODULE_MANIFEST_SCHEMA, SCREEN_SPEC_SCHEMA} from 'sources/cdeadmin_ui';
import {
  CDC_ASSET_TYPE, CDC_MODULE_ID, CDC_SERVICE_ID, CDCWorkspace, registerCDCModule,
} from 'sources/cdeadmin_ui/modules/cdc';
import manifest from 'sources/cdeadmin_ui/modules/cdc/specification/12_cdc.manifest.json';
import primary from 'sources/cdeadmin_ui/modules/cdc/specification/12_cdc_primary.screen.json';
import designer from 'sources/cdeadmin_ui/modules/cdc/specification/12_cdc_01_cdc_designer.screen.json';
import source from 'sources/cdeadmin_ui/modules/cdc/specification/12_cdc_02_source_capture.screen.json';
import mapping from 'sources/cdeadmin_ui/modules/cdc/specification/12_cdc_03_event_mapping.screen.json';
import evolution from 'sources/cdeadmin_ui/modules/cdc/specification/12_cdc_04_schema_evolution.screen.json';
import monitor from 'sources/cdeadmin_ui/modules/cdc/specification/12_cdc_05_run_monitor.screen.json';
import events from 'sources/cdeadmin_ui/modules/cdc/specification/12_cdc_06_event_inspector.screen.json';
import replay from 'sources/cdeadmin_ui/modules/cdc/specification/12_cdc_07_replay.screen.json';

function architecture(client={asset: jest.fn(), saveAsset: jest.fn()}) {
  const services = new ServiceRegistry(); registerCorePlatformServices(services);
  services.register({id: 'project.assets', factory: () => client});
  const registries = {services, capabilities: new CapabilityRegistry(),
    surfaces: new SurfaceRegistry(new ToolFactoryRegistry()), commands: new CommandRegistry(),
    inspector: new ContributionRegistry('inspector'), toolbox: new ContributionRegistry('toolbox'),
    status: new ContributionRegistry('status'), activity: new ContributionRegistry('activity'),
    bottom: new ContributionRegistry('bottom')};
  const modules = new ModuleRegistry(registries); registerCDCModule({modules, services});
  return {...registries, modules, client};
}

const emptyContent = {schema: 'cdeadmin.cdc.asset.v1', schemaVersion: 1,
  moduleId: CDC_MODULE_ID, name: '', description: '', source: null, filters: [], transforms: [],
  sink: null, deliveryPolicy: {schema: 'cdeadmin.cdc-delivery-policy.v1',
    guarantee: 'unknown', deduplication: {}, proof: {}, nativeDetails: {}},
  schemaEvolutionPolicy: {schema: 'cdeadmin.cdc-evolution-policy.v1',
    defaultAction: 'pause_and_review', changes: [], nativeDetails: {}}, alerts: [],
  visualLayout: {}, extensions: {}};

describe('CDC module registration', () => {
  it('validates the manifest and all eight exact screen contracts', () => {
    const ajv = new Ajv2020({strict: false});
    const validateManifest = ajv.compile(MODULE_MANIFEST_SCHEMA);
    expect(validateManifest(manifest)).toBe(true); expect(validateManifest.errors).toBeNull();
    const validateScreen = ajv.compile(SCREEN_SPEC_SCHEMA);
    for(const contract of [primary, designer, source, mapping, evolution, monitor, events, replay]) {
      expect(validateScreen(contract)).toBe(true); expect(validateScreen.errors).toBeNull();
      expect(contract.spec_gaps).toEqual([]);
      const commands = new Set(manifest.commands.map((item) => item.id));
      expect([...contract.entry_commands, ...contract.toolbar_commands].filter(
        (id) => !commands.has(id))).toEqual([]);
    }
  });

  it('keeps normative identities, counts, tasks and events exact', () => {
    expect(manifest).toMatchObject({moduleId: CDC_MODULE_ID, specVersion: '1.0',
      committedFirstParty: true, assetTypes: [CDC_ASSET_TYPE]});
    expect(manifest.surfaces).toHaveLength(7); expect(manifest.commands).toHaveLength(10);
    expect(manifest.taskTypes).toEqual(['cdc.provision', 'cdc.snapshot', 'cdc.stream',
      'cdc.replay', 'cdc.schema_validation']);
    expect(manifest.events).toEqual(['cdc.run.state_changed', 'cdc.lag.threshold',
      'cdc.schema.changed', 'cdc.checkpoint.advanced', 'cdc.run.failed']);
  });

  it('registers every command, surface and shell contribution', async () => {
    const host = architecture(); await host.modules.activate(CDC_MODULE_ID);
    expect(host.surfaces.list({assetType: CDC_ASSET_TYPE})).toHaveLength(7);
    expect(host.commands.list().map((item) => item.id)).toEqual(manifest.commands.map(
      (item) => item.id));
    expect(host.activity.resolve().map((item) => item.id)).toEqual(['activity.cdc']);
    expect(host.inspector.resolve({surfaceId: 'cdc.cdc_designer'})).toHaveLength(1);
    expect(host.bottom.resolve({surfaceId: 'cdc.event_inspector', currentUser: {
      permissions: ['cdc.view_payloads']}})).toHaveLength(4);
    expect(host.bottom.resolve({surfaceId: 'cdc.event_inspector', currentUser: {
      permissions: []}})).toHaveLength(3);
    expect(host.status.resolve({surfaceId: 'cdc.run_monitor'})).toHaveLength(1);
  });

  it('registers the complete runtime and all five shared task runners', async () => {
    const host = architecture(); await host.modules.activate(CDC_MODULE_ID);
    const service = await host.services.resolve(CDC_SERVICE_ID);
    expect(service).toEqual(expect.objectContaining({discover: expect.any(Function),
      start: expect.any(Function), control: expect.any(Function),
      inspectCheckpoint: expect.any(Function), inspectEvents: expect.any(Function),
      prepareReplay: expect.any(Function), executeReplay: expect.any(Function),
      resolveSchemaChange: expect.any(Function), validateSchemaChange: expect.any(Function),
      save: expect.any(Function)}));
    const tasks = await host.services.resolve(PLATFORM_SERVICE_IDS.TASKS);
    expect([...tasks.runners.keys()].sort()).toEqual(['cdc.provision', 'cdc.replay',
      'cdc.schema_validation', 'cdc.snapshot', 'cdc.stream']);
  });

  it('restores the exact asset into all seven detachable surfaces', async () => {
    const client = {asset: jest.fn().mockResolvedValue({asset_type: CDC_ASSET_TYPE,
      asset_id: 'cdc-one', content: emptyContent}), saveAsset: jest.fn()};
    const host = architecture(client); await host.modules.activate(CDC_MODULE_ID);
    for(const surface of manifest.surfaces) {
      const descriptor = host.surfaces.descriptor(`cdc.${surface.id}`, {
        toolInstanceId: surface.id, projectId: 'p', restoreRef: 'cdc-one'});
      const element = await host.surfaces.restore(descriptor, {
        services: {'project.assets': client}, commands: host.commands});
      expect(React.isValidElement(element)).toBe(true); expect(element.type).toBe(CDCWorkspace);
      expect(element.props.surface).toBe(surface.id);
    }
    expect(client.asset).toHaveBeenCalledTimes(7);
  });

  it('enforces view, edit, execute, replay and payload permissions independently', async () => {
    const host = architecture(); await host.modules.activate(CDC_MODULE_ID);
    const service = await host.services.resolve(CDC_SERVICE_ID); const session = service.create({});
    const currentUser = {permissions: ['cdc.view']};
    for(const [id, args] of [['cdc.definition.create', {content: {}}], ['cdc.run.start', {}],
      ['cdc.run.pause', {}], ['cdc.replay.execute', {}],
      ['cdc.schema_change.resolve', {changeId: 'x', action: 'fail'}]]) {
      await expect(host.commands.execute(id, {sessionId: session.id, ...args},
        {cdc: service, currentUser})).rejects.toMatchObject({code: 'permission_denied'});
    }
    expect(host.commands.get('cdc.replay.execute').permission).toEqual(['cdc.replay']);
    expect(host.commands.get('cdc.run.start').permission).toEqual(['cdc.execute']);
  });

  it('rejects undocumented and malformed arguments before runtime execution', async () => {
    const host = architecture(); await host.modules.activate(CDC_MODULE_ID);
    const service = await host.services.resolve(CDC_SERVICE_ID); const session = service.create({});
    const currentUser = {permissions: ['cdc.view', 'cdc.edit', 'cdc.execute', 'cdc.replay']};
    await expect(host.commands.execute('cdc.validate', {sessionId: session.id, guessed: true},
      {cdc: service, currentUser})).rejects.toMatchObject({code: 'invalid_arguments'});
    await expect(host.commands.execute('cdc.run.start', {sessionId: session.id,
      retry: {maximum: 99}}, {cdc: service, currentUser})).rejects.toMatchObject({
      code: 'invalid_arguments'});
    await expect(host.commands.execute('cdc.replay.prepare', {sessionId: session.id, request: {}},
      {cdc: service, currentUser})).rejects.toMatchObject({code: 'invalid_arguments'});
  });

  it('blocks dirty close and atomically unregisters every contribution', async () => {
    const host = architecture(); await host.modules.activate(CDC_MODULE_ID);
    const descriptor = host.surfaces.descriptor('cdc.cdc_designer', {
      toolInstanceId: 'one', restoreRef: 'one'});
    await expect(host.surfaces.toolRegistry.canClose(descriptor, {dirty: true}))
      .resolves.toEqual({allowed: false, reason: 'Save or discard CDC definition changes first.'});
    await host.modules.deactivate(CDC_MODULE_ID);
    expect(host.commands.list()).toEqual([]); expect(host.surfaces.list()).toEqual([]);
    expect(host.activity.resolve()).toEqual([]);
  });
});
