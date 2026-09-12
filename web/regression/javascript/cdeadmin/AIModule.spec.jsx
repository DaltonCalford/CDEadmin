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
  AI_ASSET_TYPE, AI_MODULE_ID, AI_SERVICE_ID, AIWorkspace, registerAIModule,
} from 'sources/cdeadmin_ui/modules/ai';
import {AIAdapterRegistry} from 'sources/cdeadmin_ui/modules/ai/AIService';
import manifest from 'sources/cdeadmin_ui/modules/ai/specification/04_ai.manifest.json';
import primary from 'sources/cdeadmin_ui/modules/ai/specification/04_ai_primary.screen.json';
import assistant from 'sources/cdeadmin_ui/modules/ai/specification/04_ai_01_assistant_dock.screen.json';
import evidence from 'sources/cdeadmin_ui/modules/ai/specification/04_ai_02_evidence_viewer.screen.json';
import plans from 'sources/cdeadmin_ui/modules/ai/specification/04_ai_03_plan_review.screen.json';
import diffs from 'sources/cdeadmin_ui/modules/ai/specification/04_ai_04_diff_review.screen.json';
import settings from 'sources/cdeadmin_ui/modules/ai/specification/04_ai_05_ai_settings.screen.json';
import audit from 'sources/cdeadmin_ui/modules/ai/specification/04_ai_06_ai_audit.screen.json';
import {adapter, aiDefinition, modelProfile} from './AITestUtils';

function architecture(client={asset: jest.fn(), update: jest.fn(), create: jest.fn()}) {
  const services = new ServiceRegistry(); registerCorePlatformServices(services);
  services.register({id: 'project.assets', factory: () => client});
  const registries = {services, capabilities: new CapabilityRegistry(),
    surfaces: new SurfaceRegistry(new ToolFactoryRegistry()), commands: new CommandRegistry(),
    inspector: new ContributionRegistry('inspector'), toolbox: new ContributionRegistry('toolbox'),
    status: new ContributionRegistry('status'), activity: new ContributionRegistry('activity'),
    bottom: new ContributionRegistry('bottom')}; const modules = new ModuleRegistry(registries);
  registerAIModule({modules, services, adapters: (() => { const value = new AIAdapterRegistry();
    value.register('test-ai', adapter()); return value; })(), modelProfileResolver: async () => modelProfile()});
  return {...registries, modules, client};
}

describe('AI Assistant module registration', () => {
  test('validates the exact manifest and all seven screen contracts', () => {
    const ajv = new Ajv2020({strict: false}); const validateManifest = ajv.compile(MODULE_MANIFEST_SCHEMA);
    expect(validateManifest(manifest)).toBe(true); const validateScreen = ajv.compile(SCREEN_SPEC_SCHEMA);
    for(const item of [primary, assistant, evidence, plans, diffs, settings, audit]) {
      expect(validateScreen(item)).toBe(true); expect(item.spec_gaps).toEqual([]);
    }
  });
  test('registers exact identities, counts, task runners and shell contributions', async () => {
    expect(manifest).toMatchObject({moduleId: AI_MODULE_ID, assetTypes: [AI_ASSET_TYPE]});
    expect(manifest.surfaces).toHaveLength(6); expect(manifest.commands).toHaveLength(10);
    expect(manifest.taskTypes).toHaveLength(3); expect(manifest.events).toHaveLength(4);
    const host = architecture(); await host.modules.activate(AI_MODULE_ID);
    expect(host.surfaces.list({assetType: AI_ASSET_TYPE})).toHaveLength(6);
    expect(host.commands.list().map((item) => item.id)).toEqual(manifest.commands.map((item) => item.id));
    expect(host.bottom.resolve({surfaceId: 'ai.assistant_dock'})).toHaveLength(4);
    const tasks = await host.services.resolve(PLATFORM_SERVICE_IDS.TASKS);
    expect([...tasks.runners.keys()].sort()).toEqual([...manifest.taskTypes].sort());
  });
  test('restores every exact project surface', async () => {
    const client = {asset: jest.fn().mockResolvedValue({asset_type: AI_ASSET_TYPE,
      content: aiDefinition()}), update: jest.fn(), create: jest.fn()};
    const host = architecture(client); await host.modules.activate(AI_MODULE_ID);
    for(const surface of manifest.surfaces) { const descriptor = host.surfaces.descriptor(`ai.${surface.id}`,
      {toolInstanceId: surface.id, projectId: 'p', restoreRef: 'ai-one'});
    const element = await host.surfaces.restore(descriptor,
      {services: {'project.assets': client}, commands: host.commands});
    expect(React.isValidElement(element)).toBe(true); expect(element.type).toBe(AIWorkspace);
    expect(element.props.surface).toBe(surface.id); }
  });
  test('enforces independent permissions and strict command arguments', async () => {
    const host = architecture(); await host.modules.activate(AI_MODULE_ID);
    const service = await host.services.resolve(AI_SERVICE_ID); const session = service.create({content: aiDefinition()});
    await expect(host.commands.execute('ai.session.new', {sessionId: session.id}, {ai: service,
      currentUser: {permissions: []}})).rejects.toMatchObject({code: 'permission_denied'});
    await expect(host.commands.execute('ai.ask', {sessionId: session.id, prompt: 'hello', guessed: true},
      {ai: service, currentUser: {permissions: ['ai.use']}})).rejects.toMatchObject({code: 'invalid_arguments'});
  });
  test('keeps all AI commands non-macro and non-AI-callable', async () => {
    const host = architecture(); await host.modules.activate(AI_MODULE_ID);
    expect(host.commands.list().every((item) => !item.aiEligible && !item.macroCallable)).toBe(true);
    const matrix = Object.fromEntries(host.commands.list().map((item) => [item.id,
      {confirmation: item.confirmationIntent, task: item.createsTask}]));
    expect(matrix['ai.plan.execute']).toEqual({confirmation: 'standard_consequential',
      task: 'ai.plan.execution'}); for(const id of manifest.commands.map((item) => item.id)
      .filter((id) => id !== 'ai.plan.execute')) expect(matrix[id]).toEqual({confirmation: 'none', task: ''});
  });
  test('blocks dirty close and removes all contributions atomically', async () => {
    const host = architecture(); await host.modules.activate(AI_MODULE_ID);
    const descriptor = host.surfaces.descriptor('ai.assistant_dock', {toolInstanceId: 'one'});
    await expect(host.surfaces.toolRegistry.canClose(descriptor, {dirty: true})).resolves.toEqual({
      allowed: false, reason: 'Save or discard AI asset changes first.'});
    await host.modules.deactivate(AI_MODULE_ID); expect(host.commands.list()).toEqual([]);
    expect(host.surfaces.list()).toEqual([]);
  });
});
