/////////////////////////////////////////////////////////////
// Certification, profiling and ranking-feature authorities.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';
import {PlatformRegistryError, stablePlatformId} from
  '../../platform/PlatformRegistry';
import {validateCertificationRecord, validateDiscoveryKnowledgeAsset,
  validateProfileStatistic} from './DiscoveryKnowledgeContracts';

const CERTIFICATION_SCORE = Object.freeze({CERTIFIED: 1,
  CERTIFIED_WITH_CONDITIONS: 0.85, IN_REVIEW: 0.6, UNREVIEWED: 0.5,
  EXPIRED: 0.4, REJECTED: 0.2, REVOKED: 0});
const QUALITY_SCORE = Object.freeze({CRITICAL_FAILURE: 0,
  CRITICAL_UNKNOWN_STALE: 0.4, NONCRITICAL_FAILURE: 0.7, PASS: 1,
  NOT_DEFINED: 0.5});
const EVIDENCE_FIELDS = Object.freeze(['certificationState', 'qualityState',
  'freshnessScore', 'weightedUsageEvents', 'contextRelevance',
  'lineageRelevance', 'businessRelevance', 'usageDocumentation', 'examples',
  'activeCertifiedDataProduct', 'freshnessSlaBreach',
  'unstableBreakingSchema']);

function exactFields(input, fields, label) {
  plainObject(input, label); noRawSecrets(input, label);
  const unknown = Object.keys(input).find((field) => !fields.includes(field));
  if(unknown) throw new TypeError(`${label} contains unsupported field ${unknown}.`);
  const missing = fields.find((field) => !Object.hasOwn(input, field));
  if(missing) throw new TypeError(`${label} requires ${missing}.`);
}

function nullableScore(value, label) {
  if(value === null || value === undefined) return null;
  if(!Number.isFinite(value) || value < 0 || value > 1) throw new TypeError(
    `${label} must be between 0 and 1.`
  );
  return value;
}

function bool(value, label) {
  if(typeof value !== 'boolean') throw new TypeError(`${label} must be boolean.`);
  return value;
}

export function discoveryFreshnessScore({observedAt, targetSeconds,
  graceSeconds=targetSeconds, now=Date.now()}={}) {
  observedAt = platformValue(observedAt, 'Discovery freshness observation', 64);
  const observed = Date.parse(observedAt);
  if(Number.isNaN(observed)) throw new TypeError(
    'Discovery freshness observation is invalid.'
  );
  if(!Number.isFinite(targetSeconds) || targetSeconds <= 0 ||
      !Number.isFinite(graceSeconds) || graceSeconds <= 0) throw new TypeError(
    'Discovery freshness target and grace must be positive.'
  );
  const instant = typeof now === 'number' ? now : Date.parse(now);
  if(!Number.isFinite(instant)) throw new TypeError(
    'Discovery freshness evaluation time is invalid.'
  );
  const ageSeconds = Math.max(0, (instant - observed) / 1000);
  if(ageSeconds <= targetSeconds) return 1;
  if(ageSeconds >= targetSeconds + graceSeconds) return 0;
  return 1 - (ageSeconds - targetSeconds) / graceSeconds;
}

export function validateDiscoveryFeatureSource(input) {
  exactFields(input, EVIDENCE_FIELDS, 'Discovery ranking feature source');
  if(input.certificationState !== null &&
      !Object.hasOwn(CERTIFICATION_SCORE, input.certificationState)) throw new TypeError(
    'Discovery certification feature state is invalid.'
  );
  if(input.qualityState !== null &&
      !Object.hasOwn(QUALITY_SCORE, input.qualityState)) throw new TypeError(
    'Discovery quality feature state is invalid.'
  );
  if(input.weightedUsageEvents !== null && (!Number.isFinite(
    input.weightedUsageEvents) || input.weightedUsageEvents < 0)) throw new TypeError(
    'Discovery weighted usage events must be non-negative.'
  );
  const result = {...input};
  for(const field of ['freshnessScore', 'contextRelevance', 'lineageRelevance',
    'businessRelevance']) result[field] = nullableScore(input[field],
    `Discovery ${field}`);
  for(const field of ['usageDocumentation', 'examples',
    'activeCertifiedDataProduct', 'freshnessSlaBreach',
    'unstableBreakingSchema']) result[field] = bool(input[field],
    `Discovery ${field}`);
  return immutable(result);
}

export class DiscoveryTrustFeatureAuthority {
  constructor({evidenceResolver, popularitySaturation=1000}={}) {
    if(typeof evidenceResolver !== 'function') throw new TypeError(
      'Discovery trust features require an evidence resolver.'
    );
    if(!Number.isFinite(popularitySaturation) || popularitySaturation <= 0) {
      throw new TypeError('Discovery popularity saturation must be positive.');
    }
    this.evidenceResolver = evidenceResolver;
    this.popularitySaturation = popularitySaturation;
  }

  evaluate(document, {security, candidate, query, context}={}) {
    const source = validateDiscoveryFeatureSource(this.evidenceResolver(document,
      {candidate, query, context}));
    const admit = (signal) => security?.admitSignal(document, signal) === true;
    const certification = admit('trust') ? (source.certificationState === null ?
      null : CERTIFICATION_SCORE[source.certificationState]) : null;
    const quality = admit('quality') ? (source.qualityState === null ? null :
      QUALITY_SCORE[source.qualityState]) : null;
    const freshness = admit('freshness') ? source.freshnessScore : null;
    const usage = admit('usage') && source.weightedUsageEvents !== null ? Math.min(1,
      Math.log1p(source.weightedUsageEvents) /
      Math.log1p(this.popularitySaturation)) : null;
    const contextScore = admit('context') ? source.contextRelevance : null;
    const lineage = admit('lineage') ? source.lineageRelevance : null;
    const business = admit('business') ? source.businessRelevance : null;
    let documentation = null;
    if(admit('documentation')) documentation =
      (document.description ? 0.30 : 0) + (document.ownerRefs.length ? 0.20 : 0) +
      (document.stewardRefs.length ? 0.10 : 0) +
      (document.businessTerms.length ? 0.15 : 0) +
      (source.usageDocumentation ? 0.15 : 0) + (source.examples ? 0.10 : 0);
    const flags = {activeCertifiedDataProduct: admit('trust') &&
      source.activeCertifiedDataProduct, deprecated: admit('trust') &&
      document.deprecationState === 'DEPRECATED', retiredArchived: admit('trust') &&
      ['RETIRED', 'ARCHIVED'].includes(document.deprecationState),
    criticalQualityFailure: admit('quality') &&
      source.qualityState === 'CRITICAL_FAILURE', freshnessSlaBreach:
      admit('freshness') && source.freshnessSlaBreach, noOwnerSteward:
      admit('documentation') && !document.ownerRefs.length &&
      !document.stewardRefs.length, unstableBreakingSchema: admit('trust') &&
      source.unstableBreakingSchema};
    const explanation = [];
    if(certification !== null) explanation.push({signal: 'certification',
      state: source.certificationState, score: certification});
    if(quality !== null) explanation.push({signal: 'quality',
      state: source.qualityState, score: quality});
    if(freshness !== null) explanation.push({signal: 'freshness', score: freshness,
      slaBreach: source.freshnessSlaBreach});
    if(usage !== null) explanation.push({signal: 'usage', score: usage,
      weightedEvents: source.weightedUsageEvents});
    return immutable({components: {business, trust: certification, quality,
      freshness, usage, context: contextScore, lineage, documentation}, flags,
    tieBreak: {certification, quality, freshness, usage}, explanation});
  }
}

export class InMemoryCertificationStore {
  constructor() { this.byId = new Map(); this.byTarget = new Map(); }

  save(input) {
    const record = validateCertificationRecord(input);
    if(this.byId.has(record.certificationId)) throw new PlatformRegistryError(
      'duplicate', `Certification already exists: ${record.certificationId}`,
      record.certificationId);
    this.byId.set(record.certificationId, record);
    const items = this.byTarget.get(record.targetRef) ?? [];
    this.byTarget.set(record.targetRef, [...items, record.certificationId]);
    return record;
  }

  get(id) {
    const record = this.byId.get(platformValue(id, 'Certification ID'));
    if(!record) throw new PlatformRegistryError('not_found',
      'Certification record was not found.', id);
    return record;
  }

  history(targetRef) {
    return immutable((this.byTarget.get(platformValue(targetRef,
      'Certification target', 4096)) ?? []).map((id) => this.byId.get(id)));
  }
}

function certificationInput(input, defaults={}) {
  plainObject(input, 'Certification operation'); noRawSecrets(input,
    'Certification operation');
  return {schemaVersion: 1, certificationId: input.certificationId,
    targetRef: input.targetRef, targetRevision: input.targetRevision,
    profileRef: input.profileRef, state: input.state ?? defaults.state,
    reviewerRefs: input.reviewerRefs ?? [],
    criteriaEvidence: input.criteriaEvidence ?? [],
    reviewedAt: input.reviewedAt ?? defaults.reviewedAt ?? null,
    expiresAt: input.expiresAt ?? defaults.expiresAt ?? null,
    conditions: input.conditions ?? '', notes: input.notes ?? '',
    supersedesRef: input.supersedesRef ?? defaults.supersedesRef ?? null};
}

export class CertificationService {
  constructor({store, evidenceAuthority, now=() => new Date().toISOString(),
    idFactory}={}) {
    if(!store || typeof store.save !== 'function' || typeof store.get !== 'function' ||
        typeof store.history !== 'function') throw new TypeError(
      'Certification service requires a complete record store.'
    );
    if(typeof idFactory !== 'function') throw new TypeError(
      'Certification service requires an ID factory.'
    );
    if(!evidenceAuthority || typeof evidenceAuthority.evaluate !== 'function') {
      throw new TypeError('Certification service requires an evidence authority.');
    }
    this.store = store; this.now = now; this.idFactory = idFactory;
    this.evidenceAuthority = evidenceAuthority;
  }

  request(input, profile) {
    profile = validateDiscoveryKnowledgeAsset('CertificationProfile', profile);
    if(profile.status !== 'published') throw new TypeError(
      'Certification requests require a published criteria profile.'
    );
    return this.store.save(validateCertificationRecord(certificationInput({
      ...input, certificationId: input.certificationId ?? this.idFactory(),
      state: 'IN_REVIEW', profileRef: profile.profileId,
      criteriaEvidence: [], reviewedAt: null, expiresAt: null})));
  }

  async review(requestId, input, profile, context={}) {
    const prior = this.store.get(requestId);
    if(prior.state !== 'IN_REVIEW') throw new TypeError(
      'Only an in-review certification can be reviewed.'
    );
    profile = validateDiscoveryKnowledgeAsset('CertificationProfile', profile);
    if(profile.profileId !== prior.profileRef || profile.status !== 'published') {
      throw new TypeError('Certification review profile does not match the request.');
    }
    if(!['CERTIFIED', 'CERTIFIED_WITH_CONDITIONS', 'REJECTED'].includes(
      input.state)) throw new TypeError('Certification review decision is invalid.');
    const evaluation = await this.evidenceAuthority.evaluate(prior, profile, context);
    exactFields(evaluation, ['criteriaEvidence', 'ownerPresent', 'contractCurrent',
      'qualityPassing'], 'Certification evidence evaluation');
    if(!Array.isArray(evaluation.criteriaEvidence)) throw new TypeError(
      'Certification evidence authority must return criteria evidence.'
    );
    const evidence = evaluation.criteriaEvidence.map((item) => {
      exactFields(item, ['criterionId', 'passed', 'evidence'],
        'Certification criterion result');
      if(typeof item.passed !== 'boolean') throw new TypeError(
        'Certification criterion result passed must be boolean.'
      );
      plainObject(item.evidence, 'Certification criterion result evidence');
      noRawSecrets(item.evidence, 'Certification criterion result evidence');
      return immutable({...item, evidence: {...item.evidence}});
    });
    if(new Set(evidence.map((item) => item.criterionId)).size !== evidence.length) {
      throw new TypeError('Certification criterion results must be unique.');
    }
    const byId = new Map(evidence.map((item) => [item.criterionId, item]));
    if(evidence.some((item) => !profile.criteria.some((criterion) =>
      criterion.criterionId === item.criterionId))) throw new TypeError(
      'Certification evidence contains an unknown criterion.'
    );
    const certifying = ['CERTIFIED', 'CERTIFIED_WITH_CONDITIONS'].includes(
      input.state);
    if(certifying && profile.criteria.some((item) => item.required &&
        !byId.get(item.criterionId)?.passed)) throw new TypeError(
      'Certification required criteria have not passed.'
    );
    for(const field of ['ownerPresent', 'contractCurrent', 'qualityPassing']) {
      if(typeof evaluation[field] !== 'boolean') throw new TypeError(
        `Certification ${field} evidence must be boolean.`
      );
    }
    if(certifying && ((profile.requireOwner && !evaluation.ownerPresent) ||
        (profile.requireContract && !evaluation.contractCurrent) ||
        (profile.requireQuality && !evaluation.qualityPassing))) throw new TypeError(
      'Certification governance requirements have not passed.'
    );
    const reviewedAt = this.now();
    const expiresAt = input.expiresAt ?? (input.state === 'REJECTED' ? null :
      new Date(Date.parse(reviewedAt) + profile.expirationDays * 86400000)
        .toISOString());
    return this.store.save(validateCertificationRecord(certificationInput({
      ...prior, ...input, criteriaEvidence: evidence,
      certificationId: input.certificationId ?? this.idFactory(),
      reviewedAt, expiresAt, supersedesRef: prior.certificationId})));
  }

  revoke(certificationId, input={}) {
    const prior = this.store.get(certificationId);
    if(!['CERTIFIED', 'CERTIFIED_WITH_CONDITIONS'].includes(prior.state)) {
      throw new TypeError('Only a current certification can be revoked.');
    }
    return this.store.save(validateCertificationRecord(certificationInput({
      ...prior, ...input, certificationId: input.certificationId ?? this.idFactory(),
      state: 'REVOKED', reviewedAt: this.now(), expiresAt: null,
      supersedesRef: prior.certificationId})));
  }

  current(targetRef) {
    const history = this.store.history(targetRef);
    if(!history.length) return null;
    const record = history.at(-1);
    if(['CERTIFIED', 'CERTIFIED_WITH_CONDITIONS'].includes(record.state) &&
        record.expiresAt && Date.parse(record.expiresAt) <= Date.parse(this.now())) {
      return immutable({...record, state: 'EXPIRED'});
    }
    return record;
  }
}

export class DiscoveryProfilerRegistry {
  constructor() { this.items = new Map(); }

  register({provider, version, profile}) {
    provider = stablePlatformId(provider, 'Discovery profiler provider');
    if(this.items.has(provider)) throw new PlatformRegistryError('duplicate',
      `Discovery profiler already registered: ${provider}`, provider);
    if(typeof profile !== 'function') throw new TypeError(
      'Discovery profiler requires a profile implementation.'
    );
    const item = immutable({provider, version: platformValue(version,
      'Discovery profiler version'), profile});
    this.items.set(provider, item); return () => this.items.delete(provider);
  }

  get(provider) {
    const item = this.items.get(stablePlatformId(provider,
      'Discovery profiler provider'));
    if(!item) throw new PlatformRegistryError('not_found',
      `No Discovery profiler is installed for ${provider}.`, provider);
    return item;
  }
}

export class ProfileStatisticsService {
  constructor({profilers, now=() => new Date().toISOString()}={}) {
    if(!profilers || typeof profilers.get !== 'function') throw new TypeError(
      'Profile statistics require a profiler registry.'
    );
    this.profilers = profilers; this.now = now; this.revisions = new Map();
  }

  async profile(input, {security}={}) {
    plainObject(input, 'Discovery profile request'); noRawSecrets(input,
      'Discovery profile request');
    if(typeof security?.canProfile !== 'function' ||
        typeof security?.admitStatistic !== 'function') throw new TypeError(
      'Discovery profiling requires explicit profile security.'
    );
    const targetRef = platformValue(input.targetRef,
      'Discovery profile target', 4096);
    if(security.canProfile(targetRef) !== true) throw new PlatformRegistryError(
      'forbidden', 'Discovery profiling is not permitted.', targetRef);
    const provider = this.profilers.get(input.provider);
    const result = await provider.profile(immutable({...input, targetRef}));
    if(!Array.isArray(result)) throw new TypeError(
      'Discovery profiler must return a statistics array.'
    );
    const statistics = result.map(validateProfileStatistic).filter((item) =>
      security.admitStatistic(targetRef, item) === true);
    const revision = immutable({revisionId: platformValue(input.revisionId,
      'Discovery profile revision'), targetRef, resourceRevision: platformValue(
      input.resourceRevision, 'Discovery profile resource revision', 4096),
    provider: provider.provider, providerVersion: provider.version,
    capturedAt: this.now(), statistics});
    const history = this.revisions.get(targetRef) ?? [];
    if(history.some((item) => item.revisionId === revision.revisionId)) {
      throw new PlatformRegistryError('duplicate',
        `Discovery profile revision already exists: ${revision.revisionId}`,
        revision.revisionId);
    }
    this.revisions.set(targetRef, [...history, revision]); return revision;
  }

  history(targetRef, {security}={}) {
    targetRef = platformValue(targetRef, 'Discovery profile target', 4096);
    if(typeof security?.canViewProfile !== 'function' ||
        security.canViewProfile(targetRef) !== true) throw new PlatformRegistryError(
      'forbidden', 'Discovery profile statistics are not permitted.', targetRef
    );
    return immutable([...(this.revisions.get(targetRef) ?? [])]);
  }
}
