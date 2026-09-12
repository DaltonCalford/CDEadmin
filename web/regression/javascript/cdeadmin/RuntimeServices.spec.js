/////////////////////////////////////////////////////////////
// CDEadmin runtime platform-service tests.
/////////////////////////////////////////////////////////////

import {
  ConnectionSessionService, MetadataCatalogService, NativeOperationService,
  SESSION_STATES,
} from 'sources/cdeadmin_ui/platform/PlatformServices';

function deferred() {
  let resolve; const promise = new Promise((done) => { resolve = done; });
  return {promise, resolve};
}

describe('ConnectionSessionService', () => {
  it('manages profile, credentials, session context, transaction state and disconnect', async () => {
    const credentials = {resolve: jest.fn(async () => ({password: 'runtime'}))};
    const adapter = {connect: jest.fn(async (_profile, options) => ({
      handle: {socket: 1}, details: {serverVersion: '5.0.4'},
      warning: options.credential.password ? '' : 'missing'})), disconnect: jest.fn()};
    const service = new ConnectionSessionService({credentialService: credentials});
    service.registerAdapter('provider.firebird', adapter);
    const profile = service.saveProfile({id: 'fb-local', provider: 'provider.firebird',
      endpoints: [{host: '127.0.0.1', port: 3050}],
      credentialRef: {schema: 'cdeadmin.credential-ref.v1', scheme: 'keyring', id: 'fb'},
      defaults: {database: '/data/demo.fdb'}, environment: 'development'});
    const events = []; service.subscribe((event) => events.push(event.state));
    const connected = await service.connect(profile.id, {authorization: {use: true}});
    expect(connected.state).toBe(SESSION_STATES.CONNECTED);
    expect(connected.native.serverVersion).toBe('5.0.4');
    expect(events).toEqual(['connecting', 'connected']);
    expect(service.listProfiles({provider: 'provider.firebird'})).toHaveLength(1);
    expect(service.listSessions({state: 'connected'})).toHaveLength(1);
    expect(service.updateContext(connected.id, {schema: 'APP'}).context.schema).toBe('APP');
    expect(service.updateTransaction(connected.id, {state: 'active', id: 'tx-2'})
      .transaction).toEqual(expect.objectContaining({state: 'active', id: 'tx-2'}));
    await service.disconnect(connected.id);
    expect(service.session(connected.id).state).toBe(SESSION_STATES.DISCONNECTED);
    expect(adapter.disconnect).toHaveBeenCalledWith({socket: 1}, expect.anything());
    expect(service.removeProfile(profile.id)).toBe(true);
  });

  it('records failures, prevents raw secrets and refuses active profile removal', async () => {
    const service = new ConnectionSessionService();
    expect(() => service.saveProfile({id: 'bad', provider: 'provider.x',
      endpoints: [{}], password: 'unsafe'})).toThrow('Raw credential');
    service.registerAdapter('provider.x', {connect: async () => { throw new Error('offline'); }});
    service.saveProfile({id: 'x', provider: 'provider.x', endpoints: [{}]});
    await expect(service.connect('x', {sessionId: 'known'})).rejects.toThrow('offline');
    expect(service.session('known')).toEqual(expect.objectContaining({
      state: SESSION_STATES.FAILED, warning: 'offline'}));
    service.registerAdapter('provider.y', {connect: async () => ({handle: 1})});
    service.saveProfile({id: 'y', provider: 'provider.y', endpoints: [{}]});
    await service.connect('y');
    expect(() => service.removeProfile('y')).toThrow('Profile is in use');
    expect(() => service.profile('missing')).toThrow('Unknown profile');
  });

  it('reconnects using the original stable session identity', async () => {
    let generation = 0; const disconnect = jest.fn();
    const service = new ConnectionSessionService();
    service.registerAdapter('provider.duckdb', {connect: async () => ({handle: ++generation}),
      disconnect});
    service.saveProfile({id: 'duck', provider: 'provider.duckdb', endpoints: [{path: 'x'}]});
    const session = await service.connect('duck', {sessionId: 'session-duck'});
    const next = await service.reconnect(session.id);
    expect(next.id).toBe(session.id); expect(next.handle).toBe(2);
    expect(disconnect).toHaveBeenCalled();
  });
});

describe('NativeOperationService', () => {
  it.each(['tabular', 'document', 'graph', 'scalar', 'binary-reference',
    'trace', 'plan', 'metric', 'provider-native'])(
    'executes %s without coercing it to a table', async (kind) => {
      const service = new NativeOperationService();
      service.register('provider.test', 'query.native', async () => ({kind, data: kind}));
      const value = await service.execute({provider: 'provider.test', type: 'query.native'});
      expect(value.result).toEqual(expect.objectContaining({kind, data: kind}));
    }
  );

  it('streams chunks in order, reports them and exposes final state', async () => {
    const chunks = []; const service = new NativeOperationService();
    service.register('provider.mongo', 'query.document', async function* () {
      yield {kind: 'document', data: [{_id: 1}]};
      yield {kind: 'document', data: [{_id: 2}]};
    });
    const value = await service.execute({id: 'op-doc', provider: 'provider.mongo',
      type: 'query.document'}, {onChunk: (chunk) => chunks.push(chunk.data[0]._id)});
    expect(chunks).toEqual([1, 2]); expect(value.result.chunks).toHaveLength(2);
    expect(service.state('op-doc').state).toBe('completed');
  });

  it('rejects invalid results, unsupported operations and raw secrets', async () => {
    const service = new NativeOperationService();
    service.register('provider.bad', 'query.bad', async () => ({kind: 'spreadsheet'}));
    await expect(service.execute({provider: 'provider.bad', type: 'query.bad'}))
      .rejects.toThrow('Operation result kind');
    await expect(service.execute({provider: 'provider.bad', type: 'query.missing'}))
      .rejects.toThrow('does not provide');
    await expect(service.execute({provider: 'provider.bad', type: 'query.bad', apiToken: 'x'}))
      .rejects.toThrow('Raw credential');
  });

  it('cancels an active streaming operation', async () => {
    const gate = deferred(); const service = new NativeOperationService();
    service.register('provider.slow', 'query.native', async function* () {
      yield {kind: 'scalar', data: 1}; await gate.promise;
      yield {kind: 'scalar', data: 2};
    });
    const run = service.execute({id: 'op-slow', provider: 'provider.slow',
      type: 'query.native'}, {onChunk: () => service.cancel('op-slow')});
    gate.resolve(); await expect(run).rejects.toMatchObject({name: 'AbortError'});
    expect(service.state('op-slow').state).toBe('cancelled');
  });
});

describe('MetadataCatalogService', () => {
  it('preserves normalized/native metadata with cache, refresh and invalidation', async () => {
    let calls = 0; const service = new MetadataCatalogService();
    service.register('provider.neo4j', {read: async () => ({
      normalized: {kind: 'graph'}, native: {labels: ['Person'], procedures: ['db.info']},
      version: String(++calls), provenance: {source: 'SHOW'}})});
    const reference = {provider: 'provider.neo4j', canonical: 'ref:graph', revision: null};
    expect((await service.read(reference)).version).toBe('1');
    expect((await service.read(reference)).version).toBe('1');
    const refreshed = await service.read(reference, {refresh: true});
    expect(refreshed.version).toBe('2'); expect(refreshed.native.labels).toEqual(['Person']);
    expect(service.invalidate(reference)).toBe(1); expect(service.invalidate()).toBe(0);
  });

  it('rejects flattened metadata and missing adapters', async () => {
    const service = new MetadataCatalogService();
    service.register('provider.bad', {read: async () => ({normalized: {}})});
    await expect(service.read({provider: 'provider.bad', canonical: 'ref:bad'}))
      .rejects.toThrow('normalized and native views');
    await expect(service.read({provider: 'provider.none', canonical: 'ref:none'}))
      .rejects.toThrow('No metadata adapter');
  });
});
