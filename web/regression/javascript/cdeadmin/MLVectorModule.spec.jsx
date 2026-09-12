import React from 'react';
import Ajv2020 from 'ajv/dist/2020';
import {CommandRegistry} from 'sources/cdeadmin_ui/commands/CommandRegistry';
import {CapabilityRegistry, ContributionRegistry, ServiceRegistry} from 'sources/cdeadmin_ui/platform/PlatformRegistry';
import {PLATFORM_SERVICE_IDS, registerCorePlatformServices} from 'sources/cdeadmin_ui/platform/PlatformServices';
import {ModuleRegistry} from 'sources/cdeadmin_ui/platform/ModuleRegistry';
import {SurfaceRegistry} from 'sources/cdeadmin_ui/platform/SurfaceRegistry';
import {ToolFactoryRegistry} from 'sources/cdeadmin_ui/workspace/ToolRegistry';
import {MODULE_MANIFEST_SCHEMA, SCREEN_SPEC_SCHEMA} from 'sources/cdeadmin_ui';
import {
  ML_VECTOR_ASSET_TYPE, ML_VECTOR_MODULE_ID, ML_VECTOR_SERVICE_ID, MLVectorWorkspace,
  registerMLVectorModule,
} from 'sources/cdeadmin_ui/modules/ml_vector';
import manifest from 'sources/cdeadmin_ui/modules/ml_vector/specification/11_ml_vector.manifest.json';
import primary from 'sources/cdeadmin_ui/modules/ml_vector/specification/11_ml_vector_primary.screen.json';
import explorer from 'sources/cdeadmin_ui/modules/ml_vector/specification/11_ml_vector_01_vector_explorer.screen.json';
import indexDesigner from 'sources/cdeadmin_ui/modules/ml_vector/specification/11_ml_vector_02_index_designer.screen.json';
import searchConsole from 'sources/cdeadmin_ui/modules/ml_vector/specification/11_ml_vector_03_vector_search_console.screen.json';
import embedding from 'sources/cdeadmin_ui/modules/ml_vector/specification/11_ml_vector_04_embedding_pipeline.screen.json';
import registry from 'sources/cdeadmin_ui/modules/ml_vector/specification/11_ml_vector_05_model_registry.screen.json';
import evaluation from 'sources/cdeadmin_ui/modules/ml_vector/specification/11_ml_vector_06_experiments_evaluation.screen.json';
import deployment from 'sources/cdeadmin_ui/modules/ml_vector/specification/11_ml_vector_07_deployment_bindings.screen.json';
import {mlVectorDefinition} from './MLVectorTestUtils';

function architecture(client={asset: jest.fn(), update: jest.fn(), create: jest.fn()}) {
  const services = new ServiceRegistry(); registerCorePlatformServices(services);
  services.register({id: 'project.assets', factory: () => client});
  const registries = {services, capabilities: new CapabilityRegistry(),
    surfaces: new SurfaceRegistry(new ToolFactoryRegistry()), commands: new CommandRegistry(),
    inspector: new ContributionRegistry('inspector'), toolbox: new ContributionRegistry('toolbox'),
    status: new ContributionRegistry('status'), activity: new ContributionRegistry('activity'),
    bottom: new ContributionRegistry('bottom')};
  const modules = new ModuleRegistry(registries); registerMLVectorModule({modules, services});
  return {...registries, modules, client};
}
describe('ML / Vector module registration', () => {
  test('validates the exact manifest and all eight screen contracts', () => {
    const ajv = new Ajv2020({strict: false}); const validateManifest = ajv.compile(MODULE_MANIFEST_SCHEMA);
    expect(validateManifest(manifest)).toBe(true); const validateScreen = ajv.compile(SCREEN_SPEC_SCHEMA);
    for(const item of [primary, explorer, indexDesigner, searchConsole, embedding, registry,
      evaluation, deployment]) { expect(validateScreen(item)).toBe(true); expect(item.spec_gaps).toEqual([]); }
  });
  test('registers exact identities, counts and contributions', async () => {
    expect(manifest).toMatchObject({moduleId: ML_VECTOR_MODULE_ID, assetTypes: [ML_VECTOR_ASSET_TYPE]});
    expect(manifest.surfaces).toHaveLength(7); expect(manifest.commands).toHaveLength(10);
    expect(manifest.taskTypes).toHaveLength(5); expect(manifest.events).toHaveLength(4);
    const host = architecture(); await host.modules.activate(ML_VECTOR_MODULE_ID);
    expect(host.surfaces.list({assetType: ML_VECTOR_ASSET_TYPE})).toHaveLength(7);
    expect(host.commands.list().map((item) => item.id)).toEqual(manifest.commands.map((item) => item.id));
    expect(host.bottom.resolve({surfaceId: 'ml-vector.vector_search_console'})).toHaveLength(3);
    const tasks = await host.services.resolve(PLATFORM_SERVICE_IDS.TASKS);
    expect([...tasks.runners.keys()].sort()).toEqual([...manifest.taskTypes].sort());
  });
  test('restores every exact project surface', async () => {
    const client = {asset: jest.fn().mockResolvedValue({asset_type: ML_VECTOR_ASSET_TYPE,
      content: mlVectorDefinition()}), update: jest.fn(), create: jest.fn()};
    const host = architecture(client); await host.modules.activate(ML_VECTOR_MODULE_ID);
    for(const surface of manifest.surfaces) { const descriptor = host.surfaces.descriptor(
      `ml-vector.${surface.id}`, {toolInstanceId: surface.id, projectId: 'p', restoreRef: 'ml-one'});
    const element = await host.surfaces.restore(descriptor,
      {services: {'project.assets': client}, commands: host.commands});
    expect(React.isValidElement(element)).toBe(true); expect(element.type).toBe(MLVectorWorkspace);
    expect(element.props.surface).toBe(surface.id); }
  });
  test('enforces independent permissions and strict command arguments', async () => {
    const host = architecture(); await host.modules.activate(ML_VECTOR_MODULE_ID);
    const service = await host.services.resolve(ML_VECTOR_SERVICE_ID);
    const session = service.create({content: mlVectorDefinition()});
    await expect(host.commands.execute('model.version.tag', {sessionId: session.id, modelId: 'minilm',
      versionId: 'minilm-v1', tags: ['reviewed']}, {mlVector: service,
      currentUser: {permissions: ['ml_vector.view']}})).rejects.toMatchObject({code: 'permission_denied'});
    await expect(host.commands.execute('vector.index.validate', {sessionId: session.id, designId: 'documents',
      indexId: 'documents-hnsw', guessed: true}, {mlVector: service,
      currentUser: {permissions: ['ml_vector.view']}})).rejects.toMatchObject({code: 'invalid_arguments'});
  });
  test('blocks dirty close and removes contributions atomically', async () => {
    const host = architecture(); await host.modules.activate(ML_VECTOR_MODULE_ID);
    const descriptor = host.surfaces.descriptor('ml-vector.vector_explorer', {toolInstanceId: 'one'});
    await expect(host.surfaces.toolRegistry.canClose(descriptor, {dirty: true})).resolves.toEqual({
      allowed: false, reason: 'Save or discard ML / Vector asset changes first.'});
    await host.modules.deactivate(ML_VECTOR_MODULE_ID); expect(host.commands.list()).toEqual([]);
    expect(host.surfaces.list()).toEqual([]);
  });
});
