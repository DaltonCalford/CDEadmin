import React from 'react';
import Ajv2020 from 'ajv/dist/2020';
import {CommandRegistry} from 'sources/cdeadmin_ui/commands/CommandRegistry';
import {CapabilityRegistry, ContributionRegistry, ServiceRegistry} from 'sources/cdeadmin_ui/platform/PlatformRegistry';
import {PLATFORM_SERVICE_IDS, registerCorePlatformServices} from 'sources/cdeadmin_ui/platform/PlatformServices';
import {ModuleRegistry} from 'sources/cdeadmin_ui/platform/ModuleRegistry';
import {SurfaceRegistry} from 'sources/cdeadmin_ui/platform/SurfaceRegistry';
import {ToolFactoryRegistry} from 'sources/cdeadmin_ui/workspace/ToolRegistry';
import {MODULE_MANIFEST_SCHEMA, SCREEN_SPEC_SCHEMA} from 'sources/cdeadmin_ui';
import {API_ASSET_TYPE, API_MODULE_ID, API_SERVICE_ID, APIWorkspace, registerAPIModule}
  from 'sources/cdeadmin_ui/modules/api';
import manifest from 'sources/cdeadmin_ui/modules/api/specification/07_api.manifest.json';
import primary from 'sources/cdeadmin_ui/modules/api/specification/07_api_primary.screen.json';
import explorer from 'sources/cdeadmin_ui/modules/api/specification/07_api_01_api_explorer.screen.json';
import operation from 'sources/cdeadmin_ui/modules/api/specification/07_api_02_operation_designer.screen.json';
import eventAPI from 'sources/cdeadmin_ui/modules/api/specification/07_api_03_event_api_designer.screen.json';
import schema from 'sources/cdeadmin_ui/modules/api/specification/07_api_04_schema_designer.screen.json';
import security from 'sources/cdeadmin_ui/modules/api/specification/07_api_05_security_policy.screen.json';
import testConsole from 'sources/cdeadmin_ui/modules/api/specification/07_api_06_test_console.screen.json';
import docs from 'sources/cdeadmin_ui/modules/api/specification/07_api_07_docs_preview.screen.json';
import source from 'sources/cdeadmin_ui/modules/api/specification/07_api_08_source.screen.json';
import {apiDefinition} from './APITestUtils';

function architecture(client={asset: jest.fn(), update: jest.fn(), create: jest.fn()}) {
  const services = new ServiceRegistry(); registerCorePlatformServices(services);
  services.register({id: 'project.assets', factory: () => client});
  const registries = {services, capabilities: new CapabilityRegistry(),
    surfaces: new SurfaceRegistry(new ToolFactoryRegistry()), commands: new CommandRegistry(),
    inspector: new ContributionRegistry('inspector'), toolbox: new ContributionRegistry('toolbox'),
    status: new ContributionRegistry('status'), activity: new ContributionRegistry('activity'),
    bottom: new ContributionRegistry('bottom')};
  const modules = new ModuleRegistry(registries); registerAPIModule({modules, services});
  return {...registries, modules, client};
}
describe('API Designer module registration', () => {
  test('validates the exact manifest and all nine screen contracts', () => {
    const ajv = new Ajv2020({strict: false}); const validateManifest = ajv.compile(MODULE_MANIFEST_SCHEMA);
    expect(validateManifest(manifest)).toBe(true); const validateScreen = ajv.compile(SCREEN_SPEC_SCHEMA);
    for(const item of [primary, explorer, operation, eventAPI, schema, security, testConsole, docs, source]) {
      expect(validateScreen(item)).toBe(true); expect(item.spec_gaps).toEqual([]);
    }
  });
  test('registers exact identities, counts and contributions', async () => {
    expect(manifest).toMatchObject({moduleId: API_MODULE_ID, assetTypes: [API_ASSET_TYPE]});
    expect(manifest.surfaces).toHaveLength(8); expect(manifest.commands).toHaveLength(10);
    expect(manifest.taskTypes).toHaveLength(4); expect(manifest.events).toHaveLength(4);
    const host = architecture(); await host.modules.activate(API_MODULE_ID);
    expect(host.surfaces.list({assetType: API_ASSET_TYPE})).toHaveLength(8);
    expect(host.commands.list().map((item) => item.id)).toEqual(manifest.commands.map((item) => item.id));
    expect(host.bottom.resolve({surfaceId: 'api.test_console'})).toHaveLength(4);
    const tasks = await host.services.resolve(PLATFORM_SERVICE_IDS.TASKS);
    expect([...tasks.runners.keys()].sort()).toEqual([...manifest.taskTypes].sort());
  });
  test('restores every exact project surface', async () => {
    const client = {asset: jest.fn().mockResolvedValue({asset_type: API_ASSET_TYPE,
      content: apiDefinition()}), update: jest.fn(), create: jest.fn()};
    const host = architecture(client); await host.modules.activate(API_MODULE_ID);
    for(const surface of manifest.surfaces) { const descriptor = host.surfaces.descriptor(`api.${surface.id}`,
      {toolInstanceId: surface.id, projectId: 'p', restoreRef: 'api-one'});
    const element = await host.surfaces.restore(descriptor,
      {services: {'project.assets': client}, commands: host.commands});
    expect(React.isValidElement(element)).toBe(true); expect(element.type).toBe(APIWorkspace);
    expect(element.props.surface).toBe(surface.id); }
  });
  test('enforces independent permissions and strict command arguments', async () => {
    const host = architecture(); await host.modules.activate(API_MODULE_ID);
    const service = await host.services.resolve(API_SERVICE_ID); const session = service.create({content: apiDefinition()});
    await expect(host.commands.execute('api.operation.add', {sessionId: session.id,
      operation: apiDefinition().operations[0]}, {apiDesigner: service,
      currentUser: {permissions: ['api.view']}})).rejects.toMatchObject({code: 'permission_denied'});
    await expect(host.commands.execute('api.validate', {sessionId: session.id, guessed: true},
      {apiDesigner: service, currentUser: {permissions: ['api.view']}}))
      .rejects.toMatchObject({code: 'invalid_arguments'});
  });
  test('blocks dirty close and removes contributions atomically', async () => {
    const host = architecture(); await host.modules.activate(API_MODULE_ID);
    const descriptor = host.surfaces.descriptor('api.api_explorer', {toolInstanceId: 'one'});
    await expect(host.surfaces.toolRegistry.canClose(descriptor, {dirty: true})).resolves
      .toEqual({allowed: false, reason: 'Save or discard API definition changes first.'});
    await host.modules.deactivate(API_MODULE_ID); expect(host.commands.list()).toEqual([]);
    expect(host.surfaces.list()).toEqual([]);
  });
});
