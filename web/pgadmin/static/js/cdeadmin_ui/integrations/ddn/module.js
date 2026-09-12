/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import React from 'react';
import {ProjectAssetClient} from '../../projects/ProjectAssetClient';
import {DDNDesignerSurface} from './DDNDesignerSurface';
import {DDNViewerSurface} from './DDNViewerSurface';

export const DDN_MODULE_ID = 'cdeadmin.ddn';
export const PROJECT_ASSET_SERVICE_ID = 'project.assets';

function surfaceActionCommand(id, label, description, iconKey) {
  return {
    id, label, description, iconKey, surfaces: ['tools', 'toolbar'],
    macroCallable: false,
    enabledWhen: (context) => context.enabled !== false,
    execute: (_args, context) => {
      if(typeof context.invoke !== 'function') {
        throw new Error(`Command ${id} requires an active DDN surface.`);
      }
      return context.invoke();
    },
  };
}

function surfaceCommandExecutor(context) {
  return (id, commandContext) => {
    if(!context.commands?.execute) {
      throw new Error('Workbench command authority is unavailable.');
    }
    return context.commands.execute(id, {}, commandContext);
  };
}

function assetArguments(args) {
  return args && typeof args.projectId === 'string' &&
    typeof args.assetId === 'string' ? true :
    'A projectId and assetId are required.';
}

function openCommand(surfaceId) {
  return async (args, context) => {
    if(typeof context.openSurface !== 'function') {
      throw new Error('Workbench surface opening authority is unavailable.');
    }
    return context.openSurface(surfaceId, {
      toolInstanceId: args.toolInstanceId ??
        `${surfaceId}:${args.projectId}:${args.assetId}`,
      restoreRef: args.assetId,
      projectId: args.projectId,
      title: args.title,
    });
  };
}

async function loadBoundAsset(descriptor, context) {
  const client = context.services?.[PROJECT_ASSET_SERVICE_ID] ??
    context.projectAssetClient;
  if(!client) throw new Error('Project asset service is unavailable.');
  const asset = await client.asset(descriptor.projectId, descriptor.restoreRef);
  if(asset?.asset_type !== 'ddn-workspace') {
    throw new Error('The requested project asset is not a DDN workspace.');
  }
  return {client, asset};
}

export function ddnModuleDefinition() {
  return {
    id: DDN_MODULE_ID,
    version: '1.0.0',
    title: 'DDN diagrams',
    iconKey: 'tool.erd',
    serviceRequirements: [PROJECT_ASSET_SERVICE_ID],
    contributions: {
      surfaces: [
        {
          id: 'diagram.ddn.viewer', title: 'DDN Viewer',
          iconKey: 'tool.erd', assetTypes: ['ddn-workspace'],
          readOnly: true, editable: false, detachable: true,
          duplicable: true, fullscreen: true, presentation: true,
          restore: async (descriptor, context) => {
            const {asset} = await loadBoundAsset(descriptor, context);
            return React.createElement(DDNViewerSurface, {
              snapshot: asset.content,
              presentation: context.presentation === true,
              executeCommand: surfaceCommandExecutor(context),
              onExport: context.onExport,
              onError: context.onError,
            });
          },
        },
        {
          id: 'diagram.ddn.designer', title: 'DDN Designer',
          iconKey: 'tool.erd', assetTypes: ['ddn-workspace'],
          readOnly: true, editable: true, detachable: true,
          duplicable: false, fullscreen: true,
          restore: async (descriptor, context) => {
            const {client, asset} = await loadBoundAsset(descriptor, context);
            return React.createElement(DDNDesignerSurface, {
              snapshot: asset.content,
              assetRef: asset.assetRef,
              readOnly: asset.access === 'viewer' || !asset.editor_capable,
              saveAsset: (payload) => client.saveDDNAsset(payload),
              executeCommand: surfaceCommandExecutor(context),
              onStateChange: (state) => context.setSurfaceState?.({
                dirty: state.persistence !== 'clean',
                persistence: state.persistence,
                sourceRevision: state.revision,
              }),
              onReady: (_controller, toolbox) => context.setSurfaceState?.({
                ddnToolboxItems: toolbox.items,
                insertToolboxItem: toolbox.insert,
              }),
              onExport: context.onExport,
              onError: context.onError,
            });
          },
          canClose: async (_descriptor, context) => context.dirty ? {
            allowed: false, reason: 'Save or discard the DDN changes first.',
          } : {allowed: true, reason: ''},
        },
      ],
      commands: [
        {
          id: 'ddn.viewer.open', label: 'Open in DDN Viewer',
          description: 'Open the selected DDN project asset read-only.',
          iconKey: 'tool.erd', surfaces: ['project', 'tools'],
          validateArguments: assetArguments,
          execute: openCommand('diagram.ddn.viewer'),
        },
        {
          id: 'ddn.designer.open', label: 'Open in DDN Designer',
          description: 'Edit the selected DDN project asset.',
          iconKey: 'tool.erd', surfaces: ['project', 'tools'],
          permission: ['project.asset.edit'],
          validateArguments: assetArguments,
          execute: openCommand('diagram.ddn.designer'),
        },
        surfaceActionCommand('ddn.viewer.fit', 'Fit DDN View',
          'Fit the active DDN view to its content.', 'action.fit'),
        surfaceActionCommand('ddn.viewer.export', 'Export DDN View',
          'Export the active DDN view through the DDN public API.', 'action.export'),
        surfaceActionCommand('ddn.designer.save', 'Save DDN Workspace',
          'Save the active DDN workspace as a versioned project asset.', 'action.save'),
        surfaceActionCommand('ddn.designer.undo', 'Undo DDN Change',
          'Undo the last DDN Designer source change.', 'action.undo'),
        surfaceActionCommand('ddn.designer.redo', 'Redo DDN Change',
          'Redo the last undone DDN Designer source change.', 'action.redo'),
        surfaceActionCommand('ddn.designer.create', 'Create DDN Definition',
          'Create a definition through the DDN Designer session API.', 'action.new'),
        surfaceActionCommand('ddn.designer.export', 'Export DDN Design',
          'Export the active DDN design through the DDN public API.', 'action.export'),
      ],
      activity: [{
        id: 'activity.diagrams', label: 'Diagrams', iconKey: 'tool.erd',
        priority: 50, surfaceId: 'diagram.ddn.viewer',
      }],
      inspector: [{
        id: 'inspector.ddn.validation', label: 'DDN validation',
        priority: 70, when: (context) => context.surfaceId?.startsWith('diagram.ddn.'),
        render: (context) => context.validation ?? [],
      }],
      toolbox: [{
        id: 'toolbox.ddn.elements', label: 'DDN elements', priority: 40,
        when: (context) => context.surfaceId === 'diagram.ddn.designer',
        items: (context) => context.ddnToolboxItems ?? [],
      }],
      status: [{
        id: 'status.ddn.persistence', label: 'DDN save state', priority: 40,
        when: (context) => context.surfaceId?.startsWith('diagram.ddn.'),
        value: (context) => context.persistence ?? 'clean',
      }],
    },
  };
}

export function registerDDNModule({modules, services, api}={}) {
  if(!modules || !services) {
    throw new TypeError('DDN registration requires module and service registries.');
  }
  const removeService = services.has(PROJECT_ASSET_SERVICE_ID) ? null :
    services.register({
      id: PROJECT_ASSET_SERVICE_ID,
      version: '1.0.0',
      factory: () => new ProjectAssetClient(api),
    });
  const removeModule = modules.register(ddnModuleDefinition());
  return () => {
    removeModule();
    removeService?.();
  };
}
