import {
  assertRunTransition, effectiveSchemaAction, normalizeRunResult,
  redactEventEnvelope, validateCDCDefinition, validateReplaySafety,
} from '../../../pgadmin/static/js/cdeadmin_ui/modules/cdc/CDCEngine';
import {definition, event, sinkRef} from './CDCTestUtils';

describe('CDC engine', () => {
  test('validates complete definitions and reports incomplete definitions', () => {
    expect(validateCDCDefinition(definition()).valid).toBe(true);
    expect(validateCDCDefinition({name: '', source: null, sink: null}).details).toEqual(
      expect.arrayContaining(['CDC definition name is required.',
        'A capture source ResourceRef is required.', 'A sink ResourceRef is required.'])
    );
  });

  test('warns for unknown delivery and unsafe non-default schema decisions', () => {
    const result = validateCDCDefinition(definition({deliveryPolicy: {
      guarantee: 'unknown', proof: {}, deduplication: {}, nativeDetails: {}},
    schemaEvolutionPolicy: {defaultAction: 'pause_and_review', nativeDetails: {}, changes: [{
      id: 'b1', classification: 'breaking', action: 'fail', description: '',
      mapping: {}, nativeDetails: {}}]}}));
    expect(result.warnings.join(' ')).toMatch(/bypasses.*unknown/i);
  });

  test('enforces runtime state transitions', () => {
    expect(assertRunTransition('provisioning', 'snapshotting')).toBe('snapshotting');
    expect(assertRunTransition('snapshotting', 'catching_up')).toBe('catching_up');
    expect(assertRunTransition('catching_up', 'streaming')).toBe('streaming');
    expect(assertRunTransition('streaming', 'paused')).toBe('paused');
    expect(assertRunTransition('paused', 'streaming')).toBe('streaming');
    expect(assertRunTransition('streaming', 'stopping')).toBe('stopping');
    expect(assertRunTransition('stopping', 'stopped')).toBe('stopped');
    expect(() => assertRunTransition('stopped', 'streaming')).toThrow('Invalid CDC run transition');
  });

  test('forces breaking and unknown schema changes to pause and review', () => {
    const policy = {defaultAction: 'auto_apply_compatible'};
    expect(effectiveSchemaAction({classification: 'breaking', action: 'fail'}, policy))
      .toBe('pause_and_review');
    expect(effectiveSchemaAction({classification: 'unknown'}, policy)).toBe('pause_and_review');
    expect(effectiveSchemaAction({classification: 'additive_compatible'}, policy))
      .toBe('auto_apply_compatible');
  });

  test('redacts all payloads without permission and paths with permission', () => {
    const denied = redactEventEnvelope(event(), {canViewPayloads: false});
    expect(denied.key).toBe('[REDACTED]');
    expect(denied.before).toBe('[REDACTED]');
    expect(denied.metadata).toBe('[REDACTED]');
    const allowed = redactEventEnvelope(event(), {canViewPayloads: true,
      redactedPaths: ['after.email']});
    expect(allowed.before.email).toBe('old@example.test');
    expect(allowed.after.email).toBe('[REDACTED]');
  });

  test('requires bounded replay safeguards and production confirmation', () => {
    const base = {from: '1', to: '2', target: sinkRef, estimatedEventCount: null,
      idempotencyAssessment: '', schemaCompatibility: '', production: true,
      productionConfirmed: false};
    const unsafe = validateReplaySafety(base);
    expect(unsafe.valid).toBe(false);
    expect(unsafe.details).toHaveLength(3);
    expect(unsafe.warnings).toContain('The provider did not supply an event-count estimate.');
    expect(validateReplaySafety({...base, idempotencyAssessment: 'dedup',
      schemaCompatibility: 'compatible', productionConfirmed: true}).valid).toBe(true);
  });

  test('keeps snapshot and stream measurements distinct in normalized results', () => {
    const result = normalizeRunResult({state: 'streaming', lag: 3, throughput: 12,
      checkpoint: {cursor: 9}, snapshotProgress: 0.8, errors: [], warnings: [],
      evidence: {observed: true}, nativeDetails: {}, events: []}, {phase: 'stream'});
    expect(result).toMatchObject({phase: 'stream', state: 'streaming', lag: 3,
      throughput: 12, checkpoint: {cursor: 9}, snapshotProgress: 0.8});
    expect(() => normalizeRunResult({state: 'streaming', lag: -1}, {phase: 'stream'}))
      .toThrow('lag');
  });
});
