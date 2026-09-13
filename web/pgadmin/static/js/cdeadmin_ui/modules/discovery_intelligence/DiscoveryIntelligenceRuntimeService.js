/////////////////////////////////////////////////////////////
// Activated Discovery Intelligence authority composition.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';
import {DiscoveryCanonicalizer, DISCOVERY_ENTITY_CLASSES} from
  './DiscoveryDocument';
import {InMemoryDiscoveryIndexBackend} from './DiscoveryIndexBackend';
import {DiscoveryIndexService} from './DiscoveryIndexService';
import {InMemoryDiscoveryGraphIndex} from './DiscoveryGraphIndex';
import {DiscoveryRankingEngine, BALANCED_DISCOVERY_RANKING_PROFILE,
  validateDiscoveryRankingProfile} from './DiscoveryRanking';
import {DiscoveryTrustFeatureAuthority, InMemoryCertificationStore,
  CertificationService, DiscoveryProfilerRegistry,
  ProfileStatisticsService} from './DiscoveryTrustServices';
import {InMemoryDiscoveryCursorAuthority,
  DiscoverySearchService} from './DiscoverySearchService';
import {DiscoveryKnowledgeAssetAuthority, BusinessKnowledgeService,
  DataProductService} from './DiscoveryKnowledgeService';
import {InMemoryDiscoveryUsageStore, DiscoveryUsageSignalService,
  InMemoryDiscoveryRecommendationIndex, DiscoveryRecommendationService} from
  './DiscoveryUsageRecommendations';
import {DiscoveryEngagementAssetAuthority, DiscoverySavedSearchService,
  DiscoveryCollectionService, InMemoryDiscoveryFeedbackStore,
  DiscoverySearchFeedbackService, InMemoryDiscoverySearchAnalyticsStore,
  DiscoverySearchAnalyticsService} from './DiscoverySavedAssetsAnalytics';
import {validateProtectedActorScope} from './DiscoveryEngagementContracts';
import {InMemoryDiscoveryAccessRequestStore,
  DiscoveryAccessRequestService} from './DiscoveryAccessService';
import {InMemoryDiscoveryCurationStore,
  DiscoveryCurationService} from './DiscoveryCurationService';
import {InMemoryDiscoveryIndexConfigurationStore,
  InMemoryDiscoveryIndexPolicyStore, DiscoveryIndexSourceRegistry,
  DiscoveryIndexAdministrationService} from './DiscoveryIndexAdministration';
import {DiscoveryServiceAPI} from './DiscoveryServiceAPI';
import {DiscoveryAnalysisService} from './DiscoveryAnalysisService';
import {InMemoryDiscoveryEnrichmentStore,
  DiscoveryEnrichmentService} from './DiscoveryEnrichmentService';
import {DiscoveryVisibilityTestService} from './DiscoveryVisibilityTestService';
import {DiscoveryAdministrationAssetAuthority,
  DiscoveryIndexSourceAssetService, DiscoveryRankingProfileService} from
  './DiscoveryAdministrationAssets';
import {adaptDiscoveryFormCommandArguments} from
  './DiscoveryFormCommandAdapter';

export const DISCOVERY_INTELLIGENCE_RUNTIME_SERVICE_ID =
  'cdeadmin.discovery_intelligence.runtime';

const TASK_COMMANDS = new Set([
  'discovery.related.find', 'discovery.access.request.submit',
  'discovery.access.request.approve', 'discovery.access.revoke',
  'discovery.ranking.test', 'discovery.visibility.test',
  'discovery.analytics.export',
]);

function requireMethod(value, method, label) {
  if(typeof value?.[method] !== 'function') throw new TypeError(
    `${label} requires ${method}.`
  );
}

function required(value, label) {
  return platformValue(value, label, 4096);
}

function security(context={}) {
  const value = context.discoverySecurity ?? context.security;
  if(!value || typeof value !== 'object') throw new Error(
    'Discovery security authority is unavailable.'
  );
  return value;
}

function targetProject(args, context) {
  return required(args.projectId ?? context.projectId,
    'Discovery project ID');
}

function inputValue(args) {
  const value = args.input ?? args.values ?? args;
  plainObject(value, 'Discovery command input');
  return value;
}

function unavailable(label) {
  return async () => { throw new Error(`${label} is not configured.`); };
}

function createIdFactory(prefix, now=() => Date.now()) {
  let sequence = 0;
  return (kind=prefix) => `${kind}-${now()}-${++sequence}`;
}

function defaultEvidence(document) {
  const certificationState = document.certifications?.at(-1)?.state ??
    document.trustSignals?.certificationState ?? null;
  const qualityCandidate = document.qualitySignals?.state ?? null;
  const qualityState = ['CRITICAL_FAILURE', 'CRITICAL_UNKNOWN_STALE',
    'NONCRITICAL_FAILURE', 'PASS', 'NOT_DEFINED'].includes(qualityCandidate) ?
    qualityCandidate : null;
  const numeric = (value) => Number.isFinite(value) ? value : null;
  return {certificationState, qualityState,
    freshnessScore: numeric(document.freshnessSignals?.score),
    weightedUsageEvents: numeric(document.usageSignals?.weightedEvents),
    contextRelevance: null, lineageRelevance: null, businessRelevance: null,
    usageDocumentation: document.usageSignals?.documented === true,
    examples: document.usageSignals?.examples === true,
    activeCertifiedDataProduct:
      document.trustSignals?.activeCertifiedDataProduct === true,
    freshnessSlaBreach: document.freshnessSignals?.slaBreach === true,
    unstableBreakingSchema:
      document.trustSignals?.unstableBreakingSchema === true};
}

class DiscoveryDocumentAuthority {
  constructor() { this.documents = new Map(); this.revision = null; }
  replace(documents, revision) {
    this.documents = new Map(documents.map((document) =>
      [document.canonicalRef, document]));
    this.revision = revision;
  }
  resolve(reference) { return this.documents.get(String(reference)) ?? null; }
  list() { return [...this.documents.values()]; }
}

class CatalogingDiscoveryIndexBackend {
  constructor(backend, documents) {
    this.backend = backend; this.documents = documents;
  }
  async bulkRevision(input) {
    const result = await this.backend.bulkRevision(input);
    this.documents.replace(input.documents, input.revision); return result;
  }
  upsertDocument(...args) { return this.backend.upsertDocument(...args); }
  removeDocument(...args) { return this.backend.removeDocument(...args); }
  lexicalSearch(...args) { return this.backend.lexicalSearch(...args); }
  facetSearch(...args) { return this.backend.facetSearch(...args); }
  semanticSearch(...args) { return this.backend.semanticSearch(...args); }
  getDocument(...args) { return this.backend.getDocument(...args); }
  health(...args) { return this.backend.health(...args); }
}

function localManualAccessPolicy() {
  return {evaluate: async () => ({decision: 'PENDING_APPROVAL', approvers: [],
    result: {policyId: 'cdeadmin.discovery.manual-review',
      decisionBasis: 'manual_review_required'}}),
  review: async (_request, review) => ({allowed: true,
    policyId: 'cdeadmin.discovery.manual-review', decision: review.decision,
    evidence: 'authorized_reviewer_decision'})};
}

function relationshipRecorder(relationships, idFactory) {
  return {record: async (input) => {
    for(const reference of [input.fromRef, input.toRef]) relationships.upsertNode({
      id: reference, kind: 'discovery_resource', reference, label: reference});
    return relationships.upsertEdge({id: idFactory('relationship'),
      from: input.fromRef, to: input.toRef, relation: input.relationship,
      origin: 'project_declared', fromRef: input.fromRef, toRef: input.toRef,
      evidenceRefs: input.evidenceRefs}).id;
  }};
}

function protectedActorAuthority() {
  return {protect: (input) => validateProtectedActorScope(input)};
}

export class DiscoveryIntelligenceRuntimeService {
  constructor({projectAssets, identities, tasks, relationships, federatedSearch,
    options={}}={}) {
    for(const [value, method, label] of [[projectAssets, 'saveAsset',
      'Discovery Project Asset service'], [identities, 'create',
      'Discovery identity service'], [tasks, 'submit', 'Discovery TaskService'],
    [relationships, 'upsertEdge', 'Discovery relationship service'],
    [federatedSearch, 'register', 'Discovery federated-search service']]) {
      requireMethod(value, method, label);
    }
    this.options = options; this.tasks = tasks;
    this.identities = identities; this.relationships = relationships;
    this.federatedSearch = federatedSearch;
    const idFactory = options.idFactory ?? createIdFactory('discovery');
    this.idFactory = idFactory;
    this.documents = new DiscoveryDocumentAuthority();
    const baseBackend = options.indexBackend ?? new InMemoryDiscoveryIndexBackend();
    this.indexBackend = new CatalogingDiscoveryIndexBackend(baseBackend,
      this.documents);
    this.canonicalizer = new DiscoveryCanonicalizer({identities});
    this.index = new DiscoveryIndexService({backend: this.indexBackend,
      canonicalizer: this.canonicalizer});
    this.knowledgeAssets = new DiscoveryKnowledgeAssetAuthority({projectAssets});
    this.knowledge = new BusinessKnowledgeService({assets: this.knowledgeAssets});
    this.products = new DataProductService({knowledge: this.knowledge,
      documentAuthority: this.documents});
    this.usage = new DiscoveryUsageSignalService({
      store: new InMemoryDiscoveryUsageStore(),
      actorAuthority: options.actorAuthority ?? protectedActorAuthority(),
      policy: options.usagePolicy});
    this.recommendationIndex = new InMemoryDiscoveryRecommendationIndex();
    this.recommendations = new DiscoveryRecommendationService({
      index: this.recommendationIndex, usage: this.usage,
      documentAuthority: this.documents, policy: options.recommendationPolicy});
    this.trustFeatures = new DiscoveryTrustFeatureAuthority({
      evidenceResolver: options.featureEvidenceResolver ?? defaultEvidence});
    this.ranking = new DiscoveryRankingEngine({featureAuthority:
      this.trustFeatures});
    this.graph = new InMemoryDiscoveryGraphIndex();
    this.search = new DiscoverySearchService({indexBackend: this.indexBackend,
      rankingEngine: this.ranking, graphIndex: this.graph,
      businessSource: this.knowledge,
      semanticAuthority: options.semanticAuthority ?? null,
      cursorAuthority: new InMemoryDiscoveryCursorAuthority({
        tokenFactory: () => idFactory('cursor')}),
      queryIdFactory: () => idFactory('query')});
    this.engagementAssets = new DiscoveryEngagementAssetAuthority({projectAssets});
    this.rankingProfiles = new DiscoveryRankingProfileService({assets:
      new DiscoveryAdministrationAssetAuthority({projectAssets})});
    this.rankingProfiles.register(BALANCED_DISCOVERY_RANKING_PROFILE);
    this.savedSearches = new DiscoverySavedSearchService({
      assets: this.engagementAssets, search: this.search,
      rankingProfiles: this.rankingProfiles});
    this.collections = new DiscoveryCollectionService({
      assets: this.engagementAssets, referenceAuthority: this.documents});
    const actorAuthority = options.actorAuthority ?? protectedActorAuthority();
    this.feedback = new DiscoverySearchFeedbackService({
      store: new InMemoryDiscoveryFeedbackStore(), actorAuthority,
      idFactory: () => idFactory('feedback')});
    this.analytics = new DiscoverySearchAnalyticsService({
      store: new InMemoryDiscoverySearchAnalyticsStore(), actorAuthority,
      documentAuthority: this.documents, policy: options.analyticsPolicy});
    this.certifications = new CertificationService({
      store: new InMemoryCertificationStore(),
      evidenceAuthority: options.certificationEvidenceAuthority ?? {
        evaluate: unavailable('Discovery certification evidence authority')},
      idFactory: () => idFactory('certification')});
    this.profilers = new DiscoveryProfilerRegistry();
    for(const profiler of options.profilers ?? []) this.profilers.register(profiler);
    this.profileStatistics = new ProfileStatisticsService({profilers:
      this.profilers});
    this.access = new DiscoveryAccessRequestService({
      store: new InMemoryDiscoveryAccessRequestStore(),
      policy: options.accessPolicy ?? localManualAccessPolicy(),
      grantPlanner: options.grantPlanner ?? {
        plan: unavailable('Discovery provider grant planner')},
      provisioner: options.provisioner ?? {
        apply: unavailable('Discovery provider provisioner'),
        revoke: unavailable('Discovery provider provisioner')}, tasks,
      idFactory: () => idFactory('access-request')});
    this.curation = new DiscoveryCurationService({
      store: new InMemoryDiscoveryCurationStore(),
      mutationAuthority: options.curationMutationAuthority ?? {
        apply: unavailable('Discovery curation mutation authority')},
      relationshipAuthority: relationshipRecorder(relationships, idFactory),
      idFactory});
    this.indexSources = new DiscoveryIndexSourceRegistry();
    for(const source of options.indexSourceAdapters ?? []) {
      this.indexSources.register(source.sourceId, source.adapter);
    }
    this.indexAdministration = new DiscoveryIndexAdministrationService({
      configurations: new InMemoryDiscoveryIndexConfigurationStore(),
      policies: new InMemoryDiscoveryIndexPolicyStore(),
      registry: this.indexSources, indexService: this.index, tasks,
      revisionFactory: (sourceId) => `${sourceId}@${idFactory('revision')}`});
    this.administrationAssets = new DiscoveryAdministrationAssetAuthority({
      projectAssets});
    this.indexSourceAssets = new DiscoveryIndexSourceAssetService({
      assets: this.administrationAssets,
      administration: this.indexAdministration});
    this.api = new DiscoveryServiceAPI({search: this.search,
      indexBackend: this.indexBackend, graph: this.graph,
      knowledge: this.knowledge, trust: this.trustFeatures,
      access: this.access});
    const aiCandidate = options.aiInterface ?? null;
    this.aiInterface = aiCandidate?.capabilities?.().discoveryAssistance === true ?
      aiCandidate : null;
    this.analysis = new DiscoveryAnalysisService({discoveryAPI: this.api,
      aiInterface: this.aiInterface});
    this.enrichment = new DiscoveryEnrichmentService({
      store: new InMemoryDiscoveryEnrichmentStore(), discoveryAPI: this.api,
      curation: this.curation, aiInterface: this.aiInterface,
      idFactory: () => idFactory('enrichment')});
    this.visibility = options.principalSecurityResolver ?
      new DiscoveryVisibilityTestService({search: this.search,
        principalSecurityResolver: options.principalSecurityResolver}) : null;
    this.platformServices = Object.freeze({
      DiscoveryIndexService: this.index,
      BusinessKnowledgeService: this.knowledge,
      UsageSignalService: this.usage,
      DataProductService: this.products,
      AccessRequestService: this.access,
      DiscoveryRankingService: this.ranking,
      RecommendationService: this.recommendations,
      CurationService: this.curation,
    });
    this.assetAuthorities = Object.freeze({
      BusinessTerm: this.knowledgeAssets,
      Domain: this.knowledgeAssets,
      MetricDefinition: this.knowledgeAssets,
      DataProduct: this.knowledgeAssets,
      CertificationProfile: this.knowledgeAssets,
      SavedSearch: this.engagementAssets,
      DiscoveryCollection: this.engagementAssets,
      RankingProfile: this.administrationAssets,
      IndexSourceConfig: this.administrationAssets,
    });
    this.taskDisposers = [];
    for(const commandId of TASK_COMMANDS) this.taskDisposers.push(tasks.register(
      `${commandId}.task`, (request, taskContext) => this._executeImmediate(
        commandId, request.arguments, {...request.commandContext,
          ...taskContext, security: taskContext.security})
    ));
  }

  capabilities() {
    return immutable({indexBackend: true, lexicalSearch: true,
      semanticSearch: this.options.semanticAuthority != null,
      graphSearch: true, businessKnowledge: true, usageSignals: true,
      recommendations: true, dataProducts: true, savedAssets: true,
      accessRequests: true, providerProvisioning:
        this.options.grantPlanner != null && this.options.provisioner != null,
      curationReview: true,
      curationMutation: this.options.curationMutationAuthority != null,
      certificationReview: this.options.certificationEvidenceAuthority != null,
      profilingProviders: this.profilers.items.size,
      indexSourceAdapters: this.indexSources.available(),
      visibilityTesting: this.visibility !== null,
      aiAssistance: this.aiInterface !== null,
      queryLauncher: typeof this.options.openQuery === 'function',
      sbsqlLauncher: typeof this.options.openSBsql === 'function',
      compatibilityLauncher: typeof this.options.openCompatibility === 'function',
      biLauncher: typeof this.options.openBI === 'function'});
  }

  canExecute(commandId, context={}) {
    const capabilities = this.capabilities();
    if(commandId === 'discovery.access.provision' ||
        commandId === 'discovery.access.revoke') return capabilities.providerProvisioning;
    if(commandId === 'discovery.curation.resolve' ||
        commandId === 'discovery.enrichment.accept') return capabilities.curationMutation;
    if(commandId === 'discovery.certification.review') {
      return capabilities.certificationReview;
    }
    if(commandId === 'discovery.visibility.test') return capabilities.visibilityTesting;
    if(commandId === 'discovery.index.refresh' ||
        commandId === 'discovery.index.reconcile') {
      return capabilities.indexSourceAdapters.length > 0;
    }
    if(commandId === 'discovery.open.query') return typeof (
      context.openQuery ?? this.options.openQuery) === 'function';
    if(commandId === 'discovery.open.sbsql') return typeof (
      context.openSBsql ?? this.options.openSBsql) === 'function';
    if(commandId === 'discovery.open.compatibility') return typeof (
      context.openCompatibility ?? this.options.openCompatibility) === 'function';
    if(commandId === 'discovery.open.bi') return typeof (
      context.openBI ?? this.options.openBI) === 'function';
    if(commandId === 'discovery.ask_ai') return capabilities.aiAssistance;
    return true;
  }

  async execute(commandId, args={}, context={}) {
    plainObject(args, 'Discovery command arguments');
    noRawSecrets(args, 'Discovery command arguments');
    args = adaptDiscoveryFormCommandArguments(commandId, args, context);
    if(!this.canExecute(commandId, context)) throw new Error(
      `Discovery command authority is not configured: ${commandId}`
    );
    if(TASK_COMMANDS.has(commandId)) return this.tasks.submit({
      type: `${commandId}.task`, label: commandId, arguments: args,
      commandContext: {projectId: context.projectId}, cancelable: true,
      resumable: false, resourceRefs: args.canonicalRefs ??
        [args.canonicalRef ?? args.targetRef].filter(Boolean),
      assetRefs: [args.assetId].filter(Boolean)},
    {owner: context.currentUser?.id ?? null, security: security(context)});
    return this._executeImmediate(commandId, args, context);
  }

  async _saveKnowledge(kind, args, context) {
    return this.knowledge.save(targetProject(args, context), kind,
      inputValue(args), {assetId: args.assetId,
        expectedVersion: args.expectedVersion ?? 0});
  }

  async _changeKnowledge(kind, args, context, changes) {
    const projectId = targetProject(args, context);
    const asset = await this.knowledgeAssets.get(projectId,
      required(args.assetId, 'Discovery asset ID'), {kind});
    return this.knowledge.save(projectId, kind, {...asset.content, ...changes},
      {assetId: args.assetId, expectedVersion: args.expectedVersion ??
        asset.version});
  }

  async _analysisLaunch(commandId, args, context) {
    const plan = await this.analysis.plan(inputValue(args),
      {security: security(context)});
    if(plan.status === 'refused') return plan;
    const callback = commandId === 'discovery.open.query' ?
      context.openQuery ?? this.options.openQuery :
      commandId === 'discovery.open.sbsql' ?
        context.openSBsql ?? this.options.openSBsql :
        commandId === 'discovery.open.compatibility' ?
          context.openCompatibility ?? this.options.openCompatibility :
          context.openBI ?? this.options.openBI;
    return callback(plan, context);
  }

  async _executeImmediate(commandId, args, context) {
    const secured = () => ({security: security(context)});
    switch(commandId) {
    case 'discovery.search.run':
      return this.search.search(inputValue(args), {security: security(context),
        profile: args.profile, context});
    case 'discovery.search.save':
      return this.savedSearches.save(targetProject(args, context),
        inputValue(args), {assetId: args.assetId,
          expectedVersion: args.expectedVersion ?? 0});
    case 'discovery.search.feedback':
      return this.feedback.submit(inputValue(args), secured());
    case 'discovery.entity.open':
      return this.api.get_entity(inputValue(args), secured());
    case 'discovery.entity.show_surfaces': {
      const entity = await this.api.get_entity(inputValue(args), secured());
      return immutable({resultRef: entity.resultRef,
        canonicalRef: entity.canonicalRef,
        authorizedAliases: entity.authorizedAliases});
    }
    case 'discovery.related.find':
      return this.api.get_related(inputValue(args), secured());
    case 'discovery.product.create':
    case 'discovery.product.update':
      return this.products.save(targetProject(args, context), inputValue(args),
        {assetId: args.assetId, expectedVersion: args.expectedVersion ?? 0});
    case 'discovery.product.add_member':
    case 'discovery.product.remove_member':
      return this.products.changeMember(targetProject(args, context),
        required(args.assetId, 'Discovery product asset ID'),
        required(args.collection, 'Discovery product member collection'),
        required(args.reference, 'Discovery product member reference'),
        commandId.endsWith('add_member') ? 'add' : 'remove',
        args.expectedVersion);
    case 'discovery.product.request_certification':
    case 'discovery.metric.certify':
      return this.certifications.request(inputValue(args), args.profile);
    case 'discovery.product.status.set':
      return this.products.setStatus(targetProject(args, context),
        required(args.assetId, 'Discovery product asset ID'),
        required(args.status, 'Discovery product status'), args);
    case 'discovery.term.create':
    case 'discovery.term.update':
      return this._saveKnowledge('BusinessTerm', args, context);
    case 'discovery.term.map': {
      const projectId = targetProject(args, context);
      const asset = await this.knowledgeAssets.get(projectId,
        required(args.assetId, 'Discovery term asset ID'), {kind: 'BusinessTerm'});
      const mappings = [...asset.content.mappings];
      const targetRef = required(args.mapping?.targetRef,
        'Discovery term mapping target');
      const index = mappings.findIndex((item) => item.targetRef === targetRef);
      if(args.operation === 'remove') {
        if(index >= 0) mappings.splice(index, 1);
      } else if(index >= 0) mappings[index] = args.mapping;
      else mappings.push(args.mapping);
      return this.knowledge.save(projectId, 'BusinessTerm',
        {...asset.content, mappings}, {assetId: args.assetId,
          expectedVersion: args.expectedVersion ?? asset.version});
    }
    case 'discovery.term.status.set':
      return this._changeKnowledge('BusinessTerm', args, context,
        {status: required(args.status, 'Discovery term status')});
    case 'discovery.metric.create':
    case 'discovery.metric.update':
      return this._saveKnowledge('MetricDefinition', args, context);
    case 'discovery.certification.review':
      return this.certifications.review(required(args.requestId,
        'Discovery certification request ID'), args.input ?? args.review,
      args.profile,
      context);
    case 'discovery.certification.revoke':
      return this.certifications.revoke(required(args.certificationId,
        'Discovery certification ID'), args.input ?? {});
    case 'discovery.access.request.create':
      return this.access.create(inputValue(args), secured());
    case 'discovery.access.request.submit': {
      const request = args.requestId ? null : this.access.create(
        inputValue(args), secured());
      return this.access.submit(args.requestId ?? request.requestId, secured());
    }
    case 'discovery.access.request.approve':
    case 'discovery.access.request.deny':
      return this.access.decide(required(args.requestId,
        'Discovery access request ID'), commandId.endsWith('approve') ?
        'APPROVED' : 'DENIED', {...args, security: security(context)});
    case 'discovery.access.provision': {
      const requestId = required(args.requestId,
        'Discovery access request ID');
      await this.access.prepareGrant(requestId, secured());
      return this.access.provision(requestId, {security: security(context),
        owner: context.currentUser?.id ?? null});
    }
    case 'discovery.access.revoke':
      return this.access.revoke(required(args.requestId,
        'Discovery access request ID'), {...args, security: security(context)});
    case 'discovery.index.source.create':
    case 'discovery.index.source.update':
      return this.indexSourceAssets.save(targetProject(args, context),
        inputValue(args), {...args, security: security(context)});
    case 'discovery.index.refresh':
    case 'discovery.index.reconcile':
      return this.indexAdministration.refresh(required(args.sourceId,
        'Discovery index source ID'), {...args, security: security(context),
        owner: context.currentUser?.id ?? null});
    case 'discovery.index.publish_revision':
      return this.index.publish(inputValue(args), {authorization:
        security(context)});
    case 'discovery.ranking.create':
    case 'discovery.ranking.update':
      return this.rankingProfiles.save(targetProject(args, context),
        inputValue(args), {assetId: args.assetId,
          expectedVersion: args.expectedVersion ?? 0});
    case 'discovery.ranking.test': {
      const profile = validateDiscoveryRankingProfile(args.profile ??
        inputValue(args));
      const published = {...profile, status: 'published'};
      return immutable({before: this.ranking.rank(args.candidates ?? [],
        {profile: BALANCED_DISCOVERY_RANKING_PROFILE,
          query: args.query, security: security(context)}),
      after: this.ranking.rank(args.candidates ?? [], {profile: published,
        query: args.query, security: security(context)})});
    }
    case 'discovery.ranking.publish': {
      const profile = validateDiscoveryRankingProfile(inputValue(args));
      const published = {...profile, status: 'published'};
      return this.rankingProfiles.save(targetProject(args, context), published,
        {assetId: args.assetId, expectedVersion: args.expectedVersion ?? 0});
    }
    case 'discovery.synonym.update':
      return this.indexAdministration.saveAdministrationConfiguration('policy',
        inputValue(args), {expectedRevision: args.expectedRevision ?? 0,
          security: security(context)});
    case 'discovery.curation.assign':
      return this.curation.assign(required(args.itemId,
        'Discovery curation item ID'), required(args.assigneeRef,
        'Discovery curation assignee'), {...args, security: security(context)});
    case 'discovery.curation.resolve':
      if(args.zeroResult) return this.curation.resolveZeroResult(
        args.zeroResult, secured());
      return this.curation.resolve(required(args.itemId,
        'Discovery curation item ID'), {...args, security: security(context)});
    case 'discovery.enrichment.accept':
      return this.enrichment.accept(required(args.suggestionId,
        'Discovery enrichment suggestion ID'), {...args,
        security: security(context)});
    case 'discovery.enrichment.reject':
      return this.enrichment.reject(required(args.suggestionId,
        'Discovery enrichment suggestion ID'), {...args,
        security: security(context)});
    case 'discovery.duplicate.link_equivalent':
    case 'discovery.duplicate.reject':
      return this.curation.decideDuplicate(required(args.candidateId,
        'Discovery duplicate candidate ID'), {...args,
        decision: commandId.endsWith('reject') ? 'not_duplicate' :
          args.decision ?? 'equivalent_to', security: security(context)});
    case 'discovery.visibility.test':
      return this.visibility.run(inputValue(args), {security: security(context),
        context});
    case 'discovery.analytics.export':
      return this.analytics.export({...args, security: security(context)});
    case 'discovery.open.query':
    case 'discovery.open.sbsql':
    case 'discovery.open.compatibility':
    case 'discovery.open.bi':
      return this._analysisLaunch(commandId, args, context);
    case 'discovery.ask_ai':
      return this.analysis.interpret(inputValue(args), {security:
        security(context), aiContext: context});
    default:
      throw new Error(`Unknown Discovery command ${commandId}.`);
    }
  }

  screenData(screenId) {
    return immutable({screenId, capabilities: this.capabilities(),
      indexSources: this.indexAdministration.configurations.list(),
      rankingProfiles: [...this.rankingProfiles.profiles.values()],
      knowledgeCounts: Object.fromEntries(['BusinessTerm', 'Domain',
        'MetricDefinition', 'DataProduct', 'CertificationProfile'].map((kind) =>
        [kind, this.knowledge.list(kind).length])),
      taskCount: this.tasks.list().filter((task) =>
        task.type.startsWith('discovery.')).length,
      entityClasses: DISCOVERY_ENTITY_CLASSES});
  }

  dispose() {
    this.access.unregister?.(); this.indexAdministration.unregister?.();
    this.taskDisposers.splice(0).reverse().forEach((remove) => remove());
  }
}
