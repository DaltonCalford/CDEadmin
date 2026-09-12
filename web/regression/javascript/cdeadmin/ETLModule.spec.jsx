/////////////////////////////////////////////////////////////
// ETL manifest, commands, tasks, permissions and host gates.
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
  ETL_ASSET_TYPE, ETL_MODULE_ID, ETL_SERVICE_ID, ETLWorkspace, registerETLModule,
} from 'sources/cdeadmin_ui/modules/etl';
import manifest from 'sources/cdeadmin_ui/modules/etl/specification/module-manifest.json';
import primary from 'sources/cdeadmin_ui/modules/etl/specification/screens/05_etl_primary.screen.json';
import designer from 'sources/cdeadmin_ui/modules/etl/specification/screens/05_etl_01_pipeline_designer.screen.json';
import mapping from 'sources/cdeadmin_ui/modules/etl/specification/screens/05_etl_02_mapping_editor.screen.json';
import schema from 'sources/cdeadmin_ui/modules/etl/specification/screens/05_etl_03_schema_propagation.screen.json';
import preview from 'sources/cdeadmin_ui/modules/etl/specification/screens/05_etl_04_preview.screen.json';
import deployment from 'sources/cdeadmin_ui/modules/etl/specification/screens/05_etl_05_deployment.screen.json';
import monitor from 'sources/cdeadmin_ui/modules/etl/specification/screens/05_etl_06_run_monitor.screen.json';
import schedule from 'sources/cdeadmin_ui/modules/etl/specification/screens/05_etl_07_schedule.screen.json';

function architecture(client={asset: jest.fn(), saveAsset: jest.fn()}) {
  const services = new ServiceRegistry(); registerCorePlatformServices(services);
  services.register({id: 'project.assets', factory: () => client});
  const registries = {services, capabilities: new CapabilityRegistry(),
    surfaces: new SurfaceRegistry(new ToolFactoryRegistry()), commands: new CommandRegistry(),
    inspector: new ContributionRegistry('inspector'), toolbox: new ContributionRegistry('toolbox'),
    status: new ContributionRegistry('status'), activity: new ContributionRegistry('activity'),
    bottom: new ContributionRegistry('bottom')};
  const modules = new ModuleRegistry(registries); registerETLModule({modules, services});
  return {...registries, modules, client};
}

const emptyContent = {schema: 'cdeadmin.etl.asset.v1', schemaVersion: 1,
  moduleId: ETL_MODULE_ID, name: '', description: '', mode: 'batch', parameters: [],
  nodes: [], edges: [], deployments: [], schedules: [], tests: [], visualLayout: {}, extensions: {}};

describe('ETL module registration', () => {
  it('validates the manifest and all eight exact screen contracts', () => {
    const ajv = new Ajv2020({strict: false});
    const validateManifest = ajv.compile(MODULE_MANIFEST_SCHEMA);
    expect(validateManifest(manifest)).toBe(true); expect(validateManifest.errors).toBeNull();
    const validateScreen = ajv.compile(SCREEN_SPEC_SCHEMA);
    for(const contract of [primary, designer, mapping, schema, preview, deployment,
      monitor, schedule]) {
      expect(validateScreen(contract)).toBe(true); expect(validateScreen.errors).toBeNull();
      expect(contract.spec_gaps).toEqual([]);
      const commands = new Set(manifest.commands.map((item) => item.id));
      expect([...contract.entry_commands, ...contract.toolbar_commands].filter(
        (id) => !commands.has(id))).toEqual([]);
    }
  });

  it('keeps normative identities, counts, tasks and events exact', () => {
    expect(manifest).toMatchObject({moduleId: ETL_MODULE_ID, specVersion: '1.0',
      committedFirstParty: true, assetTypes: [ETL_ASSET_TYPE]});
    expect(manifest.surfaces).toHaveLength(7); expect(manifest.commands).toHaveLength(10);
    expect(manifest.taskTypes).toEqual(['etl.preview', 'etl.run', 'etl.deploy.validation']);
    expect(manifest.events).toEqual(['etl.pipeline.changed', 'etl.run.started',
      'etl.step.failed', 'etl.run.completed', 'etl.lineage.emitted']);
  });

  it('registers every command, surface and shell contribution', async () => {
    const host = architecture(); await host.modules.activate(ETL_MODULE_ID);
    expect(host.surfaces.list({assetType: ETL_ASSET_TYPE})).toHaveLength(7);
    expect(host.commands.list().map((item) => item.id)).toEqual(manifest.commands.map(
      (item) => item.id));
    expect(host.activity.resolve().map((item) => item.id)).toEqual(['activity.etl']);
    expect(host.inspector.resolve({surfaceId: 'etl.pipeline_designer'})).toHaveLength(1);
    expect(host.bottom.resolve({surfaceId: 'etl.preview', currentUser: {
      permissions: ['etl.view_samples']}})).toHaveLength(4);
    expect(host.status.resolve({surfaceId: 'etl.run_monitor'})).toHaveLength(1);
  });

  it('registers the complete runtime and all shared task runners', async () => {
    const host = architecture(); await host.modules.activate(ETL_MODULE_ID);
    const service = await host.services.resolve(ETL_SERVICE_ID);
    expect(service).toEqual(expect.objectContaining({preview: expect.any(Function),
      deploy: expect.any(Function), startRun: expect.any(Function),
      resumeRun: expect.any(Function), save: expect.any(Function)}));
    const tasks = await host.services.resolve(PLATFORM_SERVICE_IDS.TASKS);
    expect([...tasks.runners.keys()].sort()).toEqual([
      'etl.deploy.validation', 'etl.preview', 'etl.run']);
  });

  it('restores the exact asset into all seven detachable surfaces', async () => {
    const client = {asset: jest.fn().mockResolvedValue({asset_type: ETL_ASSET_TYPE,
      asset_id: 'etl-one', content: emptyContent}), saveAsset: jest.fn()};
    const host = architecture(client); await host.modules.activate(ETL_MODULE_ID);
    for(const surface of manifest.surfaces) {
      const descriptor = host.surfaces.descriptor(`etl.${surface.id}`, {
        toolInstanceId: surface.id, projectId: 'p', restoreRef: 'etl-one'});
      const element = await host.surfaces.restore(descriptor, {
        services: {'project.assets': client}, commands: host.commands});
      expect(React.isValidElement(element)).toBe(true); expect(element.type).toBe(ETLWorkspace);
      expect(element.props.surface).toBe(surface.id);
    }
    expect(client.asset).toHaveBeenCalledTimes(7);
  });

  it('enforces view, edit, preview/sample, deploy and execute independently', async () => {
    const host = architecture(); await host.modules.activate(ETL_MODULE_ID);
    const service = await host.services.resolve(ETL_SERVICE_ID); const session = service.create({});
    const currentUser = {permissions: ['etl.view']};
    for(const [id, args] of [['etl.pipeline.create', {content: {}}],
      ['etl.preview.run', {}], ['etl.pipeline.deploy', {deploymentId: 'dev'}],
      ['etl.run.start', {deploymentId: 'dev'}]]) {
      await expect(host.commands.execute(id, {sessionId: session.id, ...args},
        {etl: service, currentUser})).rejects.toMatchObject({code: 'permission_denied'});
    }
    expect(host.commands.get('etl.preview.run').permission).toEqual([
      'etl.preview', 'etl.view_samples']);
  });

  it('rejects undocumented and malformed arguments before runtime execution', async () => {
    const host = architecture(); await host.modules.activate(ETL_MODULE_ID);
    const service = await host.services.resolve(ETL_SERVICE_ID); const session = service.create({});
    const currentUser = {permissions: ['etl.view', 'etl.edit', 'etl.preview',
      'etl.view_samples', 'etl.deploy', 'etl.execute']};
    await expect(host.commands.execute('etl.pipeline.validate', {sessionId: session.id,
      guessed: true}, {etl: service, currentUser})).rejects.toMatchObject({
      code: 'invalid_arguments'});
    await expect(host.commands.execute('etl.node.add', {sessionId: session.id, node: {}},
      {etl: service, currentUser})).rejects.toMatchObject({code: 'invalid_arguments'});
    await expect(host.commands.execute('etl.preview.run', {sessionId: session.id, limit: 10001},
      {etl: service, currentUser})).rejects.toMatchObject({code: 'invalid_arguments'});
  });

  it('blocks dirty close and atomically unregisters every contribution', async () => {
    const host = architecture(); await host.modules.activate(ETL_MODULE_ID);
    const descriptor = host.surfaces.descriptor('etl.pipeline_designer', {
      toolInstanceId: 'one', restoreRef: 'one'});
    await expect(host.surfaces.toolRegistry.canClose(descriptor, {dirty: true}))
      .resolves.toEqual({allowed: false, reason: 'Save or discard ETL pipeline changes first.'});
    await host.modules.deactivate(ETL_MODULE_ID);
    expect(host.commands.list()).toEqual([]); expect(host.surfaces.list()).toEqual([]);
    expect(host.activity.resolve()).toEqual([]);
  });
});
