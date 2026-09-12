/////////////////////////////////////////////////////////////
// Distributed tracing ingestion, query, task and event authority.
/////////////////////////////////////////////////////////////

import {diagnosticsService, platformEventService, stablePlatformId} from '../../platform/PlatformRegistry';
import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  TRACING_MODULE_ID, createTracingContent, tracingAssetRequest, tracingReferenceKey,
  validateSourceConfig, validateTracePage, validateTraceSamplingPolicy,
} from './contracts';
import {
  aggregateServiceMap, exportOTLP, paginateTraces, sanitizeTrace,
  validateTracingDefinition,
} from './TracingEngine';

export const TRACING_TASKS = Object.freeze([
  'trace.search.large', 'trace.aggregate.service_map', 'trace.export',
]);
export const TRACING_EVENTS = Object.freeze([
  'trace.ingested', 'trace.source.degraded', 'trace.retention.expired',
]);
const SUPPORT_STATES = Object.freeze([
  'supported_native', 'supported_via_cdeadmin', 'supported_via_external_adapter',
  'read_only', 'partial', 'unsupported', 'unknown',
]);
const ADAPTER_METHODS = Object.freeze([
  'ingestNativeTrace', 'correlateDatabaseOperation',
  'redactSensitiveAttributes', 'queryTraceStore',
]);
const SEARCHABLE_COMMANDS = Object.freeze([
  ['trace.search', 'Search Traces'], ['trace.open', 'Open Trace'],
  ['trace.copy_id', 'Copy Trace ID'], ['trace.resource.open', 'Open Related Resource'],
  ['trace.query.open', 'Open Related Query'], ['trace.export.otel', 'Export OpenTelemetry'],
  ['trace.source.configure', 'Configure Trace Source'],
  ['trace.sampling.update', 'Update Sampling Policy'],
]);

function actor(user={}) { return String(user.id ?? user.username ?? user.email ?? 'unknown'); }
function requireState(session, allowed, operation) {
  if(!allowed.includes(session.state)) throw new Error(
    `state_blocked: ${operation} is unavailable while tracing is ${session.state}.`
  );
}
function providerId(source) {
  return source?.providerId ?? source?.resourceRef?.provider ?? source?.resourceRef?.providerId ?? null;
}
function stringList(input, name, provider) {
  if(!Array.isArray(input[name])) throw new TypeError(`${provider} result requires ${name}[].`);
  return [...new Set(input[name].map((item) => platformValue(item, `${provider} ${name}`)))].sort();
}

export function validateTracingProviderResult(input, provider, operation, {allowEmpty=false}={}) {
  plainObject(input, `${provider} ${operation} result`);
  const {value: _transientProviderValue, ...envelope} = input;
  noRawSecrets(envelope, `${provider} ${operation} result`);
  const supportState = platformValue(input.supportState, 'Tracing support state');
  if(!SUPPORT_STATES.includes(supportState)) throw new TypeError(
    `${provider} returned an invalid tracing support state.`
  );
  if(!Array.isArray(input.warnings)) throw new TypeError(`${provider} result requires warnings[].`);
  const result = immutable({supportState,
    providerVersion: platformValue(input.providerVersion, `${provider} provider version`),
    evidence: immutable({...plainObject(input.evidence, `${provider} evidence`)}),
    warnings: input.warnings.map(String),
    nativeDetails: immutable({...plainObject(input.nativeDetails, `${provider} native details`)}),
    readCapabilities: stringList(input, 'readCapabilities', provider),
    writeCapabilities: stringList(input, 'writeCapabilities', provider),
    discoveryCapabilities: stringList(input, 'discoveryCapabilities', provider),
    nativeMechanisms: stringList(input, 'nativeMechanisms', provider),
    versionConstraints: stringList(input, 'versionConstraints', provider),
    limitations: stringList(input, 'limitations', provider),
    runtimeEvidence: input.runtimeEvidence == null ? null : immutable({
      ...plainObject(input.runtimeEvidence, `${provider} runtime evidence`)}),
    value: input.value});
  if(supportState.startsWith('supported') && !allowEmpty && input.value === undefined) {
    throw new TypeError(`${provider} returned empty success for ${operation}.`);
  }
  return result;
}
function requireSupported(result, operation, allowPartial=false) {
  if(!result.supportState.startsWith('supported') && !(allowPartial &&
      ['partial', 'read_only'].includes(result.supportState))) throw new Error(
    `capability_missing: ${operation} is ${result.supportState}. ${result.limitations.join(' ')}`
  );
  return result;
}

export class TracingAdapterRegistry {
  constructor() { this.adapters = new Map(); }
  register(id, adapter) {
    id = stablePlatformId(id, 'Tracing provider ID');
    ADAPTER_METHODS.forEach((method) => {
      if(typeof adapter?.[method] !== 'function') throw new TypeError(
        `Tracing adapter ${id} requires ${method}().`
      );
    });
    if(this.adapters.has(id)) throw new Error(`Tracing adapter already registered: ${id}`);
    this.adapters.set(id, adapter); return () => this.adapters.delete(id);
  }
  get(id) { return id ? this.adapters.get(stablePlatformId(id, 'Tracing provider ID')) ?? null : null; }
  list() { return [...this.adapters.keys()].sort(); }
}

function view(session) {
  return immutable({schema: 'cdeadmin.tracing-session.v1', id: session.id,
    content: session.content, state: session.state, dirty: session.dirty,
    selection: {...session.selection}, result: session.result, selectedTraceId: session.selectedTraceId,
    selectedSpanId: session.selectedSpanId, serviceMap: session.serviceMap,
    sourceStatuses: [...session.sourceStatuses.values()], sourceCounters: [...session.sourceCounters.values()],
    activeTaskId: session.activeTaskId, problems: [...session.problems],
    history: [...session.history], error: session.error,
    createdAt: session.createdAt, updatedAt: session.updatedAt});
}

export class TracingService {
  constructor({tasks, relationships, search, projectAssets,
    adapters=new TracingAdapterRegistry(), events=platformEventService,
    diagnostics=diagnosticsService, now=() => new Date().toISOString()}={}) {
    if(!tasks || !relationships || !search) throw new TypeError(
      'Tracing service requires Task, Relationship and Search services.'
    );
    this.tasks = tasks; this.relationships = relationships; this.search = search;
    this.projectAssets = projectAssets; this.adapters = adapters; this.events = events;
    this.diagnostics = diagnostics; this.now = now; this.sessions = new Map();
    this.traceStore = new Map(); this.listeners = new Set(); this.sequence = 0;
    this.disposers = [
      tasks.register('trace.search.large', (request, context) => this._searchTask(request, context)),
      tasks.register('trace.aggregate.service_map', (request, context) => this._mapTask(request, context)),
      tasks.register('trace.export', (request, context) => this._exportTask(request, context)),
      search.register({id: 'tracing.search', priority: 38,
        types: ['tracing.asset', 'tracing.trace', 'tracing.resource', 'tracing.command'],
        search: async (query, {context={}}={}) => {
          if(!context.permissions?.includes('trace.view')) return [];
          const needle = query.toLowerCase(); const results = SEARCHABLE_COMMANDS
            .filter(([id, label]) => `${id} ${label}`.toLowerCase().includes(needle))
            .map(([id, label]) => ({id, type: 'tracing.command', label,
              context: 'Distributed tracing command', commandId: id}));
          for(const session of this.sessions.values()) {
            if(`${session.id} ${session.content.name}`.toLowerCase().includes(needle)) results.push({
              id: session.id, type: 'tracing.asset', label: session.content.name || session.id,
              context: 'Project tracing asset'});
          }
          for(const trace of this.traceStore.values()) {
            if(`${trace.traceId} ${trace.spans.map((span) => span.name).join(' ')}`
              .toLowerCase().includes(needle)) results.push({id: trace.traceId,
              type: 'tracing.trace', label: trace.traceId,
              context: `${trace.sourceId} · live trace`, traceId: trace.traceId});
          }
          return results;
        }}),
    ];
  }

  dispose() {
    for(const session of this.sessions.values()) this._clearRelationships(session);
    this.disposers.reverse().forEach((remove) => remove()); this.disposers = [];
  }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  _emit(session) { this.listeners.forEach((listener) => listener(view(session))); }
  _touch(session, {dirty=session.dirty}={}) {
    session.updatedAt = this.now(); session.dirty = dirty; this._emit(session); return view(session);
  }
  _session(id) {
    const session = this.sessions.get(String(id));
    if(!session) throw new Error(`Unknown tracing session: ${id}`); return session;
  }
  _source(session, sourceId) {
    const source = session.content.sourceConfigs.find((item) => item.id === sourceId);
    if(!source) throw new Error(`Unknown trace source: ${sourceId}`); return source;
  }
  _policy(session, source) {
    return session.content.samplingPolicies.find((item) => item.id === source.sensitivityPolicyId) ??
      {sensitiveAttributePolicy: {}, rate: 1, tailCriteria: {}, retention: {}, enabled: true};
  }
  create(input={}) {
    plainObject(input, 'Tracing session'); const id = input.id ?? `tracing-${++this.sequence}`;
    if(this.sessions.has(id)) throw new Error(`Tracing session already exists: ${id}`);
    const content = createTracingContent(input.content ?? input);
    const validation = validateTracingDefinition(content); const now = this.now();
    const session = {id, content, state: !content.savedSearches.length &&
      !content.sourceConfigs.length ? 'empty' : validation.valid ? 'ready' : 'validation_error',
    dirty: false, selection: {surface: 'trace_search'}, selectedTraceId: null,
    selectedSpanId: null, result: validateTracePage({items: [], searchableRange: {}}),
    serviceMap: null, sourceStatuses: new Map(), sourceCounters: new Map(), traceIds: new Set(),
    activeTaskId: null, problems: [...validation.details, ...validation.warnings],
    history: [], error: '', createdAt: now, updatedAt: now};
    this.sessions.set(id, session); this._mirrorRelationships(session); this._emit(session); return view(session);
  }
  get(id) { return view(this._session(id)); }
  list() { return [...this.sessions.values()].map(view); }
  select(id, selection={}) {
    const session = this._session(id);
    for(const key of ['surface', 'traceId', 'spanId']) if(selection[key] !== undefined) {
      if(key === 'traceId') session.selectedTraceId = selection[key];
      else if(key === 'spanId') session.selectedSpanId = selection[key];
      else session.selection.surface = selection[key];
    }
    return this._touch(session);
  }
  replaceDefinition(id, input, context={}) {
    const session = this._session(id); requireState(session,
      ['empty', 'ready', 'stale', 'partial', 'disconnected', 'validation_error', 'runtime_failure'],
      'definition editing');
    session.content = createTracingContent(input); const validation = validateTracingDefinition(session.content);
    session.state = validation.valid ? 'ready' : 'validation_error';
    session.problems = [...validation.details, ...validation.warnings]; session.error = '';
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'tracing.definition.update'}); this._mirrorRelationships(session);
    return this._touch(session, {dirty: true});
  }
  configureSource(id, sourceInput, context={}) {
    const session = this._session(id); requireState(session,
      ['empty', 'ready', 'stale', 'partial', 'disconnected', 'validation_error', 'runtime_failure'],
      'source configuration');
    const source = validateSourceConfig(sourceInput);
    session.content = createTracingContent({...session.content, sourceConfigs: [
      ...session.content.sourceConfigs.filter((item) => item.id !== source.id), source]});
    session.state = 'ready'; session.error = '';
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'trace.source.configure', sourceId: source.id}); this._mirrorRelationships(session);
    return this._touch(session, {dirty: true});
  }
  updateSampling(id, policyInput, context={}) {
    const session = this._session(id); requireState(session,
      ['empty', 'ready', 'stale', 'partial', 'disconnected', 'validation_error', 'runtime_failure'],
      'sampling policy update');
    const policy = validateTraceSamplingPolicy(policyInput);
    session.content = createTracingContent({...session.content, samplingPolicies: [
      ...session.content.samplingPolicies.filter((item) => item.id !== policy.id), policy]});
    session.state = 'ready'; session.error = '';
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'trace.sampling.update', policyId: policy.id});
    return this._touch(session, {dirty: true});
  }

  _adapter(source, operation) {
    const id = providerId(source); const adapter = this.adapters.get(id);
    if(!adapter) throw new Error(`capability_missing: ${operation}: no tracing adapter for ${id ?? 'unknown'}.`);
    return {adapter, id};
  }
  async _call(source, method, input, context={}, options={}) {
    const {adapter, id} = this._adapter(source, method);
    const raw = await adapter[method](input, context);
    return requireSupported(validateTracingProviderResult(raw, id, method, options), method,
      options.allowPartial === true);
  }
  async ingest(id, sourceId, recordInput, context={}) {
    const session = this._session(id); const source = this._source(session, sourceId);
    requireState(session, ['ready', 'partial', 'read_only'], 'trace ingestion');
    if(!source.enabled) throw new Error('configuration_invalid: trace source is disabled.');
    const policy = this._policy(session, source); let record = recordInput;
    let status = null;
    try {
      if(source.sourceType === 'provider_native') {
        const ingested = await this._call(source, 'ingestNativeTrace', {source, record}, context);
        status = ingested; record = ingested.value;
        const correlatedSpans = [];
        for(const span of record.spans ?? []) {
          const result = await this._call(source, 'correlateDatabaseOperation', {source, span}, context);
          correlatedSpans.push(result.value);
        }
        record = {...record, spans: correlatedSpans};
        const sanitizedSpans = [];
        for(const span of record.spans ?? []) {
          const result = await this._call(source, 'redactSensitiveAttributes',
            {source, attributes: span.attributes ?? {}, policy: policy.sensitiveAttributePolicy}, context);
          sanitizedSpans.push({...span, attributes: result.value});
        }
        record = {...record, spans: sanitizedSpans};
      }
      const sanitized = sanitizeTrace({...record, sourceId}, policy.sensitiveAttributePolicy);
      const counts = {...(session.sourceCounters.get(sourceId) ??
        {sourceId, accepted: 0, dropped: 0, rejected: 0, redacted: 0})};
      const tailMatched = sanitized.trace.status === 'ERROR' && policy.tailCriteria?.errors === true;
      const bucket = Number.parseInt(sanitized.trace.traceId.slice(0, 8), 16) / 0xffffffff;
      if(policy.enabled === false || (!tailMatched && bucket >= policy.rate)) {
        counts.dropped += 1; counts.redacted += sanitized.redacted;
        session.sourceCounters.set(sourceId, immutable({...counts})); this._touch(session);
        return immutable({trace: null, redacted: sanitized.redacted, dropped: true});
      }
      this.traceStore.set(`${id}:${sanitized.trace.traceId}`, sanitized.trace);
      session.traceIds.add(sanitized.trace.traceId);
      counts.accepted += 1; counts.redacted += sanitized.redacted;
      session.sourceCounters.set(sourceId, immutable({...counts}));
      if(status) session.sourceStatuses.set(sourceId, immutable({...status, sourceId}));
      this._mirrorTraceRelationships(sanitized.trace);
      this.events.publish('trace.ingested', {sessionId: id, sourceId,
        traceId: sanitized.trace.traceId, accepted: 1, redacted: sanitized.redacted},
      {origin: TRACING_MODULE_ID});
      session.history.push({at: this.now(), actor: actor(context.currentUser),
        action: 'trace.ingested', sourceId, traceId: sanitized.trace.traceId,
        accepted: 1, redacted: sanitized.redacted}); this._touch(session); return sanitized;
    } catch(error) {
      const counts = {...(session.sourceCounters.get(sourceId) ??
        {sourceId, accepted: 0, dropped: 0, rejected: 0, redacted: 0})};
      counts.rejected += 1; session.sourceCounters.set(sourceId, immutable({...counts}));
      session.state = 'partial'; session.error = error.message;
      this.events.publish('trace.source.degraded', {sessionId: id, sourceId,
        error: error.message}, {origin: TRACING_MODULE_ID}); this._touch(session); throw error;
    }
  }

  async _providerTraces(session, filters, cursor, pageSize, context) {
    const traces = []; let partial = false;
    for(const source of session.content.sourceConfigs.filter(
      (item) => item.enabled && item.sourceType === 'provider_native')) {
      try {
        const result = await this._call(source, 'queryTraceStore',
          {source, filters, cursor, pageSize}, context, {allowPartial: true});
        const page = validateTracePage(result.value); traces.push(...page.items);
        partial ||= page.partial || result.supportState === 'partial';
        session.sourceStatuses.set(source.id, immutable({...result, sourceId: source.id}));
      } catch(error) {
        if(error.message.includes('permission_denied')) throw error;
        partial = true; session.problems = [...new Set([...session.problems,
          `partial_result: ${source.name}: ${error.message}`])];
        this.events.publish('trace.source.degraded', {sessionId: session.id,
          sourceId: source.id, error: error.message}, {origin: TRACING_MODULE_ID});
      }
    }
    return {traces, partial};
  }
  async _query(session, {filters={}, cursor=null, pageSize=100}={}, context={}) {
    noRawSecrets(filters, 'Trace search filters'); const local = [...session.traceIds]
      .map((traceId) => this.traceStore.get(`${session.id}:${traceId}`)).filter(Boolean);
    const provider = await this._providerTraces(session, filters, cursor, pageSize, context);
    const unique = new Map([...local, ...provider.traces].map((trace) => [trace.traceId, trace]));
    const page = paginateTraces([...unique.values()], filters, cursor, pageSize);
    const times = [...unique.values()].flatMap((trace) => [trace.startTime, trace.endTime]).sort();
    return validateTracePage({items: page.items, nextCursor: page.nextCursor,
      searchableRange: {from: times[0] ?? null, to: times.at(-1) ?? null},
      partial: provider.partial || (session.sourceStatuses.size &&
        [...session.sourceStatuses.values()].some((item) => item.supportState === 'partial')),
      dropped: [...session.sourceCounters.values()].reduce((n, item) => n + item.dropped, 0),
      rejected: [...session.sourceCounters.values()].reduce((n, item) => n + item.rejected, 0),
      redacted: [...session.sourceCounters.values()].reduce((n, item) => n + item.redacted, 0)});
  }
  async searchTraces(id, query={}, context={}) {
    const session = this._session(id); requireState(session,
      ['ready', 'partial', 'read_only', 'runtime_failure'], 'trace search');
    session.state = 'loading'; this._touch(session);
    try {
      session.result = await this._query(session, query, context);
      session.state = session.result.partial ? 'partial' : session.result.stale ? 'stale' : 'ready';
      session.error = ''; session.problems = session.result.partial ? ['partial_result'] : [];
      session.history.push({at: this.now(), actor: actor(context.currentUser),
        action: 'trace.search', resultCount: session.result.items.length}); return this._touch(session);
    } catch(error) { this._failure(session, error); throw error; }
  }
  searchLarge(id, query={}, context={}) {
    const session = this._session(id); requireState(session,
      ['ready', 'partial', 'read_only', 'runtime_failure'], 'large trace search');
    const task = this.tasks.submit({type: 'trace.search.large', label: 'Search traces',
      sessionId: id, query, cancelable: true, resumable: false,
      audit: {actor: actor(context.currentUser), command: 'trace.search'}},
    {...context, owner: actor(context.currentUser)});
    session.activeTaskId = task.id; session.state = 'background_task_active'; return this._touch(session);
  }
  async _searchTask(request, context) {
    const session = this._session(request.sessionId); context.phase('searching');
    context.progress(0.1, 'Searching bounded trace pages.');
    try {
      session.result = await this._query(session, request.query, {...context, signal: context.signal});
      session.activeTaskId = null; session.state = session.result.partial ? 'partial' : 'ready';
      context.progress(1, 'Trace search completed.'); this._touch(session); return session.result;
    } catch(error) { this._failure(session, error); throw error; }
  }
  openTrace(id, traceId, spanId=null) {
    const session = this._session(id); const trace = session.result.items.find(
      (item) => item.traceId === traceId) ?? this.traceStore.get(`${id}:${traceId}`);
    if(!trace) throw new Error(`Trace is unavailable: ${traceId}`);
    if(spanId && !trace.spans.some((span) => span.spanId === spanId)) throw new Error(
      `Span is unavailable: ${spanId}`);
    session.selectedTraceId = traceId; session.selectedSpanId = spanId ?? trace.rootSpanId;
    session.selection.surface = 'trace_waterfall'; return this._touch(session);
  }
  copyId(id, traceId, spanId=null) {
    this._session(id); return immutable({kind: spanId ? 'span' : 'trace', value: spanId ?? traceId});
  }
  relatedReference(id, traceId, spanId, kind) {
    const session = this._session(id); const trace = session.result.items.find(
      (item) => item.traceId === traceId) ?? this.traceStore.get(`${id}:${traceId}`);
    const span = trace?.spans.find((item) => item.spanId === spanId);
    if(!span) throw new Error('Selected trace span is unavailable.');
    const suffix = kind === 'resource' ? 'resource-ref.v1' : 'query-ref.v1';
    const reference = span.correlationRefs.find((ref) => ref.schema.endsWith(suffix));
    if(!reference) throw new Error(`No related ${kind} reference is available.`); return reference;
  }
  aggregateMap(id, observedWindow, context={}) {
    const session = this._session(id); requireState(session, ['ready', 'partial'], 'service-map aggregation');
    const task = this.tasks.submit({type: 'trace.aggregate.service_map',
      label: 'Aggregate observed service map', sessionId: id, observedWindow,
      traceIds: session.result.items.map((item) => item.traceId), cancelable: true, resumable: false,
      audit: {actor: actor(context.currentUser), command: 'trace.aggregate.service_map'}},
    {...context, owner: actor(context.currentUser)});
    session.activeTaskId = task.id; session.state = 'background_task_active'; return this._touch(session);
  }
  async _mapTask(request, context) {
    const session = this._session(request.sessionId); context.phase('aggregating');
    const traces = request.traceIds.map((traceId) =>
      this.traceStore.get(`${session.id}:${traceId}`) ??
      session.result.items.find((item) => item.traceId === traceId)).filter(Boolean);
    session.serviceMap = aggregateServiceMap(traces, request.observedWindow);
    session.activeTaskId = null; session.state = 'ready'; context.progress(1, 'Observed service map ready.');
    this._touch(session); return session.serviceMap;
  }
  export(id, input={}, context={}) {
    const session = this._session(id); requireState(session, ['ready', 'partial', 'read_only'], 'trace export');
    const task = this.tasks.submit({type: 'trace.export', label: 'Export OpenTelemetry traces',
      sessionId: id, traceIds: input.traceIds ?? session.result.items.map((item) => item.traceId),
      profile: input.profile ?? 'OTLP JSON 1.0', cancelable: true, resumable: false,
      audit: {actor: actor(context.currentUser), command: 'trace.export.otel',
        environment: context.environment ?? 'unknown'}}, {...context, owner: actor(context.currentUser)});
    session.activeTaskId = task.id; session.state = 'background_task_active'; return this._touch(session);
  }
  async _exportTask(request, context) {
    const session = this._session(request.sessionId); context.phase('exporting');
    const traces = request.traceIds.map((traceId) =>
      this.traceStore.get(`${session.id}:${traceId}`) ??
      session.result.items.find((item) => item.traceId === traceId)).filter(Boolean);
    if(traces.length !== request.traceIds.length) throw new Error('partial_result: one or more traces are unavailable.');
    const output = exportOTLP(traces, {profile: request.profile});
    session.activeTaskId = null; session.state = 'ready'; context.progress(1, 'OpenTelemetry export complete.');
    this._touch(session); return output;
  }
  expire(id, traceIds, context={}) {
    const session = this._session(id); const removed = [];
    traceIds.forEach((traceId) => {
      if(this.traceStore.delete(`${id}:${traceId}`)) {
        session.traceIds.delete(traceId); removed.push(traceId);
      }
    });
    if(removed.length) this.events.publish('trace.retention.expired', {sessionId: id,
      traceIds: removed, expiredAt: this.now(), actor: actor(context.currentUser)},
    {origin: TRACING_MODULE_ID});
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'trace.retention.expired', traceIds: removed}); this._touch(session);
    return immutable({removed});
  }
  async save(id, request, context={}) {
    const session = this._session(id);
    if(!this.projectAssets) throw new Error('Project Asset service is unavailable.');
    const payload = tracingAssetRequest({...request, content: session.content});
    const saved = request.assetId ? await this.projectAssets.update(request.projectId,
      request.assetId, payload) : await this.projectAssets.create(request.projectId, payload);
    session.dirty = false; session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'asset.saved', assetId: saved.asset_id, version: saved.version});
    this._touch(session); return saved;
  }
  _failure(session, error) {
    session.activeTaskId = null; session.state = error.name === 'AbortError' ? 'ready' : 'runtime_failure';
    session.error = error.name === 'AbortError' ? '' : error.message;
    session.problems = [...new Set([...session.problems,
      `${error.name === 'AbortError' ? 'task_cancelled' : 'task_failed'}: ${error.message}`])];
    this._touch(session);
  }
  _clearRelationships(session) {
    const prefix = `tracing:${session.id}`; const graph = this.relationships.snapshot();
    graph.edges.filter((edge) => edge.id.startsWith(prefix)).forEach(
      (edge) => this.relationships.removeEdge(edge.id));
    graph.nodes.filter((node) => node.id.startsWith(prefix)).forEach(
      (node) => this.relationships.removeNode(node.id));
  }
  _mirrorRelationships(session) {
    this._clearRelationships(session); const asset = `tracing:${session.id}`;
    this.relationships.upsertNode({id: asset, kind: 'tracing.asset', label: session.content.name || session.id});
    session.content.sourceConfigs.forEach((source) => {
      const node = `${asset}:source:${source.id}`;
      this.relationships.upsertNode({id: node, kind: 'tracing.source', label: source.name,
        reference: source.resourceRef});
      this.relationships.upsertEdge({id: `${asset}:source:${source.id}`, from: asset, to: node,
        type: 'ingests_from', origin: TRACING_MODULE_ID,
        metadata: {sourceType: source.sourceType, origin: 'authored_definition'}});
    });
  }
  _mirrorTraceRelationships(trace) {
    const traceNode = `trace:${trace.traceId}`;
    this.relationships.upsertNode({id: traceNode, kind: 'tracing.trace', label: trace.traceId});
    trace.spans.forEach((span) => span.correlationRefs.forEach((reference, index) => {
      const target = `trace-ref:${tracingReferenceKey(reference)}`;
      this.relationships.upsertNode({id: target, kind: 'tracing.correlation',
        label: tracingReferenceKey(reference), reference});
      this.relationships.upsertEdge({id: `${traceNode}:span:${span.spanId}:ref:${index}`,
        from: traceNode, to: target, type: 'observed_correlation', origin: TRACING_MODULE_ID,
        metadata: {spanId: span.spanId, evidence: 'trace_ingestion'}});
    }));
  }
}

export function unsupportedTracingProviderResult(provider, operation, limitation) {
  return immutable({supportState: 'unsupported', providerVersion: String(provider),
    evidence: {}, warnings: [String(limitation)], nativeDetails: {}, readCapabilities: [],
    writeCapabilities: [], discoveryCapabilities: [], nativeMechanisms: [], versionConstraints: [],
    limitations: [String(limitation)], runtimeEvidence: null, operation, value: undefined});
}
