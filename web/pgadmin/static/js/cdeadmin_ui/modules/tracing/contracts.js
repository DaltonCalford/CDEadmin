/////////////////////////////////////////////////////////////
// Distributed tracing authored assets and runtime contracts.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';

export const TRACING_MODULE_ID = 'cdeadmin.tracing';
export const TRACING_SERVICE_ID = 'tracing.runtime';
export const TRACING_ASSET_TYPE = 'cdeadmin.tracing.v1';
export const TRACING_ASSET_SCHEMA = 'cdeadmin.tracing.asset.v1';
export const TRACE_SOURCE_TYPES = Object.freeze([
  'otlp_grpc', 'otlp_http', 'cdeadmin_internal', 'provider_native', 'imported_file',
]);
export const TRACE_VIEW_KINDS = Object.freeze([
  'waterfall', 'service_resource_map', 'trace_table', 'query_correlation',
]);
export const SPAN_KINDS = Object.freeze([
  'INTERNAL', 'SERVER', 'CLIENT', 'PRODUCER', 'CONSUMER',
]);
export const TRACING_UI_STATES = Object.freeze([
  'loading', 'empty', 'ready', 'stale', 'partial', 'permission_denied',
  'disconnected', 'validation_error', 'runtime_failure', 'read_only',
  'background_task_active',
]);

const REFERENCE_SCHEMAS = new Set([
  'cdeadmin.resource-ref.v1', 'cdeadmin.asset-ref.v1',
  'cdeadmin.external-ref.v1', 'cdeadmin.result-ref.v1',
  'cdeadmin.task-ref.v1', 'cdeadmin.query-ref.v1',
]);
const SENSITIVE_ATTRIBUTE = /(?:password|passwd|secret|token|private.?key|authorization|bind(?:\.|_)?value|document(?:\.|_)?payload)/i;

function object(value, label, maximum=1024 * 1024, secretFree=true) {
  const result = plainObject(value ?? {}, label);
  if(secretFree) noRawSecrets(result, label);
  if(JSON.stringify(result).length > maximum) throw new TypeError(`${label} exceeds its size limit.`);
  return immutable({...result});
}
function array(value, label, mapper, maximum=10000) {
  if(!Array.isArray(value) || value.length > maximum) throw new TypeError(
    `${label} must be an array containing no more than ${maximum} items.`
  );
  return value.map(mapper);
}
function optionalText(value, label, maximum=16384) {
  if(value === undefined || value === null || value === '') return null;
  return platformValue(value, label, maximum);
}
function finite(value, label, minimum=0, maximum=Number.MAX_SAFE_INTEGER) {
  const number = Number(value);
  if(typeof value === 'boolean' || !Number.isFinite(number) || number < minimum || number > maximum) {
    throw new TypeError(`${label} must be a finite number from ${minimum} through ${maximum}.`);
  }
  return number;
}
function unique(value, label, mapper, maximum=10000) {
  const result = array(value ?? [], label, mapper, maximum); const ids = new Set();
  result.forEach((item) => {
    if(ids.has(item.id)) throw new TypeError(`Duplicate ${label}: ${item.id}`);
    ids.add(item.id);
  });
  return result.sort((left, right) => left.id.localeCompare(right.id));
}
function hex(value, label, length) {
  const result = platformValue(value, label, length);
  if(result.length !== length || !/^[0-9a-f]+$/i.test(result)) throw new TypeError(
    `${label} must be a ${length}-character hexadecimal OpenTelemetry identifier.`
  );
  return result.toLowerCase();
}

export function validateTracingReference(input, label='Tracing reference') {
  plainObject(input, label); noRawSecrets(input, label);
  const schema = platformValue(input.schema, `${label} schema`);
  if(!REFERENCE_SCHEMAS.has(schema)) throw new TypeError(`${label} schema is unsupported.`);
  if(schema === 'cdeadmin.resource-ref.v1') {
    platformValue(input.canonical, `${label} canonical identity`, 8192);
    platformValue(input.provider ?? input.providerId, `${label} provider`);
  } else if(schema === 'cdeadmin.asset-ref.v1') {
    platformValue(input.projectId, `${label} project ID`);
    platformValue(input.assetId, `${label} asset ID`);
  } else platformValue(input.id, `${label} identity`, 8192);
  return immutable({...input});
}

export function tracingReferenceKey(input) {
  const ref = validateTracingReference(input);
  if(ref.schema === 'cdeadmin.resource-ref.v1') return `resource:${ref.canonical}`;
  if(ref.schema === 'cdeadmin.asset-ref.v1') return `asset:${ref.projectId}/${ref.assetId}`;
  return `${ref.schema}:${ref.id}`;
}

export function validateSavedSearch(input) {
  plainObject(input, 'Tracing saved search'); noRawSecrets(input, 'Tracing saved search');
  const pageSize = finite(input.pageSize ?? 100, 'Tracing search page size', 1, 1000);
  return immutable({schema: 'cdeadmin.tracing-saved-search.v1',
    id: platformValue(input.id, 'Tracing saved search ID'),
    name: platformValue(input.name, 'Tracing saved search name'),
    filters: object(input.filters, 'Tracing saved search filters'), pageSize,
    sort: object(input.sort, 'Tracing saved search sort'),
    description: String(input.description ?? '')});
}

export function validateSavedView(input) {
  plainObject(input, 'Tracing saved view'); noRawSecrets(input, 'Tracing saved view');
  const kind = platformValue(input.kind, 'Tracing saved view kind');
  if(!TRACE_VIEW_KINDS.includes(kind)) throw new TypeError('Tracing saved view kind is invalid.');
  return immutable({schema: 'cdeadmin.tracing-saved-view.v1',
    id: platformValue(input.id, 'Tracing saved view ID'),
    name: platformValue(input.name, 'Tracing saved view name'), kind,
    searchId: optionalText(input.searchId, 'Tracing saved view search ID'),
    layout: object(input.layout, 'Tracing saved view layout'),
    observedWindow: input.observedWindow == null ? null :
      object(input.observedWindow, 'Tracing observed window'),
    description: String(input.description ?? '')});
}

export function validateSourceConfig(input) {
  plainObject(input, 'Trace source configuration'); noRawSecrets(input, 'Trace source configuration');
  const sourceType = platformValue(input.sourceType, 'Trace source type');
  if(!TRACE_SOURCE_TYPES.includes(sourceType)) throw new TypeError('Trace source type is invalid.');
  const credentialRef = input.credentialRef == null ? null : object(
    input.credentialRef, 'Trace source credential reference');
  if(credentialRef && (credentialRef.schema !== 'cdeadmin.credential-ref.v1' ||
      !String(credentialRef.id ?? '').trim())) throw new TypeError(
    'Trace source credential reference must use cdeadmin.credential-ref.v1 with a stable ID.'
  );
  return immutable({schema: 'cdeadmin.trace-source.v1',
    id: platformValue(input.id, 'Trace source ID'),
    name: platformValue(input.name, 'Trace source name'), sourceType,
    enabled: input.enabled !== false,
    endpoint: optionalText(input.endpoint, 'Trace source endpoint', 8192),
    providerId: optionalText(input.providerId, 'Trace source provider ID'),
    resourceRef: input.resourceRef == null ? null : validateTracingReference(
      input.resourceRef, 'Trace source resource'),
    credentialRef,
    transport: object(input.transport, 'Trace source transport'),
    sensitivityPolicyId: optionalText(input.sensitivityPolicyId,
      'Trace source sensitivity policy ID'),
    nativeDetails: object(input.nativeDetails, 'Trace source native details')});
}

export function validateTraceSamplingPolicy(input) {
  plainObject(input, 'Tracing sampling policy'); noRawSecrets(input, 'Tracing sampling policy');
  return immutable({schema: 'cdeadmin.tracing-sampling-policy.v1',
    id: platformValue(input.id, 'Tracing sampling policy ID'),
    name: platformValue(input.name, 'Tracing sampling policy name'),
    rate: finite(input.rate, 'Tracing sampling rate', 0, 1),
    tailCriteria: object(input.tailCriteria, 'Tracing tail criteria'),
    sensitiveAttributePolicy: object(input.sensitiveAttributePolicy,
      'Tracing sensitive attribute policy'),
    retention: object(input.retention, 'Tracing sampling retention'),
    enabled: input.enabled !== false});
}

export function createTracingContent(input={}) {
  plainObject(input, 'Tracing asset content'); noRawSecrets(input, 'Tracing asset content');
  const content = immutable({schema: TRACING_ASSET_SCHEMA, schemaVersion: 1,
    moduleId: TRACING_MODULE_ID,
    name: String(input.name ?? ''), description: String(input.description ?? ''),
    savedSearches: unique(input.savedSearches, 'Tracing saved searches', validateSavedSearch),
    savedViews: unique(input.savedViews, 'Tracing saved views', validateSavedView),
    sourceConfigs: unique(input.sourceConfigs, 'Trace source configurations', validateSourceConfig),
    samplingPolicies: unique(input.samplingPolicies, 'Tracing sampling policies', validateTraceSamplingPolicy),
    retentionPolicyRef: input.retentionPolicyRef == null ? null : validateTracingReference(
      input.retentionPolicyRef, 'Tracing retention policy reference'),
    extensions: object(input.extensions, 'Tracing extensions')});
  const searches = new Set(content.savedSearches.map((item) => item.id));
  content.savedViews.forEach((item) => {
    if(item.searchId && !searches.has(item.searchId)) throw new TypeError(
      `Tracing saved view ${item.id} references unknown search ${item.searchId}.`
    );
  });
  const policies = new Set(content.samplingPolicies.map((item) => item.id));
  content.sourceConfigs.forEach((item) => {
    if(item.sensitivityPolicyId && !policies.has(item.sensitivityPolicyId)) throw new TypeError(
      `Trace source ${item.id} references unknown sensitivity policy ${item.sensitivityPolicyId}.`
    );
  });
  return content;
}

export function tracingAssetRequest(input={}) {
  const content = createTracingContent(input.content ?? input);
  return immutable({asset_type: TRACING_ASSET_TYPE, schema_name: TRACING_ASSET_TYPE,
    schema_version: 1, name: input.name ?? (content.name || 'Distributed tracing'),
    path: input.path ?? 'tracing/tracing.json', expected_version: input.expectedVersion ?? 0,
    content, metadata: {moduleId: TRACING_MODULE_ID, ...(input.metadata ?? {})},
    dependency_references: input.dependencyReferences ?? [],
    resource_bindings: input.resourceBindings ?? [],
    validation_state: input.validationState ?? 'valid',
    validation_details: input.validationDetails ?? []});
}

export function validateTraceAttributes(input, label='Trace attributes') {
  const attrs = plainObject(input ?? {}, label);
  if(JSON.stringify(attrs).length > 1024 * 1024) throw new TypeError(`${label} exceeds its size limit.`);
  for(const [key, value] of Object.entries(attrs)) {
    platformValue(key, `${label} key`, 4096);
    if(SENSITIVE_ATTRIBUTE.test(key) && value !== '[REDACTED]') throw new TypeError(
      `${label}.${key} must be redacted before persistence.`
    );
  }
  return immutable({...attrs});
}

export function validateSpanEvent(input) {
  plainObject(input, 'Span event');
  return immutable({schema: 'cdeadmin.span-event.v1',
    id: platformValue(input.id, 'Span event ID'), name: platformValue(input.name, 'Span event name'),
    timestamp: platformValue(input.timestamp, 'Span event timestamp'),
    attributes: validateTraceAttributes(input.attributes, 'Span event attributes')});
}

export function validateSpanLink(input) {
  plainObject(input, 'Span link');
  return immutable({schema: 'cdeadmin.span-link.v1',
    traceId: hex(input.traceId, 'Linked trace ID', 32), spanId: hex(input.spanId, 'Linked span ID', 16),
    attributes: validateTraceAttributes(input.attributes, 'Span link attributes')});
}

export function validateSpan(input) {
  plainObject(input, 'Trace span');
  const kind = platformValue(input.kind ?? 'INTERNAL', 'Span kind').toUpperCase();
  const nativeKind = optionalText(input.nativeKind, 'Native span kind');
  return immutable({schema: 'cdeadmin.trace-span.v1',
    spanId: hex(input.spanId, 'Span ID', 16), traceId: hex(input.traceId, 'Span trace ID', 32),
    parentSpanId: input.parentSpanId == null ? null : hex(input.parentSpanId, 'Parent span ID', 16),
    name: platformValue(input.name, 'Span name', 8192),
    kind: SPAN_KINDS.includes(kind) ? kind : 'UNKNOWN', nativeKind: SPAN_KINDS.includes(kind) ? nativeKind : kind,
    startTime: platformValue(input.startTime, 'Span start time'),
    endTime: platformValue(input.endTime, 'Span end time'),
    status: platformValue(input.status ?? 'UNSET', 'Span status'),
    attributes: validateTraceAttributes(input.attributes),
    events: array(input.events ?? [], 'Span events', validateSpanEvent, 10000),
    links: array(input.links ?? [], 'Span links', validateSpanLink, 10000),
    resource: object(input.resource, 'Telemetry resource attributes', 1024 * 1024, false),
    instrumentationScope: object(input.instrumentationScope,
      'Instrumentation scope', 65536, false),
    correlationRefs: array(input.correlationRefs ?? [], 'Span correlation references',
      (ref) => validateTracingReference(ref, 'Span correlation reference'), 1000)});
}

export function validateTrace(input) {
  plainObject(input, 'Trace record');
  const traceId = hex(input.traceId, 'Trace ID', 32);
  const spans = array(input.spans ?? [], 'Trace spans', validateSpan, 100000);
  if(!spans.length) throw new TypeError('Trace requires at least one span.');
  if(spans.some((span) => span.traceId !== traceId)) throw new TypeError(
    'Every span must retain the owning trace ID.'
  );
  const ids = new Set(spans.map((span) => span.spanId));
  if(ids.size !== spans.length) throw new TypeError('Trace span IDs must be unique.');
  spans.forEach((span) => {
    if(span.parentSpanId && !ids.has(span.parentSpanId)) throw new TypeError(
      `Span ${span.spanId} references an unavailable parent; timing overlap is not a relationship.`
    );
  });
  const roots = spans.filter((span) => !span.parentSpanId);
  const rootSpanId = input.rootSpanId == null ? roots[0]?.spanId : hex(
    input.rootSpanId, 'Root span ID', 16);
  if(!rootSpanId || !ids.has(rootSpanId)) throw new TypeError('Trace root span is unavailable.');
  return immutable({schema: 'cdeadmin.trace.v1', traceId, rootSpanId,
    startTime: platformValue(input.startTime ?? spans[0].startTime, 'Trace start time'),
    endTime: platformValue(input.endTime ?? spans.at(-1).endTime, 'Trace end time'),
    status: platformValue(input.status ?? 'UNSET', 'Trace status'), spans,
    sourceId: platformValue(input.sourceId, 'Trace source ID'),
    provenance: object(input.provenance, 'Trace provenance', 1024 * 1024, false)});
}

export function validateTracePage(input) {
  plainObject(input, 'Trace page');
  return immutable({schema: 'cdeadmin.trace-page.v1',
    items: array(input.items ?? [], 'Trace page items', validateTrace, 1000),
    nextCursor: optionalText(input.nextCursor, 'Trace page cursor'),
    partial: input.partial === true, stale: input.stale === true,
    searchableRange: object(input.searchableRange, 'Trace searchable range', 65536, false),
    dropped: finite(input.dropped ?? 0, 'Trace page dropped count'),
    rejected: finite(input.rejected ?? 0, 'Trace page rejected count'),
    redacted: finite(input.redacted ?? 0, 'Trace page redacted count')});
}

export {SENSITIVE_ATTRIBUTE};
