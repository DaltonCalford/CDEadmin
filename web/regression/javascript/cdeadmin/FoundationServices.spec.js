/////////////////////////////////////////////////////////////
// CDEadmin foundation platform-service tests.
/////////////////////////////////////////////////////////////

import {
  CredentialReferenceService, LayoutPersistenceService, ProviderRegistry,
  ResourceIdentityService, noRawSecrets,
} from 'sources/cdeadmin_ui/platform/PlatformServices';
import Ajv2020 from 'ajv/dist/2020';
import accessSurfaceSchema from 'sources/cdeadmin_ui/specifications/ai_discovery_zero_grey/machine/schemas/scratchbird-access-surface-ref.schema.json';

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

  it('uses one ScratchBird UUID identity across authorized access surfaces', () => {
    const identities = new ResourceIdentityService({
      canDiscoverSurface: (surface, authorization) =>
        authorization.surfaceIds?.includes(surface.surfaceId),
    });
    const resource = identities.createScratchBird({
      instanceId: 'instance-a', canonicalUuid: '0199-example', kind: 'table',
      nativePath: 'company.sales.customer', revision: '8',
    });
    expect(resource.canonical).toBe(
      'scratchbird://instance-a/uuid/0199-example'
    );
    expect(identities.same(resource, identities.parse(resource.canonical))).toBe(true);
    const surface = (input) => ({
      schemaVersion: 1, providerId: 'scratchbird', instanceId: 'instance-a',
      canonicalResourceRef: resource.canonical, catalogProjectionRefs: [],
      queryCapabilities: ['read'], mutationCapabilities: [],
      visibilityScope: 'test', evidenceVersion: '1', ...input,
    });
    const removeNative = identities.registerAccessSurface(surface({
      surfaceId: 'sb-native', kind: 'sbsql_native', dialectId: 'sbsql',
      visibleQualifiedName: 'company.sales.customer',
      crossSurfaceVisibility: 'engine_authorized',
    }));
    identities.registerAccessSurface(surface({
      surfaceId: 'pg-compat', kind: 'compatibility_parser',
      dialectId: 'postgresql', parserProfile: 'postgresql-18',
      workareaSchemaRef: 'emulated/postgresql',
      visibleQualifiedName: 'public.customer', crossSurfaceVisibility: 'none',
    }));
    identities.registerAccessSurface(surface({
      surfaceId: 'mysql-compat', kind: 'compatibility_parser',
      dialectId: 'mysql', parserProfile: 'mysql-9',
      workareaSchemaRef: 'emulated/mysql',
      visibleQualifiedName: 'sales.customer', crossSurfaceVisibility: 'none',
    }));
    expect(identities.accessSurfaces(resource, {
      surfaceIds: ['pg-compat'],
    }).map((item) => item.surfaceId)).toEqual(['pg-compat']);
    expect(identities.authorizedAliases(resource, {
      surfaceIds: ['pg-compat'],
    })).toEqual([expect.objectContaining({
      visibleQualifiedName: 'public.customer',
      crossSurfaceVisibility: 'none',
    })]);
    expect(() => identities.accessSurface('mysql-compat', {
      surfaceIds: ['pg-compat'],
    })).toThrow('not discoverable');
    expect(identities.accessSurface('pg-compat', {
      surfaceIds: ['pg-compat'],
    }).dialectId).toBe('postgresql');
    expect(removeNative()).toBe(true);
    expect(() => identities.accessSurface('sb-native')).toThrow(
      'Unknown access surface'
    );
  });

  it('validates access surfaces and forbids compatibility cross-root authority', () => {
    const identities = new ResourceIdentityService();
    const base = {
      schemaVersion: 1, surfaceId: 'pg-compat', providerId: 'scratchbird',
      instanceId: 'instance-a', kind: 'compatibility_parser',
      dialectId: 'postgresql', parserProfile: 'postgresql-18',
      workareaSchemaRef: 'emulated/postgresql',
      visibleQualifiedName: 'public.customer', catalogProjectionRefs: [],
      canonicalResourceRef: 'scratchbird://instance-a/uuid/0199-example',
      visibilityScope: 'project-a', queryCapabilities: ['read'],
      mutationCapabilities: [], crossSurfaceVisibility: 'none',
      evidenceVersion: '1',
    };
    const created = identities.createAccessSurface(base);
    const validate = new Ajv2020({strict: false}).compile(accessSurfaceSchema);
    expect(validate(created)).toBe(true);
    expect(() => identities.createAccessSurface({
      ...base, crossSurfaceVisibility: 'engine_authorized',
    })).toThrow('Only ScratchBird native SBsql');
    expect(() => identities.createAccessSurface({
      ...base, workareaSchemaRef: null,
    })).toThrow('require parser profile and sandboxed workarea');
    expect(() => identities.createAccessSurface({
      ...base, providerId: 'provider.postgresql',
    })).toThrow('must be scratchbird');
    expect(() => identities.createAccessSurface({
      ...base, canonicalResourceRef: 'cde-resource://external/postgresql',
    })).toThrow('canonical ScratchBird resource');
    expect(() => identities.createAccessSurface({...base, guessed: true}))
      .toThrow('unsupported field guessed');
    identities.registerAccessSurface(base);
    expect(() => identities.registerAccessSurface(base)).toThrow(
      'already registered'
    );
  });

  it('never merges an external provider object with a ScratchBird alias by name', () => {
    const identities = new ResourceIdentityService();
    const scratchBird = identities.createScratchBird({
      instanceId: 'instance-a', canonicalUuid: '0199-example', kind: 'table',
      nativePath: 'company.sales.customer',
    });
    const external = identities.create({
      provider: 'provider.postgresql', connection: 'external:5432',
      scope: 'public', kind: 'table', nativeIdentity: 'customer',
    });
    expect(identities.same(scratchBird, external)).toBe(false);
    expect(() => identities.create({
      provider: 'scratchbird', connection: 'instance-a', scope: '/',
      kind: 'table', nativeIdentity: '0199-example',
    })).toThrow('durable UUID-backed identity');
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
