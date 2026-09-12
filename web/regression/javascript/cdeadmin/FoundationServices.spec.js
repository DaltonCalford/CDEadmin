/////////////////////////////////////////////////////////////
// CDEadmin foundation platform-service tests.
/////////////////////////////////////////////////////////////

import {
  CredentialReferenceService, LayoutPersistenceService, ProviderRegistry,
  ResourceIdentityService, noRawSecrets,
} from 'sources/cdeadmin_ui/platform/PlatformServices';

function storage() {
  const values = new Map();
  return {getItem: jest.fn((key) => values.get(key) ?? null),
    setItem: jest.fn((key, value) => values.set(key, value)),
    removeItem: jest.fn((key) => values.delete(key)), values};
}

describe('ProviderRegistry', () => {
  it('registers, filters, runs lifecycle and removes exact definitions', async () => {
    const lifecycle = {start: jest.fn(), stop: jest.fn()};
    const registry = new ProviderRegistry();
    const remove = registry.register({id: 'provider.mongo', title: 'MongoDB',
      family: 'document', version: '8.2.6', modelTypes: ['document'],
      connectionTypes: ['direct', 'replica-set'], available: true, installed: true,
      attribution: {license: 'SSPL'}, lifecycle});
    expect(registry.get('provider.mongo').connectionTypes).toEqual(['direct', 'replica-set']);
    expect(registry.list({family: 'document', available: true})).toHaveLength(1);
    expect(registry.list({modelType: 'graph'})).toEqual([]);
    await registry.start('provider.mongo', {reason: 'test'});
    await registry.stop('provider.mongo');
    expect(lifecycle.start).toHaveBeenCalledWith({reason: 'test'});
    expect(lifecycle.stop).toHaveBeenCalled(); expect(remove()).toBe(true);
  });

  it('rejects missing native models, duplicates and unknown providers', () => {
    const registry = new ProviderRegistry();
    expect(() => registry.register({id: 'provider.empty', modelTypes: []}))
      .toThrow('must declare native model types');
    registry.register({id: 'provider.redis', modelTypes: ['key-value']});
    expect(() => registry.register({id: 'provider.redis', modelTypes: ['key-value']}))
      .toThrow('already registered');
    expect(() => registry.get('provider.missing')).toThrow('Unknown provider');
  });
});

describe('ResourceIdentityService', () => {
  it('round-trips native identity without losing provider-specific characters', () => {
    const identities = new ResourceIdentityService();
    const reference = identities.create({provider: 'provider.firebird',
      connection: 'localhost:3050', scope: '/var/lib/firebird/data/demo.fdb',
      kind: 'object.table', nativeIdentity: 'APP.WORK ORDER', revision: '42'});
    const parsed = identities.parse(reference.canonical);
    expect(parsed).toEqual(expect.objectContaining({provider: reference.provider,
      connection: reference.connection, scope: reference.scope,
      kind: reference.kind, nativeIdentity: reference.nativeIdentity}));
    expect(identities.same(reference, parsed)).toBe(true);
    expect(identities.same(reference, parsed, {includeRevision: true})).toBe(false);
  });

  it('rejects incomplete and malformed identities', () => {
    const identities = new ResourceIdentityService();
    expect(() => identities.create({provider: 'bad provider'})).toThrow();
    expect(() => identities.parse('https://not-a-resource')).toThrow();
  });
});

describe('CredentialReferenceService', () => {
  it('resolves authorized handles and does not embed a raw secret', async () => {
    const service = new CredentialReferenceService({authorize: (_ref, auth) => auth.ok});
    service.register('keyring', async (id) => ({username: id, password: 'runtime-only'}));
    const reference = service.reference('keyring', 'firebird-local');
    expect(JSON.stringify(reference)).not.toContain('runtime-only');
    await expect(service.resolve(reference, {ok: false})).rejects.toThrow('not authorized');
    await expect(service.resolve(reference, {ok: true})).resolves.toEqual({
      username: 'firebird-local', password: 'runtime-only',
    });
  });

  it('rejects duplicate/missing resolvers and recursive raw credentials', async () => {
    const service = new CredentialReferenceService();
    const remove = service.register('keyring', async () => 'secret');
    expect(() => service.register('keyring', async () => 'other')).toThrow('already registered');
    expect(remove()).toBe(true);
    await expect(service.resolve(service.reference('keyring', 'missing')))
      .rejects.toThrow('resolver unavailable');
    expect(() => noRawSecrets({nested: {accessToken: 'unsafe'}})).toThrow('Raw credential');
    expect(() => noRawSecrets({credentialRef: {scheme: 'keyring'}})).not.toThrow();
  });
});

describe('LayoutPersistenceService', () => {
  it('stores device-local layouts, migrates sequentially and removes them', () => {
    const local = storage(); const service = new LayoutPersistenceService(local);
    service.registerMigration('workbench.main', 1, (value) => ({...value, inspector: true}));
    service.registerMigration('workbench.main', 2, (value) => ({...value, drawer: false}));
    service.save('workbench.main', 'device-user', 1, {width: 200});
    expect(service.load('workbench.main', 'device-user', 3, null)).toEqual({
      width: 200, inspector: true, drawer: false,
    });
    service.remove('workbench.main', 'device-user');
    expect(service.load('workbench.main', 'device-user', 3, 'fallback')).toBe('fallback');
  });

  it('falls back for malformed/future/unmigratable layouts and rejects secrets', () => {
    const local = storage(); const service = new LayoutPersistenceService(local);
    local.values.set('cdeadmin.layout.workbench.main.owner', '{bad');
    expect(service.load('workbench.main', 'owner', 1, 'fallback')).toBe('fallback');
    service.save('workbench.main', 'owner', 3, {future: true});
    expect(service.load('workbench.main', 'owner', 2, 'fallback')).toBe('fallback');
    service.save('workbench.main', 'owner', 1, {old: true});
    expect(service.load('workbench.main', 'owner', 2, 'fallback')).toBe('fallback');
    expect(() => service.save('workbench.main', 'owner', 1, {apiToken: 'unsafe'}))
      .toThrow('Raw credential');
    service.registerMigration('workbench.main', 1, (value) => value);
    expect(() => service.registerMigration('workbench.main', 1, (value) => value))
      .toThrow('already exists');
  });
});
