/////////////////////////////////////////////////////////////
// CDEadmin AI connector runtime tests.
/////////////////////////////////////////////////////////////

import Ajv2020 from 'ajv/dist/2020';
import connectorProfileSchema from 'sources/cdeadmin_ui/specifications/ai_discovery_zero_grey/machine/schemas/ai-connector-profile.schema.json';
import {
  AI_CONNECTOR_CLASSES, AIConnectorAdapterRegistry, AIConnectorService,
  createAIConnectorProfile,
} from 'sources/cdeadmin_ui/modules/ai_interface';

const healthDimensions = {
  transport: 'ready', authentication: 'ready', authorization: 'ready',
  capability_discovery: 'ready', version_compatibility: 'ready',
  task_execution: 'ready', database_session_pool: 'ready',
};

function profile(connectorClass='database_provider', changes={}) {
  const id = changes.connectorId ?? `connector-${connectorClass.replaceAll('_', '-')}`;
  const value = {
    schemaVersion: 1, connectorId: id, name: `Test ${connectorClass}`,
    connectorClass, enabled: true, providerId: null,
    connectionProfileRef: null, dialectId: null, workareaSchemaRef: null,
    mcpEndpointRef: null, mcpProtocolProfile: null,
    principalBinding: `principal-${id}`, credentialRef: null,
    policyRef: 'asset:ai-policy/default', resourceScopeRefs: ['resource:test'],
    state: 'unconfigured', capabilitySnapshotRef: null,
  };
  if(['database_provider', 'scratchbird_sbsql',
    'scratchbird_compatibility'].includes(connectorClass)) {
    Object.assign(value, {providerId: `provider.${connectorClass}`,
      connectionProfileRef: `connection:${id}`, credentialRef: `keyring:${id}`});
  }
  if(connectorClass === 'scratchbird_sbsql') value.dialectId = 'sbsql';
  if(connectorClass === 'scratchbird_compatibility') Object.assign(value,
    {dialectId: 'postgresql', workareaSchemaRef: 'scratchbird:workarea/postgresql'});
  if(['scratchbird_mcp', 'mcp_generic'].includes(connectorClass)) Object.assign(value,
    {mcpEndpointRef: `mcp:${id}`, mcpProtocolProfile: 'mcp:2026-06'});
  return {...value, ...changes};
}

function adapter(profileValue, changes={}) {
  return {
    describe: jest.fn(async () => ({name: profileValue.name, protocol: 'test'})),
    validateConfiguration: jest.fn(async () => ({valid: true, errors: []})),
    authenticateOrResolveIdentity: jest.fn(async () => ({
      principalId: profileValue.principalBinding, sessionId: `session-${profileValue.connectorId}`,
      borrowedInteractiveSession: false,
    })),
    discoverCapabilities: jest.fn(async () => ({
      snapshotRef: `snapshot:${profileValue.connectorId}`, operations: ['read'],
    })),
    test: jest.fn(async () => ({success: true, category: 'ok', checks: ['transport']})),
    prepare: jest.fn(async (operation) => ({preparedId: `prepared-${operation.operationId}`,
      sideEffects: false, operation})),
    execute: jest.fn(async (operation) => ({success: true, preparedId: operation.preparedId})),
    cancel: jest.fn(async (reference) => ({success: true, reference})),
    health: jest.fn(async () => ({dimensions: healthDimensions})),
    close: jest.fn(async () => undefined),
    ...changes,
  };
}

function runtime(classes=AI_CONNECTOR_CLASSES, adapterChanges=()=>({})) {
  const adapters = new AIConnectorAdapterRegistry();
  const instances = [];
  for(const connectorClass of classes) adapters.register({connectorClass, version: '1.0.0',
    factory: ({profile: value, connectorOwner}) => {
      const instance = adapter(value, adapterChanges(value, connectorOwner));
      instances.push({value, connectorOwner, instance}); return instance;
    }});
  const credentialResolver = jest.fn(async (reference) => ({reference, password: 'runtime-only'}));
  return {adapters, instances, credentialResolver, service: new AIConnectorService({
    adapters, credentialResolver, now: () => '2026-09-12T12:00:00.000Z',
  })};
}

describe('AI connector profile contract', () => {
  it('creates schema-valid profiles for every exact connector class', () => {
    const validate = new Ajv2020({strict: false}).compile(connectorProfileSchema);
    expect(AI_CONNECTOR_CLASSES).toEqual(['cdeadmin_capability', 'database_provider',
      'scratchbird_sbsql', 'scratchbird_compatibility', 'scratchbird_mcp',
      'mcp_generic', 'external_service']);
    for(const connectorClass of AI_CONNECTOR_CLASSES) {
      const created = createAIConnectorProfile(profile(connectorClass));
      expect(validate(created)).toBe(true);
    }
  });

  it('rejects invented fields, raw secrets, duplicates and incomplete class contracts', () => {
    expect(() => createAIConnectorProfile({...profile(), invented: true})).toThrow('unsupported field');
    expect(() => createAIConnectorProfile({...profile(), password: 'unsafe'})).toThrow('Raw credential');
    expect(() => createAIConnectorProfile(profile('database_provider', {credentialRef: null})))
      .toThrow('require provider, connection and credential');
    expect(() => createAIConnectorProfile(profile('scratchbird_sbsql', {dialectId: 'postgresql'})))
      .toThrow('require the SBsql dialect');
    expect(() => createAIConnectorProfile(profile('scratchbird_compatibility',
      {workareaSchemaRef: null}))).toThrow('require dialect and sandboxed workarea');
    expect(() => createAIConnectorProfile(profile('mcp_generic', {mcpEndpointRef: null})))
      .toThrow('require endpoint and protocol');
    expect(() => createAIConnectorProfile(profile('external_service',
      {resourceScopeRefs: ['resource:a', 'resource:a']}))).toThrow('contains duplicates');
  });
});

describe('AIConnectorAdapterRegistry', () => {
  it('advertises only installed adapters and enforces one complete adapter per class', () => {
    const adapters = new AIConnectorAdapterRegistry();
    const remove = adapters.register({connectorClass: 'external_service', version: '1',
      factory: (input) => adapter(input.profile)});
    expect(adapters.has('external_service')).toBe(true);
    expect(adapters.list().map((item) => item.connectorClass)).toEqual(['external_service']);
    expect(() => adapters.register({connectorClass: 'external_service', version: '2',
      factory: () => ({})})).toThrow('already registered');
    const incomplete = new AIConnectorAdapterRegistry();
    incomplete.register({connectorClass: 'mcp_generic', version: '1', factory: () => ({})});
    expect(() => incomplete.create(createAIConnectorProfile(profile('mcp_generic'))))
      .toThrow('requires describe()');
    expect(remove()).toBe(true); expect(adapters.list()).toEqual([]);
  });
});

describe('AIConnectorService', () => {
  it('authenticates dedicated sessions and reaches ready for all seven connector classes', async () => {
    const testRuntime = runtime();
    for(const connectorClass of AI_CONNECTOR_CLASSES) {
      const value = profile(connectorClass); testRuntime.service.create(value);
      const result = await testRuntime.service.enable(value.connectorId,
        {interactiveSessionId: 'human-session'});
      expect(result.state).toBe('ready'); expect(result.health.ready).toBe(true);
      expect(result.descriptor.adapterVersion).toBe('1.0.0');
      expect(result.identity.principalId).toBe(value.principalBinding);
    }
    expect(testRuntime.instances).toHaveLength(7);
    for(const item of testRuntime.instances) {
      expect(item.connectorOwner).toBe(`ai-connector:${item.value.connectorId}`);
      expect(item.instance.test).toHaveBeenCalledWith({destructive: false});
    }
    expect(testRuntime.credentialResolver).toHaveBeenCalledTimes(3);
    await testRuntime.service.close();
    testRuntime.instances.forEach(({instance}) => expect(instance.close).toHaveBeenCalledTimes(1));
  });

  it('supports test, prepare, execute, cancel, refresh, update, disable and revoke', async () => {
    const testRuntime = runtime(['database_provider']);
    const original = profile('database_provider', {connectorId: 'database-main', enabled: false,
      state: 'disabled'});
    testRuntime.service.create(original);
    const tested = await testRuntime.service.test('database-main');
    expect(tested.state).toBe('disabled'); expect(tested.profile.enabled).toBe(false);
    expect(tested.testResult.state).toBe('ready');
    await testRuntime.service.enable('database-main');
    const prepared = await testRuntime.service.prepare('database-main',
      {operationId: 'inspect-catalog', action: 'read'});
    expect(prepared.connectorRevision).toBe(1);
    await expect(testRuntime.service.execute('database-main', prepared.preparedId,
      {approvalId: 'approval-1'})).resolves.toEqual(expect.objectContaining({success: true}));
    await expect(testRuntime.service.cancel('database-main', 'running-1')).resolves.toEqual(
      {success: true, reference: 'running-1'});
    const stale = await testRuntime.service.prepare('database-main',
      {operationId: 'stale-operation', action: 'read'});
    const refreshed = await testRuntime.service.refreshCapabilities('database-main');
    expect(refreshed.revision).toBe(2); expect(refreshed.preparedCount).toBe(0);
    expect(refreshed.profile.capabilitySnapshotRef).toBe('snapshot:database-main');
    await expect(testRuntime.service.execute('database-main', stale.preparedId))
      .rejects.toThrow('Unknown prepared operation');
    const updatedInput = profile('database_provider', {connectorId: 'database-main',
      name: 'Updated connector'});
    const updated = await testRuntime.service.update('database-main', updatedInput);
    expect(updated.revision).toBe(3); expect(updated.state).toBe('ready');
    expect(updated.profile.name).toBe('Updated connector');
    expect(testRuntime.instances[1].instance.close).toHaveBeenCalledTimes(1);
    const disabled = await testRuntime.service.disable('database-main');
    expect(disabled.state).toBe('disabled'); expect(disabled.profile.enabled).toBe(false);
    const revoked = await testRuntime.service.revoke('database-main');
    expect(revoked.state).toBe('revoked');
    await expect(testRuntime.service.enable('database-main')).rejects.toThrow('cannot be enabled');
    await expect(testRuntime.service.update('database-main', updatedInput)).rejects.toThrow(
      'cannot be updated');
  });

  it('keeps unavailable adapters unconfigured and rejects duplicate or unknown profiles', async () => {
    const service = new AIConnectorService(); const value = profile('external_service');
    service.create(value);
    await expect(service.enable(value.connectorId)).resolves.toEqual(expect.objectContaining({
      state: 'unconfigured', diagnostics: ['No external_service adapter is installed.'],
    }));
    expect(() => service.create(value)).toThrow('already exists');
    expect(() => service.get('missing')).toThrow('Unknown AI connector');
  });

  it.each([
    ['wrong principal', () => ({principalId: 'wrong', sessionId: 'session-safe'}), undefined],
    ['borrowed session', (value) => ({principalId: value.principalBinding,
      sessionId: 'human-session', borrowedInteractiveSession: true}), 'human-session'],
  ])('fails closed for %s', async (_label, identity, interactiveSessionId) => {
    const testRuntime = runtime(['external_service'], (value) => ({
      authenticateOrResolveIdentity: jest.fn(async () => identity(value)),
    }));
    const value = profile('external_service'); testRuntime.service.create(value);
    const result = await testRuntime.service.enable(value.connectorId, {interactiveSessionId});
    expect(result.state).toBe('permission_failed'); expect(result.identity).toBeNull();
    expect(testRuntime.instances[0].instance.close).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['invalid configuration', {validateConfiguration: jest.fn(async () => ({
      valid: false, errors: ['endpoint missing']}))}, 'error'],
    ['incompatible version', {test: jest.fn(async () => ({
      success: false, category: 'version', message: 'version mismatch'}))}, 'version_incompatible'],
    ['unreachable endpoint', {test: jest.fn(async () => ({
      success: false, category: 'transport', message: 'offline'}))}, 'unreachable'],
    ['unsafe adapter output', {validateConfiguration: jest.fn(async () => ({
      valid: true, password: 'leaked'}))}, 'error'],
  ])('maps %s to its exact state', async (_label, changes, state) => {
    const testRuntime = runtime(['external_service'], () => changes);
    const value = profile('external_service'); testRuntime.service.create(value);
    const result = await testRuntime.service.enable(value.connectorId);
    expect(result.state).toBe(state); expect(result.diagnostics).toHaveLength(1);
    expect(testRuntime.instances[0].instance.close).toHaveBeenCalledTimes(1);
  });

  it('reports degraded health and rejects unsafe or side-effecting preparation', async () => {
    const degraded = {...healthDimensions, capability_discovery: 'unknown'};
    const testRuntime = runtime(['external_service'], () => ({
      health: jest.fn(async () => ({dimensions: degraded})),
    }));
    const value = profile('external_service'); testRuntime.service.create(value);
    expect((await testRuntime.service.enable(value.connectorId)).state).toBe('degraded');
    expect((await testRuntime.service.health(value.connectorId)).ready).toBe(false);
    await expect(testRuntime.service.prepare(value.connectorId,
      {operationId: 'unsafe', password: 'raw'})).rejects.toThrow('Raw credential');
    testRuntime.instances[0].instance.prepare.mockResolvedValueOnce({
      preparedId: 'unsafe-side-effect', sideEffects: true,
    });
    await expect(testRuntime.service.prepare(value.connectorId,
      {operationId: 'unsafe', action: 'write'})).rejects.toThrow('side-effect free');
  });

  it('rejects connector ID changes and leaves a disabled update disconnected', async () => {
    const testRuntime = runtime(['external_service']);
    const value = profile('external_service'); testRuntime.service.create(value);
    await testRuntime.service.enable(value.connectorId);
    await expect(testRuntime.service.update(value.connectorId,
      profile('external_service', {connectorId: 'different'}))).rejects.toThrow('cannot be changed');
    const updated = await testRuntime.service.update(value.connectorId,
      {...value, enabled: false, state: 'disabled', name: 'Disabled update'});
    expect(updated).toEqual(expect.objectContaining({state: 'disabled', revision: 2}));
    expect(updated.profile.enabled).toBe(false); expect(updated.identity).toBeNull();
  });
});
