/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {createToolDescriptor, TOOL_PLACEMENT_MODES} from '../workspace/ToolDescriptor';
import {toolFactoryRegistry} from '../workspace/ToolRegistry';
import {PlatformRegistryError, stablePlatformId} from './PlatformRegistry';

function values(value, label) {
  if(value === undefined) return Object.freeze([]);
  if(!Array.isArray(value)) throw new TypeError(`${label} must be an array.`);
  return Object.freeze(value.map((item) => String(item)));
}

export class SurfaceRegistry {
  constructor(toolRegistry=toolFactoryRegistry) {
    this.toolRegistry = toolRegistry;
    this.surfaces = new Map();
  }

  register(input) {
    const id = stablePlatformId(input?.id, 'Surface ID');
    const toolKind = stablePlatformId(input?.toolKind ?? id, 'Surface tool kind');
    if(this.surfaces.has(id)) throw new PlatformRegistryError(
      'duplicate', `Surface already registered: ${id}`, id
    );
    if(typeof input?.restore !== 'function') {
      throw new TypeError(`Surface ${id} requires a restore function.`);
    }
    const definition = Object.freeze({
      schema: 'cdeadmin.surface-definition.v1', id, toolKind,
      moduleId: stablePlatformId(input.moduleId, 'Module ID'),
      version: Number.isSafeInteger(input.version) && input.version > 0 ? input.version : 1,
      title: String(input.title ?? id), iconKey: String(input.iconKey ?? 'command.default'),
      assetTypes: values(input.assetTypes, 'Surface asset types'),
      resourceKinds: values(input.resourceKinds, 'Surface resource kinds'),
      editable: Boolean(input.editable), readOnly: input.readOnly !== false,
      detachable: input.detachable !== false, duplicable: Boolean(input.duplicable),
      fullscreen: input.fullscreen !== false, presentation: Boolean(input.presentation),
      requiresLiveSession: Boolean(input.requiresLiveSession),
      restore: input.restore,
      checkpoint: input.checkpoint,
      canClose: input.canClose,
      migrate: input.migrate,
      inspectorProvider: input.inspectorProvider ?? null,
      commandProvider: input.commandProvider ?? null,
      toolboxProvider: input.toolboxProvider ?? null,
      statusProvider: input.statusProvider ?? null,
      validationProvider: input.validationProvider ?? null,
    });
    const removeFactory = this.toolRegistry.register({
      toolKind, detachable: definition.detachable,
      duplicable: definition.duplicable,
      requiresLiveSession: definition.requiresLiveSession,
      restore: (descriptor, context) => definition.restore(descriptor, context),
      checkpoint: definition.checkpoint,
      canClose: definition.canClose,
      migrate: definition.migrate,
    });
    this.surfaces.set(id, definition);
    return () => {
      this.surfaces.delete(id);
      removeFactory();
    };
  }

  get(id) {
    const result = this.surfaces.get(id);
    if(!result) throw new PlatformRegistryError(
      'not_found', `Unknown workbench surface: ${id}`, id
    );
    return result;
  }

  list(context={}) {
    return [...this.surfaces.values()].filter((surface) =>
      !context.assetType || surface.assetTypes.includes(context.assetType)
    ).filter((surface) =>
      !context.resourceKind || surface.resourceKinds.includes(context.resourceKind)
    );
  }

  descriptor(surfaceId, input={}) {
    const surface = this.get(surfaceId);
    return createToolDescriptor({
      toolInstanceId: input.toolInstanceId,
      toolKind: surface.toolKind,
      restoreRef: input.restoreRef,
      projectId: input.projectId,
      context: input.context,
      presentation: {
        title: input.title ?? surface.title,
        iconKey: input.iconKey ?? surface.iconKey,
      },
      placement: {
        mode: input.placement?.mode ?? TOOL_PLACEMENT_MODES.DOCKED,
        ...input.placement,
      },
      state: input.state,
      capabilities: {
        detachable: surface.detachable,
        duplicable: surface.duplicable,
        requiresLiveSession: surface.requiresLiveSession,
      },
    });
  }

  restore(descriptor, context={}) {
    return this.toolRegistry.restore(descriptor, context);
  }
}

export const surfaceRegistry = new SurfaceRegistry();
