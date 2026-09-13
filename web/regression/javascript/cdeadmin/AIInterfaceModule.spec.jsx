/////////////////////////////////////////////////////////////
// Activated AI Interface module, permissions and migration gates.
/////////////////////////////////////////////////////////////

import React from 'react';
import {CommandRegistry} from 'sources/cdeadmin_ui/commands/CommandRegistry';
import {
  CapabilityRegistry, ContributionRegistry, PermissionRegistry, ServiceRegistry,
} from 'sources/cdeadmin_ui/platform/PlatformRegistry';
import {
  PLATFORM_SERVICE_IDS, registerCorePlatformServices,
} from 'sources/cdeadmin_ui/platform/PlatformServices';
import {ModuleRegistry} from 'sources/cdeadmin_ui/platform/ModuleRegistry';
import {SurfaceRegistry} from 'sources/cdeadmin_ui/platform/SurfaceRegistry';
import {ToolFactoryRegistry} from 'sources/cdeadmin_ui/workspace/ToolRegistry';
import {noRawSecrets} from 'sources/cdeadmin_ui/platform/serviceUtils';
import {
  AI_COMMAND_CATALOG, AI_CONNECTOR_TYPES, AI_INTERFACE_MANIFEST,
  AI_INTERFACE_MODULE_ID, AI_PERMISSION_CATALOG,
} from 'sources/cdeadmin_ui/specifications/ai_discovery_zero_grey';
import {
  AI_INTERFACE_RUNTIME_SERVICE_ID, AI_PRECURSOR_MIGRATION_MODE,
  AI_PRECURSOR_MODULE_ID, AI_SESSION_TURN_TASK, AIInterfaceWorkspace,
  adaptAIFormCommandArguments, registerAIInterfaceModule,
  secureAIFormCommandArguments,
} from 'sources/cdeadmin_ui/modules/ai_interface';

function projectAssets() {
  const stored = new Map();
  return {stored,
    saveAsset: jest.fn(async (projectId, assetId, request) => {
      const value = {...request, project_id: projectId, asset_id: assetId,
        version: request.expected_version + 1};
      stored.set(`${projectId}/${assetId}`, value); return value;
    }),
    asset: jest.fn(async (projectId, assetId) => stored.get(
      `${projectId}/${assetId}`)),
    revisions: jest.fn(async () => []),
    deleteAsset: jest.fn(async (_projectId, assetId, version) => ({
      asset_id: assetId, deleted_version: version})),
  };
}

function architecture(options={}) {
  const services = new ServiceRegistry(); registerCorePlatformServices(services);
  const client = projectAssets(); services.register({id: 'project.assets',
    factory: () => client});
  const registries = {services, capabilities: new CapabilityRegistry(),
    surfaces: new SurfaceRegistry(new ToolFactoryRegistry()),
    commands: new CommandRegistry(), permissions: new PermissionRegistry(),
    inspector: new ContributionRegistry('inspector'),
    toolbox: new ContributionRegistry('toolbox'),
    status: new ContributionRegistry('status'),
    activity: new ContributionRegistry('activity'),
    bottom: new ContributionRegistry('bottom')};
  const modules = new ModuleRegistry(registries);
  registerAIInterfaceModule({modules, services, options});
  return {...registries, modules, client};
}

describe('AI Interface activation', () => {
  it('registers all exact command risk/task/authority metadata and permissions', async () => {
    const host = architecture(); await host.modules.activate(AI_INTERFACE_MODULE_ID);
    expect(host.commands.list()).toHaveLength(46);
    expect(host.commands.list().map(({id, aiRiskClass: risk, task, authority}) =>
      ({id, risk, task, authority}))).toEqual(AI_COMMAND_CATALOG.commands);
    expect(host.permissions.list({moduleId: AI_INTERFACE_MODULE_ID}).map(
      ({id, description}) => [id, description])).toEqual(
      [...AI_PERMISSION_CATALOG.permissions].sort((left, right) =>
        left[0].localeCompare(right[0])));
    expect(host.commands.list().every((item) => item.macroCallable === false &&
      item.permission.length === 1)).toBe(true);
    const registered = new Set(AI_PERMISSION_CATALOG.permissions.map(([id]) => id));
    expect(host.commands.list().flatMap((item) => item.permission).every((id) =>
      registered.has(id))).toBe(true);
    expect([...registered].filter((id) => !host.commands.list().some((item) =>
      item.permission.includes(id)))).toEqual(['ai.approve_high_risk',
      'ai.approve_production', 'ai.view_sensitive_context']);
  });

  it('activates every screen and the complete shared-shell contribution set', async () => {
    const host = architecture(); await host.modules.activate(AI_INTERFACE_MODULE_ID);
    expect(host.surfaces.list()).toHaveLength(AI_INTERFACE_MANIFEST.screenCount);
    expect(host.activity.resolve().map((item) => item.id)).toEqual([
      'activity.ai-interface']);
    expect(host.inspector.resolve({surfaceId:
      'cdeadmin.ai_interface.ai_workbench'})).toHaveLength(1);
    expect(host.bottom.resolve({surfaceId:
      'cdeadmin.ai_interface.ai_workbench'})).toHaveLength(1);
    expect(host.status.resolve({surfaceId:
      'cdeadmin.ai_interface.ai_workbench'})).toHaveLength(1);
    for(const surface of host.surfaces.list()) {
      const descriptor = host.surfaces.descriptor(surface.id, {
        toolInstanceId: surface.id});
      const element = await host.surfaces.restore(descriptor, {
        commands: host.commands});
      expect(React.isValidElement(element)).toBe(true);
      expect(element.type).toBe(AIInterfaceWorkspace);
      expect(element.props.screenId).toBe(surface.id);
      expect(element.props.fieldOptions.mode).toEqual([]);
    }
  });

  it('enforces command permissions and model-authority availability', async () => {
    const host = architecture(); await host.modules.activate(AI_INTERFACE_MODULE_ID);
    const runtime = await host.services.resolve(AI_INTERFACE_RUNTIME_SERVICE_ID);
    const args = {sessionId: 'session-one', name: 'Session one',
      agentProfileRef: 'agent-one', mode: 'ASK', connectorRefs: [],
      contextRefs: []};
    await expect(host.commands.execute('ai.session.new', args,
      {aiInterface: runtime, currentUser: {id: 'one', permissions: []}}))
      .rejects.toMatchObject({code: 'permission_denied'});
    await expect(host.commands.execute('ai.session.new', args,
      {aiInterface: runtime, currentUser: {id: 'one', permissions: [
        'ai.manage_own_sessions']}})).resolves.toMatchObject({
      sessionId: 'session-one', owner: 'one'});
    await expect(host.commands.execute('ai.session.ask', {sessionId:
      'session-one'}, {aiInterface: runtime, currentUser: {id: 'one',
      permissions: ['ai.use']}})).rejects.toMatchObject({
      code: 'command_disabled'});
  });

  it('keeps non-installed connector classes unavailable without advertising them', async () => {
    const host = architecture(); await host.modules.activate(AI_INTERFACE_MODULE_ID);
    const runtime = await host.services.resolve(AI_INTERFACE_RUNTIME_SERVICE_ID);
    expect(runtime.capabilities().connectorClasses).toEqual([
      'cdeadmin_capability']);
    expect(AI_CONNECTOR_TYPES.types.map((item) => item.id).filter((id) =>
      !runtime.capabilities().connectorClasses.includes(id))).toEqual([
      'database_provider', 'scratchbird_sbsql', 'scratchbird_compatibility',
      'scratchbird_mcp', 'mcp_generic', 'external_service']);
    expect(runtime.capabilities().connectorClasses).not.toContain(
      'scratchbird_sbsql');
  });

  it('converts transient secret controls into canonical references before dispatch', () => {
    const secured = secureAIFormCommandArguments({
      formId: 'ai.database_connector',
      values: {provider: 'firebird', credential: 'cde-secret://vault/one'},
      persistedValues: {provider: 'firebird'},
      secretFieldIds: ['credential'],
    });
    expect(secured).toEqual({formId: 'ai.database_connector', values: {
      provider: 'firebird', credentialRef: 'cde-secret://vault/one'},
    persistedValues: {provider: 'firebird'}});
    expect(JSON.stringify(secured)).not.toContain('"credential"');
    expect(() => noRawSecrets(secured, 'secured form submission')).not.toThrow();
  });

  it('adapts form-native session values to the canonical runtime command', async () => {
    const host = architecture(); await host.modules.activate(AI_INTERFACE_MODULE_ID);
    const runtime = await host.services.resolve(AI_INTERFACE_RUNTIME_SERVICE_ID);
    const submission = {formId: 'ai.new_session', values: {
      name: 'Form session', agent_profile: {id: 'agent-one'}, mode: 'ANALYZE',
      project: {canonical: 'project-one'}, connector_set: [{id: 'connector-one'}],
      initial_context: [{canonical: 'resource://one'}]}, persistedValues: {}};
    await expect(host.commands.execute('ai.session.new', submission, {
      aiInterface: runtime, currentUser: {id: 'one', permissions: [
        'ai.manage_own_sessions']}})).resolves.toMatchObject({name: 'Form session',
      agentProfileRef: 'agent-one', mode: 'ANALYZE', projectId: 'project-one',
      connectorRefs: ['connector-one'], context: [{reference: 'resource://one'}]});
  });

  it('requires canonical editor identity instead of inventing asset context', () => {
    expect(() => adaptAIFormCommandArguments('ai.retention_policy.update', {
      formId: 'ai.retention_policy', values: {retain_messages: true, days: 30,
        retain_tool_results: false, audit_days: 365,
        retain_prompts_in_audit: false}}, {})).toThrow(
      'ai.retention_policy requires explicit content command context.'
    );
  });

  it('runs the built-in capability lifecycle with a dedicated non-human session', async () => {
    const host = architecture(); await host.modules.activate(AI_INTERFACE_MODULE_ID);
    const runtime = await host.services.resolve(AI_INTERFACE_RUNTIME_SERVICE_ID);
    const currentUser = {id: 'connector-admin', permissions: [
      'ai.manage_connectors']};
    const profile = {schemaVersion: 1, connectorId: 'cdeadmin-tools',
      name: 'CDEadmin tools', connectorClass: 'cdeadmin_capability', enabled: true,
      providerId: null, connectionProfileRef: null, dialectId: null,
      workareaSchemaRef: null, mcpEndpointRef: null, mcpProtocolProfile: null,
      principalBinding: 'cdeadmin-ai-tools', credentialRef: null,
      policyRef: 'tool-policy', resourceScopeRefs: [], state: 'unconfigured',
      capabilitySnapshotRef: null};
    await expect(host.commands.execute('ai.connector.create', {
      projectId: 'project-one', profile}, {aiInterface: runtime, currentUser}))
      .resolves.toMatchObject({runtime: {state: 'unconfigured'}});
    const ready = await host.commands.execute('ai.connector.enable', {
      connectorId: 'cdeadmin-tools'}, {aiInterface: runtime, currentUser});
    expect(ready).toMatchObject({state: 'ready', identity: {
      principalId: 'cdeadmin-ai-tools', borrowedInteractiveSession: false},
    capabilities: {commandExecutionAuthority: 'CommandRegistry'}});
    expect(ready.identity.sessionId).not.toBe('human-session');
  });

  it('executes governed session turns as cancellable TaskService work with retention', async () => {
    const host = architecture({sessionResponder: async ({prompt}) => ({
      text: `Response to ${prompt}`})});
    await host.modules.activate(AI_INTERFACE_MODULE_ID);
    const runtime = await host.services.resolve(AI_INTERFACE_RUNTIME_SERVICE_ID);
    const currentUser = {id: 'session-owner', permissions: [
      'ai.manage_own_sessions', 'ai.use']};
    await host.commands.execute('ai.session.new', {sessionId: 'session-task',
      agentProfileRef: 'agent-one', mode: 'ASK', connectorRefs: [],
      contextRefs: []}, {aiInterface: runtime, currentUser});
    const task = await host.commands.execute('ai.session.ask', {
      sessionId: 'session-task', prompt: 'Explain the selected object.',
      retentionPolicy: {schemaVersion: 1, policyId: 'retention-one',
        name: 'Test retention', retainMessages: true, conversationDays: 1,
        retainToolResults: false, auditDays: 30,
        retainPromptsInAudit: false}}, {aiInterface: runtime, currentUser});
    const tasks = await host.services.resolve(PLATFORM_SERVICE_IDS.TASKS);
    await expect(tasks.wait(task.id)).resolves.toEqual({
      text: 'Response to Explain the selected object.'});
    await Promise.resolve();
    expect(runtime.getSession('session-task')).toMatchObject({state: 'ready',
      activeTaskRef: null, messages: [{role: 'user'}, {role: 'assistant'}]});
  });

  it('keeps continuing work discoverable after its UI closes and displays unknown cost',
    async () => {
      let complete;
      const host = architecture({sessionResponder: () => new Promise((resolve) => {
        complete = resolve;
      })});
      await host.modules.activate(AI_INTERFACE_MODULE_ID);
      const runtime = await host.services.resolve(AI_INTERFACE_RUNTIME_SERVICE_ID);
      const currentUser = {id: 'session-owner', permissions: [
        'ai.manage_own_sessions', 'ai.use']};
      runtime.newSession({sessionId: 'session-continuing',
        agentProfileRef: 'agent-one', connectorRefs: [], contextRefs: []},
      {currentUser});
      const task = runtime.ask('session-continuing', {prompt: 'Continue safely.',
        retentionPolicy: {schemaVersion: 1, policyId: 'retention-one',
          name: 'Test retention', retainMessages: false, conversationDays: 1,
          retainToolResults: false, auditDays: 30,
          retainPromptsInAudit: false}}, {currentUser});
      expect(runtime.screenData('cdeadmin.ai_interface.run_monitor'))
        .toContainEqual(expect.objectContaining({id: task.id,
          type: AI_SESSION_TURN_TASK}));
      runtime.audit.append({eventType: 'ai.usage', initiator: 'system',
        usage: {inputTokens: 12, outputTokens: 4, toolCalls: 1}});
      expect(runtime.screenData('cdeadmin.ai_interface.usage_cost'))
        .toContainEqual(expect.objectContaining({usage: expect.objectContaining({
          inputTokens: 12, cost: 'unknown'})}));
      await Promise.resolve();
      complete({text: 'Finished'});
      const tasks = await host.services.resolve(PLATFORM_SERVICE_IDS.TASKS);
      await expect(tasks.wait(task.id)).resolves.toEqual({text: 'Finished'});
    });

  it('owns governed Discovery enrichment and analysis response boundaries', async () => {
    const discoveryResponder = jest.fn(async ({requestType}) =>
      requestType === 'enrichment' ? {proposedValue: 'Governed customer.',
        modelRef: 'model:one', profileRef: 'profile:curation', confidence: 0.9,
        sourceEvidenceRefs: ['evidence:one']} : {summary: 'Use a count aggregate.'});
    const host = architecture({discoveryResponder});
    await host.modules.activate(AI_INTERFACE_MODULE_ID);
    const runtime = await host.services.resolve(AI_INTERFACE_RUNTIME_SERVICE_ID);
    const context = {currentUser: {id: 'analyst', permissions: [
      'ai.use', 'ai.delegate_draft']}};
    expect(runtime.capabilities().discoveryAssistance).toBe(true);
    await expect(runtime.requestDiscoveryEnrichment({kind: 'description',
      targetRef: 'resource:customer', field: 'description',
      currentValue: 'Customer table', sourceEvidenceRefs: ['evidence:one'],
      permittedContext: {name: 'CUSTOMER'}}, context)).resolves.toMatchObject({
      proposedValue: 'Governed customer.', modelRef: 'model:one'});
    await expect(runtime.requestDiscoveryAnalysis({question: 'Count customers',
      connectorClass: 'database_provider', dialectId: 'firebird',
      accessSurfaceRefs: [], entities: [{canonicalRef: 'resource:customer'}],
      executionAuthorized: false}, context)).resolves.toEqual({
      summary: 'Use a count aggregate.'});
    expect(discoveryResponder.mock.calls.map(([input]) => input.requestType))
      .toEqual(['enrichment', 'analysis_plan']);
    await expect(runtime.requestDiscoveryAnalysis({question: 'Count customers',
      connectorClass: 'database_provider', dialectId: 'firebird',
      accessSurfaceRefs: [], entities: [], executionAuthorized: true}, context))
      .rejects.toThrow(/cannot claim execution authorization/);
  });

  it('keeps AI Discovery assistance explicitly unavailable when not configured', async () => {
    const host = architecture(); await host.modules.activate(AI_INTERFACE_MODULE_ID);
    const runtime = await host.services.resolve(AI_INTERFACE_RUNTIME_SERVICE_ID);
    expect(runtime.capabilities().discoveryAssistance).toBe(false);
    await expect(runtime.requestDiscoveryEnrichment({kind: 'description'}, {
      currentUser: {id: 'analyst', permissions: ['ai.use',
        'ai.delegate_draft']}})).rejects.toThrow(/No governed AI Interface/);
  });

  it('invokes registered Discovery read tools through the AI Interface authority', async () => {
    const host = architecture(); await host.modules.activate(AI_INTERFACE_MODULE_ID);
    const runtime = await host.services.resolve(AI_INTERFACE_RUNTIME_SERVICE_ID);
    runtime.toolCatalog.readTools.register({id: 'discovery.test_read',
      moduleId: 'cdeadmin.discovery_intelligence',
      description: 'Read one security-trimmed Discovery record.',
      requiredPermissions: ['ai.use', 'discovery.use'], maxRows: 1,
      maxBytes: 4096, inputSchema: {type: 'object', additionalProperties: false},
      outputSchema: {type: 'object', additionalProperties: false,
        required: ['rows'], properties: {rows: {type: 'array'}}},
      accessCheck: (context) => context.discoverySecurity?.allowed === true,
      execute: async () => ({rows: [{id: 'visible'}]}),
      classify: () => 'INTERNAL'});
    await expect(runtime.invokeReadTool({readToolId: 'discovery.test_read',
      arguments: {}}, {currentUser: {id: 'analyst', permissions:
      ['ai.delegate_read', 'ai.use', 'discovery.use']},
    discoverySecurity: {allowed: true}})).resolves.toMatchObject({
      classification: 'INTERNAL', value: {rows: [{id: 'visible'}]}});
    expect(() => runtime.invokeReadTool({readToolId: 'discovery.test_read',
      arguments: {}}, {currentUser: {id: 'analyst', permissions:
      ['ai.delegate', 'ai.use', 'discovery.use']},
    discoverySecurity: {allowed: true}})).toThrow(/ai.delegate_read/);
  });

  it('provides explicit preserve-source precursor migration and no legacy module', async () => {
    const host = architecture(); await host.modules.activate(AI_INTERFACE_MODULE_ID);
    const runtime = await host.services.resolve(AI_INTERFACE_RUNTIME_SERVICE_ID);
    expect(host.modules.has(AI_PRECURSOR_MODULE_ID)).toBe(false);
    expect(runtime.precursor).toMatchObject({moduleId: AI_PRECURSOR_MODULE_ID,
      migrationMode: AI_PRECURSOR_MIGRATION_MODE, automatic: false,
      migrate: expect.any(Function)});
    expect(runtime.migration.prepare).toEqual(expect.any(Function));
  });

  it('registers real task authorities and removes all module contributions atomically', async () => {
    const host = architecture({sessionResponder: async () => ({text: 'answer'})});
    await host.modules.activate(AI_INTERFACE_MODULE_ID);
    const tasks = await host.services.resolve(PLATFORM_SERVICE_IDS.TASKS);
    expect([...tasks.runners.keys()]).toEqual(expect.arrayContaining([
      AI_SESSION_TURN_TASK, 'cdeadmin.ai.background-run.v1',
      'cdeadmin.ai.plan.execution.v1']));
    await host.modules.deactivate(AI_INTERFACE_MODULE_ID);
    expect(host.commands.list()).toEqual([]); expect(host.surfaces.list()).toEqual([]);
    expect(host.permissions.list({moduleId: AI_INTERFACE_MODULE_ID})).toEqual([]);
  });
});
