import {TaskExecutionService, RelationshipGraphService,
  FederatedSearchService} from '../../../pgadmin/static/js/cdeadmin_ui/platform/CoordinationServices';
import {CDCAdapterRegistry, CDCService} from '../../../pgadmin/static/js/cdeadmin_ui/modules/cdc/CDCService';

export const sourceRef = Object.freeze({schema: 'cdeadmin.resource-ref.v1',
  canonical: 'mongodb://cluster/orders', provider: 'mongodb'});
export const sinkRef = Object.freeze({schema: 'cdeadmin.resource-ref.v1',
  canonical: 'clickhouse://warehouse/orders', provider: 'clickhouse'});
export const credentialRef = Object.freeze({schema: 'cdeadmin.credential-ref.v1',
  scheme: 'vault', id: 'cdc-source'});

export function definition(overrides={}) {
  return {name: 'Orders CDC', description: 'Order changes', source: {
    resourceRef: sourceRef, captureMechanism: 'oplog/change_stream',
    nativeMechanism: 'MongoDB change streams', requiredPrivileges: ['changeStream'],
    credentialRef, startPosition: {kind: 'latest', nativeDetails: {}},
    snapshotPolicy: {mode: 'initial', consistency: 'majority', nativeDetails: {}},
    nativeDetails: {}}, filters: [], transforms: [], sink: {resourceRef: sinkRef,
    serialization: 'JSONEachRow', nativeDetails: {}}, deliveryPolicy: {
    guarantee: 'at_least_once', deduplication: {key: 'eventId'}, proof: {},
    nativeDetails: {}}, schemaEvolutionPolicy: {defaultAction: 'pause_and_review',
    changes: [], nativeDetails: {}}, alerts: [], visualLayout: {}, extensions: {}, ...overrides};
}

export function providerResult(value, overrides={}) {
  return {supportState: 'supported_native', providerVersion: 'test-1',
    evidence: {runtime: true}, warnings: [], nativeDetails: {},
    readCapabilities: ['cdc.read'], writeCapabilities: ['cdc.write'],
    discoveryCapabilities: ['cdc.discover'], nativeMechanisms: ['MongoDB change streams'],
    versionConstraints: [], limitations: [], value, ...overrides};
}

export function adapter({mechanisms=['MongoDB change streams'], events=[], calls=[],
  streamResult=null}={}) {
  const call = (name, value) => async (input) => {
    calls.push({name, input}); return providerResult(typeof value === 'function' ? value(input) : value);
  };
  return {getCapabilities: call('getCapabilities', {}),
    listCaptureMechanisms: call('listCaptureMechanisms', mechanisms),
    validateCapturePrivileges: call('validateCapturePrivileges', {valid: true}),
    resolveStartPosition: call('resolveStartPosition', {kind: 'native', value: 'cursor-1'}),
    validateSink: call('validateSink', {valid: true}),
    provision: call('provision', {provisioned: true}),
    snapshot: call('snapshot', {state: 'catching_up', lag: 2, throughput: 10,
      checkpoint: {phase: 'snapshot', cursor: 's1'}, snapshotProgress: 1, events: []}),
    stream: call('stream', streamResult ?? {state: 'streaming', lag: 0, throughput: 25,
      checkpoint: {phase: 'stream', cursor: 'c2'}, events}),
    controlRun: call('controlRun', {controlled: true}),
    readCheckpoint: call('readCheckpoint', {nativeCursor: {cursor: 'c2'},
      taskRevision: '2', capturedAt: '2026-09-11T00:00:00Z', nativeDetails: {}}),
    validateSchemaChange: call('validateSchemaChange', {valid: true}),
    inspectEvents: call('inspectEvents', events),
    replay: call('replay', (input) => input.action === 'prepare' ?
      {estimatedEventCount: 12, safe: true} : {applied: 12})};
}

export function serviceFixture(options={}) {
  const tasks = new TaskExecutionService({now: () => '2026-09-11T00:00:00Z'});
  const relationships = new RelationshipGraphService();
  const search = new FederatedSearchService(); const adapters = new CDCAdapterRegistry();
  const sourceAdapter = options.sourceAdapter ?? adapter(options);
  const sinkAdapter = options.sinkAdapter ?? adapter(options);
  adapters.register('mongodb', sourceAdapter); adapters.register('clickhouse', sinkAdapter);
  const service = new CDCService({tasks, relationships, search, adapters,
    projectAssets: options.projectAssets, events: options.events,
    now: () => '2026-09-11T00:00:00Z'});
  return {service, tasks, relationships, search, adapters, sourceAdapter, sinkAdapter};
}

export function event(overrides={}) {
  return {eventId: 'e1', sourceResourceRef: sourceRef, operation: 'update',
    eventTime: '2026-09-11T00:00:00Z', captureTime: '2026-09-11T00:00:01Z',
    transactionId: 'tx1', key: {id: 1}, before: {email: 'old@example.test'},
    after: {email: 'new@example.test'}, changedFields: ['email'],
    sourcePosition: {cursor: 'c2'}, schemaVersion: '3', headers: {tenant: 'a'},
    metadata: {partition: 1}, ...overrides};
}
