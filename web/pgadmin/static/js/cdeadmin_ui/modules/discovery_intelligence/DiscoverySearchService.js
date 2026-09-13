/////////////////////////////////////////////////////////////
// Security-first Discovery query, candidate and result authority.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';
import {normalizeDiscoveryIndexBackend} from './DiscoveryIndexBackend';
import {discoveryDocumentVisibility, validateDiscoveryDocument} from
  './DiscoveryDocument';
import {validateDiscoveryGraphEdge} from './DiscoveryGraphIndex';
import {BALANCED_DISCOVERY_RANKING_PROFILE,
  validateDiscoveryRankingProfile} from './DiscoveryRanking';

export const DISCOVERY_SEARCH_MODES = Object.freeze([
  'QUICK', 'KEYWORD', 'ADVANCED_FACETED', 'SEMANTIC', 'BUSINESS_TERM',
  'FIELD', 'METRIC', 'SIMILAR_TO', 'GRAPH_RELATED', 'NATURAL_LANGUAGE',
]);
export const DISCOVERY_ACCESS_DIMENSIONS = Object.freeze([
  'DISCOVER_IDENTITY', 'DISCOVER_METADATA', 'VIEW_SCHEMA',
  'VIEW_PROFILE_STATS', 'VIEW_SAMPLE', 'QUERY', 'EXPORT', 'REQUEST_ACCESS',
  'ADMINISTER',
]);
export const DISCOVERY_DEFAULT_FACETS = Object.freeze([
  'entityClass', 'provider', 'domain', 'certification', 'quality', 'freshness',
  'access', 'owner', 'environment', 'nativeKind', 'updated', 'accessSurface',
]);

const QUERY_FIELDS = Object.freeze(['text', 'mode', 'entityClasses', 'providers',
  'domains', 'owners', 'certification', 'qualityState', 'freshness',
  'accessState', 'classification', 'tags', 'nativeKinds', 'environments',
  'updatedAfter', 'updatedBefore', 'minimumUsage', 'sort', 'rankingProfile',
  'pageSize', 'cursor', 'relatedTo']);
const ARRAY_QUERY_FIELDS = Object.freeze(['entityClasses', 'providers', 'domains',
  'owners', 'certification', 'qualityState', 'freshness', 'accessState',
  'classification', 'tags', 'nativeKinds', 'environments', 'relatedTo']);
const SORTS = Object.freeze(['relevance', 'name', 'freshness', 'quality', 'usage',
  'updated']);
const EXPERT_FIELDS = Object.freeze(['provider', 'domain', 'owner', 'type',
  'certified', 'quality', 'freshness', 'access', 'tag']);
const FILTER_SIGNAL = Object.freeze({entityClasses: 'entityClass',
  providers: 'provider', domains: 'domain', owners: 'owner',
  certification: 'certification', qualityState: 'quality', freshness: 'freshness',
  accessState: 'access', classification: 'classification', tags: 'tag',
  nativeKinds: 'nativeKind', environments: 'environment'});

export class DiscoveryQuerySyntaxError extends SyntaxError {
  constructor(message, position, code='invalid_syntax') {
    super(`${message} at position ${position}.`); this.name = 'DiscoveryQuerySyntaxError';
    this.code = code; this.position = position;
  }
}

function exactObjectFields(value, fields, label) {
  plainObject(value, label);
  const unknown = Object.keys(value).find((field) => !fields.includes(field));
  if(unknown) throw new TypeError(`${label} contains unsupported field ${unknown}.`);
}

function strings(value, label, maximum=4096) {
  if(!Array.isArray(value)) throw new TypeError(`${label} must be an array.`);
  const result = value.map((item) => platformValue(item, `${label} value`, maximum));
  if(new Set(result).size !== result.length) throw new TypeError(
    `${label} contains duplicate values.`
  );
  return result;
}

function optionalDate(value, label) {
  if(value === null || value === undefined) return null;
  value = platformValue(value, label, 64);
  if(Number.isNaN(Date.parse(value))) throw new TypeError(`${label} is invalid.`);
  return value;
}

export function validateDiscoverySearchQuery(input) {
  exactObjectFields(input, QUERY_FIELDS, 'Discovery search query');
  noRawSecrets(input, 'Discovery search query');
  const mode = input.mode ?? 'QUICK';
  if(!DISCOVERY_SEARCH_MODES.includes(mode)) throw new TypeError(
    'Discovery search mode is invalid.'
  );
  const text = String(input.text ?? '').trim();
  if(text.length > 1000 || (!text && mode !== 'GRAPH_RELATED' &&
      mode !== 'SIMILAR_TO' && mode !== 'ADVANCED_FACETED')) throw new TypeError(
    'Discovery search text must contain 1 to 1000 characters.'
  );
  const result = {text, mode};
  for(const field of ARRAY_QUERY_FIELDS) result[field] = strings(
    input[field] ?? [], `Discovery search ${field}`);
  if((mode === 'GRAPH_RELATED' || mode === 'SIMILAR_TO') &&
      !result.relatedTo.length) throw new TypeError(
    `${mode} search requires a related resource reference.`
  );
  result.updatedAfter = optionalDate(input.updatedAfter,
    'Discovery search updated-after');
  result.updatedBefore = optionalDate(input.updatedBefore,
    'Discovery search updated-before');
  if(result.updatedAfter && result.updatedBefore &&
      Date.parse(result.updatedAfter) > Date.parse(result.updatedBefore)) {
    throw new TypeError('Discovery search date range is reversed.');
  }
  result.minimumUsage = input.minimumUsage ?? null;
  if(result.minimumUsage !== null && (!Number.isFinite(result.minimumUsage) ||
      result.minimumUsage < 0 || result.minimumUsage > 1)) throw new TypeError(
    'Discovery search minimum usage must be between 0 and 1.'
  );
  result.sort = input.sort ?? 'relevance';
  if(!SORTS.includes(result.sort)) throw new TypeError(
    'Discovery search sort is invalid.'
  );
  result.rankingProfile = input.rankingProfile ?? null;
  if(result.rankingProfile !== null) result.rankingProfile = platformValue(
    result.rankingProfile, 'Discovery search ranking profile');
  result.pageSize = input.pageSize ?? 50;
  if(!Number.isInteger(result.pageSize) || result.pageSize < 1 ||
      result.pageSize > 200) throw new TypeError(
    'Discovery page size must be an integer from 1 to 200.'
  );
  result.cursor = input.cursor === null || input.cursor === undefined ? null :
    platformValue(input.cursor, 'Discovery search cursor', 4096);
  return immutable(result);
}

function readQuoted(text, start) {
  let result = '';
  for(let index = start + 1; index < text.length; index++) {
    if(text[index] === '\\') {
      if(index + 1 >= text.length) throw new DiscoveryQuerySyntaxError(
        'Incomplete escape', index);
      result += text[index + 1]; index++; continue;
    }
    if(text[index] === '"') return {value: result, end: index + 1};
    result += text[index];
  }
  throw new DiscoveryQuerySyntaxError('Unclosed quoted phrase', start);
}

function readGroup(text, start) {
  let quote = false;
  for(let index = start + 1; index < text.length; index++) {
    if(text[index] === '"' && text[index - 1] !== '\\') quote = !quote;
    if(text[index] === ')' && !quote) return {value: text.slice(start + 1, index),
      end: index + 1};
  }
  throw new DiscoveryQuerySyntaxError('Unclosed filter group', start);
}

function groupValues(value, position) {
  const values = value.split(/\s+OR\s+/i).map((item) => item.trim()).map((item) => {
    if(item.startsWith('"')) {
      const parsed = readQuoted(item, 0);
      if(parsed.end !== item.length) throw new DiscoveryQuerySyntaxError(
        'Unexpected text after quoted filter value', position + parsed.end);
      return parsed.value;
    }
    return item;
  });
  if(values.some((item) => !item)) throw new DiscoveryQuerySyntaxError(
    'Filter group contains an empty value', position);
  return values;
}

export function parseDiscoverySearchText(text) {
  text = String(text ?? ''); const terms = []; const phrases = []; const filters = [];
  let index = 0;
  while(index < text.length) {
    while(/\s/u.test(text[index] ?? '')) index++;
    if(index >= text.length) break;
    const position = index;
    if(text[index] === '"') {
      const parsed = readQuoted(text, index);
      if(!parsed.value.trim()) throw new DiscoveryQuerySyntaxError(
        'Quoted phrase cannot be empty', position);
      if(parsed.end < text.length && !/\s/u.test(text[parsed.end])) {
        throw new DiscoveryQuerySyntaxError('Unexpected text after quoted phrase',
          parsed.end);
      }
      phrases.push(parsed.value); index = parsed.end; continue;
    }
    let exclude = false;
    if(text[index] === '-') { exclude = true; index++; }
    const start = index;
    while(index < text.length && !/[\s:]/u.test(text[index])) index++;
    const head = text.slice(start, index);
    if(text[index] !== ':') {
      if(exclude) throw new DiscoveryQuerySyntaxError(
        'Exclusion requires a field', position);
      if(/^OR$/i.test(head)) throw new DiscoveryQuerySyntaxError(
        'OR is valid only inside a field group', position);
      if(!head) throw new DiscoveryQuerySyntaxError('Unexpected token', position);
      terms.push(head); continue;
    }
    if(!EXPERT_FIELDS.includes(head)) throw new DiscoveryQuerySyntaxError(
      `Unknown search field ${head || '(empty)'}`, start, 'unknown_field');
    index++;
    if(index >= text.length || /\s/u.test(text[index])) throw new DiscoveryQuerySyntaxError(
      `Search field ${head} requires a value`, index, 'missing_value');
    let values;
    if(text[index] === '"') {
      const parsed = readQuoted(text, index); values = [parsed.value]; index = parsed.end;
    } else if(text[index] === '(') {
      const parsed = readGroup(text, index); values = groupValues(parsed.value, index);
      index = parsed.end;
    } else {
      const valueStart = index;
      while(index < text.length && !/\s/u.test(text[index])) index++;
      values = [text.slice(valueStart, index)];
    }
    if(index < text.length && !/\s/u.test(text[index])) {
      throw new DiscoveryQuerySyntaxError('Unexpected text after filter value', index);
    }
    if(values.some((value) => !value.trim())) throw new DiscoveryQuerySyntaxError(
      `Search field ${head} requires a value`, position, 'missing_value');
    filters.push({field: head, values, exclude, position});
  }
  return immutable({terms, phrases, filters,
    lexicalText: [...terms, ...phrases].join(' ')});
}

function requireSecurity(security) {
  const methods = ['admitDocument', 'admitAlias', 'admitSignal', 'admitFacet',
    'admitGraphEdge', 'exposeCanonicalRef'];
  for(const method of methods) if(typeof security?.[method] !== 'function') {
    throw new TypeError(`Discovery search security is missing ${method}.`);
  }
  return security;
}

function normalized(value) {
  return String(value ?? '').normalize('NFKC').toLocaleLowerCase();
}

function exact(value) { return String(value ?? '').trim().normalize('NFC'); }

function tokens(value) {
  return normalized(value).match(/[\p{L}\p{N}_:.@/-]+/gu) ?? [];
}

function values(value) {
  return Array.isArray(value) ? value : value === null || value === undefined ? [] :
    [value];
}

function visibleText(document, parsed, security) {
  const aliases = document.authorizedAliases.filter((item) =>
    security.admitAlias(item, document) === true);
  const metadata = security.admitDocument(document, 'DISCOVER_METADATA') === true;
  const business = metadata && security.admitSignal(document, 'business') === true;
  const fields = {name: [document.name], qualifiedName: document.qualifiedNames,
    alias: aliases.map((item) => item.visibleQualifiedName),
    description: metadata && document.description ? [document.description] : [],
    businessTerm: business ? [...document.businessTerms, ...document.synonyms] : [],
    tag: metadata && security.admitSignal(document, 'tag') === true ? document.tags : [],
    owner: metadata && security.admitSignal(document, 'owner') === true ?
      document.ownerRefs : [],
    domain: metadata && security.admitSignal(document, 'domain') === true ?
      document.domainRefs : [],
    searchText: metadata ? [document.searchText] : []};
  const all = Object.values(fields).flat(); const queryTokens = [
    ...parsed.terms.flatMap(tokens), ...parsed.phrases.flatMap(tokens)];
  const haystack = tokens(all.join(' '));
  const matchedTokens = [...new Set(queryTokens.filter((token) =>
    haystack.includes(token)))];
  const phraseMatches = parsed.phrases.filter((phrase) => normalized(all.join(' '))
    .includes(normalized(phrase)));
  const exactQuery = exact(parsed.lexicalText);
  const exactVisibleQualifiedName = Boolean(exactQuery) &&
    [...fields.qualifiedName, ...fields.alias].some((item) => exact(item) === exactQuery);
  const exactVisibleName = Boolean(exactQuery) &&
    [...fields.name, ...fields.qualifiedName, ...fields.alias]
      .some((item) => exact(item) === exactQuery);
  const exactBusinessTerm = Boolean(exactQuery) && fields.businessTerm.some((item) =>
    normalized(item) === normalized(exactQuery));
  const denominator = queryTokens.length + parsed.phrases.length;
  const lexical = denominator ? Math.min(1,
    (matchedTokens.length + phraseMatches.length) / denominator) : null;
  const matchedAlias = fields.alias.find((item) => exact(item) === exactQuery ||
    queryTokens.some((token) => tokens(item).includes(token))) ?? null;
  return {aliases, fields, lexical, matchedTokens, phraseMatches,
    exactVisibleQualifiedName, exactVisibleName, exactBusinessTerm, matchedAlias};
}

function includesAny(actual, expected) {
  const candidates = new Set(values(actual).map(normalized));
  return expected.some((item) => candidates.has(normalized(item)));
}

function facetValues(document, field, security) {
  if(field === 'entityClass') return [document.entityClass];
  if(field === 'provider') return values(document.provider);
  if(field === 'nativeKind') return [document.nativeKind];
  if(field === 'domain') return document.domainRefs;
  if(field === 'owner') return document.ownerRefs;
  if(field === 'environment') return values(document.connectionEnvironment);
  if(field === 'classification') return values(document.classification);
  if(field === 'tag') return document.tags;
  if(field === 'accessSurface') return document.authorizedAliases.filter((item) =>
    security.admitAlias(item, document) === true).map((item) => item.dialectId);
  if(field === 'updated') return [document.updatedAt.slice(0, 10)];
  const mapping = {certification: 'certification', quality: 'quality',
    freshness: 'freshness', access: 'access'};
  return values(document.facetValues[mapping[field] ?? field]);
}

function assertFilterAuthority(query, parsed, security) {
  const explicit = Object.entries(FILTER_SIGNAL).filter(([field]) =>
    query[field].length).map(([, signal]) => signal);
  const expert = parsed.filters.map((item) => ({provider: 'provider', domain: 'domain',
    owner: 'owner', type: 'nativeKind', certified: 'certification', quality: 'quality',
    freshness: 'freshness', access: 'access', tag: 'tag'})[item.field]);
  if(query.updatedAfter || query.updatedBefore) explicit.push('updated');
  if(query.minimumUsage !== null) explicit.push('usage');
  for(const field of new Set([...explicit, ...expert])) {
    if(security.admitFacet(field) !== true) throw new TypeError(
      `Discovery search is not authorized to filter by ${field}.`
    );
  }
}

function matchesFilters(document, query, parsed, security, featureEvidence) {
  const mappings = {entityClasses: 'entityClass', providers: 'provider',
    domains: 'domain', owners: 'owner', certification: 'certification',
    qualityState: 'quality', freshness: 'freshness', accessState: 'access',
    classification: 'classification', tags: 'tag', nativeKinds: 'nativeKind',
    environments: 'environment'};
  for(const [queryField, facet] of Object.entries(mappings)) {
    if(query[queryField].length && !includesAny(facetValues(document, facet, security),
      query[queryField])) return false;
  }
  const expertMap = {provider: 'provider', domain: 'domain', owner: 'owner',
    certified: 'certification', quality: 'quality', freshness: 'freshness',
    access: 'access', tag: 'tag'};
  for(const filter of parsed.filters) {
    const matched = filter.field === 'type' ?
      includesAny([document.entityClass, document.nativeKind], filter.values) :
      includesAny(facetValues(document, expertMap[filter.field], security),
        filter.values);
    if((!filter.exclude && !matched) || (filter.exclude && matched)) return false;
  }
  if(query.updatedAfter && Date.parse(document.updatedAt) < Date.parse(
    query.updatedAfter)) return false;
  if(query.updatedBefore && Date.parse(document.updatedAt) > Date.parse(
    query.updatedBefore)) return false;
  if(query.minimumUsage !== null && featureEvidence &&
      (featureEvidence?.tieBreak?.usage ?? 0.5) < query.minimumUsage) return false;
  return true;
}

function stable(value) {
  if(Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
  if(value && typeof value === 'object') return `{${Object.keys(value).sort().map(
    (key) => `${JSON.stringify(key)}:${stable(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}

export class InMemoryDiscoveryCursorAuthority {
  constructor({tokenFactory, now=() => Date.now(), ttlMs=15 * 60 * 1000}={}) {
    if(typeof tokenFactory !== 'function') throw new TypeError(
      'Discovery cursor authority requires an opaque token factory.'
    );
    if(!Number.isInteger(ttlMs) || ttlMs < 1000) throw new TypeError(
      'Discovery cursor lifetime must be at least one second.'
    );
    this.tokenFactory = tokenFactory; this.now = now; this.ttlMs = ttlMs;
    this.records = new Map();
  }

  issue(record) {
    plainObject(record, 'Discovery cursor record'); noRawSecrets(record,
      'Discovery cursor record');
    const token = platformValue(this.tokenFactory(), 'Discovery opaque cursor', 4096);
    if(this.records.has(token)) throw new TypeError(
      'Discovery cursor factory returned a duplicate token.'
    );
    this.records.set(token, immutable({...record, expiresAt: this.now() + this.ttlMs}));
    return token;
  }

  read(token) {
    token = platformValue(token, 'Discovery opaque cursor', 4096);
    const record = this.records.get(token);
    if(!record) throw new DiscoveryQuerySyntaxError('Unknown search cursor', 0,
      'stale_cursor');
    this.records.delete(token);
    if(record.expiresAt <= this.now()) throw new DiscoveryQuerySyntaxError(
      'Expired search cursor', 0, 'stale_cursor');
    return record;
  }
}

function mergeCandidate(target, document, source, score, evidence=[]) {
  const canonicalRef = document.canonicalRef;
  const candidate = target.get(canonicalRef) ?? {document, lexical: null,
    semantic: null, business: null, graph: null, lineage: null,
    sourceEvidence: []};
  candidate[source] = candidate[source] === null ? score : Math.max(
    candidate[source], score);
  candidate.sourceEvidence.push(...evidence);
  target.set(canonicalRef, candidate);
}

function backendRows(input, label) {
  if(!Array.isArray(input)) throw new TypeError(`${label} must return an array.`);
  return input.map((item) => {
    plainObject(item, `${label} candidate`);
    return {...item, document: validateDiscoveryDocument(item.document)};
  });
}

function htmlEscape(value) {
  return String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll('\'', '&#39;');
}

function resultProjection(candidate, security) {
  const document = candidate.document;
  const metadata = security.admitDocument(document, 'DISCOVER_METADATA') === true;
  const description = metadata ? document.description : null;
  const matched = candidate.matchedAlias ? `Matched alias: ${candidate.matchedAlias}` :
    candidate.matchedTokens?.length ? `Matched: ${candidate.matchedTokens.join(', ')}` :
      document.name;
  return immutable({resultRef: document.documentId,
    canonicalRef: security.exposeCanonicalRef(document) === true ?
      document.canonicalRef : null, entityClass: document.entityClass,
    name: document.name, snippet: htmlEscape(description || matched),
    provider: metadata ? document.provider : null,
    nativeKind: metadata ? document.nativeKind : null,
    trustSummary: metadata && security.admitSignal(document, 'trust') === true ?
      document.trustSignals : null,
    accessSummary: metadata && security.admitSignal(document, 'access') === true ?
      document.accessState : null,
    score: candidate.score, explanation: candidate.explanation,
    visibleAliases: candidate.visibleAliases.map((item) => immutable({
      surfaceId: item.surfaceId, dialectId: item.dialectId,
      visibleQualifiedName: item.visibleQualifiedName})), actions: []});
}

function sortRanked(items, sort) {
  if(sort === 'relevance') return items;
  const sorted = [...items];
  const scalar = (item) => sort === 'name' ? item.document.name :
    sort === 'updated' ? item.document.updatedAt : item.tieBreak[sort];
  return sorted.sort((left, right) => {
    const a = scalar(left); const b = scalar(right);
    const order = sort === 'name' ? String(a).localeCompare(String(b)) :
      sort === 'updated' ? String(b).localeCompare(String(a)) : b - a;
    return order || left.document.canonicalRef.localeCompare(
      right.document.canonicalRef);
  });
}

export class DiscoverySearchService {
  constructor({indexBackend, rankingEngine, graphIndex=null, businessSource=null,
    semanticAuthority=null, cursorAuthority, queryIdFactory}={}) {
    this.backend = normalizeDiscoveryIndexBackend(indexBackend);
    if(!rankingEngine || typeof rankingEngine.rank !== 'function') throw new TypeError(
      'Discovery search requires a ranking engine.'
    );
    if(graphIndex && (typeof graphIndex.neighbors !== 'function' ||
        typeof graphIndex.health !== 'function')) throw new TypeError(
      'Discovery graph source is incomplete.'
    );
    if(businessSource && typeof businessSource.search !== 'function') throw new TypeError(
      'Discovery business candidate source is incomplete.'
    );
    if(semanticAuthority && typeof semanticAuthority.vectorForQuery !== 'function') {
      throw new TypeError('Discovery semantic authority is incomplete.');
    }
    if(!cursorAuthority || typeof cursorAuthority.issue !== 'function' ||
        typeof cursorAuthority.read !== 'function') throw new TypeError(
      'Discovery search requires a cursor authority.'
    );
    if(typeof queryIdFactory !== 'function') throw new TypeError(
      'Discovery search requires a query ID factory.'
    );
    this.rankingEngine = rankingEngine; this.graphIndex = graphIndex;
    this.businessSource = businessSource; this.semanticAuthority = semanticAuthority;
    this.cursorAuthority = cursorAuthority; this.queryIdFactory = queryIdFactory;
  }

  async search(input, {security, profile=BALANCED_DISCOVERY_RANKING_PROFILE,
    context={}}={}) {
    security = requireSecurity(security);
    const query = validateDiscoverySearchQuery(input);
    const parsed = parseDiscoverySearchText(query.text);
    assertFilterAuthority(query, parsed, security);
    profile = validateDiscoveryRankingProfile(profile, {requirePublished: true});
    if(query.rankingProfile && ![profile.profileId, profile.name].includes(
      query.rankingProfile)) throw new TypeError(
      'Discovery query ranking profile does not match the supplied profile.'
    );
    const fingerprint = stable({...query, cursor: null});
    const active = await this.backend.health();
    if(!active.ready) throw new TypeError('Discovery index is not ready.');
    let offset = 0; let indexRevision = active.activeRevision;
    let queryId = platformValue(this.queryIdFactory(), 'Discovery query ID');
    let graphRevision = null;
    if(query.cursor) {
      const cursor = this.cursorAuthority.read(query.cursor);
      if(cursor.queryFingerprint !== fingerprint ||
          cursor.profileRevision !== profile.publishedRevision) {
        throw new DiscoveryQuerySyntaxError('Search cursor does not match the query',
          0, 'cursor_mismatch');
      }
      ({offset, indexRevision, queryId, graphRevision} = cursor);
    }
    const warnings = []; const candidates = new Map();
    const admit = (document) => security.admitDocument(document,
      'DISCOVER_IDENTITY') === true;
    const aliasAdmit = (item, document) => security.admitAlias(item, document) === true;
    const lexical = parsed.lexicalText || query.mode === 'ADVANCED_FACETED' ?
      await this.backend.lexicalSearch({text: parsed.lexicalText || '*',
        matchAll: !parsed.lexicalText, limit: 500, revision: indexRevision,
        admit, aliasAdmit, includeExact: true,
        textProjection: (document) => Object.values(visibleText(document,
          parsed, security).fields).flat(),
        exactProjection: (document, aliases) => {
          const metadata = security.admitDocument(document,
            'DISCOVER_METADATA') === true;
          const business = metadata && security.admitSignal(document,
            'business') === true ? [...document.businessTerms,
              ...document.synonyms] : [];
          return [document.name, ...document.qualifiedNames,
            ...aliases.map((item) => item.visibleQualifiedName), ...business];
        }}) : [];
    for(const item of backendRows(lexical, 'Discovery lexical backend')) {
      if(!admit(item.document)) continue;
      const visible = visibleText(item.document, parsed, security);
      if(parsed.lexicalText && !visible.lexical && !visible.exactVisibleName &&
          !visible.exactBusinessTerm) continue;
      mergeCandidate(candidates, item.document, 'lexical', visible.lexical ?? 0,
        [{source: 'lexical', matchedTerms: visible.matchedTokens,
          phraseMatches: visible.phraseMatches}]);
      Object.assign(candidates.get(item.document.canonicalRef), visible,
        {visibleAliases: visible.aliases});
    }
    const semanticRequired = ['SEMANTIC', 'SIMILAR_TO'].includes(query.mode);
    const semanticRequested = semanticRequired || Boolean(query.text);
    if(this.semanticAuthority && semanticRequested) {
      const vector = await this.semanticAuthority.vectorForQuery({query, context});
      const semantic = await this.backend.semanticSearch({vector, limit: 500,
        revision: indexRevision, admit: (document) => admit(document) &&
          security.admitSignal(document, 'semantic') === true});
      for(const item of backendRows(semantic, 'Discovery semantic backend')) {
        if(admit(item.document) &&
            security.admitSignal(item.document, 'semantic') === true) {
          mergeCandidate(candidates, item.document, 'semantic', item.score,
            [{source: 'semantic'}]);
        }
      }
    } else if(semanticRequired) throw new TypeError(
      'Semantic Discovery search is unavailable without an embedding authority.'
    );
    else if(semanticRequested) warnings.push('semantic_unavailable');
    const graphRequired = query.mode === 'GRAPH_RELATED';
    if(query.relatedTo.length || graphRequired) {
      if(!this.graphIndex) throw new TypeError(
        'Graph-related Discovery search is unavailable without a graph index.'
      );
      const graphHealth = await this.graphIndex.health();
      graphRevision = graphRevision ?? graphHealth.activeRevision;
      if(!graphHealth.ready) throw new TypeError('Discovery graph index is not ready.');
      const graph = await this.graphIndex.neighbors({canonicalRefs: query.relatedTo,
        revision: graphRevision, limit: 100,
        admitEdge: (edge) => security.admitGraphEdge(edge) === true});
      if(!Array.isArray(graph)) throw new TypeError(
        'Discovery graph source returned an invalid candidate set.'
      );
      const seeds = new Set(query.relatedTo);
      const graphCandidates = new Map();
      for(const item of graph) {
        plainObject(item, 'Discovery graph candidate');
        if(!Array.isArray(item.edges)) throw new TypeError(
          'Discovery graph candidate requires source edges.'
        );
        for(const inputEdge of item.edges) {
          const edge = validateDiscoveryGraphEdge(inputEdge, graphRevision);
          if(security.admitGraphEdge(edge) !== true) continue;
          const canonicalRef = seeds.has(edge.fromRef) ? edge.toRef :
            !edge.directed && seeds.has(edge.toRef) ? edge.fromRef : null;
          if(!canonicalRef || seeds.has(canonicalRef)) continue;
          const prior = graphCandidates.get(canonicalRef);
          const evidence = {source: 'graph', edgeId: edge.edgeId, type: edge.type,
            direction: seeds.has(edge.fromRef) ? 'outbound' : 'inbound'};
          if(!prior || prior.score < edge.weight) graphCandidates.set(canonicalRef,
            {score: edge.weight, evidence: [evidence]});
          else if(prior.score === edge.weight) prior.evidence.push(evidence);
        }
      }
      for(const [canonicalRef, graphCandidate] of graphCandidates) {
        try {
          const document = validateDiscoveryDocument(await this.backend.getDocument(
            canonicalRef, {revision: indexRevision, admit}));
          if(admit(document) && security.admitSignal(document, 'lineage') === true) {
            mergeCandidate(candidates, document, 'graph', graphCandidate.score,
              graphCandidate.evidence);
          }
        } catch(error) {
          if(error?.code !== 'not_found') throw error;
        }
      }
    }
    if(this.businessSource) {
      const business = await this.businessSource.search({query, parsed,
        indexRevision, admit, security, limit: 200, context});
      if(!Array.isArray(business)) throw new TypeError(
        'Discovery business source returned an invalid candidate set.'
      );
      for(const item of business) {
        plainObject(item, 'Discovery business candidate');
        exactObjectFields(item, ['canonicalRef', 'score', 'evidence'],
          'Discovery business candidate');
        if(!Number.isFinite(item.score) || item.score < 0 || item.score > 1) {
          throw new TypeError('Discovery business candidate score must be 0..1.');
        }
        if(!Array.isArray(item.evidence)) throw new TypeError(
          'Discovery business candidate evidence must be an array.'
        );
        const evidence = item.evidence.map((entry) => {
          plainObject(entry, 'Discovery business candidate evidence');
          noRawSecrets(entry, 'Discovery business candidate evidence');
          return {source: 'business', ...entry};
        });
        const document = validateDiscoveryDocument(await this.backend.getDocument(
          item.canonicalRef, {revision: indexRevision, admit}));
        if(admit(document) && security.admitSignal(document, 'business') === true) {
          mergeCandidate(
            candidates, document, 'business', item.score,
            evidence);
        }
      }
    } else if(query.mode === 'BUSINESS_TERM') throw new TypeError(
      'Business-term Discovery search is unavailable without its authority.'
    );
    let merged = [...candidates.values()];
    for(const candidate of merged) {
      const visible = visibleText(candidate.document, parsed, security);
      Object.assign(candidate, visible, {visibleAliases: visible.aliases});
      if(visible.matchedAlias) candidate.sourceEvidence.push({source: 'alias',
        visibleQualifiedName: visible.matchedAlias});
      if(visible.exactVisibleName) candidate.sourceEvidence.push({source: 'exact',
        field: 'visible_name'});
      if(visible.exactBusinessTerm) candidate.sourceEvidence.push({source: 'exact',
        field: 'business_term'});
    }
    const technicalSearch = query.nativeKinds.length > 0 ||
      ['ADVANCED_FACETED', 'FIELD', 'GRAPH_RELATED'].includes(query.mode);
    merged = merged.filter((candidate) => {
      const visibility = discoveryDocumentVisibility(candidate.document);
      return (technicalSearch ? visibility.technicalSearch :
        visibility.businessSearch) && matchesFilters(candidate.document, query,
        parsed, security);
    });
    merged.sort((left, right) => Number(right.exactVisibleName) -
      Number(left.exactVisibleName) || Math.max(right.lexical ?? 0,
      right.semantic ?? 0, right.business ?? 0, right.graph ?? 0) -
      Math.max(left.lexical ?? 0, left.semantic ?? 0, left.business ?? 0,
        left.graph ?? 0) || left.document.canonicalRef.localeCompare(
      right.document.canonicalRef));
    if(merged.length > 1000) {
      merged = merged.slice(0, 1000); warnings.push('candidate_cap_applied');
    }
    let ranked = this.rankingEngine.rank(merged, {profile, query, security, context});
    if(query.minimumUsage !== null) ranked = ranked.filter((candidate) =>
      matchesFilters(candidate.document, query, parsed, security, candidate));
    ranked = sortRanked(ranked, query.sort);
    const facets = this._facets(ranked, security);
    const page = ranked.slice(offset, offset + query.pageSize);
    const nextOffset = offset + page.length;
    const nextCursor = nextOffset < ranked.length ? this.cursorAuthority.issue({
      queryFingerprint: fingerprint, profileRevision: profile.publishedRevision,
      indexRevision, graphRevision, queryId, offset: nextOffset}) : null;
    return immutable({queryId, indexRevision,
      rankingProfile: {profileId: profile.profileId,
        revision: profile.publishedRevision}, totalVisibleEstimate: ranked.length,
      facets, results: page.map((item) => resultProjection(item, security)),
      warnings, nextCursor});
  }

  async autocomplete(prefix, {security, limit=20, revision}={}) {
    security = requireSecurity(security);
    prefix = platformValue(prefix, 'Discovery autocomplete prefix', 1000);
    if([...prefix].length < 2) throw new TypeError(
      'Discovery autocomplete prefix must contain at least two characters.'
    );
    if(!Number.isInteger(limit) || limit < 1 || limit > 50) throw new TypeError(
      'Discovery autocomplete limit must be an integer from 1 to 50.'
    );
    const health = await this.backend.health();
    const parsed = parseDiscoverySearchText(prefix);
    const candidates = await this.backend.lexicalSearch({text: prefix, limit: 500,
      revision: revision ?? health.activeRevision,
      admit: (document) => security.admitDocument(document,
        'DISCOVER_IDENTITY') === true,
      aliasAdmit: (alias, document) => security.admitAlias(alias, document) === true,
      textProjection: (document) => Object.values(visibleText(document,
        parsed, security).fields).flat()});
    const match = normalized(prefix); const suggestions = new Map();
    for(const {document} of backendRows(candidates, 'Discovery autocomplete backend')) {
      if(security.admitDocument(document, 'DISCOVER_IDENTITY') !== true) continue;
      if(!discoveryDocumentVisibility(document).businessSearch) continue;
      const visible = visibleText(document, parsed, security);
      const options = [{kind: 'name', value: document.name},
        ...visible.aliases.map((item) => ({kind: 'alias',
          value: item.visibleQualifiedName})),
        ...(security.admitSignal(document, 'business') === true ?
          visible.fields.businessTerm.map((value) =>
            ({kind: 'businessTerm', value})) : [])];
      for(const option of options) if(normalized(option.value).startsWith(match)) {
        const key = `${option.kind}:${option.value}`;
        if(!suggestions.has(key)) suggestions.set(key, immutable({...option,
          resultRef: document.documentId,
          canonicalRef: security.exposeCanonicalRef(document) === true ?
            document.canonicalRef : null}));
      }
    }
    return immutable([...suggestions.values()].sort((left, right) =>
      left.value.localeCompare(right.value) || left.kind.localeCompare(right.kind))
      .slice(0, limit));
  }

  _facets(ranked, security) {
    const output = {};
    for(const field of DISCOVERY_DEFAULT_FACETS) {
      if(security.admitFacet(field) !== true) continue;
      const counts = new Map();
      for(const candidate of ranked) for(const value of facetValues(
        candidate.document, field, security)) {
        const key = String(value); counts.set(key, (counts.get(key) ?? 0) + 1);
      }
      output[field] = [...counts.entries()].sort((left, right) =>
        right[1] - left[1] || left[0].localeCompare(right[0]))
        .slice(0, 50)
        .map(([value, count]) => ({value, count}));
    }
    return immutable(output);
  }
}
