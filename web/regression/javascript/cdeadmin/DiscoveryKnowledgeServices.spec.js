/////////////////////////////////////////////////////////////
// Discovery business knowledge, trust and profiling authority gates.
/////////////////////////////////////////////////////////////

import {
  BusinessKnowledgeService, CertificationService, DataProductService,
  DiscoveryKnowledgeAssetAuthority, DiscoveryProfilerRegistry,
  DiscoveryRankingEngine, DiscoverySearchService, DiscoveryTrustFeatureAuthority,
  InMemoryCertificationStore, InMemoryDiscoveryCursorAuthority,
  InMemoryDiscoveryIndexBackend, ProfileStatisticsService,
  discoveryFreshnessScore,
  discoveryKnowledgeAssetRequest, validateCertificationRecord,
  validateDiscoveryKnowledgeAsset, validateProfileStatistic,
} from 'sources/cdeadmin_ui/modules/discovery_intelligence';

function mapping(overrides={}) {
  return {targetRef: 'cde-resource://firebird/local/app/table/customer',
    relationship: 'represents', confidence: 1, origin: 'curator',
    validFrom: null, validTo: null, approvedBy: 'user:curator',
    approvalState: 'APPROVED', ...overrides};
}

function term(overrides={}) {
  return {schemaVersion: 1, termId: 'customer', name: 'Customer',
    definition: 'A party that purchases products.', status: 'APPROVED',
    domainRef: 'sales', synonyms: ['client'], acronyms: ['CUST'],
    mappings: [mapping()], ownerRefs: ['team:data'], version: '1', ...overrides};
}

function domain(overrides={}) {
  return {schemaVersion: 1, domainId: 'sales', name: 'Sales',
    description: 'Sales and customer data.', parentRef: null,
    ownerRefs: ['team:data'], stewardRefs: [], status: 'active', version: '1',
    ...overrides};
}

function metric(overrides={}) {
  return {schemaVersion: 1, metricId: 'revenue', name: 'Revenue',
    description: 'Recognized revenue.', businessFormula: 'sum sales amount',
    aggregation: 'sum', grain: 'one sale', dimensionRefs: [], filters: [],
    timeSemantics: 'booking date', semanticModelRef: null,
    implementationRefs: ['cde-resource://firebird/local/app/view/revenue'],
    ownerRefs: ['team:finance'], stewardRefs: [],
    certification: {state: 'UNREVIEWED'}, domainRef: 'sales', version: '1',
    ...overrides};
}

function product(overrides={}) {
  return {schemaVersion: 1, productId: 'customer-360', name: 'Customer 360',
    summary: 'Governed customer view.', domainRef: 'sales',
    ownerRefs: ['team:data'], stewardRefs: ['user:steward'], status: 'DRAFT',
    version: '1.0', resourceRefs: [mapping().targetRef], assetRefs: [],
    semanticModelRefs: [], metricRefs: [], dashboardRefs: [], apiRefs: [],
    contractRefs: [], qualityRefs: [], lineageRefs: [], usageDocs: 'How to use.',
    accessPolicyRef: null, slaSummary: {}, certification: {state: 'UNREVIEWED'},
    releaseNotes: 'Initial version.', ...overrides};
}

function certificationProfile(overrides={}) {
  return {schemaVersion: 1, profileId: 'standard', name: 'Standard',
    targetTypes: ['DATA_PRODUCT'], expirationDays: 365,
    criteria: [{criterionId: 'owner', required: true, source: 'metadata',
      evidenceRule: 'owner present'}], requireOwner: true, requireContract: false,
    requireQuality: true, status: 'published', version: '1', ...overrides};
}

function discoveryDocument(overrides={}) {
  return {schemaVersion: 1, documentId: 'document-customer',
    canonicalRef: mapping().targetRef, entityClass: 'LIVE_RESOURCE',
    nativeKind: 'table', provider: 'firebird', connectionEnvironment: 'test',
    name: 'CUSTOMER_DATA', qualifiedNames: ['APP.CUSTOMER_DATA'],
    authorizedAliases: [], description: 'Customer master.',
    businessTerms: ['Customer'], synonyms: ['Client'], domainRefs: ['sales'],
    ownerRefs: ['team:data'], stewardRefs: ['user:steward'], tags: [],
    classification: 'INTERNAL', schemaSummary: {}, nativeMetadataSummary: {},
    trustSignals: {}, qualitySignals: {}, freshnessSignals: {}, usageSignals: {},
    lineageSignals: {}, contractSignals: {}, certifications: [],
    deprecationState: null, accessState: {state: 'AVAILABLE'},
    searchText: 'Customer data', embeddingRefs: [],
    facetValues: {provider: 'firebird'},
    updatedAt: '2026-09-12T12:00:00.000Z', sourceRevision: 'source-1',
    indexRevision: 'revision-1', ...overrides};
}

class ProjectAssets {
  constructor() { this.items = new Map(); this.history = new Map(); }
  async saveAsset(projectId, assetId, request) {
    const key = `${projectId}:${assetId}`; const prior = this.items.get(key);
    if((prior?.version ?? 0) !== request.expected_version) throw new Error('conflict');
    const value = {...request, project_id: projectId, asset_id: assetId,
      version: (prior?.version ?? 0) + 1};
    this.items.set(key, value); this.history.set(key,
      [...(this.history.get(key) ?? []), value]); return value;
  }
  async asset(projectId, assetId) { return this.items.get(`${projectId}:${assetId}`); }
  async revisions(projectId, assetId) {
    return this.history.get(`${projectId}:${assetId}`) ?? [];
  }
  async deleteAsset(projectId, assetId, expectedVersion) {
    const key = `${projectId}:${assetId}`;
    if(this.items.get(key)?.version !== expectedVersion) throw new Error('conflict');
    return this.items.delete(key);
  }
}

function trustSource(overrides={}) {
  return {certificationState: 'CERTIFIED', qualityState: 'PASS',
    freshnessScore: 1, weightedUsageEvents: 1000, contextRelevance: 1,
    lineageRelevance: 1, businessRelevance: 1, usageDocumentation: true,
    examples: true, activeCertifiedDataProduct: false,
    freshnessSlaBreach: false, unstableBreakingSchema: false, ...overrides};
}

function searchSecurity(overrides={}) {
  return {admitDocument: () => true, admitAlias: () => true,
    admitSignal: () => true, admitFacet: () => true,
    admitGraphEdge: () => true, admitBusinessKnowledge: () => true,
    exposeCanonicalRef: () => true, ...overrides};
}

describe('Discovery knowledge contracts and persistence', () => {
  test.each([
    ['BusinessTerm', term()], ['Domain', domain()],
    ['MetricDefinition', metric()], ['DataProduct', product()],
    ['CertificationProfile', certificationProfile()],
  ])('validates and freezes %s independently', (kind, input) => {
    const value = validateDiscoveryKnowledgeAsset(kind, input);
    expect(Object.isFrozen(value)).toBe(true);
  });

  test('copies the exact DataProduct schema and refuses false certification', () => {
    expect(validateDiscoveryKnowledgeAsset('DataProduct', product())).toMatchObject({
      status: 'DRAFT', resourceRefs: [mapping().targetRef]});
    expect(() => validateDiscoveryKnowledgeAsset('DataProduct', product({
      status: 'CERTIFIED'}))).toThrow(/cannot claim CERTIFIED/);
    expect(() => validateDiscoveryKnowledgeAsset('DataProduct', product({
      unknown: true}))).toThrow(/unsupported field unknown/);
  });

  test('keeps a business metric separate from physical implementations', () => {
    const value = validateDiscoveryKnowledgeAsset('MetricDefinition', metric());
    expect(value.metricId).toBe('revenue');
    expect(value.businessFormula).toBe('sum sales amount');
    expect(value.implementationRefs).toHaveLength(1);
    expect(() => validateDiscoveryKnowledgeAsset('MetricDefinition', metric({
      filters: {raw: 'x'}}))).toThrow(/filters must be an array/);
  });

  test('never promotes a suggested mapping without a human approver', () => {
    expect(validateDiscoveryKnowledgeAsset('BusinessTerm', term({mappings: [
      mapping({approvalState: 'SUGGESTED', approvedBy: null, origin: 'ai'})]}))
      .mappings[0].approvalState).toBe('SUGGESTED');
    expect(() => validateDiscoveryKnowledgeAsset('BusinessTerm', term({mappings: [
      mapping({approvalState: 'APPROVED', approvedBy: null, origin: 'ai'})]})))
      .toThrow(/human approver/);
  });

  test('creates versioned project assets with dependency and resource bindings', async () => {
    const authority = new DiscoveryKnowledgeAssetAuthority({projectAssets:
      new ProjectAssets()});
    const request = discoveryKnowledgeAssetRequest('DataProduct', product());
    expect(request).toMatchObject({asset_type: 'cdeadmin.discovery.data_product',
      expected_version: 0, resource_bindings: [mapping().targetRef]});
    const saved = await authority.save('project', 'DataProduct', product());
    expect(saved).toMatchObject({assetKind: 'DataProduct', version: 1});
    await expect(authority.revisions('project', saved.asset_id,
      {kind: 'DataProduct'})).resolves.toHaveLength(1);
  });
});

describe('BusinessKnowledgeService and DataProductService', () => {
  function services(documentAuthority={resolve: (reference) => ({
    canonicalRef: reference, nativeKind: 'table', nativeMetadataSummary: {},
    facetValues: {}})}) {
    const assets = new DiscoveryKnowledgeAssetAuthority({projectAssets:
      new ProjectAssets()});
    const knowledge = new BusinessKnowledgeService({assets,
      now: () => Date.parse('2026-09-12T12:00:00Z')});
    return {assets, knowledge, products: new DataProductService({knowledge,
      documentAuthority})};
  }

  test('rejects domain cycles and preserves the current good hierarchy', () => {
    const {knowledge} = services();
    knowledge.register('Domain', domain());
    knowledge.register('Domain', domain({domainId: 'child', name: 'Child',
      parentRef: 'sales'}));
    expect(() => knowledge.register('Domain', domain({parentRef: 'child'})))
      .toThrow(/cycle/);
    expect(knowledge.get('Domain', 'sales').parentRef).toBeNull();
  });

  test('returns only approved, active and explicitly admitted business mappings', () => {
    const {knowledge} = services();
    knowledge.register('BusinessTerm', term());
    knowledge.register('BusinessTerm', term({termId: 'suggestion', name: 'Client',
      mappings: [mapping({approvalState: 'SUGGESTED', approvedBy: null})]}));
    expect(knowledge.search({query: {text: 'client'},
      parsed: {lexicalText: 'client'}, security: {
        admitBusinessKnowledge: () => true}})).toMatchObject([{
      canonicalRef: mapping().targetRef, score: 0.9}]);
    expect(knowledge.search({query: {text: 'customer'},
      parsed: {lexicalText: 'customer'}, security: {
        admitBusinessKnowledge: () => false}})).toEqual([]);
  });

  test('persists product members and enforces status transitions', async () => {
    const {products} = services();
    const created = await products.save('project', product());
    const updated = await products.changeMember('project', created.asset_id,
      'metricRefs', 'MetricDefinition:revenue', 'add', 1);
    expect(updated.content.metricRefs).toEqual(['MetricDefinition:revenue']);
    await expect(products.setStatus('project', created.asset_id, 'CERTIFIED',
      {expectedVersion: 2})).rejects.toThrow(/transition DRAFT -> CERTIFIED/);
    await expect(products.setStatus('project', created.asset_id, 'REVIEW',
      {expectedVersion: 2})).resolves.toMatchObject({content: {status: 'REVIEW'}});
    const record = {schemaVersion: 1, certificationId: 'cert-product',
      targetRef: created.asset_id, targetRevision: '3', profileRef: 'standard',
      state: 'CERTIFIED', reviewerRefs: ['user:reviewer'], criteriaEvidence: [],
      reviewedAt: '2026-09-12T12:00:00Z',
      expiresAt: '2027-09-12T12:00:00Z', conditions: '', notes: 'Approved',
      supersedesRef: 'cert-request'};
    await expect(products.setStatus('project', created.asset_id, 'CERTIFIED',
      {expectedVersion: 3, certification: {...record, targetRevision: '2'}}))
      .rejects.toThrow(/does not bind/);
    await expect(products.setStatus('project', created.asset_id, 'CERTIFIED',
      {expectedVersion: 3, certification: record})).resolves.toMatchObject({
      content: {status: 'CERTIFIED', certification: {
        certificationId: 'cert-product'}}});
  });

  test('refuses system-catalog resources as Data Product members', async () => {
    const {products} = services({resolve: (reference) => ({
      canonicalRef: reference, nativeKind: 'system_table',
      nativeMetadataSummary: {systemCatalog: true}, facetValues: {}})});
    await expect(products.save('project', product())).rejects.toThrow(
      /not eligible for Data Product membership/);
  });
});

describe('Certification authority', () => {
  function certification() {
    let id = 0;
    return new CertificationService({store: new InMemoryCertificationStore(),
      evidenceAuthority: {evaluate: async () => ({criteriaEvidence: [{
        criterionId: 'owner', passed: true, evidence: {ownerRef: 'team:data'}}],
      ownerPresent: true, contractCurrent: true, qualityPassing: true})},
      now: () => '2026-09-12T12:00:00.000Z', idFactory: () => `cert-${++id}`});
  }

  test('binds request and review to exact target/profile revisions', async () => {
    const service = certification(); const profile = certificationProfile();
    const request = service.request({targetRef: 'DataProduct:customer-360',
      targetRevision: '7', reviewerRefs: ['user:reviewer']}, profile);
    expect(request).toMatchObject({state: 'IN_REVIEW', targetRevision: '7',
      profileRef: 'standard'});
    const reviewed = await service.review(request.certificationId, {
      state: 'CERTIFIED', reviewerRefs: ['user:reviewer'], notes: 'Approved.'},
    profile);
    expect(reviewed).toMatchObject({state: 'CERTIFIED',
      supersedesRef: request.certificationId});
    expect(service.current(request.targetRef).certificationId)
      .toBe(reviewed.certificationId);
  });

  test('requires authoritative mandatory evidence and conditions', async () => {
    let id = 0;
    const service = new CertificationService({store: new InMemoryCertificationStore(),
      evidenceAuthority: {evaluate: async () => ({criteriaEvidence: [{
        criterionId: 'owner', passed: false, evidence: {reason: 'missing'}}],
      ownerPresent: false, contractCurrent: true, qualityPassing: true})},
      now: () => '2026-09-12T12:00:00.000Z', idFactory: () => `cert-${++id}`});
    const profile = certificationProfile();
    const request = service.request({targetRef: 'MetricDefinition:revenue',
      targetRevision: '3'}, profile);
    await expect(service.review(request.certificationId, {state: 'CERTIFIED'},
      profile)).rejects.toThrow(/required criteria/);
    expect(() => validateCertificationRecord({schemaVersion: 1,
      certificationId: 'x', targetRef: 'x', targetRevision: '1', profileRef: 'p',
      state: 'CERTIFIED_WITH_CONDITIONS', reviewerRefs: [], criteriaEvidence: [],
      reviewedAt: '2026-09-12T12:00:00Z', expiresAt: null, conditions: '',
      notes: '', supersedesRef: null})).toThrow(/requires conditions/);
  });

  test('records revocation as a new immutable audit record', async () => {
    const service = certification(); const profile = certificationProfile();
    const request = service.request({targetRef: 'DataProduct:customer-360',
      targetRevision: '7'}, profile);
    const reviewed = await service.review(request.certificationId,
      {state: 'CERTIFIED'}, profile);
    const revoked = service.revoke(reviewed.certificationId, {notes: 'Withdrawn'});
    expect(revoked).toMatchObject({state: 'REVOKED',
      supersedesRef: reviewed.certificationId});
    expect(service.store.history(request.targetRef)).toHaveLength(3);
  });
});

describe('profile statistics and trust features', () => {
  test('validates exact/estimated/sampled/provider-reported provenance', () => {
    expect(validateProfileStatistic({metric: 'row_count', value: 20,
      method: 'exact', sampleSize: null, capturedAt: '2026-09-12T12:00:00Z',
      resourceRevision: 'r1', classification: 'INTERNAL'})).toMatchObject({
      method: 'exact', value: 20});
    expect(() => validateProfileStatistic({metric: 'row_count', value: 20,
      method: 'guessed', sampleSize: null, capturedAt: '2026-09-12T12:00:00Z',
      resourceRevision: 'r1', classification: 'INTERNAL'})).toThrow(/method/);
  });

  test('runs only a registered provider profiler and trims individual statistics', async () => {
    const registry = new DiscoveryProfilerRegistry();
    registry.register({provider: 'firebird', version: '5.0.4', profile: async () => [
      {metric: 'row_count', value: 20, method: 'exact', sampleSize: null,
        capturedAt: '2026-09-12T12:00:00Z', resourceRevision: 'r1',
        classification: 'INTERNAL'},
      {metric: 'common_value', value: 'private', method: 'sampled', sampleSize: 20,
        capturedAt: '2026-09-12T12:00:00Z', resourceRevision: 'r1',
        classification: 'RESTRICTED'}]});
    const service = new ProfileStatisticsService({profilers: registry,
      now: () => '2026-09-12T12:01:00Z'});
    const policy = {canProfile: () => true,
      admitStatistic: (_target, item) => item.classification !== 'RESTRICTED',
      canViewProfile: () => true};
    const result = await service.profile({provider: 'firebird',
      targetRef: mapping().targetRef, resourceRevision: 'r1', revisionId: 'p1'},
    {security: policy});
    expect(result).toMatchObject({provider: 'firebird', providerVersion: '5.0.4'});
    expect(result.statistics).toHaveLength(1);
    expect(service.history(mapping().targetRef, {security: policy})).toHaveLength(1);
  });

  test('implements exact trust, quality, popularity and documentation formulas', () => {
    const document = discoveryDocument();
    const authority = new DiscoveryTrustFeatureAuthority({evidenceResolver: () =>
      trustSource()});
    const result = authority.evaluate(document, {security: searchSecurity()});
    expect(result.components).toMatchObject({trust: 1, quality: 1, freshness: 1,
      usage: 1, context: 1, lineage: 1, business: 1, documentation: 1});
    expect(result.tieBreak).toEqual({certification: 1, quality: 1,
      freshness: 1, usage: 1});
  });

  test('implements SLA freshness with one-target default grace', () => {
    const now = Date.parse('2026-09-12T12:00:00Z');
    expect(discoveryFreshnessScore({observedAt: '2026-09-12T11:00:00Z',
      targetSeconds: 3600, now})).toBe(1);
    expect(discoveryFreshnessScore({observedAt: '2026-09-12T10:30:00Z',
      targetSeconds: 3600, now})).toBeCloseTo(0.5, 12);
    expect(discoveryFreshnessScore({observedAt: '2026-09-12T10:00:00Z',
      targetSeconds: 3600, now})).toBe(0);
  });

  test.each([
    ['CERTIFIED', 1], ['CERTIFIED_WITH_CONDITIONS', 0.85], ['IN_REVIEW', 0.6],
    ['UNREVIEWED', 0.5], ['EXPIRED', 0.4], ['REJECTED', 0.2], ['REVOKED', 0],
  ])('maps certification %s to its published trust score', (state, expected) => {
    const authority = new DiscoveryTrustFeatureAuthority({evidenceResolver: () =>
      trustSource({certificationState: state})});
    expect(authority.evaluate(discoveryDocument(), {security: searchSecurity()})
      .components.trust).toBe(expected);
  });

  test.each([
    ['CRITICAL_FAILURE', 0], ['CRITICAL_UNKNOWN_STALE', 0.4],
    ['NONCRITICAL_FAILURE', 0.7], ['PASS', 1], ['NOT_DEFINED', 0.5],
  ])('maps quality %s to its published quality score', (state, expected) => {
    const authority = new DiscoveryTrustFeatureAuthority({evidenceResolver: () =>
      trustSource({qualityState: state})});
    expect(authority.evaluate(discoveryDocument(), {security: searchSecurity()})
      .components.quality).toBe(expected);
  });

  test('uses neutral nulls and no explanation for denied feature signals', () => {
    const authority = new DiscoveryTrustFeatureAuthority({evidenceResolver: () =>
      trustSource()});
    const result = authority.evaluate(discoveryDocument(), {security:
      searchSecurity({admitSignal: () => false})});
    expect(Object.values(result.components).every((item) => item === null)).toBe(true);
    expect(result.explanation).toEqual([]);
  });

  test('applies documented quality defaults and critical penalty evidence', () => {
    const item = discoveryDocument();
    const authority = new DiscoveryTrustFeatureAuthority({evidenceResolver: () =>
      trustSource({certificationState: null, qualityState: 'CRITICAL_FAILURE',
        weightedUsageEvents: null})});
    const ranked = new DiscoveryRankingEngine({featureAuthority: authority}).rank([{
      document: item, lexical: 1, semantic: null, business: null, graph: null,
      exactVisibleName: false, exactVisibleQualifiedName: false,
      exactBusinessTerm: false}], {query: {}, security: searchSecurity()});
    expect(ranked[0].explanation.componentScores).toMatchObject({trust: null,
      quality: 0, usage: null});
    expect(ranked[0].explanation.penalties).toEqual({
      critical_quality_failure: -0.2});
  });
});

describe('Business Knowledge search integration', () => {
  test('expands approved terms through the real search authority', async () => {
    const assets = new DiscoveryKnowledgeAssetAuthority({projectAssets:
      new ProjectAssets()});
    const knowledge = new BusinessKnowledgeService({assets});
    knowledge.register('BusinessTerm', term({name: 'Lifetime Value',
      synonyms: ['LTV']}));
    const item = discoveryDocument({name: 'CUSTOMER_LEDGER',
      searchText: 'ledger entries', businessTerms: []});
    const backend = new InMemoryDiscoveryIndexBackend();
    await backend.bulkRevision({revision: 'revision-1', documents: [item]});
    const service = new DiscoverySearchService({indexBackend: backend,
      rankingEngine: new DiscoveryRankingEngine({featureAuthority:
        new DiscoveryTrustFeatureAuthority({evidenceResolver: () => trustSource()})}),
      businessSource: knowledge,
      cursorAuthority: new InMemoryDiscoveryCursorAuthority({
        tokenFactory: () => 'opaque'}), queryIdFactory: () => 'query-1'});
    const result = await service.search({text: 'LTV', mode: 'BUSINESS_TERM'},
      {security: searchSecurity()});
    expect(result.results).toHaveLength(1);
    expect(result.results[0].name).toBe('CUSTOMER_LEDGER');
    expect(result.results[0].explanation.candidateEvidence).toContainEqual({
      source: 'business', termId: 'customer', termName: 'Lifetime Value',
      relationship: 'represents', exact: false, deprecatedTerm: false});
  });
});
