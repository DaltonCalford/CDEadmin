/////////////////////////////////////////////////////////////
// Activated zero-grey AI Interface module composition boundary.
/////////////////////////////////////////////////////////////

import React from 'react';
import {Box} from '@mui/material';
import {platformValue} from '../../platform/serviceUtils';
import {ProjectAssetClient} from '../../projects/ProjectAssetClient';
import {PLATFORM_SERVICE_IDS} from '../../platform/PlatformServices';
import {PROJECT_ASSET_SERVICE_ID} from '../../integrations/ddn/module';
import {
  AI_COMMAND_CATALOG, AI_INTERFACE_MANIFEST, AI_INTERFACE_MODULE_ID,
  AI_PERMISSION_CATALOG,
} from '../../specifications/ai_discovery_zero_grey';
import {AIAssetAuthority} from './AIAssetAuthority';
import {
  AIConnectorAdapterRegistry, AIConnectorService,
} from './AIConnectorRegistry';
import {AIAuditService} from './AIAuditService';
import {AIEmergencyControlService} from './AIEmergencyControlService';
import {AIApprovalService} from './AIApprovalService';
import {AIPlanService} from './AIPlanService';
import {AIPlanExecutionService} from './AIPlanExecutionService';
import {AIAuthorizationService} from './AIAuthorizationService';
import {AIResultHandleService} from './AIResultHandleService';
import {AIQueryAuthority} from './AIQueryAuthority';
import {AIBackgroundRunService} from './AIBackgroundRunService';
import {AIToolCatalog} from './AIToolPublication';
import {AICompatibilityMigrationService} from './AICompatibilityMigrationService';
import {
  AI_INTERFACE_RUNTIME_SERVICE_ID, AIInterfaceRuntimeService,
} from './AIInterfaceRuntimeService';
import {
  AI_INTERFACE_SCREENS, AI_SCREEN_GROUPS,
} from './AIInterfaceContracts';
import {AIInterfaceWorkspace} from './AIInterfaceWorkspace';

export const AI_PRECURSOR_MODULE_ID = 'cdeadmin.ai';
export const AI_PRECURSOR_MIGRATION_MODE = 'explicit-preserve-source';
export {AI_INTERFACE_MODULE_ID, AI_INTERFACE_RUNTIME_SERVICE_ID};

const JSON_OBJECT_SCHEMA = Object.freeze({type: 'object'});
const RISK_NUMBER = (risk) => Number(String(risk).slice(1));

const PERMISSIONS = Object.freeze({
  'ai.session.new': 'ai.manage_own_sessions',
  'ai.session.archive': 'ai.manage_own_sessions',
  'ai.session.ask': 'ai.use', 'ai.session.cancel': 'ai.use',
  'ai.context.add': 'ai.use', 'ai.context.remove': 'ai.use',
  'ai.context.set_exposure': 'ai.use',
  'ai.plan.create': 'ai.delegate_draft', 'ai.plan.validate': 'ai.delegate_draft',
  'ai.plan.approve': 'ai.delegate_write', 'ai.plan.reject': 'ai.delegate_draft',
  'ai.plan.execute': 'ai.delegate_write',
  'ai.connector.create': 'ai.manage_connectors',
  'ai.connector.update': 'ai.manage_connectors',
  'ai.connector.test': 'ai.manage_connectors',
  'ai.connector.enable': 'ai.manage_connectors',
  'ai.connector.disable': 'ai.manage_connectors',
  'ai.connector.revoke': 'ai.manage_connectors',
  'ai.connector.refresh_capabilities': 'ai.manage_connectors',
  'ai.agent_profile.create': 'ai.manage_agent_profiles',
  'ai.agent_profile.update': 'ai.manage_agent_profiles',
  'ai.agent_profile.clone': 'ai.manage_agent_profiles',
  'ai.agent_profile.enable': 'ai.manage_agent_profiles',
  'ai.agent_profile.disable': 'ai.manage_agent_profiles',
  'ai.model_profile.create': 'ai.manage_model_profiles',
  'ai.model_profile.update': 'ai.manage_model_profiles',
  'ai.model_profile.test': 'ai.manage_model_profiles',
  'ai.model_profile.disable': 'ai.manage_model_profiles',
  'ai.tool_policy.update': 'ai.admin', 'ai.data_policy.update': 'ai.admin',
  'ai.approval_policy.update': 'ai.admin', 'ai.budget_policy.update': 'ai.admin',
  'ai.retention_policy.update': 'ai.admin',
  'ai.query.compile': 'ai.delegate_read', 'ai.query.explain': 'ai.delegate_read',
  'ai.query.execute_read': 'ai.delegate_read',
  'ai.query.execute_mutation': 'ai.delegate_write',
  'ai.cdeadmin.command.propose': 'ai.delegate_draft',
  'ai.cdeadmin.command.execute': 'ai.delegate_write',
  'ai.mcp.tools.refresh': 'ai.manage_connectors',
  'ai.mcp.tool.invoke': 'ai.delegate_read', 'ai.audit.export': 'ai.export_audit',
  'ai.run.cancel': 'ai.use', 'ai.emergency.disable_writes': 'ai.admin',
  'ai.emergency.disable_all': 'ai.admin',
  'ai.emergency.invalidate_approvals': 'ai.admin',
});

function label(commandId) {
  return commandId.replace(/^ai\./, '').split('.').flatMap((part) =>
    part.split('_')).map((part) => `${part[0].toUpperCase()}${part.slice(1)}`)
    .join(' ');
}

function exposure(item) {
  if(!item.task) return 'hidden';
  if(RISK_NUMBER(item.risk) <= 1) return 'read_only';
  if(RISK_NUMBER(item.risk) <= 3) return 'draft_only';
  return item.risk === 'R7' ? 'hidden' : 'executable';
}

function runtimeFrom(context) {
  const runtime = context.aiInterface ??
    context.services?.[AI_INTERFACE_RUNTIME_SERVICE_ID] ?? context.service;
  if(!runtime || typeof runtime.execute !== 'function') throw new Error(
    'AI Interface runtime authority is unavailable.'
  );
  return runtime;
}

export function secureAIFormCommandArguments(args={}) {
  if(!args.formId || !args.values || !args.persistedValues) return args;
  const safeValues = {...args.persistedValues};
  const secretIds = new Set(args.secretFieldIds ?? []);
  for(const fieldId of secretIds) {
    const reference = args.values[fieldId];
    if(reference !== null && reference !== undefined && reference !== '') {
      safeValues[`${fieldId}Ref`] = platformValue(reference,
        `${fieldId} credential reference`, 2048);
    }
  }
  return Object.freeze({formId: args.formId, values: Object.freeze(safeValues),
    persistedValues: Object.freeze({...args.persistedValues})});
}

export function aiInterfaceCommandDefinitions() {
  return AI_COMMAND_CATALOG.commands.map((item) => Object.freeze({
    id: item.id, label: label(item.id),
    description: `${label(item.id)} through the governed AI Interface authority.`,
    iconKey: 'tool.ai', permission: [PERMISSIONS[item.id]],
    surfaces: ['tools', 'toolbar'], macroCallable: false,
    aiEligible: item.task, aiExposure: exposure(item), aiRiskClass: item.risk,
    aiModuleId: AI_INTERFACE_MODULE_ID,
    aiArgumentSchema: exposure(item) === 'hidden' ? undefined : JSON_OBJECT_SCHEMA,
    aiResultSchema: exposure(item) === 'hidden' ? undefined : JSON_OBJECT_SCHEMA,
    aiContextCostHint: 0, authority: item.authority, task: item.task,
    createsTask: item.task ? `${item.id}.task` : '',
    auditCategory: 'ai_interface', requiresConfirmation: RISK_NUMBER(item.risk) >= 4,
    confirmationIntent: RISK_NUMBER(item.risk) >= 6 ? 'high_impact' :
      RISK_NUMBER(item.risk) >= 4 ? 'consequential' : 'none',
    enabledWhen: (context) => {
      const runtime = context.aiInterface ??
        context.services?.[AI_INTERFACE_RUNTIME_SERVICE_ID];
      if(item.id === 'ai.session.ask') return runtime?.capabilities().sessionTurns === true;
      if(item.id === 'ai.model_profile.test') return runtime?.capabilities().modelTesting === true;
      if(item.id.startsWith('ai.cdeadmin.command.')) return runtime?.capabilities().toolCatalog === true;
      return true;
    },
    visibleWhen: () => true,
    disabledReason: `${label(item.id)} authority is not configured.`,
    validateArguments: (args) => args && !Array.isArray(args) &&
      typeof args === 'object' ? true : 'Command arguments must be an object.',
    execute: (args, context) => runtimeFrom(context).execute(item.id, args, context),
  }));
}

function permissionDefinitions() {
  return AI_PERMISSION_CATALOG.permissions.map(([id, description]) => ({id,
    description}));
}

function resultEnvelope(value) {
  if(value && ['success', 'partial', 'refused', 'failed',
    'approval_required'].includes(value.status)) return value;
  return {status: 'success', result: value ?? null, diagnostics: [],
    resourceRefs: [], assetRefs: [], taskRefs: [], nextAllowedActions: [],
    auditRef: null};
}

function builtInCapabilityAdapter(commands) {
  let profile; let closed = false; let sequence = 0;
  const catalog = () => commands.list().filter((command) =>
    command.aiExposure !== 'hidden').map((command) => ({id: command.id,
    riskClass: command.aiRiskClass, exposure: command.aiExposure,
    moduleId: command.aiModuleId, argumentSchema: command.aiArgumentSchema,
    resultSchema: command.aiResultSchema}));
  return {describe: async () => ({connectorClass: 'cdeadmin_capability',
    implementation: 'CDEadmin CommandRegistry', version: '1.0.0'}),
  validateConfiguration: async (input) => { profile = input; return {valid: true,
    errors: []}; },
  authenticateOrResolveIdentity: async ({connectorOwner}) => ({principalId:
    profile.principalBinding, sessionId: `${connectorOwner}:capability`,
  borrowedInteractiveSession: false}),
  discoverCapabilities: async () => ({snapshotRef: 'cdeadmin-command-catalog:v1',
    toolCatalog: true, commandExecutionAuthority: 'CommandRegistry',
    commandCount: catalog().length}),
  test: async () => ({success: !closed, category: closed ? 'transport' : 'ok',
    message: closed ? 'Connector is closed.' : 'CommandRegistry is available.'}),
  prepare: async (operation) => ({preparedId: `capability-${++sequence}`,
    sideEffects: false, operationKind: 'metadata_read', connectorSnapshot: {},
    canonicalResourceRefs: [], accessSurfaceRefs: [], normalizedArguments: operation,
    nativeCompiledArtifact: null, riskClass: 'R0', estimatedEffects: [], budgets: {},
    validation: [{passed: true}], expiresAt: new Date(Date.now() + 30000).toISOString(),
    catalog: catalog()}),
  execute: async (prepared) => resultEnvelope({catalog: prepared.catalog}),
  cancel: async () => false,
  health: async () => ({dimensions: {transport: 'ready', authentication: 'ready',
    authorization: 'ready', capability_discovery: 'ready',
    version_compatibility: 'ready', task_execution: 'ready'}}),
  close: async () => { closed = true; }};
}

function allow(reason, evidenceRef) {
  return {allowed: true, reason, evidenceRef};
}
function deny(reason) { return {allowed: false, reason, evidenceRef: null}; }

function createRuntime({resolved, commands, options}) {
  const audit = new AIAuditService();
  const emergency = new AIEmergencyControlService({audit});
  const assets = new AIAssetAuthority({projectAssets:
    resolved[PROJECT_ASSET_SERVICE_ID]});
  const adapters = options.connectorAdapters ?? new AIConnectorAdapterRegistry();
  if(!adapters.has('cdeadmin_capability')) adapters.register({
    connectorClass: 'cdeadmin_capability', version: '1.0.0',
    factory: () => builtInCapabilityAdapter(commands),
  });
  const credentials = resolved[PLATFORM_SERVICE_IDS.CREDENTIALS];
  const credentialResolver = options.credentialResolver ?? (async (reference,
    authorization) => {
    const match = /^cde-secret:\/\/([^/]+)\/(.+)$/.exec(String(reference));
    if(!match) throw new Error('AI connectors require a CredentialRef canonical reference.');
    return credentials.resolve({schema: 'cdeadmin.credential-ref.v1',
      scheme: decodeURIComponent(match[1]), id: decodeURIComponent(match[2]),
      canonical: reference}, authorization);
  });
  const connectors = new AIConnectorService({adapters, credentialResolver});
  const approvals = new AIApprovalService({audit,
    approvalEpoch: () => emergency.approvalEpoch()});
  let plans;
  const validateStep = options.validatePlanStep ?? (async (step, {plan}) => {
    if(!step.connectorRef) return {valid: false,
      checkId: plan.validationChecks[0] ?? 'connector-required',
      reason: 'No connector is bound to this executable step.', evidenceRef: null,
      riskClass: 'R0', approvalRequired: false, approvalBinding: null,
      preparedOperation: null, retry: {kind: 'mutation',
        providerReadIdempotent: false, explicitlyIdempotent: false,
        backendIdempotencyRef: null}};
    const prepared = await connectors.prepare(step.connectorRef, {
      operationKind: step.kind, commandId: step.commandId,
      normalizedArguments: step.arguments, resourceRefs: step.resourceRefs});
    const riskClass = prepared.riskClass;
    const approvalRequired = RISK_NUMBER(riskClass) >= 4;
    const snapshot = plan.connectorSnapshots.find((item) => String(
      item.connectorId ?? item.connectorRef ?? item.id) === step.connectorRef);
    return {valid: true, checkId: prepared.validation?.[0]?.checkId ??
      plan.validationChecks[0], reason: '', evidenceRef: prepared.preparedId,
    riskClass, approvalRequired, approvalBinding: approvalRequired ? {
      requirementId: plan.requiredApprovals[0], stepIds: [step.stepId],
      connectorRef: step.connectorRef, principalRef: snapshot.principalBinding,
      resourceRefs: step.resourceRefs, environment: prepared.environment ?? 'explicit',
      riskClass} : null, preparedOperation: {
      preparedId: prepared.preparedId, connectorSnapshot: snapshot,
      operationKind: prepared.operationKind, canonicalResourceRefs:
        prepared.canonicalResourceRefs, accessSurfaceRefs: prepared.accessSurfaceRefs,
      normalizedArguments: prepared.normalizedArguments,
      nativeCompiledArtifact: prepared.nativeCompiledArtifact,
      riskClass, estimatedEffects: prepared.estimatedEffects,
      budgets: prepared.budgets, validation: prepared.validation,
      expiresAt: prepared.expiresAt}, retry: prepared.retry ?? {kind:
      riskClass === 'R0' || riskClass === 'R1' ? 'read' : 'mutation',
    providerReadIdempotent: false, explicitlyIdempotent: false,
    backendIdempotencyRef: null}};
  });
  plans = new AIPlanService({approvals, validateStep,
    contextRevision: options.contextRevision ?? (async () =>
      'unavailable:context-revision-authority'), audit});
  const executeStep = options.executePlanStep ?? (async ({step,
    preparedOperation, approvalEvidence}, context) => {
    if(preparedOperation && step.connectorRef) return resultEnvelope(
      await connectors.execute(step.connectorRef, preparedOperation.preparedId,
        approvalEvidence));
    if(step.commandId) return resultEnvelope(await commands.execute(
      step.commandId, step.arguments, context));
    return {status: 'refused', result: null,
      diagnostics: [{message: 'The plan step has no executable authority.'}],
      resourceRefs: step.resourceRefs, assetRefs: step.assetRefs, taskRefs: [],
      nextAllowedActions: [], auditRef: null};
  });
  const planExecution = new AIPlanExecutionService({plans,
    tasks: resolved[PLATFORM_SERVICE_IDS.TASKS], executeStep,
    cancelStep: options.cancelPlanStep ?? (async ({step,
      preparedOperation}) => step.connectorRef && preparedOperation ?
      connectors.cancel(step.connectorRef, preparedOperation.preparedId) : false),
    emergency, audit});
  const authorization = new AIAuthorizationService({commands,
    emergencyState: (request) => emergency.authorizationCheck(request),
    modelHealth: ({model}) => model.enabled ? allow('Model profile is enabled.',
      `ai-model:${model.profileId}`) : deny('Model profile is disabled.'),
    connectorHealth: ({connector}) => {
      try {
        const value = connectors.get(connector.connectorId);
        if(['ready', 'degraded'].includes(value.state)) return allow(
          'Connector has verified usable state.', `ai-connector:${value.revision}`
        );
        return deny(`Connector is ${value.state}.`);
      } catch(error) { return deny(error.message); }
    },
    delegation: ({agent, connector}) => {
      if(agent.connectorRefs.includes(connector.connectorId)) return allow(
        'Agent explicitly binds the connector.', `ai-agent:${agent.profileId}`
      );
      return deny('Agent does not bind this connector.');
    },
    connectorPolicy: ({connector, context}) => connector.policyRef ===
      context.dataPolicy.policyId ? allow('Connector data policy matches.',
        `ai-policy:${connector.policyRef}`) : deny('Connector data policy does not match.'),
    environmentPolicy: ({operation}) => operation.environment ?
      allow('Target environment is explicit.', `environment:${operation.environment}`) :
      deny('Target environment is not explicit.'),
    databasePrecheck: ({connector}) => { try { const value = connectors.get(
      connector.connectorId); return value.identity?.principalId === connector.principalBinding ?
      allow('Dedicated database principal is authenticated.',
        `ai-principal:${connector.principalBinding}`) :
      deny('Dedicated database principal is not authenticated.'); } catch(error) {
      return deny(error.message); }},
    dataEgress: ({model, dataPolicy}) => !dataPolicy.allowedModelProfileRefs.length ||
      dataPolicy.allowedModelProfileRefs.includes(model.profileId) ?
      allow('Model is admitted by data policy.', `ai-data-policy:${dataPolicy.policyId}`) :
      deny('Model is not admitted by data policy.'),
    budget: () => allow('Explicit budget values passed deterministic validation.',
      'ai-budget:deterministic'),
    planValidation: ({operation}) => !operation.plan && ['R0', 'R1'].includes(
      operation.riskClass) ? allow('Low-risk direct operation is permitted.',
        'ai-plan:bounded-direct') : plans.authorizationCheck({operation}),
    approvalValidation: ({operation, approvalPolicy}) => approvals.authorizationCheck(
      {operation, approvalPolicy}),
    backendAuthorization: ({connector}) => { try { return connectors.get(
      connector.connectorId).identity?.principalId === connector.principalBinding ?
      allow('Backend principal remains bound.', `ai-principal:${connector.principalBinding}`) :
      deny('Backend principal binding is no longer current.'); } catch(error) {
      return deny(error.message); }},
  });
  const resultHandles = new AIResultHandleService({authorize: (descriptor,
    _operation, context) => context.currentUser?.id === descriptor.ownerId ||
      context.currentUser?.permissions?.includes('ai.admin')});
  const queries = new AIQueryAuthority({connectors, authorization, resultHandles});
  const toolCatalog = new AIToolCatalog({commands, authorization});
  const backgroundRuns = new AIBackgroundRunService({
    tasks: resolved[PLATFORM_SERVICE_IDS.TASKS],
    runTurn: options.backgroundRunTurn ?? (async () => ({status: 'failed',
      resultRefs: [], taskRefs: [], nextState: null,
      diagnostics: [{message: 'No governed background model authority is configured.'}]})),
    emergency, audit});
  const runtime = new AIInterfaceRuntimeService({assets, connectors, plans,
    planExecution, queries, audit, emergency, backgroundRuns,
    tasks: resolved[PLATFORM_SERVICE_IDS.TASKS], toolCatalog,
    modelTester: options.modelTester ?? null,
    sessionResponder: options.sessionResponder ?? null,
    discoveryResponder: options.discoveryResponder ?? null});
  runtime.authorization = authorization; runtime.approvals = approvals;
  runtime.resultHandles = resultHandles;
  runtime.migration = new AICompatibilityMigrationService({assets, audit});
  runtime.precursor = Object.freeze({moduleId: AI_PRECURSOR_MODULE_ID,
    migrationMode: AI_PRECURSOR_MIGRATION_MODE, automatic: false,
    migrate: (projectId, migrationPackage, context) => runtime.migration.migrate(
      projectId, migrationPackage, context)});
  const disposeRuntime = runtime.dispose.bind(runtime);
  runtime.dispose = async () => {
    backgroundRuns.dispose(); planExecution.dispose(); disposeRuntime();
    await connectors.close();
  };
  return runtime;
}

function surfaceRuntime(registry, descriptor, context) {
  return registry.resolve(AI_INTERFACE_RUNTIME_SERVICE_ID).then((runtime) => {
    const screenId = descriptor.toolKind;
    const workspace = context.aiInterfaceWorkspace ?? {};
    const connectorClasses = runtime.capabilities().connectorClasses;
    const fieldOptions = {...(workspace.fieldOptions ?? {}),
      mode: connectorClasses.filter((item) => ['database_provider',
        'scratchbird_sbsql', 'scratchbird_compatibility', 'scratchbird_mcp',
        'mcp_generic'].includes(item))};
    return React.createElement(AIInterfaceWorkspace, {...workspace, screenId,
      data: runtime.screenData(screenId),
      fieldOptions,
      commandAvailability: Object.fromEntries(aiInterfaceCommandDefinitions().map(
        (command) => [command.id, command.enabledWhen({aiInterface: runtime})])),
      executeCommand: (id, args, commandContext={}) => context.commands.execute(
        id, secureAIFormCommandArguments(args), {...context, ...commandContext, aiInterface: runtime,
          service: runtime}),
      onNavigate: (next) => context.openSurface?.({toolKind: next}),
      onDetach: context.detachSurface ? () => context.detachSurface(descriptor.id) : null,
      onStateChange: context.setSurfaceState,
    });
  });
}

export function aiInterfaceModuleDefinition(registry) {
  return {id: AI_INTERFACE_MODULE_ID, version: '1.0.0',
    title: 'AI Interface', iconKey: 'tool.ai',
    serviceRequirements: [AI_INTERFACE_RUNTIME_SERVICE_ID], contributions: {
      permissions: permissionDefinitions(),
      commands: aiInterfaceCommandDefinitions(),
      surfaces: AI_INTERFACE_SCREENS.map((screen) => ({id: screen.screen_id,
        title: label(screen.screen_id.replace(`${AI_INTERFACE_MODULE_ID}.`, '')),
        iconKey: 'tool.ai', assetTypes: [...AI_INTERFACE_MANIFEST.assetTypes],
        readOnly: false, editable: true, detachable: true, duplicable: true,
        fullscreen: true,
        restore: (descriptor, context) => surfaceRuntime(registry, descriptor, context),
        checkpoint: async (_descriptor, context) => context.aiInterfaceState ?? null,
        canClose: async (_descriptor, context) => context.dirty ? {allowed: false,
          reason: 'Save or discard AI Interface changes first.'} :
          {allowed: true, reason: ''}})),
      activity: [{id: 'activity.ai-interface', label: 'AI Interface',
        iconKey: 'tool.ai', priority: 45,
        render: () => <AIInterfaceActivity />}],
      inspector: [{id: 'inspector.ai-interface', label: 'AI Interface', priority: 45,
        when: (context) => context.surfaceId?.startsWith(`${AI_INTERFACE_MODULE_ID}.`),
        render: (context) => context.aiInterfaceInspector ?? {}}],
      bottom: [{id: 'bottom.ai-interface.tasks', label: 'Tasks', priority: 95,
        when: (context) => context.surfaceId?.startsWith(`${AI_INTERFACE_MODULE_ID}.`),
        render: (context) => context.aiInterfaceTasks ?? []}],
      status: [{id: 'status.ai-interface', label: 'AI Interface', priority: 45,
        when: (context) => context.surfaceId?.startsWith(`${AI_INTERFACE_MODULE_ID}.`),
        value: (context) => context.aiInterfaceState ?? 'ready'}],
    }};
}

function AIInterfaceActivity() {
  return <Box component="nav" aria-label="AI Interface">
    {AI_SCREEN_GROUPS.map((group) => <Box key={group.id} sx={{p: 1}}>
      <Box component="strong">{group.label}</Box>
      {group.screens.map((screen) => <Box key={screen}
        component="button" type="button" onClick={() => window.dispatchEvent(
          new CustomEvent('cdeadmin:ai-interface-open', {detail: {
            screenId: `${AI_INTERFACE_MODULE_ID}.${screen}`}}))}
        sx={{display: 'block', border: 0, bgcolor: 'transparent', cursor: 'pointer'}}>
        {label(screen)}
      </Box>)}
    </Box>)}
  </Box>;
}

export function registerAIInterfaceModule({modules, services, api,
  options={}}={}) {
  if(!modules || !services) throw new TypeError(
    'AI Interface registration requires module and service registries.'
  );
  const removers = [];
  if(!services.has(PROJECT_ASSET_SERVICE_ID)) removers.push(services.register({
    id: PROJECT_ASSET_SERVICE_ID, version: '1.0.0',
    factory: () => new ProjectAssetClient(api)}));
  if(!services.has(AI_INTERFACE_RUNTIME_SERVICE_ID)) removers.push(services.register({
    id: AI_INTERFACE_RUNTIME_SERVICE_ID, version: '1.0.0', dependencies: [
      PLATFORM_SERVICE_IDS.TASKS, PLATFORM_SERVICE_IDS.CREDENTIALS,
      PROJECT_ASSET_SERVICE_ID], factory: ({services: resolved}) => createRuntime({
      resolved, commands: modules.commands, options})}));
  const removeModule = modules.register(aiInterfaceModuleDefinition(services));
  return () => { removeModule(); removers.reverse().forEach((remove) => remove()); };
}
