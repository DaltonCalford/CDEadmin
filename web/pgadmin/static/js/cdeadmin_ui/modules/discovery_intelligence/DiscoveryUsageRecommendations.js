/////////////////////////////////////////////////////////////
// Canonical usage aggregation and explainable recommendations.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';
import {DISCOVERY_RECOMMENDATION_TYPES, DISCOVERY_USAGE_WINDOWS,
  validateProtectedActorScope, validateRecommendationEvidence,
  validateRecommendationPolicy, validateUsageEvent, validateUsagePolicy} from
  './DiscoveryEngagementContracts';
import {discoveryDocumentVisibility} from './DiscoveryDocument';

function requireMethod(authority, method, label) {
  if(typeof authority?.[method] !== 'function') throw new TypeError(
    `${label} requires ${method} authority.`
  );
}

function timestamp(value) { return Date.parse(value); }

export class InMemoryDiscoveryUsageStore {
  constructor() { this.events = new Map(); }

  append(event) {
    event = validateUsageEvent(event);
    if(this.events.has(event.eventId)) throw new TypeError(
      `Discovery usage event ${event.eventId} already exists.`
    );
    this.events.set(event.eventId, event); return event;
  }

  range({from=null, to=null}={}) {
    const lower = from === null ? -Infinity : Number(from);
    const upper = to === null ? Infinity : Number(to);
    if(Number.isNaN(lower) || Number.isNaN(upper) || lower > upper) {
      throw new TypeError('Discovery usage range is invalid.');
    }
    return [...this.events.values()].filter((event) => {
      const time = timestamp(event.time); return time >= lower && time <= upper;
    }).sort((left, right) => timestamp(left.time) - timestamp(right.time) ||
      left.eventId.localeCompare(right.eventId));
  }
}

function eventWeight(event, policy) {
  if(!policy.enabledTypes.includes(event.consumerType)) return 0;
  if(event.consumerType === 'query_execution' && !event.success) return 0.05;
  return policy.eventWeights[event.consumerType] ?? 0;
}

function surfaceCounts(events, resourceRef, security) {
  const counts = new Map();
  for(const event of events) if(event.canonicalResourceRefs.includes(resourceRef)) {
    for(const surfaceRef of event.accessSurfaceRefs) {
      if(security.admitUsageSurface(resourceRef, surfaceRef, event) !== true) continue;
      const prior = counts.get(surfaceRef) ?? {eventCount: 0, successCount: 0};
      prior.eventCount += 1; prior.successCount += event.success ? 1 : 0;
      counts.set(surfaceRef, prior);
    }
  }
  return [...counts].map(([surfaceRef, value]) => ({surfaceRef, ...value}))
    .sort((left, right) => left.surfaceRef.localeCompare(right.surfaceRef));
}

function distinctPrivacy(values, minimum) {
  const count = new Set(values.filter(Boolean)).size;
  return count >= minimum ? count : null;
}

export class DiscoveryUsageSignalService {
  constructor({store, actorAuthority, policy={}, now=() => Date.now()}={}) {
    requireMethod(store, 'append', 'Discovery usage store');
    requireMethod(store, 'range', 'Discovery usage store');
    requireMethod(actorAuthority, 'protect', 'Discovery usage actor privacy');
    this.store = store; this.actorAuthority = actorAuthority;
    this.policy = validateUsagePolicy(policy); this.now = now;
  }

  record(input) {
    plainObject(input, 'Discovery usage input');
    noRawSecrets(input, 'Discovery usage input');
    const actorScope = validateProtectedActorScope(
      this.actorAuthority.protect(input.actorScope));
    return this.store.append(validateUsageEvent({...input, actorScope}));
  }

  events(window='all') {
    if(!this.policy.windows.includes(window) && window !== 'all') throw new TypeError(
      `Discovery usage window ${window} is not enabled.`
    );
    const duration = DISCOVERY_USAGE_WINDOWS[window];
    if(duration === undefined) throw new TypeError('Discovery usage window is invalid.');
    return this.store.range({from: duration === null ? null : this.now() - duration,
      to: this.now()});
  }

  aggregate({window='30d', resourceRefs=null, security}={}) {
    requireMethod(security, 'admitUsage', 'Discovery usage security');
    requireMethod(security, 'admitUsageSurface', 'Discovery usage security');
    const events = this.events(window); const requested = resourceRefs === null ? null :
      new Set(resourceRefs.map((reference) => platformValue(reference,
        'Discovery usage resource reference', 4096)));
    const refs = new Set();
    for(const event of events) for(const ref of event.canonicalResourceRefs) {
      if(!requested || requested.has(ref)) refs.add(ref);
    }
    const results = [];
    for(const resourceRef of refs) {
      if(security.admitUsage(resourceRef) !== true) continue;
      const matching = events.filter((event) =>
        event.canonicalResourceRefs.includes(resourceRef) &&
        security.admitUsage(resourceRef, event) === true);
      const weightedEvents = matching.reduce((sum, event) =>
        sum + eventWeight(event, this.policy), 0);
      const actorKeys = matching.map((event) => event.actorScope.actorKey);
      const teamKeys = matching.flatMap((event) => event.actorScope.teamKeys);
      results.push({resourceRef, window, eventCount: matching.length,
        successCount: matching.filter((event) => event.success).length,
        weightedEvents,
        popularity: Math.min(1, Math.log1p(weightedEvents) /
          Math.log1p(this.policy.popularitySaturation)),
        distinctUsers: distinctPrivacy(actorKeys, this.policy.minDistinctUsers),
        distinctTeams: distinctPrivacy(teamKeys, this.policy.minDistinctUsers),
        actors: this.policy.showIndividualUsage ? [...new Set(actorKeys)].sort() : null,
        surfaceUsage: surfaceCounts(matching, resourceRef, security)});
    }
    return immutable(results.sort((left, right) =>
      right.weightedEvents - left.weightedEvents ||
      left.resourceRef.localeCompare(right.resourceRef)));
  }

  cooccurrence(sourceRef, {window='30d'}={}) {
    sourceRef = platformValue(sourceRef, 'Discovery co-occurrence source', 4096);
    const pairs = new Map();
    for(const event of this.events(window)) {
      if(!event.canonicalResourceRefs.includes(sourceRef)) continue;
      for(const targetRef of event.canonicalResourceRefs) if(targetRef !== sourceRef) {
        const item = pairs.get(targetRef) ?? {events: 0, actors: new Set(),
          lastObservedAt: null};
        item.events += 1; item.actors.add(event.actorScope.actorKey);
        if(!item.lastObservedAt || timestamp(event.time) >
            timestamp(item.lastObservedAt)) item.lastObservedAt = event.time;
        pairs.set(targetRef, item);
      }
    }
    return immutable([...pairs].map(([targetRef, item]) => ({targetRef,
      eventCount: item.events, distinctActors: item.actors.size,
      lastObservedAt: item.lastObservedAt})).sort((left, right) =>
      right.eventCount - left.eventCount || left.targetRef.localeCompare(
        right.targetRef)));
  }
}

export class InMemoryDiscoveryRecommendationIndex {
  constructor() { this.items = new Map(); this.revision = 0; }

  publish(inputs, {replace=false}={}) {
    if(!Array.isArray(inputs)) throw new TypeError(
      'Discovery recommendation publish input must be an array.'
    );
    const candidate = replace ? new Map() : new Map(this.items);
    for(const input of inputs) {
      const item = validateRecommendationEvidence(input);
      if(candidate.has(item.evidenceId) &&
          candidate.get(item.evidenceId).sourceRef !== item.sourceRef) {
        throw new TypeError('Recommendation evidence identity cannot be reassigned.');
      }
      candidate.set(item.evidenceId, item);
    }
    this.items = candidate; this.revision += 1;
    return immutable({revision: this.revision, count: this.items.size});
  }

  remove(evidenceId) {
    evidenceId = platformValue(evidenceId, 'Recommendation evidence ID', 512);
    if(!this.items.delete(evidenceId)) return false;
    this.revision += 1; return true;
  }

  forSource(sourceRef) {
    return [...this.items.values()].filter((item) => item.sourceRef === sourceRef)
      .sort((left, right) => right.score - left.score ||
        left.evidenceId.localeCompare(right.evidenceId));
  }
}

function recommendationSecurity(security) {
  for(const method of ['admitDocument', 'admitRecommendation', 'admitSignal']) {
    requireMethod(security, method, 'Discovery recommendation security');
  }
}

export class DiscoveryRecommendationService {
  constructor({index, usage, documentAuthority, policy={}, now=() => Date.now()}={}) {
    requireMethod(index, 'forSource', 'Discovery recommendation index');
    requireMethod(usage, 'cooccurrence', 'Discovery recommendation usage');
    requireMethod(documentAuthority, 'resolve', 'Discovery recommendation document');
    this.index = index; this.usage = usage; this.documentAuthority = documentAuthority;
    this.policy = validateRecommendationPolicy(policy); this.now = now;
  }

  _visible(reference, security) {
    const document = this.documentAuthority.resolve(reference);
    return document && discoveryDocumentVisibility(document).recommendations &&
      security.admitDocument(document) === true ? document : null;
  }

  recommendations(sourceRef, {security, window='30d'}={}) {
    recommendationSecurity(security);
    sourceRef = platformValue(sourceRef, 'Discovery recommendation source', 4096);
    if(!this._visible(sourceRef, security)) return immutable([]);
    const oldest = this.now() - this.policy.staleDays * 24 * 60 * 60 * 1000;
    const candidates = this.index.forSource(sourceRef).filter((item) =>
      this.policy.enabledTypes.includes(item.type) &&
      timestamp(item.observedAt) >= oldest &&
      (!item.expiresAt || timestamp(item.expiresAt) >= this.now()));
    if(this.policy.enabledTypes.includes('frequently_used_together')) {
      for(const item of this.usage.cooccurrence(sourceRef, {window})) {
        if(item.distinctActors < this.policy.minDistinctUsers) continue;
        candidates.push(validateRecommendationEvidence({schemaVersion: 1,
          evidenceId: `cooccurrence:${sourceRef}:${item.targetRef}:${window}`,
          type: 'frequently_used_together', sourceRef,
          targetRef: item.targetRef,
          score: Math.min(1, Math.log1p(item.eventCount) / Math.log1p(1000)),
          reason: `Used together in ${item.eventCount} canonical usage events (${window}).`,
          evidenceRefs: [], evidenceKind: 'canonical_cooccurrence',
          explicitEvidence: true, observedAt: item.lastObservedAt,
          expiresAt: null, distinctActors: item.distinctActors}));
      }
    }
    const admitted = [];
    for(const item of candidates) {
      if(item.type === 'used_by_team' &&
          (item.distinctActors ?? 0) < this.policy.minDistinctUsers) continue;
      const target = this._visible(item.targetRef, security);
      if(!target || security.admitRecommendation(item, target) !== true ||
          security.admitSignal(item.type, item) !== true) continue;
      admitted.push({...item, targetDocument: target});
    }
    const best = new Map();
    for(const item of admitted) {
      const key = `${item.type}\u0000${item.targetRef}`;
      const current = best.get(key);
      if(!current || item.score > current.score || (item.score === current.score &&
          item.evidenceId.localeCompare(current.evidenceId) < 0)) best.set(key, item);
    }
    const grouped = new Map(DISCOVERY_RECOMMENDATION_TYPES.map((type) => [type, []]));
    for(const item of best.values()) grouped.get(item.type).push(item);
    const result = [];
    for(const type of DISCOVERY_RECOMMENDATION_TYPES) {
      if(!this.policy.enabledTypes.includes(type)) continue;
      const items = grouped.get(type).sort((left, right) =>
        right.score - left.score || left.targetRef.localeCompare(right.targetRef))
        .slice(0, this.policy.maximumPerType);
      if(items.length) result.push({type, items});
    }
    return immutable(result);
  }
}
