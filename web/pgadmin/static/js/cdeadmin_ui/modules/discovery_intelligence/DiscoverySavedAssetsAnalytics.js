/////////////////////////////////////////////////////////////
// Saved discovery assets, feedback and privacy-safe search analytics.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';
import {validateDiscoveryCollection, validateProtectedActorScope,
  validateSavedSearch, validateSearchAnalyticsEvent,
  validateSearchAnalyticsPolicy, validateSearchFeedback} from
  './DiscoveryEngagementContracts';

export const DISCOVERY_ENGAGEMENT_ASSET_TYPES = Object.freeze({
  SavedSearch: 'cdeadmin.discovery.saved_search',
  DiscoveryCollection: 'cdeadmin.discovery.collection',
});

function requireMethod(authority, method, label) {
  if(typeof authority?.[method] !== 'function') throw new TypeError(
    `${label} requires ${method} authority.`
  );
}

function filePart(value) { return String(value).replace(/[^a-zA-Z0-9._:-]/g, '-'); }

function validateEngagement(kind, input) {
  if(kind === 'SavedSearch') return validateSavedSearch(input);
  if(kind === 'DiscoveryCollection') return validateDiscoveryCollection(input);
  throw new TypeError(`Unknown Discovery engagement asset kind ${kind}.`);
}

function engagementIdentity(kind, content) {
  return kind === 'SavedSearch' ? content.savedSearchId : content.collectionId;
}

export function discoveryEngagementAssetRequest(kind, input, options={}) {
  const content = validateEngagement(kind, input);
  const expectedVersion = options.expectedVersion ?? 0;
  if(!Number.isInteger(expectedVersion) || expectedVersion < 0) throw new TypeError(
    'Discovery engagement asset expected version is invalid.'
  );
  const type = DISCOVERY_ENGAGEMENT_ASSET_TYPES[kind];
  const id = engagementIdentity(kind, content);
  const request = {asset_type: type, schema_name: type, schema_version: 1,
    name: platformValue(options.name ?? content.name,
      'Discovery engagement asset name', 256),
    path: platformValue(options.path ?? `discovery/${kind}/${filePart(id)}.json`,
      'Discovery engagement asset path', 1024), expected_version: expectedVersion,
    content, metadata: {moduleId: 'cdeadmin.discovery_intelligence',
      assetKind: kind, logicalId: id, sharingScope: content.scope,
      ownerRef: content.ownerRef}, dependency_references: kind === 'SavedSearch' ?
      [content.rankingProfileRef].filter(Boolean) :
      [...content.items], resource_bindings: kind === 'DiscoveryCollection' ?
      content.items.filter((item) => item.startsWith('cde-resource://')) : [],
    source_control_eligible: content.scope !== 'private', editor_capable: true,
    viewer_capable: true, validation_state: 'valid', validation_details: []};
  noRawSecrets(request, 'Discovery engagement asset request');
  return immutable(request);
}

function storedAsset(asset, expectedKind=null, {contentRequired=true}={}) {
  plainObject(asset, 'Stored Discovery engagement asset');
  noRawSecrets(asset, 'Stored Discovery engagement asset');
  const kind = Object.entries(DISCOVERY_ENGAGEMENT_ASSET_TYPES).find(
    ([, type]) => type === asset.asset_type)?.[0];
  if(!kind || (expectedKind && expectedKind !== kind) ||
      asset.schema_name !== DISCOVERY_ENGAGEMENT_ASSET_TYPES[kind] ||
      asset.schema_version !== 1) throw new TypeError(
    `Stored asset is not ${expectedKind ?? 'a Discovery engagement asset'}.`
  );
  return immutable({...asset, assetKind: kind,
    ...(contentRequired ? {content: validateEngagement(kind, asset.content)} : {})});
}

export class DiscoveryEngagementAssetAuthority {
  constructor({projectAssets}={}) {
    for(const method of ['saveAsset', 'asset', 'revisions', 'deleteAsset']) {
      requireMethod(projectAssets, method, 'Discovery engagement Project Asset service');
    }
    this.projectAssets = projectAssets;
  }

  async save(projectId, kind, input, options={}) {
    projectId = platformValue(projectId, 'Discovery engagement project ID');
    const request = discoveryEngagementAssetRequest(kind, input, options);
    const assetId = platformValue(options.assetId ??
      `${kind}:${request.metadata.logicalId}`, 'Discovery engagement asset ID');
    return storedAsset(await this.projectAssets.saveAsset(projectId, assetId,
      request), kind);
  }

  async get(projectId, assetId, {version=null, kind=null, security}={}) {
    requireMethod(security, 'admitEngagementAsset',
      'Discovery engagement asset security');
    const value = storedAsset(await this.projectAssets.asset(platformValue(projectId,
      'Discovery engagement project ID'), platformValue(assetId,
      'Discovery engagement asset ID'), version), kind);
    if(security.admitEngagementAsset(value.content, 'read') !== true) {
      throw new Error('Discovery engagement asset access denied.');
    }
    return value;
  }

  async revisions(projectId, assetId, {kind=null, security}={}) {
    requireMethod(security, 'admitEngagementAsset',
      'Discovery engagement asset security');
    const values = await this.projectAssets.revisions(platformValue(projectId,
      'Discovery engagement project ID'), platformValue(assetId,
      'Discovery engagement asset ID'));
    if(!Array.isArray(values)) throw new TypeError(
      'Discovery engagement revisions response must be an array.'
    );
    return immutable(values.map((item) => storedAsset(item, kind)).filter((item) =>
      security.admitEngagementAsset(item.content, 'history') === true).map(
      (item) => storedAsset(item, kind, {contentRequired: false})));
  }

  async remove(projectId, assetId, expectedVersion, {security}={}) {
    requireMethod(security, 'admitEngagementAsset',
      'Discovery engagement asset security');
    if(!Number.isInteger(expectedVersion) || expectedVersion < 1) throw new TypeError(
      'Discovery engagement delete version is invalid.'
    );
    projectId = platformValue(projectId, 'Discovery engagement project ID');
    assetId = platformValue(assetId, 'Discovery engagement asset ID');
    const asset = storedAsset(await this.projectAssets.asset(projectId, assetId));
    if(security.admitEngagementAsset(asset.content, 'delete') !== true) {
      throw new Error('Discovery engagement asset delete denied.');
    }
    return this.projectAssets.deleteAsset(projectId, assetId, expectedVersion);
  }
}

export class DiscoverySavedSearchService {
  constructor({assets, search, rankingProfiles=null}={}) {
    requireMethod(assets, 'save', 'Discovery saved search asset');
    requireMethod(assets, 'get', 'Discovery saved search asset');
    requireMethod(search, 'search', 'Discovery saved search runtime');
    if(rankingProfiles !== null) requireMethod(rankingProfiles, 'resolve',
      'Discovery ranking profile');
    this.assets = assets; this.search = search; this.rankingProfiles = rankingProfiles;
  }

  save(projectId, input, options={}) {
    return this.assets.save(projectId, 'SavedSearch', input, options);
  }

  async run(projectId, assetId, {security, overrides={}}={}) {
    plainObject(overrides, 'Discovery saved search overrides');
    const asset = await this.assets.get(projectId, assetId,
      {kind: 'SavedSearch', security});
    const saved = asset.content;
    const query = {...saved.searchScope, ...saved.query, ...saved.filters,
      ...(overrides.filters ?? {})};
    const profileRef = overrides.rankingProfileRef ?? saved.rankingProfileRef;
    let profile;
    if(profileRef) {
      if(!this.rankingProfiles) throw new TypeError(
        'Saved search ranking profile authority is unavailable.'
      );
      profile = this.rankingProfiles.resolve(profileRef);
      if(!profile) throw new TypeError(`Saved ranking profile ${profileRef} was not found.`);
      query.rankingProfile = profile.profileId;
    }
    noRawSecrets(query, 'Discovery saved search execution');
    return this.search.search({...query, ...overrides.query}, {security, profile});
  }
}

export class DiscoveryCollectionService {
  constructor({assets, referenceAuthority}={}) {
    requireMethod(assets, 'save', 'Discovery collection asset');
    requireMethod(assets, 'get', 'Discovery collection asset');
    requireMethod(referenceAuthority, 'resolve', 'Discovery collection reference');
    this.assets = assets; this.referenceAuthority = referenceAuthority;
  }

  save(projectId, input, options={}) {
    return this.assets.save(projectId, 'DiscoveryCollection', input, options);
  }

  async resolve(projectId, assetId, {security}={}) {
    requireMethod(security, 'admitDocument', 'Discovery collection security');
    const asset = await this.assets.get(projectId, assetId,
      {kind: 'DiscoveryCollection', security});
    const visibleItems = [];
    for(const reference of asset.content.items) {
      const value = this.referenceAuthority.resolve(reference);
      if(value && security.admitDocument(value) === true) {
        visibleItems.push({reference, value});
      }
    }
    return immutable({...asset, visibleItems});
  }
}

export class InMemoryDiscoveryFeedbackStore {
  constructor() { this.items = new Map(); }
  append(input) {
    const value = validateSearchFeedback(input);
    if(this.items.has(value.feedbackId)) throw new TypeError(
      `Discovery feedback ${value.feedbackId} already exists.`
    );
    this.items.set(value.feedbackId, value); return value;
  }
  all() { return [...this.items.values()]; }
}

export class DiscoverySearchFeedbackService {
  constructor({store, actorAuthority, now=() => new Date().toISOString(),
    idFactory}={}) {
    requireMethod(store, 'append', 'Discovery feedback store');
    requireMethod(actorAuthority, 'protect', 'Discovery feedback actor privacy');
    if(typeof idFactory !== 'function') throw new TypeError(
      'Discovery feedback requires an ID authority.'
    );
    this.store = store; this.actorAuthority = actorAuthority;
    this.now = now; this.idFactory = idFactory;
  }

  submit(input, {security}={}) {
    requireMethod(security, 'admitDocumentRef', 'Discovery feedback security');
    requireMethod(security, 'submitSearchFeedback', 'Discovery feedback security');
    plainObject(input, 'Discovery feedback submission');
    noRawSecrets(input, 'Discovery feedback submission');
    const resultRef = platformValue(input.resultRef,
      'Discovery search feedback result', 4096);
    if(security.admitDocumentRef(resultRef) !== true ||
        security.submitSearchFeedback(resultRef, input.type) !== true) {
      throw new Error('Discovery search feedback access denied.');
    }
    const actorScope = validateProtectedActorScope(
      this.actorAuthority.protect(input.actorScope));
    return this.store.append({...input, schemaVersion: 1,
      feedbackId: this.idFactory(), actorScope, resultRef,
      createdAt: this.now()});
  }
}

export class InMemoryDiscoverySearchAnalyticsStore {
  constructor() { this.items = new Map(); }
  append(input) {
    const value = validateSearchAnalyticsEvent(input);
    if(this.items.has(value.activityId)) throw new TypeError(
      `Discovery search activity ${value.activityId} already exists.`
    );
    this.items.set(value.activityId, value); return value;
  }
  range({from=-Infinity, to=Infinity}={}) {
    return [...this.items.values()].filter((item) => {
      const time = Date.parse(item.occurredAt); return time >= from && time <= to;
    }).sort((left, right) => Date.parse(left.occurredAt) -
      Date.parse(right.occurredAt) || left.activityId.localeCompare(right.activityId));
  }
  purgeRawQueries(before) {
    let changed = 0;
    for(const [id, item] of this.items) if(item.queryText &&
        Date.parse(item.occurredAt) < before) {
      this.items.set(id, immutable({...item, queryText: null})); changed += 1;
    }
    return changed;
  }
}

function average(values) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) /
    values.length : null;
}

function visibleSelections(events, documentAuthority, security, threshold) {
  const values = new Map();
  for(const event of events) for(const reference of event.selectedRefs) {
    const document = documentAuthority.resolve(reference);
    if(!document || security.admitDocument(document) !== true) continue;
    const value = values.get(reference) ?? {reference, document, count: 0,
      actors: new Set()};
    value.count += 1; value.actors.add(event.actorScope.actorKey);
    values.set(reference, value);
  }
  return [...values.values()].filter((item) => item.actors.size >= threshold)
    .map((item) => ({reference: item.reference, count: item.count,
      distinctUsers: item.actors.size, document: item.document})).sort(
      (left, right) => right.count - left.count ||
      left.reference.localeCompare(right.reference));
}

function termMetrics(events, threshold) {
  const terms = new Map();
  for(const event of events) if(event.queryText) {
    const term = event.queryText.normalize('NFKC').toLocaleLowerCase().trim();
    if(!term) continue;
    const value = terms.get(term) ?? {term, count: 0, actors: new Set(),
      zeroResults: 0};
    value.count += 1; value.actors.add(event.actorScope.actorKey);
    value.zeroResults += event.resultCount === 0 ? 1 : 0; terms.set(term, value);
  }
  return [...terms.values()].filter((item) => item.count >= threshold &&
    item.actors.size >= threshold).map((item) => ({term: item.term,
    count: item.count, zeroResults: item.zeroResults})).sort((left, right) =>
    right.count - left.count || left.term.localeCompare(right.term));
}

export class DiscoverySearchAnalyticsService {
  constructor({store, actorAuthority, documentAuthority, policy={},
    now=() => Date.now()}={}) {
    for(const method of ['append', 'range', 'purgeRawQueries']) {
      requireMethod(store, method, 'Discovery search analytics store');
    }
    requireMethod(actorAuthority, 'protect', 'Discovery analytics actor privacy');
    requireMethod(documentAuthority, 'resolve', 'Discovery analytics document');
    this.store = store; this.actorAuthority = actorAuthority;
    this.documentAuthority = documentAuthority;
    this.policy = validateSearchAnalyticsPolicy(policy); this.now = now;
  }

  record(input, {security}={}) {
    requireMethod(security, 'recordSearchAnalytics',
      'Discovery analytics security');
    requireMethod(security, 'admitDocument', 'Discovery analytics security');
    if(security.recordSearchAnalytics() !== true) throw new Error(
      'Discovery search analytics recording denied.'
    );
    plainObject(input, 'Discovery search analytics input');
    noRawSecrets(input, 'Discovery search analytics input');
    const references = (input.selectedRefs ?? []).map((reference) => platformValue(
      reference, 'Discovery selected reference', 4096));
    const selectedRefs = [...new Set(references.filter((reference) => {
      const document = this.documentAuthority.resolve(reference);
      return document && security.admitDocument(document) === true;
    }))];
    const actorScope = validateProtectedActorScope(this.actorAuthority.protect(
      input.actorScope, {anonymize: this.policy.anonymizeActors}));
    return this.store.append({...input, actorScope, selectedRefs,
      queryText: this.policy.rawQueryRetentionDays ? input.queryText : null});
  }

  purgeExpiredRawQueries() {
    const boundary = this.now() - this.policy.rawQueryRetentionDays *
      24 * 60 * 60 * 1000;
    return this.store.purgeRawQueries(this.policy.rawQueryRetentionDays ?
      boundary : this.now());
  }

  report({from=-Infinity, to=Infinity, security}={}) {
    requireMethod(security, 'mayViewSearchAnalytics',
      'Discovery analytics security');
    requireMethod(security, 'admitDocument', 'Discovery analytics security');
    if(!this.policy.adminAccess || security.mayViewSearchAnalytics() !== true) {
      throw new Error('Discovery search analytics access denied.');
    }
    const events = this.store.range({from, to});
    const distinct = new Set(events.map((item) => item.actorScope.actorKey)).size;
    const threshold = this.policy.minimumAggregationThreshold;
    if(events.length < threshold || distinct < threshold) return immutable({
      suppressed: true, threshold, searchCount: null, uniqueSearchers: null,
      metrics: null, topTerms: [], zeroResultTerms: [], popularAssets: []});
    const selected = visibleSelections(events, this.documentAuthority,
      security, threshold);
    const terms = termMetrics(events, threshold);
    const selectionTimes = events.filter((item) => item.usefulSelectedAt).map(
      (item) => Date.parse(item.usefulSelectedAt) - Date.parse(item.occurredAt));
    const searchCount = events.length;
    const metrics = {zeroResultRate: events.filter((item) =>
      item.resultCount === 0).length / searchCount,
    abandonmentRate: events.filter((item) => item.abandoned).length / searchCount,
    selectionRate: events.filter((item) => item.selectedRefs.length).length /
      searchCount, timeToUsefulSelectionMs: average(selectionTimes),
    accessRequestConversionRate: events.filter((item) =>
      item.accessRequested).length / searchCount,
    lowConfidenceQueries: events.filter((item) => item.confidence !== null &&
      item.confidence < 0.5).length};
    return immutable({suppressed: false, threshold, searchCount,
      uniqueSearchers: distinct, metrics, topTerms: terms,
      zeroResultTerms: terms.filter((item) => item.zeroResults > 0).sort(
        (left, right) => right.zeroResults - left.zeroResults ||
        left.term.localeCompare(right.term)), popularAssets: selected,
      deprecatedAssetsStillSelected: selected.filter((item) =>
        item.document.deprecationState),
      unownedPopularAssets: selected.filter((item) =>
        !(item.document.ownerRefs ?? []).length),
      qualityFailingPopularAssets: selected.filter((item) =>
        ['FAIL', 'CRITICAL_FAIL'].includes(item.document.qualitySignals?.state))});
  }

  export(options={}) {
    const {security} = options;
    requireMethod(security, 'mayExportSearchAnalytics',
      'Discovery analytics export security');
    if(!this.policy.exportAllowed ||
        security.mayExportSearchAnalytics() !== true) throw new Error(
      'Discovery search analytics export denied.'
    );
    return immutable({schemaVersion: 1, generatedAt: new Date(this.now()).toISOString(),
      policyVersion: this.policy.version, report: this.report(options)});
  }

  authorizeAIAnalysis({security, dataPolicy}={}) {
    requireMethod(security, 'mayAnalyzeSearchLogsWithAI',
      'Discovery analytics AI security');
    requireMethod(dataPolicy, 'authorizeSearchLogAnalysis',
      'AI Interface data policy');
    return this.policy.aiAnalysisAllowed &&
      security.mayAnalyzeSearchLogsWithAI() === true &&
      dataPolicy.authorizeSearchLogAnalysis({policyVersion: this.policy.version,
        rawQueriesRetained: this.policy.rawQueryRetentionDays > 0}) === true;
  }
}
