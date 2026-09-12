/////////////////////////////////////////////////////////////
// CDEadmin provider, identity, credential and layout authorities.
/////////////////////////////////////////////////////////////

import {stablePlatformId, PlatformRegistryError} from './PlatformRegistry';
import {immutable, noRawSecrets, plainObject, platformValue} from './serviceUtils';

export class ProviderRegistry {
  constructor() { this.providers = new Map(); }

  register(input) {
    const id = stablePlatformId(input?.id, 'Provider ID');
    if(this.providers.has(id)) throw new PlatformRegistryError(
      'duplicate', `Provider already registered: ${id}`, id
    );
    if(!Array.isArray(input.modelTypes) || !input.modelTypes.length) {
      throw new TypeError(`Provider ${id} must declare native model types.`);
    }
    const descriptor = immutable({
      schema: 'cdeadmin.provider-definition.v1', id,
      title: String(input.title ?? id), version: String(input.version ?? ''),
      family: String(input.family ?? 'other'),
      modelTypes: [...new Set(input.modelTypes.map(String))],
      connectionTypes: [...new Set((input.connectionTypes ?? []).map(String))],
      attribution: {...(input.attribution ?? {})},
      available: input.available === true, installed: input.installed === true,
      lifecycle: input.lifecycle ?? null, metadataAdapter: input.metadataAdapter ?? null,
      operationAdapter: input.operationAdapter ?? null,
    });
    this.providers.set(id, descriptor);
    return () => this.providers.delete(id);
  }

  get(id) {
    const provider = this.providers.get(stablePlatformId(id, 'Provider ID'));
    if(!provider) throw new PlatformRegistryError('not_found', `Unknown provider: ${id}`, id);
    return provider;
  }

  list({family, modelType, available}={}) {
    return [...this.providers.values()].filter((provider) =>
      (!family || provider.family === family) &&
      (!modelType || provider.modelTypes.includes(modelType)) &&
      (available === undefined || provider.available === available)
    ).sort((left, right) => left.title.localeCompare(right.title));
  }

  async start(id, context={}) {
    const provider = this.get(id); await provider.lifecycle?.start?.(context); return provider;
  }

  async stop(id, context={}) {
    const provider = this.get(id); await provider.lifecycle?.stop?.(context); return provider;
  }
}

export class ResourceIdentityService {
  constructor({canDiscoverSurface=() => true}={}) {
    if(typeof canDiscoverSurface !== 'function') {
      throw new TypeError('Access-surface authorization must be callable.');
    }
    this.canDiscoverSurface = canDiscoverSurface;
    this.surfaces = new Map();
    this.surfaceIdsByCanonical = new Map();
  }

  create(input) {
    plainObject(input, 'Resource reference');
    noRawSecrets(input, 'Resource reference');
    if(input.provider === 'scratchbird') {
      throw new TypeError(
        'ScratchBird resources require a durable UUID-backed identity.'
      );
    }
    const value = immutable({schema: 'cdeadmin.resource-ref.v1',
      provider: stablePlatformId(input.provider, 'Resource provider'),
      providerId: stablePlatformId(input.provider, 'Resource provider'),
      connection: platformValue(input.connection, 'Connection scope'),
      scope: platformValue(input.scope ?? '/', 'Native scope'),
      kind: stablePlatformId(input.kind, 'Resource kind'),
      nativeIdentity: platformValue(input.nativeIdentity, 'Native identity'),
      revision: input.revision === undefined ? null : String(input.revision)});
    return immutable({...value, canonical: this.canonical(value)});
  }

  createScratchBird(input) {
    plainObject(input, 'ScratchBird resource reference');
    noRawSecrets(input, 'ScratchBird resource reference');
    const allowed = new Set([
      'instanceId', 'canonicalUuid', 'kind', 'nativePath', 'revision',
    ]);
    const unknown = Object.keys(input).filter((field) => !allowed.has(field));
    if(unknown.length) throw new TypeError(
      `ScratchBird resource contains unsupported field ${unknown[0]}.`
    );
    const instanceId = platformValue(input.instanceId, 'ScratchBird instance ID');
    const canonicalUuid = platformValue(
      input.canonicalUuid,
      'ScratchBird canonical UUID'
    );
    const kind = stablePlatformId(input.kind, 'ScratchBird resource kind');
    const nativePath = platformValue(input.nativePath, 'ScratchBird native path');
    return immutable({
      schema: 'cdeadmin.resource-ref.v1',
      provider: 'scratchbird',
      providerId: 'scratchbird',
      connection: instanceId,
      instanceId,
      scope: '/',
      kind,
      nativeIdentity: canonicalUuid,
      canonicalUuid,
      nativePath,
      revision: input.revision === undefined ? null : String(input.revision),
      canonical: `scratchbird://${encodeURIComponent(instanceId)}/uuid/${
        encodeURIComponent(canonicalUuid)}`,
    });
  }

  canonical(reference) {
    const parts = [reference.provider, reference.connection, reference.scope,
      reference.kind, reference.nativeIdentity].map((part) =>
      encodeURIComponent(String(part))
    );
    return `cde-resource://${parts.join('/')}`;
  }

  parse(canonical) {
    const scratchBird = /^scratchbird:\/\/([^/]+)\/uuid\/(.+)$/
      .exec(String(canonical));
    if(scratchBird) {
      const [instanceId, canonicalUuid] = scratchBird.slice(1)
        .map(decodeURIComponent);
      return this.createScratchBird({
        instanceId,
        canonicalUuid,
        kind: 'unknown',
        nativePath: canonicalUuid,
      });
    }
    const match = /^cde-resource:\/\/([^/]+)\/([^/]+)\/([^/]+)\/([^/]+)\/(.+)$/
      .exec(String(canonical));
    if(!match) throw new TypeError('Canonical resource reference is invalid.');
    const [provider, connection, scope, kind, nativeIdentity] = match.slice(1)
      .map(decodeURIComponent);
    return this.create({provider, connection, scope, kind, nativeIdentity});
  }

  same(left, right, {includeRevision=false}={}) {
    return left?.canonical === right?.canonical &&
      (!includeRevision || left?.revision === right?.revision);
  }

  createAccessSurface(input) {
    plainObject(input, 'ScratchBird access surface');
    noRawSecrets(input, 'ScratchBird access surface');
    const fields = new Set([
      'schemaVersion', 'surfaceId', 'providerId', 'instanceId', 'kind',
      'dialectId', 'parserProfile', 'workareaSchemaRef',
      'visibleQualifiedName', 'catalogProjectionRefs',
      'canonicalResourceRef', 'visibilityScope', 'queryCapabilities',
      'mutationCapabilities', 'crossSurfaceVisibility', 'evidenceVersion',
    ]);
    const unknown = Object.keys(input).filter((field) => !fields.has(field));
    if(unknown.length) throw new TypeError(
      `ScratchBird access surface contains unsupported field ${unknown[0]}.`
    );
    if(input.schemaVersion !== 1) {
      throw new TypeError('ScratchBird access-surface schema version is invalid.');
    }
    const kind = platformValue(input.kind, 'Access-surface kind');
    if(!['sbsql_native', 'compatibility_parser', 'mcp',
      'cdeadmin_provider'].includes(kind)) {
      throw new TypeError('Access-surface kind is invalid.');
    }
    const crossSurfaceVisibility = platformValue(
      input.crossSurfaceVisibility,
      'Cross-surface visibility'
    );
    if(!['none', 'engine_authorized'].includes(crossSurfaceVisibility)) {
      throw new TypeError('Cross-surface visibility is invalid.');
    }
    if(kind !== 'sbsql_native' && crossSurfaceVisibility !== 'none') {
      throw new TypeError(
        'Only ScratchBird native SBsql may declare cross-surface visibility.'
      );
    }
    const optional = (value, label) => value === undefined || value === null ?
      null : platformValue(value, label);
    const parserProfile = optional(input.parserProfile, 'Parser profile');
    const workareaSchemaRef = optional(
      input.workareaSchemaRef,
      'Sandboxed workarea'
    );
    if(kind === 'compatibility_parser' && (!parserProfile || !workareaSchemaRef)) {
      throw new TypeError(
        'Compatibility access surfaces require parser profile and sandboxed workarea.'
      );
    }
    const canonicalResourceRef = platformValue(
      input.canonicalResourceRef,
      'Canonical ScratchBird resource'
    );
    if(!canonicalResourceRef.startsWith('scratchbird://')) {
      throw new TypeError(
        'Access surfaces must resolve to a canonical ScratchBird resource.'
      );
    }
    const canonicalInstance = /^scratchbird:\/\/([^/]+)\/uuid\/(.+)$/
      .exec(canonicalResourceRef);
    if(!canonicalInstance || decodeURIComponent(canonicalInstance[1]) !==
        input.instanceId) {
      throw new TypeError(
        'Access surface instance does not match its canonical resource.'
      );
    }
    if(input.providerId !== 'scratchbird') {
      throw new TypeError(
        'ScratchBird access-surface provider must be scratchbird.'
      );
    }
    const uniqueStrings = (values, label) => {
      if(!Array.isArray(values)) throw new TypeError(`${label} must be an array.`);
      const result = values.map((value) => platformValue(value, label));
      if(new Set(result).size !== result.length) {
        throw new TypeError(`${label} contains duplicates.`);
      }
      return result;
    };
    return immutable({
      schemaVersion: 1,
      surfaceId: platformValue(input.surfaceId, 'Access-surface ID'),
      providerId: 'scratchbird',
      instanceId: platformValue(input.instanceId, 'ScratchBird instance ID'),
      kind,
      dialectId: platformValue(input.dialectId, 'Access-surface dialect'),
      parserProfile,
      workareaSchemaRef,
      visibleQualifiedName: optional(
        input.visibleQualifiedName,
        'Visible qualified name'
      ),
      catalogProjectionRefs: uniqueStrings(
        input.catalogProjectionRefs ?? [],
        'Catalog projection references'
      ),
      canonicalResourceRef,
      visibilityScope: optional(input.visibilityScope, 'Visibility scope'),
      queryCapabilities: uniqueStrings(
        input.queryCapabilities,
        'Query capabilities'
      ),
      mutationCapabilities: uniqueStrings(
        input.mutationCapabilities,
        'Mutation capabilities'
      ),
      crossSurfaceVisibility,
      evidenceVersion: optional(input.evidenceVersion, 'Evidence version'),
    });
  }

  registerAccessSurface(input) {
    const surface = this.createAccessSurface(input);
    if(this.surfaces.has(surface.surfaceId)) {
      throw new PlatformRegistryError(
        'duplicate',
        `Access surface already registered: ${surface.surfaceId}`,
        surface.surfaceId
      );
    }
    this.surfaces.set(surface.surfaceId, surface);
    const ids = this.surfaceIdsByCanonical.get(surface.canonicalResourceRef) ??
      new Set();
    ids.add(surface.surfaceId);
    this.surfaceIdsByCanonical.set(surface.canonicalResourceRef, ids);
    return () => this.removeAccessSurface(surface.surfaceId);
  }

  removeAccessSurface(surfaceId) {
    const surface = this.surfaces.get(surfaceId);
    if(!surface) return false;
    this.surfaces.delete(surfaceId);
    const ids = this.surfaceIdsByCanonical.get(surface.canonicalResourceRef);
    ids?.delete(surfaceId);
    if(ids?.size === 0) this.surfaceIdsByCanonical.delete(
      surface.canonicalResourceRef
    );
    return true;
  }

  accessSurface(surfaceId, authorization={}) {
    const surface = this.surfaces.get(platformValue(
      surfaceId,
      'Access-surface ID'
    ));
    if(!surface) throw new PlatformRegistryError(
      'not_found',
      `Unknown access surface: ${surfaceId}`,
      surfaceId
    );
    if(!this.canDiscoverSurface(surface, authorization)) {
      throw new PlatformRegistryError(
        'not_found',
        'Access surface is not discoverable.',
        surfaceId
      );
    }
    return surface;
  }

  accessSurfaces(reference, authorization={}) {
    const canonical = typeof reference === 'string' ? reference :
      reference?.canonical;
    if(!canonical) throw new TypeError('Canonical resource reference is required.');
    const ids = this.surfaceIdsByCanonical.get(canonical) ?? new Set();
    return immutable([...ids].map((id) => this.surfaces.get(id)).filter((surface) =>
      this.canDiscoverSurface(surface, authorization)
    ).sort((left, right) => left.surfaceId.localeCompare(right.surfaceId)));
  }

  authorizedAliases(reference, authorization={}) {
    return immutable(this.accessSurfaces(reference, authorization).filter((surface) =>
      surface.visibleQualifiedName
    ).map((surface) => immutable({
      surfaceId: surface.surfaceId,
      kind: surface.kind,
      dialectId: surface.dialectId,
      visibleQualifiedName: surface.visibleQualifiedName,
      workareaSchemaRef: surface.workareaSchemaRef,
      crossSurfaceVisibility: surface.crossSurfaceVisibility,
    })));
  }
}

export class CredentialReferenceService {
  constructor({authorize=() => true}={}) {
    this.authorize = authorize; this.resolvers = new Map();
  }

  register(scheme, resolver) {
    scheme = stablePlatformId(scheme, 'Credential scheme');
    if(typeof resolver !== 'function') throw new TypeError('Credential resolver is required.');
    if(this.resolvers.has(scheme)) throw new PlatformRegistryError(
      'duplicate', `Credential resolver already registered: ${scheme}`, scheme
    );
    this.resolvers.set(scheme, resolver); return () => this.resolvers.delete(scheme);
  }

  reference(scheme, id) {
    scheme = stablePlatformId(scheme, 'Credential scheme');
    id = platformValue(id, 'Credential reference ID');
    return immutable({schema: 'cdeadmin.credential-ref.v1', scheme, id,
      canonical: `cde-secret://${encodeURIComponent(scheme)}/${encodeURIComponent(id)}`});
  }

  async resolve(reference, authorization={}) {
    if(!reference || reference.schema !== 'cdeadmin.credential-ref.v1') {
      throw new TypeError('CredentialRef is invalid.');
    }
    if(!this.authorize(reference, authorization)) throw new PlatformRegistryError(
      'credential_forbidden', 'Credential access is not authorized.', reference.id
    );
    const resolver = this.resolvers.get(reference.scheme);
    if(!resolver) throw new PlatformRegistryError(
      'resolver_unavailable', `Credential resolver unavailable: ${reference.scheme}`,
      reference.scheme
    );
    return resolver(reference.id, authorization);
  }
}

export class LayoutPersistenceService {
  constructor(storage=typeof window === 'undefined' ? null : window.localStorage,
    prefix='cdeadmin.layout.') {
    this.storage = storage; this.prefix = prefix; this.migrations = new Map();
  }

  registerMigration(layoutType, fromVersion, migrate) {
    const key = `${stablePlatformId(layoutType, 'Layout type')}:${Number(fromVersion)}`;
    if(typeof migrate !== 'function') throw new TypeError('Layout migration is required.');
    if(this.migrations.has(key)) throw new PlatformRegistryError(
      'duplicate', `Layout migration already exists: ${key}`, key
    );
    this.migrations.set(key, migrate); return () => this.migrations.delete(key);
  }

  save(layoutType, ownerScope, version, value) {
    layoutType = stablePlatformId(layoutType, 'Layout type');
    ownerScope = platformValue(ownerScope, 'Layout owner scope');
    noRawSecrets(value, 'layout');
    const record = immutable({schema: 'cdeadmin.layout.v1', layoutType, ownerScope,
      version: Number(version), value});
    this.storage?.setItem(`${this.prefix}${layoutType}.${ownerScope}`, JSON.stringify(record));
    return record;
  }

  load(layoutType, ownerScope, targetVersion, fallback) {
    layoutType = stablePlatformId(layoutType, 'Layout type');
    ownerScope = platformValue(ownerScope, 'Layout owner scope');
    try {
      const raw = this.storage?.getItem(`${this.prefix}${layoutType}.${ownerScope}`);
      if(!raw) return fallback;
      let record = JSON.parse(raw);
      if(record.schema !== 'cdeadmin.layout.v1' || record.layoutType !== layoutType ||
          record.ownerScope !== ownerScope || !Number.isSafeInteger(record.version)) return fallback;
      while(record.version < targetVersion) {
        const migrate = this.migrations.get(`${layoutType}:${record.version}`);
        if(!migrate) return fallback;
        record = {...record, version: record.version + 1, value: migrate(record.value)};
      }
      return record.version === targetVersion ? immutable(record.value) : fallback;
    } catch { return fallback; }
  }

  remove(layoutType, ownerScope) {
    this.storage?.removeItem(`${this.prefix}${layoutType}.${ownerScope}`);
  }
}
