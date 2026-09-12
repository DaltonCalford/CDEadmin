/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {commandRegistry} from '../commands/CommandRegistry';
import {
  activityRegistry, bottomRegistry, capabilityRegistry, inspectorRegistry, serviceRegistry,
  stablePlatformId, statusRegistry, toolboxRegistry, PlatformRegistryError,
} from './PlatformRegistry';
import {surfaceRegistry} from './SurfaceRegistry';

export const MODULE_STATES = Object.freeze({
  REGISTERED: 'registered', ACTIVATING: 'activating', ACTIVE: 'active',
  FAILED: 'failed', INACTIVE: 'inactive',
});

function moduleVersion(value) {
  const normalized = String(value ?? '1.0.0').trim();
  if(!/^\d+\.\d+\.\d+(?:[-+][a-z0-9.-]+)?$/i.test(normalized)) {
    throw new TypeError('Module version must use semantic versioning.');
  }
  return normalized;
}

function requirements(values, label) {
  if(values === undefined) return Object.freeze([]);
  if(!Array.isArray(values)) throw new TypeError(`${label} must be an array.`);
  return Object.freeze(values.map((value) => stablePlatformId(value, label)));
}

export class ModuleRegistry {
  constructor(registries={}) {
    this.services = registries.services ?? serviceRegistry;
    this.capabilities = registries.capabilities ?? capabilityRegistry;
    this.surfaces = registries.surfaces ?? surfaceRegistry;
    this.commands = registries.commands ?? commandRegistry;
    this.inspector = registries.inspector ?? inspectorRegistry;
    this.toolbox = registries.toolbox ?? toolboxRegistry;
    this.status = registries.status ?? statusRegistry;
    this.activity = registries.activity ?? activityRegistry;
    this.bottom = registries.bottom ?? bottomRegistry;
    this.modules = new Map();
  }

  register(input) {
    const id = stablePlatformId(input?.id, 'Module ID');
    if(this.modules.has(id)) throw new PlatformRegistryError(
      'duplicate', `Module already registered: ${id}`, id
    );
    const descriptor = Object.freeze({
      id, version: moduleVersion(input.version),
      title: String(input.title ?? id), iconKey: String(input.iconKey ?? 'command.default'),
      serviceRequirements: requirements(input.serviceRequirements, 'Service requirement'),
      capabilityRequirements: requirements(input.capabilityRequirements,
        'Capability requirement'),
      contributions: Object.freeze({...input.contributions}),
      activate: typeof input.activate === 'function' ? input.activate : async () => {},
      deactivate: typeof input.deactivate === 'function' ? input.deactivate : async () => {},
    });
    this.modules.set(id, {descriptor, state: MODULE_STATES.REGISTERED,
      disposers: [], error: null, runtime: null});
    return () => this.unregister(id);
  }

  unregister(id) {
    const record = this._record(id);
    if(record.state === MODULE_STATES.ACTIVE) throw new PlatformRegistryError(
      'active', `Active module cannot be unregistered: ${id}`, id
    );
    return this.modules.delete(id);
  }

  _record(id) {
    const record = this.modules.get(id);
    if(!record) throw new PlatformRegistryError(
      'not_found', `Unknown module: ${id}`, id
    );
    return record;
  }

  has(id) { return this.modules.has(id); }

  state(id) {
    const record = this._record(id);
    return Object.freeze({
      id: record.descriptor.id, state: record.state, error: record.error,
      version: record.descriptor.version, title: record.descriptor.title,
    });
  }

  list() { return [...this.modules.keys()].map((id) => this.state(id)); }

  async activate(id, context={}) {
    const record = this._record(id);
    if(record.state === MODULE_STATES.ACTIVE) return record.runtime;
    if(record.state === MODULE_STATES.ACTIVATING) throw new PlatformRegistryError(
      'activation_cycle', `Module is already activating: ${id}`, id
    );
    record.state = MODULE_STATES.ACTIVATING;
    record.error = null;
    const disposers = [];
    try {
      const services = {};
      for(const serviceId of record.descriptor.serviceRequirements) {
        services[serviceId] = await this.services.resolve(serviceId, context);
      }
      const scopeId = context.capabilityScopeId;
      if(record.descriptor.capabilityRequirements.length) {
        if(!scopeId) throw new PlatformRegistryError(
          'capability_scope_required', `Module ${id} requires a capability scope.`, id
        );
        this.capabilities.require(scopeId, record.descriptor.capabilityRequirements);
      }
      const contributions = record.descriptor.contributions;
      for(const surface of contributions.surfaces ?? []) {
        disposers.push(this.surfaces.register({...surface, moduleId: id}));
      }
      for(const command of contributions.commands ?? []) {
        disposers.push(this.commands.register(command));
      }
      for(const [registry, items] of [
        [this.inspector, contributions.inspector],
        [this.toolbox, contributions.toolbox],
        [this.status, contributions.status],
        [this.activity, contributions.activity],
        [this.bottom, contributions.bottom],
      ]) for(const item of items ?? []) {
        disposers.push(registry.register({...item, moduleId: id}));
      }
      const runtime = await record.descriptor.activate(Object.freeze({
        module: record.descriptor, services: Object.freeze(services), context,
      }));
      record.disposers = disposers;
      record.runtime = runtime ?? Object.freeze({});
      record.state = MODULE_STATES.ACTIVE;
      return record.runtime;
    } catch(error) {
      for(const dispose of disposers.reverse()) dispose();
      record.disposers = [];
      record.runtime = null;
      record.error = error;
      record.state = MODULE_STATES.FAILED;
      throw error;
    }
  }

  async deactivate(id, context={}) {
    const record = this._record(id);
    if(record.state !== MODULE_STATES.ACTIVE) {
      record.state = MODULE_STATES.INACTIVE;
      return false;
    }
    await record.descriptor.deactivate(Object.freeze({
      module: record.descriptor, runtime: record.runtime, context,
    }));
    for(const dispose of [...record.disposers].reverse()) dispose();
    record.disposers = [];
    record.runtime = null;
    record.state = MODULE_STATES.INACTIVE;
    return true;
  }
}

export const moduleRegistry = new ModuleRegistry();
