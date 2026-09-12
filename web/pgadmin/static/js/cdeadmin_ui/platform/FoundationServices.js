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
  create(input) {
    plainObject(input, 'Resource reference');
    const value = immutable({schema: 'cdeadmin.resource-ref.v1',
      provider: stablePlatformId(input.provider, 'Resource provider'),
      connection: platformValue(input.connection, 'Connection scope'),
      scope: platformValue(input.scope ?? '/', 'Native scope'),
      kind: stablePlatformId(input.kind, 'Resource kind'),
      nativeIdentity: platformValue(input.nativeIdentity, 'Native identity'),
      revision: input.revision === undefined ? null : String(input.revision)});
    return immutable({...value, canonical: this.canonical(value)});
  }

  canonical(reference) {
    const parts = [reference.provider, reference.connection, reference.scope,
      reference.kind, reference.nativeIdentity].map((part) =>
      encodeURIComponent(String(part))
    );
    return `cde-resource://${parts.join('/')}`;
  }

  parse(canonical) {
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
