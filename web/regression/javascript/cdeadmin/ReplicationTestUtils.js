import {
  FederatedSearchService, RelationshipGraphService, TaskExecutionService,
} from 'sources/cdeadmin_ui/platform/CoordinationServices';
import {
  ReplicationAdapterRegistry, ReplicationService,
} from 'sources/cdeadmin_ui/modules/replication/ReplicationService';

export const scopeRef = Object.freeze({schema: 'cdeadmin.resource-ref.v1',
  canonical: 'postgresql://cluster-one', provider: 'postgresql'});

export function replicationHealth(overall='healthy', overrides={}) {
  return {overall, dimensions: {connectivity: overall, linkState: overall,
    quorum: overall, errors: overall, provider: overall},
  policyAllowsUnknownCritical: false, providerReported: 'ONLINE', nativeDetails: {},
  ...overrides};
}

export function replicationPosition(id='0/16B6C50') {
  return {provider: 'postgresql', positionType: 'LSN', rawValue: id,
    comparableWithinScope: true, observedAt: '2026-09-11T00:00:00Z', nativeDetails: {}};
}

export function participant(id, normalizedRole, nativeRole, overrides={}) {
  return {id, name: id, resourceRef: {schema: 'cdeadmin.resource-ref.v1',
    canonical: `postgresql://cluster-one/${id}`, provider: 'postgresql'},
  normalizedRole, nativeRole, nativeState: 'streaming', groupId: 'group-one',
  health: replicationHealth(), position: replicationPosition(), storage: {bytes: 1024},
  connections: {active: 1}, nativeDetails: {}, ...overrides};
}

export function topology(overrides={}) {
  return {id: 'topology-one', name: 'PostgreSQL streaming replication', scopeRef,
    providerId: 'postgresql', revision: 'provider-1', participants: [
      participant('primary', 'writer_capable', 'primary'),
      participant('replica', 'read_only_replica', 'standby')],
    groups: [{id: 'group-one', name: 'Streaming group', nativeType: 'physical_streaming',
      memberIds: ['primary', 'replica'], quorumState: 'not_applicable',
      roleMetadata: {}, nativeDetails: {}}], links: [{id: 'link-one', name: 'Primary to replica',
      sourceParticipantId: 'primary', targetParticipantId: 'replica',
      mechanism: 'physical streaming', nativeState: 'streaming',
      health: replicationHealth(), position: null, checkpoint: {slot: 'slot-one'},
      errors: [], nativeDetails: {}}], lagSamples: [], conflictPolicy: {}, healthPolicy: {},
    events: [], nativeDetails: {}, ...overrides};
}

export function lagSample(overrides={}) {
  return {id: 'lag-one', linkId: 'link-one', observedAt: '2026-09-11T00:00:00Z',
    unit: 'bytes', value: 1024, source: 'provider_calculated',
    calculation: {method: 'pg_wal_lsn_diff'}, nativeDetails: {}, ...overrides};
}

export function planItem(id, action) {
  return {id, label: id, action, targetRef: null, parameters: {},
    evidenceRequirements: {}, nativeDetails: {}};
}

export function failoverPlan(overrides={}) {
  return {id: 'plan-one', name: 'Promote replica', topologyId: 'topology-one',
    candidateParticipantId: 'replica', targetRole: 'primary',
    preconditions: [planItem('quorum', 'check_quorum')],
    commands: [planItem('promote', 'pg_promote')],
    expectedTopology: {participantRoles: {replica: 'primary'},
      linkStates: {'link-one': 'streaming'}},
    verification: [planItem('verify', 'verify_primary')],
    rollback: [planItem('rollback', 'restore_primary')],
    dataLossRisk: 'Provider assessed', estimatedRPO: {value: 0, unit: 'bytes',
      evidence: {provider: 'postgresql'}}, estimatedRTO: null, nativeDetails: {}, ...overrides};
}

export function definition(overrides={}) {
  return {name: 'Reference topology', description: 'Exact native provider evidence',
    savedTopologies: [topology()], alertPolicies: [{id: 'lag-alert',
      topologyId: 'topology-one', linkId: 'link-one', metric: 'provider_lag',
      maximum: 500, unit: 'bytes', enabled: true, nativeDetails: {}}],
    failoverPlans: [failoverPlan()], visualLayouts: [{id: 'layout-one',
      topologyId: 'topology-one', positions: {primary: {x: 100, y: 100},
        replica: {x: 320, y: 100}}, nativeDetails: {}}], snapshotRefs: [],
    extensions: {}, ...overrides};
}

export function providerResult(value, overrides={}) {
  return {supportState: 'supported_native', providerVersion: 'postgresql-test-1',
    evidence: {runtime: true}, warnings: [], nativeDetails: {},
    readCapabilities: ['replication.read'], writeCapabilities: ['replication.control'],
    discoveryCapabilities: ['replication.discover'], nativeMechanisms: ['physical streaming'],
    versionConstraints: [], limitations: [], value, ...overrides};
}

export function failoverReview(overrides={}) {
  return {preconditions: [{id: 'quorum', passed: true, message: 'quorum safe', evidence: {}}],
    quorumSafe: true, candidateEligible: true, positionSafe: true,
    dataLossAssessed: true, evidence: {provider: true}, nativeDetails: {}, ...overrides};
}

export function adapter({calls=[], topologyResult, lagResult=lagSample(),
  reviewResult=failoverReview(), blockedDiscovery}={}) {
  let promoted = false;
  const record = (name, value) => async (input, context={}) => {
    calls.push({name, input});
    if(name === 'discoverTopology' && blockedDiscovery) {
      await blockedDiscovery(context.signal);
    }
    if(name === 'executePreparedCommand' && input.action === 'failover') promoted = true;
    let resolved = typeof value === 'function' ? value(input, promoted) : value;
    if(name === 'discoverTopology' && !topologyResult) {
      resolved = topology(promoted ? {revision: 'provider-2', participants: [
        participant('primary', 'read_only_replica', 'standby'),
        participant('replica', 'writer_capable', 'primary')]} : {});
    }
    return providerResult(resolved);
  };
  return {getCapabilities: record('getCapabilities', {}),
    discoverTopology: record('discoverTopology', topologyResult),
    readReplicationPosition: record('readReplicationPosition',
      (input) => replicationPosition(input.participant.id === 'primary' ? '0/20' : '0/19')),
    calculateOrReadLag: record('calculateOrReadLag', lagResult),
    validateFailover: record('validateFailover', reviewResult),
    prepareReplicationCommand: record('prepareReplicationCommand', {nativeCommand: 'provider-safe'}),
    executePreparedCommand: record('executePreparedCommand', (input) => ({nativeState:
      input.action === 'link.pause' ? 'paused' : 'streaming', executed: true}))};
}

export function serviceFixture(options={}) {
  const tasks = new TaskExecutionService({now: () => '2026-09-11T00:00:00Z'});
  const relationships = new RelationshipGraphService();
  const search = new FederatedSearchService(); const adapters = new ReplicationAdapterRegistry();
  const calls = options.calls ?? []; const provider = options.adapter ?? adapter({...options, calls});
  adapters.register('postgresql', provider);
  const service = new ReplicationService({tasks, relationships, search, adapters,
    projectAssets: options.projectAssets, events: options.events,
    now: () => '2026-09-11T00:00:00Z'});
  return {service, tasks, relationships, search, adapters, provider, calls};
}
