/////////////////////////////////////////////////////////////
// Discovery access, curation and index-administration contracts.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';

export const DISCOVERY_ACCESS_STATES = Object.freeze(['DRAFT', 'SUBMITTED',
  'AUTO_APPROVED', 'PENDING_APPROVAL', 'APPROVED', 'DENIED', 'PROVISIONING',
  'ACTIVE', 'EXPIRED', 'REVOKED', 'FAILED']);
export const DISCOVERY_REQUESTABLE_ACCESS = Object.freeze(['DISCOVER_METADATA',
  'VIEW_SCHEMA', 'VIEW_PROFILE_STATS', 'VIEW_SAMPLE', 'QUERY', 'EXPORT',
  'ADMINISTER']);
export const DISCOVERY_CURATION_TYPES = Object.freeze(['missing_owner',
  'missing_description', 'unmapped_business_term', 'ai_suggestion',
  'duplicate_candidate', 'stale_certification',
  'quality_failure_on_popular_asset', 'broken_lineage',
  'unresolved_access_surface', 'zero_result_term']);
export const DISCOVERY_DUPLICATE_DECISIONS = Object.freeze(['equivalent_to',
  'replacement_for', 'related_to', 'not_duplicate']);
export const DISCOVERY_ZERO_RESULT_ACTIONS = Object.freeze(['add_synonym',
  'create_business_term', 'map_existing_term', 'mark_expected_no_result',
  'open_data_gap']);
export const DISCOVERY_INDEX_SOURCE_TYPES = Object.freeze(['provider_metadata',
  'provider_system_catalog', 'cdeadmin_project_assets', 'query_history',
  'lineage', 'data_quality', 'contracts', 'dashboards_reports',
  'semantic_models_cubes', 'etl', 'cdc', 'migration', 'api', 'ml_vector', 'ddn',
  'git', 'external_catalog', 'business_glossary', 'manual_curation']);
export const DISCOVERY_INDEX_MODES = Object.freeze(['EVENT_DRIVEN',
  'INCREMENTAL_POLL', 'FULL_RECONCILE', 'MANUAL', 'IMPORT']);
export const DISCOVERY_INDEX_SOURCE_STATES = Object.freeze(['DISABLED', 'IDLE',
  'QUEUED', 'RUNNING', 'DEGRADED', 'FAILED', 'AUTH_REQUIRED', 'PAUSED']);
export const DISCOVERY_INDEX_REVISION_STATES = Object.freeze(['BUILDING',
  'VALIDATING', 'READY', 'ACTIVE', 'FAILED', 'RETIRED']);
export const DISCOVERY_INDEX_BACKEND_TYPES = Object.freeze(['cdeadmin_managed',
  'scratchbird', 'external_search_adapter']);

function strict(input, label, fields) {
  plainObject(input, label); noRawSecrets(input, label);
  const unknown = Object.keys(input).find((field) => !fields.includes(field));
  if(unknown) throw new TypeError(`${label} contains unsupported field ${unknown}.`);
}
function optional(value, label, maximum=4096) {
  return value === null || value === undefined ? null : platformValue(
    value, label, maximum);
}
function timestamp(value, label) {
  value = platformValue(value, label, 64);
  if(Number.isNaN(Date.parse(value))) throw new TypeError(`${label} is invalid.`);
  return value;
}
function strings(value, label, allowed=null) {
  if(!Array.isArray(value)) throw new TypeError(`${label} must be an array.`);
  const result = value.map((item) => platformValue(item, `${label} value`, 4096));
  if(!result.length || new Set(result).size !== result.length ||
      (allowed && result.some((item) => !allowed.includes(item)))) throw new TypeError(
    `${label} is invalid.`
  );
  return result;
}
function bool(value, label) {
  if(typeof value !== 'boolean') throw new TypeError(`${label} must be Boolean.`);
  return value;
}
function integer(value, label, minimum=0) {
  if(!Number.isInteger(value) || value < minimum) throw new TypeError(
    `${label} is invalid.`
  );
  return value;
}

export function validateDiscoveryAccessRequest(input) {
  strict(input, 'Discovery access request', ['schemaVersion', 'requestId',
    'requester', 'targetRef', 'requestedAccess', 'environment', 'reason',
    'duration', 'projectRef', 'policyResult', 'approvers', 'status',
    'providerGrantPlanRef']);
  if(input.schemaVersion !== 1 || !DISCOVERY_ACCESS_STATES.includes(input.status)) {
    throw new TypeError('Discovery access request version or state is invalid.');
  }
  const policyResult = input.policyResult ?? null;
  if(policyResult !== null) {
    plainObject(policyResult, 'Discovery access policy result');
    noRawSecrets(policyResult, 'Discovery access policy result');
  }
  return immutable({schemaVersion: 1,
    requestId: platformValue(input.requestId, 'Discovery access request ID', 512),
    requester: platformValue(input.requester, 'Discovery access requester', 512),
    targetRef: platformValue(input.targetRef, 'Discovery access target', 4096),
    requestedAccess: strings(input.requestedAccess,
      'Discovery requested access', DISCOVERY_REQUESTABLE_ACCESS),
    environment: platformValue(input.environment,
      'Discovery access environment', 512),
    reason: platformValue(input.reason, 'Discovery access reason', 4000),
    duration: platformValue(input.duration, 'Discovery access duration', 128),
    projectRef: optional(input.projectRef, 'Discovery access project', 512),
    policyResult: policyResult === null ? null : {...policyResult},
    approvers: input.approvers?.length ? strings(input.approvers,
      'Discovery access approvers') : [], status: input.status,
    providerGrantPlanRef: optional(input.providerGrantPlanRef,
      'Discovery provider grant plan', 512)});
}

export function validateCurationItem(input) {
  strict(input, 'Discovery curation item', ['schemaVersion', 'itemId', 'type',
    'targetRef', 'summary', 'evidenceRefs', 'status', 'assigneeRef', 'dueAt',
    'comments', 'createdAt', 'resolvedAt', 'resolutionRef']);
  if(input.schemaVersion !== 1 || !DISCOVERY_CURATION_TYPES.includes(input.type) ||
      !['OPEN', 'ASSIGNED', 'RESOLVED', 'DISMISSED'].includes(input.status)) {
    throw new TypeError('Discovery curation item type, state or version is invalid.');
  }
  if(!Array.isArray(input.comments ?? [])) throw new TypeError(
    'Discovery curation comments must be an array.'
  );
  const comments = (input.comments ?? []).map((comment) => {
    strict(comment, 'Discovery curation comment', ['actorRef', 'time', 'text']);
    return {actorRef: platformValue(comment.actorRef, 'Curation comment actor', 512),
      time: timestamp(comment.time, 'Curation comment time'),
      text: platformValue(comment.text, 'Curation comment', 4000)};
  });
  return immutable({schemaVersion: 1,
    itemId: platformValue(input.itemId, 'Discovery curation item ID', 512),
    type: input.type, targetRef: platformValue(input.targetRef,
      'Discovery curation target', 4096),
    summary: platformValue(input.summary, 'Discovery curation summary', 4000),
    evidenceRefs: input.evidenceRefs?.length ? strings(input.evidenceRefs,
      'Discovery curation evidence') : [], status: input.status,
    assigneeRef: optional(input.assigneeRef, 'Discovery curation assignee', 512),
    dueAt: input.dueAt ? timestamp(input.dueAt, 'Discovery curation due time') : null,
    comments, createdAt: timestamp(input.createdAt, 'Discovery curation creation time'),
    resolvedAt: input.resolvedAt ? timestamp(input.resolvedAt,
      'Discovery curation resolution time') : null,
    resolutionRef: optional(input.resolutionRef,
      'Discovery curation resolution reference', 512)});
}

export function validateCurationEvidence(input) {
  strict(input, 'Discovery curation evidence', ['schemaVersion', 'evidenceId',
    'actorRef', 'time', 'targetRef', 'fieldOrRelationship', 'before', 'after',
    'reason', 'sourceEvidenceRefs']);
  return immutable({schemaVersion: 1,
    evidenceId: platformValue(input.evidenceId, 'Curation evidence ID', 512),
    actorRef: platformValue(input.actorRef, 'Curation evidence actor', 512),
    time: timestamp(input.time, 'Curation evidence time'),
    targetRef: platformValue(input.targetRef, 'Curation evidence target', 4096),
    fieldOrRelationship: platformValue(input.fieldOrRelationship,
      'Curation field or relationship', 512), before: input.before ?? null,
    after: input.after ?? null,
    reason: platformValue(input.reason, 'Curation reason', 4000),
    sourceEvidenceRefs: strings(input.sourceEvidenceRefs,
      'Curation source evidence')});
}

export function validateDuplicateCandidate(input) {
  strict(input, 'Discovery duplicate candidate', ['schemaVersion', 'candidateId',
    'leftRef', 'rightRef', 'signals', 'createdAt']);
  if(!Array.isArray(input.signals) || !input.signals.length) throw new TypeError(
    'Discovery duplicate candidate requires evidence signals.'
  );
  noRawSecrets(input.signals, 'Discovery duplicate signals');
  const leftRef = platformValue(input.leftRef, 'Duplicate left reference', 4096);
  const rightRef = platformValue(input.rightRef, 'Duplicate right reference', 4096);
  return immutable({schemaVersion: 1,
    candidateId: platformValue(input.candidateId, 'Duplicate candidate ID', 512),
    leftRef, rightRef, signals: input.signals.map((item) => immutable({...item})),
    createdAt: timestamp(input.createdAt, 'Duplicate candidate time')});
}

export function validateIndexSourceConfiguration(input) {
  strict(input, 'Discovery index source configuration', ['schemaVersion',
    'sourceId', 'name', 'type', 'scope', 'enabled', 'mode', 'schedule',
    'mandatory', 'credentialRef', 'respectProviderVisibility', 'version']);
  if(input.schemaVersion !== 1 || !DISCOVERY_INDEX_SOURCE_TYPES.includes(input.type) ||
      !DISCOVERY_INDEX_MODES.includes(input.mode)) throw new TypeError(
    'Discovery index source type, mode or version is invalid.'
  );
  if(input.type.startsWith('provider_') &&
      input.respectProviderVisibility !== true) throw new TypeError(
    'Provider index sources must respect provider visibility.'
  );
  if(['INCREMENTAL_POLL', 'FULL_RECONCILE'].includes(input.mode) &&
      !String(input.schedule ?? '').trim()) throw new TypeError(
    'Scheduled index source mode requires a schedule.'
  );
  return immutable({schemaVersion: 1,
    sourceId: platformValue(input.sourceId, 'Discovery index source ID', 512),
    name: platformValue(input.name, 'Discovery index source name', 120),
    type: input.type, scope: input.scope ?? [],
    enabled: bool(input.enabled, 'Discovery index source enabled'), mode: input.mode,
    schedule: optional(input.schedule, 'Discovery index source schedule', 512),
    mandatory: bool(input.mandatory, 'Discovery mandatory index source'),
    credentialRef: optional(input.credentialRef,
      'Discovery index source credential reference', 512),
    respectProviderVisibility: bool(input.respectProviderVisibility,
      'Discovery provider visibility setting'),
    version: platformValue(input.version, 'Discovery index source version', 128)});
}

export function validateIndexSourceHealth(input) {
  strict(input, 'Discovery index source health', ['sourceId', 'state',
    'lastSuccessfulAt', 'lastAttemptedAt', 'documentsAdded', 'documentsUpdated',
    'documentsDeleted', 'errors', 'permissionFailures', 'staleCount',
    'queueDepth', 'averageLatencyMs']);
  if(!DISCOVERY_INDEX_SOURCE_STATES.includes(input.state)) throw new TypeError(
    'Discovery index source health state is invalid.'
  );
  return immutable({sourceId: platformValue(input.sourceId,
    'Discovery index source health ID', 512), state: input.state,
  lastSuccessfulAt: input.lastSuccessfulAt ? timestamp(input.lastSuccessfulAt,
    'Discovery source success time') : null,
  lastAttemptedAt: input.lastAttemptedAt ? timestamp(input.lastAttemptedAt,
    'Discovery source attempt time') : null,
  documentsAdded: integer(input.documentsAdded ?? 0, 'Documents added'),
  documentsUpdated: integer(input.documentsUpdated ?? 0, 'Documents updated'),
  documentsDeleted: integer(input.documentsDeleted ?? 0, 'Documents deleted'),
  errors: integer(input.errors ?? 0, 'Discovery source errors'),
  permissionFailures: integer(input.permissionFailures ?? 0,
    'Discovery source permission failures'),
  staleCount: integer(input.staleCount ?? 0, 'Discovery source stale count'),
  queueDepth: integer(input.queueDepth ?? 0, 'Discovery source queue depth'),
  averageLatencyMs: integer(input.averageLatencyMs ?? 0,
    'Discovery source average latency')});
}

export function validateDiscoveryIndexPolicy(input) {
  strict(input, 'Discovery index administration policy', ['schemaVersion',
    'policyId', 'sourceRefs', 'backendRef', 'fieldWeights', 'synonyms',
    'stopWords', 'embeddingProfileRef', 'rankingProfileRefs', 'retentionDays',
    'usageWindows', 'curationWorkflowRef', 'securityTrimProfileRef',
    'reconcileScope', 'version']);
  plainObject(input.fieldWeights, 'Discovery index field weights');
  plainObject(input.synonyms, 'Discovery index synonyms');
  for(const [field, weight] of Object.entries(input.fieldWeights)) {
    platformValue(field, 'Discovery index weighted field', 256);
    if(typeof weight !== 'number' || !Number.isFinite(weight) || weight < 0) {
      throw new TypeError('Discovery index field weight is invalid.');
    }
  }
  for(const [term, values] of Object.entries(input.synonyms)) {
    platformValue(term, 'Discovery synonym term', 256);
    strings(values, `Discovery synonyms for ${term}`);
  }
  return immutable({schemaVersion: 1,
    policyId: platformValue(input.policyId, 'Discovery index policy ID', 512),
    sourceRefs: strings(input.sourceRefs, 'Discovery index source references'),
    backendRef: platformValue(input.backendRef, 'Discovery index backend reference', 512),
    fieldWeights: {...input.fieldWeights}, synonyms: {...input.synonyms},
    stopWords: input.stopWords?.length ? strings(input.stopWords,
      'Discovery stop words') : [],
    embeddingProfileRef: optional(input.embeddingProfileRef,
      'Discovery embedding profile', 512),
    rankingProfileRefs: strings(input.rankingProfileRefs,
      'Discovery ranking profile references'),
    retentionDays: integer(input.retentionDays,
      'Discovery index retention days', 1),
    usageWindows: strings(input.usageWindows, 'Discovery usage windows'),
    curationWorkflowRef: platformValue(input.curationWorkflowRef,
      'Discovery curation workflow', 512),
    securityTrimProfileRef: platformValue(input.securityTrimProfileRef,
      'Discovery security trim profile', 512),
    reconcileScope: strings(input.reconcileScope,
      'Discovery reconcile scope'),
    version: platformValue(input.version, 'Discovery index policy version', 128)});
}

export function validateDiscoveryIndexBackendConfiguration(input) {
  strict(input, 'Discovery index backend configuration', ['schemaVersion',
    'backendId', 'type', 'connectionRef', 'credentialRef', 'indexNamespace',
    'lexical', 'vector', 'facets', 'graph', 'version']);
  if(input.schemaVersion !== 1 || !DISCOVERY_INDEX_BACKEND_TYPES.includes(input.type)) {
    throw new TypeError('Discovery index backend type or version is invalid.');
  }
  if(input.type !== 'cdeadmin_managed' && !input.connectionRef) throw new TypeError(
    'External Discovery index backend requires a connection reference.'
  );
  if(input.lexical !== true || input.facets !== true) throw new TypeError(
    'Discovery index backend requires lexical search and facets.'
  );
  return immutable({schemaVersion: 1,
    backendId: platformValue(input.backendId, 'Discovery index backend ID', 512),
    type: input.type, connectionRef: optional(input.connectionRef,
      'Discovery index backend connection', 512),
    credentialRef: optional(input.credentialRef,
      'Discovery index backend credential reference', 512),
    indexNamespace: platformValue(input.indexNamespace,
      'Discovery index namespace', 512), lexical: true,
    vector: bool(input.vector, 'Discovery vector index feature'), facets: true,
    graph: bool(input.graph, 'Discovery graph index feature'),
    version: platformValue(input.version, 'Discovery index backend version', 128)});
}
