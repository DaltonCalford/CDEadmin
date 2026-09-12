/////////////////////////////////////////////////////////////
// Discovery canonicalization and atomic revision service.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';
import {normalizeDiscoveryIndexBackend} from './DiscoveryIndexBackend';

export const DISCOVERY_INDEX_SERVICE_ID =
  'cdeadmin.discovery_intelligence.index';
export const DISCOVERY_DELETION_SAFETY_THRESHOLD = 0.20;
const PUBLISH_FIELDS = Object.freeze(['revision', 'items', 'embeddings',
  'priorDocumentCount', 'mandatorySource', 'deletionApproval']);

export class DiscoveryIndexService {
  constructor({backend, canonicalizer, deletionSafetyThreshold=
  DISCOVERY_DELETION_SAFETY_THRESHOLD}={}) {
    try { backend = normalizeDiscoveryIndexBackend(backend); }
    catch { throw new TypeError(
      'DiscoveryIndexService requires a complete index backend.'); }
    if(!canonicalizer || typeof canonicalizer.batch !== 'function') throw new TypeError(
      'DiscoveryIndexService requires canonicalization authority.'
    );
    if(!Number.isFinite(deletionSafetyThreshold) || deletionSafetyThreshold < 0 ||
        deletionSafetyThreshold > 1) throw new TypeError(
      'Discovery deletion safety threshold must be between zero and one.'
    );
    this.backend = backend; this.canonicalizer = canonicalizer;
    this.deletionSafetyThreshold = deletionSafetyThreshold;
    this.state = 'empty'; this.lastFailure = null; this.pending = null;
  }

  async publish(input, context={}) {
    plainObject(input, 'Discovery publish request'); noRawSecrets(input,
      'Discovery publish request');
    const unknown = Object.keys(input).filter((field) =>
      !PUBLISH_FIELDS.includes(field));
    if(unknown.length) throw new TypeError(
      `Discovery publish request contains unsupported field ${unknown[0]}.`
    );
    if(input.mandatorySource !== undefined && typeof input.mandatorySource !== 'boolean') {
      throw new TypeError('Discovery mandatory-source selection must be boolean.');
    }
    if(input.deletionApproval !== undefined && typeof input.deletionApproval !== 'boolean') {
      throw new TypeError('Discovery deletion approval must be boolean.');
    }
    if(input.embeddings !== undefined) plainObject(input.embeddings,
      'Discovery revision embeddings');
    const revision = platformValue(input.revision, 'Discovery index revision');
    const prior = await this.backend.health();
    this.state = 'building'; this.lastFailure = null;
    try {
      const documents = this.canonicalizer.batch(input.items, context).map((document) =>
        document.indexRevision === revision ? document : (() => { throw new TypeError(
          'Canonicalized document does not bind the candidate index revision.'); })());
      const canonical = new Set(documents.map((document) => document.canonicalRef));
      if(canonical.size !== documents.length) throw new TypeError(
        'Discovery candidate canonical identities must be unique.'
      );
      const priorCount = input.priorDocumentCount ?? prior.documentCount ?? 0;
      if(!Number.isInteger(priorCount) || priorCount < 0) throw new TypeError(
        'Discovery prior document count must be a non-negative integer.'
      );
      const deletionCount = Math.max(0, priorCount - documents.length);
      const deletionRatio = priorCount ? deletionCount / priorCount : 0;
      if(input.mandatorySource === true && deletionRatio > this.deletionSafetyThreshold &&
          input.deletionApproval !== true) {
        this.state = 'awaiting_deletion_approval';
        this.pending = immutable({revision, documentCount: documents.length,
          priorDocumentCount: priorCount, deletionCount, deletionRatio});
        return immutable({published: false, approvalRequired: true, ...this.pending,
          activeRevision: prior.activeRevision});
      }
      const published = await this.backend.bulkRevision({revision, documents,
        embeddings: input.embeddings ?? {}});
      this.state = 'healthy'; this.pending = null;
      return immutable({published: true, approvalRequired: false, ...published,
        previousRevision: prior.activeRevision});
    } catch(error) {
      this.state = prior.ready ? 'degraded' : 'failed';
      this.lastFailure = immutable({message: error.message, at: new Date().toISOString(),
        candidateRevision: revision, retainedRevision: prior.activeRevision});
      throw error;
    }
  }

  async health() {
    const backend = await this.backend.health();
    return immutable({...backend, serviceState: this.state,
      lastFailure: this.lastFailure, pending: this.pending});
  }

  lexicalSearch(input) { return this.backend.lexicalSearch(input); }
  facetSearch(input) { return this.backend.facetSearch(input); }
  semanticSearch(input) { return this.backend.semanticSearch(input); }
  getDocument(canonicalRef, input) { return this.backend.getDocument(
    canonicalRef, input); }
}
