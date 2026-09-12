/////////////////////////////////////////////////////////////
// OpenTelemetry normalization, redaction and trace analysis.
/////////////////////////////////////////////////////////////

import {immutable, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  SENSITIVE_ATTRIBUTE, createTracingContent, validateTrace, validateTraceAttributes,
} from './contracts';

function clone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}
function time(value, label) {
  const text = platformValue(value, label);
  const parsed = Date.parse(text);
  if(!Number.isFinite(parsed)) throw new TypeError(`${label} must be an ISO timestamp.`);
  return parsed;
}

export function validateTracingDefinition(input) {
  const content = createTracingContent(input); const details = []; const warnings = [];
  if(!content.name.trim()) details.push('Tracing asset name is required.');
  if(!content.sourceConfigs.length) warnings.push(
    'No trace source is configured; CDEadmin internal tracing remains available only when registered.'
  );
  content.sourceConfigs.forEach((source) => {
    if(['otlp_grpc', 'otlp_http'].includes(source.sourceType) && !source.endpoint) {
      details.push(`Trace source ${source.id} requires an OTLP endpoint.`);
    }
    if(source.sourceType === 'provider_native' && (!source.providerId || !source.resourceRef)) {
      details.push(`Provider-native trace source ${source.id} requires provider and resource identity.`);
    }
  });
  return immutable({valid: details.length === 0, details: [...new Set(details)],
    warnings: [...new Set(warnings)]});
}

export function redactAttributes(input, policy={}) {
  plainObject(input ?? {}, 'Trace attributes'); plainObject(policy ?? {}, 'Sensitivity policy');
  const deny = new Set((policy.denyKeys ?? []).map((key) => String(key).toLowerCase()));
  const allow = new Set((policy.allowKeys ?? []).map((key) => String(key).toLowerCase()));
  const redactSql = policy.captureRawStatements !== true;
  const redactPayload = policy.capturePayloads !== true;
  let redacted = 0; const output = {};
  const nested = (value) => {
    if(Array.isArray(value)) return value.map(nested);
    if(!value || typeof value !== 'object') return clone(value);
    return Object.fromEntries(Object.entries(value).map(([key, child]) => {
      if(SENSITIVE_ATTRIBUTE.test(key) || deny.has(key.toLowerCase())) {
        redacted += 1; return [key, '[REDACTED]'];
      }
      return [key, nested(child)];
    }));
  };
  for(const [key, value] of Object.entries(input ?? {})) {
    const normalized = key.toLowerCase();
    const sensitive = SENSITIVE_ATTRIBUTE.test(key) || deny.has(normalized) ||
      (redactSql && ['db.statement', 'db.query.text'].includes(normalized)) ||
      (redactPayload && /(?:payload|body|document)/i.test(key));
    if(sensitive && !allow.has(normalized)) {
      output[key] = '[REDACTED]'; redacted += 1;
    } else output[key] = nested(value);
  }
  return immutable({attributes: validateTraceAttributes(output), redacted});
}

export function sanitizeTrace(input, policy={}) {
  plainObject(input, 'Trace record'); let count = 0;
  const spans = (input.spans ?? []).map((span) => {
    const attrs = redactAttributes(span.attributes ?? {}, policy); count += attrs.redacted;
    const events = (span.events ?? []).map((event) => {
      const eventAttrs = redactAttributes(event.attributes ?? {}, policy); count += eventAttrs.redacted;
      return {...event, attributes: eventAttrs.attributes};
    });
    const links = (span.links ?? []).map((link) => {
      const linkAttrs = redactAttributes(link.attributes ?? {}, policy); count += linkAttrs.redacted;
      return {...link, attributes: linkAttrs.attributes};
    });
    const resource = redactAttributes(span.resource ?? {}, policy); count += resource.redacted;
    const scope = redactAttributes(span.instrumentationScope ?? {}, policy); count += scope.redacted;
    return {...span, attributes: attrs.attributes, events, links,
      resource: resource.attributes, instrumentationScope: scope.attributes};
  });
  const provenance = redactAttributes(input.provenance ?? {}, policy); count += provenance.redacted;
  return immutable({trace: validateTrace({...input, spans,
    provenance: provenance.attributes}), redacted: count});
}

function otlpValue(value) {
  if(value == null || typeof value !== 'object') return value;
  for(const key of ['stringValue', 'boolValue', 'intValue', 'doubleValue', 'bytesValue']) {
    if(Object.hasOwn(value, key)) return value[key];
  }
  if(value.arrayValue) return (value.arrayValue.values ?? []).map(otlpValue);
  if(value.kvlistValue) return Object.fromEntries(
    (value.kvlistValue.values ?? []).map((item) => [item.key, otlpValue(item.value)]));
  return clone(value);
}
function otlpAttributes(values=[]) {
  if(!Array.isArray(values)) throw new TypeError('OTLP attributes must be an array.');
  return Object.fromEntries(values.map((item) => [
    platformValue(item.key, 'OTLP attribute key', 4096), otlpValue(item.value),
  ]));
}
function nanos(value, label) {
  const number = Number(platformValue(value, label, 32));
  if(!Number.isFinite(number) || number < 0) throw new TypeError(`${label} is invalid.`);
  return new Date(Math.floor(number / 1000000)).toISOString();
}

export function importOTLP(input, {sourceId='otlp-import', policy={}, provenance={}}={}) {
  plainObject(input, 'OTLP document'); const byTrace = new Map();
  const resourceSpans = input.resourceSpans ?? input.resource_spans;
  if(!Array.isArray(resourceSpans)) throw new TypeError('OTLP document requires resourceSpans[].');
  resourceSpans.forEach((resourceEntry) => {
    const resource = otlpAttributes(resourceEntry.resource?.attributes ?? []);
    const scopes = resourceEntry.scopeSpans ?? resourceEntry.scope_spans ?? [];
    scopes.forEach((scopeEntry) => {
      const scope = {name: scopeEntry.scope?.name ?? '', version: scopeEntry.scope?.version ?? '',
        attributes: otlpAttributes(scopeEntry.scope?.attributes ?? [])};
      (scopeEntry.spans ?? []).forEach((span) => {
        const traceId = platformValue(span.traceId ?? span.trace_id, 'OTLP trace ID', 32).toLowerCase();
        const status = span.status?.code ?? span.status?.message ?? 'UNSET';
        const normalized = {spanId: span.spanId ?? span.span_id, traceId,
          parentSpanId: (span.parentSpanId ?? span.parent_span_id) || null,
          name: span.name, kind: span.kind ?? 'INTERNAL', nativeKind: String(span.kind ?? ''),
          startTime: nanos(span.startTimeUnixNano ?? span.start_time_unix_nano, 'OTLP span start'),
          endTime: nanos(span.endTimeUnixNano ?? span.end_time_unix_nano, 'OTLP span end'),
          status: String(status), attributes: otlpAttributes(span.attributes ?? []),
          events: (span.events ?? []).map((event, index) => ({id: `${span.spanId}:event:${index}`,
            name: event.name, timestamp: nanos(event.timeUnixNano ?? event.time_unix_nano,
              'OTLP event time'), attributes: otlpAttributes(event.attributes ?? [])})),
          links: (span.links ?? []).map((link) => ({traceId: link.traceId ?? link.trace_id,
            spanId: link.spanId ?? link.span_id, attributes: otlpAttributes(link.attributes ?? [])})),
          resource, instrumentationScope: scope, correlationRefs: []};
        const record = byTrace.get(traceId) ?? [];
        record.push(normalized); byTrace.set(traceId, record);
      });
    });
  });
  let redacted = 0;
  const traces = [...byTrace.entries()].map(([traceId, spans]) => {
    const ordered = spans.sort((left, right) => time(left.startTime, 'Span start') -
      time(right.startTime, 'Span start'));
    const sanitized = sanitizeTrace({traceId, rootSpanId: ordered.find(
      (span) => !span.parentSpanId)?.spanId, startTime: ordered[0].startTime,
    endTime: [...ordered].sort((left, right) => time(right.endTime, 'Span end') -
        time(left.endTime, 'Span end'))[0].endTime,
    status: ordered.some((span) => span.status === 'ERROR') ? 'ERROR' : 'UNSET',
    spans: ordered, sourceId, provenance: {format: 'OTLP JSON', ...provenance}}, policy);
    redacted += sanitized.redacted; return sanitized.trace;
  });
  return immutable({schema: 'cdeadmin.trace-import.v1', profile: 'OTLP JSON',
    traces, accepted: traces.length, dropped: 0, rejected: 0, redacted});
}

export function traceDuration(trace) {
  trace = validateTrace(trace); return Math.max(0,
    time(trace.endTime, 'Trace end') - time(trace.startTime, 'Trace start'));
}

export function criticalPath(traceInput) {
  const trace = validateTrace(traceInput); const children = new Map();
  trace.spans.forEach((span) => {
    const key = span.parentSpanId ?? '__root__';
    children.set(key, [...(children.get(key) ?? []), span]);
  });
  function longest(span) {
    const descendants = children.get(span.spanId) ?? [];
    const best = descendants.map(longest).sort((left, right) => right.duration - left.duration)[0];
    const own = Math.max(0, time(span.endTime, 'Span end') - time(span.startTime, 'Span start'));
    return best ? {duration: own + best.duration, spans: [span.spanId, ...best.spans]} :
      {duration: own, spans: [span.spanId]};
  }
  return immutable(longest(trace.spans.find((span) => span.spanId === trace.rootSpanId)));
}

export function paginateTraces(traces, filters={}, cursor=null, pageSize=100) {
  const maximum = Math.min(1000, Math.max(1, Number(pageSize) || 100));
  const start = cursor == null ? 0 : Number.parseInt(String(cursor), 10);
  if(!Number.isInteger(start) || start < 0) throw new TypeError('Trace cursor is invalid.');
  let selected = traces.map(validateTrace);
  if(filters.status) selected = selected.filter((item) => item.status === filters.status);
  if(filters.service) selected = selected.filter((item) => item.spans.some(
    (span) => span.resource['service.name'] === filters.service));
  if(filters.database) selected = selected.filter((item) => item.spans.some(
    (span) => span.attributes['db.namespace'] === filters.database ||
      span.attributes['db.name'] === filters.database));
  if(filters.resource) selected = selected.filter((item) => item.spans.some((span) =>
    JSON.stringify(span.resource).toLowerCase().includes(String(filters.resource).toLowerCase()) ||
    span.correlationRefs.some((ref) => JSON.stringify(ref).toLowerCase()
      .includes(String(filters.resource).toLowerCase()))));
  if(filters.text) selected = selected.filter((item) => `${item.traceId} ${item.sourceId} ${
    item.spans.map((span) => `${span.name} ${JSON.stringify(span.attributes)}`).join(' ')}`
    .toLowerCase().includes(String(filters.text).toLowerCase()));
  if(filters.minimumDurationMs != null) selected = selected.filter(
    (item) => traceDuration(item) >= Number(filters.minimumDurationMs));
  if(filters.from) selected = selected.filter((item) => time(item.startTime, 'Trace start') >=
    time(filters.from, 'Trace filter from'));
  if(filters.to) selected = selected.filter((item) => time(item.endTime, 'Trace end') <=
    time(filters.to, 'Trace filter to'));
  selected.sort((left, right) => right.startTime.localeCompare(left.startTime));
  const items = selected.slice(start, start + maximum);
  return immutable({items, nextCursor: start + maximum < selected.length ?
    String(start + maximum) : null, total: selected.length});
}

export function aggregateServiceMap(traces, observedWindow) {
  plainObject(observedWindow, 'Observed service-map window');
  const nodes = new Map(); const edges = new Map();
  traces.map(validateTrace).forEach((trace) => {
    const byId = new Map(trace.spans.map((span) => [span.spanId, span]));
    trace.spans.forEach((span) => {
      const service = String(span.resource['service.name'] ?? span.resource['db.system'] ?? 'unknown');
      const node = nodes.get(service) ?? {id: service, name: service, traceCount: 0, errorCount: 0};
      node.traceCount += 1; if(span.status === 'ERROR') node.errorCount += 1; nodes.set(service, node);
      if(span.parentSpanId) {
        const parent = byId.get(span.parentSpanId);
        if(!parent) return;
        const source = String(parent.resource['service.name'] ?? parent.resource['db.system'] ?? 'unknown');
        if(source === service) return;
        const id = `${source}->${service}`; const edge = edges.get(id) ??
          {id, from: source, to: service, observedCalls: 0, errorCount: 0};
        edge.observedCalls += 1; if(span.status === 'ERROR') edge.errorCount += 1; edges.set(id, edge);
      }
    });
  });
  return immutable({schema: 'cdeadmin.observed-service-map.v1', observedWindow: objectWindow(observedWindow),
    label: `Observed ${observedWindow.from} through ${observedWindow.to}`,
    nodes: [...nodes.values()].sort((a, b) => a.id.localeCompare(b.id)),
    edges: [...edges.values()].sort((a, b) => a.id.localeCompare(b.id))});
}
function objectWindow(value) {
  return immutable({from: platformValue(value.from, 'Observed window start'),
    to: platformValue(value.to, 'Observed window end')});
}

function toNanos(value) { return String(Math.round(time(value, 'OTLP time') * 1000000)); }
function toOTLPAttributes(input) {
  return Object.entries(input).map(([key, value]) => ({key, value: typeof value === 'boolean' ?
    {boolValue: value} : typeof value === 'number' ? {doubleValue: value} :
      {stringValue: typeof value === 'string' ? value : JSON.stringify(value)}}));
}
export function exportOTLP(traces, {profile='OTLP JSON 1.0'}={}) {
  const records = traces.map(validateTrace); const groups = new Map();
  records.forEach((trace) => trace.spans.forEach((span) => {
    const resourceKey = JSON.stringify(span.resource); const scopeKey = JSON.stringify(span.instrumentationScope);
    const key = `${resourceKey}\n${scopeKey}`; const group = groups.get(key) ??
      {resource: {attributes: toOTLPAttributes(span.resource)}, scopeSpans: [{scope: {
        name: span.instrumentationScope.name ?? '', version: span.instrumentationScope.version ?? '',
        attributes: toOTLPAttributes(span.instrumentationScope.attributes ?? {})}, spans: []}]};
    group.scopeSpans[0].spans.push({traceId: span.traceId, spanId: span.spanId,
      parentSpanId: span.parentSpanId ?? '', name: span.name,
      kind: span.kind === 'UNKNOWN' ? span.nativeKind : span.kind,
      startTimeUnixNano: toNanos(span.startTime), endTimeUnixNano: toNanos(span.endTime),
      status: {code: span.status}, attributes: toOTLPAttributes(span.attributes),
      events: span.events.map((event) => ({name: event.name, timeUnixNano: toNanos(event.timestamp),
        attributes: toOTLPAttributes(event.attributes)})),
      links: span.links.map((link) => ({traceId: link.traceId, spanId: link.spanId,
        attributes: toOTLPAttributes(link.attributes)}))}); groups.set(key, group);
  }));
  return immutable({schema: 'cdeadmin.trace-export.v1', profile,
    provenance: {format: 'OTLP JSON', traceCount: records.length},
    document: {resourceSpans: [...groups.values()]}});
}
