import {
  aggregateServiceMap, criticalPath, exportOTLP, importOTLP, paginateTraces,
  redactAttributes, sanitizeTrace, traceDuration, validateTracingDefinition,
} from 'sources/cdeadmin_ui/modules/tracing/TracingEngine';
import {definition, span, trace} from './TracingTestUtils';

describe('Distributed Tracing engine', () => {
  test('redacts SQL, bind/payload and secret attributes before persistence', () => {
    const result = redactAttributes({'db.statement': 'select secret', 'db.bind.value': '7',
      password: 'bad', safe: 'visible'}, {captureRawStatements: false, capturePayloads: false});
    expect(result.attributes).toEqual({'db.statement': '[REDACTED]',
      'db.bind.value': '[REDACTED]', password: '[REDACTED]', safe: 'visible'});
    expect(result.redacted).toBe(3);
    const saved = sanitizeTrace(trace({spans: [span({attributes: {
      'db.statement': 'select 1', safe: 'yes'}})]}), {}).trace;
    expect(saved.spans[0].attributes['db.statement']).toBe('[REDACTED]');
    const nested = sanitizeTrace(trace({provenance: {transport: {accessToken: 'bad'}},
      spans: [span({attributes: {safe: {password: 'bad'}}})]}), {});
    expect(nested.trace.provenance.transport.accessToken).toBe('[REDACTED]');
    expect(nested.trace.spans[0].attributes.safe.password).toBe('[REDACTED]');
  });

  test('imports and exports OTLP while preserving identifiers and unknown attributes', () => {
    const imported = importOTLP({resourceSpans: [{resource: {attributes: [{key: 'service.name',
      value: {stringValue: 'api'}}]}, scopeSpans: [{scope: {name: 'test', version: '1'},
      spans: [{traceId: trace().traceId, spanId: span().spanId, parentSpanId: '',
        name: 'request', kind: 'FUTURE_KIND', startTimeUnixNano: '1789084800000000000',
        endTimeUnixNano: '1789084800050000000', status: {code: 'OK'},
        attributes: [{key: 'vendor.future', value: {stringValue: 'kept'}}],
        events: [], links: []}]}]}]}, {sourceId: 'file'});
    expect(imported).toMatchObject({accepted: 1, rejected: 0});
    expect(imported.traces[0].traceId).toBe(trace().traceId);
    expect(imported.traces[0].spans[0].attributes['vendor.future']).toBe('kept');
    const output = exportOTLP(imported.traces);
    expect(output.document.resourceSpans[0].scopeSpans[0].spans[0].traceId).toBe(trace().traceId);
  });

  test('paginates, filters and exposes the next bounded cursor', () => {
    const second = trace({traceId: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      spans: [span({traceId: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        spanId: 'bbbbbbbbbbbbbbbb'})], rootSpanId: 'bbbbbbbbbbbbbbbb'});
    const page = paginateTraces([trace(), second], {service: 'cdeadmin'}, null, 1);
    expect(page.items).toHaveLength(1); expect(page.nextCursor).toBe('1');
    expect(paginateTraces([trace()], {status: 'ERROR'}, null, 10).items).toEqual([]);
  });

  test('calculates duration and critical path only from declared parent links', () => {
    const child = span({spanId: '3333333333333333', parentSpanId: span().spanId,
      startTime: '2026-09-11T00:00:00.010Z', endTime: '2026-09-11T00:00:00.030Z'});
    const value = trace({spans: [span(), child]});
    expect(traceDuration(value)).toBe(50);
    expect(criticalPath(value).spans).toEqual([span().spanId, child.spanId]);
  });

  test('labels service maps as observed time windows', () => {
    const child = span({spanId: '3333333333333333', parentSpanId: span().spanId,
      resource: {'service.name': 'database'}});
    const map = aggregateServiceMap([trace({spans: [span(), child]})],
      {from: trace().startTime, to: trace().endTime});
    expect(map.label).toMatch(/^Observed /); expect(map.edges[0]).toMatchObject({
      from: 'cdeadmin', to: 'database', observedCalls: 1});
  });

  test('validates required source-specific configuration', () => {
    expect(validateTracingDefinition(definition()).valid).toBe(true);
    expect(validateTracingDefinition(definition({sourceConfigs: [{...definition().sourceConfigs[0],
      endpoint: null, sourceType: 'otlp_http'}]})).valid).toBe(false);
  });
});
