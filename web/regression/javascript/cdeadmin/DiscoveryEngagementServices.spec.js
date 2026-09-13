/////////////////////////////////////////////////////////////
// Discovery engagement, usage, recommendation and privacy gates.
/////////////////////////////////////////////////////////////

import {
  DISCOVERY_FEEDBACK_TYPES, DISCOVERY_RECOMMENDATION_TYPES,
  DiscoveryCollectionService, DiscoveryEngagementAssetAuthority,
  DiscoveryRecommendationService, DiscoverySavedSearchService,
  DiscoverySearchAnalyticsService, DiscoverySearchFeedbackService,
  DiscoveryUsageSignalService, InMemoryDiscoveryFeedbackStore,
  InMemoryDiscoveryRecommendationIndex, InMemoryDiscoverySearchAnalyticsStore,
  InMemoryDiscoveryUsageStore, discoveryEngagementAssetRequest,
  validateDiscoveryCollection, validateRecommendationEvidence,
  validateSavedSearch, validateSearchAnalyticsPolicy, validateUsageEvent,
  validateUsagePolicy,
} from 'sources/cdeadmin_ui/modules/discovery_intelligence';

const NOW = Date.parse('2026-09-12T12:00:00.000Z');
const SOURCE = 'cde-resource://firebird/local/app/table/orders';
const TARGET = 'cde-resource://firebird/local/app/table/customer';

function actorAuthority() {
  return {protect: (scope) => ({actorKey: `opaque:${scope.actorRef}`,
    teamKeys: (scope.teamRefs ?? []).map((item) => `opaque:${item}`),
    organizationKey: scope.organizationRef ? `opaque:${scope.organizationRef}` : null})};
}

function document(reference, overrides={}) {
  return {canonicalRef: reference, name: reference.split('/').at(-1),
    ownerRefs: ['team:data'], qualitySignals: {state: 'PASS'},
    deprecationState: null, ...overrides};
}

function documents(overrides={}) {
  const values = new Map([[SOURCE, document(SOURCE)], [TARGET, document(TARGET)],
    ['asset:third', document('asset:third', {ownerRefs: [],
      qualitySignals: {state: 'FAIL'}, deprecationState: 'DEPRECATED'})]]);
  return {resolve: (reference) => values.get(reference) ?? null, ...overrides};
}

function usageSecurity(overrides={}) {
  return {admitUsage: () => true, admitUsageSurface: () => true, ...overrides};
}

function recommendationSecurity(overrides={}) {
  return {admitDocument: () => true, admitRecommendation: () => true,
    admitSignal: () => true, ...overrides};
}

function usageEvent(id, actor, overrides={}) {
  return {schemaVersion: 1, eventId: id,
    actorScope: {actorRef: actor, teamRefs: ['team:data']},
    time: new Date(NOW - 1000).toISOString(),
    canonicalResourceRefs: [SOURCE], accessSurfaceRefs: ['surface:firebird'],
    consumerType: 'query_execution', consumerRef: null, success: true,
    durationMs: 10, rows: 1, bytes: 8, ...overrides};
}

class ProjectAssets {
  constructor() { this.items = new Map(); this.history = new Map(); }
  async saveAsset(projectId, assetId, request) {
    const key = `${projectId}:${assetId}`; const current = this.items.get(key);
    if((current?.version ?? 0) !== request.expected_version) throw new Error('conflict');
    const value = {...request, project_id: projectId, asset_id: assetId,
      version: (current?.version ?? 0) + 1};
    this.items.set(key, value); this.history.set(key,
      [...(this.history.get(key) ?? []), value]); return value;
  }
  async asset(projectId, assetId) { return this.items.get(`${projectId}:${assetId}`); }
  async revisions(projectId, assetId) {
    return this.history.get(`${projectId}:${assetId}`) ?? [];
  }
  async deleteAsset(projectId, assetId, version) {
    const key = `${projectId}:${assetId}`;
    if(this.items.get(key)?.version !== version) throw new Error('conflict');
    return this.items.delete(key);
  }
}

function savedSearch(overrides={}) {
  return {schemaVersion: 1, savedSearchId: 'customer-search', name: 'Customers',
    description: 'Current customer assets.', scope: 'private',
    ownerRef: 'user:one', query: {text: 'customer', mode: 'QUICK'},
    filters: {providers: ['firebird']}, rankingProfileRef: null,
    displayColumns: ['name', 'provider'], searchScope: {domains: ['sales']},
    version: '1', ...overrides};
}

function collection(overrides={}) {
  return {schemaVersion: 1, collectionId: 'favorites', name: 'Favorites',
    description: 'Useful assets.', scope: 'team', ownerRef: 'user:one',
    items: [SOURCE, TARGET], version: '1', ...overrides};
}

function engagementSecurity(overrides={}) {
  return {admitEngagementAsset: () => true, admitDocument: () => true,
    ...overrides};
}

describe('Discovery engagement contracts', () => {
  test('validates every required feedback and recommendation type', () => {
    expect(DISCOVERY_FEEDBACK_TYPES).toHaveLength(7);
    expect(DISCOVERY_RECOMMENDATION_TYPES).toHaveLength(11);
    expect(new Set(DISCOVERY_RECOMMENDATION_TYPES).size).toBe(11);
  });

  test('deduplicates canonical aliases and surfaces within one usage event', () => {
    const value = validateUsageEvent({...usageEvent('event', 'one'),
      actorScope: {actorKey: 'opaque:one', teamKeys: [], organizationKey: null},
      canonicalResourceRefs: [SOURCE, SOURCE],
      accessSurfaceRefs: ['surface:a', 'surface:a']});
    expect(value.canonicalResourceRefs).toEqual([SOURCE]);
    expect(value.accessSurfaceRefs).toEqual(['surface:a']);
  });

  test('uses exact popularity defaults without inventing weights', () => {
    const policy = validateUsagePolicy({});
    expect(policy).toMatchObject({popularitySaturation: 1000,
      minDistinctUsers: 3, eventWeights: {query_execution: 1,
        etl_cdc_production_use: 1.5, access_request: 0,
        project_reference: 0}});
  });

  test('rejects unsupported policy, asset and evidence fields', () => {
    expect(() => validateUsagePolicy({madeUp: true})).toThrow(/unsupported/);
    expect(() => validateSavedSearch({...savedSearch(), copiedResults: []}))
      .toThrow(/copiedResults/);
    expect(() => validateDiscoveryCollection({...collection(), password: 'bad'}))
      .toThrow(/unsupported field password|Raw credential/);
  });

  test('requires explicit relationship/deprecation evidence for replacement', () => {
    expect(() => validateRecommendationEvidence({schemaVersion: 1,
      evidenceId: 'replacement', type: 'recommended_replacement',
      sourceRef: SOURCE, targetRef: TARGET, score: 1, reason: 'Similar.',
      evidenceRefs: [], evidenceKind: 'semantic', explicitEvidence: false,
      observedAt: new Date(NOW).toISOString(), expiresAt: null,
      distinctActors: null})).toThrow(/explicit relationship\/deprecation/);
  });

  test('enforces analytics privacy policy ranges', () => {
    expect(validateSearchAnalyticsPolicy({})).toMatchObject({
      rawQueryRetentionDays: 0, anonymizeActors: true,
      minimumAggregationThreshold: 3, exportAllowed: false});
    expect(() => validateSearchAnalyticsPolicy({minimumAggregationThreshold: 0}))
      .toThrow(/minimum threshold/);
  });
});

describe('DiscoveryUsageSignalService', () => {
  function service(policy={}) {
    return new DiscoveryUsageSignalService({store: new InMemoryDiscoveryUsageStore(),
      actorAuthority: actorAuthority(), policy: {windows: ['24h', '30d', 'all'],
        ...policy}, now: () => NOW});
  }

  test('requires an actor privacy authority', () => {
    expect(() => new DiscoveryUsageSignalService({
      store: new InMemoryDiscoveryUsageStore()})).toThrow(/protect authority/);
  });

  test('records immutable, protected events and rejects replay', () => {
    const usage = service(); const value = usage.record(usageEvent('one', 'alice'));
    expect(value.actorScope.actorKey).toBe('opaque:alice');
    expect(Object.isFrozen(value)).toBe(true);
    expect(() => usage.record(usageEvent('one', 'alice'))).toThrow(/already exists/);
  });

  test('aggregates canonical counts and retains separate surface counters', () => {
    const usage = service({minDistinctUsers: 2});
    usage.record(usageEvent('one', 'alice', {canonicalResourceRefs: [SOURCE,
      SOURCE], accessSurfaceRefs: ['surface:firebird', 'surface:postgres']}));
    usage.record(usageEvent('two', 'bob'));
    const [result] = usage.aggregate({window: '30d', security: usageSecurity()});
    expect(result).toMatchObject({resourceRef: SOURCE, eventCount: 2,
      successCount: 2, weightedEvents: 2, distinctUsers: 2});
    expect(result.surfaceUsage).toEqual([
      {surfaceRef: 'surface:firebird', eventCount: 2, successCount: 2},
      {surfaceRef: 'surface:postgres', eventCount: 1, successCount: 1},
    ]);
  });

  test('applies exact success/failure and signal weights', () => {
    const usage = service({minDistinctUsers: 1});
    usage.record(usageEvent('failed', 'alice', {success: false}));
    usage.record(usageEvent('etl', 'alice', {
      consumerType: 'etl_cdc_production_use'}));
    usage.record(usageEvent('request', 'alice', {consumerType: 'access_request'}));
    expect(usage.aggregate({window: '30d', security: usageSecurity()})[0]
      .weightedEvents).toBeCloseTo(1.55);
  });

  test('suppresses distinct identities below privacy threshold', () => {
    const usage = service({minDistinctUsers: 3, showIndividualUsage: false});
    usage.record(usageEvent('one', 'alice'));
    const result = usage.aggregate({window: '30d', security: usageSecurity()})[0];
    expect(result.distinctUsers).toBeNull();
    expect(result.actors).toBeNull();
  });

  test('security removes canonical resources and individual surfaces', () => {
    const usage = service({minDistinctUsers: 1});
    usage.record(usageEvent('one', 'alice'));
    expect(usage.aggregate({window: '30d', security: usageSecurity({
      admitUsage: () => false})})).toEqual([]);
    expect(usage.aggregate({window: '30d', security: usageSecurity({
      admitUsageSurface: () => false})})[0].surfaceUsage).toEqual([]);
  });

  test('honors enabled windows and excludes old events', () => {
    const usage = service();
    usage.record(usageEvent('old', 'alice', {
      time: new Date(NOW - 31 * 24 * 60 * 60 * 1000).toISOString()}));
    expect(usage.aggregate({window: '30d', security: usageSecurity()})).toEqual([]);
    expect(() => usage.aggregate({window: '7d', security: usageSecurity()}))
      .toThrow(/not enabled/);
  });

  test('computes canonical co-occurrence without duplicate aliases', () => {
    const usage = service();
    usage.record(usageEvent('one', 'alice', {
      canonicalResourceRefs: [SOURCE, SOURCE, TARGET]}));
    expect(usage.cooccurrence(SOURCE, {window: '30d'})).toEqual([{
      targetRef: TARGET, eventCount: 1, distinctActors: 1,
      lastObservedAt: new Date(NOW - 1000).toISOString()}]);
  });
});

describe('DiscoveryRecommendationService', () => {
  function services(policy={}, documentAuthority=documents()) {
    const usage = new DiscoveryUsageSignalService({
      store: new InMemoryDiscoveryUsageStore(), actorAuthority: actorAuthority(),
      policy: {windows: ['30d', 'all']}, now: () => NOW});
    const index = new InMemoryDiscoveryRecommendationIndex();
    return {usage, index, recommendations: new DiscoveryRecommendationService({
      index, usage, documentAuthority, policy,
      now: () => NOW})};
  }

  function evidence(type, overrides={}) {
    return {schemaVersion: 1, evidenceId: `${type}:one`, type, sourceRef: SOURCE,
      targetRef: TARGET, score: 0.8, reason: `Reason for ${type}.`,
      evidenceRefs: ['evidence:one'], evidenceKind: type ===
        'recommended_replacement' ? 'deprecation' : 'relationship',
      explicitEvidence: type === 'recommended_replacement',
      observedAt: new Date(NOW - 1000).toISOString(), expiresAt: null,
      distinctActors: type === 'used_by_team' ? 3 : null, ...overrides};
  }

  test.each(DISCOVERY_RECOMMENDATION_TYPES.filter((type) =>
    type !== 'frequently_used_together'))('publishes and returns %s evidence',
    (type) => {
      const {index, recommendations} = services(); index.publish([evidence(type)]);
      expect(recommendations.recommendations(SOURCE,
        {security: recommendationSecurity()})[0]).toMatchObject({type,
        items: [{reason: `Reason for ${type}.`, targetRef: TARGET}]});
    });

  test('generates frequently-used-together from canonical usage', () => {
    const {usage, recommendations} = services({minDistinctUsers: 2});
    usage.record(usageEvent('one', 'alice', {
      canonicalResourceRefs: [SOURCE, TARGET]}));
    usage.record(usageEvent('two', 'bob', {
      canonicalResourceRefs: [SOURCE, TARGET]}));
    expect(recommendations.recommendations(SOURCE,
      {security: recommendationSecurity()})).toMatchObject([{
      type: 'frequently_used_together', items: [{targetRef: TARGET,
        distinctActors: 2}]}]);
  });

  test('suppresses team/co-occurrence evidence below the privacy threshold', () => {
    const {usage, index, recommendations} = services({minDistinctUsers: 3});
    index.publish([evidence('used_by_team', {distinctActors: 2})]);
    usage.record(usageEvent('one', 'alice', {
      canonicalResourceRefs: [SOURCE, TARGET]}));
    expect(recommendations.recommendations(SOURCE,
      {security: recommendationSecurity()})).toEqual([]);
  });

  test('filters security-hidden sources, targets, evidence and signals first', () => {
    const {index, recommendations} = services();
    index.publish([evidence('related_by_lineage')]);
    expect(recommendations.recommendations(SOURCE, {security:
      recommendationSecurity({admitDocument: (item) =>
        item.canonicalRef !== TARGET})})).toEqual([]);
    expect(recommendations.recommendations(SOURCE, {security:
      recommendationSecurity({admitRecommendation: () => false})})).toEqual([]);
    expect(recommendations.recommendations(SOURCE, {security:
      recommendationSecurity({admitSignal: () => false})})).toEqual([]);
  });

  test('suppresses native system catalogs from recommendations', () => {
    const baseline = documents();
    const targetCatalog = {resolve: (reference) => {
      const value = baseline.resolve(reference);
      return reference === TARGET ? {...value, nativeKind: 'system_table',
        nativeMetadataSummary: {systemCatalog: true}} : value;
    }};
    const {index, recommendations} = services({}, targetCatalog);
    index.publish([evidence('related_by_lineage')]);
    expect(recommendations.recommendations(SOURCE,
      {security: recommendationSecurity()})).toEqual([]);
    const sourceCatalog = {resolve: (reference) => {
      const value = baseline.resolve(reference);
      return reference === SOURCE ? {...value, nativeKind: 'system_table'} : value;
    }};
    const source = services({}, sourceCatalog);
    source.index.publish([evidence('related_by_lineage')]);
    expect(source.recommendations.recommendations(SOURCE,
      {security: recommendationSecurity()})).toEqual([]);
  });

  test('removes stale/expired evidence and caps each section', () => {
    const {index, recommendations} = services({maximumPerType: 1, staleDays: 2});
    index.publish([evidence('same_domain'), evidence('same_domain', {
      evidenceId: 'stale', targetRef: 'asset:third', score: 1,
      observedAt: new Date(NOW - 3 * 24 * 60 * 60 * 1000).toISOString()}),
    evidence('same_domain', {evidenceId: 'expired', targetRef: 'asset:third',
      expiresAt: new Date(NOW - 1).toISOString()})]);
    expect(recommendations.recommendations(SOURCE,
      {security: recommendationSecurity()})[0].items).toHaveLength(1);
  });

  test('index replacement is atomic and evidence identities cannot move sources', () => {
    const {index} = services(); index.publish([evidence('same_domain')]);
    expect(() => index.publish([evidence('same_domain', {sourceRef: TARGET,
      targetRef: 'asset:third'})]))
      .toThrow(/cannot be reassigned/);
    expect(index.publish([], {replace: true})).toEqual({revision: 2, count: 0});
  });
});

describe('Saved searches and collections', () => {
  function services() {
    const assets = new DiscoveryEngagementAssetAuthority({projectAssets:
      new ProjectAssets()});
    return {assets, saved: new DiscoverySavedSearchService({assets,
      search: {search: jest.fn(async (query, context) => ({query, context}))},
      rankingProfiles: {resolve: (ref) => ref === 'balanced' ?
        {profileId: 'balanced'} : null}}), collections:
      new DiscoveryCollectionService({assets, referenceAuthority: documents()})};
  }

  test('persists definitions and references, never result copies', async () => {
    const request = discoveryEngagementAssetRequest('SavedSearch', savedSearch());
    expect(request).toMatchObject({asset_type: 'cdeadmin.discovery.saved_search',
      source_control_eligible: false, content: {query: {text: 'customer'}}});
    expect(request.content.results).toBeUndefined();
    const {saved} = services();
    await expect(saved.save('project', savedSearch())).resolves.toMatchObject({
      version: 1, assetKind: 'SavedSearch'});
  });

  test('reruns a saved definition with current security and ranking profile', async () => {
    const {saved} = services();
    const asset = await saved.save('project', savedSearch({
      rankingProfileRef: 'balanced'}));
    const result = await saved.run('project', asset.asset_id,
      {security: engagementSecurity(), overrides: {filters: {tags: ['gold']}}});
    expect(result.query).toMatchObject({text: 'customer', providers: ['firebird'],
      domains: ['sales'], tags: ['gold'], rankingProfile: 'balanced'});
    expect(result.context.security).toBeDefined();
  });

  test('fails closed if a saved ranking profile cannot be resolved', async () => {
    const {saved} = services();
    const asset = await saved.save('project', savedSearch({
      rankingProfileRef: 'missing'}));
    await expect(saved.run('project', asset.asset_id,
      {security: engagementSecurity()})).rejects.toThrow(/was not found/);
  });

  test('collection stores refs only and revalidates every item at read time', async () => {
    const {collections} = services();
    const asset = await collections.save('project', collection());
    const result = await collections.resolve('project', asset.asset_id,
      {security: engagementSecurity({admitDocument: (item) =>
        item.canonicalRef === SOURCE})});
    expect(result.content.items).toEqual([SOURCE, TARGET]);
    expect(result.visibleItems.map((item) => item.reference)).toEqual([SOURCE]);
  });

  test('sharing scope never bypasses asset read authorization', async () => {
    const {assets, collections} = services();
    const asset = await collections.save('project', collection({
      scope: 'organization'}));
    await expect(assets.get('project', asset.asset_id, {
      kind: 'DiscoveryCollection', security: engagementSecurity({
        admitEngagementAsset: () => false})})).rejects.toThrow(/access denied/);
  });

  test('maintains revisions and authorizes exact asset before delete', async () => {
    const {assets, saved} = services();
    const first = await saved.save('project', savedSearch());
    await saved.save('project', savedSearch({description: 'Updated.'}),
      {assetId: first.asset_id, expectedVersion: 1});
    await expect(assets.revisions('project', first.asset_id,
      {kind: 'SavedSearch', security: engagementSecurity()})).resolves.toHaveLength(2);
    await expect(assets.remove('project', first.asset_id, 2, {security:
      engagementSecurity({admitEngagementAsset: () => false})}))
      .rejects.toThrow(/delete denied/);
  });
});

describe('Search feedback', () => {
  function service(store=new InMemoryDiscoveryFeedbackStore()) {
    let id = 0; return {store, feedback: new DiscoverySearchFeedbackService({store,
      actorAuthority: actorAuthority(), now: () => new Date(NOW).toISOString(),
      idFactory: () => `feedback-${++id}`})};
  }

  test.each(DISCOVERY_FEEDBACK_TYPES)('records %s as append-only signal', (type) => {
    const {store, feedback} = service();
    const result = feedback.submit({actorScope: {actorRef: 'alice'},
      resultRef: TARGET, type, note: 'Review this.', queryRef: 'query:one'},
    {security: {admitDocumentRef: () => true,
      submitSearchFeedback: () => true}});
    expect(result.type).toBe(type); expect(store.all()).toHaveLength(1);
  });

  test('does not expose a metadata mutation API and denies hidden results', () => {
    const {store, feedback} = service();
    expect(feedback.mutateMetadata).toBeUndefined();
    expect(() => feedback.submit({actorScope: {actorRef: 'alice'},
      resultRef: TARGET, type: 'Useful'}, {security: {
      admitDocumentRef: () => false, submitSearchFeedback: () => true}}))
      .toThrow(/access denied/);
    expect(store.all()).toEqual([]);
  });
});

describe('Discovery search analytics privacy', () => {
  function service(policy={}) {
    return new DiscoverySearchAnalyticsService({
      store: new InMemoryDiscoverySearchAnalyticsStore(),
      actorAuthority: actorAuthority(), documentAuthority: documents(),
      policy: {minimumAggregationThreshold: 2, rawQueryRetentionDays: 30,
        adminAccess: true, exportAllowed: true, ...policy}, now: () => NOW});
  }

  function analyticsSecurity(overrides={}) {
    return {recordSearchAnalytics: () => true, admitDocument: () => true,
      mayViewSearchAnalytics: () => true, mayExportSearchAnalytics: () => true,
      mayAnalyzeSearchLogsWithAI: () => true, ...overrides};
  }

  function activity(id, actor, overrides={}) {
    return {schemaVersion: 1, activityId: id,
      actorScope: {actorRef: actor, teamRefs: ['team:data']}, queryId: `query:${id}`,
      queryText: 'Customer', occurredAt: new Date(NOW - 1000).toISOString(),
      resultCount: 2, selectedRefs: [TARGET],
      usefulSelectedAt: new Date(NOW).toISOString(), accessRequested: false,
      abandoned: false, confidence: 0.9, queryDurationMs: 20, ...overrides};
  }

  test('suppresses all metrics below the configured aggregation threshold', () => {
    const analytics = service({minimumAggregationThreshold: 3});
    analytics.record(activity('one', 'alice'), {security: analyticsSecurity()});
    expect(analytics.report({security: analyticsSecurity()})).toMatchObject({
      suppressed: true, searchCount: null, topTerms: [], popularAssets: []});
  });

  test('calculates all required journey rates and privacy-qualified terms', () => {
    const analytics = service();
    analytics.record(activity('one', 'alice'), {security: analyticsSecurity()});
    analytics.record(activity('two', 'bob', {resultCount: 0, selectedRefs: [],
      usefulSelectedAt: null, accessRequested: true, abandoned: true,
      confidence: 0.2}), {security: analyticsSecurity()});
    const report = analytics.report({security: analyticsSecurity()});
    expect(report).toMatchObject({suppressed: false, searchCount: 2,
      uniqueSearchers: 2, metrics: {zeroResultRate: 0.5,
        abandonmentRate: 0.5, selectionRate: 0.5,
        accessRequestConversionRate: 0.5, lowConfidenceQueries: 1},
      topTerms: [{term: 'customer', count: 2, zeroResults: 1}]});
    expect(report.metrics.timeToUsefulSelectionMs).toBe(1000);
  });

  test('never records or reports a security-hidden selected asset', () => {
    const analytics = service();
    const security = analyticsSecurity({admitDocument: (item) =>
      item.canonicalRef !== TARGET});
    analytics.record(activity('one', 'alice'), {security});
    analytics.record(activity('two', 'bob'), {security});
    const report = analytics.report({security});
    expect(report.popularAssets).toEqual([]);
    expect(report.metrics.selectionRate).toBe(0);
  });

  test('classifies visible selected assets from authoritative metadata', () => {
    const analytics = service();
    for(const [id, actor] of [['one', 'alice'], ['two', 'bob']]) {
      analytics.record(activity(id, actor, {selectedRefs: ['asset:third']}),
        {security: analyticsSecurity()});
    }
    const report = analytics.report({security: analyticsSecurity()});
    expect(report.deprecatedAssetsStillSelected).toHaveLength(1);
    expect(report.unownedPopularAssets).toHaveLength(1);
    expect(report.qualityFailingPopularAssets).toHaveLength(1);
  });

  test('discards raw queries when retention is disabled', () => {
    const analytics = service({rawQueryRetentionDays: 0});
    analytics.record(activity('one', 'alice'), {security: analyticsSecurity()});
    analytics.record(activity('two', 'bob'), {security: analyticsSecurity()});
    expect(analytics.report({security: analyticsSecurity()}).topTerms).toEqual([]);
  });

  test('purges expired raw query text without deleting the aggregate event', () => {
    const analytics = service({rawQueryRetentionDays: 1});
    analytics.record(activity('one', 'alice', {
      occurredAt: new Date(NOW - 2 * 24 * 60 * 60 * 1000).toISOString(),
      usefulSelectedAt: null}), {security: analyticsSecurity()});
    expect(analytics.purgeExpiredRawQueries()).toBe(1);
    expect(analytics.store.range()[0]).toMatchObject({queryText: null,
      activityId: 'one'});
  });

  test('requires both policy and permission for export', () => {
    const analytics = service();
    analytics.record(activity('one', 'alice'), {security: analyticsSecurity()});
    analytics.record(activity('two', 'bob'), {security: analyticsSecurity()});
    expect(analytics.export({security: analyticsSecurity()})).toMatchObject({
      schemaVersion: 1, policyVersion: '1', report: {searchCount: 2}});
    expect(() => analytics.export({security: analyticsSecurity({
      mayExportSearchAnalytics: () => false})})).toThrow(/export denied/);
  });

  test('requires AI Interface data policy in addition to local policy', () => {
    const disabled = service({aiAnalysisAllowed: false});
    expect(disabled.authorizeAIAnalysis({security: analyticsSecurity(),
      dataPolicy: {authorizeSearchLogAnalysis: () => true}})).toBe(false);
    const enabled = service({aiAnalysisAllowed: true});
    expect(enabled.authorizeAIAnalysis({security: analyticsSecurity(),
      dataPolicy: {authorizeSearchLogAnalysis: () => true}})).toBe(true);
    expect(enabled.authorizeAIAnalysis({security: analyticsSecurity(),
      dataPolicy: {authorizeSearchLogAnalysis: () => false}})).toBe(false);
  });

  test('separates recording, viewing and export permissions', () => {
    const analytics = service();
    expect(() => analytics.record(activity('one', 'alice'), {security:
      analyticsSecurity({recordSearchAnalytics: () => false})}))
      .toThrow(/recording denied/);
    expect(() => analytics.report({security: analyticsSecurity({
      mayViewSearchAnalytics: () => false})})).toThrow(/access denied/);
  });
});
