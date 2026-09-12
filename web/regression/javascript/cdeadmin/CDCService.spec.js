import {
  CDCAdapterRegistry, CDCService, validateCDCProviderResult,
} from '../../../pgadmin/static/js/cdeadmin_ui/modules/cdc/CDCService';
import {TaskExecutionService, RelationshipGraphService,
  FederatedSearchService} from '../../../pgadmin/static/js/cdeadmin_ui/platform/CoordinationServices';
import {PlatformEventService} from '../../../pgadmin/static/js/cdeadmin_ui/platform/PlatformRegistry';
import {adapter, definition, event, providerResult, serviceFixture,
  sinkRef, sourceRef} from './CDCTestUtils';

describe('CDC service', () => {
  test('requires all provider adapter responsibilities and unique registration', () => {
    const registry = new CDCAdapterRegistry();
    expect(() => registry.register('bad', {})).toThrow('getCapabilities');
    registry.register('mongodb', adapter());
    expect(() => registry.register('mongodb', adapter())).toThrow('already registered');
    expect(registry.list()).toEqual(['mongodb']);
  });

  test('strictly validates provider support evidence and empty success', () => {
    expect(validateCDCProviderResult(providerResult({ok: true}), 'mongodb', 'test'))
      .toMatchObject({supportState: 'supported_native', providerVersion: 'test-1'});
    expect(() => validateCDCProviderResult({...providerResult({}), warnings: undefined},
      'mongodb', 'test')).toThrow('warnings');
    expect(() => validateCDCProviderResult(providerResult(undefined), 'mongodb', 'test'))
      .toThrow('empty success');
    expect(() => validateCDCProviderResult(providerResult({}, {supportState: 'yes'}),
      'mongodb', 'test')).toThrow('support state');
    for(const supportState of ['supported_native', 'supported_via_cdeadmin',
      'supported_via_external_adapter', 'read_only', 'partial', 'unsupported', 'unknown']) {
      expect(validateCDCProviderResult(providerResult(supportState.startsWith('supported') ? {} :
        undefined, {supportState, runtimeEvidence: {observed: true}}), 'mongodb', 'test'))
        .toMatchObject({supportState, runtimeEvidence: {observed: true}});
    }
  });

  test('creates, edits, validates and mirrors source/sink relationships', () => {
    const {service, relationships} = serviceFixture();
    const created = service.create({id: 'cdc-1'});
    expect(created.state).toBe('empty');
    const updated = service.replaceDefinition('cdc-1', definition(),
      {currentUser: {id: 'u1'}});
    expect(updated.state).toBe('ready'); expect(updated.dirty).toBe(true);
    expect(updated.history[0]).toMatchObject({actor: 'u1', action: 'cdc.definition.create'});
    const graph = relationships.snapshot();
    expect(graph.edges.map((edge) => edge.type).sort()).toEqual(['reads_from', 'writes_to']);
    expect(graph.edges.every((edge) => edge.origin === 'cdeadmin.cdc')).toBe(true);
    service.dispose();
    expect(relationships.snapshot().edges).toHaveLength(0);
  });

  test('discovers exact provider mechanism, privileges and start position', async () => {
    const calls = []; const {service} = serviceFixture({calls});
    service.create({id: 'cdc-1', content: definition()});
    const result = await service.discover('cdc-1');
    expect(result.providerStatuses[0].nativeMechanisms).toContain('MongoDB change streams');
    expect(calls.map((item) => item.name)).toEqual(expect.arrayContaining([
      'getCapabilities', 'listCaptureMechanisms', 'validateCapturePrivileges',
      'resolveStartPosition']));
  });

  test('fails closed when exact native mechanism is not advertised', async () => {
    const {service} = serviceFixture({mechanisms: ['polling']});
    service.create({id: 'cdc-1', content: definition()});
    await expect(service.discover('cdc-1')).rejects.toThrow('did not advertise');
  });

  test('preserves partial discovery state but refuses to start a live run', async () => {
    const partial = adapter(); partial.getCapabilities = async () => providerResult({}, {
      supportState: 'partial', limitations: ['Snapshot evidence unavailable.']});
    const {service} = serviceFixture({sourceAdapter: partial});
    service.create({id: 'cdc-1', content: definition()});
    const discovered = await service.discover('cdc-1');
    expect(discovered.state).toBe('partial');
    expect(discovered.providerStatuses[0].limitations).toContain('Snapshot evidence unavailable.');
    await expect(service.start('cdc-1')).rejects.toThrow('CDC source is partial');
  });

  test('fails closed without source provider adapter', async () => {
    const tasks = new TaskExecutionService(); const relationships = new RelationshipGraphService();
    const search = new FederatedSearchService(); const service = new CDCService({tasks,
      relationships, search, adapters: new CDCAdapterRegistry()});
    service.create({id: 'cdc-1', content: definition()});
    await expect(service.discover('cdc-1')).rejects.toThrow('no CDC adapter');
  });

  test('runs provision, snapshot and stream as dependent shared tasks', async () => {
    const calls = []; const {service, tasks} = serviceFixture({calls});
    service.create({id: 'cdc-1', content: definition()});
    const started = await service.start('cdc-1', {}, {currentUser: {id: 'operator'}});
    const run = started.runs[0];
    expect(run.provisionTaskId).toBeTruthy(); expect(run.snapshotTaskId).toBeTruthy();
    expect(run.streamTaskId).toBe(started.activeTaskId);
    await tasks.wait(run.streamTaskId);
    const completed = service.get('cdc-1');
    expect(completed.runs[0]).toMatchObject({state: 'streaming', lag: 0, throughput: 25,
      snapshotCheckpoint: {phase: 'snapshot', cursor: 's1'},
      streamCheckpoint: {phase: 'stream', cursor: 'c2'}});
    expect(tasks.view(run.snapshotTaskId).dependencies).toEqual([run.provisionTaskId]);
    expect(tasks.view(run.streamTaskId).dependencies).toEqual([run.snapshotTaskId]);
    expect(calls.map((item) => item.name)).toEqual(expect.arrayContaining([
      'provision', 'snapshot', 'stream', 'validateSink']));
  });

  test('skips snapshot task only when snapshot policy is none', async () => {
    const source = {...definition().source,
      snapshotPolicy: {mode: 'none', nativeDetails: {}}};
    const {service, tasks} = serviceFixture();
    service.create({id: 'cdc-1', content: definition({source})});
    const started = await service.start('cdc-1'); const run = started.runs[0];
    expect(run.snapshotTaskId).toBeNull();
    expect(tasks.view(run.streamTaskId).dependencies).toEqual([run.provisionTaskId]);
    await tasks.wait(run.streamTaskId);
  });

  test('executes pause, resume and stop through provider control tasks', async () => {
    const calls = []; const {service, tasks} = serviceFixture({calls});
    service.create({id: 'cdc-1', content: definition()});
    const started = await service.start('cdc-1'); await tasks.wait(started.activeTaskId);
    for(const [action, expected] of [['pause', 'paused'], ['resume', 'streaming'],
      ['stop', 'stopped']]) {
      const controlled = await service.control('cdc-1', action);
      await tasks.wait(controlled.activeTaskId);
      expect(service.get('cdc-1').runs[0].state).toBe(expected);
    }
    expect(calls.filter((item) => item.name === 'controlRun').map((item) => item.input.action))
      .toEqual(['pause', 'resume', 'stop']);
  });

  test('keeps authored definition after a provider task failure', async () => {
    const failing = adapter(); failing.snapshot = async () => { throw new Error('source offline'); };
    const {service, tasks} = serviceFixture({sourceAdapter: failing});
    service.create({id: 'cdc-1', content: definition()});
    const started = await service.start('cdc-1');
    await expect(tasks.wait(started.runs[0].snapshotTaskId)).rejects.toThrow('source offline');
    const failed = service.get('cdc-1');
    expect(failed.state).toBe('runtime_failure');
    expect(failed.content.name).toBe('Orders CDC');
    expect(failed.runs[0].state).toBe('failed');
  });

  test('retries a transient provider failure through the shared task policy', async () => {
    const transient = adapter(); const original = transient.snapshot; let attempts = 0;
    transient.snapshot = async (...args) => {
      attempts++;
      if(attempts === 1) throw new Error('transient snapshot failure');
      return original(...args);
    };
    const {service, tasks} = serviceFixture({sourceAdapter: transient});
    service.create({id: 'cdc-1', content: definition()});
    const started = await service.start('cdc-1', {retry: {maximum: 1}});
    await expect(tasks.wait(started.activeTaskId)).resolves.toEqual(expect.any(Object));
    expect(attempts).toBe(2);
    expect(tasks.view(started.runs[0].snapshotTaskId).retry.attempt).toBe(2);
    expect(service.get('cdc-1').content.name).toBe('Orders CDC');
  });

  test('cancels a provider task without discarding authored content', async () => {
    const blocked = adapter(); let entered; const running = new Promise((resolve) => { entered = resolve; });
    blocked.stream = async (_input, {signal}) => {
      entered();
      await new Promise((_resolve, reject) => signal.addEventListener('abort', () => {
        const error = new Error('cancelled'); error.name = 'AbortError'; reject(error);
      }, {once: true}));
    };
    const source = {...definition().source, snapshotPolicy: {mode: 'none', nativeDetails: {}}};
    const {service, tasks} = serviceFixture({sourceAdapter: blocked});
    const initial = service.create({id: 'cdc-1', content: definition({source})});
    const started = await service.start('cdc-1'); await running;
    expect(tasks.cancel(started.activeTaskId)).toBe(true);
    await expect(tasks.wait(started.activeTaskId)).rejects.toMatchObject({name: 'AbortError'});
    expect(tasks.view(started.activeTaskId).state).toBe('cancelled');
    expect(service.get('cdc-1').content).toEqual(initial.content);
  });

  test('rejects an invalid snapshot completion state', async () => {
    const invalid = adapter(); invalid.snapshot = async () => providerResult({state: 'streaming',
      lag: 0, throughput: 1, checkpoint: {cursor: 'bad'}, events: []});
    const {service, tasks} = serviceFixture({sourceAdapter: invalid});
    service.create({id: 'cdc-1', content: definition()});
    const started = await service.start('cdc-1');
    await expect(tasks.wait(started.runs[0].snapshotTaskId)).rejects.toThrow(
      'invalid snapshot completion state streaming');
    expect(service.get('cdc-1')).toMatchObject({state: 'runtime_failure',
      content: expect.objectContaining({name: 'Orders CDC'})});
  });

  test('reads and preserves provider-native checkpoints', async () => {
    const {service, tasks} = serviceFixture();
    service.create({id: 'cdc-1', content: definition()});
    const started = await service.start('cdc-1'); await tasks.wait(started.activeTaskId);
    const inspected = await service.inspectCheckpoint('cdc-1');
    expect(inspected.checkpoints.stream).toMatchObject({taskRevision: '2',
      nativeCursor: {cursor: 'c2'}});
  });

  test('redacts payloads unless the separate payload permission is present', async () => {
    const {service} = serviceFixture({events: [event()]});
    service.create({id: 'cdc-1', content: definition()});
    const denied = await service.inspectEvents('cdc-1', {limit: 10},
      {currentUser: {permissions: ['cdc.view']}});
    expect(denied.events[0].after).toBe('[REDACTED]');
    const allowed = await service.inspectEvents('cdc-1', {limit: 10,
      redactedPaths: ['after.email']},
    {currentUser: {permissions: ['cdc.view', 'cdc.view_payloads']}});
    expect(allowed.events[0].before.email).toBe('old@example.test');
    expect(allowed.events[0].after.email).toBe('[REDACTED]');
    await expect(service.inspectEvents('cdc-1', {limit: 1001})).rejects.toThrow('1 through 1000');
  });

  test('prepares and executes replay as separate tasks without rewinding run', async () => {
    const calls = []; const {service, tasks} = serviceFixture({calls});
    service.create({id: 'cdc-1', content: definition()});
    const prepared = service.prepareReplay('cdc-1', {from: '1', to: '2', target: sinkRef,
      estimatedEventCount: null, idempotencyAssessment: 'eventId dedup',
      schemaCompatibility: 'compatible', production: true, productionConfirmed: true},
    {currentUser: {id: 'operator'}});
    await tasks.wait(prepared.activeTaskId);
    const review = service.get('cdc-1').replayReview;
    expect(review.request.estimatedEventCount).toBe(12);
    const executing = service.executeReplay('cdc-1', {currentUser: {id: 'operator'}});
    await tasks.wait(executing.activeTaskId);
    expect(calls.filter((item) => item.name === 'replay').map((item) => item.input.action))
      .toEqual(['prepare', 'execute']);
    expect(service.get('cdc-1').runs).toHaveLength(0);
  });

  test('forces breaking schema resolutions to pause and review', () => {
    const change = {id: 'breaking-1', classification: 'breaking', action: 'fail',
      description: 'drop field', mapping: {}, nativeDetails: {}};
    const {service} = serviceFixture();
    service.create({id: 'cdc-1', content: definition({schemaEvolutionPolicy: {
      defaultAction: 'fail', changes: [change], nativeDetails: {}}})});
    const result = service.resolveSchemaChange('cdc-1', 'breaking-1', 'map_to_existing');
    expect(result.schemaChanges[0].action).toBe('pause_and_review');
    expect(result.dirty).toBe(true);
    expect(result.content.schemaEvolutionPolicy.changes[0].action).toBe('pause_and_review');
  });

  test('validates detected schema changes through the shared task service', async () => {
    const change = {id: 'change-1', classification: 'additive_compatible',
      action: 'pause_and_review', description: 'new column', mapping: {}, nativeDetails: {}};
    const {service, tasks} = serviceFixture();
    service.create({id: 'cdc-1', content: definition({schemaEvolutionPolicy: {
      defaultAction: 'pause_and_review', changes: [change], nativeDetails: {}}})});
    const submitted = service.validateSchemaChange('cdc-1', 'change-1',
      {currentUser: {id: 'reviewer'}});
    await tasks.wait(submitted.activeTaskId);
    expect(service.get('cdc-1').history.at(-1)).toMatchObject({
      action: 'cdc.schema_validation', changeId: 'change-1'});
  });

  test('emits configured lag threshold events from observed stream metrics', async () => {
    const events = new PlatformEventService(); const observed = [];
    events.subscribe('cdc.lag.threshold', (item) => observed.push(item));
    const streamResult = {state: 'streaming', lag: 50, throughput: 25,
      checkpoint: {cursor: 'c2'}, events: []};
    const {service, tasks} = serviceFixture({events, streamResult});
    service.create({id: 'cdc-1', content: definition({alerts: [{id: 'lag-high', event: 'lag',
      threshold: {maximum: 30}, enabled: true, nativeDetails: {}}]})});
    const started = await service.start('cdc-1'); await tasks.wait(started.activeTaskId);
    expect(observed[0].payload).toMatchObject({alertId: 'lag-high', lag: 50, maximum: 30});
  });

  test('saves with optimistic revision through project assets', async () => {
    const projectAssets = {create: jest.fn().mockResolvedValue({asset_id: 'a1', version: 1}),
      update: jest.fn().mockResolvedValue({asset_id: 'a1', version: 2})};
    const {service} = serviceFixture({projectAssets});
    service.create({id: 'cdc-1', content: definition()});
    await service.save('cdc-1', {projectId: 'p1', name: 'Orders', path: 'cdc/orders.json',
      expectedVersion: 0});
    expect(projectAssets.create.mock.calls[0][1]).toMatchObject({asset_type: 'cdeadmin.cdc.v1',
      expected_version: 0});
    await service.save('cdc-1', {projectId: 'p1', assetId: 'a1', name: 'Orders',
      path: 'cdc/orders.json', expectedVersion: 1});
    expect(projectAssets.update.mock.calls[0][2].expected_version).toBe(1);
  });

  test('contributes permission-filtered searchable assets resources runs and changes', async () => {
    const change = {id: 'change-1', classification: 'additive_compatible',
      action: 'pause_and_review', description: 'new column', mapping: {}, nativeDetails: {}};
    const {service, search} = serviceFixture();
    service.create({id: 'cdc-1', content: definition({schemaEvolutionPolicy: {
      defaultAction: 'pause_and_review', changes: [change], nativeDetails: {}}})});
    expect((await search.search('orders', {context: {permissions: []}})).total).toBe(0);
    const result = await search.search('orders', {context: {permissions: ['cdc.view']}});
    expect(result.total).toBeGreaterThanOrEqual(3);
    const command = await search.search('replay', {context: {permissions: ['cdc.view']}});
    expect(command.groups[0].results).toEqual(expect.arrayContaining([
      expect.objectContaining({type: 'cdc.command', commandId: 'cdc.replay.prepare'}),
      expect.objectContaining({type: 'cdc.command', commandId: 'cdc.replay.execute'}),
    ]));
  });

  test('rejects invalid session selection and unknown schema changes', () => {
    const {service} = serviceFixture(); service.create({id: 'cdc-1', content: definition()});
    expect(() => service.get('missing')).toThrow('Unknown CDC session');
    expect(() => service.resolveSchemaChange('cdc-1', 'missing', 'fail')).toThrow('Unknown CDC');
  });

  test('does not confuse source and sink provider identities', async () => {
    const sourceCalls = []; const sinkCalls = [];
    const {service, tasks} = serviceFixture({sourceAdapter: adapter({calls: sourceCalls}),
      sinkAdapter: adapter({calls: sinkCalls})});
    service.create({id: 'cdc-1', content: definition()});
    const started = await service.start('cdc-1'); await tasks.wait(started.activeTaskId);
    expect(sourceCalls.map((item) => item.name)).toContain('stream');
    expect(sourceCalls.map((item) => item.name)).not.toContain('validateSink');
    expect(sinkCalls.map((item) => item.name)).toContain('validateSink');
    expect(sinkCalls.map((item) => item.name)).not.toContain('stream');
    expect(sourceRef.provider).not.toBe(sinkRef.provider);
  });
});
