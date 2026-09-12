/////////////////////////////////////////////////////////////
// CDEadmin AI plan, approval and execution lifecycle contracts.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';

export const AI_PLAN_STATES = Object.freeze(['draft', 'validating', 'invalid',
  'ready_for_approval', 'approved', 'running', 'succeeded', 'failed', 'cancelled', 'stale']);
export const AI_STEP_STATES = Object.freeze(['pending', 'validated', 'approval_required',
  'running', 'succeeded', 'failed', 'skipped', 'cancelled']);
export const AI_FAILURE_CATEGORIES = Object.freeze([
  'MODEL_PROVIDER_FAILURE', 'CONNECTOR_TRANSPORT_FAILURE', 'AUTHENTICATION_FAILURE',
  'AUTHORIZATION_DENIED', 'POLICY_DENIED', 'RESOURCE_SCOPE_DENIED', 'BUDGET_EXCEEDED',
  'APPROVAL_REQUIRED', 'APPROVAL_STALE', 'COMPILE_ERROR', 'PROVIDER_ERROR', 'TASK_FAILED',
  'PARTIAL_RESULT', 'VERSION_INCOMPATIBLE', 'MCP_TOOL_ERROR',
  'INTERNAL_ORCHESTRATOR_ERROR',
]);
export const AI_RISK_CLASS_IDS = Object.freeze(['R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7']);
export const AI_OPERATION_KINDS = Object.freeze(['metadata_read', 'data_read', 'query_compile',
  'query_explain', 'query_read', 'query_mutation', 'project_draft', 'command', 'task']);

export class AIExecutionFailure extends Error {
  constructor(category, message, {cause=null, diagnostics=[], retryable=false}={}) {
    if(!AI_FAILURE_CATEGORIES.includes(category)) throw new TypeError(
      'AI execution failure category is invalid.');
    super(String(message)); this.name = 'AIExecutionFailure'; this.category = category;
    this.cause = cause; this.diagnostics = immutable([...diagnostics]);
    this.retryable = retryable === true;
  }
}

export function normalizeExecutionFailure(error, fallback='INTERNAL_ORCHESTRATOR_ERROR') {
  if(error instanceof AIExecutionFailure) return error;
  const category = AI_FAILURE_CATEGORIES.includes(error?.category) ? error.category : fallback;
  return new AIExecutionFailure(category, error?.message ?? String(error), {cause: error,
    diagnostics: Array.isArray(error?.diagnostics) ? error.diagnostics : [],
    retryable: error?.retryable === true});
}

export function stableAIJson(value) {
  if(Array.isArray(value)) return `[${value.map(stableAIJson).join(',')}]`;
  if(value && typeof value === 'object') return `{${Object.keys(value).sort().map((key) =>
    `${JSON.stringify(key)}:${stableAIJson(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}

// Synchronous SHA-256 keeps approval creation deterministic in browser and test runtimes.
export function sha256(value) {
  const source = unescape(encodeURIComponent(String(value)));
  const words = []; const bitLength = source.length * 8;
  for(let index = 0; index < source.length; index++) words[index >> 2] |=
    source.charCodeAt(index) << (24 - (index % 4) * 8);
  words[bitLength >> 5] |= 0x80 << (24 - bitLength % 32);
  words[((bitLength + 64 >> 9) << 4) + 15] = bitLength;
  const constants = []; const initial = []; let candidate = 2;
  const composite = {};
  while(constants.length < 64) {
    if(!composite[candidate]) {
      for(let multiple = candidate * candidate; multiple < 313; multiple += candidate)
        composite[multiple] = true;
      if(initial.length < 8) initial.push((Math.sqrt(candidate) * 0x100000000) | 0);
      constants.push((Math.pow(candidate, 1 / 3) * 0x100000000) | 0);
    }
    candidate++;
  }
  let hash = [...initial];
  for(let offset = 0; offset < words.length; offset += 16) {
    const schedule = words.slice(offset, offset + 16); const old = [...hash];
    for(let index = 0; index < 64; index++) {
      const word15 = schedule[index - 15]; const word2 = schedule[index - 2];
      const a = hash[0]; const e = hash[4];
      const sigma0 = index < 16 ? 0 : ((word15 >>> 7 | word15 << 25) ^
        (word15 >>> 18 | word15 << 14) ^ (word15 >>> 3));
      const sigma1 = index < 16 ? 0 : ((word2 >>> 17 | word2 << 15) ^
        (word2 >>> 19 | word2 << 13) ^ (word2 >>> 10));
      const word = index < 16 ? schedule[index] | 0 :
        (schedule[index - 16] + sigma0 + schedule[index - 7] + sigma1) | 0;
      schedule[index] = word;
      const choice = e & hash[5] ^ ~e & hash[6];
      const majority = a & hash[1] ^ a & hash[2] ^ hash[1] & hash[2];
      const sum0 = (a >>> 2 | a << 30) ^ (a >>> 13 | a << 19) ^ (a >>> 22 | a << 10);
      const sum1 = (e >>> 6 | e << 26) ^ (e >>> 11 | e << 21) ^ (e >>> 25 | e << 7);
      const temporary1 = (hash[7] + sum1 + choice + constants[index] + word) | 0;
      const temporary2 = (sum0 + majority) | 0;
      hash = [(temporary1 + temporary2) | 0, hash[0], hash[1], hash[2],
        (hash[3] + temporary1) | 0, hash[4], hash[5], hash[6]];
    }
    hash = hash.map((word, index) => (word + old[index]) | 0);
  }
  return hash.map((word) => (word >>> 0).toString(16).padStart(8, '0')).join('');
}

export function aiPlanHash(plan) {
  plainObject(plan, 'AI plan'); noRawSecrets(plan, 'AI plan');
  // Runtime state changes do not revise the reviewed plan content.
  const {status: _status, ...content} = plan;
  const reviewable = {...content, steps: plan.steps?.map((step) => {
    const {status: _stepStatus, ...stepContent} = step; return stepContent;
  })};
  return `sha256:${sha256(stableAIJson(reviewable))}`;
}

export function aiActor(context, permission=null) {
  const user = plainObject(context?.currentUser, 'AI lifecycle current user');
  const id = platformValue(user.id ?? user.username ?? user.email, 'AI lifecycle user identity');
  const permissions = new Set(user.permissions ?? []);
  if(permission && !permissions.has(permission)) throw new AIExecutionFailure(
    'AUTHORIZATION_DENIED', `The current user lacks ${permission}.`);
  if(user.isAIPrincipal === true || id === context?.modelActorId) throw new AIExecutionFailure(
    'AUTHORIZATION_DENIED', 'An AI/model principal cannot perform this human authority action.');
  return immutable({id, permissions: [...permissions]});
}

export function boundedStrings(value, label, {maximum=10000}={}) {
  if(!Array.isArray(value) || value.length > maximum) throw new TypeError(
    `${label} must be a bounded array.`);
  const result = value.map((item) => platformValue(item, `${label} value`, 2048));
  if(new Set(result).size !== result.length) throw new TypeError(`${label} contains duplicates.`);
  return result;
}

export function exactLifecycleObject(value, fields, label) {
  plainObject(value, label); noRawSecrets(value, label);
  const unknown = Object.keys(value).filter((field) => !fields.includes(field));
  if(unknown.length) throw new TypeError(`${label} contains unsupported field ${unknown[0]}.`);
  return value;
}

export function validateAIPreparedOperation(input) {
  exactLifecycleObject(input, ['preparedId', 'connectorSnapshot', 'operationKind',
    'canonicalResourceRefs', 'accessSurfaceRefs', 'normalizedArguments',
    'nativeCompiledArtifact', 'riskClass', 'estimatedEffects', 'budgets', 'validation',
    'expiresAt'], 'AI prepared operation');
  const operationKind = platformValue(input.operationKind, 'AI prepared operation kind');
  if(!AI_OPERATION_KINDS.includes(operationKind)) throw new TypeError(
    'AI prepared operation kind is invalid.');
  const riskClass = platformValue(input.riskClass, 'AI prepared operation risk');
  if(!AI_RISK_CLASS_IDS.includes(riskClass)) throw new TypeError(
    'AI prepared operation risk is invalid.');
  const connectorSnapshot = immutable({...plainObject(input.connectorSnapshot,
    'AI prepared connector snapshot')});
  const normalizedArguments = immutable({...plainObject(input.normalizedArguments,
    'AI prepared normalized arguments')});
  const budgets = immutable({...plainObject(input.budgets, 'AI prepared budgets')});
  if(!Array.isArray(input.estimatedEffects) || !Array.isArray(input.validation)) throw new TypeError(
    'AI prepared effects and validation must be arrays.');
  const expiresAt = platformValue(input.expiresAt, 'AI prepared operation expiry');
  if(Number.isNaN(Date.parse(expiresAt))) throw new TypeError(
    'AI prepared operation expiry must be a timestamp.');
  return immutable({preparedId: platformValue(input.preparedId, 'AI prepared operation ID'),
    connectorSnapshot, operationKind, canonicalResourceRefs: boundedStrings(
      input.canonicalResourceRefs, 'AI prepared canonical resources'), accessSurfaceRefs:
      boundedStrings(input.accessSurfaceRefs, 'AI prepared access surfaces'),
    normalizedArguments, nativeCompiledArtifact: input.nativeCompiledArtifact == null ? null :
      immutable({...plainObject(input.nativeCompiledArtifact,
        'AI prepared native compiled artifact')}), riskClass,
    estimatedEffects: immutable(input.estimatedEffects.map((item) => immutable(
      {...plainObject(item, 'AI prepared effect')}))), budgets,
    validation: immutable(input.validation.map((item) => immutable(
      {...plainObject(item, 'AI prepared validation')}))), expiresAt});
}
