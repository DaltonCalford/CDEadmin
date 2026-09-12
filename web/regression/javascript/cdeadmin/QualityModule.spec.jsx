/////////////////////////////////////////////////////////////
// First-party Data Quality manifest and host integration gates.
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
  QUALITY_ASSET_TYPE, QUALITY_MODULE_ID, QUALITY_SERVICE_ID,
  QualityWorkspace, registerQualityModule,
} from 'sources/cdeadmin_ui/modules/quality';
import manifest from 'sources/cdeadmin_ui/modules/quality/specification/module-manifest.json';
import primary from 'sources/cdeadmin_ui/modules/quality/specification/screens/02_quality_primary.screen.json';
import overview from 'sources/cdeadmin_ui/modules/quality/specification/screens/02_quality_01_quality_overview.screen.json';
import rules from 'sources/cdeadmin_ui/modules/quality/specification/screens/02_quality_02_rule_set_designer.screen.json';
import scope from 'sources/cdeadmin_ui/modules/quality/specification/screens/02_quality_03_data_scope_slice_designer.screen.json';
import results from 'sources/cdeadmin_ui/modules/quality/specification/screens/02_quality_04_validation_run_results.screen.json';
import profiler from 'sources/cdeadmin_ui/modules/quality/specification/screens/02_quality_05_profiler.screen.json';
import history from 'sources/cdeadmin_ui/modules/quality/specification/screens/02_quality_06_quality_history.screen.json';
import schedules from 'sources/cdeadmin_ui/modules/quality/specification/screens/02_quality_07_schedule_actions.screen.json';

function architecture(client={asset: jest.fn(), saveAsset: jest.fn()}) {
  const services = new ServiceRegistry(); registerCorePlatformServices(services);
  services.register({id: 'project.assets', factory: () => client});
  const registries = {services, capabilities: new CapabilityRegistry(),
    surfaces: new SurfaceRegistry(new ToolFactoryRegistry()), commands: new CommandRegistry(),
    inspector: new ContributionRegistry('inspector'), toolbox: new ContributionRegistry('toolbox'),
    status: new ContributionRegistry('status'), activity: new ContributionRegistry('activity'),
    bottom: new ContributionRegistry('bottom')};
  const modules = new ModuleRegistry(registries); registerQualityModule({modules, services});
  return {...registries, modules, client};
}

const content = {schema: 'cdeadmin.quality.asset.v1', schemaVersion: 1,
  moduleId: QUALITY_MODULE_ID, name: 'Orders', owner: '', tags: [], rules: [], parameters: {},
  defaultScope: null, severityPolicy: {}, schedule: null, actions: [], baselineRefs: []};

describe('Data Quality module registration', () => {
  it('validates the exact manifest and every one of the eight screen contracts', () => {
    const ajv = new Ajv2020({strict: false});
    const validateManifest = ajv.compile(MODULE_MANIFEST_SCHEMA);
    expect(validateManifest(manifest)).toBe(true); expect(validateManifest.errors).toBeNull();
    const validateScreen = ajv.compile(SCREEN_SPEC_SCHEMA);
    for(const contract of [primary, overview, rules, scope, results, profiler, history, schedules]) {
      expect({id: contract.screen_id, valid: validateScreen(contract),
        gaps: contract.spec_gaps, errors: validateScreen.errors})
        .toEqual({id: contract.screen_id, valid: true, gaps: [], errors: null});
      const registered = new Set(manifest.commands.map((command) => command.id));
      expect([...contract.entry_commands, ...contract.toolbar_commands]
        .filter((id) => !registered.has(id))).toEqual([]);
    }
  });

  it('keeps production identities, counts and task/event contracts exact', () => {
    expect(manifest).toMatchObject({moduleId: QUALITY_MODULE_ID, specVersion: '1.0',
      committedFirstParty: true, assetTypes: [QUALITY_ASSET_TYPE]});
    expect(manifest.surfaces).toHaveLength(7); expect(manifest.commands).toHaveLength(9);
    expect(manifest.taskTypes).toEqual(['quality.validation.run', 'quality.profile.run',
      'quality.baseline.capture']);
    expect(manifest.events).toEqual(['quality.run.started', 'quality.rule.failed',
      'quality.run.completed', 'quality.baseline.changed']);
  });

  it('registers all surfaces, commands and permission-sensitive shared contributions', async () => {
    const host = architecture(); await host.modules.activate(QUALITY_MODULE_ID);
    expect(host.surfaces.list({assetType: QUALITY_ASSET_TYPE})).toHaveLength(7);
    expect(host.commands.list().map((item) => item.id)).toEqual([
      'quality.ruleset.create', 'quality.rule.add', 'quality.rule.test',
      'quality.ruleset.run', 'quality.run.cancel', 'quality.baseline.capture',
      'quality.profile.run', 'quality.result.export', 'quality.contract.sync']);
    expect(host.activity.resolve().map((item) => item.id)).toEqual(['activity.quality']);
    expect(host.inspector.resolve({surfaceId: 'quality.quality_overview'})).toHaveLength(1);
    expect(host.bottom.resolve({surfaceId: 'quality.quality_overview', currentUser: {
      permissions: []}})).toHaveLength(2);
    expect(host.bottom.resolve({surfaceId: 'quality.quality_overview', currentUser: {
      permissions: ['quality.view_samples']}})).toHaveLength(3);
    expect(host.status.resolve({surfaceId: 'quality.quality_overview'})).toHaveLength(1);
  });

  it('activates all three executable task runners and the shared runtime service', async () => {
    const host = architecture(); await host.modules.activate(QUALITY_MODULE_ID);
    const service = await host.services.resolve(QUALITY_SERVICE_ID);
    expect(service).toEqual(expect.objectContaining({run: expect.any(Function),
      profile: expect.any(Function), captureBaseline: expect.any(Function),
      exportResult: expect.any(Function), importGX: expect.any(Function)}));
    const tasks = await host.services.resolve(PLATFORM_SERVICE_IDS.TASKS);
    expect([...tasks.runners.keys()].sort()).toEqual([
      'quality.baseline.capture', 'quality.profile.run', 'quality.validation.run']);
  });

  it('restores an exact project asset into every provider-independent surface', async () => {
    const asset = {asset_type: QUALITY_ASSET_TYPE, asset_id: 'quality-one', content};
    const client = {asset: jest.fn().mockResolvedValue(asset), saveAsset: jest.fn()};
    const host = architecture(client); await host.modules.activate(QUALITY_MODULE_ID);
    for(const surface of manifest.surfaces) {
      const descriptor = host.surfaces.descriptor(`quality.${surface.id}`, {
        toolInstanceId: surface.id, projectId: 'project-one', restoreRef: 'quality-one'});
      const element = await host.surfaces.restore(descriptor, {
        services: {'project.assets': client}, commands: host.commands});
      expect(React.isValidElement(element)).toBe(true); expect(element.type).toBe(QualityWorkspace);
      expect(element.props.surface).toBe(surface.id);
    }
    expect(client.asset).toHaveBeenCalledTimes(7);
  });

  it('enforces edit, execute, view, sample-export and administration separately', async () => {
    const host = architecture(); await host.modules.activate(QUALITY_MODULE_ID);
    const service = await host.services.resolve(QUALITY_SERVICE_ID); const session = service.create({});
    const currentUser = {permissions: ['quality.view']};
    await expect(host.commands.execute('quality.ruleset.create', {sessionId: session.id},
      {quality: service, currentUser})).rejects.toMatchObject({code: 'permission_denied'});
    await expect(host.commands.execute('quality.profile.run', {sessionId: session.id, budget: 1},
      {quality: service, currentUser})).rejects.toMatchObject({code: 'permission_denied'});
    await expect(host.commands.execute('quality.contract.sync', {sessionId: session.id,
      contractRef: {schema: 'cdeadmin.asset-ref.v1', projectId: 'p', assetId: 'c'}},
    {quality: service, currentUser})).rejects.toMatchObject({code: 'permission_denied'});
    expect(host.commands.get('quality.result.export')).toMatchObject({permission: ['quality.view'],
      auditCategory: 'data_quality'});
  });

  it('rejects undocumented argument blobs and every malformed bounded command input', async () => {
    const host = architecture(); await host.modules.activate(QUALITY_MODULE_ID);
    const service = await host.services.resolve(QUALITY_SERVICE_ID); const session = service.create({});
    const currentUser = {permissions: ['quality.edit', 'quality.execute', 'quality.view']};
    await expect(host.commands.execute('quality.ruleset.run', {
      sessionId: session.id, guessedOption: true}, {quality: service, currentUser}))
      .rejects.toMatchObject({code: 'invalid_arguments'});
    await expect(host.commands.execute('quality.profile.run', {
      sessionId: session.id, budget: 1000001}, {quality: service, currentUser}))
      .rejects.toMatchObject({code: 'invalid_arguments'});
    await expect(host.commands.execute('quality.baseline.capture', {sessionId: session.id,
      tolerances: {default: -0.1}}, {quality: service, currentUser}))
      .rejects.toMatchObject({code: 'invalid_arguments'});
    await expect(host.commands.execute('quality.result.export', {sessionId: session.id,
      runId: 'run', format: 'xml'}, {quality: service, currentUser}))
      .rejects.toMatchObject({code: 'invalid_arguments'});
  });

  it('blocks dirty close and atomically removes every module contribution', async () => {
    const host = architecture(); await host.modules.activate(QUALITY_MODULE_ID);
    const descriptor = host.surfaces.descriptor('quality.rule_set_designer', {
      toolInstanceId: 'quality-one', restoreRef: 'quality-one'});
    await expect(host.surfaces.toolRegistry.canClose(descriptor, {dirty: true}))
      .resolves.toEqual({allowed: false,
        reason: 'Save or discard Data Quality changes first.'});
    await host.modules.deactivate(QUALITY_MODULE_ID);
    expect(host.commands.list()).toEqual([]); expect(host.surfaces.list()).toEqual([]);
    expect(host.bottom.resolve({surfaceId: 'quality.quality_overview'})).toEqual([]);
  });
});
