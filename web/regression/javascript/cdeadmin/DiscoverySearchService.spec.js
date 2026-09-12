/////////////////////////////////////////////////////////////
// Security-first Discovery search and ranking release gates.
/////////////////////////////////////////////////////////////

import {
  BALANCED_DISCOVERY_RANKING_PROFILE,
  DiscoveryQuerySyntaxError, DiscoveryRankingEngine, DiscoverySearchService,
  InMemoryDiscoveryCursorAuthority, InMemoryDiscoveryGraphIndex,
  InMemoryDiscoveryIndexBackend, parseDiscoverySearchText,
  validateDiscoveryRankingProfile, validateDiscoverySearchQuery,
} from 'sources/cdeadmin_ui/modules/discovery_intelligence';

function document(id, overrides={}) {
  return {schemaVersion: 1, documentId: `document-${id}`,
    canonicalRef: `cde-resource://test/local/scope/table/${id}`,
    entityClass: 'LIVE_RESOURCE', nativeKind: 'table', provider: 'firebird',
    connectionEnvironment: 'test', name: id, qualifiedNames: [`APP.${id}`],
    authorizedAliases: [], description: `${id} description`, businessTerms: [],
    synonyms: [], domainRefs: ['operations'], ownerRefs: ['team-one'],
    stewardRefs: ['steward-one'], tags: ['governed'], classification: 'INTERNAL',
    schemaSummary: {}, nativeMetadataSummary: {}, trustSignals: {},
    qualitySignals: {}, freshnessSignals: {}, usageSignals: {}, lineageSignals: {},
    contractSignals: {}, certifications: [], deprecationState: null,
    accessState: {state: 'AVAILABLE'}, searchText: `${id} description`,
    embeddingRefs: [], facetValues: {provider: 'firebird', nativeKind: 'table',
      domain: 'operations', certification: 'CERTIFIED', quality: 'PASS',
      freshness: 'CURRENT', access: 'AVAILABLE'},
    updatedAt: '2026-09-12T12:00:00.000Z', sourceRevision: 'source-1',
    indexRevision: 'revision-1', ...overrides};
}

function alias(surfaceId, name, scope=surfaceId) {
  return {surfaceId, kind: 'compatibility_parser', dialectId: surfaceId,
    visibleQualifiedName: name, workareaSchemaRef: `${surfaceId}-workarea`,
    visibilityScope: scope, queryCapabilities: ['read'], mutationCapabilities: [],
    crossSurfaceVisibility: 'none', evidenceVersion: '1'};
}

function security({hidden=[], aliases=[], metadata=true, signals=true,
  facets=true, canonical=true, edges=true}={}) {
  return {admitDocument: (item, dimension) => !hidden.includes(item.name) &&
      (dimension !== 'DISCOVER_METADATA' || metadata),
  admitAlias: (item) => aliases.includes(item.visibilityScope),
  admitSignal: () => signals,
  admitFacet: () => facets,
  admitGraphEdge: () => edges,
  exposeCanonicalRef: () => canonical};
}

function featureAuthority(overrides={}) {
  return {evaluate: (item, {security: policy}) => {
    const allowed = (signal) => policy.admitSignal(item, signal) === true;
    return {components: {trust: allowed('trust') ? 1 : null,
      quality: allowed('quality') ? 1 : null,
      freshness: allowed('freshness') ? 1 : null,
      usage: allowed('usage') ? (item.usageSignals.score ?? 1) : null,
      context: allowed('context') ? 1 : null,
      documentation: allowed('documentation') ? 1 : null},
    flags: {activeCertifiedDataProduct: false,
      deprecated: item.deprecationState === 'DEPRECATED', retiredArchived: false,
      criticalQualityFailure: false, freshnessSlaBreach: false,
      noOwnerSteward: false, unstableBreakingSchema: false},
    tieBreak: {certification: allowed('trust') ? 1 : null,
      quality: allowed('quality') ? 1 : null,
      freshness: allowed('freshness') ? 1 : null,
      usage: allowed('usage') ? (item.usageSignals.score ?? 1) : null},
    explanation: allowed('trust') ? [{code: 'certified', value: true}] : [],
    ...overrides}; }};
}

function runtime({documents, embeddings={}, graphIndex=null, semanticAuthority=null,
  businessSource=null, features=featureAuthority()}={}) {
  const backend = new InMemoryDiscoveryIndexBackend({now: () =>
    '2026-09-12T12:00:00.000Z'});
  const cursorAuthority = new InMemoryDiscoveryCursorAuthority({
    tokenFactory: (() => { let id = 0; return () => `opaque-${++id}`; })(),
    now: () => 1000});
  let queryId = 0;
  const service = new DiscoverySearchService({indexBackend: backend,
    rankingEngine: new DiscoveryRankingEngine({featureAuthority: features}),
    graphIndex, semanticAuthority, businessSource, cursorAuthority,
    queryIdFactory: () => `query-${++queryId}`});
  return backend.bulkRevision({revision: 'revision-1', documents, embeddings})
    .then(() => ({backend, service, cursorAuthority}));
}

describe('Discovery ranking contract', () => {
  test('publishes the exact Balanced profile and validates exact profile shape', () => {
    expect(validateDiscoveryRankingProfile(BALANCED_DISCOVERY_RANKING_PROFILE,
      {requirePublished: true})).toEqual(BALANCED_DISCOVERY_RANKING_PROFILE);
    expect(Object.values(BALANCED_DISCOVERY_RANKING_PROFILE.weights).reduce(
      (sum, value) => sum + value, 0)).toBeCloseTo(1, 12);
  });

  test.each([
    [{...BALANCED_DISCOVERY_RANKING_PROFILE,
      weights: {...BALANCED_DISCOVERY_RANKING_PROFILE.weights, lexical: 0.21}},
    /sum exactly/],
    [{...BALANCED_DISCOVERY_RANKING_PROFILE,
      penalties: {...BALANCED_DISCOVERY_RANKING_PROFILE.penalties,
        deprecated: 0.1}}, /must not be positive/],
    [{...BALANCED_DISCOVERY_RANKING_PROFILE, extra: true}, /unsupported field/],
    [{...BALANCED_DISCOVERY_RANKING_PROFILE, status: 'draft'}, /published/],
  ])('rejects an unsafe or non-published ranking profile', (profile, error) => {
    expect(() => validateDiscoveryRankingProfile(profile,
      {requirePublished: true})).toThrow(error);
  });

  test('reproduces weighted arithmetic, adjustment and explanation exactly', () => {
    const item = document('Customer', {deprecationState: 'DEPRECATED'});
    const ranked = new DiscoveryRankingEngine({featureAuthority:
      featureAuthority()}).rank([{document: item, lexical: 1, semantic: 0.5,
      business: 0.5, graph: 0.5, exactVisibleName: true,
      exactVisibleQualifiedName: true, exactBusinessTerm: false}],
    {query: {}, security: security()});
    expect(ranked[0].explanation.weightedComponentScores).toEqual({
      lexical: 0.22, semantic: 0.1, business: 0.06, trust: 0.1,
      quality: 0.08, freshness: 0.07, usage: 0.07, context: 0.05,
      lineage: 0.02, documentation: 0.05});
    expect(ranked[0].explanation.boosts).toEqual({
      exact_visible_qualified_name: 0.12});
    expect(ranked[0].explanation.penalties).toEqual({deprecated: -0.3});
    expect(ranked[0].score).toBeCloseTo(0.64, 12);
  });

  test('uses the exact published tie order and final stable identity', () => {
    const engine = new DiscoveryRankingEngine({featureAuthority:
      featureAuthority()});
    const values = engine.rank(['B', 'A'].map((id) => ({document: document(id),
      lexical: 0.5, semantic: null, business: null, graph: null,
      exactVisibleName: false, exactVisibleQualifiedName: false,
      exactBusinessTerm: false})), {query: {}, security: security()});
    expect(values.map((item) => item.document.name)).toEqual(['A', 'B']);
  });
});

describe('Discovery query language', () => {
  test('parses phrases, inclusions, exclusions and OR groups without reinterpretation', () => {
    expect(parseDiscoverySearchText(
      '"customer orders" provider:(firebird OR postgresql) -tag:retired owner:"Data Team"'
    )).toEqual({terms: [], phrases: ['customer orders'], lexicalText: 'customer orders',
      filters: [{field: 'provider', values: ['firebird', 'postgresql'],
        exclude: false, position: 18},
      {field: 'tag', values: ['retired'], exclude: true, position: 52},
      {field: 'owner', values: ['Data Team'], exclude: false, position: 65}]});
  });

  test.each([
    ['provider:(firebird OR postgresql', 'invalid_syntax', 9],
    ['unknown:value', 'unknown_field', 0],
    ['provider: value', 'missing_value', 9],
    ['customer OR orders', 'invalid_syntax', 9],
    ['"customer', 'invalid_syntax', 0],
    ['"customer"orders', 'invalid_syntax', 10],
  ])('returns position-aware diagnostics for malformed syntax', (text, code, position) => {
    try { parseDiscoverySearchText(text); throw new Error('expected rejection'); }
    catch(error) {
      expect(error).toBeInstanceOf(DiscoveryQuerySyntaxError);
      expect(error).toMatchObject({code, position});
    }
  });

  test('validates query bounds, modes, filters and related-search input', () => {
    expect(validateDiscoverySearchQuery({text: 'customer'})).toMatchObject({
      mode: 'QUICK', pageSize: 50, sort: 'relevance'});
    expect(() => validateDiscoverySearchQuery({text: '', mode: 'QUICK'})).toThrow(
      /1 to 1000/);
    expect(() => validateDiscoverySearchQuery({text: '',
      mode: 'GRAPH_RELATED'})).toThrow(/related resource/);
    expect(() => validateDiscoverySearchQuery({text: 'x', extra: true})).toThrow(
      /unsupported field extra/);
  });
});

describe('security-first candidate retrieval', () => {
  test('removes denied documents from results, counts, facets and autocomplete', async () => {
    const {service} = await runtime({documents: [document('Customer'),
      document('SecretCustomer')]});
    const policy = security({hidden: ['SecretCustomer']});
    const result = await service.search({text: 'customer'}, {security: policy});
    expect(result.results.map((item) => item.name)).toEqual(['Customer']);
    expect(result.totalVisibleEstimate).toBe(1);
    expect(result.facets.provider).toEqual([{value: 'firebird', count: 1}]);
    await expect(service.autocomplete('Se', {security: policy})).resolves.toEqual([]);
  });

  test('matches and returns only the admitted ScratchBird alias', async () => {
    const item = document('Customer', {provider: 'scratchbird',
      qualifiedNames: [], searchText: 'Customer', authorizedAliases: [
        alias('postgresql', 'public.customer', 'pg-visible'),
        alias('firebird', 'APP.CUSTOMER', 'fb-hidden')]});
    const {service} = await runtime({documents: [item]});
    const policy = security({aliases: ['pg-visible'], canonical: false});
    const result = await service.search({text: 'public.customer'},
      {security: policy});
    expect(result.results).toMatchObject([{name: 'Customer', canonicalRef: null,
      visibleAliases: [{surfaceId: 'postgresql',
        visibleQualifiedName: 'public.customer'}]}]);
    expect(result.results[0].explanation.candidateEvidence).toContainEqual({
      source: 'alias', visibleQualifiedName: 'public.customer'});
    expect(result.results[0].visibleAliases).toHaveLength(1);
    await expect(service.search({text: 'APP.CUSTOMER'}, {security: policy}))
      .resolves.toMatchObject({results: [], totalVisibleEstimate: 0});
    await expect(service.autocomplete('AP', {security: policy})).resolves.toEqual([]);
  });

  test('does not match hidden metadata or expose it through snippets/explanations', async () => {
    const item = document('Customer', {description: '<script>hidden phrase</script>',
      searchText: 'hidden phrase'});
    const {service} = await runtime({documents: [item]});
    const denied = security({metadata: false, signals: false});
    await expect(service.search({text: 'hidden phrase'}, {security: denied}))
      .resolves.toMatchObject({results: [], facets: {entityClass: []}});
    const visible = await service.search({text: 'Customer'}, {security: denied});
    expect(visible.results[0]).toMatchObject({provider: null, nativeKind: null,
      trustSummary: null, accessSummary: null});
    expect(JSON.stringify(visible.results[0])).not.toContain('hidden phrase');
    await expect(service.autocomplete('hi', {security: denied})).resolves.toEqual([]);
  });

  test('requires explicit authority for every security operation and filter facet', async () => {
    const {service} = await runtime({documents: [document('Customer')]});
    await expect(service.search({text: 'customer'}, {security: {}})).rejects.toThrow(
      /missing admitDocument/);
    await expect(service.search({text: 'customer', providers: ['firebird']},
      {security: security({facets: false})})).rejects.toThrow(
      /not authorized to filter by provider/);
  });

  test('revalidates and re-admits results from a non-compliant backend', async () => {
    const visible = document('Customer'); const hidden = document('SecretCustomer');
    const storage = new InMemoryDiscoveryIndexBackend();
    await storage.bulkRevision({revision: 'revision-1', documents: [visible, hidden]});
    const malicious = {upsertDocument: storage.upsertDocument.bind(storage),
      removeDocument: storage.removeDocument.bind(storage),
      lexicalSearch: async () => [{document: hidden, score: 1}],
      facetSearch: storage.facetSearch.bind(storage),
      semanticSearch: storage.semanticSearch.bind(storage),
      getDocument: storage.getDocument.bind(storage),
      bulkRevision: storage.bulkRevision.bind(storage),
      health: storage.health.bind(storage)};
    const service = new DiscoverySearchService({indexBackend: malicious,
      rankingEngine: new DiscoveryRankingEngine({featureAuthority:
        featureAuthority()}),
      cursorAuthority: new InMemoryDiscoveryCursorAuthority({
        tokenFactory: () => 'opaque'}), queryIdFactory: () => 'query'});
    await expect(service.search({text: 'customer'},
      {security: security({hidden: ['SecretCustomer']})}))
      .resolves.toMatchObject({results: [], totalVisibleEstimate: 0});
  });

  test('escapes active snippet content', async () => {
    const item = document('Customer', {description: '<b>Customer</b>'});
    const {service} = await runtime({documents: [item]});
    const result = await service.search({text: 'Customer'}, {security: security()});
    expect(result.results[0].snippet).toBe('&lt;b&gt;Customer&lt;/b&gt;');
  });
});

describe('candidate fusion, graph, semantic and structured retrieval', () => {
  test('merges lexical and semantic candidates once by canonical identity', async () => {
    const item = document('Customer');
    const {service} = await runtime({documents: [item],
      embeddings: {[item.canonicalRef]: [1, 0]},
      semanticAuthority: {vectorForQuery: async () => [1, 0]}});
    const result = await service.search({text: 'Customer', mode: 'SEMANTIC'},
      {security: security()});
    expect(result.results).toHaveLength(1);
    expect(result.results[0].explanation.componentScores).toMatchObject({
      lexical: 1, semantic: 1});
  });

  test('guarantees exact identities into the bounded lexical candidate set', async () => {
    const filler = Array.from({length: 510}, (_, index) => document(
      `A${String(index).padStart(3, '0')}`, {searchText: 'Needle'}));
    const exactMatch = document('Needle', {canonicalRef:
      'cde-resource://test/local/scope/table/zzzz'});
    const backend = new InMemoryDiscoveryIndexBackend();
    await backend.bulkRevision({revision: 'revision-1',
      documents: [...filler, exactMatch]});
    const result = await backend.lexicalSearch({text: 'Needle', limit: 500,
      admit: () => true, includeExact: true,
      exactProjection: (item) => [item.name]});
    expect(result).toHaveLength(501);
    expect(result.some((item) => item.document.name === 'Needle')).toBe(true);
  });

  test('requires a separately configured semantic authority', async () => {
    const {service} = await runtime({documents: [document('Customer')]});
    await expect(service.search({text: 'Customer', mode: 'SEMANTIC'},
      {security: security()})).rejects.toThrow(/embedding authority/);
  });

  test('retrieves real admitted graph neighbors and excludes denied edges', async () => {
    const customer = document('Customer'); const orders = document('Orders');
    const graphIndex = new InMemoryDiscoveryGraphIndex({now: () =>
      '2026-09-12T12:00:00.000Z'});
    graphIndex.publish({revision: 'graph-1', edges: [{edgeId: 'edge-one',
      fromRef: customer.canonicalRef, toRef: orders.canonicalRef, type: 'feeds',
      weight: 0.9, directed: true, evidence: {source: 'lineage'},
      revision: 'graph-1'}]});
    const {service} = await runtime({documents: [customer, orders], graphIndex});
    const query = {text: '', mode: 'GRAPH_RELATED',
      relatedTo: [customer.canonicalRef]};
    await expect(service.search(query, {security: security()})).resolves
      .toMatchObject({results: [{name: 'Orders'}]});
    await expect(service.search(query, {security: security({edges: false})}))
      .resolves.toMatchObject({results: [], totalVisibleEstimate: 0});
  });

  test('revalidates graph edges even when a graph adapter ignores admission', async () => {
    const customer = document('Customer'); const orders = document('Orders');
    const edge = {edgeId: 'edge-one', fromRef: customer.canonicalRef,
      toRef: orders.canonicalRef, type: 'feeds', weight: 0.9, directed: true,
      evidence: {}, revision: 'graph-1'};
    const graphIndex = {health: () => ({ready: true, activeRevision: 'graph-1'}),
      neighbors: () => [{canonicalRef: orders.canonicalRef, score: 0.9,
        evidence: [], edges: [edge]}]};
    const {service} = await runtime({documents: [customer, orders], graphIndex});
    await expect(service.search({text: '', mode: 'GRAPH_RELATED',
      relatedTo: [customer.canonicalRef]}, {security: security({edges: false})}))
      .resolves.toMatchObject({results: [], totalVisibleEstimate: 0});
  });

  test('publishes graph revisions atomically and rejects malformed edges', () => {
    const graph = new InMemoryDiscoveryGraphIndex();
    expect(() => graph.publish({revision: 'graph-1', edges: [{edgeId: 'bad'}]}))
      .toThrow(/requires fromRef/);
    expect(graph.health()).toMatchObject({state: 'empty', edgeCount: 0});
  });

  test('applies structured inclusions, exclusions and visible facets', async () => {
    const {service} = await runtime({documents: [document('Customer'),
      document('Orders', {tags: ['retired'], facetValues: {provider: 'firebird',
        nativeKind: 'table', domain: 'operations', certification: 'UNREVIEWED',
        quality: 'PASS', freshness: 'CURRENT', access: 'AVAILABLE'}})]});
    const result = await service.search({text: 'provider:firebird -tag:retired',
      mode: 'ADVANCED_FACETED'}, {security: security()});
    expect(result.results.map((item) => item.name)).toEqual(['Customer']);
    expect(result.facets.certification).toEqual([
      {value: 'CERTIFIED', count: 1}]);
  });

  test('keeps business candidates unavailable until their real authority exists', async () => {
    const {service} = await runtime({documents: [document('Customer')]});
    await expect(service.search({text: 'Customer', mode: 'BUSINESS_TERM'},
      {security: security()})).rejects.toThrow(/business-term.*unavailable/i);
  });
});

describe('stable result paging', () => {
  test('uses opaque one-use cursors bound to query, profile and retained revision', async () => {
    const {service} = await runtime({documents: [document('A'), document('B'),
      document('C')]});
    const policy = security();
    const first = await service.search({text: '', mode: 'ADVANCED_FACETED',
      pageSize: 1}, {security: policy});
    expect(first).toMatchObject({queryId: 'query-1', indexRevision: 'revision-1',
      totalVisibleEstimate: 3, nextCursor: 'opaque-1'});
    const second = await service.search({text: '', mode: 'ADVANCED_FACETED',
      pageSize: 1, cursor: first.nextCursor}, {security: policy});
    expect(second.queryId).toBe(first.queryId);
    expect(second.results[0].name).not.toBe(first.results[0].name);
    await expect(service.search({text: 'different', pageSize: 1,
      cursor: second.nextCursor}, {security: policy})).rejects.toMatchObject({
      code: 'cursor_mismatch'});
  });

  test('rejects expired cursors without silently restarting', () => {
    let now = 0;
    const cursors = new InMemoryDiscoveryCursorAuthority({tokenFactory: () => 'opaque',
      now: () => now, ttlMs: 1000});
    const token = cursors.issue({queryFingerprint: 'one'}); now = 1000;
    expect(() => cursors.read(token)).toThrow(/Expired search cursor/);
  });
});
