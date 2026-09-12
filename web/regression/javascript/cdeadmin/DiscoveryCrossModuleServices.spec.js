/////////////////////////////////////////////////////////////
// AI/DDI service, tool, analysis and enrichment release gates.
/////////////////////////////////////////////////////////////

import {AIReadToolRegistry} from
  'sources/cdeadmin_ui/modules/ai_interface';
import {
  DISCOVERY_AI_TOOL_IDS, DISCOVERY_SERVICE_API_METHODS,
  DiscoveryAIToolBridge, DiscoveryAnalysisService,
  DiscoveryEnrichmentService, DiscoveryServiceAPI,
  InMemoryDiscoveryEnrichmentStore, InMemoryDiscoveryGraphIndex,
  InMemoryDiscoveryIndexBackend,
} from 'sources/cdeadmin_ui/modules/discovery_intelligence';

function alias(id, kind, root, dialectId=kind === 'sbsql_native' ? 'sbsql' : id) {
  return {surfaceId: id, kind, dialectId,
    visibleQualifiedName: `${root}.CUSTOMER`, workareaSchemaRef: root,
    visibilityScope: root, queryCapabilities: ['read'], mutationCapabilities: [],
    crossSurfaceVisibility: kind === 'sbsql_native' ?
      'engine_authorized' : 'none', evidenceVersion: '1'};
}

function document(id, overrides={}) {
  return {schemaVersion: 1, documentId: `document-${id}`,
    canonicalRef: `cde-resource://firebird/local/app/table/${id}`,
    entityClass: 'LIVE_RESOURCE', nativeKind: 'table', provider: 'firebird',
    connectionEnvironment: 'test', name: id, qualifiedNames: [`APP.${id}`],
    authorizedAliases: [], description: `${id} description`,
    businessTerms: ['Customer'], synonyms: ['Client'], domainRefs: ['sales'],
    ownerRefs: ['team:data'], stewardRefs: ['user:steward'], tags: ['governed'],
    classification: 'INTERNAL', schemaSummary: {columns: 4},
    nativeMetadataSummary: {relationType: 'persistent'},
    trustSignals: {state: 'CERTIFIED'}, qualitySignals: {state: 'PASS'},
    freshnessSignals: {state: 'CURRENT'}, usageSignals: {score: 0.8},
    lineageSignals: {upstream: 1}, contractSignals: {state: 'VALID'},
    certifications: [{state: 'CERTIFIED'}], deprecationState: null,
    accessState: {state: 'AVAILABLE', query: true},
    searchText: `${id} description`, embeddingRefs: [],
    facetValues: {provider: 'firebird', certification: 'CERTIFIED'},
    updatedAt: '2026-09-12T12:00:00.000Z', sourceRevision: 'source-1',
    indexRevision: 'revision-1', ...overrides};
}

function security(overrides={}) {
  return {admitDocument: () => true, admitAlias: () => true,
    admitSignal: () => true, admitFacet: () => true,
    admitGraphEdge: () => true, admitBusinessKnowledge: () => true,
    exposeCanonicalRef: () => true, createAccessRequest: () => true,
    allowAnalysisRoute: () => true, allowCrossSurfaceAnalysis: () => true,
    requestEnrichmentSuggestion: () => true,
    reviewEnrichmentSuggestion: () => true,
    correctEnrichmentSuggestion: () => true,
    viewEnrichmentSuggestion: () => true,
    viewEnrichmentSuggestionHistory: () => true,
    createCurationItem: () => true, resolveCurationItem: () => true,
    ...overrides};
}

async function apiRuntime(documents=[document('CUSTOMER')]) {
  const backend = new InMemoryDiscoveryIndexBackend({now: () =>
    '2026-09-12T12:00:00.000Z'});
  await backend.bulkRevision({revision: 'revision-1', documents});
  const graph = new InMemoryDiscoveryGraphIndex({now: () =>
    '2026-09-12T12:00:00.000Z'});
  graph.publish({revision: 'graph-1', edges: documents.length > 1 ? [{
    edgeId: 'edge-1', fromRef: documents[0].canonicalRef,
    toRef: documents[1].canonicalRef, type: 'lineage', weight: 0.9,
    directed: true, evidence: {source: 'lineage'}, revision: 'graph-1'}] : []});
  const search = {search: jest.fn(async () => ({queryId: 'query-1',
    indexRevision: 'revision-1', rankingProfile: {profileId: 'balanced',
      revision: '1'}, totalVisibleEstimate: 1, facets: {},
    results: [{resultRef: documents[0].documentId,
      canonicalRef: documents[0].canonicalRef, name: documents[0].name}],
    warnings: [], nextCursor: null}))};
  const knowledgeValues = new Map([
    ['BusinessTerm:customer', {termId: 'customer', name: 'Customer'}],
    ['MetricDefinition:revenue', {metricId: 'revenue', name: 'Revenue'}],
    ['DataProduct:customer-360', {productId: 'customer-360',
      name: 'Customer 360'}],
  ]);
  const knowledge = {get: jest.fn((kind, id) =>
    knowledgeValues.get(`${kind}:${id}`) ?? null)};
  const trust = {evaluate: jest.fn(() => ({components: {trust: 1},
    flags: {}, tieBreak: {}, explanation: []}))};
  const access = {create: jest.fn((input) => ({schemaVersion: 1,
    requestId: 'request-1', ...input, policyResult: null, approvers: [],
    status: 'DRAFT', providerGrantPlanRef: null}))};
  const api = new DiscoveryServiceAPI({search, indexBackend: backend, graph,
    knowledge, trust, access});
  return {api, backend, graph, search, knowledge, trust, access};
}

describe('typed Discovery service API', () => {
  it('implements all nine exact service operations over existing authorities', async () => {
    const second = document('ORDER');
    const {api, search, access} = await apiRuntime([document('CUSTOMER'), second]);
    expect(DISCOVERY_SERVICE_API_METHODS.every((method) =>
      typeof api[method] === 'function')).toBe(true);
    await expect(api.search({text: 'customer'}, {security: security()}))
      .resolves.toMatchObject({queryId: 'query-1'});
    expect(search.search).toHaveBeenCalledTimes(1);
    await expect(api.get_entity({canonicalRef: document('CUSTOMER').canonicalRef},
      {security: security()})).resolves.toMatchObject({name: 'CUSTOMER',
      schemaSummary: {columns: 4}, accessState: {query: true}});
    await expect(api.get_related({canonicalRefs: [document('CUSTOMER').canonicalRef]},
      {security: security()})).resolves.toMatchObject({rows: [{entity: {
      name: 'ORDER'}, score: 0.9}]});
    expect(api.get_business_term({termId: 'customer'},
      {security: security()})).toMatchObject({name: 'Customer'});
    expect(api.get_metric({metricId: 'revenue'},
      {security: security()})).toMatchObject({name: 'Revenue'});
    expect(api.get_product({productId: 'customer-360'},
      {security: security()})).toMatchObject({name: 'Customer 360'});
    await expect(api.get_trust({canonicalRef: document('CUSTOMER').canonicalRef},
      {security: security()})).resolves.toMatchObject({evaluation: {
      components: {trust: 1}}});
    await expect(api.get_access_state({canonicalRef:
      document('CUSTOMER').canonicalRef}, {security: security()}))
      .resolves.toMatchObject({accessState: {state: 'AVAILABLE'}});
    await expect(api.request_access({requester: 'user:one', targetRef:
      document('CUSTOMER').canonicalRef, requestedAccess: ['QUERY'],
    environment: 'test', reason: 'Analyze customer activity', duration: 'P30D',
    projectRef: null}, {security: security()})).resolves.toMatchObject({
      requestId: 'request-1', status: 'DRAFT'});
    expect(access.create).toHaveBeenCalledTimes(1);
  });

  it('trims metadata, aliases and signals and makes hidden identities absent', async () => {
    const withAlias = document('CUSTOMER', {authorizedAliases: [
      alias('pg', 'compatibility_parser', 'pg-root', 'postgresql')]});
    const {api} = await apiRuntime([withAlias]);
    const trimmed = await api.get_entity({canonicalRef: withAlias.canonicalRef},
      {security: security({admitDocument: (_item, dimension) =>
        dimension === 'DISCOVER_IDENTITY', admitAlias: () => false,
      admitSignal: () => false, exposeCanonicalRef: () => false})});
    expect(trimmed).toMatchObject({canonicalRef: null, provider: null,
      qualifiedNames: [], authorizedAliases: [], description: null,
      trustSignals: null, accessState: null});
    await expect(api.get_entity({canonicalRef: withAlias.canonicalRef},
      {security: security({admitDocument: () => false})}))
      .rejects.toThrow(/not found/i);
    expect(() => api.get_metric({metricId: 'revenue'}, {security:
      security({admitBusinessKnowledge: () => false})})).toThrow(/not found/i);
  });

  it('drops graph neighbors that are not visible instead of leaking their count', async () => {
    const hidden = document('HIDDEN');
    const {api} = await apiRuntime([document('CUSTOMER'), hidden]);
    const result = await api.get_related({canonicalRefs: [
      document('CUSTOMER').canonicalRef]}, {security: security({
      admitDocument: (candidate) => candidate.name !== 'HIDDEN'})});
    expect(result.rows).toEqual([]);
  });
});

function projectedScratchBird(id, compatibility) {
  return {resultRef: `document-${id}`, canonicalRef:
    `scratchbird://instance-one/uuid/${id}`, entityClass: 'LIVE_RESOURCE',
  provider: 'scratchbird', nativeKind: 'table', name: id,
  authorizedAliases: [alias(`native-${id}`, 'sbsql_native', 'native'),
    alias(compatibility, 'compatibility_parser', `${compatibility}-root`,
      compatibility)], description: `${id} data`, businessTerms: [],
  schemaSummary: {}, accessState: {state: 'AVAILABLE'}};
}

describe('Discovery search-to-analysis planning', () => {
  function analysis(entities, aiInterface=null) {
    return new DiscoveryAnalysisService({discoveryAPI: {get_entity:
      jest.fn(async ({canonicalRef}) => entities.get(canonicalRef))}, aiInterface});
  }

  it('selects native SBsql for a cross-surface ScratchBird selection', async () => {
    const left = projectedScratchBird('one', 'postgresql');
    const right = projectedScratchBird('two', 'mysql');
    const service = analysis(new Map([[left.canonicalRef, left],
      [right.canonicalRef, right]]));
    const result = await service.plan({canonicalRefs: [left.canonicalRef,
      right.canonicalRef], question: 'Compare both roots'}, {security: security()});
    expect(result).toMatchObject({status: 'draft', executionAuthorized: false,
      connectorClass: 'scratchbird_sbsql', dialectId: 'sbsql'});
    expect(result.actions).toContain('open_sbsql');
  });

  it('refuses a compatibility connector for cross-root work', async () => {
    const left = projectedScratchBird('one', 'postgresql');
    const right = projectedScratchBird('two', 'mysql');
    const service = analysis(new Map([[left.canonicalRef, left],
      [right.canonicalRef, right]]));
    const result = await service.plan({canonicalRefs: [left.canonicalRef,
      right.canonicalRef], question: 'Compare both roots',
    requestedConnector: 'scratchbird_compatibility',
    requestedSurfaceId: 'postgresql'}, {security: security()});
    expect(result).toMatchObject({status: 'refused', executionAuthorized: false,
      diagnostics: [{code: 'compatibility_surface_not_visible_for_every_resource'}]});
    expect(result.actions).toContain('open_sbsql');
  });

  it('allows one explicitly authorized compatibility root without inventing cross-root support', async () => {
    const left = projectedScratchBird('one', 'postgresql');
    const right = projectedScratchBird('two', 'postgresql');
    const service = analysis(new Map([[left.canonicalRef, left],
      [right.canonicalRef, right]]));
    const result = await service.plan({canonicalRefs: [left.canonicalRef,
      right.canonicalRef], question: 'Analyze one compatibility root',
    requestedConnector: 'scratchbird_compatibility',
    requestedSurfaceId: 'postgresql'}, {security: security()});
    expect(result).toMatchObject({status: 'draft',
      connectorClass: 'scratchbird_compatibility', dialectId: 'postgresql'});
    expect(result.actions).toContain('open_compatibility');
    expect(result.actions).not.toContain('open_sbsql');
  });

  it('refuses SBsql cross-surface work when the explicit policy denies it', async () => {
    const left = projectedScratchBird('one', 'postgresql');
    const right = projectedScratchBird('two', 'mysql');
    const service = analysis(new Map([[left.canonicalRef, left],
      [right.canonicalRef, right]]));
    await expect(service.plan({canonicalRefs: [left.canonicalRef,
      right.canonicalRef], question: 'Compare both roots'}, {security:
      security({allowCrossSurfaceAnalysis: () => false})})).resolves.toMatchObject({
      status: 'refused', diagnostics: [{code:
        'sbsql_cross_surface_policy_denied'}]});
  });

  it('keeps deterministic analysis available with AI disabled', async () => {
    const entity = {...projectedScratchBird('one', 'postgresql'),
      provider: 'firebird', canonicalRef: 'cde-resource://firebird/local/db/table/one',
      authorizedAliases: []};
    const service = analysis(new Map([[entity.canonicalRef, entity]]));
    expect(service.capabilities()).toEqual({deterministicPlanning: true,
      aiInterpretation: false,
      aiDisabledReason: 'AI Interface discovery assistance is not configured.'});
    const result = await service.interpret({canonicalRefs: [entity.canonicalRef],
      question: 'Count rows'}, {security: security()});
    expect(result.plan).toMatchObject({status: 'draft',
      connectorClass: 'database_provider'});
    expect(result.interpretation).toBeNull();
  });

  it('sends only the trimmed draft to the AI Interface and never authorizes execution', async () => {
    const entity = {...projectedScratchBird('one', 'postgresql'),
      provider: 'firebird', canonicalRef: 'cde-resource://firebird/local/db/table/one',
      authorizedAliases: []};
    const aiInterface = {requestDiscoveryAnalysis: jest.fn(async () => ({
      summary: 'Use a count aggregate.'}))};
    const result = await analysis(new Map([[entity.canonicalRef, entity]]),
      aiInterface).interpret({canonicalRefs: [entity.canonicalRef],
      question: 'Count rows'}, {security: security(), aiContext: {policy: 'safe'}});
    expect(result.interpretation).toEqual({summary: 'Use a count aggregate.'});
    expect(aiInterface.requestDiscoveryAnalysis).toHaveBeenCalledWith(
      expect.objectContaining({executionAuthorized: false,
        connectorClass: 'database_provider'}), {policy: 'safe'});
  });
});

describe('Discovery tools published through the AI Interface catalog', () => {
  function toolRuntime(overrides={}) {
    const readTools = new AIReadToolRegistry();
    const discoveryAPI = {search: jest.fn(async () => ({results: [{id: 'one'}],
      queryId: 'query-1'})), get_entity: jest.fn(async ({canonicalRef}) => ({
      canonicalRef, resultRef: 'document-one', name: 'Customer'})),
    get_related: jest.fn(async () => ({rows: []})),
    get_product: jest.fn(), get_business_term: jest.fn(),
    get_metric: jest.fn(() => ({metricId: 'revenue'})),
    get_trust: jest.fn(async () => ({score: 1})),
    get_access_state: jest.fn(), request_access: jest.fn(), ...overrides};
    const analysis = {plan: jest.fn(async () => ({status: 'draft'}))};
    const egress = {admit: jest.fn(() => true),
      trim: jest.fn(async (_id, value) => value),
      classify: jest.fn(() => 'INTERNAL')};
    const bridge = new DiscoveryAIToolBridge({readTools, discoveryAPI,
      analysis, egress});
    bridge.register();
    return {readTools, discoveryAPI, analysis, egress, bridge};
  }

  function context(overrides={}) {
    return {currentUser: {permissions: ['ai.use', 'ai.delegate_read',
      'discovery.use', 'discovery.request_access']},
    discoverySecurity: security(), discoveryEnabled: true, ...overrides};
  }

  it('registers the exact bounded read/propose catalog and invokes trimmed results', async () => {
    const {readTools, egress} = toolRuntime();
    expect(readTools.published(context()).map((item) => item.readToolId).sort())
      .toEqual([...DISCOVERY_AI_TOOL_IDS].sort());
    await expect(readTools.invoke('discovery.search_data', {
      query: {text: 'customer', pageSize: 10}}, context())).resolves.toMatchObject({
      classification: 'INTERNAL', value: {rows: [{id: 'one'}],
        meta: {queryId: 'query-1'}}});
    expect(egress.trim).toHaveBeenCalledTimes(1);
    expect(egress.classify).toHaveBeenCalledTimes(1);
  });

  it('enforces AI egress, Discovery availability, permissions and strict schemas', async () => {
    const {readTools, egress} = toolRuntime();
    egress.admit.mockReturnValue(false);
    expect(readTools.published(context())).toEqual([]);
    await expect(readTools.invoke('discovery.explain_asset', {
      canonicalRef: 'resource:one'}, context())).rejects.toThrow(/access denied/);
    egress.admit.mockReturnValue(true);
    await expect(readTools.invoke('discovery.explain_asset', {
      canonicalRef: 'resource:one', invented: true}, context()))
      .rejects.toThrow(/JSON Schema/);
    expect(readTools.published(context({discoveryEnabled: false}))).toEqual([]);
  });

  it('creates an access proposal without persisting or granting access', async () => {
    const {readTools, discoveryAPI} = toolRuntime();
    const result = await readTools.invoke('discovery.request_access_draft', {
      requester: 'user:one', targetRef: 'resource:one',
      requestedAccess: ['QUERY'], environment: 'test',
      reason: 'Analyze customer activity', duration: 'P30D'}, context());
    expect(result.value.rows[0]).toMatchObject({status: 'PROPOSED',
      persisted: false});
    expect(discoveryAPI.request_access).not.toHaveBeenCalled();
  });

  it('rejects an egress authority that attempts to add rows', async () => {
    const {readTools, egress} = toolRuntime();
    egress.trim.mockResolvedValue({rows: [{id: 1}, {id: 2}], meta: {}});
    await expect(readTools.invoke('discovery.explain_asset', {
      canonicalRef: 'resource:one'}, context())).rejects.toThrow(/cannot add/);
  });

  it('owns registration lifecycle and removes every published tool', () => {
    const {readTools, bridge} = toolRuntime();
    expect(() => bridge.register()).toThrow(/already registered/);
    expect(bridge.dispose()).toBe(true);
    expect(readTools.published(context())).toEqual([]);
    expect(bridge.dispose()).toBe(false);
  });
});

describe('reviewable AI enrichment', () => {
  function enrichment(aiInterface) {
    const apply = jest.fn();
    const curation = {open: jest.fn(() => ({itemId: 'item-1'})),
      resolve: jest.fn(async (_id, input) => { apply(input.value); return {
        item: {itemId: 'item-1'}, evidence: {evidenceId: 'evidence-1'}}; }),
      dismiss: jest.fn(() => ({item: {itemId: 'item-1'},
        evidence: {evidenceId: 'evidence-2'}}))};
    const service = new DiscoveryEnrichmentService({
      store: new InMemoryDiscoveryEnrichmentStore(),
      discoveryAPI: {get_entity: jest.fn(async ({canonicalRef}) => ({
        resultRef: 'document-one', canonicalRef, entityClass: 'LIVE_RESOURCE',
        name: 'CUSTOMER', description: 'Customer table', businessTerms: [],
        synonyms: [], domainRefs: [], ownerRefs: [], tags: [],
        classification: 'INTERNAL', schemaSummary: {},
        nativeMetadataSummary: {}}))}, curation, aiInterface,
      idFactory: () => 'suggestion-1',
      now: (() => { const values = ['2026-09-12T12:00:00.000Z',
        '2026-09-12T12:01:00.000Z']; return () => values.shift() ??
          '2026-09-12T12:01:00.000Z'; })()});
    return {service, curation, apply};
  }

  const request = {kind: 'description', targetRef: 'resource:customer',
    field: 'description', currentValue: 'Customer table',
    sourceEvidenceRefs: ['evidence:metadata-1']};
  const response = {proposedValue: 'Governed customer master.',
    modelRef: 'model:one', profileRef: 'profile:curation', confidence: 0.92,
    sourceEvidenceRefs: ['evidence:metadata-1']};

  it('keeps Discovery operational and reports enrichment unavailable with AI off', async () => {
    const {service, curation} = enrichment(null);
    expect(service.capabilities()).toEqual({review: true, aiEnrichment: false,
      aiDisabledReason: 'AI Interface enrichment is not configured.'});
    await expect(service.request(request, {security: security()}))
      .rejects.toThrow(/AI Interface enrichment is disabled/);
    expect(curation.open).not.toHaveBeenCalled();
  });

  it('stores model/profile/evidence provenance as pending without changing knowledge', async () => {
    const aiInterface = {requestDiscoveryEnrichment: jest.fn(async () => response)};
    const {service, curation, apply} = enrichment(aiInterface);
    const suggestion = await service.request(request, {security: security(),
      aiContext: {currentUser: {id: 'one'}}});
    expect(suggestion).toMatchObject({suggestionId: 'suggestion-1',
      modelRef: 'model:one', profileRef: 'profile:curation', confidence: 0.92,
      reviewState: 'PENDING', reviewedAt: null});
    expect(aiInterface.requestDiscoveryEnrichment).toHaveBeenCalledWith(
      expect.objectContaining({permittedContext: expect.objectContaining({
        name: 'CUSTOMER'})}), {currentUser: {id: 'one'}});
    expect(curation.open).not.toHaveBeenCalled();
    expect(apply).not.toHaveBeenCalled();
  });

  it('keeps confidence null when the model supplies no meaningful value', async () => {
    const withoutConfidence = {...response}; delete withoutConfidence.confidence;
    const {service} = enrichment({requestDiscoveryEnrichment:
      jest.fn(async () => withoutConfidence)});
    await expect(service.request(request, {security: security()}))
      .resolves.toMatchObject({confidence: null, reviewState: 'PENDING'});
  });

  it('changes authoritative knowledge only after an explicit accepted curation', async () => {
    const {service, curation, apply} = enrichment({
      requestDiscoveryEnrichment: jest.fn(async () => response)});
    await service.request(request, {security: security()});
    const accepted = await service.accept('suggestion-1', {
      actorRef: 'user:curator', reason: 'Verified against source metadata.',
      security: security()});
    expect(accepted).toMatchObject({reviewState: 'ACCEPTED',
      reviewerRef: 'user:curator', curationItemRef: 'item-1',
      curationEvidenceRef: 'evidence-1'});
    expect(curation.resolve).toHaveBeenCalledWith('item-1',
      expect.objectContaining({action: 'accept_ai_suggestion'}));
    expect(apply).toHaveBeenCalledTimes(1);
    expect(service.history('suggestion-1', {security: security()}))
      .toHaveLength(2);
    await expect(service.accept('suggestion-1', {actorRef: 'user:curator',
      reason: 'Again', security: security()})).rejects.toThrow(/already ACCEPTED/);
  });

  it('records rejection without applying the suggestion', async () => {
    const {service, curation, apply} = enrichment({
      requestDiscoveryEnrichment: jest.fn(async () => response)});
    await service.request(request, {security: security()});
    await expect(service.reject('suggestion-1', {actorRef: 'user:curator',
      reason: 'Description is inaccurate.', security: security()}))
      .resolves.toMatchObject({reviewState: 'REJECTED',
        curationEvidenceRef: 'evidence-2'});
    expect(curation.dismiss).toHaveBeenCalledTimes(1);
    expect(apply).not.toHaveBeenCalled();
  });

  it('refuses invented evidence and unauthorized curator corrections', async () => {
    const invented = enrichment({requestDiscoveryEnrichment: jest.fn(async () =>
      ({...response, sourceEvidenceRefs: ['evidence:hidden']}))});
    await expect(invented.service.request(request, {security: security()}))
      .rejects.toThrow(/outside its permitted context/);
    const valid = enrichment({requestDiscoveryEnrichment:
      jest.fn(async () => response)});
    await valid.service.request(request, {security: security()});
    await expect(valid.service.accept('suggestion-1', {
      proposedValue: 'Corrected value', actorRef: 'user:curator',
      reason: 'Correction', security: security({
        correctEnrichmentSuggestion: () => false})}))
      .rejects.toThrow(/correction denied/);
    expect(valid.apply).not.toHaveBeenCalled();
  });
});
