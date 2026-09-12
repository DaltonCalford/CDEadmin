import {
  SPAN_KINDS, TRACE_SOURCE_TYPES, TRACE_VIEW_KINDS, TRACING_UI_STATES,
  createTracingContent, tracingAssetRequest, validateSpan, validateTrace,
} from 'sources/cdeadmin_ui/modules/tracing/contracts';
import {definition, span, trace} from './TracingTestUtils';

describe('Distributed Tracing contracts', () => {
  test('publishes exact source, view, span and UI taxonomies', () => {
    expect(TRACE_SOURCE_TYPES).toEqual(['otlp_grpc', 'otlp_http', 'cdeadmin_internal',
      'provider_native', 'imported_file']);
    expect(TRACE_VIEW_KINDS).toHaveLength(4); expect(SPAN_KINDS).toHaveLength(5);
    expect(TRACING_UI_STATES).toHaveLength(11);
  });

  test.each(SPAN_KINDS)('preserves OpenTelemetry span kind %s', (kind) => {
    expect(validateSpan(span({kind})).kind).toBe(kind);
  });

  test('preserves unknown future span kind beside UNKNOWN normalization', () => {
    expect(validateSpan(span({kind: 'FUTURE_KIND'}))).toMatchObject({
      kind: 'UNKNOWN', nativeKind: 'FUTURE_KIND'});
  });

  test('requires exact hexadecimal IDs and explicit parent evidence', () => {
    expect(() => validateSpan(span({spanId: 'bad'}))).toThrow('hexadecimal');
    expect(() => validateTrace(trace({spans: [span({parentSpanId: '3333333333333333'})]})))
      .toThrow('unavailable parent');
  });

  test('validates cross-references among authored searches, views, sources and policies', () => {
    expect(createTracingContent(definition()).savedViews[0].searchId).toBe('errors');
    expect(() => createTracingContent(definition({savedViews: [{...definition().savedViews[0],
      searchId: 'missing'}]}))).toThrow('unknown search');
    expect(() => createTracingContent(definition({sourceConfigs: [{...definition().sourceConfigs[0],
      sensitivityPolicyId: 'missing'}]}))).toThrow('unknown sensitivity policy');
  });

  test('rejects raw secrets from canonical assets', () => {
    expect(() => createTracingContent(definition({extensions: {password: 'forbidden'}})))
      .toThrow(/credential/i);
  });

  test('builds a strict optimistic project-asset request', () => {
    expect(tracingAssetRequest({content: definition(), expectedVersion: 3})).toMatchObject({
      asset_type: 'cdeadmin.tracing.v1', schema_name: 'cdeadmin.tracing.v1',
      expected_version: 3});
  });
});
