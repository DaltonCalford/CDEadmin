import {
  CDC_CAPTURE_MECHANISMS, CDC_DELIVERY_GUARANTEES, CDC_EVOLUTION_ACTIONS,
  CDC_RUNTIME_STATES, CDC_SCHEMA_CHANGE_CLASSES, CDC_SNAPSHOT_MODES,
  CDC_START_KINDS, cdcAssetRequest, createCDCContent, exportCDCAsset,
  importCDCAsset, validateCheckpoint, validateEventEnvelope, validateReplayRequest,
} from '../../../pgadmin/static/js/cdeadmin_ui/modules/cdc/contracts';
import {credentialRef, definition, sinkRef, sourceRef} from './CDCTestUtils';

describe('CDC contracts', () => {
  test('publishes every normative enumeration', () => {
    expect(CDC_CAPTURE_MECHANISMS).toEqual(['log_based', 'logical_replication',
      'oplog/change_stream', 'provider_native_stream', 'trigger_based', 'polling',
      'mga_history', 'external_connector']);
    expect(CDC_START_KINDS).toEqual(['latest', 'timestamp', 'lsn', 'binlog', 'oplog',
      'offset', 'native']);
    expect(CDC_SNAPSHOT_MODES).toEqual(['none', 'initial', 'incremental', 'provider_native']);
    expect(CDC_DELIVERY_GUARANTEES).toContain('exactly_once_proven');
    expect(CDC_SCHEMA_CHANGE_CLASSES).toEqual(['additive_compatible',
      'compatible_with_mapping', 'breaking', 'unknown']);
    expect(CDC_EVOLUTION_ACTIONS).toContain('pause_and_review');
    expect(CDC_RUNTIME_STATES).toHaveLength(9);
  });

  test.each(CDC_CAPTURE_MECHANISMS)('accepts capture mechanism %s', (captureMechanism) => {
    expect(createCDCContent(definition({source: {...definition().source,
      captureMechanism}})).source.captureMechanism).toBe(captureMechanism);
  });

  test.each(CDC_START_KINDS)('validates start position %s', (kind) => {
    const source = {...definition().source, startPosition: {kind,
      value: kind === 'latest' ? null : 'position', nativeDetails: {}}};
    expect(createCDCContent(definition({source})).source.startPosition.kind).toBe(kind);
  });

  test('requires provider ResourceRefs and CredentialRefs', () => {
    expect(() => createCDCContent(definition({source: {...definition().source,
      resourceRef: {schema: 'cdeadmin.asset-ref.v1', projectId: 'p', assetId: 'a'}}})))
      .toThrow('ResourceRef');
    expect(() => createCDCContent(definition({source: {...definition().source,
      credentialRef: sourceRef}}))).toThrow('CredentialRef');
    expect(createCDCContent(definition()).source.credentialRef).toEqual(credentialRef);
  });

  test('requires complete evidence before exactly-once can be authored', () => {
    expect(() => createCDCContent(definition({deliveryPolicy: {
      guarantee: 'exactly_once_proven', proof: {sourceCapture: 'yes'},
      deduplication: {}, nativeDetails: {}}}))).toThrow('transport proof');
    const content = createCDCContent(definition({deliveryPolicy: {
      guarantee: 'exactly_once_proven', proof: {sourceCapture: 'a', transport: 'b',
        sinkApplication: 'c'}, deduplication: {}, nativeDetails: {}}}));
    expect(content.deliveryPolicy.guarantee).toBe('exactly_once_proven');
  });

  test('blocks unsafe auto-application and invalid incremental snapshots', () => {
    const change = {id: 'change-1', classification: 'breaking',
      action: 'auto_apply_compatible', description: '', mapping: {}, nativeDetails: {}};
    expect(() => createCDCContent(definition({schemaEvolutionPolicy: {
      defaultAction: 'pause_and_review', changes: [change], nativeDetails: {}}})))
      .toThrow('cannot be auto-applied');
    expect(() => createCDCContent(definition({source: {...definition().source,
      snapshotPolicy: {mode: 'incremental', nativeDetails: {}}}})))
      .toThrow('batch size');
  });

  test('round-trips deterministic canonical source and rejects secrets', () => {
    const serialized = exportCDCAsset(definition());
    expect(exportCDCAsset(importCDCAsset(serialized))).toBe(serialized);
    expect(() => createCDCContent(definition({extensions: {password: 'forbidden'}})))
      .toThrow(/credential/i);
    expect(() => importCDCAsset('{bad')).toThrow('Invalid CDC source');
  });

  test('builds asset request with exact resource bindings and no credential binding', () => {
    const request = cdcAssetRequest({projectId: 'p1', assetId: 'a1', name: 'Orders',
      path: 'cdc/orders.json', expectedVersion: 3, content: definition()});
    expect(request.asset_type).toBe('cdeadmin.cdc.v1');
    expect(request.resource_bindings).toEqual([sourceRef, sinkRef]);
    expect(request.resource_bindings).not.toContainEqual(credentialRef);
  });

  test('normalizes event envelopes without fabricating absent fields', () => {
    const envelope = validateEventEnvelope({sourceResourceRef: sourceRef,
      operation: 'delete', captureTime: 'now', sourcePosition: {cursor: 1}});
    expect(envelope).not.toHaveProperty('before');
    expect(envelope).not.toHaveProperty('after');
    expect(() => validateEventEnvelope({...envelope, operation: 'replace'})).toThrow('operation');
  });

  test('validates checkpoints and explicit bounded replay requests', () => {
    expect(validateCheckpoint({nativeCursor: {x: 1}, taskRevision: '7', capturedAt: 'now'}))
      .toMatchObject({taskRevision: '7'});
    const replay = validateReplayRequest({from: '1', to: '2', target: sinkRef,
      idempotencyAssessment: 'event ID', schemaCompatibility: 'compatible',
      production: true, productionConfirmed: true});
    expect(replay.productionConfirmed).toBe(true);
    expect(() => validateReplayRequest({...replay, from: '2'})).toThrow('distinct');
  });

  test('validates actionable lag alerts', () => {
    const content = createCDCContent(definition({alerts: [{id: 'lag-high', event: 'lag',
      threshold: {maximum: 30}, enabled: true, nativeDetails: {}}]}));
    expect(content.alerts[0].threshold.maximum).toBe(30);
    expect(() => createCDCContent(definition({alerts: [{id: 'lag-bad', event: 'lag',
      threshold: {}, enabled: true, nativeDetails: {}}]}))).toThrow('maximum threshold');
  });
});
