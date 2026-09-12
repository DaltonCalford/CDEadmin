/////////////////////////////////////////////////////////////
// Governed AI Assistant canonical contracts and source.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';

export const AI_MODULE_ID = 'cdeadmin.ai';
export const AI_SERVICE_ID = 'cdeadmin.ai.runtime';
export const AI_ASSET_TYPE = 'cdeadmin.ai.v1';
export const AI_ASSET_SCHEMA = 'cdeadmin.ai.asset.v1';
export const AI_MODES = Object.freeze(['ask', 'explain', 'draft', 'plan', 'review',
  'execute_with_confirmation']);
export const AI_OUTPUT_TYPES = Object.freeze(['answer', 'explanation', 'draft_text', 'draft_query',
  'draft_asset_change', 'diagnostic_hypothesis', 'proposed_command', 'proposed_task', 'multi_step_plan']);
export const AI_EVIDENCE_CLASSES = Object.freeze(['verified_live_metadata', 'observed_runtime_evidence',
  'inferred', 'model_suggestion']);
export const AI_STATES = Object.freeze(['empty', 'loading', 'ready', 'stale', 'partial',
  'permission_denied', 'disconnected', 'validation_error', 'runtime_failure', 'read_only',
  'background_task_active']);
const REF_SCHEMAS = new Set(['cdeadmin.resource-ref.v1', 'cdeadmin.asset-ref.v1',
  'cdeadmin.external-ref.v1', 'cdeadmin.credential-ref.v1', 'cdeadmin.result-ref.v1',
  'cdeadmin.diagnostic-ref.v1']);

function exact(input, fields, label) { const unknown = Object.keys(input).filter((field) =>
  !fields.includes(field)); if(unknown.length) throw new TypeError(
  `${label} contains unsupported field ${unknown[0]}.`); }
function text(value, label, maximum=8192, optional=false) {
  if(optional && (value === undefined || value === null || value === '')) return null;
  return platformValue(value, label, maximum);
}
function object(value, label) { const result = plainObject(value ?? {}, label);
  noRawSecrets(result, label); return immutable({...result}); }
function list(value, label, mapper, maximum=10000) { if(!Array.isArray(value) || value.length > maximum)
  throw new TypeError(`${label} must contain no more than ${maximum} items.`); return value.map(mapper); }
function strings(value, label) { return [...new Set(list(value ?? [], label,
  (item) => text(item, `${label} value`)))].sort(); }
function unique(value, label, mapper) { const ids = new Set(); const result = list(value ?? [], label, mapper);
  result.forEach((item) => { if(ids.has(item.id)) throw new TypeError(`Duplicate ${label} ID: ${item.id}`);
    ids.add(item.id); }); return result.sort((a, b) => a.id.localeCompare(b.id)); }
function positive(value, label, {optional=false, maximum=1000000}={}) {
  if(optional && value == null) return null; if(!Number.isInteger(value) || value < 1 || value > maximum)
    throw new TypeError(`${label} must be an integer from 1 to ${maximum}.`); return value; }
function extension(input) { plainObject(input, 'AI extension'); exact(input, ['id', 'name', 'value'],
  'AI extension'); const name = text(input.name, 'AI extension name'); if(!name.startsWith('x-'))
  throw new TypeError('AI extension names must begin with x-.'); noRawSecrets(input.value,
  `AI extension ${name}`); return immutable({id: text(input.id ?? name, 'AI extension ID'), name,
  value: input.value ?? null}); }
function extensions(value) { return unique(value ?? [], 'AI extension', extension); }

export function validateAIRef(input, label='AI reference', schemas=REF_SCHEMAS) {
  plainObject(input, label); noRawSecrets(input, label); const schema = text(input.schema, `${label} schema`);
  if(!schemas.has(schema)) throw new TypeError(`${label} schema is unsupported.`);
  if(schema === 'cdeadmin.resource-ref.v1') {
    text(input.canonical, `${label} canonical identity`);
    text(input.providerId ?? input.provider, `${label} provider`);
  } else if(schema === 'cdeadmin.asset-ref.v1') {
    text(input.projectId, `${label} project ID`); text(input.assetId, `${label} asset ID`);
  } else text(input.id, `${label} identity`);
  if(schema === 'cdeadmin.credential-ref.v1') exact(input,
    ['schema', 'id', 'providerId', 'scope', 'displayName'], label); return immutable({...input});
}
export function aiReferenceKey(input) { const ref = validateAIRef(input);
  if(ref.schema === 'cdeadmin.resource-ref.v1') return `resource:${ref.canonical}`;
  if(ref.schema === 'cdeadmin.asset-ref.v1') return `asset:${ref.projectId}/${ref.assetId}`;
  return `${ref.schema}:${ref.id}`; }

export function validateContextScope(input) { plainObject(input, 'AI context scope'); noRawSecrets(input,
  'AI context scope'); exact(input, ['schema', 'id', 'name', 'reference', 'type', 'environment',
  'sensitivity', 'exposure', 'timeRange', 'description', 'nativeDetails'], 'AI context scope');
if(input.schema && input.schema !== 'cdeadmin.ai-context-scope.v1') throw new TypeError(
  'AI context scope schema is invalid.');
if(!['public', 'internal', 'sensitive', 'restricted'].includes(input.sensitivity)) throw new TypeError(
  'AI context sensitivity is invalid.');
if(!['metadata_only', 'content'].includes(input.exposure)) throw new TypeError('AI context exposure is invalid.');
return immutable({schema: 'cdeadmin.ai-context-scope.v1', id: text(input.id, 'AI context scope ID'),
  name: text(input.name ?? input.id, 'AI context scope name'), reference: validateAIRef(input.reference),
  type: text(input.type, 'AI context type'), environment: text(input.environment,
    'AI context environment', 1024, true), sensitivity: input.sensitivity, exposure: input.exposure,
  timeRange: object(input.timeRange, 'AI context time range'), description: String(input.description ?? ''),
  nativeDetails: object(input.nativeDetails, 'AI context native details')}); }

export function validateEvidence(input) { plainObject(input, 'AI evidence'); noRawSecrets(input, 'AI evidence');
  exact(input, ['schema', 'id', 'kind', 'classification', 'sourceRef', 'claimIds', 'summary', 'revision',
    'contentDigest', 'nativeDetails'], 'AI evidence'); if(input.schema && input.schema !==
    'cdeadmin.ai-evidence.v1') throw new TypeError('AI evidence schema is invalid.');
  if(!AI_EVIDENCE_CLASSES.includes(input.classification)) throw new TypeError(
    'AI evidence classification is invalid.');
  return immutable({schema: 'cdeadmin.ai-evidence.v1', id: text(input.id, 'AI evidence ID'),
    kind: text(input.kind, 'AI evidence kind'), classification: input.classification,
    sourceRef: validateAIRef(input.sourceRef), claimIds: strings(input.claimIds, 'AI evidence claim IDs'),
    summary: String(input.summary ?? ''), revision: text(input.revision, 'AI evidence revision', 1024, true),
    contentDigest: text(input.contentDigest, 'AI evidence digest', 1024, true),
    nativeDetails: object(input.nativeDetails, 'AI evidence native details')}); }

export function validateProposedAction(input) { plainObject(input, 'AI proposed action'); noRawSecrets(input,
  'AI proposed action'); exact(input, ['schema', 'id', 'type', 'commandId', 'taskType', 'assetChangeRef',
  'targetRef', 'arguments', 'rationale', 'effects', 'permissions', 'rollbackNote', 'dependencies',
  'validation', 'evidenceRefs', 'diff', 'extensions'], 'AI proposed action');
if(input.schema && input.schema !== 'cdeadmin.ai-proposed-action.v1') throw new TypeError(
  'AI proposed action schema is invalid.');
if(!['proposed_command', 'proposed_task', 'draft_asset_change'].includes(input.type)) throw new TypeError(
  'AI proposed action type is invalid.');
if(input.type === 'proposed_command' && !input.commandId) throw new TypeError(
  'Proposed commands require a command ID.');
if(input.type === 'proposed_task' && !input.taskType) throw new TypeError('Proposed tasks require a task type.');
if(input.type === 'draft_asset_change' && !input.assetChangeRef) throw new TypeError(
  'Draft asset changes require an asset reference.');
if(input.type !== 'proposed_command' && input.commandId) throw new TypeError(
  'Only proposed commands may define a command ID.');
if(input.type !== 'proposed_task' && input.taskType) throw new TypeError(
  'Only proposed tasks may define a task type.');
if(input.type !== 'draft_asset_change' && input.assetChangeRef) throw new TypeError(
  'Only draft asset changes may define an asset reference.');
if(typeof input.validation?.valid !== 'boolean') throw new TypeError(
  'AI action validation must declare a boolean valid state.');
return immutable({schema: 'cdeadmin.ai-proposed-action.v1', id: text(input.id, 'AI action ID'), type: input.type,
  commandId: text(input.commandId, 'AI action command ID', 1024, true),
  taskType: text(input.taskType, 'AI action task type', 1024, true),
  assetChangeRef: input.assetChangeRef ? validateAIRef(input.assetChangeRef, 'AI asset change',
    new Set(['cdeadmin.asset-ref.v1'])) : null, targetRef: validateAIRef(input.targetRef, 'AI action target'),
  arguments: object(input.arguments, 'AI action arguments'), rationale: String(input.rationale ?? ''),
  effects: strings(input.effects, 'AI action effects'), permissions: strings(input.permissions,
    'AI action permissions'), rollbackNote: String(input.rollbackNote ?? ''),
  dependencies: strings(input.dependencies, 'AI action dependencies'),
  validation: object(input.validation, 'AI action validation'),
  evidenceRefs: list(input.evidenceRefs ?? [], 'AI action evidence', (item) => validateAIRef(item,
    'AI action evidence reference')), diff: object(input.diff, 'AI action diff'),
  extensions: extensions(input.extensions)}); }

export function validateActionPlan(input) { plainObject(input, 'AI action plan'); noRawSecrets(input,
  'AI action plan'); exact(input, ['schema', 'id', 'name', 'revision', 'targetRevision', 'contextScopeIds',
  'actions', 'validation', 'rollbackNotes', 'evidenceRefs', 'description', 'extensions'], 'AI action plan');
if(input.schema && input.schema !== 'cdeadmin.ai-action-plan.v1') throw new TypeError(
  'AI action plan schema is invalid.'); const actions = unique(input.actions ?? [], 'AI action',
  validateProposedAction); const actionIds = new Set(actions.map((item) => item.id));
actions.forEach((action) => action.dependencies.forEach((dependency) => { if(!actionIds.has(dependency))
  throw new TypeError(`AI action ${action.id} depends on unknown action ${dependency}.`); }));
return immutable({schema: 'cdeadmin.ai-action-plan.v1', id: text(input.id, 'AI plan ID'),
  name: text(input.name ?? input.id, 'AI plan name'), revision: positive(input.revision, 'AI plan revision'),
  targetRevision: text(input.targetRevision, 'AI plan target revision'),
  contextScopeIds: strings(input.contextScopeIds, 'AI plan context scopes'), actions,
  validation: object(input.validation, 'AI plan validation'), rollbackNotes: String(input.rollbackNotes ?? ''),
  evidenceRefs: list(input.evidenceRefs ?? [], 'AI plan evidence', (item) => validateAIRef(item,
    'AI plan evidence reference')), description: String(input.description ?? ''),
  extensions: extensions(input.extensions)}); }

function sessionPolicy(input) { const value = object(input, 'AI session policy'); exact(value,
  ['id', 'allowedModes', 'defaultMode', 'allowedReadToolIds', 'allowedProposalCommandIds',
    'maximumPlanSteps', 'requireEvidence', 'nativeDetails'], 'AI session policy');
if(!AI_MODES.includes(value.defaultMode)) throw new TypeError('AI default mode is invalid.');
const allowedModes = strings(value.allowedModes, 'AI allowed modes'); if(allowedModes.some((mode) =>
  !AI_MODES.includes(mode)) || !allowedModes.includes(value.defaultMode)) throw new TypeError(
  'AI allowed modes are invalid.'); if(typeof value.requireEvidence !== 'boolean') throw new TypeError(
  'AI evidence policy must be explicit.'); return immutable({id: text(value.id, 'AI session policy ID'),
  allowedModes, defaultMode: value.defaultMode, allowedReadToolIds: strings(value.allowedReadToolIds,
    'AI read tools'), allowedProposalCommandIds: strings(value.allowedProposalCommandIds,
    'AI proposal commands'), maximumPlanSteps: positive(value.maximumPlanSteps,
    'AI maximum plan steps', {maximum: 1000}), requireEvidence: value.requireEvidence,
  nativeDetails: object(value.nativeDetails, 'AI session policy native details')}); }
function persistencePolicy(input) { const value = object(input, 'AI conversation persistence policy');
  exact(value, ['persistMessages', 'storePrompts', 'storeResponses', 'retentionDays', 'nativeDetails'],
    'AI conversation persistence policy'); for(const field of ['persistMessages', 'storePrompts',
    'storeResponses']) if(typeof value[field] !== 'boolean') throw new TypeError(
    `AI persistence ${field} must be boolean.`); return immutable({persistMessages: value.persistMessages,
    storePrompts: value.storePrompts, storeResponses: value.storeResponses,
    retentionDays: positive(value.retentionDays, 'AI retention days', {optional: true, maximum: 36500}),
    nativeDetails: object(value.nativeDetails, 'AI persistence native details')}); }

export function createAIContent(input={}) { plainObject(input, 'AI asset'); noRawSecrets(input, 'AI asset');
  exact(input, ['schema', 'schemaVersion', 'moduleId', 'name', 'description', 'sessionPolicy',
    'savedContextRefs', 'savedPlans', 'modelProfileRef', 'conversationPersistencePolicy', 'extensions'],
  'AI asset'); const contexts = unique(input.savedContextRefs ?? [], 'AI context scope', validateContextScope);
  const plans = unique(input.savedPlans ?? [], 'AI action plan', validateActionPlan);
  const contextIds = new Set(contexts.map((item) => item.id)); plans.forEach((plan) =>
    plan.contextScopeIds.forEach((id) => { if(!contextIds.has(id)) throw new TypeError(
      `AI plan ${plan.id} references unknown context scope ${id}.`); }));
  return immutable({schema: AI_ASSET_SCHEMA, schemaVersion: 1, moduleId: AI_MODULE_ID,
    name: String(input.name ?? ''), description: String(input.description ?? ''),
    sessionPolicy: sessionPolicy(input.sessionPolicy ?? {id: 'default', allowedModes: ['ask', 'explain'],
      defaultMode: 'ask', allowedReadToolIds: [], allowedProposalCommandIds: [], maximumPlanSteps: 25,
      requireEvidence: true, nativeDetails: {}}), savedContextRefs: contexts, savedPlans: plans,
    modelProfileRef: input.modelProfileRef ? validateAIRef(input.modelProfileRef, 'AI model profile',
      new Set(['cdeadmin.asset-ref.v1'])) : null,
    conversationPersistencePolicy: persistencePolicy(input.conversationPersistencePolicy ?? {
      persistMessages: false, storePrompts: false, storeResponses: false, retentionDays: null,
      nativeDetails: {}}), extensions: extensions(input.extensions)}); }
function canonical(value) { if(Array.isArray(value)) return value.map(canonical);
  if(!value || typeof value !== 'object') return value; return Object.fromEntries(Object.keys(value).sort()
    .map((key) => [key, canonical(value[key])])); }
export function serializeAIContent(input) { return JSON.stringify(canonical(createAIContent(input)), null, 2); }
export function aiAssetRequest({name, path, expectedVersion=0, content}) { const value = createAIContent(content);
  return {asset_type: AI_ASSET_TYPE, schema_name: AI_ASSET_TYPE, schema_version: 1,
    name: text(name || value.name || 'AI assistant', 'AI asset name', 256),
    path: text(path || 'ai/assistant.json', 'AI asset path', 1024), expected_version: expectedVersion,
    content: value, metadata: {moduleId: AI_MODULE_ID}, dependency_references: value.modelProfileRef ?
      [value.modelProfileRef] : [], resource_bindings: value.savedContextRefs.filter((item) =>
      item.reference.schema === 'cdeadmin.resource-ref.v1').map((item) => item.reference),
    source_control_eligible: true, editor_capable: true, viewer_capable: true,
    validation_state: 'unknown', validation_details: []}; }
