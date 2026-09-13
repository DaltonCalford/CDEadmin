/////////////////////////////////////////////////////////////
// Canonical, versioned DiscoveryDocument projection authority.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';

export const DISCOVERY_ENTITY_CLASSES = Object.freeze([
  'LIVE_RESOURCE', 'PROJECT_ASSET', 'DATA_PRODUCT', 'BUSINESS_TERM', 'METRIC',
  'DIMENSION', 'DOMAIN', 'GLOSSARY', 'API', 'DASHBOARD', 'REPORT',
  'SEMANTIC_MODEL', 'CUBE', 'QUERY', 'PIPELINE', 'CDC_STREAM', 'CONTRACT',
  'QUALITY_RULESET', 'LINEAGE_VIEW', 'ML_MODEL', 'VECTOR_INDEX', 'DDN_WORKSPACE',
  'PERSON', 'TEAM', 'POLICY',
]);
export const DISCOVERY_VISIBILITY_FIELDS = Object.freeze([
  'businessSearch', 'technicalSearch', 'recommendations',
  'dataProductCandidate',
]);

const FIELDS = Object.freeze(['schemaVersion', 'documentId', 'canonicalRef',
  'entityClass', 'nativeKind', 'provider', 'connectionEnvironment', 'name',
  'qualifiedNames', 'authorizedAliases', 'description', 'businessTerms',
  'synonyms', 'domainRefs', 'ownerRefs', 'stewardRefs', 'tags', 'classification',
  'schemaSummary', 'nativeMetadataSummary', 'trustSignals', 'qualitySignals',
  'freshnessSignals', 'usageSignals', 'lineageSignals', 'contractSignals',
  'certifications', 'deprecationState', 'accessState', 'searchText',
  'embeddingRefs', 'facetValues', 'updatedAt', 'sourceRevision', 'indexRevision']);
const REQUIRED_FIELDS = Object.freeze(['schemaVersion', 'documentId', 'canonicalRef',
  'entityClass', 'nativeKind', 'name', 'authorizedAliases', 'trustSignals',
  'qualitySignals', 'freshnessSignals', 'usageSignals', 'accessState', 'searchText',
  'updatedAt', 'sourceRevision', 'indexRevision']);
const ARRAY_FIELDS = Object.freeze(['qualifiedNames', 'businessTerms', 'synonyms',
  'domainRefs', 'ownerRefs', 'stewardRefs', 'tags', 'embeddingRefs']);
const OBJECT_FIELDS = Object.freeze(['schemaSummary', 'nativeMetadataSummary',
  'trustSignals', 'qualitySignals', 'freshnessSignals', 'usageSignals',
  'lineageSignals', 'contractSignals', 'accessState', 'facetValues']);

function optional(value, label, maximum=8192) {
  return value === null || value === undefined ? null : platformValue(
    value, label, maximum);
}

function strings(value, label) {
  if(!Array.isArray(value)) throw new TypeError(`${label} must be an array.`);
  const result = value.map((item) => platformValue(item, `${label} value`, 4096));
  if(new Set(result).size !== result.length) throw new TypeError(
    `${label} contains duplicate values.`
  );
  return result;
}

function object(value, label) {
  plainObject(value, label); noRawSecrets(value, label);
  return immutable({...value});
}

function objects(value, label) {
  if(!Array.isArray(value)) throw new TypeError(`${label} must be an array.`);
  return value.map((item) => object(item, `${label} item`));
}

function timestamp(value, label) {
  value = platformValue(value, label, 64);
  if(Number.isNaN(Date.parse(value))) throw new TypeError(`${label} is invalid.`);
  return value;
}

export function validateDiscoveryDocument(input) {
  plainObject(input, 'Discovery document'); noRawSecrets(input, 'Discovery document');
  const unknown = Object.keys(input).filter((field) => !FIELDS.includes(field));
  if(unknown.length) throw new TypeError(
    `Discovery document contains unsupported field ${unknown[0]}.`
  );
  const missing = REQUIRED_FIELDS.find((field) => !Object.hasOwn(input, field));
  if(missing) throw new TypeError(`Discovery document requires ${missing}.`);
  if(input.schemaVersion !== 1) throw new TypeError(
    'Discovery document schema version is invalid.'
  );
  if(!DISCOVERY_ENTITY_CLASSES.includes(input.entityClass)) throw new TypeError(
    'Discovery document entity class is invalid.'
  );
  const result = {schemaVersion: 1,
    documentId: platformValue(input.documentId, 'Discovery document ID', 512),
    canonicalRef: platformValue(input.canonicalRef, 'Discovery canonical reference', 4096),
    entityClass: input.entityClass,
    nativeKind: platformValue(input.nativeKind, 'Discovery native kind', 512),
    provider: optional(input.provider, 'Discovery provider', 512),
    connectionEnvironment: optional(input.connectionEnvironment,
      'Discovery connection environment', 512),
    name: platformValue(input.name, 'Discovery name', 1024),
    authorizedAliases: objects(input.authorizedAliases, 'Discovery authorized aliases'),
    description: optional(input.description, 'Discovery description', 65536),
    classification: optional(input.classification, 'Discovery classification', 512),
    certifications: objects(input.certifications ?? [], 'Discovery certifications'),
    deprecationState: optional(input.deprecationState, 'Discovery deprecation state', 512),
    searchText: platformValue(input.searchText, 'Discovery search text', 262144),
    updatedAt: timestamp(input.updatedAt, 'Discovery update time'),
    sourceRevision: platformValue(input.sourceRevision, 'Discovery source revision', 2048),
    indexRevision: platformValue(input.indexRevision, 'Discovery index revision', 2048)};
  for(const field of ARRAY_FIELDS) result[field] = strings(input[field] ?? [],
    `Discovery ${field}`);
  for(const field of OBJECT_FIELDS) result[field] = object(input[field] ?? {},
    `Discovery ${field}`);
  if(!Object.keys(result.accessState).length) throw new TypeError(
    'Discovery document requires explicit access/security state.'
  );
  for(const [field, values] of Object.entries(result.facetValues)) {
    const items = Array.isArray(values) ? values : [values];
    if(items.some((value) => !['string', 'number', 'boolean'].includes(
      typeof value) || (typeof value === 'number' && !Number.isFinite(value)))) {
      throw new TypeError(`Discovery facet ${field} contains an invalid value.`);
    }
  }
  return immutable(result);
}

export function discoveryDocumentVisibility(document) {
  plainObject(document, 'Discovery document visibility source');
  noRawSecrets(document, 'Discovery document visibility source');
  const nativeMetadata = document.nativeMetadataSummary ?? {};
  const facets = document.facetValues ?? {};
  plainObject(nativeMetadata, 'Discovery native metadata visibility source');
  plainObject(facets, 'Discovery facet visibility source');
  const configured = nativeMetadata.discoveryVisibility;
  if(configured !== undefined) {
    plainObject(configured, 'Discovery document visibility');
    const unknown = Object.keys(configured).find((field) =>
      !DISCOVERY_VISIBILITY_FIELDS.includes(field));
    const missing = DISCOVERY_VISIBILITY_FIELDS.find((field) =>
      !Object.hasOwn(configured, field));
    if(unknown || missing || DISCOVERY_VISIBILITY_FIELDS.some((field) =>
      typeof configured[field] !== 'boolean')) throw new TypeError(
      `Discovery document visibility is invalid${unknown ? `: ${unknown}` : ''}.`
    );
    return immutable({...configured});
  }
  const systemCatalog = nativeMetadata.systemCatalog === true ||
    facets.systemCatalog === true ||
    ['system_catalog', 'catalog_projection', 'system_table', 'system_view']
      .includes(String(document.nativeKind).toLocaleLowerCase());
  return immutable(systemCatalog ? {businessSearch: false,
    technicalSearch: true, recommendations: false,
    dataProductCandidate: false} : {businessSearch: true,
    technicalSearch: true, recommendations: true,
    dataProductCandidate: true});
}

function alias(surface) {
  return immutable({surfaceId: surface.surfaceId, kind: surface.kind,
    dialectId: surface.dialectId, visibleQualifiedName: surface.visibleQualifiedName,
    workareaSchemaRef: surface.workareaSchemaRef,
    visibilityScope: surface.visibilityScope,
    queryCapabilities: [...surface.queryCapabilities],
    mutationCapabilities: [...surface.mutationCapabilities],
    crossSurfaceVisibility: surface.crossSurfaceVisibility,
    evidenceVersion: surface.evidenceVersion});
}

function normalizeReference(reference, identities) {
  plainObject(reference, 'Discovery resource reference');
  if(reference.provider === 'scratchbird') return identities.createScratchBird({
    instanceId: reference.instanceId, canonicalUuid: reference.canonicalUuid,
    kind: reference.kind, nativePath: reference.nativePath,
    revision: reference.revision});
  return identities.create({provider: reference.provider,
    connection: reference.connection, scope: reference.scope, kind: reference.kind,
    nativeIdentity: reference.nativeIdentity, revision: reference.revision});
}

export class DiscoveryCanonicalizer {
  constructor({identities}={}) {
    if(!identities || typeof identities.create !== 'function' ||
        typeof identities.createScratchBird !== 'function' ||
        typeof identities.accessSurfaces !== 'function') throw new TypeError(
      'Discovery canonicalization requires ResourceIdentityService.'
    );
    this.identities = identities;
  }

  resource(input, {authorization={}}={}) {
    plainObject(input, 'Discovery resource projection');
    plainObject(input.document, 'Discovery resource document');
    const reference = normalizeReference(input.reference, this.identities);
    const candidate = {...input.document, canonicalRef: reference.canonical,
      provider: reference.provider, facetValues: {...input.document.facetValues,
        provider: reference.provider, entityClass: input.document.entityClass,
        nativeKind: input.document.nativeKind}};
    if(input.document.canonicalRef && input.document.canonicalRef !== reference.canonical) {
      throw new TypeError('Discovery canonical reference conflicts with resource identity.');
    }
    if(reference.provider === 'scratchbird') {
      const aliases = this.identities.accessSurfaces(reference, authorization).map(alias);
      candidate.authorizedAliases = aliases;
    }
    return validateDiscoveryDocument(candidate);
  }

  asset(input) {
    plainObject(input, 'Discovery asset projection');
    plainObject(input.document, 'Discovery asset document');
    const canonicalRef = platformValue(input.assetRef,
      'Discovery asset reference', 4096);
    if(input.document.canonicalRef && input.document.canonicalRef !== canonicalRef) {
      throw new TypeError('Discovery canonical reference conflicts with asset identity.');
    }
    return validateDiscoveryDocument({...input.document, canonicalRef,
      provider: input.document.provider ?? null,
      facetValues: {...input.document.facetValues,
        entityClass: input.document.entityClass,
        nativeKind: input.document.nativeKind}});
  }

  imported(input, context={}) {
    plainObject(input, 'Imported Discovery projection');
    if(input.mappedResource) return this.resource({reference: input.mappedResource,
      document: input.document}, context);
    if(input.importedEntityRef) return this.asset({assetRef: input.importedEntityRef,
      document: {...input.document, provider: input.document.provider ??
        'external_catalog'}});
    throw new TypeError(
      'Imported Discovery metadata requires a stable resource mapping or imported entity reference.'
    );
  }

  batch(inputs, context={}) {
    if(!Array.isArray(inputs)) throw new TypeError(
      'Discovery canonicalization batch must be an array.'
    );
    const byCanonical = new Map();
    for(const input of inputs) {
      const document = input.kind === 'resource' ? this.resource(input, context) :
        input.kind === 'asset' ? this.asset(input) : input.kind === 'imported' ?
          this.imported(input, context) : (() => { throw new TypeError(
            'Discovery input kind must be resource, asset or imported.'); })();
      if(byCanonical.has(document.canonicalRef)) throw new TypeError(
        `Discovery batch contains duplicate canonical identity ${document.canonicalRef}.`
      );
      byCanonical.set(document.canonicalRef, document);
    }
    return immutable([...byCanonical.values()]);
  }
}
