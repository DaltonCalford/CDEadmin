import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {PlatformRegistryError, stablePlatformId} from '../../platform/PlatformRegistry';
import {AI_CONNECTOR_TYPES, AI_DISCOVERY_STATE_MACHINES} from '../../specifications/ai_discovery_zero_grey';

export const AI_CONNECTOR_SERVICE_ID = 'cdeadmin.ai_interface.connectors';
export const AI_CONNECTOR_STATES = AI_DISCOVERY_STATE_MACHINES.ai_connector;
export const AI_CONNECTOR_CLASSES = Object.freeze(AI_CONNECTOR_TYPES.types.map((item) => item.id));
const METHODS = ['describe', 'validateConfiguration', 'authenticateOrResolveIdentity',
  'discoverCapabilities', 'test', 'prepare', 'execute', 'cancel', 'health', 'close'];
const DATABASE = new Set(['database_provider', 'scratchbird_sbsql', 'scratchbird_compatibility']);
const FIELDS = new Set(['schemaVersion', 'connectorId', 'name', 'connectorClass', 'enabled',
  'providerId', 'connectionProfileRef', 'dialectId', 'workareaSchemaRef', 'mcpEndpointRef',
  'mcpProtocolProfile', 'principalBinding', 'credentialRef', 'policyRef', 'resourceScopeRefs',
  'state', 'capabilitySnapshotRef']);
const optional = (value, label) => value == null ? null : platformValue(value, label);
function list(value, label) { if(!Array.isArray(value)) throw new TypeError(`${label} must be an array.`);
  const result = value.map((item) => platformValue(item, label)); if(new Set(result).size !== result.length)
    throw new TypeError(`${label} contains duplicates.`); return result; }

export function createAIConnectorProfile(input) {
  plainObject(input, 'AI connector profile'); noRawSecrets(input, 'AI connector profile');
  const unknown = Object.keys(input).filter((field) => !FIELDS.has(field)); if(unknown.length)
    throw new TypeError(`AI connector profile contains unsupported field ${unknown[0]}.`);
  if(input.schemaVersion !== 1) throw new TypeError('AI connector profile schema version is invalid.');
  if(!AI_CONNECTOR_CLASSES.includes(input.connectorClass)) throw new TypeError('AI connector class is invalid.');
  if(typeof input.enabled !== 'boolean') throw new TypeError('AI connector enabled state must be boolean.');
  if(!AI_CONNECTOR_STATES.includes(input.state)) throw new TypeError('AI connector state is invalid.');
  const value = immutable({schemaVersion: 1, connectorId: stablePlatformId(input.connectorId,
    'AI connector ID'), name: platformValue(input.name, 'AI connector name'),
  connectorClass: input.connectorClass, enabled: input.enabled, providerId: optional(input.providerId,
    'AI connector provider'), connectionProfileRef: optional(input.connectionProfileRef,
    'AI connection profile'), dialectId: optional(input.dialectId, 'AI connector dialect'),
  workareaSchemaRef: optional(input.workareaSchemaRef, 'AI connector workarea'),
  mcpEndpointRef: optional(input.mcpEndpointRef, 'AI MCP endpoint'),
  mcpProtocolProfile: optional(input.mcpProtocolProfile, 'AI MCP protocol profile'),
  principalBinding: platformValue(input.principalBinding, 'AI principal binding'),
  credentialRef: optional(input.credentialRef, 'AI credential reference'),
  policyRef: platformValue(input.policyRef, 'AI policy reference'),
  resourceScopeRefs: list(input.resourceScopeRefs ?? [], 'AI resource scopes'), state: input.state,
  capabilitySnapshotRef: optional(input.capabilitySnapshotRef, 'AI capability snapshot')});
  if(DATABASE.has(value.connectorClass) && (!value.providerId || !value.connectionProfileRef ||
      !value.credentialRef)) throw new TypeError('Database AI connectors require provider, connection and credential references.');
  if(value.connectorClass === 'scratchbird_sbsql' && value.dialectId !== 'sbsql')
    throw new TypeError('ScratchBird native connectors require the SBsql dialect.');
  if(value.connectorClass === 'scratchbird_compatibility' && (!value.dialectId || !value.workareaSchemaRef))
    throw new TypeError('ScratchBird compatibility connectors require dialect and sandboxed workarea.');
  if(['scratchbird_mcp', 'mcp_generic'].includes(value.connectorClass) &&
      (!value.mcpEndpointRef || !value.mcpProtocolProfile)) throw new TypeError(
    'MCP connectors require endpoint and protocol profile references.'); return value;
}

export class AIConnectorAdapterRegistry {
  constructor() { this.definitions = new Map(); }
  register(input) { plainObject(input, 'AI connector adapter'); const id = stablePlatformId(
    input.connectorClass, 'AI connector class'); if(!AI_CONNECTOR_CLASSES.includes(id))
    throw new TypeError(`Unknown AI connector class: ${id}`); if(typeof input.factory !== 'function')
    throw new TypeError('AI connector adapter factory is required.'); if(this.definitions.has(id))
    throw new PlatformRegistryError('duplicate', `AI connector adapter already registered: ${id}`, id);
  const value = immutable({connectorClass: id, version: platformValue(input.version,
    'AI connector adapter version'), factory: input.factory}); this.definitions.set(id, value);
  return () => this.definitions.delete(id); }
  has(id) { return this.definitions.has(id); }
  list() { return [...this.definitions.values()].sort((a, b) => a.connectorClass.localeCompare(b.connectorClass)); }
  create(profile) { const definition = this.definitions.get(profile.connectorClass); if(!definition) return null;
    const adapter = definition.factory({profile, connectorOwner: `ai-connector:${profile.connectorId}`});
    METHODS.forEach((method) => { if(typeof adapter?.[method] !== 'function') throw new TypeError(
      `AI connector adapter ${profile.connectorClass} requires ${method}().`); });
    return {adapter, version: definition.version}; }
}

function view(record) { return immutable({profile: record.profile, revision: record.revision,
  state: record.state, descriptor: record.descriptor, identity: record.identity,
  capabilities: record.capabilities, health: record.health, diagnostics: [...record.diagnostics],
  preparedCount: record.prepared.size}); }
export class AIConnectorService {
  constructor({adapters=new AIConnectorAdapterRegistry(), credentialResolver=async () => null,
    now=() => new Date().toISOString()}={}) { if(!(adapters instanceof AIConnectorAdapterRegistry))
    throw new TypeError('AI connector adapter registry is invalid.'); if(typeof credentialResolver !== 'function')
    throw new TypeError('AI credential resolver must be callable.'); this.adapters = adapters;
  this.credentialResolver = credentialResolver; this.now = now; this.records = new Map(); }
  create(input) { const profile = createAIConnectorProfile(input); if(this.records.has(profile.connectorId))
    throw new PlatformRegistryError('duplicate', `AI connector already exists: ${profile.connectorId}`,
      profile.connectorId); const state = profile.enabled ? 'unconfigured' : 'disabled'; const record = {
    profile: immutable({...profile, state}), revision: 1, state, adapter: null, descriptor: null,
    identity: null, capabilities: null, health: null, diagnostics: [], prepared: new Map()};
  this.records.set(profile.connectorId, record); return view(record); }
  get(id) { return view(this._record(id)); }
  list() { return [...this.records.values()].map(view).sort((a, b) => a.profile.name.localeCompare(b.profile.name)); }
  async update(id, input, context={}) { const record = this._record(id); if(record.state === 'revoked')
    throw new Error('Revoked connector cannot be updated.'); const profile = createAIConnectorProfile(input);
  if(profile.connectorId !== record.profile.connectorId) throw new TypeError('AI connector ID cannot be changed.');
  await this._close(record); record.profile = profile; record.revision += 1; record.state = profile.enabled ?
    'unconfigured' : 'disabled'; record.profile = immutable({...record.profile, state: record.state});
  record.descriptor = null; record.identity = null; record.capabilities = null; record.health = null;
  record.diagnostics = []; record.prepared.clear(); return profile.enabled ? this.enable(id, context) : view(record); }
  async enable(id, context={}) { const record = this._record(id); if(record.state === 'revoked')
    throw new Error('Revoked connector cannot be enabled.'); await this._close(record); record.prepared.clear();
  record.state = 'validating';
  record.profile = immutable({...record.profile, enabled: true, state: record.state}); record.diagnostics = [];
  const created = this.adapters.create(record.profile); if(!created) return this._fail(record, 'unconfigured',
    `No ${record.profile.connectorClass} adapter is installed.`); record.adapter = created.adapter; try {
    const config = await record.adapter.validateConfiguration(record.profile); this._safe(config, 'configuration');
    if(config.valid !== true) return this._failClosed(record, 'error',
      config.errors?.join(' ') || 'Invalid configuration.');
    const credential = record.profile.credentialRef ? await this.credentialResolver(record.profile.credentialRef,
      {connectorId: id, purpose: 'ai_connector'}) : null; const identity = await record.adapter
      .authenticateOrResolveIdentity({profile: record.profile, credential, connectorOwner: `ai-connector:${id}`});
    this._safe(identity, 'identity'); if(identity.principalId !== record.profile.principalBinding ||
      !identity.sessionId || identity.borrowedInteractiveSession === true ||
      (context.interactiveSessionId && identity.sessionId === context.interactiveSessionId))
      return this._failClosed(record, 'permission_failed',
        'Dedicated AI principal/session was not authenticated.');
    const descriptor = await record.adapter.describe(); const capabilities = await record.adapter.discoverCapabilities();
    const test = await record.adapter.test({destructive: false}); [descriptor, capabilities, test]
      .forEach((item) => this._safe(item, 'validation result')); if(test.success !== true)
      return this._failClosed(record, test.category === 'version' ? 'version_incompatible' : 'unreachable',
        test.message || 'Connector test failed.'); record.descriptor = immutable({...descriptor,
      adapterVersion: created.version}); record.identity = immutable({...identity});
    record.capabilities = immutable({...capabilities}); record.health = await this._health(record);
    record.state = record.health.ready ? 'ready' : 'degraded'; record.profile = immutable({...record.profile,
      state: record.state}); return view(record); } catch(error) {
    return this._failClosed(record, 'error', error.message); } }
  async test(id, context={}) { const record = this._record(id); const wasEnabled = record.profile.enabled;
    const result = await this.enable(id, context); if(!wasEnabled) {
      await this._close(record); record.state = 'disabled'; record.profile = immutable({...record.profile,
        enabled: false, state: record.state}); record.identity = null; record.prepared.clear(); return immutable({
        ...view(record), testResult: result}); } return immutable({...result, testResult: result}); }
  async disable(id) { const r = this._record(id); await this._close(r); r.state = 'disabled';
    r.profile = immutable({...r.profile, enabled: false, state: r.state}); r.identity = null; r.prepared.clear();
    return view(r); }
  async revoke(id) { const r = this._record(id); await this._close(r); r.state = 'revoked';
    r.profile = immutable({...r.profile, enabled: false, state: r.state}); r.identity = null;
    r.capabilities = null; r.prepared.clear(); return view(r); }
  async prepare(id, operation) { const r = this._usable(id); plainObject(operation, 'AI operation');
    noRawSecrets(operation, 'AI operation'); const result = await r.adapter.prepare(immutable({...operation,
      connectorRevision: r.revision})); this._safe(result, 'prepared operation'); if(!result.preparedId ||
      result.sideEffects === true) throw new TypeError('Prepared operation must be identified and side-effect free.');
    const prepared = immutable({...result, connectorId: id, connectorRevision: r.revision,
      preparedAt: this.now()}); r.prepared.set(prepared.preparedId, prepared); return prepared; }
  async execute(id, preparedId, approval=null) { const r = this._usable(id); const op = r.prepared.get(preparedId);
    if(!op) throw new Error(`Unknown prepared operation: ${preparedId}`); this._safe(approval, 'approval evidence');
    const result = await r.adapter.execute(op, approval); this._safe(result, 'execution result');
    r.prepared.delete(preparedId); return immutable(result); }
  async cancel(id, ref) { const result = await this._usable(id).adapter.cancel(platformValue(ref,
    'AI operation reference')); this._safe(result, 'cancellation result'); return immutable(result); }
  async refreshCapabilities(id) { const r = this._usable(id); try { const capabilities = await r.adapter
    .discoverCapabilities(); this._safe(capabilities, 'capabilities'); r.capabilities = immutable({...capabilities});
  r.revision += 1; r.prepared.clear(); if(capabilities.snapshotRef) r.profile = immutable({...r.profile,
    capabilitySnapshotRef: platformValue(capabilities.snapshotRef, 'AI capability snapshot')});
  r.health = await this._health(r); r.state = r.health.ready ? 'ready' : 'degraded';
  r.profile = immutable({...r.profile, state: r.state}); r.diagnostics = []; return view(r); } catch(error) {
    r.prepared.clear(); return this._fail(r, 'degraded', error.message); } }
  async health(id) { const r = this._record(id); if(!r.adapter) return immutable({
    state: r.state, ready: false, dimensions: {}, checkedAt: this.now()}); r.health = await this._health(r);
  if(['ready', 'degraded'].includes(r.state)) { r.state = r.health.ready ? 'ready' : 'degraded';
    r.profile = immutable({...r.profile, state: r.state}); } return r.health; }
  async close() { for(const record of this.records.values()) await this._close(record); }
  _record(id) { id = stablePlatformId(id, 'AI connector ID'); const value = this.records.get(id); if(!value)
    throw new PlatformRegistryError('not_found', `Unknown AI connector: ${id}`, id); return value; }
  _usable(id) { const r = this._record(id); if(!['ready', 'degraded'].includes(r.state) || !r.adapter)
    throw new Error(`AI connector ${id} is not usable (${r.state}).`); return r; }
  _safe(value, label) { if(value != null) { plainObject(value, `AI connector ${label}`);
    noRawSecrets(value, `AI connector ${label}`); } }
  _fail(r, state, message) { r.state = state; r.profile = immutable({...r.profile, state});
    r.diagnostics.push(String(message)); return view(r); }
  async _failClosed(r, state, message) { await this._close(r); r.identity = null;
    r.prepared.clear(); return this._fail(r, state, message); }
  async _health(r) { const value = await r.adapter.health(); this._safe(value, 'health'); const dimensions =
    plainObject(value.dimensions, 'AI connector health dimensions'); const required = ['transport',
    'authentication', 'authorization', 'capability_discovery', 'version_compatibility', 'task_execution'];
  if(DATABASE.has(r.profile.connectorClass)) required.push('database_session_pool'); const ready = required
    .every((name) => dimensions[name] === 'ready'); return immutable({state: ready ? 'ready' : 'degraded',
    ready, dimensions: {...dimensions}, checkedAt: this.now()}); }
  async _close(r) { if(r.adapter) await r.adapter.close(); r.adapter = null; }
}
