/////////////////////////////////////////////////////////////
// Versioned pluggable Discovery index backend contract.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';
import {PlatformRegistryError, stablePlatformId} from '../../platform/PlatformRegistry';
import {validateDiscoveryDocument} from './DiscoveryDocument';

export const DISCOVERY_BACKEND_METHODS = Object.freeze(['upsertDocument',
  'removeDocument', 'lexicalSearch', 'facetSearch', 'semanticSearch',
  'getDocument', 'bulkRevision', 'health']);
export const DISCOVERY_BACKEND_CONTRACT_METHODS = Object.freeze({
  upsertDocument: 'upsert_document', removeDocument: 'remove_document',
  lexicalSearch: 'lexical_search', facetSearch: 'facet_search',
  semanticSearch: 'semantic_search', getDocument: 'get_document',
  bulkRevision: 'bulk_revision', health: 'health'});

export function normalizeDiscoveryIndexBackend(backend) {
  const normalized = {};
  for(const method of DISCOVERY_BACKEND_METHODS) {
    const contractMethod = DISCOVERY_BACKEND_CONTRACT_METHODS[method];
    const implementation = backend?.[method] ?? backend?.[contractMethod];
    if(typeof implementation !== 'function') throw new TypeError(
      `Discovery backend is missing ${method} (${contractMethod}).`
    );
    normalized[method] = implementation.bind(backend);
    normalized[contractMethod] = normalized[method];
  }
  return Object.freeze(normalized);
}

function requireAdmit(input) {
  if(typeof input?.admit !== 'function') throw new TypeError(
    'Discovery retrieval requires an explicit security admission function.'
  );
  return input.admit;
}

function boundedLimit(value, maximum=500) {
  value = value ?? 50;
  if(!Number.isInteger(value) || value < 1 || value > maximum) throw new TypeError(
    `Discovery result limit must be an integer from 1 to ${maximum}.`
  );
  return value;
}

function tokens(value) {
  return String(value ?? '').normalize('NFKC').toLocaleLowerCase().match(
    /[\p{L}\p{N}_:.@/-]+/gu) ?? [];
}

function cosine(left, right) {
  if(!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length ||
      !left.length) throw new TypeError('Discovery embedding vectors are incompatible.');
  let dot = 0; let leftNorm = 0; let rightNorm = 0;
  for(let index = 0; index < left.length; index++) {
    if(!Number.isFinite(left[index]) || !Number.isFinite(right[index])) throw new TypeError(
      'Discovery embedding vectors must contain finite numbers.'
    );
    dot += left[index] * right[index]; leftNorm += left[index] ** 2;
    rightNorm += right[index] ** 2;
  }
  const similarity = leftNorm && rightNorm ? dot / Math.sqrt(
    leftNorm * rightNorm) : 0;
  return Math.max(0, Math.min(1, (similarity + 1) / 2));
}

function embedding(value, label='Discovery embedding vector') {
  if(!Array.isArray(value) || !value.length || value.some((item) =>
    !Number.isFinite(item)) || value.every((item) => item === 0)) throw new TypeError(
    `${label} must contain finite numbers.`
  );
  return Object.freeze([...value]);
}

function revisionRecord(revision, documents, embeddings=new Map()) {
  return {revision, documents, embeddings, publishedAt: new Date().toISOString()};
}

export class DiscoveryIndexBackendRegistry {
  constructor() { this.backends = new Map(); }

  register(input) {
    const id = stablePlatformId(input?.id, 'Discovery backend ID');
    if(this.backends.has(id)) throw new PlatformRegistryError(
      'duplicate', `Discovery backend already registered: ${id}`, id
    );
    if(typeof input.factory !== 'function') throw new TypeError(
      'Discovery backend factory is required.'
    );
    const value = immutable({id, version: platformValue(input.version,
      'Discovery backend version'), factory: input.factory});
    this.backends.set(id, value); return () => this.backends.delete(id);
  }

  create(id, options={}) {
    const definition = this.backends.get(stablePlatformId(id, 'Discovery backend ID'));
    if(!definition) throw new PlatformRegistryError(
      'not_found', `Unknown Discovery backend: ${id}`, id
    );
    try { return normalizeDiscoveryIndexBackend(definition.factory(options)); }
    catch(error) { throw new TypeError(`Discovery backend ${id}: ${error.message}`); }
  }

  list() { return [...this.backends.values()].sort((left, right) =>
    left.id.localeCompare(right.id)); }
}

export class InMemoryDiscoveryIndexBackend {
  constructor({now=() => new Date().toISOString()}={}) {
    this.now = now; this.revisions = new Map(); this.activeRevision = null;
  }

  async bulkRevision(input) {
    plainObject(input, 'Discovery bulk revision'); noRawSecrets(input,
      'Discovery bulk revision');
    const revision = platformValue(input.revision, 'Discovery index revision');
    if(this.revisions.has(revision)) throw new PlatformRegistryError(
      'duplicate', `Discovery revision already exists: ${revision}`, revision
    );
    if(!Array.isArray(input.documents)) throw new TypeError(
      'Discovery revision documents must be an array.'
    );
    const documents = new Map();
    const documentIds = new Set();
    for(const value of input.documents) {
      const document = validateDiscoveryDocument(value);
      if(document.indexRevision !== revision) throw new TypeError(
        'Discovery document index revision does not match candidate revision.'
      );
      if(documents.has(document.canonicalRef)) throw new TypeError(
        `Discovery revision duplicates canonical identity ${document.canonicalRef}.`
      );
      if(documentIds.has(document.documentId)) throw new TypeError(
        `Discovery revision duplicates document ID ${document.documentId}.`
      );
      documents.set(document.canonicalRef, document);
      documentIds.add(document.documentId);
    }
    const embeddings = new Map();
    for(const [canonicalRef, vector] of Object.entries(input.embeddings ?? {})) {
      if(!documents.has(canonicalRef)) throw new TypeError(
        `Discovery embedding references unknown document ${canonicalRef}.`
      );
      embeddings.set(canonicalRef, embedding(vector,
        `Discovery embedding for ${canonicalRef}`));
    }
    const record = revisionRecord(revision, documents, embeddings);
    record.publishedAt = this.now();
    this.revisions.set(revision, record); this.activeRevision = revision;
    return immutable({revision, documentCount: documents.size,
      embeddingCount: embeddings.size, publishedAt: record.publishedAt});
  }

  async upsertDocument(document, {revision, embedding: nextEmbedding}={}) {
    revision = platformValue(revision, 'Discovery replacement revision');
    document = validateDiscoveryDocument(document);
    if(document.indexRevision !== revision) throw new TypeError(
      'Discovery document index revision does not match replacement revision.'
    );
    const active = this._revision(); const candidate = [...active.documents.values()]
      .filter((item) => item.canonicalRef !== document.canonicalRef)
      .map((item) => ({...item, indexRevision: revision}));
    const embeddings = Object.fromEntries([...active.embeddings].filter(
      ([canonicalRef]) => canonicalRef !== document.canonicalRef));
    if(nextEmbedding !== undefined) embeddings[document.canonicalRef] = embedding(
      nextEmbedding, `Discovery embedding for ${document.canonicalRef}`);
    return this.bulkRevision({revision: platformValue(revision,
      'Discovery replacement revision'), documents: [...candidate, document],
    embeddings});
  }

  async removeDocument(canonicalRef, {revision}={}) {
    const active = this._revision(); canonicalRef = platformValue(canonicalRef,
      'Discovery canonical reference');
    if(!active.documents.has(canonicalRef)) throw new PlatformRegistryError(
      'not_found', `Unknown Discovery document: ${canonicalRef}`, canonicalRef
    );
    return this.bulkRevision({revision: platformValue(revision,
      'Discovery replacement revision'), documents: [...active.documents.values()]
      .filter((item) => item.canonicalRef !== canonicalRef).map((item) => ({...item,
        indexRevision: revision})), embeddings: Object.fromEntries(
      [...active.embeddings].filter(([reference]) => reference !== canonicalRef))});
  }

  async lexicalSearch(input) {
    const admit = requireAdmit(input); const record = this._revision(input.revision);
    const aliasAdmit = typeof input.aliasAdmit === 'function' ? input.aliasAdmit :
      () => false;
    const queryTokens = [...new Set(tokens(platformValue(input.text,
      'Discovery lexical query', 8192)))];
    const limit = boundedLimit(input.limit);
    return immutable([...record.documents.values()].filter((document) =>
      admit(document) === true).map((document) => {
      const haystack = tokens([document.name, ...document.qualifiedNames,
        ...document.authorizedAliases.filter((item) => aliasAdmit(item,
          document) === true).map((item) => item.visibleQualifiedName),
        document.searchText].join(' '));
      const matched = queryTokens.filter((token) => haystack.includes(token));
      return {document, score: queryTokens.length ? matched.length / queryTokens.length : 0,
        matchedTokens: matched};
    }).filter((item) => item.score > 0).sort((left, right) =>
      right.score - left.score || left.document.canonicalRef.localeCompare(
        right.document.canonicalRef)).slice(0, limit));
  }

  async facetSearch(input) {
    const admit = requireAdmit(input); const record = this._revision(input.revision);
    if(!Array.isArray(input.fields) || input.fields.length > 50) throw new TypeError(
      'Discovery facet fields must be a bounded array.'
    );
    const fields = input.fields.map((field) => platformValue(field,
      'Discovery facet field', 512));
    if(new Set(fields).size !== fields.length) throw new TypeError(
      'Discovery facet fields contain duplicates.'
    );
    const limit = boundedLimit(input.limit ?? 50, 50);
    const result = {};
    for(const field of fields) {
      const counts = new Map();
      for(const document of record.documents.values()) if(admit(document) === true) {
        const value = document.facetValues[field];
        for(const item of Array.isArray(value) ? value : value == null ? [] : [value]) {
          const key = String(item); counts.set(key, (counts.get(key) ?? 0) + 1);
        }
      }
      result[field] = [...counts.entries()].sort((left, right) =>
        right[1] - left[1] || left[0].localeCompare(right[0])).map(([value, count]) =>
        ({value, count})).slice(0, limit);
    }
    return immutable(result);
  }

  async semanticSearch(input) {
    const admit = requireAdmit(input); const record = this._revision(input.revision);
    const queryVector = embedding(input.vector);
    const limit = boundedLimit(input.limit);
    return immutable([...record.embeddings.entries()].map(([canonicalRef, vector]) => ({
      document: record.documents.get(canonicalRef), score: cosine(queryVector, vector)}))
      .filter((item) => admit(item.document) === true).sort((left, right) =>
        right.score - left.score || left.document.canonicalRef.localeCompare(
          right.document.canonicalRef)).slice(0, limit));
  }

  async getDocument(canonicalRef, input={}) {
    const admit = requireAdmit(input); const value = this._revision(input.revision)
      .documents.get(platformValue(canonicalRef, 'Discovery canonical reference', 4096));
    if(!value || admit(value) !== true) throw new PlatformRegistryError(
      'not_found', 'Discovery document was not found.', canonicalRef
    );
    return value;
  }

  async health() {
    const record = this.activeRevision ? this.revisions.get(this.activeRevision) : null;
    return immutable({state: record ? 'healthy' : 'empty', ready: Boolean(record),
      activeRevision: this.activeRevision, documentCount: record?.documents.size ?? 0,
      retainedRevisions: this.revisions.size, checkedAt: this.now()});
  }

  upsert_document(...args) { return this.upsertDocument(...args); }
  remove_document(...args) { return this.removeDocument(...args); }
  lexical_search(...args) { return this.lexicalSearch(...args); }
  facet_search(...args) { return this.facetSearch(...args); }
  semantic_search(...args) { return this.semanticSearch(...args); }
  get_document(...args) { return this.getDocument(...args); }
  bulk_revision(...args) { return this.bulkRevision(...args); }

  _revision(revision=this.activeRevision) {
    const record = this.revisions.get(String(revision));
    if(!record) throw new PlatformRegistryError(
      'not_found', `Unknown Discovery index revision: ${revision}`, revision
    );
    return record;
  }
}
