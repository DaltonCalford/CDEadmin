import {
  ReplicationAdapterRegistry, ReplicationService, validateReplicationProviderResult,
} from 'sources/cdeadmin_ui/modules/replication/ReplicationService';
import {
  FederatedSearchService, RelationshipGraphService, TaskExecutionService,
} from 'sources/cdeadmin_ui/platform/CoordinationServices';
import {PlatformEventService} from 'sources/cdeadmin_ui/platform/PlatformRegistry';
import {
  adapter, definition, failoverPlan, providerResult, serviceFixture, topology,
} from './ReplicationTestUtils';

async function discovered(fixture) {
  fixture.service.create({id: 'replication-one', content: definition()});
  const submitted = fixture.service.refresh('replication-one', 'topology-one');
  await fixture.tasks.wait(submitted.activeTaskId);
  return fixture.service.get('replication-one');
}

describe('Replication Topology service', () => {
  test('requires every provider responsibility and unique registration', () => {
    const registry = new ReplicationAdapterRegistry();
    expect(() => registry.register('bad', {})).toThrow('getCapabilities');
    registry.register('postgresql', adapter());
    expect(() => registry.register('postgresql', adapter())).toThrow('already registered');
    expect(registry.list()).toEqual(['postgresql']);
  });

  test('strictly validates all support states and rejects empty success', () => {
    expect(validateReplicationProviderResult(providerResult({}), 'postgresql', 'test'))
      .toMatchObject({supportState: 'supported_native'});
    expect(() => validateReplicationProviderResult({...providerResult({}), warnings: undefined},
      'postgresql', 'test')).toThrow('warnings');
    expect(() => validateReplicationProviderResult(providerResult(undefined),
      'postgresql', 'test')).toThrow('empty success');
    for(const supportState of ['supported_native', 'supported_via_cdeadmin',
      'supported_via_external_adapter', 'read_only', 'partial', 'unsupported', 'unknown']) {
      const value = supportState.startsWith('supported') ? {} : undefined;
      expect(validateReplicationProviderResult(providerResult(value, {supportState,
        runtimeEvidence: {observed: true}}), 'postgresql', 'test', {allowEmpty: true}))
        .toMatchObject({supportState, runtimeEvidence: {observed: true}});
    }
  });

  test('creates, edits and mirrors authored relationships', () => {
    const {service, relationships} = serviceFixture();
    expect(service.create({id: 'replication-one'}).state).toBe('empty');
    const changed = service.replaceDefinition('replication-one', definition(),
      {currentUser: {id: 'author'}});
    expect(changed).toMatchObject({state: 'ready', dirty: true});
    expect(relationships.snapshot().edges.map((item) => item.type).sort())
      .toEqual(['contains', 'contains', 'models', 'replicates_to']);
    service.dispose(); expect(relationships.snapshot().edges).toHaveLength(0);
  });

  test('discovers topology, each opaque position and provider lag independently', async () => {
    const fixture = serviceFixture(); const result = await discovered(fixture);
    expect(result).toMatchObject({state: 'ready', activeTaskId: null});
    expect(result.liveTopologies[0].participants.map((item) => item.position.rawValue))
      .toEqual(['0/20', '0/19']);
    expect(result.liveTopologies[0].lagSamples[0]).toMatchObject({value: 1024,
      calculation: {method: 'pg_wal_lsn_diff'}});
    expect(fixture.calls.map((item) => item.name)).toEqual([
      'getCapabilities', 'discoverTopology', 'readReplicationPosition',
      'readReplicationPosition', 'calculateOrReadLag']);
  });

  test.each([['partial', 'partial'], ['read_only', 'read_only'],
    ['unsupported', 'partial'], ['unknown', 'partial']])(
    'preserves explicit provider state %s as %s', async (supportState, expected) => {
      const custom = adapter(); custom.getCapabilities = async () => providerResult(undefined,
        {supportState, limitations: [`${supportState} evidence`]});
      const fixture = serviceFixture({adapter: custom});
      fixture.service.create({id: 'replication-one', content: definition()});
      const started = fixture.service.refresh('replication-one', 'topology-one');
      await fixture.tasks.wait(started.activeTaskId);
      expect(fixture.service.get('replication-one')).toMatchObject({state: expected,
        problems: [`${supportState} evidence`]});
    });

  test('fails closed when provider adapter is unavailable', async () => {
    const tasks = new TaskExecutionService(); const service = new ReplicationService({tasks,
      relationships: new RelationshipGraphService(), search: new FederatedSearchService(),
      adapters: new ReplicationAdapterRegistry()});
    service.create({id: 'replication-one', content: definition()});
    const started = service.refresh('replication-one', 'topology-one');
    await expect(tasks.wait(started.activeTaskId)).rejects.toThrow('no replication adapter');
    expect(service.get('replication-one').state).toBe('runtime_failure');
  });

  test('never accepts topology evidence from a different provider', async () => {
    const wrong = topology({providerId: 'mysql'});
    const fixture = serviceFixture({topologyResult: wrong});
    fixture.service.create({id: 'replication-one', content: definition()});
    const started = fixture.service.refresh('replication-one', 'topology-one');
    await expect(fixture.tasks.wait(started.activeTaskId)).rejects.toThrow('provider identity');
  });

  test('moves visual layout without invoking the provider', () => {
    const fixture = serviceFixture(); fixture.service.create({id: 'replication-one',
      content: definition()});
    const moved = fixture.service.updateLayout('replication-one', 'topology-one', 'replica',
      {x: 800, y: 600}, {currentUser: {id: 'designer'}});
    expect(moved.content.visualLayouts[0].positions.replica).toEqual({x: 800, y: 600});
    expect(fixture.calls).toEqual([]);
  });

  test('controls a live link only through prepared provider commands', async () => {
    const fixture = serviceFixture(); await discovered(fixture); fixture.calls.splice(0);
    await fixture.service.controlLink('replication-one', 'topology-one', 'link-one', 'pause');
    expect(fixture.calls.map((item) => item.name)).toEqual([
      'prepareReplicationCommand', 'executePreparedCommand']);
    expect(fixture.service.get('replication-one').liveTopologies[0].links[0].nativeState)
      .toBe('paused');
    await expect(fixture.service.controlLink('replication-one', 'topology-one',
      'link-one', 'promote')).rejects.toThrow('action is invalid');
  });

  test('requires all provider safety evidence before arming', async () => {
    const custom = adapter({reviewResult: {preconditions: [{id: 'quorum', passed: true,
      evidence: {}}], quorumSafe: true, candidateEligible: true, positionSafe: true,
    dataLossAssessed: false, evidence: {}, nativeDetails: {}}});
    const fixture = serviceFixture({adapter: custom}); await discovered(fixture);
    const validated = await fixture.service.validateFailover('replication-one', 'plan-one');
    expect(validated.failoverReviews[0].valid).toBe(false);
    expect(() => fixture.service.armFailover('replication-one', 'plan-one', {
      confirmationRef: 'ticket-1', environment: 'production', connection: 'cluster-one'}))
      .toThrow('validate failover');
  });

  test('arms the exact revision, executes, rediscovers and verifies failover', async () => {
    const events = new PlatformEventService(); const observed = [];
    events.subscribe('replication.failover.completed', (event) => observed.push(event));
    const fixture = serviceFixture({events}); await discovered(fixture);
    await fixture.service.validateFailover('replication-one', 'plan-one');
    fixture.service.armFailover('replication-one', 'plan-one', {confirmationRef: 'change-7',
      environment: 'production', connection: 'cluster-one'}, {currentUser: {id: 'operator'}});
    const started = fixture.service.executeFailover('replication-one', 'plan-one');
    await fixture.tasks.wait(started.activeTaskId);
    const complete = fixture.service.get('replication-one');
    expect(complete.liveTopologies[0].participants[1]).toMatchObject({nativeRole: 'primary',
      normalizedRole: 'writer_capable'});
    expect(complete.armedPlan).toBeNull(); expect(observed).toHaveLength(1);
    expect(fixture.calls.filter((item) => item.name === 'discoverTopology')).toHaveLength(2);
  });

  test('invalidates arming when an authored plan revision changes', async () => {
    const fixture = serviceFixture(); await discovered(fixture);
    await fixture.service.validateFailover('replication-one', 'plan-one');
    fixture.service.armFailover('replication-one', 'plan-one', {confirmationRef: 'ticket',
      environment: 'test', connection: 'cluster'});
    fixture.service.upsertPlan('replication-one', failoverPlan({targetRole: 'leader'}));
    expect(() => fixture.service.executeFailover('replication-one', 'plan-one'))
      .toThrow('not armed');
  });

  test('records role, degraded-link and lag-threshold events on rediscovery', async () => {
    const events = new PlatformEventService(); const observed = [];
    for(const type of ['replication.role.changed', 'replication.link.degraded',
      'replication.lag.threshold']) events.subscribe(type, (item) => observed.push(item.type));
    let calls = 0; const changing = adapter({lagResult: {...topology().lagSamples[0],
      id: 'unused'}});
    changing.discoverTopology = async () => providerResult(calls++ === 0 ? topology() : topology({
      participants: [{...topology().participants[0], nativeRole: 'leader'},
        topology().participants[1]], links: [{...topology().links[0],
        health: {...topology().links[0].health, overall: 'degraded', dimensions: {
          ...topology().links[0].health.dimensions, linkState: 'degraded'}}}]}));
    changing.calculateOrReadLag = async () => providerResult({id: `lag-${calls}`,
      linkId: 'link-one', observedAt: 'now', unit: 'bytes', value: 900,
      source: 'provider_reported', calculation: {}, nativeDetails: {}});
    const fixture = serviceFixture({adapter: changing, events}); await discovered(fixture);
    const again = fixture.service.refresh('replication-one', 'topology-one');
    await fixture.tasks.wait(again.activeTaskId);
    expect(observed).toEqual(expect.arrayContaining(['replication.role.changed',
      'replication.link.degraded', 'replication.lag.threshold']));
  });

  test('retries transient discovery and preserves authored content', async () => {
    const transient = adapter(); const original = transient.discoverTopology; let attempts = 0;
    transient.discoverTopology = async (...args) => {
      attempts++; if(attempts === 1) throw new Error('transient discovery failure');
      return original(...args);
    };
    const fixture = serviceFixture({adapter: transient});
    const initial = fixture.service.create({id: 'replication-one', content: definition()});
    const started = fixture.service.refresh('replication-one', 'topology-one',
      {retry: {maximum: 1}});
    await fixture.tasks.wait(started.activeTaskId);
    expect(attempts).toBe(2);
    expect(fixture.service.get('replication-one').content).toEqual(initial.content);
  });

  test('cancels discovery without discarding authored content', async () => {
    let entered; const running = new Promise((resolve) => { entered = resolve; });
    const blockedDiscovery = async (signal) => {
      entered(); await new Promise((_resolve, reject) => signal.addEventListener('abort', () => {
        const error = new Error('cancelled'); error.name = 'AbortError'; reject(error);
      }, {once: true}));
    };
    const fixture = serviceFixture({blockedDiscovery});
    const initial = fixture.service.create({id: 'replication-one', content: definition()});
    const started = fixture.service.refresh('replication-one', 'topology-one'); await running;
    expect(fixture.tasks.cancel(started.activeTaskId)).toBe(true);
    await expect(fixture.tasks.wait(started.activeTaskId)).rejects.toMatchObject({name: 'AbortError'});
    expect(fixture.service.get('replication-one').content).toEqual(initial.content);
  });

  test('creates deterministic snapshots and persists optimistically', async () => {
    const projectAssets = {update: jest.fn().mockResolvedValue({asset_id: 'asset-one', version: 3}),
      create: jest.fn()};
    const fixture = serviceFixture({projectAssets}); await discovered(fixture);
    const snap = fixture.service.createSnapshot('replication-one', 'topology-one', {
      snapshotId: 'snapshot-one', capturedAt: '2026-09-11T00:00:00Z',
      reference: {schema: 'cdeadmin.external-ref.v1', id: 'snapshot://one'}});
    expect(snap.content.savedTopologies.map((item) => item.id)).toContain('snapshot-one');
    await fixture.service.save('replication-one', {projectId: 'project-one', assetId: 'asset-one',
      name: 'Replication', path: 'replication/reference.json', expectedVersion: 2});
    expect(projectAssets.update).toHaveBeenCalledWith('project-one', 'asset-one',
      expect.objectContaining({expected_version: 2}));
    expect(fixture.service.get('replication-one').dirty).toBe(false);
  });

  test('federated search covers commands, assets, resources, plans and events', async () => {
    const eventTopology = topology({events: [{id: 'event-one', topologyId: 'topology-one',
      participantId: 'replica', linkId: null, type: 'role_changed', occurredAt: 'now',
      cause: 'provider rediscovery', evidence: {}, nativeDetails: {}}]});
    const fixture = serviceFixture({topologyResult: eventTopology}); await discovered(fixture);
    const context = {permissions: ['replication.view']};
    const results = async (query, options={context}) => (await fixture.search.search(
      query, options)).groups.flatMap((group) => group.results);
    expect((await results('refresh replication'))[0].type)
      .toBe('replication.command');
    expect((await results('reference topology'))[0].type)
      .toBe('replication.asset');
    expect((await results('cluster-one')).some(
      (item) => item.type === 'replication.resource')).toBe(true);
    expect((await results('promote replica'))[0].type)
      .toBe('replication.plan');
    expect((await results('role_changed'))[0].type).toBe('replication.event');
    expect((await fixture.search.search('reference', {context: {permissions: []}})).total).toBe(0);
  });
});
