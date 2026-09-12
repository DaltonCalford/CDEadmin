/////////////////////////////////////////////////////////////
// Revisioned graph-neighborhood candidate source for Discovery.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';
import {PlatformRegistryError} from '../../platform/PlatformRegistry';

const EDGE_FIELDS = Object.freeze(['edgeId', 'fromRef', 'toRef', 'type',
  'weight', 'directed', 'evidence', 'revision']);

function boundedLimit(value) {
  value = value ?? 200;
  if(!Number.isInteger(value) || value < 1 || value > 200) throw new TypeError(
    'Discovery graph candidate limit must be an integer from 1 to 200.'
  );
  return value;
}

export function validateDiscoveryGraphEdge(input, revision=input?.revision) {
  plainObject(input, 'Discovery graph edge'); noRawSecrets(input,
    'Discovery graph edge');
  const unknown = Object.keys(input).find((field) => !EDGE_FIELDS.includes(field));
  if(unknown) throw new TypeError(
    `Discovery graph edge contains unsupported field ${unknown}.`
  );
  const missing = EDGE_FIELDS.find((field) => !Object.hasOwn(input, field));
  if(missing) throw new TypeError(`Discovery graph edge requires ${missing}.`);
  if(typeof input.directed !== 'boolean') throw new TypeError(
    'Discovery graph edge directed must be boolean.'
  );
  if(!Number.isFinite(input.weight) || input.weight < 0 || input.weight > 1) {
    throw new TypeError('Discovery graph edge weight must be between 0 and 1.');
  }
  plainObject(input.evidence, 'Discovery graph edge evidence');
  noRawSecrets(input.evidence, 'Discovery graph edge evidence');
  const value = immutable({edgeId: platformValue(input.edgeId,
    'Discovery graph edge ID'), fromRef: platformValue(input.fromRef,
    'Discovery graph source reference', 4096), toRef: platformValue(input.toRef,
    'Discovery graph target reference', 4096), type: platformValue(input.type,
    'Discovery graph edge type'), weight: input.weight, directed: input.directed,
  evidence: {...input.evidence}, revision: platformValue(input.revision,
    'Discovery graph revision')});
  if(revision !== undefined && value.revision !== revision) throw new TypeError(
    'Discovery graph edge does not bind the candidate revision.'
  );
  return value;
}

export class InMemoryDiscoveryGraphIndex {
  constructor({now=() => new Date().toISOString()}={}) {
    this.now = now; this.revisions = new Map(); this.activeRevision = null;
  }

  publish({revision, edges}) {
    revision = platformValue(revision, 'Discovery graph revision');
    if(this.revisions.has(revision)) throw new PlatformRegistryError(
      'duplicate', `Discovery graph revision already exists: ${revision}`, revision
    );
    if(!Array.isArray(edges)) throw new TypeError(
      'Discovery graph revision edges must be an array.'
    );
    const byId = new Map();
    for(const edge of edges) {
      const value = validateDiscoveryGraphEdge(edge, revision);
      if(byId.has(value.edgeId)) throw new TypeError(
        `Discovery graph revision duplicates edge ID ${value.edgeId}.`
      );
      byId.set(value.edgeId, value);
    }
    const record = {revision, edges: byId, publishedAt: this.now()};
    this.revisions.set(revision, record); this.activeRevision = revision;
    return immutable({revision, edgeCount: byId.size,
      publishedAt: record.publishedAt});
  }

  neighbors({canonicalRefs, revision=this.activeRevision, admitEdge, limit}={}) {
    if(!Array.isArray(canonicalRefs) || !canonicalRefs.length) throw new TypeError(
      'Discovery graph search requires canonical seed references.'
    );
    if(typeof admitEdge !== 'function') throw new TypeError(
      'Discovery graph search requires explicit edge admission.'
    );
    const seeds = new Set(canonicalRefs.map((item) => platformValue(item,
      'Discovery graph seed reference', 4096)));
    const record = this._revision(revision); const candidates = new Map();
    for(const edge of record.edges.values()) {
      let canonicalRef = null;
      if(seeds.has(edge.fromRef)) canonicalRef = edge.toRef;
      else if(!edge.directed && seeds.has(edge.toRef)) canonicalRef = edge.fromRef;
      if(!canonicalRef || seeds.has(canonicalRef) || admitEdge(edge) !== true) continue;
      const previous = candidates.get(canonicalRef);
      const item = {canonicalRef, score: edge.weight,
        evidence: [{edgeId: edge.edgeId, type: edge.type,
          direction: seeds.has(edge.fromRef) ? 'outbound' : 'inbound'}],
        edges: [edge]};
      if(!previous || previous.score < item.score) candidates.set(canonicalRef, item);
      else if(previous.score === item.score) {
        previous.evidence.push(...item.evidence); previous.edges.push(edge);
      }
    }
    return immutable([...candidates.values()].sort((left, right) =>
      right.score - left.score || left.canonicalRef.localeCompare(right.canonicalRef))
      .slice(0, boundedLimit(limit)));
  }

  health() {
    const record = this.activeRevision ? this.revisions.get(this.activeRevision) : null;
    return immutable({state: record ? 'healthy' : 'empty', ready: Boolean(record),
      activeRevision: this.activeRevision, edgeCount: record?.edges.size ?? 0,
      retainedRevisions: this.revisions.size, checkedAt: this.now()});
  }

  _revision(revision) {
    const record = this.revisions.get(String(revision));
    if(!record) throw new PlatformRegistryError('not_found',
      `Unknown Discovery graph revision: ${revision}`, revision);
    return record;
  }
}
