/////////////////////////////////////////////////////////////
// CDEadmin connection, operation and metadata authorities.
/////////////////////////////////////////////////////////////

import {stablePlatformId, PlatformRegistryError} from './PlatformRegistry';
import {abortError, immutable, noRawSecrets, plainObject, platformValue} from './serviceUtils';

export const SESSION_STATES = Object.freeze({
  CONNECTING: 'connecting', CONNECTED: 'connected', DISCONNECTED: 'disconnected',
  RECONNECTING: 'reconnecting', FAILED: 'failed',
});

export class ConnectionSessionService {
  constructor({credentialService=null}={}) {
    this.credentialService = credentialService; this.profiles = new Map();
    this.adapters = new Map(); this.sessions = new Map(); this.listeners = new Set();
    this.sequence = 0;
  }

  registerAdapter(providerId, adapter) {
    providerId = stablePlatformId(providerId, 'Provider ID');
    if(typeof adapter?.connect !== 'function') throw new TypeError(
      'Connection adapter requires connect().'
    );
    if(this.adapters.has(providerId)) throw new PlatformRegistryError(
      'duplicate', `Connection adapter already registered: ${providerId}`, providerId
    );
    this.adapters.set(providerId, adapter); return () => this.adapters.delete(providerId);
  }

  saveProfile(input) {
    plainObject(input, 'Connection profile'); noRawSecrets(input, 'profile');
    const id = platformValue(input.id, 'Connection profile ID');
    const profile = immutable({schema: 'cdeadmin.connection-profile.v1', id,
      provider: stablePlatformId(input.provider, 'Provider ID'),
      label: String(input.label ?? id), endpoints: [...(input.endpoints ?? [])],
      credentialRef: input.credentialRef ?? null, defaults: {...(input.defaults ?? {})},
      routing: {...(input.routing ?? {})}, readOnly: input.readOnly === true,
      environment: String(input.environment ?? 'unknown')});
    if(!profile.endpoints.length) throw new TypeError('Connection profile needs an endpoint.');
    this.profiles.set(id, profile); return profile;
  }

  removeProfile(id) {
    if([...this.sessions.values()].some((session) => session.profileId === id &&
      session.state !== SESSION_STATES.DISCONNECTED)) throw new PlatformRegistryError(
      'profile_in_use', `Profile is in use: ${id}`, id
    );
    return this.profiles.delete(id);
  }

  profile(id) {
    const value = this.profiles.get(String(id));
    if(!value) throw new PlatformRegistryError('not_found', `Unknown profile: ${id}`, id);
    return value;
  }

  listProfiles({provider}={}) {
    return [...this.profiles.values()].filter((item) => !provider || item.provider === provider);
  }

  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  _publish(session) { this.listeners.forEach((listener) => listener(session)); }

  async connect(profileId, options={}) {
    noRawSecrets(options, 'connection options');
    const profile = this.profile(profileId); const adapter = this.adapters.get(profile.provider);
    if(!adapter) throw new PlatformRegistryError(
      'adapter_unavailable', `No connection adapter for ${profile.provider}`, profile.provider
    );
    const id = options.sessionId ?? `session-${++this.sequence}`;
    let session = immutable({id, profileId, provider: profile.provider,
      state: SESSION_STATES.CONNECTING, context: {...profile.defaults},
      transaction: {state: 'none', id: null, isolation: null, readOnly: profile.readOnly},
      warning: '', native: {}, handle: null});
    this.sessions.set(id, session); this._publish(session);
    try {
      const credential = profile.credentialRef ?
        await this.credentialService?.resolve(profile.credentialRef, options.authorization) : null;
      const native = await adapter.connect(profile, {credential, signal: options.signal,
        connectAs: options.connectAs, context: options.context});
      session = immutable({...session, state: SESSION_STATES.CONNECTED,
        native: native?.details ?? {}, handle: native?.handle ?? native,
        warning: String(native?.warning ?? '')});
      this.sessions.set(id, session); this._publish(session); return session;
    } catch(error) {
      session = immutable({...session, state: SESSION_STATES.FAILED,
        warning: error.message, handle: null});
      this.sessions.set(id, session); this._publish(session); throw error;
    }
  }

  session(id) {
    const value = this.sessions.get(String(id));
    if(!value) throw new PlatformRegistryError('not_found', `Unknown session: ${id}`, id);
    return value;
  }

  listSessions({profileId, state}={}) {
    return [...this.sessions.values()].filter((item) =>
      (!profileId || item.profileId === profileId) && (!state || item.state === state)
    );
  }

  updateContext(id, context) {
    noRawSecrets(context, 'session context'); const current = this.session(id);
    const next = immutable({...current, context: {...current.context, ...context}});
    this.sessions.set(id, next); this._publish(next); return next;
  }

  updateTransaction(id, transaction) {
    const current = this.session(id);
    const next = immutable({...current, transaction: {...current.transaction, ...transaction}});
    this.sessions.set(id, next); this._publish(next); return next;
  }

  async disconnect(id) {
    const current = this.session(id); const adapter = this.adapters.get(current.provider);
    await adapter?.disconnect?.(current.handle, current);
    const next = immutable({...current, state: SESSION_STATES.DISCONNECTED,
      handle: null, transaction: {...current.transaction, state: 'none', id: null}});
    this.sessions.set(id, next); this._publish(next); return next;
  }

  async reconnect(id, options={}) {
    const current = this.session(id);
    const reconnecting = immutable({...current, state: SESSION_STATES.RECONNECTING});
    this.sessions.set(id, reconnecting); this._publish(reconnecting);
    await this.disconnect(id); return this.connect(current.profileId, {...options, sessionId: id});
  }
}

export const RESULT_KINDS = Object.freeze([
  'tabular', 'document', 'graph', 'scalar', 'binary-reference', 'trace',
  'plan', 'metric', 'provider-native',
]);

export class NativeOperationService {
  constructor() { this.executors = new Map(); this.operations = new Map(); this.sequence = 0; }

  register(providerId, operationType, executor) {
    const key = `${stablePlatformId(providerId, 'Provider ID')}:${stablePlatformId(
      operationType, 'Operation type'
    )}`;
    if(typeof executor !== 'function') throw new TypeError('Operation executor is required.');
    if(this.executors.has(key)) throw new PlatformRegistryError(
      'duplicate', `Operation executor already registered: ${key}`, key
    );
    this.executors.set(key, executor); return () => this.executors.delete(key);
  }

  async execute(input, context={}) {
    plainObject(input, 'Operation request'); noRawSecrets(input, 'operation');
    const provider = stablePlatformId(input.provider, 'Provider ID');
    const type = stablePlatformId(input.type, 'Operation type');
    const executor = this.executors.get(`${provider}:${type}`);
    if(!executor) throw new PlatformRegistryError(
      'operation_unavailable', `${provider} does not provide ${type}`, `${provider}:${type}`
    );
    const id = input.id ?? `operation-${++this.sequence}`; const controller = new AbortController();
    if(context.signal) context.signal.aborted ? controller.abort() :
      context.signal.addEventListener('abort', () => controller.abort(), {once: true});
    const operation = {id, state: 'running', controller, result: null, error: null};
    this.operations.set(id, operation);
    try {
      const produced = await executor(immutable({...input, provider, type}),
        {...context, signal: controller.signal});
      const chunks = [];
      if(produced?.[Symbol.asyncIterator]) {
        for await (const chunk of produced) {
          if(controller.signal.aborted) throw abortError();
          chunks.push(this._result(chunk)); context.onChunk?.(chunks[chunks.length - 1]);
        }
      } else chunks.push(this._result(produced));
      operation.state = 'completed'; operation.result = chunks.length === 1 ? chunks[0] :
        immutable({schema: 'cdeadmin.operation-result-stream.v1', chunks});
      return immutable({id, state: operation.state, result: operation.result});
    } catch(error) {
      operation.state = controller.signal.aborted ? 'cancelled' : 'failed';
      operation.error = error; throw error;
    }
  }

  _result(value) {
    if(!value || !RESULT_KINDS.includes(value.kind)) throw new TypeError(
      `Operation result kind must be one of: ${RESULT_KINDS.join(', ')}.`
    );
    return immutable({schema: 'cdeadmin.operation-result.v1', ...value});
  }

  cancel(id) {
    const operation = this.operations.get(id);
    if(!operation || operation.state !== 'running') return false;
    operation.controller.abort(); return true;
  }

  state(id) {
    const operation = this.operations.get(id);
    if(!operation) throw new PlatformRegistryError('not_found', `Unknown operation: ${id}`, id);
    return immutable({id, state: operation.state, result: operation.result,
      error: operation.error?.message ?? ''});
  }
}

export class MetadataCatalogService {
  constructor() { this.adapters = new Map(); this.cache = new Map(); }

  register(providerId, adapter) {
    providerId = stablePlatformId(providerId, 'Provider ID');
    if(typeof adapter?.read !== 'function') throw new TypeError('Metadata adapter requires read().');
    if(this.adapters.has(providerId)) throw new PlatformRegistryError(
      'duplicate', `Metadata adapter already registered: ${providerId}`, providerId
    );
    this.adapters.set(providerId, adapter); return () => this.adapters.delete(providerId);
  }

  async read(reference, {refresh=false, signal}={}) {
    const key = `${reference.canonical}@${reference.revision ?? ''}`;
    if(!refresh && this.cache.has(key)) return this.cache.get(key);
    const adapter = this.adapters.get(reference.provider);
    if(!adapter) throw new PlatformRegistryError(
      'adapter_unavailable', `No metadata adapter for ${reference.provider}`, reference.provider
    );
    const value = await adapter.read(reference, {signal});
    if(!value?.normalized || !value?.native) throw new TypeError(
      'Metadata result requires normalized and native views.'
    );
    const result = immutable({schema: 'cdeadmin.metadata.v1', reference,
      normalized: value.normalized, native: value.native,
      provenance: value.provenance ?? {}, refreshedAt: value.refreshedAt ?? new Date().toISOString(),
      version: String(value.version ?? reference.revision ?? '')});
    this.cache.set(key, result); return result;
  }

  invalidate(reference=null) {
    if(!reference) { const count = this.cache.size; this.cache.clear(); return count; }
    let count = 0;
    for(const key of this.cache.keys()) if(key.startsWith(`${reference.canonical}@`)) {
      this.cache.delete(key); count++;
    }
    return count;
  }
}
