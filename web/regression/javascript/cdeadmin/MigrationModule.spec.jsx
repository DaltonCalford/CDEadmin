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
  MIGRATION_ASSET_TYPE, MIGRATION_MODULE_ID, MIGRATION_SERVICE_ID,
  MigrationWorkspace, registerMigrationModule,
} from 'sources/cdeadmin_ui/modules/migration';
import manifest from 'sources/cdeadmin_ui/modules/migration/specification/03_migration.manifest.json';
import primary from 'sources/cdeadmin_ui/modules/migration/specification/03_migration_primary.screen.json';
import portfolio from 'sources/cdeadmin_ui/modules/migration/specification/03_migration_01_migration_portfolio.screen.json';
import assessmentScreen from 'sources/cdeadmin_ui/modules/migration/specification/03_migration_02_assessment.screen.json';
import mappingScreen from 'sources/cdeadmin_ui/modules/migration/specification/03_migration_03_mapping_designer.screen.json';
import schemaScreen from 'sources/cdeadmin_ui/modules/migration/specification/03_migration_04_schema_plan.screen.json';
import movementScreen from 'sources/cdeadmin_ui/modules/migration/specification/03_migration_05_data_movement.screen.json';
import validationScreen from 'sources/cdeadmin_ui/modules/migration/specification/03_migration_06_validation.screen.json';
import cutoverScreen from 'sources/cdeadmin_ui/modules/migration/specification/03_migration_07_cutover_runbook.screen.json';
import monitorScreen from 'sources/cdeadmin_ui/modules/migration/specification/03_migration_08_run_monitor.screen.json';
import {definition} from './MigrationTestUtils';

function architecture(client={asset: jest.fn(), update: jest.fn(), create: jest.fn()}) {
  const services = new ServiceRegistry(); registerCorePlatformServices(services);
  services.register({id: 'project.assets', factory: () => client});
  const registries = {services, capabilities: new CapabilityRegistry(),
    surfaces: new SurfaceRegistry(new ToolFactoryRegistry()), commands: new CommandRegistry(),
    inspector: new ContributionRegistry('inspector'), toolbox: new ContributionRegistry('toolbox'),
    status: new ContributionRegistry('status'), activity: new ContributionRegistry('activity'),
    bottom: new ContributionRegistry('bottom')};
  const modules = new ModuleRegistry(registries); registerMigrationModule({modules, services});
  return {...registries, modules, client};
}

describe('Migration module registration', () => {
  test('validates the exact manifest and all nine screen contracts', () => {
    const ajv = new Ajv2020({strict: false}); const validateManifest = ajv.compile(MODULE_MANIFEST_SCHEMA);
    expect(validateManifest(manifest)).toBe(true); expect(validateManifest.errors).toBeNull();
    const validateScreen = ajv.compile(SCREEN_SPEC_SCHEMA);
    for(const item of [primary, portfolio, assessmentScreen, mappingScreen, schemaScreen,
      movementScreen, validationScreen, cutoverScreen, monitorScreen]) {
      expect(validateScreen(item)).toBe(true); expect(validateScreen.errors).toBeNull();
      expect(item.spec_gaps).toEqual([]);
    }
  });
  test('keeps exact identities, counts, tasks, events and drag safety', () => {
    expect(manifest).toMatchObject({moduleId: MIGRATION_MODULE_ID, specVersion: '1.0',
      committedFirstParty: true, assetTypes: [MIGRATION_ASSET_TYPE]});
    expect(manifest.surfaces).toHaveLength(8); expect(manifest.commands).toHaveLength(10);
    expect(manifest.taskTypes).toHaveLength(6); expect(manifest.events).toHaveLength(5);
    expect(manifest.rules.directProviderMutationFromDragDrop).toBe(false);
  });
  test('registers every surface, command and shell contribution', async () => {
    const host = architecture(); await host.modules.activate(MIGRATION_MODULE_ID);
    expect(host.surfaces.list({assetType: MIGRATION_ASSET_TYPE})).toHaveLength(8);
    expect(host.commands.list().map((item) => item.id)).toEqual(manifest.commands.map((item) => item.id));
    expect(host.activity.resolve().map((item) => item.id)).toEqual(['activity.migration']);
    expect(host.inspector.resolve({surfaceId: 'migration.assessment'})).toHaveLength(1);
    expect(host.bottom.resolve({surfaceId: 'migration.validation'})).toHaveLength(4);
    expect(host.status.resolve({surfaceId: 'migration.run_monitor'})).toHaveLength(1);
  });
  test('registers the complete runtime and six shared task runners', async () => {
    const host = architecture(); await host.modules.activate(MIGRATION_MODULE_ID);
    const service = await host.services.resolve(MIGRATION_SERVICE_ID);
    expect(service).toEqual(expect.objectContaining({assess: expect.any(Function),
      dryRun: expect.any(Function), startCopy: expect.any(Function), armCutover: expect.any(Function),
      executeCutover: expect.any(Function), executeRollback: expect.any(Function),
      verify: expect.any(Function), save: expect.any(Function)}));
    const tasks = await host.services.resolve(PLATFORM_SERVICE_IDS.TASKS);
    expect([...tasks.runners.keys()].sort()).toEqual([...manifest.taskTypes].sort());
  });
  test('restores the exact authored asset into every detachable surface', async () => {
    const client = {asset: jest.fn().mockResolvedValue({asset_type: MIGRATION_ASSET_TYPE,
      asset_id: 'migration-one', content: definition()}), update: jest.fn(), create: jest.fn()};
    const host = architecture(client); await host.modules.activate(MIGRATION_MODULE_ID);
    for(const surface of manifest.surfaces) {
      const descriptor = host.surfaces.descriptor(`migration.${surface.id}`, {
        toolInstanceId: surface.id, projectId: 'p', restoreRef: 'migration-one'});
      const element = await host.surfaces.restore(descriptor,
        {services: {'project.assets': client}, commands: host.commands});
      expect(React.isValidElement(element)).toBe(true); expect(element.type).toBe(MigrationWorkspace);
      expect(element.props.surface).toBe(surface.id);
    }
    expect(client.asset).toHaveBeenCalledTimes(8);
  });
  test('enforces edit, execute, cutover and rollback permissions independently', async () => {
    const host = architecture(); await host.modules.activate(MIGRATION_MODULE_ID);
    const service = await host.services.resolve(MIGRATION_SERVICE_ID);
    const session = service.create({content: definition()}); const context = {
      migration: service, currentUser: {permissions: ['migration.view']}};
    for(const [id, args] of [['migration.project.create', {definition: definition()}],
      ['migration.assess.run', {}], ['migration.cutover.arm', {confirmationRef: 'c',
        environment: 'production', connection: 'target-one'}], ['migration.rollback.execute', {}]]) {
      await expect(host.commands.execute(id, {sessionId: session.id, ...args}, context))
        .rejects.toMatchObject({code: 'permission_denied'});
    }
  });
  test('rejects undocumented and malformed command arguments', async () => {
    const host = architecture(); await host.modules.activate(MIGRATION_MODULE_ID);
    const service = await host.services.resolve(MIGRATION_SERVICE_ID); const session = service.create({content: definition()});
    const context = {migration: service, currentUser: {permissions: ['migration.edit',
      'migration.execute', 'migration.cutover', 'migration.rollback']}};
    await expect(host.commands.execute('migration.dry_run', {sessionId: session.id, guessed: true}, context))
      .rejects.toMatchObject({code: 'invalid_arguments'});
    await expect(host.commands.execute('migration.mapping.accept', {sessionId: session.id,
      mappingId: 'orders-map', decision: 'maybe'}, context)).rejects.toMatchObject({code: 'invalid_arguments'});
  });
  test('blocks dirty close and atomically removes contributions', async () => {
    const host = architecture(); await host.modules.activate(MIGRATION_MODULE_ID);
    const descriptor = host.surfaces.descriptor('migration.assessment', {toolInstanceId: 'one'});
    await expect(host.surfaces.toolRegistry.canClose(descriptor, {dirty: true})).resolves.toEqual({
      allowed: false, reason: 'Save or discard migration asset changes first.'});
    await host.modules.deactivate(MIGRATION_MODULE_ID);
    expect(host.commands.list()).toEqual([]); expect(host.surfaces.list()).toEqual([]);
  });
});
