/////////////////////////////////////////////////////////////
// AI-optional, evidence-preserving Discovery enrichment review.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';

export const DISCOVERY_ENRICHMENT_KINDS = Object.freeze([
  'description', 'business_term', 'synonym', 'domain', 'owner_candidate',
  'classification_candidate', 'related_asset', 'usage_example',
  'sample_query', 'data_product_membership',
]);
export const DISCOVERY_ENRICHMENT_REVIEW_STATES = Object.freeze([
  'PENDING', 'ACCEPTED', 'REJECTED',
]);

function exact(input, fields, label, required=fields) {
  plainObject(input, label); noRawSecrets(input, label);
  const unknown = Object.keys(input).find((field) => !fields.includes(field));
  if(unknown) throw new TypeError(`${label} contains unsupported field ${unknown}.`);
  const missing = required.find((field) => !Object.hasOwn(input, field));
  if(missing) throw new TypeError(`${label} requires ${missing}.`);
  return input;
}

function requireMethod(value, method, label) {
  if(typeof value?.[method] !== 'function') throw new TypeError(
    `${label} requires ${method}.`
  );
}

function evidence(input, label) {
  if(!Array.isArray(input) || !input.length || input.length > 100) throw new TypeError(
    `${label} must contain 1 to 100 references.`
  );
  const result = input.map((reference) => platformValue(reference,
    `${label} reference`, 4096));
  if(new Set(result).size !== result.length) throw new TypeError(
    `${label} contains duplicate references.`
  );
  return result;
}

function value(input, label) {
  if(input === undefined) throw new TypeError(`${label} is required.`);
  noRawSecrets({value: input}, label);
  try { return JSON.parse(JSON.stringify(input)); } catch {
    throw new TypeError(`${label} must be JSON serializable.`);
  }
}

export function validateDiscoveryEnrichmentSuggestion(input) {
  exact(input, ['schemaVersion', 'suggestionId', 'kind', 'targetRef', 'field',
    'currentValue', 'proposedValue', 'sourceEvidenceRefs', 'modelRef',
    'profileRef', 'confidence', 'requestedAt', 'reviewState', 'reviewedAt',
    'reviewerRef', 'reviewReason', 'curationItemRef', 'curationEvidenceRef'],
  'Discovery enrichment suggestion');
  if(input.schemaVersion !== 1 || !DISCOVERY_ENRICHMENT_KINDS.includes(
    input.kind) || !DISCOVERY_ENRICHMENT_REVIEW_STATES.includes(
    input.reviewState)) throw new TypeError(
    'Discovery enrichment suggestion version, kind or state is invalid.'
  );
  const requestedAt = platformValue(input.requestedAt,
    'Discovery enrichment request time', 64);
  if(Number.isNaN(Date.parse(requestedAt))) throw new TypeError(
    'Discovery enrichment request time is invalid.'
  );
  const reviewedAt = input.reviewedAt == null ? null : platformValue(
    input.reviewedAt, 'Discovery enrichment review time', 64);
  if(reviewedAt && Number.isNaN(Date.parse(reviewedAt))) throw new TypeError(
    'Discovery enrichment review time is invalid.'
  );
  if(input.reviewState === 'PENDING' && (reviewedAt || input.reviewerRef ||
      input.reviewReason || input.curationItemRef || input.curationEvidenceRef)) {
    throw new TypeError('Pending enrichment suggestions cannot claim review evidence.');
  }
  if(input.reviewState !== 'PENDING' && (!reviewedAt || !input.reviewerRef ||
      !input.reviewReason || !input.curationItemRef ||
      !input.curationEvidenceRef)) throw new TypeError(
    'Reviewed enrichment suggestions require complete curation evidence.'
  );
  if(input.confidence !== null && (!Number.isFinite(input.confidence) ||
      input.confidence < 0 || input.confidence > 1)) throw new TypeError(
    'Discovery enrichment confidence must be null or between 0 and 1.'
  );
  return immutable({schemaVersion: 1,
    suggestionId: platformValue(input.suggestionId,
      'Discovery enrichment suggestion ID', 512), kind: input.kind,
    targetRef: platformValue(input.targetRef,
      'Discovery enrichment target', 4096),
    field: platformValue(input.field, 'Discovery enrichment field', 512),
    currentValue: value(input.currentValue,
      'Discovery enrichment current value'),
    proposedValue: value(input.proposedValue,
      'Discovery enrichment proposed value'),
    sourceEvidenceRefs: evidence(input.sourceEvidenceRefs,
      'Discovery enrichment source evidence'),
    modelRef: platformValue(input.modelRef,
      'Discovery enrichment model reference', 512),
    profileRef: platformValue(input.profileRef,
      'Discovery enrichment profile reference', 512),
    confidence: input.confidence, requestedAt, reviewState: input.reviewState,
    reviewedAt, reviewerRef: input.reviewerRef == null ? null : platformValue(
      input.reviewerRef, 'Discovery enrichment reviewer', 512),
    reviewReason: input.reviewReason == null ? null : platformValue(
      input.reviewReason, 'Discovery enrichment review reason', 4000),
    curationItemRef: input.curationItemRef == null ? null : platformValue(
      input.curationItemRef, 'Discovery enrichment curation item', 512),
    curationEvidenceRef: input.curationEvidenceRef == null ? null : platformValue(
      input.curationEvidenceRef, 'Discovery enrichment curation evidence', 512)});
}

export class InMemoryDiscoveryEnrichmentStore {
  constructor() { this.current = new Map(); this.history = new Map(); }
  append(input) {
    const suggestion = validateDiscoveryEnrichmentSuggestion(input);
    const prior = this.current.get(suggestion.suggestionId);
    if(prior && prior.reviewState !== 'PENDING') throw new TypeError(
      'Reviewed enrichment suggestion history is immutable.'
    );
    this.current.set(suggestion.suggestionId, suggestion);
    this.history.set(suggestion.suggestionId,
      [...(this.history.get(suggestion.suggestionId) ?? []), suggestion]);
    return suggestion;
  }
  get(suggestionId) { return this.current.get(String(suggestionId)) ?? null; }
  revisions(suggestionId) {
    return [...(this.history.get(String(suggestionId)) ?? [])];
  }
  list() { return [...this.current.values()].sort((left, right) =>
    right.requestedAt.localeCompare(left.requestedAt) ||
    left.suggestionId.localeCompare(right.suggestionId)); }
}

export class DiscoveryEnrichmentService {
  constructor({store, discoveryAPI, curation, aiInterface=null,
    idFactory, now=() => new Date().toISOString()}={}) {
    for(const method of ['append', 'get', 'revisions', 'list']) requireMethod(
      store, method, 'Discovery enrichment store');
    requireMethod(discoveryAPI, 'get_entity',
      'Discovery enrichment service API');
    for(const method of ['open', 'resolve', 'dismiss']) requireMethod(curation,
      method, 'Discovery enrichment curation authority');
    if(aiInterface !== null) requireMethod(aiInterface,
      'requestDiscoveryEnrichment', 'Discovery AI Interface');
    if(typeof idFactory !== 'function') throw new TypeError(
      'Discovery enrichment requires an ID authority.'
    );
    this.store = store; this.discoveryAPI = discoveryAPI;
    this.curation = curation; this.aiInterface = aiInterface;
    this.idFactory = idFactory; this.now = now;
  }

  capabilities() {
    return immutable({review: true, aiEnrichment: this.aiInterface !== null,
      aiDisabledReason: this.aiInterface === null ?
        'AI Interface enrichment is not configured.' : null});
  }

  async request(input, {security, aiContext={}}={}) {
    exact(input, ['kind', 'targetRef', 'field', 'currentValue',
      'sourceEvidenceRefs'], 'Discovery enrichment request');
    if(!DISCOVERY_ENRICHMENT_KINDS.includes(input.kind)) throw new TypeError(
      'Discovery enrichment kind is invalid.'
    );
    requireMethod(security, 'requestEnrichmentSuggestion',
      'Discovery enrichment security');
    const targetRef = platformValue(input.targetRef,
      'Discovery enrichment target', 4096);
    if(security.requestEnrichmentSuggestion(targetRef, input.kind) !== true) {
      throw new Error('Discovery enrichment request denied.');
    }
    if(!this.aiInterface) throw new Error(
      'AI Interface enrichment is disabled; deterministic Discovery remains available.'
    );
    const entity = await this.discoveryAPI.get_entity({canonicalRef: targetRef},
      {security});
    const sourceEvidenceRefs = evidence(input.sourceEvidenceRefs,
      'Discovery enrichment source evidence');
    const response = await this.aiInterface.requestDiscoveryEnrichment({
      kind: input.kind, targetRef, field: platformValue(input.field,
        'Discovery enrichment field', 512),
      currentValue: value(input.currentValue,
        'Discovery enrichment current value'), sourceEvidenceRefs,
      permittedContext: {resultRef: entity.resultRef,
        canonicalRef: entity.canonicalRef, entityClass: entity.entityClass,
        name: entity.name, description: entity.description,
        businessTerms: entity.businessTerms, synonyms: entity.synonyms,
        domainRefs: entity.domainRefs, ownerRefs: entity.ownerRefs,
        tags: entity.tags, classification: entity.classification,
        schemaSummary: entity.schemaSummary,
        nativeMetadataSummary: entity.nativeMetadataSummary}}, aiContext);
    exact(response, ['proposedValue', 'modelRef', 'profileRef', 'confidence',
      'sourceEvidenceRefs'], 'Discovery AI enrichment response',
    ['proposedValue', 'modelRef', 'profileRef', 'sourceEvidenceRefs']);
    const responseEvidence = evidence(response.sourceEvidenceRefs,
      'Discovery AI enrichment response evidence');
    if(responseEvidence.some((reference) => !sourceEvidenceRefs.includes(reference))) {
      throw new Error('AI enrichment cited evidence outside its permitted context.');
    }
    return this.store.append({schemaVersion: 1,
      suggestionId: platformValue(this.idFactory(),
        'Discovery enrichment suggestion ID', 512), kind: input.kind,
      targetRef, field: input.field, currentValue: input.currentValue,
      proposedValue: response.proposedValue, sourceEvidenceRefs: responseEvidence,
      modelRef: response.modelRef, profileRef: response.profileRef,
      confidence: response.confidence ?? null, requestedAt: this.now(),
      reviewState: 'PENDING', reviewedAt: null, reviewerRef: null,
      reviewReason: null, curationItemRef: null, curationEvidenceRef: null});
  }

  async accept(suggestionId, {proposedValue, actorRef, reason,
    security}={}) {
    const suggestion = this._pending(suggestionId);
    requireMethod(security, 'reviewEnrichmentSuggestion',
      'Discovery enrichment security');
    if(security.reviewEnrichmentSuggestion(suggestion, 'ACCEPT') !== true) {
      throw new Error('Discovery enrichment acceptance denied.');
    }
    let acceptedValue = proposedValue === undefined ? suggestion.proposedValue :
      value(proposedValue, 'Corrected Discovery enrichment value');
    if(JSON.stringify(acceptedValue) !== JSON.stringify(suggestion.proposedValue)) {
      requireMethod(security, 'correctEnrichmentSuggestion',
        'Discovery enrichment correction security');
      if(security.correctEnrichmentSuggestion(suggestion) !== true) throw new Error(
        'Discovery enrichment correction denied.'
      );
    }
    actorRef = platformValue(actorRef, 'Discovery enrichment reviewer', 512);
    reason = platformValue(reason, 'Discovery enrichment review reason', 4000);
    const item = this.curation.open({type: 'ai_suggestion',
      targetRef: suggestion.targetRef,
      summary: `Review AI ${suggestion.kind} suggestion for ${suggestion.field}.`,
      evidenceRefs: suggestion.sourceEvidenceRefs}, {security});
    const result = await this.curation.resolve(item.itemId, {
      action: 'accept_ai_suggestion', value: {field: suggestion.field,
        proposedValue: acceptedValue, suggestionId: suggestion.suggestionId},
      actorRef, reason, sourceEvidenceRefs: suggestion.sourceEvidenceRefs,
      security});
    return this.store.append({...suggestion, proposedValue: acceptedValue,
      reviewState: 'ACCEPTED', reviewedAt: this.now(), reviewerRef: actorRef,
      reviewReason: reason, curationItemRef: result.item.itemId,
      curationEvidenceRef: result.evidence.evidenceId});
  }

  async reject(suggestionId, {actorRef, reason, security}={}) {
    const suggestion = this._pending(suggestionId);
    requireMethod(security, 'reviewEnrichmentSuggestion',
      'Discovery enrichment security');
    if(security.reviewEnrichmentSuggestion(suggestion, 'REJECT') !== true) {
      throw new Error('Discovery enrichment rejection denied.');
    }
    actorRef = platformValue(actorRef, 'Discovery enrichment reviewer', 512);
    reason = platformValue(reason, 'Discovery enrichment review reason', 4000);
    const item = this.curation.open({type: 'ai_suggestion',
      targetRef: suggestion.targetRef,
      summary: `Review AI ${suggestion.kind} suggestion for ${suggestion.field}.`,
      evidenceRefs: suggestion.sourceEvidenceRefs}, {security});
    const result = this.curation.dismiss(item.itemId, {actorRef, reason,
      sourceEvidenceRefs: suggestion.sourceEvidenceRefs, security});
    return this.store.append({...suggestion, reviewState: 'REJECTED',
      reviewedAt: this.now(), reviewerRef: actorRef, reviewReason: reason,
      curationItemRef: result.item.itemId,
      curationEvidenceRef: result.evidence.evidenceId});
  }

  get(suggestionId, {security}={}) {
    const suggestion = this._suggestion(suggestionId);
    requireMethod(security, 'viewEnrichmentSuggestion',
      'Discovery enrichment security');
    if(security.viewEnrichmentSuggestion(suggestion) !== true) throw new Error(
      'Discovery enrichment suggestion view denied.'
    );
    return suggestion;
  }

  list({reviewState=null, security}={}) {
    requireMethod(security, 'viewEnrichmentSuggestion',
      'Discovery enrichment security');
    if(reviewState !== null && !DISCOVERY_ENRICHMENT_REVIEW_STATES.includes(
      reviewState)) throw new TypeError('Discovery enrichment review filter is invalid.');
    return immutable(this.store.list().filter((suggestion) =>
      (!reviewState || suggestion.reviewState === reviewState) &&
      security.viewEnrichmentSuggestion(suggestion) === true));
  }

  history(suggestionId, {security}={}) {
    const suggestion = this._suggestion(suggestionId);
    requireMethod(security, 'viewEnrichmentSuggestionHistory',
      'Discovery enrichment security');
    if(security.viewEnrichmentSuggestionHistory(suggestion) !== true) {
      throw new Error('Discovery enrichment suggestion history denied.');
    }
    return immutable(this.store.revisions(suggestion.suggestionId));
  }

  _suggestion(suggestionId) {
    const suggestion = this.store.get(platformValue(suggestionId,
      'Discovery enrichment suggestion ID', 512));
    if(!suggestion) throw new Error(
      `Discovery enrichment suggestion ${suggestionId} was not found.`
    );
    return suggestion;
  }

  _pending(suggestionId) {
    const suggestion = this._suggestion(suggestionId);
    if(suggestion.reviewState !== 'PENDING') throw new TypeError(
      `Discovery enrichment suggestion is already ${suggestion.reviewState}.`
    );
    return suggestion;
  }
}
