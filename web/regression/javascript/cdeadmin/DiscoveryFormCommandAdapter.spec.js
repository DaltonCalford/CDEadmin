/////////////////////////////////////////////////////////////
// Discovery contract-form command boundary regression tests.
/////////////////////////////////////////////////////////////

import {adaptDiscoveryFormCommandArguments,
  secureDiscoveryFormCommandArguments} from
  'sources/cdeadmin_ui/modules/discovery_intelligence';

const rankingContent = {schemaVersion: 1, profileId: 'ranking:one', name: 'One',
  weights: {lexical: .22, semantic: .2, business: .12, trust: .1,
    quality: .08, freshness: .07, usage: .07, context: .05,
    lineage: .04, documentation: .05},
  boosts: {exact_visible_qualified_name: .12, exact_business_term: .08,
    active_certified_data_product: .06},
  penalties: {deprecated: -.3, retired_archived: -.45,
    critical_quality_failure: -.2, freshness_sla_breach: -.15,
    no_owner_steward: -.03, unstable_breaking_schema: -.08},
  publishedRevision: 'ranking:one@1', status: 'draft'};

function rankingValues() {
  return {lexical: .22, semantic: .2, business: .12, trust: .1,
    quality: .08, freshness: .07, usage: .07, context: .05,
    lineage: .04, documentation: .05, exact_name: .12, exact_term: .08,
    cert_product: .06, deprecated: -.3, retired: -.45,
    critical_quality: -.2, freshness_breach: -.15, no_owner: -.03,
    unstable_schema: -.08};
}

describe('Discovery form command adapter', () => {
  test('removes transient secrets and retains only an explicit credential reference',
    () => {
      const secured = secureDiscoveryFormCommandArguments({
        formId: 'discovery.index_source',
        values: {name: 'Provider', credential: 'vault://discovery/provider'},
        persistedValues: {name: 'Provider'}, secretFieldIds: ['credential']});
      expect(secured.values).toEqual({name: 'Provider',
        credentialRef: 'vault://discovery/provider'});
      expect(JSON.stringify(secured)).not.toContain('"credential"');
    });

  test('maps quick and advanced forms to exact search query fields', () => {
    expect(adaptDiscoveryFormCommandArguments('discovery.search.run', {
      formId: 'discovery.quick_search', values: {text: 'customer', mode: 'QUICK',
        scope: ['firebird'], ranking: 'Balanced'}}, {
      discoveryFormCommandDefaults: {scopeQueryField: 'providers'}})).toEqual({
      input: {text: 'customer', mode: 'QUICK', providers: ['firebird'],
        rankingProfile: 'Balanced'}, profile: undefined});
    expect(adaptDiscoveryFormCommandArguments('discovery.search.run', {
      formId: 'discovery.advanced_search', values: {builder: '',
        entity_classes: ['LIVE_RESOURCE'], providers: ['firebird'], domains: [],
        access: ['QUERY'], ranking: 'Balanced', sort: 'usage'}}, {}))
      .toEqual({input: {text: '', mode: 'ADVANCED_FACETED',
        entityClasses: ['LIVE_RESOURCE'], providers: ['firebird'], domains: [],
        accessState: ['QUERY'], rankingProfile: 'Balanced', sort: 'usage'},
      profile: undefined});
  });

  test('requires canonical context rather than fabricating saved-asset identity',
    () => {
      expect(() => adaptDiscoveryFormCommandArguments('discovery.search.save', {
        formId: 'discovery.saved_search', values: {name: 'Customers',
          scope: 'private', description: 'Customer records'}}, {})).toThrow(
        'requires explicit content command context');
      const content = {schemaVersion: 1, savedSearchId: 'saved:one', name: 'Old',
        description: '', scope: 'private', ownerRef: 'user:one', query: {
          text: 'customer'}, filters: {}, rankingProfileRef: null,
        displayColumns: [], searchScope: {}, version: '1'};
      expect(adaptDiscoveryFormCommandArguments('discovery.search.save', {
        formId: 'discovery.saved_search', values: {name: 'Customers',
          scope: 'team', description: 'Customer records'}}, {
        discoveryFormCommandDefaults: {projectId: 'project:one', content,
          assetId: 'asset:saved', expectedVersion: 2}})).toMatchObject({
        projectId: 'project:one', assetId: 'asset:saved', expectedVersion: 2,
        content: {savedSearchId: 'saved:one', name: 'Customers', scope: 'team'}});
    });

  test('maps all ranking controls to exact provider-neutral profile keys', () => {
    const result = adaptDiscoveryFormCommandArguments(
      'discovery.ranking.publish', {formId: 'discovery.ranking_profile',
        values: rankingValues()}, {discoveryFormCommandDefaults: {
        projectId: 'project:one', content: rankingContent}});
    expect(result.content.weights).toEqual(rankingContent.weights);
    expect(result.content.boosts).toEqual(rankingContent.boosts);
    expect(result.content.penalties).toEqual(rankingContent.penalties);
  });

  test('maps index-source controls and never stores a raw credential', () => {
    const content = {schemaVersion: 1, sourceId: 'source:firebird', name: 'Old',
      type: 'provider_metadata', scope: [], enabled: true,
      mode: 'INCREMENTAL_POLL', schedule: '*/5 * * * *', mandatory: false,
      credentialRef: null, respectProviderVisibility: true, version: '1'};
    const secured = secureDiscoveryFormCommandArguments({
      formId: 'discovery.index_source', values: {name: 'Firebird',
        type: 'provider_metadata', scope: ['resource:one'], enabled: true,
        mode: 'INCREMENTAL_POLL', schedule: '*/10 * * * *', mandatory: true,
        credential: 'vault://firebird/metadata', respect_provider_visibility: true},
      persistedValues: {name: 'Firebird', type: 'provider_metadata',
        scope: ['resource:one'], enabled: true, mode: 'INCREMENTAL_POLL',
        schedule: '*/10 * * * *', mandatory: true,
        respect_provider_visibility: true}, secretFieldIds: ['credential']});
    const result = adaptDiscoveryFormCommandArguments(
      'discovery.index.source.update', secured, {
        discoveryFormCommandDefaults: {projectId: 'project:one', content,
          assetId: 'asset:source', expectedVersion: 1,
          expectedConfigurationRevision: 3}});
    expect(result).toMatchObject({projectId: 'project:one',
      expectedConfigurationRevision: 3, content: {sourceId: 'source:firebird',
        credentialRef: 'vault://firebird/metadata', scope: ['resource:one']}});
    expect(JSON.stringify(result)).not.toContain('"credential"');
  });

  test('distinguishes governed curation from zero-result resolution', () => {
    expect(adaptDiscoveryFormCommandArguments('discovery.curation.resolve', {
      formId: 'discovery.curation_resolution', values: {item: 'item:one',
        action: 'replace_description', value: 'Current description',
        evidence: 'evidence:one'}}, {discoveryFormCommandDefaults: {
      actorRef: 'user:curator'}})).toEqual({itemId: 'item:one',
      action: 'replace_description', value: 'Current description',
      actorRef: 'user:curator', reason: 'evidence:one',
      sourceEvidenceRefs: ['evidence:one']});
    expect(adaptDiscoveryFormCommandArguments('discovery.curation.resolve', {
      formId: 'discovery.zero_result_resolution', values: {query: 'custmer',
        frequency: 12, resolution: 'add_synonym', target: 'term:customer',
        note: 'Common spelling error.'}}, {discoveryFormCommandDefaults: {
      actorRef: 'user:curator', sourceEvidenceRefs: ['analytics:one']}}))
      .toEqual({zeroResult: {query: 'custmer', frequency: 12,
        action: 'add_synonym', targetRef: 'term:customer',
        actorRef: 'user:curator', note: 'Common spelling error.',
        sourceEvidenceRefs: ['analytics:one']}});
  });

  test('maps access, visibility and feedback identities explicitly', () => {
    const access = adaptDiscoveryFormCommandArguments(
      'discovery.access.request.submit', {formId: 'discovery.access_request',
        values: {target: 'resource:one', access: ['QUERY'],
          environment: 'production', reason: 'Reconciliation', duration: 'P7D',
          project: 'project:one'}}, {discoveryFormCommandDefaults: {
        requester: 'user:analyst'}});
    expect(access.input).toMatchObject({requester: 'user:analyst',
      targetRef: 'resource:one', requestedAccess: ['QUERY']});
    const visibility = adaptDiscoveryFormCommandArguments(
      'discovery.visibility.test', {formId: 'discovery.visibility_test',
        values: {principal: 'user:reader', query: 'customer',
          ranking: 'Balanced'}}, {discoveryFormCommandDefaults: {
        profile: rankingContent}});
    expect(visibility.input).toMatchObject({principalRef: 'user:reader',
      query: {text: 'customer', mode: 'QUICK'}, profile: rankingContent});
    const feedback = adaptDiscoveryFormCommandArguments(
      'discovery.search.feedback', {formId: 'discovery.search_feedback',
        values: {result: 'resource:one', type: 'Useful', note: 'Relevant'}},
      {discoveryFormCommandDefaults: {actorScope: {actorKey: 'opaque:one',
        teamKeys: [], organizationKey: null}, queryRef: 'query:one'}});
    expect(feedback.input).toMatchObject({resultRef: 'resource:one',
      type: 'Useful', queryRef: 'query:one'});
  });
});
