/////////////////////////////////////////////////////////////
// Data Contract manifest, command and host integration gates.
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
  CONTRACT_ASSET_TYPE, CONTRACT_MODULE_ID, CONTRACT_SERVICE_ID,
  ContractWorkspace, registerContractModule,
} from 'sources/cdeadmin_ui/modules/data_contract';
import manifest from 'sources/cdeadmin_ui/modules/data_contract/specification/module-manifest.json';
import primary from 'sources/cdeadmin_ui/modules/data_contract/specification/screens/06_contract_primary.screen.json';
import explorer from 'sources/cdeadmin_ui/modules/data_contract/specification/screens/06_contract_01_contract_explorer.screen.json';
import editor from 'sources/cdeadmin_ui/modules/data_contract/specification/screens/06_contract_02_contract_editor.screen.json';
import binding from 'sources/cdeadmin_ui/modules/data_contract/specification/screens/06_contract_03_schema_resource_binding.screen.json';
import quality from 'sources/cdeadmin_ui/modules/data_contract/specification/screens/06_contract_04_quality_sla.screen.json';
import team from 'sources/cdeadmin_ui/modules/data_contract/specification/screens/06_contract_05_team_roles_support.screen.json';
import compliance from 'sources/cdeadmin_ui/modules/data_contract/specification/screens/06_contract_06_compliance.screen.json';
import versions from 'sources/cdeadmin_ui/modules/data_contract/specification/screens/06_contract_07_version_diff.screen.json';

function architecture(client={asset: jest.fn(), saveAsset: jest.fn()}) {
  const services = new ServiceRegistry(); registerCorePlatformServices(services);
  services.register({id: 'project.assets', factory: () => client});
  const registries = {services, capabilities: new CapabilityRegistry(),
    surfaces: new SurfaceRegistry(new ToolFactoryRegistry()), commands: new CommandRegistry(),
    inspector: new ContributionRegistry('inspector'), toolbox: new ContributionRegistry('toolbox'),
    status: new ContributionRegistry('status'), activity: new ContributionRegistry('activity'),
    bottom: new ContributionRegistry('bottom')};
  const modules = new ModuleRegistry(registries); registerContractModule({modules, services});
  return {...registries, modules, client};
}

const content = {schema: 'cdeadmin.contract.asset.v1', schemaVersion: 1,
  moduleId: CONTRACT_MODULE_ID, contractVersion: '1.0.0', status: 'proposed',
  name: '', domain: '', description: '', elements: [], bindings: [],
  qualityObligations: [], sla: [], team: [], roles: [], servers: [],
  authoritativeDefinitions: [], extensions: {}};

describe('Data Contract module registration', () => {
  it('validates the exact manifest and every one of the eight screen contracts', () => {
    const ajv = new Ajv2020({strict: false});
    const validateManifest = ajv.compile(MODULE_MANIFEST_SCHEMA);
    expect(validateManifest(manifest)).toBe(true); expect(validateManifest.errors).toBeNull();
    const validateScreen = ajv.compile(SCREEN_SPEC_SCHEMA);
    for(const contract of [primary, explorer, editor, binding, quality, team, compliance, versions]) {
      expect({id: contract.screen_id, valid: validateScreen(contract), gaps: contract.spec_gaps,
        errors: validateScreen.errors}).toEqual({id: contract.screen_id, valid: true,
        gaps: [], errors: null});
      const registered = new Set(manifest.commands.map((command) => command.id));
      expect([...contract.entry_commands, ...contract.toolbar_commands]
        .filter((id) => !registered.has(id))).toEqual([]);
    }
  });

  it('keeps normative identities, counts, tasks and events exact', () => {
    expect(manifest).toMatchObject({moduleId: CONTRACT_MODULE_ID, specVersion: '1.0',
      committedFirstParty: true, assetTypes: [CONTRACT_ASSET_TYPE]});
    expect(manifest.surfaces).toHaveLength(7); expect(manifest.commands).toHaveLength(9);
    expect(manifest.taskTypes).toEqual(['contract.import', 'contract.compliance.validation',
      'contract.metadata.compare']);
    expect(manifest.events).toEqual(['contract.changed', 'contract.activated',
      'contract.compliance.failed', 'contract.drift.detected']);
  });

  it('registers all workbench and shell contributions', async () => {
    const host = architecture(); await host.modules.activate(CONTRACT_MODULE_ID);
    expect(host.surfaces.list({assetType: CONTRACT_ASSET_TYPE})).toHaveLength(7);
    expect(host.commands.list().map((item) => item.id)).toEqual([
      'contract.create', 'contract.import.odcs', 'contract.validate',
      'contract.bind_resource', 'contract.sync_metadata', 'contract.compliance.run',
      'contract.version.create', 'contract.status.set', 'contract.export.odcs']);
    expect(host.activity.resolve().map((item) => item.id)).toEqual(['activity.contract']);
    expect(host.inspector.resolve({surfaceId: 'contract.contract_editor'})).toHaveLength(1);
    expect(host.bottom.resolve({surfaceId: 'contract.compliance'})).toHaveLength(4);
    expect(host.status.resolve({surfaceId: 'contract.compliance'})).toHaveLength(1);
  });

  it('activates all task runners and the complete runtime service', async () => {
    const host = architecture(); await host.modules.activate(CONTRACT_MODULE_ID);
    const service = await host.services.resolve(CONTRACT_SERVICE_ID);
    expect(service).toEqual(expect.objectContaining({importODCS: expect.any(Function),
      compliance: expect.any(Function), syncMetadata: expect.any(Function),
      exportODCS: expect.any(Function), createVersion: expect.any(Function)}));
    const tasks = await host.services.resolve(PLATFORM_SERVICE_IDS.TASKS);
    expect([...tasks.runners.keys()].sort()).toEqual(['contract.compliance.validation',
      'contract.import', 'contract.metadata.compare']);
  });

  it('restores exact project assets into every surface', async () => {
    const asset = {asset_type: CONTRACT_ASSET_TYPE, asset_id: 'contract-one', content};
    const client = {asset: jest.fn().mockResolvedValue(asset), saveAsset: jest.fn()};
    const host = architecture(client); await host.modules.activate(CONTRACT_MODULE_ID);
    for(const surface of manifest.surfaces) {
      const descriptor = host.surfaces.descriptor(`contract.${surface.id}`, {
        toolInstanceId: surface.id, projectId: 'project-one', restoreRef: 'contract-one'});
      const element = await host.surfaces.restore(descriptor, {
        services: {'project.assets': client}, commands: host.commands});
      expect(React.isValidElement(element)).toBe(true); expect(element.type).toBe(ContractWorkspace);
      expect(element.props.surface).toBe(surface.id);
    }
    expect(client.asset).toHaveBeenCalledTimes(7);
  });

  it('enforces edit, activate, compliance and import/export permissions separately', async () => {
    const host = architecture(); await host.modules.activate(CONTRACT_MODULE_ID);
    const service = await host.services.resolve(CONTRACT_SERVICE_ID); const session = service.create({});
    const currentUser = {permissions: ['contract.view']};
    for(const [command, args] of [['contract.create', {}], ['contract.status.set', {
      status: 'draft'}], ['contract.compliance.run', {}], ['contract.export.odcs', {
      apiVersion: 'v3'}]]) await expect(host.commands.execute(command,
      {sessionId: session.id, ...args}, {contract: service, currentUser}))
      .rejects.toMatchObject({code: 'permission_denied'});
    expect(host.commands.get('contract.validate')).toMatchObject({permission: ['contract.view']});
  });

  it('rejects undocumented and malformed command arguments before execution', async () => {
    const host = architecture(); await host.modules.activate(CONTRACT_MODULE_ID);
    const service = await host.services.resolve(CONTRACT_SERVICE_ID); const session = service.create({});
    const currentUser = {permissions: ['contract.edit', 'contract.activate',
      'contract.compliance', 'contract.import_export', 'contract.view']};
    await expect(host.commands.execute('contract.validate', {sessionId: session.id, guessed: true},
      {contract: service, currentUser})).rejects.toMatchObject({code: 'invalid_arguments'});
    await expect(host.commands.execute('contract.status.set', {sessionId: session.id,
      status: 'invented'}, {contract: service, currentUser}))
      .rejects.toMatchObject({code: 'invalid_arguments'});
    await expect(host.commands.execute('contract.bind_resource', {sessionId: session.id,
      binding: {}}, {contract: service, currentUser}))
      .rejects.toMatchObject({code: 'invalid_arguments'});
  });

  it('blocks dirty close and removes every contribution atomically', async () => {
    const host = architecture(); await host.modules.activate(CONTRACT_MODULE_ID);
    const descriptor = host.surfaces.descriptor('contract.contract_editor', {
      toolInstanceId: 'one', restoreRef: 'one'});
    await expect(host.surfaces.toolRegistry.canClose(descriptor, {dirty: true}))
      .resolves.toEqual({allowed: false,
        reason: 'Save or discard Data Contract changes first.'});
    await host.modules.deactivate(CONTRACT_MODULE_ID);
    expect(host.commands.list()).toEqual([]); expect(host.surfaces.list()).toEqual([]);
    expect(host.bottom.resolve({surfaceId: 'contract.compliance'})).toEqual([]);
  });
});
