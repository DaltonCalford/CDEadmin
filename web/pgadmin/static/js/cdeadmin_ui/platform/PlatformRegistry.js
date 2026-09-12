/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

const STABLE_ID = /^[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*$/;

export class PlatformRegistryError extends Error {
  constructor(code, message, id='') {
    super(message);
    this.name = 'PlatformRegistryError';
    this.code = code;
    this.id = id;
  }
}

export function stablePlatformId(value, label='Platform ID') {
  const id = String(value ?? '').trim();
  if(!STABLE_ID.test(id)) throw new TypeError(`${label} is invalid.`);
  return id;
}

function version(value) {
  const normalized = String(value ?? '1.0.0').trim();
  if(!/^\d+\.\d+\.\d+(?:[-+][a-z0-9.-]+)?$/i.test(normalized)) {
    throw new TypeError('Registry version must use semantic versioning.');
  }
  return normalized;
}

function stringArray(value) {
  if(value === undefined || value === null) return Object.freeze([]);
  if(!Array.isArray(value)) throw new TypeError('Registry dependencies must be an array.');
  return Object.freeze(value.map((item) => stablePlatformId(item)));
}

export class ServiceRegistry {
  constructor() {
    this.definitions = new Map();
    this.instances = new Map();
    this.activationOrder = [];
  }

  register(input) {
    const id = stablePlatformId(input?.id, 'Service ID');
    if(this.definitions.has(id)) {
      throw new PlatformRegistryError('duplicate', `Service already registered: ${id}`, id);
    }
    if(typeof input?.factory !== 'function') {
      throw new TypeError(`Service ${id} requires a factory.`);
    }
    const descriptor = Object.freeze({
      id, version: version(input.version), dependencies: stringArray(input.dependencies),
      factory: input.factory,
    });
    this.definitions.set(id, descriptor);
    return () => this.unregister(id);
  }

  unregister(id) {
    id = stablePlatformId(id, 'Service ID');
    if(this.instances.has(id)) {
      throw new PlatformRegistryError(
        'active', `Active service cannot be unregistered: ${id}`, id
      );
    }
    return this.definitions.delete(id);
  }

  has(id) { return this.definitions.has(id); }

  descriptor(id) {
    const result = this.definitions.get(id);
    if(!result) throw new PlatformRegistryError(
      'not_found', `Unknown platform service: ${id}`, id
    );
    return result;
  }

  async resolve(id, context={}) {
    id = stablePlatformId(id, 'Service ID');
    if(this.instances.has(id)) return this.instances.get(id);
    return this._activate(id, context, []);
  }

  async _activate(id, context, stack) {
    if(this.instances.has(id)) return this.instances.get(id);
    if(stack.includes(id)) {
      throw new PlatformRegistryError(
        'dependency_cycle', `Service dependency cycle: ${[...stack, id].join(' -> ')}`, id
      );
    }
    const descriptor = this.descriptor(id);
    const dependencies = {};
    for(const dependency of descriptor.dependencies) {
      dependencies[dependency] = await this._activate(
        dependency, context, [...stack, id]
      );
    }
    let instance;
    try {
      instance = await descriptor.factory(Object.freeze({
        context, services: Object.freeze(dependencies), registry: this,
      }));
    } catch(error) {
      throw new PlatformRegistryError(
        'activation_failed', `Service ${id} failed to activate: ${error.message}`, id
      );
    }
    if(instance === undefined || instance === null) {
      throw new PlatformRegistryError(
        'activation_failed', `Service ${id} returned no implementation.`, id
      );
    }
    this.instances.set(id, instance);
    this.activationOrder.push(id);
    return instance;
  }

  active(id) { return this.instances.get(id) ?? null; }

  async deactivate(id) {
    id = stablePlatformId(id, 'Service ID');
    const instance = this.instances.get(id);
    if(!instance) return false;
    const dependents = [...this.instances.keys()].filter((candidate) =>
      this.definitions.get(candidate)?.dependencies.includes(id)
    );
    if(dependents.length) throw new PlatformRegistryError(
      'dependency_active', `${id} is required by: ${dependents.join(', ')}`, id
    );
    await instance.dispose?.();
    this.instances.delete(id);
    this.activationOrder = this.activationOrder.filter((item) => item !== id);
    return true;
  }

  async dispose() {
    for(const id of [...this.activationOrder].reverse()) {
      await this.instances.get(id)?.dispose?.();
      this.instances.delete(id);
    }
    this.activationOrder = [];
  }
}

export const CAPABILITY_STATES = Object.freeze({
  AVAILABLE: 'available',
  UNAVAILABLE: 'unavailable',
  UNKNOWN: 'unknown',
});

export class CapabilityRegistry {
  constructor() { this.scopes = new Map(); }

  replace(scopeId, capabilities, evidence={}) {
    scopeId = stablePlatformId(scopeId, 'Capability scope ID');
    if(!capabilities || Array.isArray(capabilities) || typeof capabilities !== 'object') {
      throw new TypeError('Capabilities must be a keyed object.');
    }
    const values = new Map();
    Object.entries(capabilities).forEach(([id, input]) => {
      id = stablePlatformId(id, 'Capability ID');
      const candidate = typeof input === 'boolean' ? {
        state: input ? CAPABILITY_STATES.AVAILABLE : CAPABILITY_STATES.UNAVAILABLE,
      } : input;
      if(!candidate || !Object.values(CAPABILITY_STATES).includes(candidate.state)) {
        throw new TypeError(`Capability ${id} has an invalid state.`);
      }
      values.set(id, Object.freeze({
        id, state: candidate.state, version: String(candidate.version ?? '1'),
        reason: String(candidate.reason ?? ''),
        evidence: Object.freeze({...evidence, ...(candidate.evidence ?? {})}),
      }));
    });
    this.scopes.set(scopeId, values);
    return () => this.scopes.delete(scopeId);
  }

  resolve(scopeId, capabilityId) {
    const value = this.scopes.get(scopeId)?.get(capabilityId);
    return value ?? Object.freeze({
      id: capabilityId, state: CAPABILITY_STATES.UNKNOWN, version: '0',
      reason: 'capability_not_reported', evidence: Object.freeze({}),
    });
  }

  require(scopeId, requirements=[]) {
    const missing = requirements.map((id) => this.resolve(scopeId, id))
      .filter((item) => item.state !== CAPABILITY_STATES.AVAILABLE);
    if(missing.length) throw new PlatformRegistryError(
      'capability_unavailable',
      `Required capabilities unavailable: ${missing.map((item) => item.id).join(', ')}`,
      scopeId
    );
    return true;
  }

  list(scopeId) { return [...(this.scopes.get(scopeId)?.values() ?? [])]; }
}

export class ContributionRegistry {
  constructor(kind) {
    this.kind = stablePlatformId(kind, 'Contribution kind');
    this.items = new Map();
  }

  register(input) {
    const id = stablePlatformId(input?.id, `${this.kind} contribution ID`);
    if(this.items.has(id)) throw new PlatformRegistryError(
      'duplicate', `${this.kind} contribution already registered: ${id}`, id
    );
    const item = Object.freeze({
      ...input, id, moduleId: stablePlatformId(input.moduleId, 'Module ID'),
      priority: Number.isFinite(input.priority) ? input.priority : 100,
      when: typeof input.when === 'function' ? input.when : () => true,
    });
    this.items.set(id, item);
    return () => this.items.delete(id);
  }

  resolve(context={}) {
    return [...this.items.values()].filter((item) => item.when(context))
      .sort((left, right) => left.priority - right.priority ||
        left.id.localeCompare(right.id));
  }
}

export class PlatformEventService {
  constructor({authorize=() => true}={}) {
    this.authorize = authorize;
    this.listeners = new Map();
    this.sequence = 0;
  }

  subscribe(type, listener) {
    type = stablePlatformId(type, 'Event type');
    if(typeof listener !== 'function') throw new TypeError('Event listener is required.');
    const listeners = this.listeners.get(type) ?? new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
    return () => listeners.delete(listener);
  }

  async publish(type, payload={}, context={}) {
    type = stablePlatformId(type, 'Event type');
    if(!this.authorize(type, payload, context)) throw new PlatformRegistryError(
      'event_forbidden', `Event publication is not authorized: ${type}`, type
    );
    const event = Object.freeze({
      schema: 'cdeadmin.platform-event.v1', sequence: ++this.sequence,
      type, payload: Object.freeze({...payload}), origin: String(context.origin ?? ''),
    });
    for(const listener of this.listeners.get(type) ?? []) await listener(event);
    for(const listener of this.listeners.get('platform.any') ?? []) await listener(event);
    return event;
  }
}

export const DIAGNOSTIC_STATES = Object.freeze({
  OPEN: 'open', ACKNOWLEDGED: 'acknowledged', RESOLVED: 'resolved',
});

export class DiagnosticsService {
  constructor() { this.items = new Map(); this.listeners = new Set(); }

  subscribe(listener) {
    if(typeof listener !== 'function') {
      throw new TypeError('Diagnostic listener is required.');
    }
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  _publish(diagnostic) {
    this.listeners.forEach((listener) => listener(diagnostic));
  }

  report(input) {
    const id = stablePlatformId(input?.id, 'Diagnostic ID');
    const severity = String(input?.severity ?? 'error');
    if(!['info', 'warning', 'error', 'critical'].includes(severity)) {
      throw new TypeError('Diagnostic severity is invalid.');
    }
    const diagnostic = Object.freeze({
      schema: 'cdeadmin.diagnostic.v1', id, severity,
      origin: stablePlatformId(input.origin, 'Diagnostic origin'),
      diagnosticId: id,
      originModule: stablePlatformId(input.origin, 'Diagnostic origin'),
      message: String(input.message ?? ''), code: String(input.code ?? ''),
      resourceRef: input.resourceRef ?? null, assetRef: input.assetRef ?? null,
      taskRef: input.taskRef ?? null, location: input.location ?? null,
      evidenceRefs: [...(input.evidenceRefs ?? [])],
      recommendedActions: [...(input.recommendedActions ?? (
        input.suggestedCommandId ? [input.suggestedCommandId] : []))],
      suggestedCommandId: input.suggestedCommandId ?? null,
      state: DIAGNOSTIC_STATES.OPEN, resolved: false, occurrences: 1,
      firstObserved: input.firstObserved ?? new Date().toISOString(),
      lastObserved: input.lastObserved ?? new Date().toISOString(),
    });
    const prior = this.items.get(id);
    this.items.set(id, prior ? Object.freeze({
      ...diagnostic, occurrences: prior.occurrences + 1,
      firstObserved: prior.firstObserved,
    }) : diagnostic);
    const result = this.items.get(id);
    this._publish(result);
    return result;
  }

  transition(id, state) {
    if(!Object.values(DIAGNOSTIC_STATES).includes(state)) {
      throw new TypeError('Diagnostic state is invalid.');
    }
    const current = this.items.get(id);
    if(!current) throw new PlatformRegistryError(
      'not_found', `Unknown diagnostic: ${id}`, id
    );
    const next = Object.freeze({...current, state,
      resolved: state === DIAGNOSTIC_STATES.RESOLVED,
      lastObserved: new Date().toISOString()});
    this.items.set(id, next);
    this._publish(next);
    return next;
  }

  list({state, severity, origin}={}) {
    return [...this.items.values()].filter((item) =>
      (!state || item.state === state) && (!severity || item.severity === severity) &&
      (!origin || item.origin === origin)
    );
  }
}

export class WorkbenchContextService {
  constructor(initial={}) {
    this.value = Object.freeze({...initial});
    this.listeners = new Set();
  }

  snapshot() { return this.value; }

  replace(value={}) {
    this.value = Object.freeze({...value});
    this.listeners.forEach((listener) => listener(this.value));
    return this.value;
  }

  update(changes={}) {
    return this.replace({...this.value, ...changes});
  }

  subscribe(listener) {
    if(typeof listener !== 'function') {
      throw new TypeError('Workbench context listener is required.');
    }
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
}

export const serviceRegistry = new ServiceRegistry();
export const capabilityRegistry = new CapabilityRegistry();
export const inspectorRegistry = new ContributionRegistry('inspector');
export const toolboxRegistry = new ContributionRegistry('toolbox');
export const statusRegistry = new ContributionRegistry('status');
export const activityRegistry = new ContributionRegistry('activity');
export const bottomRegistry = new ContributionRegistry('bottom');
export const platformEventService = new PlatformEventService();
export const diagnosticsService = new DiagnosticsService();
export const workbenchContextService = new WorkbenchContextService({
  surfaceId: '', surfaceTitle: '', projectId: '', assetId: '',
  connectionState: 'disconnected', transactionState: 'none',
  persistence: 'clean', validation: [],
});
