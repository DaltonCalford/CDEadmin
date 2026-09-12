/////////////////////////////////////////////////////////////
// Deterministic, explainable Discovery ranking authority.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';

export const DISCOVERY_RANKING_COMPONENTS = Object.freeze([
  'lexical', 'semantic', 'business', 'trust', 'quality', 'freshness',
  'usage', 'context', 'lineage', 'documentation',
]);
export const DISCOVERY_RANKING_BOOSTS = Object.freeze([
  'exact_visible_qualified_name', 'exact_business_term',
  'active_certified_data_product',
]);
export const DISCOVERY_RANKING_PENALTIES = Object.freeze([
  'deprecated', 'retired_archived', 'critical_quality_failure',
  'freshness_sla_breach', 'no_owner_steward',
  'unstable_breaking_schema',
]);
export const DISCOVERY_TIE_BREAK = Object.freeze([
  'exact_visible_name', 'certification', 'quality', 'freshness', 'usage',
  'stable_id',
]);

export const BALANCED_DISCOVERY_RANKING_PROFILE = immutable({
  schemaVersion: 1,
  profileId: 'cdeadmin.discovery.ranking.balanced',
  name: 'Balanced',
  weights: {lexical: 0.22, semantic: 0.20, business: 0.12, trust: 0.10,
    quality: 0.08, freshness: 0.07, usage: 0.07, context: 0.05,
    lineage: 0.04, documentation: 0.05},
  boosts: {exact_visible_qualified_name: 0.12,
    exact_business_term: 0.08, active_certified_data_product: 0.06},
  penalties: {deprecated: -0.30, retired_archived: -0.45,
    critical_quality_failure: -0.20, freshness_sla_breach: -0.15,
    no_owner_steward: -0.03, unstable_breaking_schema: -0.08},
  publishedRevision: 'cdeadmin.discovery.ranking.balanced@1',
  status: 'published',
});

const PROFILE_FIELDS = Object.freeze(['schemaVersion', 'profileId', 'name',
  'weights', 'boosts', 'penalties', 'publishedRevision', 'status']);
const PROFILE_REQUIRED_FIELDS = Object.freeze(PROFILE_FIELDS.filter((field) =>
  field !== 'status'));
const EVIDENCE_FIELDS = Object.freeze(['components', 'flags', 'tieBreak',
  'explanation']);
const FLAG_FIELDS = Object.freeze([
  'activeCertifiedDataProduct', 'deprecated', 'retiredArchived',
  'criticalQualityFailure', 'freshnessSlaBreach', 'noOwnerSteward',
  'unstableBreakingSchema',
]);
const TIE_FIELDS = Object.freeze(['certification', 'quality', 'freshness',
  'usage']);
const AUTHORITY_COMPONENTS = Object.freeze(['business', 'trust', 'quality',
  'freshness', 'usage', 'context', 'lineage', 'documentation']);
const REQUIRED_AUTHORITY_COMPONENTS = Object.freeze(AUTHORITY_COMPONENTS.filter(
  (field) => !['business', 'lineage'].includes(field)));
const EPSILON = 1e-9;

function exactFields(value, fields, label, required=fields) {
  plainObject(value, label);
  const unknown = Object.keys(value).find((field) => !fields.includes(field));
  if(unknown) throw new TypeError(`${label} contains unsupported field ${unknown}.`);
  const missing = required.find((field) => !Object.hasOwn(value, field));
  if(missing) throw new TypeError(`${label} requires ${missing}.`);
}

function boundedScore(value, label, {nullable=false, minimum=0}={}) {
  if(nullable && (value === null || value === undefined)) return null;
  if(!Number.isFinite(value) || value < minimum || value > 1) throw new TypeError(
    `${label} must be between ${minimum} and 1.`
  );
  return value;
}

export function validateDiscoveryRankingProfile(input, {requirePublished=false}={}) {
  exactFields(input, PROFILE_FIELDS, 'Discovery ranking profile',
    PROFILE_REQUIRED_FIELDS);
  noRawSecrets(input, 'Discovery ranking profile');
  if(input.schemaVersion !== 1) throw new TypeError(
    'Discovery ranking profile schema version is invalid.'
  );
  const status = input.status ?? 'draft';
  if(!['draft', 'published', 'retired'].includes(status)) throw new TypeError(
    'Discovery ranking profile status is invalid.'
  );
  if(requirePublished && status !== 'published') throw new TypeError(
    'Discovery search requires a published ranking profile.'
  );
  exactFields(input.weights, DISCOVERY_RANKING_COMPONENTS,
    'Discovery ranking weights');
  exactFields(input.boosts, DISCOVERY_RANKING_BOOSTS,
    'Discovery ranking boosts');
  exactFields(input.penalties, DISCOVERY_RANKING_PENALTIES,
    'Discovery ranking penalties');
  const weights = {};
  for(const field of DISCOVERY_RANKING_COMPONENTS) weights[field] = boundedScore(
    input.weights[field], `Discovery ranking weight ${field}`);
  const total = Object.values(weights).reduce((sum, value) => sum + value, 0);
  if(Math.abs(total - 1) > EPSILON) throw new TypeError(
    'Discovery ranking weights must sum exactly to 1.00.'
  );
  const boosts = {};
  for(const field of DISCOVERY_RANKING_BOOSTS) boosts[field] = boundedScore(
    input.boosts[field], `Discovery ranking boost ${field}`);
  const penalties = {};
  for(const field of DISCOVERY_RANKING_PENALTIES) penalties[field] = boundedScore(
    input.penalties[field], `Discovery ranking penalty ${field}`, {minimum: -1});
  if(Object.values(penalties).some((value) => value > 0)) throw new TypeError(
    'Discovery ranking penalties must not be positive.'
  );
  return immutable({schemaVersion: 1,
    profileId: platformValue(input.profileId, 'Discovery ranking profile ID'),
    name: platformValue(input.name, 'Discovery ranking profile name'), weights,
    boosts, penalties, publishedRevision: platformValue(input.publishedRevision,
      'Discovery ranking published revision'), status});
}

export function validateDiscoveryFeatureEvidence(input) {
  exactFields(input, EVIDENCE_FIELDS, 'Discovery feature evidence');
  exactFields(input.components, AUTHORITY_COMPONENTS,
    'Discovery authority components', REQUIRED_AUTHORITY_COMPONENTS);
  exactFields(input.flags, FLAG_FIELDS, 'Discovery adjustment flags');
  exactFields(input.tieBreak, TIE_FIELDS, 'Discovery tie-break evidence');
  if(!Array.isArray(input.explanation)) throw new TypeError(
    'Discovery feature explanation must be an array.'
  );
  const components = {};
  for(const [field, value] of Object.entries(input.components)) {
    components[field] = boundedScore(value,
      `Discovery authority component ${field}`, {nullable: true});
  }
  for(const field of AUTHORITY_COMPONENTS) if(!Object.hasOwn(components, field)) {
    components[field] = null;
  }
  const flags = {};
  for(const field of FLAG_FIELDS) {
    if(typeof input.flags[field] !== 'boolean') throw new TypeError(
      `Discovery adjustment flag ${field} must be boolean.`
    );
    flags[field] = input.flags[field];
  }
  const tieBreak = {};
  for(const field of TIE_FIELDS) tieBreak[field] = boundedScore(
    input.tieBreak[field], `Discovery tie-break ${field}`, {nullable: true});
  const explanation = input.explanation.map((item) => {
    plainObject(item, 'Discovery feature explanation item');
    noRawSecrets(item, 'Discovery feature explanation item');
    return immutable({...item});
  });
  return immutable({components, flags, tieBreak, explanation});
}

function sourceScore(value) {
  return value === null || value === undefined ? null : boundedScore(value,
    'Discovery candidate score', {nullable: true});
}

function neutral(value) { return value === null ? 0.5 : value; }
function clamp(value) { return Math.max(0, Math.min(1, value)); }

export class DiscoveryRankingEngine {
  constructor({featureAuthority}={}) {
    if(!featureAuthority || typeof featureAuthority.evaluate !== 'function') {
      throw new TypeError('Discovery ranking requires a feature authority.');
    }
    this.featureAuthority = featureAuthority;
  }

  rank(candidates, {profile=BALANCED_DISCOVERY_RANKING_PROFILE, query,
    security, context={}}={}) {
    if(!Array.isArray(candidates)) throw new TypeError(
      'Discovery ranking candidates must be an array.'
    );
    profile = validateDiscoveryRankingProfile(profile, {requirePublished: true});
    const ranked = candidates.map((candidate) => {
      plainObject(candidate, 'Discovery ranking candidate');
      const evidence = validateDiscoveryFeatureEvidence(
        this.featureAuthority.evaluate(candidate.document,
          {candidate, query, security, context})
      );
      if(!Array.isArray(candidate.sourceEvidence ?? [])) throw new TypeError(
        'Discovery candidate source evidence must be an array.'
      );
      const sourceEvidence = (candidate.sourceEvidence ?? []).map((item) => {
        plainObject(item, 'Discovery candidate source evidence item');
        noRawSecrets(item, 'Discovery candidate source evidence item');
        return immutable({...item});
      });
      const components = {
        lexical: sourceScore(candidate.lexical),
        semantic: sourceScore(candidate.semantic),
        business: sourceScore(candidate.business) ?? evidence.components.business,
        trust: evidence.components.trust,
        quality: evidence.components.quality,
        freshness: evidence.components.freshness,
        usage: evidence.components.usage,
        context: evidence.components.context,
        lineage: sourceScore(candidate.graph) ?? sourceScore(candidate.lineage) ??
          evidence.components.lineage,
        documentation: evidence.components.documentation,
      };
      const treated = Object.fromEntries(Object.entries(components).map(
        ([field, value]) => [field, neutral(value)]));
      const weighted = Object.fromEntries(DISCOVERY_RANKING_COMPONENTS.map(
        (field) => [field, treated[field] * profile.weights[field]]));
      const boosts = {};
      if(candidate.exactVisibleQualifiedName) boosts.exact_visible_qualified_name =
        profile.boosts.exact_visible_qualified_name;
      if(candidate.exactBusinessTerm) boosts.exact_business_term =
        profile.boosts.exact_business_term;
      if(evidence.flags.activeCertifiedDataProduct) {
        boosts.active_certified_data_product =
          profile.boosts.active_certified_data_product;
      }
      const penalties = {};
      const penaltyFlags = {deprecated: evidence.flags.deprecated,
        retired_archived: evidence.flags.retiredArchived,
        critical_quality_failure: evidence.flags.criticalQualityFailure,
        freshness_sla_breach: evidence.flags.freshnessSlaBreach,
        no_owner_steward: evidence.flags.noOwnerSteward,
        unstable_breaking_schema: evidence.flags.unstableBreakingSchema};
      for(const [field, active] of Object.entries(penaltyFlags)) {
        if(active) penalties[field] = profile.penalties[field];
      }
      const baseScore = Object.values(weighted).reduce((sum, value) =>
        sum + value, 0);
      const finalScore = clamp(baseScore + Object.values(boosts).reduce(
        (sum, value) => sum + value, 0) + Object.values(penalties).reduce(
        (sum, value) => sum + value, 0));
      return immutable({...candidate, score: finalScore,
        explanation: {profileId: profile.profileId,
          profileRevision: profile.publishedRevision,
          componentScores: components, neutralizedComponentScores: treated,
          weightedComponentScores: weighted, boosts, penalties, finalScore,
          candidateEvidence: sourceEvidence, evidence: evidence.explanation},
        tieBreak: {exactVisibleName: Boolean(candidate.exactVisibleName),
          certification: neutral(evidence.tieBreak.certification),
          quality: neutral(evidence.tieBreak.quality),
          freshness: neutral(evidence.tieBreak.freshness),
          usage: neutral(evidence.tieBreak.usage)}});
    });
    return immutable(ranked.sort((left, right) => right.score - left.score ||
      Number(right.tieBreak.exactVisibleName) - Number(left.tieBreak.exactVisibleName) ||
      right.tieBreak.certification - left.tieBreak.certification ||
      right.tieBreak.quality - left.tieBreak.quality ||
      right.tieBreak.freshness - left.tieBreak.freshness ||
      right.tieBreak.usage - left.tieBreak.usage ||
      left.document.canonicalRef.localeCompare(right.document.canonicalRef)));
  }
}
