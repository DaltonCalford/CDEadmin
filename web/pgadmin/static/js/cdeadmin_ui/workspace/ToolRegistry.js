/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {createToolDescriptor, TOOL_DESCRIPTOR_SCHEMA} from './ToolDescriptor';

export class ToolFactoryRegistry {
  constructor() {
    this.factories = new Map();
  }

  register(input) {
    const toolKind = String(input?.toolKind ?? '').trim();
    if(!toolKind) throw new TypeError('Tool factory kind is required.');
    if(typeof input.restore !== 'function') {
      throw new TypeError(`Tool factory ${toolKind} requires a restore handler.`);
    }
    if(this.factories.has(toolKind)) {
      throw new Error(`Tool factory is already registered: ${toolKind}`);
    }
    const factory = Object.freeze({
      toolKind,
      descriptorSchema: input.descriptorSchema ?? TOOL_DESCRIPTOR_SCHEMA,
      detachable: Boolean(input.detachable),
      duplicable: Boolean(input.duplicable),
      requiresLiveSession: Boolean(input.requiresLiveSession),
      restore: input.restore,
      checkpoint: typeof input.checkpoint === 'function' ?
        input.checkpoint : async (descriptor)=>descriptor,
      canClose: typeof input.canClose === 'function' ?
        input.canClose : async ()=>({allowed: true, reason: ''}),
      migrate: typeof input.migrate === 'function' ? input.migrate : null,
    });
    this.factories.set(toolKind, factory);
    return ()=>this.factories.delete(toolKind);
  }

  has(toolKind) {
    return this.factories.has(toolKind);
  }

  get(toolKind) {
    const factory = this.factories.get(toolKind);
    if(!factory) throw new Error(`No tool factory is registered for ${toolKind}.`);
    return factory;
  }

  capabilities(toolKind) {
    const factory = this.get(toolKind);
    return Object.freeze({
      detachable: factory.detachable,
      duplicable: factory.duplicable,
      requiresLiveSession: factory.requiresLiveSession,
    });
  }

  async checkpoint(descriptor, context={}) {
    const normalized = createToolDescriptor(descriptor);
    return this.get(normalized.toolKind).checkpoint(normalized, context);
  }

  restore(descriptor, context={}) {
    const normalized = createToolDescriptor(descriptor);
    const factory = this.get(normalized.toolKind);
    if(factory.descriptorSchema !== normalized.schema) {
      if(!factory.migrate) {
        throw new Error(`Tool ${normalized.toolKind} cannot restore ${normalized.schema}.`);
      }
      return factory.restore(factory.migrate(normalized), context);
    }
    return factory.restore(normalized, context);
  }

  async canClose(descriptor, context={}) {
    const normalized = createToolDescriptor(descriptor);
    const result = await this.get(normalized.toolKind).canClose(
      normalized, context
    );
    return Object.freeze({
      allowed: result?.allowed !== false,
      reason: String(result?.reason ?? ''),
    });
  }
}

export const toolFactoryRegistry = new ToolFactoryRegistry();
