/////////////////////////////////////////////////////////////
// Discovery document, canonicalization and index backend gates.
/////////////////////////////////////////////////////////////

import {ResourceIdentityService} from
  'sources/cdeadmin_ui/platform/FoundationServices';
import {
  DiscoveryCanonicalizer, DiscoveryIndexBackendRegistry, DiscoveryIndexService,
  InMemoryDiscoveryIndexBackend, validateDiscoveryDocument,
} from 'sources/cdeadmin_ui/modules/discovery_intelligence';

function document(overrides={}) {
  return {schemaVersion: 1, documentId: 'document-one',
    canonicalRef: 'cde-resource://provider/connection/%2F/table/one',
    entityClass: 'LIVE_RESOURCE', nativeKind: 'table', provider: 'provider',
    connectionEnvironment: 'test', name: 'Customer', qualifiedNames: ['app.customer'],
    authorizedAliases: [], description: 'Customer master data', businessTerms: [],
    synonyms: [], domainRefs: [], ownerRefs: ['team-one'], stewardRefs: [], tags: [],
    classification: 'INTERNAL', schemaSummary: {}, nativeMetadataSummary: {},
    trustSignals: {}, qualitySignals: {}, freshnessSignals: {}, usageSignals: {},
    lineageSignals: {}, contractSignals: {}, certifications: [],
    deprecationState: null, accessState: {discoverability: 'scope:test'},
    searchText: 'Customer master data app customer', embeddingRefs: [],
    facetValues: {provider: 'provider', nativeKind: 'table'},
    updatedAt: '2026-09-12T12:00:00.000Z', sourceRevision: 'source-1',
    indexRevision: 'revision-1', ...overrides};
}

function surface(canonicalResourceRef, surfaceId, name, visibilityScope) {
  return {schemaVersion: 1, surfaceId, providerId: 'scratchbird',
    instanceId: 'instance-one', kind: surfaceId === 'native' ? 'sbsql_native' :
      'compatibility_parser', dialectId: surfaceId === 'native' ? 'sbsql' : surfaceId,
    parserProfile: surfaceId === 'native' ? null : `${surfaceId}-profile`,
    workareaSchemaRef: surfaceId === 'native' ? null : `${surfaceId}-workarea`,
    visibleQualifiedName: name, catalogProjectionRefs: [], canonicalResourceRef,
    visibilityScope, queryCapabilities: ['read'], mutationCapabilities: [],
    crossSurfaceVisibility: surfaceId === 'native' ? 'engine_authorized' : 'none',
    evidenceVersion: '1'};
}

function scratchbirdAuthorities() {
  const identities = new ResourceIdentityService({canDiscoverSurface: (item, auth) =>
    auth.scopes?.includes(item.visibilityScope)});
  const reference = identities.createScratchBird({instanceId: 'instance-one',
    canonicalUuid: 'uuid-customer', kind: 'table', nativePath: 'app/customer'});
  identities.registerAccessSurface(surface(reference.canonical, 'native',
    'app.customer', 'native'));
  identities.registerAccessSurface(surface(reference.canonical, 'postgresql',
    'public.customer', 'postgresql'));
  identities.registerAccessSurface(surface(reference.canonical, 'firebird',
    'LEGACY.CUSTOMERS_FB', 'firebird'));
  return {identities, reference};
}

function resourceItem(reference, overrides={}) {
  return {kind: 'resource', reference, document: document({canonicalRef: undefined,
    documentId: `document-${reference.nativeIdentity}`,
    provider: reference.provider, indexRevision: 'revision-1', ...overrides})};
}

describe('DiscoveryDocument', () => {
  test('validates and freezes the exact derived projection', () => {
    const value = validateDiscoveryDocument(document());
    expect(value).toMatchObject({schemaVersion: 1, entityClass: 'LIVE_RESOURCE',
      nativeKind: 'table', accessState: {discoverability: 'scope:test'}});
    expect(Object.isFrozen(value)).toBe(true);
  });

  test.each([
    [{...document(), extra: true}, /unsupported field extra/],
    [{...document(), schemaVersion: 2}, /schema version/],
    [{...document(), entityClass: 'TABLE'}, /entity class/],
    [(() => { const value = document(); delete value.trustSignals; return value; })(),
      /requires trustSignals/],
    [{...document(), accessState: {}}, /security state/],
    [{...document(), facetValues: {provider: {hidden: true}}}, /invalid value/],
    [{...document(), nativeMetadataSummary: {password: 'hidden'}}, /Raw credential/],
  ])('refuses invalid, unlabeled or secret-bearing documents', (input, error) => {
    expect(() => validateDiscoveryDocument(input)).toThrow(error);
  });
});

describe('DiscoveryCanonicalizer', () => {
  test('collapses three ScratchBird surfaces into one canonical document', () => {
    const {identities, reference} = scratchbirdAuthorities();
    const canonicalizer = new DiscoveryCanonicalizer({identities});
    const values = canonicalizer.batch([resourceItem(reference)],
      {authorization: {scopes: ['native', 'postgresql', 'firebird']}});
    expect(values).toHaveLength(1);
    expect(values[0]).toMatchObject({canonicalRef: reference.canonical,
      provider: 'scratchbird'});
    expect(values[0].authorizedAliases.map((item) => item.surfaceId)).toEqual([
      'firebird', 'native', 'postgresql']);
    expect(values[0].authorizedAliases.map((item) =>
      item.visibleQualifiedName)).toContain('public.customer');
  });

  test('does not disclose a ScratchBird alias outside indexing authorization', () => {
    const {identities, reference} = scratchbirdAuthorities();
    const value = new DiscoveryCanonicalizer({identities}).resource(
      resourceItem(reference), {authorization: {scopes: ['postgresql']}});
    expect(value.authorizedAliases).toHaveLength(1);
    expect(value.authorizedAliases[0].surfaceId).toBe('postgresql');
    expect(value.authorizedAliases.map((item) =>
      item.visibleQualifiedName)).not.toContain('LEGACY.CUSTOMERS_FB');
  });

  test('keeps same-named external provider resources distinct', () => {
    const identities = new ResourceIdentityService();
    const canonicalizer = new DiscoveryCanonicalizer({identities});
    const pg = identities.create({provider: 'postgresql', connection: 'local',
      scope: 'public', kind: 'table', nativeIdentity: 'customer'});
    const fb = identities.create({provider: 'firebird', connection: 'local',
      scope: 'APP', kind: 'table', nativeIdentity: 'CUSTOMER'});
    const values = canonicalizer.batch([resourceItem(pg, {name: 'Customer'}),
      resourceItem(fb, {name: 'Customer'})]);
    expect(new Set(values.map((item) => item.canonicalRef)).size).toBe(2);
  });

  test('refuses a supplied identity that conflicts with ResourceIdentityService', () => {
    const identities = new ResourceIdentityService();
    const reference = identities.create({provider: 'postgresql', connection: 'local',
      scope: 'public', kind: 'table', nativeIdentity: 'customer'});
    expect(() => new DiscoveryCanonicalizer({identities}).resource(
      resourceItem(reference, {canonicalRef: 'cde-resource://wrong'}))).toThrow(
      /conflicts with resource identity/);
  });

  test('requires explicit stable identity for imported catalog metadata', () => {
    const identities = new ResourceIdentityService();
    const canonicalizer = new DiscoveryCanonicalizer({identities});
    expect(() => canonicalizer.imported({document: document()})).toThrow(
      /stable resource mapping or imported entity reference/);
    const imported = canonicalizer.imported({importedEntityRef:
      'cde-asset://catalog/imported-customer', document: document({
      canonicalRef: undefined, entityClass: 'PROJECT_ASSET'})});
    expect(imported).toMatchObject({canonicalRef:
      'cde-asset://catalog/imported-customer', provider: 'provider'});
  });

  test('requires per-search authorization before an alias can match', async () => {
    const {identities, reference} = scratchbirdAuthorities();
    const candidate = new DiscoveryCanonicalizer({identities}).resource(
      resourceItem(reference), {authorization: {scopes: ['postgresql', 'firebird']}});
    const backend = new InMemoryDiscoveryIndexBackend();
    await backend.bulkRevision({revision: 'revision-1', documents: [candidate]});
    await expect(backend.lexicalSearch({text: 'public.customer',
      admit: () => true})).resolves.toEqual([]);
    await expect(backend.lexicalSearch({text: 'public.customer', admit: () => true,
      aliasAdmit: (item) => item.visibilityScope === 'postgresql'}))
      .resolves.toMatchObject([{document: {canonicalRef: reference.canonical}}]);
    await expect(backend.lexicalSearch({text: 'LEGACY.CUSTOMERS_FB', admit: () => true,
      aliasAdmit: (item) => item.visibilityScope === 'postgresql'}))
      .resolves.toEqual([]);
  });
});

describe('pluggable Discovery index and atomic publication', () => {
  let identities; let canonicalizer; let backend; let service;
  beforeEach(() => {
    identities = new ResourceIdentityService();
    canonicalizer = new DiscoveryCanonicalizer({identities});
    backend = new InMemoryDiscoveryIndexBackend({now: () =>
      '2026-09-12T13:00:00.000Z'});
    service = new DiscoveryIndexService({backend, canonicalizer});
  });

  function item(provider, id, overrides={}) {
    const reference = identities.create({provider, connection: 'local', scope: '/',
      kind: 'table', nativeIdentity: id});
    return resourceItem(reference, {documentId: `${provider}-${id}`,
      canonicalRef: undefined, provider, ...overrides});
  }

  test('rejects incomplete backend adapters at creation', () => {
    const registry = new DiscoveryIndexBackendRegistry();
    registry.register({id: 'broken', version: '1.0.0', factory: () => ({})});
    expect(() => registry.create('broken')).toThrow(/missing upsertDocument/);
  });

  test('accepts the exact snake-case backend contract through a JS facade', async () => {
    const delegate = new InMemoryDiscoveryIndexBackend();
    const registry = new DiscoveryIndexBackendRegistry();
    registry.register({id: 'contract-backend', version: '1.0.0', factory: () =>
      ({upsert_document: delegate.upsert_document.bind(delegate),
        remove_document: delegate.remove_document.bind(delegate),
        lexical_search: delegate.lexical_search.bind(delegate),
        facet_search: delegate.facet_search.bind(delegate),
        semantic_search: delegate.semantic_search.bind(delegate),
        get_document: delegate.get_document.bind(delegate),
        bulk_revision: delegate.bulk_revision.bind(delegate),
        health: delegate.health.bind(delegate)})});
    const adapter = registry.create('contract-backend');
    expect(typeof adapter.bulkRevision).toBe('function');
    await expect(adapter.health()).resolves.toMatchObject({state: 'empty'});
  });

  test('publishes a complete revision and provides real lexical/facet/vector retrieval', async () => {
    const one = item('postgresql', 'customer');
    const two = item('firebird', 'orders', {documentId: 'firebird-orders', name: 'Orders',
      searchText: 'Sales work orders', facetValues: {provider: 'firebird',
        nativeKind: 'table'}});
    const result = await service.publish({revision: 'revision-1', items: [one, two],
      embeddings: {[one.reference.canonical]: [1, 0], [two.reference.canonical]: [0, 1]}});
    expect(result).toMatchObject({published: true, documentCount: 2,
      embeddingCount: 2, previousRevision: null});
    const admitPostgres = (value) => value.provider === 'postgresql';
    await expect(service.lexicalSearch({text: 'customer', admit: admitPostgres}))
      .resolves.toMatchObject([{score: 1, document: {name: 'Customer'}}]);
    await expect(service.facetSearch({fields: ['provider'], admit: () => true}))
      .resolves.toEqual({provider: [{value: 'firebird', count: 1},
        {value: 'postgresql', count: 1}]});
    const semantic = await service.semanticSearch({vector: [0.9, 0.1],
      admit: () => true});
    expect(semantic).toHaveLength(2);
    expect(semantic[0]).toMatchObject({document: {name: 'Customer'}});
    await expect(service.getDocument(two.reference.canonical,
      {admit: admitPostgres})).rejects.toMatchObject({code: 'not_found'});
  });

  test('requires security admission on every retrieval path', async () => {
    await service.publish({revision: 'revision-1', items: [item('postgresql',
      'customer')]});
    await expect(service.lexicalSearch({text: 'customer'})).rejects.toThrow(
      /security admission/);
    await expect(service.facetSearch({fields: ['provider']})).rejects.toThrow(
      /security admission/);
    await expect(service.semanticSearch({vector: [1, 0]})).rejects.toThrow(
      /security admission/);
    await expect(service.getDocument('anything')).rejects.toThrow(/security admission/);
  });

  test('validates retrieval limits and semantic query vectors', async () => {
    await service.publish({revision: 'revision-1', items: [item('postgresql',
      'customer')]});
    await expect(service.lexicalSearch({text: 'customer', limit: 501,
      admit: () => true})).rejects.toThrow(/result limit/);
    await expect(service.facetSearch({fields: ['provider'], limit: 51,
      admit: () => true})).rejects.toThrow(/result limit/);
    await expect(service.semanticSearch({vector: [], admit: () => true}))
      .rejects.toThrow(/embedding vector/);
    await expect(service.semanticSearch({vector: [0, 0], admit: () => true}))
      .rejects.toThrow(/embedding vector/);
  });

  test('retains the previous good revision after candidate validation failure', async () => {
    const first = item('postgresql', 'customer');
    await service.publish({revision: 'revision-1', items: [first]});
    const invalid = item('firebird', 'orders', {indexRevision: 'wrong-revision'});
    await expect(service.publish({revision: 'revision-2', items: [invalid]}))
      .rejects.toThrow(/candidate index revision/);
    await expect(backend.health()).resolves.toMatchObject({activeRevision: 'revision-1',
      documentCount: 1});
    await expect(service.health()).resolves.toMatchObject({serviceState: 'degraded',
      lastFailure: {candidateRevision: 'revision-2', retainedRevision: 'revision-1'}});
  });

  test('rejects ambiguous document IDs and malformed publish options atomically', async () => {
    await service.publish({revision: 'revision-1', items: [item('postgresql',
      'customer')]});
    const collision = [item('postgresql', 'orders', {indexRevision: 'revision-2',
      documentId: 'same'}), item('firebird', 'orders', {indexRevision: 'revision-2',
      documentId: 'same'})];
    await expect(service.publish({revision: 'revision-2', items: collision}))
      .rejects.toThrow(/duplicates document ID/);
    await expect(service.publish({revision: 'revision-3', items: [], unknown: true}))
      .rejects.toThrow(/unsupported field unknown/);
    await expect(service.publish({revision: 'revision-3', items: [],
      priorDocumentCount: '1'})).rejects.toThrow(/non-negative integer/);
    await expect(backend.health()).resolves.toMatchObject({activeRevision: 'revision-1',
      documentCount: 1});
  });

  test('requires approval above the mandatory-source deletion threshold', async () => {
    await service.publish({revision: 'revision-1', items: [
      item('postgresql', 'one'), item('postgresql', 'two'),
      item('postgresql', 'three'), item('postgresql', 'four'),
      item('postgresql', 'five')]});
    const pending = await service.publish({revision: 'revision-2', items: [
      item('postgresql', 'one', {indexRevision: 'revision-2'}),
      item('postgresql', 'two', {indexRevision: 'revision-2'}),
      item('postgresql', 'three', {indexRevision: 'revision-2'})],
    mandatorySource: true});
    expect(pending).toMatchObject({published: false, approvalRequired: true,
      deletionCount: 2, deletionRatio: 0.4, activeRevision: 'revision-1'});
    await expect(backend.health()).resolves.toMatchObject({activeRevision: 'revision-1'});
  });

  test('atomically supports direct upsert and removal revisions', async () => {
    const first = item('postgresql', 'customer');
    await service.publish({revision: 'revision-1', items: [first]});
    const second = validateDiscoveryDocument({...document(), documentId: 'orders',
      canonicalRef: identities.create({provider: 'firebird', connection: 'local',
        scope: '/', kind: 'table', nativeIdentity: 'orders'}).canonical,
      provider: 'firebird', name: 'Orders', searchText: 'Orders',
      indexRevision: 'revision-2'});
    await backend.upsertDocument(second, {revision: 'revision-2'});
    await expect(backend.health()).resolves.toMatchObject({activeRevision: 'revision-2',
      documentCount: 2});
    await backend.removeDocument(first.reference.canonical, {revision: 'revision-3'});
    await expect(backend.health()).resolves.toMatchObject({activeRevision: 'revision-3',
      documentCount: 1});
    await expect(backend.removeDocument(first.reference.canonical,
      {revision: 'revision-4'})).rejects.toMatchObject({code: 'not_found'});
  });
});
