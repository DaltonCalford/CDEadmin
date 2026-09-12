/////////////////////////////////////////////////////////////
// CDEadmin AI Interface governed asset contracts.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {createAIConnectorProfile} from './AIConnectorRegistry';

export const AI_ASSET_TYPES = Object.freeze({
  AIModelProfile: 'cdeadmin.ai_interface.model_profile.v1',
  AIConnectorProfile: 'cdeadmin.ai_interface.connector_profile.v1',
  AIAgentProfile: 'cdeadmin.ai_interface.agent_profile.v1',
  AIToolPolicy: 'cdeadmin.ai_interface.tool_policy.v1',
  AIDataPolicy: 'cdeadmin.ai_interface.data_policy.v1',
  AIApprovalPolicy: 'cdeadmin.ai_interface.approval_policy.v1',
  AIBudgetPolicy: 'cdeadmin.ai_interface.budget_policy.v1',
  AIRetentionPolicy: 'cdeadmin.ai_interface.retention_policy.v1',
  AIInstructionAsset: 'cdeadmin.ai_interface.instruction_asset.v1',
  AIPlan: 'cdeadmin.ai_interface.plan.v1',
});
export const AI_ASSET_KINDS = Object.freeze(Object.keys(AI_ASSET_TYPES));
export const AI_CLASSIFICATIONS = Object.freeze(['PUBLIC', 'INTERNAL', 'CONFIDENTIAL',
  'RESTRICTED', 'REGULATED', 'SECRET_REFERENCE_ONLY']);
export const AI_EXPOSURE_LEVELS = Object.freeze(['IDENTITY_ONLY', 'METADATA_SUMMARY',
  'SCHEMA', 'STATISTICS', 'SAFE_SAMPLE', 'FULL_ALLOWED_CONTENT']);
const PLAN_STATES = ['draft', 'validating', 'invalid', 'ready_for_approval', 'approved',
  'running', 'succeeded', 'failed', 'cancelled', 'stale'];
const STEP_KINDS = ['READ_METADATA', 'READ_DATA', 'COMPILE_QUERY', 'EXECUTE_QUERY',
  'CREATE_ASSET_DRAFT', 'EDIT_ASSET', 'INVOKE_COMMAND', 'START_TASK', 'WAIT_TASK',
  'VALIDATE', 'REQUEST_APPROVAL'];
const STEP_STATES = ['pending', 'validated', 'approval_required', 'running', 'succeeded',
  'failed', 'skipped', 'cancelled'];

function exact(input, fields, label) { const unknown = Object.keys(input).filter((field) =>
  !fields.includes(field)); if(unknown.length) throw new TypeError(
  `${label} contains unsupported field ${unknown[0]}.`); }
function text(value, label, maximum=8192, optional=false) { if(optional &&
    (value === null || value === undefined || value === '')) return null;
return platformValue(value, label, maximum); }
function bool(value, label) { if(typeof value !== 'boolean') throw new TypeError(`${label} must be boolean.`);
  return value; }
function integer(value, label, {minimum=1, maximum=Number.MAX_SAFE_INTEGER, optional=false}={}) {
  if(optional && value == null) return null; if(!Number.isInteger(value) || value < minimum || value > maximum)
    throw new TypeError(`${label} must be an integer from ${minimum} to ${maximum}.`); return value; }
function number(value, label, {minimum=0, optional=false}={}) { if(optional && value == null) return null;
  if(typeof value !== 'number' || !Number.isFinite(value) || value < minimum) throw new TypeError(
    `${label} must be a finite number of at least ${minimum}.`); return value; }
function choice(value, choices, label) { if(!choices.includes(value)) throw new TypeError(`${label} is invalid.`);
  return value; }
function strings(value, label, {maximum=10000}={}) { if(!Array.isArray(value) || value.length > maximum)
  throw new TypeError(`${label} must be a bounded array.`); const result = value.map((item) => text(item,
  `${label} value`, 2048)); if(new Set(result).size !== result.length) throw new TypeError(
  `${label} contains duplicates.`); return result; }
function object(value, label) { plainObject(value, label); noRawSecrets(value, label); return immutable({...value}); }
function base(input, fields, label) { plainObject(input, label); noRawSecrets(input, label);
  exact(input, fields, label); if(input.schemaVersion !== 1) throw new TypeError(
    `${label} schema version is invalid.`); }

export function validateAIModelProfile(input) { const fields = ['schemaVersion', 'profileId', 'displayName',
  'runtimeClass', 'endpointRef', 'modelId', 'credentialRef', 'toolCallingSupport',
  'structuredOutputSupport', 'contextLimit', 'outputLimit', 'streamingSupport', 'dataResidency',
  'retentionStatement', 'approvedClassificationMax', 'costModel', 'enabled'];
base(input, fields, 'AI model profile'); return immutable({schemaVersion: 1,
  profileId: text(input.profileId, 'AI model profile ID', 256),
  displayName: text(input.displayName, 'AI model profile name', 120),
  runtimeClass: choice(input.runtimeClass, ['local', 'self_hosted', 'remote'], 'AI model runtime class'),
  endpointRef: text(input.endpointRef, 'AI model endpoint reference', 2048, true),
  modelId: text(input.modelId, 'AI model ID', 512),
  credentialRef: text(input.credentialRef, 'AI model credential reference', 2048, true),
  toolCallingSupport: bool(input.toolCallingSupport, 'AI model tool-calling support'),
  structuredOutputSupport: bool(input.structuredOutputSupport, 'AI model structured-output support'),
  contextLimit: integer(input.contextLimit, 'AI model context limit', {optional: true}),
  outputLimit: integer(input.outputLimit, 'AI model output limit', {optional: true}),
  streamingSupport: bool(input.streamingSupport, 'AI model streaming support'),
  dataResidency: text(input.dataResidency, 'AI model data residency', 4000),
  retentionStatement: text(input.retentionStatement, 'AI model retention statement', 16000),
  approvedClassificationMax: choice(input.approvedClassificationMax, AI_CLASSIFICATIONS,
    'AI model approved classification'), costModel: input.costModel == null ? null : object(input.costModel,
    'AI model cost model'), enabled: bool(input.enabled, 'AI model enabled state')}); }

export function validateAIAgentProfile(input) { const fields = ['schemaVersion', 'profileId', 'name',
  'description', 'enabled', 'autonomyMode', 'modelProfileRef', 'connectorRefs', 'toolPolicyRef',
  'dataPolicyRef', 'approvalPolicyRef', 'budgetPolicyRef', 'retentionPolicyRef',
  'instructionAssetRef', 'tags']; base(input, fields, 'AI agent profile'); return immutable({schemaVersion: 1,
  profileId: text(input.profileId, 'AI agent profile ID', 256), name: text(input.name,
    'AI agent profile name', 120), description: text(input.description, 'AI agent description', 4000, true),
  enabled: bool(input.enabled, 'AI agent enabled state'), autonomyMode: choice(input.autonomyMode,
    ['ASK_ONLY', 'DRAFT_ONLY', 'READ_ONLY_TOOLS', 'BOUNDED_EXECUTION'], 'AI autonomy mode'),
  modelProfileRef: text(input.modelProfileRef, 'AI agent model profile reference', 256),
  connectorRefs: strings(input.connectorRefs, 'AI agent connector references'),
  toolPolicyRef: text(input.toolPolicyRef, 'AI tool policy reference', 256),
  dataPolicyRef: text(input.dataPolicyRef, 'AI data policy reference', 256),
  approvalPolicyRef: text(input.approvalPolicyRef, 'AI approval policy reference', 256),
  budgetPolicyRef: text(input.budgetPolicyRef, 'AI budget policy reference', 256),
  retentionPolicyRef: text(input.retentionPolicyRef, 'AI retention policy reference', 256),
  instructionAssetRef: text(input.instructionAssetRef, 'AI instruction asset reference', 256, true),
  tags: strings(input.tags ?? [], 'AI agent tags')}); }

export function validateAIToolPolicy(input) { const fields = ['schemaVersion', 'policyId', 'name',
  'moduleIds', 'commandIds', 'deniedCommandIds', 'maxAutomaticRisk', 'allowProjectDrafts',
  'allowBackgroundTasks']; base(input, fields, 'AI tool policy'); const commands = strings(input.commandIds,
  'AI tool policy command IDs'); const denied = strings(input.deniedCommandIds ?? [],
  'AI tool policy denied commands'); return immutable({schemaVersion: 1,
  policyId: text(input.policyId, 'AI tool policy ID', 256), name: text(input.name,
    'AI tool policy name', 120), moduleIds: strings(input.moduleIds, 'AI tool policy module IDs'),
  commandIds: commands,
  deniedCommandIds: denied, maxAutomaticRisk: choice(input.maxAutomaticRisk, ['R0', 'R1'],
    'AI tool automatic risk'), allowProjectDrafts: bool(input.allowProjectDrafts,
    'AI tool project-draft policy'), allowBackgroundTasks: bool(input.allowBackgroundTasks,
    'AI tool background-task policy')}); }

function queryPolicy(input) { const fields = ['allowRead', 'allowWrite', 'allowDDL',
  'allowTransactionControl', 'allowExplain', 'allowSystemCatalog', 'allowCrossSurface', 'maxRows',
  'maxResultBytes', 'maxStatementSeconds', 'maxStatementsPerPlan', 'maxParallelQueries',
  'allowedResourceRefs', 'blockedResourceRefs', 'allowTemporaryObjects', 'allowStoredProcedureCall',
  'allowExternalSideEffectFunctions']; plainObject(input, 'AI query policy'); noRawSecrets(input,
  'AI query policy'); exact(input, fields, 'AI query policy'); const result = {allowRead: bool(input.allowRead,
  'AI query read policy'), allowWrite: bool(input.allowWrite, 'AI query write policy'),
allowDDL: bool(input.allowDDL, 'AI query DDL policy'), allowTransactionControl: bool(
  input.allowTransactionControl, 'AI query transaction-control policy'), allowExplain: bool(
  input.allowExplain, 'AI query explain policy'), allowSystemCatalog: bool(input.allowSystemCatalog,
  'AI query system-catalog policy'), allowCrossSurface: bool(input.allowCrossSurface,
  'AI query cross-surface policy'), maxRows: integer(input.maxRows, 'AI query row limit'),
maxResultBytes: integer(input.maxResultBytes, 'AI query byte limit'), maxStatementSeconds: integer(
  input.maxStatementSeconds, 'AI query duration limit'), maxStatementsPerPlan: integer(
  input.maxStatementsPerPlan, 'AI query statement limit'), maxParallelQueries: integer(
  input.maxParallelQueries, 'AI query parallelism limit'), allowedResourceRefs: strings(
  input.allowedResourceRefs, 'AI query allowed resources'), blockedResourceRefs: strings(
  input.blockedResourceRefs, 'AI query blocked resources'), allowTemporaryObjects: bool(
  input.allowTemporaryObjects, 'AI query temporary-object policy'), allowStoredProcedureCall: bool(
  input.allowStoredProcedureCall, 'AI query procedure policy'), allowExternalSideEffectFunctions: bool(
  input.allowExternalSideEffectFunctions, 'AI query external-side-effect policy')};
if(result.allowCrossSurface && !result.allowRead) throw new TypeError(
  'AI cross-surface querying requires read access.'); return immutable(result); }

export function validateAIDataPolicy(input) { const fields = ['schemaVersion', 'policyId', 'name',
  'defaultExposureLevel', 'maxClassification', 'allowRemoteSamples', 'allowRemoteSource',
  'sensitiveColumnHandling', 'maxSampleRows', 'maxTextCharacters', 'allowedModelProfileRefs',
  'requireLocalForRestricted', 'queryPolicy']; base(input, fields, 'AI data policy'); return immutable({
  schemaVersion: 1, policyId: text(input.policyId, 'AI data policy ID', 256),
  name: text(input.name, 'AI data policy name', 120), defaultExposureLevel: choice(
    input.defaultExposureLevel, AI_EXPOSURE_LEVELS, 'AI default exposure level'),
  maxClassification: choice(input.maxClassification, AI_CLASSIFICATIONS, 'AI data classification'),
  allowRemoteSamples: bool(input.allowRemoteSamples, 'AI remote-sample policy'),
  allowRemoteSource: bool(input.allowRemoteSource, 'AI remote-source policy'),
  sensitiveColumnHandling: choice(input.sensitiveColumnHandling,
    ['excluded', 'masked', 'tokenized', 'aggregated_only', 'allowed'], 'AI sensitive-column policy'),
  maxSampleRows: integer(input.maxSampleRows, 'AI sample row limit', {minimum: 0}),
  maxTextCharacters: integer(input.maxTextCharacters, 'AI text limit'), allowedModelProfileRefs: strings(
    input.allowedModelProfileRefs ?? [], 'AI allowed model profiles'), requireLocalForRestricted: bool(
    input.requireLocalForRestricted, 'AI restricted-data locality policy'), queryPolicy: queryPolicy(
    input.queryPolicy)}); }

export function validateAIApprovalPolicy(input) { const fields = ['schemaVersion', 'policyId', 'name',
  'riskRules', 'expiryMinutes']; base(input, fields, 'AI approval policy'); const rules = object(input.riskRules,
  'AI approval risk rules'); exact(rules, ['R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6'],
  'AI approval risk rules'); const choices = {R0: ['auto', 'review', 'forbid'],
  R1: ['auto', 'review', 'forbid'], R2: ['auto', 'review_diff', 'review', 'forbid'],
  R3: ['review', 'forbid'], R4: ['confirm_each', 'dual_approval', 'forbid'],
  R5: ['typed_confirm', 'dual_approval', 'forbid'],
  R6: ['typed_confirm', 'dual_approval', 'dual_or_typed', 'forbid']};
const riskRules = Object.fromEntries(Object.entries(choices).map(([risk, allowed]) =>
  [risk, choice(rules[risk], allowed, `AI approval ${risk} rule`)])); const expiry = object(
  input.expiryMinutes, 'AI approval expiry'); exact(expiry, ['R4', 'R5', 'R6'], 'AI approval expiry');
return immutable({schemaVersion: 1, policyId: text(input.policyId, 'AI approval policy ID', 256),
  name: text(input.name, 'AI approval policy name', 120), riskRules,
  expiryMinutes: {R4: integer(expiry.R4, 'AI R4 approval expiry'),
    R5: integer(expiry.R5, 'AI R5 approval expiry'),
    R6: integer(expiry.R6, 'AI R6 approval expiry')}}); }

export function validateAIBudgetPolicy(input) { const fields = ['schemaVersion', 'policyId', 'name',
  'maxModelInputTokens', 'maxModelOutputTokens', 'maxToolCallsPerTurn', 'maxToolCallsPerPlan',
  'maxDatabaseQueriesPerTurn', 'maxDatabaseRowsPerQuery', 'maxDatabaseBytesPerQuery',
  'maxParallelTools', 'maxRunMinutes', 'maxBackgroundTasks', 'maxEstimatedCostPerTurn',
  'maxEstimatedCostPerDay']; base(input, fields, 'AI budget policy'); const missing = fields.slice(3)
  .find((field) => !Object.hasOwn(input, field)); if(missing) throw new TypeError(
  `AI budget policy requires explicit ${missing}.`); const result = {schemaVersion: 1,
  policyId: text(input.policyId, 'AI budget policy ID', 256), name: text(input.name,
    'AI budget policy name', 120)}; fields.slice(3, 13).forEach((field) => { result[field] = integer(
  input[field], `AI budget ${field}`, {optional: true}); }); result.maxEstimatedCostPerTurn = number(
  input.maxEstimatedCostPerTurn, 'AI estimated per-turn cost', {optional: true});
result.maxEstimatedCostPerDay = number(input.maxEstimatedCostPerDay,
  'AI estimated daily cost', {optional: true}); return immutable(result); }

export function validateAIRetentionPolicy(input) { const fields = ['schemaVersion', 'policyId', 'name',
  'retainMessages', 'conversationDays', 'retainToolResults', 'auditDays', 'retainPromptsInAudit'];
base(input, fields, 'AI retention policy'); const retainMessages = bool(input.retainMessages,
  'AI message retention'); return immutable({schemaVersion: 1,
  policyId: text(input.policyId, 'AI retention policy ID', 256), name: text(input.name,
    'AI retention policy name', 120), retainMessages, conversationDays: integer(input.conversationDays,
    'AI conversation retention days', {optional: !retainMessages}), retainToolResults: bool(
    input.retainToolResults, 'AI tool-result retention'), auditDays: integer(input.auditDays,
    'AI audit retention days'), retainPromptsInAudit: bool(input.retainPromptsInAudit,
    'AI prompt audit retention')}); }

export function validateAIInstructionAsset(input) { const fields = ['schemaVersion', 'instructionId',
  'name', 'scope', 'instructions', 'locked', 'versionNote']; base(input, fields,
  'AI instruction asset'); return immutable({schemaVersion: 1,
  instructionId: text(input.instructionId, 'AI instruction ID', 256), name: text(input.name,
    'AI instruction name', 120), scope: choice(input.scope, ['user', 'project', 'team', 'organization'],
    'AI instruction scope'), instructions: text(input.instructions, 'AI instructions', 262144),
  locked: bool(input.locked, 'AI instruction locked state'), versionNote: text(input.versionNote,
    'AI instruction version note', 500, true)}); }

function planStep(input) { const fields = ['stepId', 'kind', 'status', 'commandId', 'connectorRef',
  'resourceRefs', 'assetRefs', 'arguments']; plainObject(input, 'AI plan step'); noRawSecrets(input,
  'AI plan step'); exact(input, fields, 'AI plan step'); return immutable({
  stepId: text(input.stepId, 'AI plan step ID', 256), kind: choice(input.kind, STEP_KINDS,
    'AI plan step kind'), status: choice(input.status, STEP_STATES, 'AI plan step state'),
  commandId: text(input.commandId, 'AI plan command ID', 256, true), connectorRef: text(
    input.connectorRef, 'AI plan connector reference', 256, true), resourceRefs: strings(
    input.resourceRefs ?? [], 'AI plan resource references'), assetRefs: strings(input.assetRefs ?? [],
    'AI plan asset references'), arguments: object(input.arguments ?? {}, 'AI plan arguments')}); }
export function validateAIPlan(input) { const fields = ['schemaVersion', 'planId', 'revision',
  'baseContextRevision', 'agentProfileRef', 'modelProfileSnapshot', 'connectorSnapshots', 'steps',
  'risks', 'requiredApprovals', 'validationChecks', 'status']; base(input, fields, 'AI plan');
if(!Array.isArray(input.steps)) throw new TypeError('AI plan steps must be an array.');
const steps = input.steps.map(planStep); if(new Set(steps.map((item) => item.stepId)).size !== steps.length)
  throw new TypeError('AI plan step IDs must be unique.'); if(!Array.isArray(input.connectorSnapshots))
  throw new TypeError('AI connector snapshots must be an array.'); return immutable({schemaVersion: 1,
  planId: text(input.planId, 'AI plan ID', 256), revision: integer(input.revision, 'AI plan revision'),
  baseContextRevision: text(input.baseContextRevision, 'AI plan base-context revision', 2048),
  agentProfileRef: text(input.agentProfileRef, 'AI plan agent profile reference', 256),
  modelProfileSnapshot: object(input.modelProfileSnapshot, 'AI model profile snapshot'),
  connectorSnapshots: input.connectorSnapshots.map((item) => object(item, 'AI connector snapshot')),
  steps, risks: strings(input.risks, 'AI plan risks'), requiredApprovals: strings(
    input.requiredApprovals, 'AI plan required approvals'), validationChecks: strings(
    input.validationChecks, 'AI plan validation checks'), status: choice(input.status, PLAN_STATES,
    'AI plan state')}); }

const VALIDATORS = Object.freeze({AIModelProfile: validateAIModelProfile,
  AIConnectorProfile: createAIConnectorProfile, AIAgentProfile: validateAIAgentProfile,
  AIToolPolicy: validateAIToolPolicy, AIDataPolicy: validateAIDataPolicy,
  AIApprovalPolicy: validateAIApprovalPolicy, AIBudgetPolicy: validateAIBudgetPolicy,
  AIRetentionPolicy: validateAIRetentionPolicy, AIInstructionAsset: validateAIInstructionAsset,
  AIPlan: validateAIPlan});
export function validateAIAsset(kind, input) { if(!AI_ASSET_KINDS.includes(kind)) throw new TypeError(
  `Unknown AI asset kind: ${kind}`); return VALIDATORS[kind](input); }
export function aiAssetIdentity(kind, content) { const fields = {AIModelProfile: 'profileId',
  AIConnectorProfile: 'connectorId', AIAgentProfile: 'profileId', AIToolPolicy: 'policyId',
  AIDataPolicy: 'policyId', AIApprovalPolicy: 'policyId', AIBudgetPolicy: 'policyId',
  AIRetentionPolicy: 'policyId', AIInstructionAsset: 'instructionId', AIPlan: 'planId'};
return validateAIAsset(kind, content)[fields[kind]]; }
