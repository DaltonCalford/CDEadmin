/////////////////////////////////////////////////////////////
// First-party Schema Comparison manifest and host integration gates.
/////////////////////////////////////////////////////////////

import React from 'react';
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
import {
  SCHEMA_COMPARE_ASSET_TYPE, SCHEMA_COMPARE_MODULE_ID,
  SCHEMA_COMPARE_SERVICE_ID, SchemaCompareWorkspace, registerSchemaCompareModule,
} from 'sources/cdeadmin_ui/modules/schema_compare';
import manifest from 'sources/cdeadmin_ui/modules/schema_compare/specification/module-manifest.json';

function architecture(client={asset: jest.fn(), saveAsset: jest.fn()}) {
  const services = new ServiceRegistry(); registerCorePlatformServices(services);
  services.register({id: 'project.assets', factory: () => client});
  const registries = {services, capabilities: new CapabilityRegistry(),
    surfaces: new SurfaceRegistry(new ToolFactoryRegistry()), commands: new CommandRegistry(),
    inspector: new ContributionRegistry('inspector'),
    toolbox: new ContributionRegistry('toolbox'), status: new ContributionRegistry('status'),
    activity: new ContributionRegistry('activity'), bottom: new ContributionRegistry('bottom')};
  const modules = new ModuleRegistry(registries);
  registerSchemaCompareModule({modules, services});
  return {...registries, modules, client};
}

const asset = {asset_type: SCHEMA_COMPARE_ASSET_TYPE, asset_id: 'compare-one',
  content: {schema: 'cdeadmin.schema-compare.asset.v1', schemaVersion: 1,
    moduleId: SCHEMA_COMPARE_MODULE_ID, leftRef: null, rightRef: null,
    options: {}, acceptedMappings: [], ignoredDiffs: [], comparisonResultRef: null,
    changePlan: null}};

describe('Schema Comparison module registration', () => {
  it('keeps the production manifest aligned with executable identities', () => {
    expect(manifest).toMatchObject({moduleId: SCHEMA_COMPARE_MODULE_ID,
      specVersion: '1.0', committedFirstParty: true,
      assetTypes: [SCHEMA_COMPARE_ASSET_TYPE]});
    expect(manifest.surfaces.map((item) => `schema_compare.${item.id}`)).toEqual([
      'schema_compare.compare_setup', 'schema_compare.diff_tree',
      'schema_compare.object_diff', 'schema_compare.mapping_review',
      'schema_compare.change_plan', 'schema_compare.apply_export_review',
    ]);
    expect(manifest.commands.map((item) => item.id)).toHaveLength(8);
    expect(manifest.rules).toEqual({secretsInAssets: false,
      directProviderMutationFromDragDrop: false, providerSupportMayBeInferred: false,
      specGapOnUndefinedChoice: true});
  });

  it('matches the normative six surfaces, eight commands and shell contributions', async () => {
    const host = architecture(); await host.modules.activate(SCHEMA_COMPARE_MODULE_ID);
    expect(host.surfaces.list({assetType: SCHEMA_COMPARE_ASSET_TYPE})
      .map((item) => item.id)).toEqual([
      'schema_compare.compare_setup', 'schema_compare.diff_tree',
      'schema_compare.object_diff', 'schema_compare.mapping_review',
      'schema_compare.change_plan', 'schema_compare.apply_export_review',
    ]);
    expect(host.commands.list().map((item) => item.id)).toEqual([
      'schema_compare.session.create', 'schema_compare.run',
      'schema_compare.rename.accept', 'schema_compare.mapping.set',
      'schema_compare.plan.generate', 'schema_compare.plan.validate',
      'schema_compare.plan.export', 'schema_compare.plan.apply',
    ]);
    expect(host.activity.resolve().map((item) => item.id))
      .toEqual(['activity.schema_compare']);
    expect(host.inspector.resolve({surfaceId: 'schema_compare.object_diff'}))
      .toHaveLength(1);
    expect(host.bottom.resolve({surfaceId: 'schema_compare.change_plan'}))
      .toHaveLength(2);
    expect(host.status.resolve({surfaceId: 'schema_compare.compare_setup'}))
      .toHaveLength(1);
  });

  it('activates an executable runtime with all four background task types', async () => {
    const host = architecture(); await host.modules.activate(SCHEMA_COMPARE_MODULE_ID);
    const service = await host.services.resolve(SCHEMA_COMPARE_SERVICE_ID);
    expect(service).toEqual(expect.objectContaining({create: expect.any(Function),
      run: expect.any(Function), generatePlan: expect.any(Function),
      validatePlan: expect.any(Function), applyPlan: expect.any(Function)}));
    const tasks = await host.services.resolve(PLATFORM_SERVICE_IDS.TASKS);
    expect([...tasks.runners.keys()].sort()).toEqual([
      'schema_compare.diff', 'schema_compare.plan.apply',
      'schema_compare.plan.validation', 'schema_compare.scan',
    ]);
  });

  it('restores a project asset through the shared persistence authority', async () => {
    const client = {asset: jest.fn().mockResolvedValue(asset), saveAsset: jest.fn()};
    const host = architecture(client); await host.modules.activate(SCHEMA_COMPARE_MODULE_ID);
    const descriptor = host.surfaces.descriptor('schema_compare.compare_setup', {
      toolInstanceId: 'compare-one', projectId: 'project-one', restoreRef: 'compare-one',
    });
    const element = await host.surfaces.restore(descriptor, {
      services: {'project.assets': client}, commands: host.commands,
    });
    expect(React.isValidElement(element)).toBe(true);
    expect(element.type).toBe(SchemaCompareWorkspace);
    expect(client.asset).toHaveBeenCalledWith('project-one', 'compare-one');
  });

  it('enforces command permissions, explicit confirmation metadata and macro exclusion',
    async () => {
      const host = architecture(); await host.modules.activate(SCHEMA_COMPARE_MODULE_ID);
      const service = await host.services.resolve(SCHEMA_COMPARE_SERVICE_ID);
      const currentUser = {permissions: ['schema_compare.view']};
      await expect(host.commands.execute('schema_compare.session.create', {}, {
        service, currentUser,
      })).resolves.toMatchObject({state: 'empty'});
      await expect(host.commands.execute('schema_compare.plan.apply', {}, {
        service, currentUser,
      })).rejects.toMatchObject({code: 'permission_denied'});
      const apply = host.commands.get('schema_compare.plan.apply');
      expect(apply).toMatchObject({requiresConfirmation: true,
        createsTask: 'schema_compare.plan.apply', macroCallable: false,
        auditCategory: 'schema_change'});
    });

  it('uses dirty-state close protection and atomically removes contributions', async () => {
    const host = architecture(); await host.modules.activate(SCHEMA_COMPARE_MODULE_ID);
    const descriptor = host.surfaces.descriptor('schema_compare.change_plan', {
      toolInstanceId: 'change-one', restoreRef: 'session-one',
    });
    await expect(host.surfaces.toolRegistry.canClose(descriptor, {dirty: true}))
      .resolves.toEqual({allowed: false,
        reason: 'Save or discard Schema Comparison changes first.'});
    await host.modules.deactivate(SCHEMA_COMPARE_MODULE_ID);
    expect(host.commands.list()).toEqual([]); expect(host.surfaces.list()).toEqual([]);
    expect(host.bottom.resolve({surfaceId: 'schema_compare.change_plan'})).toEqual([]);
  });
});
