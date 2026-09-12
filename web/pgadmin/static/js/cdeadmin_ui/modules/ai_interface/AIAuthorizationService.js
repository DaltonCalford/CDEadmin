/////////////////////////////////////////////////////////////
// Deterministic CDEadmin AI authorization authority.
/////////////////////////////////////////////////////////////

import {noRawSecrets, plainObject, platformValue, immutable} from '../../platform/serviceUtils';
import {AI_COMMAND_RISKS} from '../../commands/CommandRegistry';
import {AI_CLASSIFICATIONS, AI_EXPOSURE_LEVELS, validateAIAsset} from './AIAssetContracts';

export const AI_AUTHORIZATION_SERVICE_ID = 'cdeadmin.ai_interface.authorization';
export const AI_AUTHORIZATION_CHECKS = Object.freeze([
  'emergency_state', 'agent_profile', 'model_profile', 'connector', 'ai_use_permission',
  'delegation', 'tool_exposure', 'tool_policy', 'connector_policy', 'resource_scope',
  'environment_policy', 'database_principal', 'data_egress', 'budget', 'plan', 'approval',
  'backend_authorization',
]);

const OPERATIONS = ['metadata_read', 'data_read', 'query_compile', 'query_explain', 'query_read',
  'query_mutation', 'project_draft', 'command', 'task'];
const AUTHORITY_NAMES = ['emergencyState', 'modelHealth', 'connectorHealth', 'delegation',
  'connectorPolicy', 'environmentPolicy', 'databasePrecheck', 'dataEgress', 'budget',
  'planValidation', 'approvalValidation', 'backendAuthorization'];

function exact(value, fields, label) {
  plainObject(value, label); const unknown = Object.keys(value).filter((field) => !fields.includes(field));
  if(unknown.length) throw new TypeError(`${label} contains unsupported field ${unknown[0]}.`);
}
function strings(value, label) {
  if(!Array.isArray(value) || value.length > 10000) throw new TypeError(`${label} must be a bounded array.`);
  const result = value.map((item) => platformValue(item, `${label} value`, 2048));
  if(new Set(result).size !== result.length) throw new TypeError(`${label} contains duplicates.`);
  return result;
}
function riskIndex(risk) { const result = AI_COMMAND_RISKS.indexOf(risk);
  if(result < 0) throw new TypeError('AI operation risk class is invalid.'); return result; }
function permissionForRisk(risk) {
  if(['R0', 'R1'].includes(risk)) return ['ai.delegate_read'];
  if(['R2', 'R3'].includes(risk)) return ['ai.delegate_draft'];
  if(risk === 'R4') return ['ai.delegate_write'];
  if(risk === 'R5') return ['ai.delegate_write', 'ai.approve_high_risk'];
  if(risk === 'R6') return ['ai.delegate_write', 'ai.approve_production'];
  return [];
}
function authorityResult(value, name) {
  exact(value, ['allowed', 'reason', 'evidenceRef'], `AI ${name} authority result`);
  if(typeof value.allowed !== 'boolean') throw new TypeError(`AI ${name} authority must decide explicitly.`);
  return immutable({allowed: value.allowed, reason: String(value.reason ?? ''),
    evidenceRef: value.evidenceRef == null ? null : platformValue(value.evidenceRef,
      `AI ${name} evidence reference`, 2048)});
}
function stableJson(value) { if(Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if(value && typeof value === 'object') return `{${Object.keys(value).sort().map((key) =>
    `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`; return JSON.stringify(value); }
function operation(input) {
  const fields = ['operationId', 'commandId', 'operationKind', 'resourceRefs', 'accessSurfaceRefs',
    'environment', 'modelVisibleContent', 'estimatedBudget', 'normalizedArguments', 'riskClass',
    'plan', 'approvalEvidence', 'phase'];
  exact(input, fields, 'AI authorization operation'); noRawSecrets(input, 'AI authorization operation');
  const operationKind = platformValue(input.operationKind, 'AI operation kind');
  if(!OPERATIONS.includes(operationKind)) throw new TypeError('AI operation kind is invalid.');
  if(!['plan', 'execute'].includes(input.phase)) throw new TypeError('AI authorization phase is invalid.');
  const riskClass = platformValue(input.riskClass, 'AI operation risk class'); riskIndex(riskClass);
  return immutable({operationId: platformValue(input.operationId, 'AI operation ID'),
    commandId: platformValue(input.commandId, 'AI command ID'), operationKind,
    resourceRefs: strings(input.resourceRefs ?? [], 'AI operation resources'),
    accessSurfaceRefs: strings(input.accessSurfaceRefs ?? [], 'AI operation access surfaces'),
    environment: platformValue(input.environment, 'AI operation environment'),
    modelVisibleContent: input.modelVisibleContent ?? null,
    estimatedBudget: immutable({...plainObject(input.estimatedBudget ?? {}, 'AI estimated budget')}),
    normalizedArguments: immutable({...plainObject(input.normalizedArguments,
      'AI normalized arguments')}), riskClass,
    plan: input.plan ?? null, approvalEvidence: input.approvalEvidence ?? null, phase: input.phase});
}
function contentAllowed(content, dataPolicy) {
  if(content == null) return true; exact(content, ['classification', 'exposureLevel', 'remote',
    'containsSamples', 'containsSource', 'sampleRows', 'textCharacters'], 'AI model-visible content');
  const classification = platformValue(content.classification, 'AI content classification');
  const exposure = platformValue(content.exposureLevel, 'AI content exposure');
  if(!AI_CLASSIFICATIONS.includes(classification) || !AI_EXPOSURE_LEVELS.includes(exposure)) return false;
  if(AI_CLASSIFICATIONS.indexOf(classification) > AI_CLASSIFICATIONS.indexOf(
    dataPolicy.maxClassification)) return false;
  if(AI_EXPOSURE_LEVELS.indexOf(exposure) > AI_EXPOSURE_LEVELS.indexOf(
    dataPolicy.defaultExposureLevel)) return false;
  if(content.remote === true && content.containsSamples === true && !dataPolicy.allowRemoteSamples) return false;
  if(content.remote === true && content.containsSource === true && !dataPolicy.allowRemoteSource) return false;
  if(!Number.isInteger(content.sampleRows) || content.sampleRows < 0 ||
      content.sampleRows > dataPolicy.maxSampleRows) return false;
  return Number.isInteger(content.textCharacters) && content.textCharacters >= 0 &&
    content.textCharacters <= dataPolicy.maxTextCharacters;
}
function budgetAllowed(usage, policy) {
  const mapping = {modelInputTokens: 'maxModelInputTokens', modelOutputTokens: 'maxModelOutputTokens',
    toolCallsPerTurn: 'maxToolCallsPerTurn', toolCallsPerPlan: 'maxToolCallsPerPlan',
    databaseQueriesPerTurn: 'maxDatabaseQueriesPerTurn', databaseRowsPerQuery: 'maxDatabaseRowsPerQuery',
    databaseBytesPerQuery: 'maxDatabaseBytesPerQuery', parallelTools: 'maxParallelTools',
    runMinutes: 'maxRunMinutes', backgroundTasks: 'maxBackgroundTasks',
    estimatedCostPerTurn: 'maxEstimatedCostPerTurn', estimatedCostPerDay: 'maxEstimatedCostPerDay'};
  const unknown = Object.keys(usage).filter((field) => !Object.hasOwn(mapping, field));
  if(unknown.length) throw new TypeError(`AI estimated budget contains unsupported field ${unknown[0]}.`);
  for(const [usageField, policyField] of Object.entries(mapping)) {
    const limit = policy[policyField]; const amount = usage[usageField] ?? 0;
    if(typeof amount !== 'number' || !Number.isFinite(amount) || amount < 0) return false;
    if(limit !== null && amount > limit) return false;
  }
  return true;
}

export class AIAuthorizationService {
  constructor({commands, now=() => new Date().toISOString(), decisionTtlSeconds=30, ...authorities}={}) {
    if(!commands || typeof commands.resolve !== 'function') throw new TypeError(
      'AI authorization requires CommandRegistry.');
    AUTHORITY_NAMES.forEach((name) => { if(typeof authorities[name] !== 'function') throw new TypeError(
      `AI authorization requires ${name} authority.`); });
    if(!Number.isSafeInteger(decisionTtlSeconds) || decisionTtlSeconds < 1 || decisionTtlSeconds > 300)
      throw new TypeError('AI execution-decision TTL must be from 1 to 300 seconds.');
    this.commands = commands; this.authorities = authorities; this.now = now; this.sequence = 0;
    this.decisionTtlSeconds = decisionTtlSeconds; this.executionDecisions = new WeakMap();
  }

  async evaluate(input, context={}) {
    const op = operation(input); const trace = []; const user = plainObject(context.currentUser,
      'AI authorization user'); const permissions = new Set(user.permissions ?? []);
    const agent = validateAIAsset('AIAgentProfile', context.agentProfile);
    const model = validateAIAsset('AIModelProfile', context.modelProfile);
    const connector = validateAIAsset('AIConnectorProfile', context.connectorProfile);
    const toolPolicy = validateAIAsset('AIToolPolicy', context.toolPolicy);
    const dataPolicy = validateAIAsset('AIDataPolicy', context.dataPolicy);
    const budgetPolicy = validateAIAsset('AIBudgetPolicy', context.budgetPolicy);
    const approvalPolicy = validateAIAsset('AIApprovalPolicy', context.approvalPolicy);
    let command = null;
    const decide = (name, allowed, reason='', evidenceRef=null) => {
      const check = immutable({name, allowed, reason, evidenceRef}); trace.push(check); return allowed;
    };
    const external = async (name, authority, detail={}) => {
      const result = authorityResult(await authority(immutable({operation: op, context, ...detail})), name);
      return decide(name, result.allowed, result.reason, result.evidenceRef);
    };
    const deny = () => immutable({schema: 'cdeadmin.ai-authorization-decision.v1',
      decisionId: `ai-decision-${++this.sequence}`, operationId: op.operationId,
      commandId: op.commandId, phase: op.phase, allowed: false, completedChecks: trace,
      deniedAt: trace.at(-1).name, reason: trace.at(-1).reason, decidedAt: this.now()});

    if(!await external('emergency_state', this.authorities.emergencyState)) return deny();
    if(!decide('agent_profile', agent.enabled, agent.enabled ? '' : 'Agent profile is disabled.')) return deny();
    if(!model.enabled || !await external('model_profile', this.authorities.modelHealth, {model})) {
      if(!model.enabled && trace.at(-1)?.name !== 'model_profile') decide(
        'model_profile', false, 'Model profile is disabled.'); return deny();
    }
    const connectorState = connector.enabled && ['ready', 'degraded'].includes(connector.state);
    if(!connectorState || !await external('connector', this.authorities.connectorHealth, {connector})) {
      if(!connectorState && trace.at(-1)?.name !== 'connector') decide(
        'connector', false, 'Connector is disabled or unusable.'); return deny();
    }
    if(!decide('ai_use_permission', permissions.has('ai.use'), 'The user lacks ai.use.')) return deny();
    const risk = op.riskClass; const delegationPermissions = permissionForRisk(risk);
    if(risk === 'R7' || !delegationPermissions.every((item) => permissions.has(item)) ||
        !await external('delegation', this.authorities.delegation, {agent, connector, risk})) {
      if((risk === 'R7' || !delegationPermissions.every((item) => permissions.has(item))) &&
          trace.at(-1)?.name !== 'delegation') decide('delegation', false,
        risk === 'R7' ? 'Raw-secret operations cannot be delegated.' : 'Delegation permission is absent.');
      return deny();
    }
    try { command = this.commands.resolve(op.commandId, {currentUser: user,
      commandCustomizations: context.commandCustomizations}); } catch(error) {
      decide('tool_exposure', false, `Command is not registered: ${error.message}`); return deny(); }
    const validExposure = command.visible && command.enabled && command.aiExposure !== 'hidden' &&
      command.aiRiskClass === risk && risk !== 'R7' &&
      !(command.aiExposure === 'read_only' && riskIndex(risk) > 1) &&
      !(command.aiExposure === 'draft_only' && riskIndex(risk) > 2);
    if(!decide('tool_exposure', validExposure, 'Command is not safely AI-exposed.')) return deny();
    const toolAllowed = !toolPolicy.deniedCommandIds.includes(command.id) &&
      (toolPolicy.commandIds.includes(command.id) || toolPolicy.moduleIds.includes(command.aiModuleId)) &&
      (op.operationKind !== 'project_draft' || toolPolicy.allowProjectDrafts) &&
      (!command.createsTask || toolPolicy.allowBackgroundTasks);
    if(!decide('tool_policy', toolAllowed, 'Agent tool policy denies this command.')) return deny();
    if(!await external('connector_policy', this.authorities.connectorPolicy,
      {connector, command, risk})) return deny();
    const resourcesInScope = op.resourceRefs.every((item) => connector.resourceScopeRefs.includes(item));
    const crossSurfaceSafe = op.accessSurfaceRefs.length < 2 ||
      (connector.connectorClass === 'scratchbird_sbsql' && dataPolicy.queryPolicy.allowCrossSurface);
    if(!decide('resource_scope', resourcesInScope && crossSurfaceSafe,
      resourcesInScope ? 'Cross-surface access is not authorized through native SBsql.' :
        'A target resource is outside connector scope.')) return deny();
    if(!await external('environment_policy', this.authorities.environmentPolicy,
      {connector, command, risk})) return deny();
    if(!await external('database_principal', this.authorities.databasePrecheck,
      {connector, command, risk})) return deny();
    if(!contentAllowed(op.modelVisibleContent, dataPolicy) ||
        !await external('data_egress', this.authorities.dataEgress, {model, dataPolicy})) {
      if(!contentAllowed(op.modelVisibleContent, dataPolicy) && trace.at(-1)?.name !== 'data_egress')
        decide('data_egress', false, 'Model-visible content exceeds the data policy.'); return deny();
    }
    if(!budgetAllowed(op.estimatedBudget, budgetPolicy) ||
        !await external('budget', this.authorities.budget, {budgetPolicy})) {
      if(!budgetAllowed(op.estimatedBudget, budgetPolicy) && trace.at(-1)?.name !== 'budget')
        decide('budget', false, 'Operation exceeds its configured budget.'); return deny();
    }
    if(!await external('plan', this.authorities.planValidation, {plan: op.plan, command, risk})) return deny();
    const approvalRule = approvalPolicy.riskRules[risk];
    const automatic = ['R0', 'R1'].includes(risk) && approvalRule === 'auto' &&
      riskIndex(risk) <= riskIndex(toolPolicy.maxAutomaticRisk);
    if(!automatic && op.approvalEvidence == null) {
      decide('approval', false, `Approval is required by ${risk} policy.`); return deny();
    }
    if(!automatic && !await external('approval', this.authorities.approvalValidation,
      {approvalPolicy, approvalRule, command, risk})) return deny();
    if(automatic) decide('approval', true, 'Automatic approval is allowed by bounded low-risk policy.');
    if(op.phase === 'execute') {
      if(!await external('backend_authorization', this.authorities.backendAuthorization,
        {connector, command, risk})) return deny();
    } else decide('backend_authorization', true, 'Deferred until final execution.');
    const decision = immutable({schema: 'cdeadmin.ai-authorization-decision.v1',
      decisionId: `ai-decision-${++this.sequence}`, operationId: op.operationId,
      commandId: op.commandId, phase: op.phase, riskClass: risk, allowed: true,
      completedChecks: trace, deniedAt: null, reason: '', decidedAt: this.now()});
    if(op.phase === 'execute') this.executionDecisions.set(decision, {
      arguments: stableJson(op.normalizedArguments), expiresAt: Date.parse(this.now()) +
        this.decisionTtlSeconds * 1000});
    return decision;
  }

  assertExecutionDecision(decision, commandId, normalizedArguments) {
    if(!decision || !this.executionDecisions.has(decision) || decision.allowed !== true ||
        decision.phase !== 'execute' || decision.commandId !== commandId ||
        decision.completedChecks.length !== AI_AUTHORIZATION_CHECKS.length ||
        this.executionDecisions.get(decision).arguments !== stableJson(normalizedArguments) ||
        this.executionDecisions.get(decision).expiresAt < Date.parse(this.now())) {
      throw new Error('A current final AI execution authorization is required.');
    }
    this.executionDecisions.delete(decision); return true;
  }
}
