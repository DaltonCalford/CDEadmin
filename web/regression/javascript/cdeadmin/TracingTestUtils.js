import {
  FederatedSearchService, RelationshipGraphService, TaskExecutionService,
} from 'sources/cdeadmin_ui/platform/CoordinationServices';
import {
  TracingAdapterRegistry, TracingService,
} from 'sources/cdeadmin_ui/modules/tracing/TracingService';

export const resourceRef = Object.freeze({schema: 'cdeadmin.resource-ref.v1',
  canonical: 'postgresql://cluster/database', provider: 'postgresql'});

export function span(overrides={}) {
  return {spanId: '2222222222222222', traceId: '11111111111111111111111111111111',
    parentSpanId: null, name: 'SELECT work_orders', kind: 'CLIENT',
    startTime: '2026-09-11T00:00:00.000Z', endTime: '2026-09-11T00:00:00.050Z',
    status: 'OK', attributes: {'db.system': 'postgresql', 'db.name': 'app',
      'db.query.summary': 'SELECT work_orders'}, events: [], links: [],
    resource: {'service.name': 'cdeadmin'},
    instrumentationScope: {name: 'cdeadmin.query', version: '1.0'},
    correlationRefs: [resourceRef, {schema: 'cdeadmin.query-ref.v1', id: 'query-one'}],
    ...overrides};
}

export function trace(overrides={}) {
  return {traceId: '11111111111111111111111111111111', rootSpanId: '2222222222222222',
    startTime: '2026-09-11T00:00:00.000Z', endTime: '2026-09-11T00:00:00.050Z',
    status: 'OK', spans: [span()], sourceId: 'provider-one',
    provenance: {format: 'provider-native'}, ...overrides};
}

export function source(overrides={}) {
  return {id: 'provider-one', name: 'PostgreSQL native traces', sourceType: 'provider_native',
    enabled: true, endpoint: null, providerId: 'postgresql', resourceRef,
    credentialRef: {schema: 'cdeadmin.credential-ref.v1', id: 'cred-one'}, transport: {},
    sensitivityPolicyId: 'safe', nativeDetails: {}, ...overrides};
}

export function policy(overrides={}) {
  return {id: 'safe', name: 'Safe sampling', rate: 1,
    tailCriteria: {errors: true}, sensitiveAttributePolicy: {
      captureRawStatements: false, capturePayloads: false},
    retention: {days: 7}, enabled: true, ...overrides};
}

export function definition(overrides={}) {
  return {name: 'Reference tracing', description: 'OpenTelemetry and provider-native traces',
    savedSearches: [{id: 'errors', name: 'Errors', filters: {status: 'ERROR'},
      pageSize: 100, sort: {startTime: 'desc'}, description: 'Recent errors'}],
    savedViews: [{id: 'waterfall', name: 'Waterfall', kind: 'waterfall', searchId: 'errors',
      layout: {}, observedWindow: null, description: 'Trace waterfall'}],
    sourceConfigs: [source()], samplingPolicies: [policy()], retentionPolicyRef: null,
    extensions: {}, ...overrides};
}

export function providerResult(value, overrides={}) {
  return {supportState: 'supported_native', providerVersion: 'postgresql-test-1',
    evidence: {runtime: true}, warnings: [], nativeDetails: {},
    readCapabilities: ['trace.query'], writeCapabilities: ['trace.ingest'],
    discoveryCapabilities: ['trace.source'], nativeMechanisms: ['provider-native'],
    versionConstraints: [], limitations: [], runtimeEvidence: {observed: true},
    value, ...overrides};
}

export function adapter(calls=[]) {
  const record = (name, output) => async (input) => {
    calls.push({name, input}); return providerResult(typeof output === 'function' ? output(input) : output);
  };
  return {ingestNativeTrace: record('ingestNativeTrace', (input) => input.record),
    correlateDatabaseOperation: record('correlateDatabaseOperation', (input) => input.span),
    redactSensitiveAttributes: record('redactSensitiveAttributes', (input) => Object.fromEntries(
      Object.entries(input.attributes).map(([key, value]) => [key,
        /statement|password|payload/i.test(key) ? '[REDACTED]' : value]))),
    queryTraceStore: record('queryTraceStore', {items: [], nextCursor: null, partial: false,
      stale: false, searchableRange: {}, dropped: 0, rejected: 0, redacted: 0})};
}

export function serviceFixture(options={}) {
  const tasks = new TaskExecutionService({now: () => '2026-09-11T00:00:00Z'});
  const relationships = new RelationshipGraphService(); const search = new FederatedSearchService();
  const adapters = new TracingAdapterRegistry(); const calls = options.calls ?? [];
  adapters.register('postgresql', options.adapter ?? adapter(calls));
  const service = new TracingService({tasks, relationships, search, adapters,
    projectAssets: options.projectAssets, events: options.events,
    now: () => '2026-09-11T00:00:00Z'});
  return {service, tasks, relationships, search, adapters, calls};
}

export function sessionValue(overrides={}) {
  return {schema: 'cdeadmin.tracing-session.v1', id: 'tracing-one', content: definition(),
    state: 'ready', dirty: false, selection: {surface: 'trace_search'},
    result: {items: [trace()], nextCursor: null, partial: false, stale: false,
      searchableRange: {from: trace().startTime, to: trace().endTime}, dropped: 0,
      rejected: 0, redacted: 2}, selectedTraceId: trace().traceId,
    selectedSpanId: span().spanId, serviceMap: {schema: 'cdeadmin.observed-service-map.v1',
      observedWindow: {from: trace().startTime, to: trace().endTime},
      label: `Observed ${trace().startTime} through ${trace().endTime}`,
      nodes: [{id: 'cdeadmin', name: 'cdeadmin', traceCount: 1, errorCount: 0}], edges: []},
    sourceStatuses: [], sourceCounters: [{sourceId: 'provider-one', accepted: 1,
      dropped: 0, rejected: 0, redacted: 2}], activeTaskId: null, problems: [],
    history: [], error: '', createdAt: 'now', updatedAt: 'now', ...overrides};
}
