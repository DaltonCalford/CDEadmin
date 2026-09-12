/////////////////////////////////////////////////////////////
// Security-trimmed typed Discovery service API.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';
import {normalizeDiscoveryIndexBackend} from './DiscoveryIndexBackend';

export const DISCOVERY_SERVICE_API_ID =
  'cdeadmin.discovery_intelligence.service_api';
export const DISCOVERY_SERVICE_API_METHODS = Object.freeze([
  'search', 'get_entity', 'get_related', 'get_product', 'get_business_term',
  'get_metric', 'get_trust', 'get_access_state', 'request_access',
]);

function exact(input, fields, label) {
  plainObject(input, label); noRawSecrets(input, label);
  const unknown = Object.keys(input).find((field) => !fields.includes(field));
  if(unknown) throw new TypeError(`${label} contains unsupported field ${unknown}.`);
  return input;
}

function requireMethod(value, method, label) {
  if(typeof value?.[method] !== 'function') throw new TypeError(
    `${label} requires ${method}.`
  );
}

function requireSecurity(security, methods) {
  for(const method of methods) requireMethod(security, method,
    'Discovery service security');
  return security;
}

function optional(value, label, maximum=4096) {
  return value === null || value === undefined ? null : platformValue(
    value, label, maximum);
}

function visibleAliases(document, security) {
  return document.authorizedAliases.filter((surface) =>
    security.admitAlias(surface, document) === true).map((surface) => immutable({
    surfaceId: surface.surfaceId, kind: surface.kind,
    dialectId: surface.dialectId,
    visibleQualifiedName: surface.visibleQualifiedName,
    workareaSchemaRef: surface.workareaSchemaRef,
    visibilityScope: surface.visibilityScope,
    queryCapabilities: [...surface.queryCapabilities],
    mutationCapabilities: [...surface.mutationCapabilities],
    crossSurfaceVisibility: surface.crossSurfaceVisibility,
    evidenceVersion: surface.evidenceVersion,
  }));
}

function signal(document, security, name, value) {
  return security.admitSignal(document, name) === true ? value : null;
}

export function discoveryEntityProjection(document, security) {
  requireSecurity(security, ['admitDocument', 'admitAlias', 'admitSignal',
    'exposeCanonicalRef']);
  if(security.admitDocument(document, 'DISCOVER_IDENTITY') !== true) {
    throw new Error('Discovery entity was not found.');
  }
  const metadata = security.admitDocument(document, 'DISCOVER_METADATA') === true;
  return immutable({resultRef: document.documentId,
    canonicalRef: security.exposeCanonicalRef(document) === true ?
      document.canonicalRef : null,
    entityClass: document.entityClass, name: document.name,
    provider: metadata ? document.provider : null,
    nativeKind: metadata ? document.nativeKind : null,
    connectionEnvironment: metadata ? document.connectionEnvironment : null,
    qualifiedNames: metadata ? [...document.qualifiedNames] : [],
    authorizedAliases: visibleAliases(document, security),
    description: metadata ? document.description : null,
    businessTerms: metadata && security.admitSignal(document, 'business') === true ?
      [...document.businessTerms] : [],
    synonyms: metadata && security.admitSignal(document, 'business') === true ?
      [...document.synonyms] : [],
    domainRefs: metadata && security.admitSignal(document, 'business') === true ?
      [...document.domainRefs] : [],
    ownerRefs: metadata ? [...document.ownerRefs] : [],
    stewardRefs: metadata ? [...document.stewardRefs] : [],
    tags: metadata ? [...document.tags] : [],
    classification: metadata ? document.classification : null,
    schemaSummary: metadata ? document.schemaSummary : null,
    nativeMetadataSummary: metadata ? document.nativeMetadataSummary : null,
    trustSignals: metadata ? signal(document, security, 'trust',
      document.trustSignals) : null,
    qualitySignals: metadata ? signal(document, security, 'quality',
      document.qualitySignals) : null,
    freshnessSignals: metadata ? signal(document, security, 'freshness',
      document.freshnessSignals) : null,
    usageSignals: metadata ? signal(document, security, 'usage',
      document.usageSignals) : null,
    lineageSignals: metadata ? signal(document, security, 'lineage',
      document.lineageSignals) : null,
    contractSignals: metadata ? signal(document, security, 'contracts',
      document.contractSignals) : null,
    certifications: metadata && security.admitSignal(document, 'trust') === true ?
      [...document.certifications] : [],
    deprecationState: metadata ? document.deprecationState : null,
    accessState: metadata ? signal(document, security, 'access',
      document.accessState) : null,
    updatedAt: metadata ? document.updatedAt : null,
    sourceRevision: metadata ? document.sourceRevision : null,
    indexRevision: document.indexRevision});
}

function knowledgeProjection(kind, value, security) {
  requireSecurity(security, ['admitBusinessKnowledge']);
  if(!value || security.admitBusinessKnowledge(value, {kind}) !== true) {
    throw new Error(`Discovery ${kind} was not found.`);
  }
  return immutable({...value});
}

export class DiscoveryServiceAPI {
  constructor({search, indexBackend, graph, knowledge, trust, access}={}) {
    requireMethod(search, 'search', 'Discovery service API search authority');
    const backend = normalizeDiscoveryIndexBackend(indexBackend);
    for(const method of ['neighbors', 'health']) requireMethod(graph, method,
      'Discovery service API graph authority');
    for(const method of ['get']) requireMethod(knowledge, method,
      'Discovery service API knowledge authority');
    requireMethod(trust, 'evaluate', 'Discovery service API trust authority');
    requireMethod(access, 'create', 'Discovery service API access authority');
    this.searchAuthority = search; this.backend = backend; this.graph = graph;
    this.knowledge = knowledge; this.trust = trust; this.access = access;
  }

  search(input, {security, profile, context={}}={}) {
    requireSecurity(security, ['admitDocument', 'admitAlias', 'admitSignal',
      'admitFacet', 'admitGraphEdge', 'admitBusinessKnowledge',
      'exposeCanonicalRef']);
    return this.searchAuthority.search(input, {security, profile, context});
  }

  async get_entity(input, {security}={}) {
    exact(input, ['canonicalRef', 'revision'], 'Discovery get-entity request');
    requireSecurity(security, ['admitDocument', 'admitAlias', 'admitSignal',
      'exposeCanonicalRef']);
    const canonicalRef = platformValue(input.canonicalRef,
      'Discovery entity canonical reference', 4096);
    const revision = optional(input.revision, 'Discovery index revision', 2048);
    const document = await this.backend.getDocument(canonicalRef, {
      ...(revision === null ? {} : {revision}),
      admit: (candidate) => security.admitDocument(candidate,
        'DISCOVER_IDENTITY') === true});
    return discoveryEntityProjection(document, security);
  }

  async get_related(input, {security}={}) {
    exact(input, ['canonicalRefs', 'revision', 'limit'],
      'Discovery get-related request');
    requireSecurity(security, ['admitDocument', 'admitAlias', 'admitSignal',
      'admitGraphEdge', 'exposeCanonicalRef']);
    if(!Array.isArray(input.canonicalRefs) || !input.canonicalRefs.length ||
        input.canonicalRefs.length > 50) throw new TypeError(
      'Discovery related references must contain 1 to 50 items.'
    );
    const canonicalRefs = [...new Set(input.canonicalRefs.map((reference) =>
      platformValue(reference, 'Discovery related canonical reference', 4096)))];
    if(canonicalRefs.length !== input.canonicalRefs.length) throw new TypeError(
      'Discovery related references contain duplicates.'
    );
    const limit = input.limit ?? 50;
    if(!Number.isInteger(limit) || limit < 1 || limit > 200) throw new TypeError(
      'Discovery related limit must be an integer from 1 to 200.'
    );
    const health = await this.graph.health();
    if(!health.ready) throw new Error('Discovery graph index is not ready.');
    const revision = optional(input.revision, 'Discovery graph revision', 2048) ??
      health.activeRevision;
    const candidates = await this.graph.neighbors({canonicalRefs, revision,
      limit, admitEdge: (edge) => security.admitGraphEdge(edge) === true});
    const rows = [];
    for(const candidate of candidates) {
      try {
        const entity = await this.get_entity({canonicalRef: candidate.canonicalRef},
          {security});
        rows.push(immutable({entity, score: candidate.score,
          evidence: candidate.evidence}));
      } catch(error) {
        if(!/not found/i.test(error.message)) throw error;
      }
    }
    return immutable({revision, rows});
  }

  get_product(input, {security}={}) {
    exact(input, ['productId'], 'Discovery get-product request');
    return knowledgeProjection('DataProduct', this.knowledge.get('DataProduct',
      platformValue(input.productId, 'Discovery product ID', 512)), security);
  }

  get_business_term(input, {security}={}) {
    exact(input, ['termId'], 'Discovery get-business-term request');
    return knowledgeProjection('BusinessTerm', this.knowledge.get('BusinessTerm',
      platformValue(input.termId, 'Discovery business term ID', 512)), security);
  }

  get_metric(input, {security}={}) {
    exact(input, ['metricId'], 'Discovery get-metric request');
    return knowledgeProjection('MetricDefinition', this.knowledge.get(
      'MetricDefinition', platformValue(input.metricId,
        'Discovery metric ID', 512)), security);
  }

  async get_trust(input, {security, context={}}={}) {
    exact(input, ['canonicalRef', 'revision'], 'Discovery get-trust request');
    requireSecurity(security, ['admitDocument', 'admitAlias', 'admitSignal',
      'exposeCanonicalRef']);
    const canonicalRef = platformValue(input.canonicalRef,
      'Discovery trust canonical reference', 4096);
    const revision = optional(input.revision, 'Discovery index revision', 2048);
    const document = await this.backend.getDocument(canonicalRef, {
      ...(revision === null ? {} : {revision}),
      admit: (candidate) => security.admitDocument(candidate,
        'DISCOVER_IDENTITY') === true});
    if(security.admitDocument(document, 'DISCOVER_METADATA') !== true ||
        security.admitSignal(document, 'trust') !== true) throw new Error(
      'Discovery trust state was not found.'
    );
    return immutable({canonicalRef: security.exposeCanonicalRef(document) === true ?
      document.canonicalRef : null, resultRef: document.documentId,
    evaluation: this.trust.evaluate(document, {security, context}),
    trustSignals: document.trustSignals, qualitySignals:
      signal(document, security, 'quality', document.qualitySignals),
    freshnessSignals: signal(document, security, 'freshness',
      document.freshnessSignals), certifications: [...document.certifications]});
  }

  async get_access_state(input, {security}={}) {
    exact(input, ['canonicalRef', 'revision'],
      'Discovery get-access-state request');
    const entity = await this.get_entity(input, {security});
    if(entity.accessState === null) throw new Error(
      'Discovery access state was not found.'
    );
    return immutable({resultRef: entity.resultRef,
      canonicalRef: entity.canonicalRef, accessState: entity.accessState});
  }

  async request_access(input, {security}={}) {
    exact(input, ['requester', 'targetRef', 'requestedAccess', 'environment',
      'reason', 'duration', 'projectRef'], 'Discovery request-access request');
    const targetRef = platformValue(input.targetRef,
      'Discovery access target', 4096);
    await this.get_entity({canonicalRef: targetRef}, {security});
    return this.access.create({...input, targetRef,
      projectRef: input.projectRef ?? null}, {security});
  }
}
