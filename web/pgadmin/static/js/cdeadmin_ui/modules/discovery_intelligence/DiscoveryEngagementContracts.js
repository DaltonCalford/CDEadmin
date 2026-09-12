/////////////////////////////////////////////////////////////
// Discovery usage, recommendation and user-engagement contracts.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';

export const DISCOVERY_USAGE_TYPES = Object.freeze([
  'query_execution', 'dashboard_report_view', 'api_request',
  'etl_cdc_production_use', 'ml_training_evaluation_use', 'ai_context_use',
  'search_result_selection', 'favorite_collection_add', 'access_request',
  'project_reference',
]);

export const DISCOVERY_USAGE_WINDOWS = Object.freeze({
  '24h': 24 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
  '30d': 30 * 24 * 60 * 60 * 1000,
  '90d': 90 * 24 * 60 * 60 * 1000,
  all: null,
});

export const DISCOVERY_USAGE_WEIGHTS = Object.freeze({
  query_execution: 1,
  dashboard_report_view: 0.5,
  api_request: 0.25,
  etl_cdc_production_use: 1.5,
  ml_training_evaluation_use: 1.5,
  search_result_selection: 0.25,
  favorite_collection_add: 0.5,
  ai_context_use: 0.25,
  access_request: 0,
  project_reference: 0,
});

export const DISCOVERY_RECOMMENDATION_TYPES = Object.freeze([
  'related_by_lineage', 'frequently_used_together', 'similar_semantics',
  'same_business_term', 'same_domain', 'recommended_replacement',
  'certified_alternative', 'used_by_team', 'downstream_dashboard',
  'related_metric', 'related_api',
]);

export const DISCOVERY_FEEDBACK_TYPES = Object.freeze([
  'Useful', 'Not relevant', 'Wrong meaning', 'Outdated', 'Duplicate',
  'Should be certified', 'Missing data',
]);

export const DISCOVERY_SHARING_SCOPES = Object.freeze([
  'private', 'project', 'team', 'organization',
]);

function strict(input, label, fields) {
  plainObject(input, label); noRawSecrets(input, label);
  const unknown = Object.keys(input).find((field) => !fields.includes(field));
  if(unknown) throw new TypeError(`${label} contains unsupported field ${unknown}.`);
}

function number(value, label, {minimum=0, maximum=Number.MAX_SAFE_INTEGER}={}) {
  if(typeof value !== 'number' || !Number.isFinite(value) || value < minimum ||
      value > maximum) throw new TypeError(`${label} is invalid.`);
  return value;
}

function integer(value, label, limits={}) {
  value = number(value, label, limits);
  if(!Number.isInteger(value)) throw new TypeError(`${label} must be an integer.`);
  return value;
}

function boolean(value, label) {
  if(typeof value !== 'boolean') throw new TypeError(`${label} must be Boolean.`);
  return value;
}

function timestamp(value, label) {
  value = platformValue(value, label, 64);
  if(Number.isNaN(Date.parse(value))) throw new TypeError(`${label} is invalid.`);
  return value;
}

function optional(value, label, maximum=4096) {
  return value === null || value === undefined ? null : platformValue(
    value, label, maximum);
}

function strings(value, label, {maximum=4096, unique=true}={}) {
  if(!Array.isArray(value)) throw new TypeError(`${label} must be an array.`);
  const result = value.map((item) => platformValue(item, `${label} value`, maximum));
  if(unique && new Set(result).size !== result.length) throw new TypeError(
    `${label} contains duplicate values.`
  );
  return result;
}

export function validateProtectedActorScope(input) {
  strict(input, 'Protected actor scope', ['actorKey', 'teamKeys',
    'organizationKey']);
  return immutable({
    actorKey: platformValue(input.actorKey, 'Protected actor key', 512),
    teamKeys: strings(input.teamKeys ?? [], 'Protected team keys', {maximum: 512}),
    organizationKey: optional(input.organizationKey,
      'Protected organization key', 512),
  });
}

export function validateUsagePolicy(input={}) {
  strict(input, 'Discovery usage policy', ['schemaVersion', 'windows',
    'minDistinctUsers', 'showIndividualUsage', 'enabledTypes', 'eventWeights',
    'popularitySaturation', 'version']);
  if((input.schemaVersion ?? 1) !== 1) throw new TypeError(
    'Discovery usage policy schema version is invalid.'
  );
  const windows = strings(input.windows ?? ['7d', '30d', '90d'],
    'Discovery usage windows', {maximum: 8});
  if(!windows.length || windows.some((window) =>
    !Object.hasOwn(DISCOVERY_USAGE_WINDOWS, window))) throw new TypeError(
    'Discovery usage policy contains an invalid window.'
  );
  const enabledTypes = strings(input.enabledTypes ?? DISCOVERY_USAGE_TYPES,
    'Discovery usage enabled types', {maximum: 64});
  if(enabledTypes.some((type) => !DISCOVERY_USAGE_TYPES.includes(type))) {
    throw new TypeError('Discovery usage policy contains an invalid signal type.');
  }
  const eventWeights = {...DISCOVERY_USAGE_WEIGHTS, ...(input.eventWeights ?? {})};
  strict(eventWeights, 'Discovery usage event weights', DISCOVERY_USAGE_TYPES);
  for(const [type, value] of Object.entries(eventWeights)) number(value,
    `Discovery usage weight ${type}`, {maximum: 100});
  return immutable({schemaVersion: 1, windows,
    minDistinctUsers: integer(input.minDistinctUsers ?? 3,
      'Discovery usage privacy threshold', {minimum: 1, maximum: 100000}),
    showIndividualUsage: boolean(input.showIndividualUsage ?? false,
      'Discovery individual usage setting'), enabledTypes, eventWeights,
    popularitySaturation: number(input.popularitySaturation ?? 1000,
      'Discovery popularity saturation', {minimum: 1, maximum: 1e12}),
    version: platformValue(input.version ?? '1', 'Discovery usage policy version', 128)});
}

export function validateUsageEvent(input) {
  strict(input, 'Discovery usage event', ['schemaVersion', 'eventId', 'actorScope',
    'time', 'canonicalResourceRefs', 'accessSurfaceRefs', 'consumerType',
    'consumerRef', 'success', 'durationMs', 'rows', 'bytes']);
  if((input.schemaVersion ?? 1) !== 1) throw new TypeError(
    'Discovery usage event schema version is invalid.'
  );
  const canonicalResourceRefs = [...new Set(strings(input.canonicalResourceRefs,
    'Canonical resource references', {unique: false}))];
  if(!canonicalResourceRefs.length) throw new TypeError(
    'Discovery usage event requires a canonical resource reference.'
  );
  const accessSurfaceRefs = [...new Set(strings(input.accessSurfaceRefs ?? [],
    'Access surface references', {unique: false}))];
  if(!DISCOVERY_USAGE_TYPES.includes(input.consumerType)) throw new TypeError(
    'Discovery usage consumer type is invalid.'
  );
  return immutable({schemaVersion: 1,
    eventId: platformValue(input.eventId, 'Discovery usage event ID', 512),
    actorScope: validateProtectedActorScope(input.actorScope),
    time: timestamp(input.time, 'Discovery usage event time'),
    canonicalResourceRefs, accessSurfaceRefs, consumerType: input.consumerType,
    consumerRef: optional(input.consumerRef, 'Discovery consumer reference'),
    success: boolean(input.success, 'Discovery usage success'),
    durationMs: input.durationMs === null || input.durationMs === undefined ? null :
      number(input.durationMs, 'Discovery usage duration'),
    rows: input.rows === null || input.rows === undefined ? null :
      integer(input.rows, 'Discovery usage rows'),
    bytes: input.bytes === null || input.bytes === undefined ? null :
      integer(input.bytes, 'Discovery usage bytes')});
}

export function validateRecommendationEvidence(input) {
  strict(input, 'Discovery recommendation evidence', ['schemaVersion',
    'evidenceId', 'type', 'sourceRef', 'targetRef', 'score', 'reason',
    'evidenceRefs', 'evidenceKind', 'explicitEvidence', 'observedAt', 'expiresAt',
    'distinctActors']);
  if((input.schemaVersion ?? 1) !== 1 ||
      !DISCOVERY_RECOMMENDATION_TYPES.includes(input.type)) throw new TypeError(
    'Discovery recommendation evidence type or version is invalid.'
  );
  if(input.sourceRef === input.targetRef) throw new TypeError(
    'A Discovery recommendation cannot target its source.'
  );
  const evidenceRefs = strings(input.evidenceRefs ?? [],
    'Discovery recommendation evidence references');
  if(input.type === 'recommended_replacement' &&
      (input.explicitEvidence !== true ||
       !['relationship', 'deprecation'].includes(input.evidenceKind) ||
       !evidenceRefs.length)) {
    throw new TypeError(
      'Replacement recommendations require explicit relationship/deprecation evidence.'
    );
  }
  return immutable({schemaVersion: 1,
    evidenceId: platformValue(input.evidenceId,
      'Discovery recommendation evidence ID', 512), type: input.type,
    sourceRef: platformValue(input.sourceRef,
      'Discovery recommendation source reference', 4096),
    targetRef: platformValue(input.targetRef,
      'Discovery recommendation target reference', 4096),
    score: number(input.score, 'Discovery recommendation score', {maximum: 1}),
    reason: platformValue(input.reason, 'Discovery recommendation reason', 4000),
    evidenceRefs,
    evidenceKind: optional(input.evidenceKind,
      'Discovery recommendation evidence kind', 256),
    explicitEvidence: boolean(input.explicitEvidence ?? false,
      'Discovery recommendation explicit evidence'),
    observedAt: timestamp(input.observedAt,
      'Discovery recommendation observation time'),
    expiresAt: input.expiresAt ? timestamp(input.expiresAt,
      'Discovery recommendation expiry') : null,
    distinctActors: input.distinctActors === null ||
      input.distinctActors === undefined ? null : integer(input.distinctActors,
        'Discovery recommendation distinct actors', {minimum: 1})});
}

export function validateRecommendationPolicy(input={}) {
  strict(input, 'Discovery recommendation policy', ['schemaVersion',
    'enabledTypes', 'maximumPerType', 'staleDays', 'minDistinctUsers', 'version']);
  const enabledTypes = strings(input.enabledTypes ?? DISCOVERY_RECOMMENDATION_TYPES,
    'Discovery recommendation types', {maximum: 64});
  if(enabledTypes.some((type) => !DISCOVERY_RECOMMENDATION_TYPES.includes(type))) {
    throw new TypeError('Discovery recommendation policy contains an invalid type.');
  }
  return immutable({schemaVersion: 1, enabledTypes,
    maximumPerType: integer(input.maximumPerType ?? 10,
      'Discovery maximum recommendations', {minimum: 1, maximum: 50}),
    staleDays: integer(input.staleDays ?? 30,
      'Discovery recommendation stale days', {minimum: 1, maximum: 36500}),
    minDistinctUsers: integer(input.minDistinctUsers ?? 3,
      'Discovery recommendation privacy threshold', {minimum: 1, maximum: 100000}),
    version: platformValue(input.version ?? '1',
      'Discovery recommendation policy version', 128)});
}

function sharingScope(value) {
  if(!DISCOVERY_SHARING_SCOPES.includes(value)) throw new TypeError(
    'Discovery sharing scope is invalid.'
  );
  return value;
}

function boundedText(value, label, maximum) {
  const result = String(value ?? '');
  if(result.length > maximum) throw new TypeError(`${label} is too long.`);
  return result;
}

export function validateSavedSearch(input) {
  strict(input, 'Discovery saved search', ['schemaVersion', 'savedSearchId',
    'name', 'description', 'scope', 'ownerRef', 'query', 'filters',
    'rankingProfileRef', 'displayColumns', 'searchScope', 'version']);
  plainObject(input.query, 'Discovery saved query');
  plainObject(input.filters ?? {}, 'Discovery saved filters');
  plainObject(input.searchScope ?? {}, 'Discovery saved search scope');
  noRawSecrets(input.query, 'Discovery saved query');
  noRawSecrets(input.filters ?? {}, 'Discovery saved filters');
  noRawSecrets(input.searchScope ?? {}, 'Discovery saved search scope');
  return immutable({schemaVersion: 1,
    savedSearchId: platformValue(input.savedSearchId,
      'Discovery saved search ID', 512),
    name: platformValue(input.name, 'Discovery saved search name', 120),
    description: boundedText(input.description,
      'Discovery saved search description', 1000),
    scope: sharingScope(input.scope),
    ownerRef: platformValue(input.ownerRef, 'Discovery saved search owner', 512),
    query: {...input.query}, filters: {...(input.filters ?? {})},
    rankingProfileRef: optional(input.rankingProfileRef,
      'Discovery ranking profile reference', 512),
    displayColumns: strings(input.displayColumns ?? [],
      'Discovery saved display columns', {maximum: 256}),
    searchScope: {...(input.searchScope ?? {})},
    version: platformValue(input.version ?? '1',
      'Discovery saved search version', 128)});
}

export function validateDiscoveryCollection(input) {
  strict(input, 'Discovery collection', ['schemaVersion', 'collectionId', 'name',
    'description', 'scope', 'ownerRef', 'items', 'version']);
  return immutable({schemaVersion: 1,
    collectionId: platformValue(input.collectionId,
      'Discovery collection ID', 512),
    name: platformValue(input.name, 'Discovery collection name', 120),
    description: boundedText(input.description,
      'Discovery collection description', 2000),
    scope: sharingScope(input.scope),
    ownerRef: platformValue(input.ownerRef, 'Discovery collection owner', 512),
    items: strings(input.items ?? [], 'Discovery collection items'),
    version: platformValue(input.version ?? '1',
      'Discovery collection version', 128)});
}

export function validateSearchFeedback(input) {
  strict(input, 'Discovery search feedback', ['schemaVersion', 'feedbackId',
    'actorScope', 'resultRef', 'type', 'note', 'queryRef', 'createdAt']);
  if(!DISCOVERY_FEEDBACK_TYPES.includes(input.type)) throw new TypeError(
    'Discovery search feedback type is invalid.'
  );
  return immutable({schemaVersion: 1,
    feedbackId: platformValue(input.feedbackId,
      'Discovery search feedback ID', 512),
    actorScope: validateProtectedActorScope(input.actorScope),
    resultRef: platformValue(input.resultRef,
      'Discovery search feedback result', 4096), type: input.type,
    note: boundedText(input.note, 'Discovery feedback note', 2000),
    queryRef: optional(input.queryRef, 'Discovery feedback query reference', 512),
    createdAt: timestamp(input.createdAt, 'Discovery feedback creation time')});
}

export function validateSearchAnalyticsPolicy(input={}) {
  strict(input, 'Discovery analytics policy', ['schemaVersion',
    'rawQueryRetentionDays', 'anonymizeActors', 'minimumAggregationThreshold',
    'adminAccess', 'exportAllowed', 'aiAnalysisAllowed', 'version']);
  return immutable({schemaVersion: 1,
    rawQueryRetentionDays: integer(input.rawQueryRetentionDays ?? 0,
      'Discovery raw query retention', {maximum: 3650}),
    anonymizeActors: boolean(input.anonymizeActors ?? true,
      'Discovery analytics actor anonymization'),
    minimumAggregationThreshold: integer(
      input.minimumAggregationThreshold ?? 3,
      'Discovery analytics minimum threshold', {minimum: 1, maximum: 100000}),
    adminAccess: boolean(input.adminAccess ?? true,
      'Discovery analytics admin access'),
    exportAllowed: boolean(input.exportAllowed ?? false,
      'Discovery analytics export'),
    aiAnalysisAllowed: boolean(input.aiAnalysisAllowed ?? false,
      'Discovery analytics AI analysis'),
    version: platformValue(input.version ?? '1',
      'Discovery analytics policy version', 128)});
}

export function validateSearchAnalyticsEvent(input) {
  strict(input, 'Discovery search analytics event', ['schemaVersion',
    'activityId', 'actorScope', 'queryId', 'queryText', 'occurredAt',
    'resultCount', 'selectedRefs', 'usefulSelectedAt', 'accessRequested',
    'abandoned', 'confidence', 'queryDurationMs']);
  const occurredAt = timestamp(input.occurredAt,
    'Discovery search activity time');
  const usefulSelectedAt = input.usefulSelectedAt ? timestamp(
    input.usefulSelectedAt, 'Discovery useful selection time') : null;
  if(usefulSelectedAt && Date.parse(usefulSelectedAt) < Date.parse(occurredAt)) {
    throw new TypeError('Discovery useful selection predates its query.');
  }
  return immutable({schemaVersion: 1,
    activityId: platformValue(input.activityId,
      'Discovery search activity ID', 512),
    actorScope: validateProtectedActorScope(input.actorScope),
    queryId: platformValue(input.queryId, 'Discovery query ID', 512),
    queryText: optional(input.queryText, 'Discovery raw query text', 8192),
    occurredAt,
    resultCount: integer(input.resultCount, 'Discovery search result count'),
    selectedRefs: strings(input.selectedRefs ?? [],
      'Discovery selected references'), usefulSelectedAt,
    accessRequested: boolean(input.accessRequested ?? false,
      'Discovery access request conversion'),
    abandoned: boolean(input.abandoned ?? !(input.selectedRefs ?? []).length,
      'Discovery search abandonment'),
    confidence: input.confidence === null || input.confidence === undefined ? null :
      number(input.confidence, 'Discovery query confidence', {maximum: 1}),
    queryDurationMs: input.queryDurationMs === null ||
      input.queryDurationMs === undefined ? null : number(input.queryDurationMs,
        'Discovery query duration')});
}
