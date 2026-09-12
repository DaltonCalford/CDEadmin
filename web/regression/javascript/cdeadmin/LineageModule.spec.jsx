/////////////////////////////////////////////////////////////
// First-party Data Lineage manifest and host integration gates.
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
import {LINEAGE_ASSET_TYPE, LINEAGE_MODULE_ID, LINEAGE_SERVICE_ID,
  LineageWorkspace, registerLineageModule} from 'sources/cdeadmin_ui/modules/lineage';
import manifest from 'sources/cdeadmin_ui/modules/lineage/specification/module-manifest.json';
import {MODULE_MANIFEST_SCHEMA, SCREEN_SPEC_SCHEMA} from 'sources/cdeadmin_ui';
import primaryScreen from 'sources/cdeadmin_ui/modules/lineage/specification/screens/01_lineage_primary.screen.json';
import explorerScreen from 'sources/cdeadmin_ui/modules/lineage/specification/screens/01_lineage_01_lineage_explorer.screen.json';
import fieldScreen from 'sources/cdeadmin_ui/modules/lineage/specification/screens/01_lineage_02_field_lineage.screen.json';
import impactScreen from 'sources/cdeadmin_ui/modules/lineage/specification/screens/01_lineage_03_impact_analysis.screen.json';
import timelineScreen from 'sources/cdeadmin_ui/modules/lineage/specification/screens/01_lineage_04_timeline_snapshot_compare.screen.json';
import evidenceScreen from 'sources/cdeadmin_ui/modules/lineage/specification/screens/01_lineage_05_evidence_inspector.screen.json';
import ingestionScreen from 'sources/cdeadmin_ui/modules/lineage/specification/screens/01_lineage_06_ingestion_status.screen.json';

function architecture(client={asset: jest.fn(), saveAsset: jest.fn()}) {
  const services = new ServiceRegistry(); registerCorePlatformServices(services);
  services.register({id: 'project.assets', factory: () => client});
  const registries = {services, capabilities: new CapabilityRegistry(),
    surfaces: new SurfaceRegistry(new ToolFactoryRegistry()), commands: new CommandRegistry(),
    inspector: new ContributionRegistry('inspector'), toolbox: new ContributionRegistry('toolbox'),
    status: new ContributionRegistry('status'), activity: new ContributionRegistry('activity'),
    bottom: new ContributionRegistry('bottom')};
  const modules = new ModuleRegistry(registries);
  registerLineageModule({modules, services});
  return {...registries, modules, client};
}

const content = {schema: 'cdeadmin.lineage.asset.v1', schemaVersion: 1,
  moduleId: LINEAGE_MODULE_ID, scopeRefs: [], sourcePolicies: [], savedFilters: {},
  curatedEdges: [], suppressedInferenceRules: [], snapshotRefs: []};

describe('Data Lineage module registration', () => {
  it('validates the exact manifest and all seven screen contracts', () => {
    const ajv = new Ajv2020({strict: false});
    const validateManifest = ajv.compile(MODULE_MANIFEST_SCHEMA);
    expect(validateManifest(manifest)).toBe(true);
    expect(validateManifest.errors).toBeNull();
    const validateScreen = ajv.compile(SCREEN_SPEC_SCHEMA);
    for(const screenContract of [primaryScreen, explorerScreen, fieldScreen,
      impactScreen, timelineScreen, evidenceScreen, ingestionScreen]) {
      expect({id: screenContract.screen_id, valid: validateScreen(screenContract),
        gaps: screenContract.spec_gaps, errors: validateScreen.errors})
        .toEqual({id: screenContract.screen_id, valid: true, gaps: [], errors: null});
      const registered = new Set(manifest.commands.map((command) => command.id));
      expect([...screenContract.entry_commands, ...screenContract.toolbar_commands]
        .filter((id) => !registered.has(id))).toEqual([]);
    }
  });

  it('keeps the production manifest aligned with all normative identities', () => {
    expect(manifest).toMatchObject({moduleId: LINEAGE_MODULE_ID, specVersion: '1.0',
      committedFirstParty: true, assetTypes: [LINEAGE_ASSET_TYPE]});
    expect(manifest.surfaces.map((item) => `lineage.${item.id}`)).toEqual([
      'lineage.lineage_explorer', 'lineage.field_lineage', 'lineage.impact_analysis',
      'lineage.timeline_snapshot_compare', 'lineage.evidence_inspector',
      'lineage.ingestion_status']);
    expect(manifest.commands).toHaveLength(11);
    expect(manifest.taskTypes).toHaveLength(5); expect(manifest.events).toHaveLength(5);
  });

  it('registers six surfaces, eleven commands and shared shell contributions', async () => {
    const host = architecture(); await host.modules.activate(LINEAGE_MODULE_ID);
    expect(host.surfaces.list({assetType: LINEAGE_ASSET_TYPE})).toHaveLength(6);
    expect(host.commands.list().map((item) => item.id)).toEqual([
      'lineage.graph.refresh', 'lineage.node.trace_upstream',
      'lineage.node.trace_downstream', 'lineage.impact.run', 'lineage.edge.curate',
      'lineage.edge.suppress_inference', 'lineage.snapshot.create',
      'lineage.snapshot.compare', 'lineage.import.openlineage',
      'lineage.export.openlineage', 'lineage.view.open_ddn']);
    expect(host.activity.resolve().map((item) => item.id)).toEqual(['activity.lineage']);
    expect(host.inspector.resolve({surfaceId: 'lineage.lineage_explorer'})).toHaveLength(1);
    expect(host.bottom.resolve({surfaceId: 'lineage.lineage_explorer'})).toHaveLength(2);
    expect(host.status.resolve({surfaceId: 'lineage.lineage_explorer'})).toHaveLength(1);
  });

  it('activates all five executable task runners', async () => {
    const host = architecture(); await host.modules.activate(LINEAGE_MODULE_ID);
    const service = await host.services.resolve(LINEAGE_SERVICE_ID);
    expect(service).toEqual(expect.objectContaining({refresh: expect.any(Function),
      trace: expect.any(Function), runImpact: expect.any(Function),
      importOpenLineage: expect.any(Function), exportOpenLineage: expect.any(Function)}));
    const tasks = await host.services.resolve(PLATFORM_SERVICE_IDS.TASKS);
    expect([...tasks.runners.keys()].sort()).toEqual([
      'lineage.discovery.scan', 'lineage.impact.compute', 'lineage.import',
      'lineage.parsing.extract', 'lineage.reconcile']);
  });

  it('restores an exact Lineage project asset through shared persistence', async () => {
    const asset = {asset_type: LINEAGE_ASSET_TYPE, asset_id: 'lineage-one', content};
    const client = {asset: jest.fn().mockResolvedValue(asset), saveAsset: jest.fn()};
    const host = architecture(client); await host.modules.activate(LINEAGE_MODULE_ID);
    const descriptor = host.surfaces.descriptor('lineage.lineage_explorer', {
      toolInstanceId: 'lineage-one', projectId: 'project-one', restoreRef: 'lineage-one'});
    const element = await host.surfaces.restore(descriptor, {
      services: {'project.assets': client}, commands: host.commands});
    expect(React.isValidElement(element)).toBe(true); expect(element.type).toBe(LineageWorkspace);
    expect(client.asset).toHaveBeenCalledWith('project-one', 'lineage-one');
  });

  it('enforces separated scan, curate and export permissions at invocation time', async () => {
    const host = architecture(); await host.modules.activate(LINEAGE_MODULE_ID);
    const service = await host.services.resolve(LINEAGE_SERVICE_ID);
    const session = service.create({}); const currentUser = {permissions: ['lineage.view']};
    await expect(host.commands.execute('lineage.snapshot.create', {sessionId: session.id},
      {lineage: service, currentUser})).rejects.toMatchObject({code: 'permission_denied'});
    await expect(host.commands.execute('lineage.export.openlineage', {sessionId: session.id},
      {lineage: service, currentUser})).rejects.toMatchObject({code: 'permission_denied'});
    expect(host.commands.get('lineage.graph.refresh')).toMatchObject({
      createsTask: 'lineage.reconcile', auditCategory: 'data_lineage'});
  });

  it('rejects undocumented command blobs and invalid traversal bounds', async () => {
    const host = architecture(); await host.modules.activate(LINEAGE_MODULE_ID);
    const service = await host.services.resolve(LINEAGE_SERVICE_ID);
    const session = service.create({});
    const currentUser = {permissions: ['lineage.scan', 'lineage.view']};
    await expect(host.commands.execute('lineage.graph.refresh', {
      sessionId: session.id, options: {invented: true},
    }, {lineage: service, currentUser})).rejects.toMatchObject({code: 'invalid_arguments'});
    await expect(host.commands.execute('lineage.node.trace_upstream', {
      sessionId: session.id, nodeId: 'node', depth: 11,
    }, {lineage: service, currentUser})).rejects.toMatchObject({code: 'invalid_arguments'});
  });

  it('blocks dirty close and atomically removes every contribution', async () => {
    const host = architecture(); await host.modules.activate(LINEAGE_MODULE_ID);
    const descriptor = host.surfaces.descriptor('lineage.lineage_explorer', {
      toolInstanceId: 'lineage-one', restoreRef: 'lineage-one'});
    await expect(host.surfaces.toolRegistry.canClose(descriptor, {dirty: true}))
      .resolves.toEqual({allowed: false, reason: 'Save or discard Data Lineage changes first.'});
    await host.modules.deactivate(LINEAGE_MODULE_ID);
    expect(host.commands.list()).toEqual([]); expect(host.surfaces.list()).toEqual([]);
    expect(host.bottom.resolve({surfaceId: 'lineage.lineage_explorer'})).toEqual([]);
  });
});
