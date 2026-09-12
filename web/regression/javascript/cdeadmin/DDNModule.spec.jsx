/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import React from 'react';
import {CommandRegistry} from 'sources/cdeadmin_ui/commands/CommandRegistry';
import {
  ContributionRegistry, ServiceRegistry,
} from 'sources/cdeadmin_ui/platform/PlatformRegistry';
import {SurfaceRegistry} from 'sources/cdeadmin_ui/platform/SurfaceRegistry';
import {ModuleRegistry} from 'sources/cdeadmin_ui/platform/ModuleRegistry';
import {ToolFactoryRegistry} from 'sources/cdeadmin_ui/workspace/ToolRegistry';
import {
  DDNDesignerSurface, DDNViewerSurface, PROJECT_ASSET_SERVICE_ID,
  ddnModuleDefinition,
} from 'sources/cdeadmin_ui/integrations/ddn';

function host(client) {
  const services = new ServiceRegistry();
  const tools = new ToolFactoryRegistry();
  const registries = {
    services, capabilities: {require: jest.fn()},
    surfaces: new SurfaceRegistry(tools), commands: new CommandRegistry(),
    inspector: new ContributionRegistry('inspector'),
    toolbox: new ContributionRegistry('toolbox'),
    status: new ContributionRegistry('status'),
    activity: new ContributionRegistry('activity'),
  };
  services.register({id: PROJECT_ASSET_SERVICE_ID, factory: () => client});
  const modules = new ModuleRegistry(registries);
  modules.register(ddnModuleDefinition());
  return {...registries, modules};
}

const asset = {
  asset_type: 'ddn-workspace', access: 'editor', editor_capable: true,
  assetRef: {schemaVersion: 1, projectId: 'project-one', assetId: 'diagram-one',
    assetType: 'ddn-workspace', assetVersion: 2, path: 'diagram.ddn',
    displayName: 'Diagram'},
  content: {format: 'ddn-workspace@1', entry: 'diagram.ddn', view: 'main',
    files: {'diagram.ddn': 'diagram main {}'}},
};

describe('DDN first-party module hosting', () => {
  it('registers both surfaces and all shell contributions', async () => {
    const client = {asset: jest.fn().mockResolvedValue(asset),
      saveDDNAsset: jest.fn()};
    const architecture = host(client);
    await architecture.modules.activate('cdeadmin.ddn');
    expect(architecture.surfaces.list({assetType: 'ddn-workspace'})
      .map((surface) => surface.id)).toEqual([
      'diagram.ddn.viewer', 'diagram.ddn.designer',
    ]);
    expect(architecture.commands.has('ddn.viewer.open')).toBe(true);
    expect(architecture.commands.has('ddn.viewer.fit')).toBe(true);
    expect(architecture.commands.has('ddn.viewer.export')).toBe(true);
    expect(architecture.commands.has('ddn.designer.save')).toBe(true);
    expect(architecture.commands.has('ddn.designer.undo')).toBe(true);
    expect(architecture.commands.has('ddn.designer.redo')).toBe(true);
    expect(architecture.commands.has('ddn.designer.create')).toBe(true);
    expect(architecture.commands.has('ddn.designer.export')).toBe(true);
    expect(architecture.inspector.resolve({surfaceId: 'diagram.ddn.designer'}))
      .toHaveLength(1);
    expect(architecture.toolbox.resolve({surfaceId: 'diagram.ddn.designer'}))
      .toHaveLength(1);
    expect(architecture.status.resolve({surfaceId: 'diagram.ddn.viewer'}))
      .toHaveLength(1);
    expect(architecture.activity.resolve()).toHaveLength(1);
  });

  it('restores viewer and designer only through project asset authority', async () => {
    const client = {asset: jest.fn().mockResolvedValue(asset),
      saveDDNAsset: jest.fn().mockResolvedValue({assetRef: asset.assetRef})};
    const architecture = host(client);
    await architecture.modules.activate('cdeadmin.ddn');
    const service = await architecture.services.resolve(PROJECT_ASSET_SERVICE_ID);
    const descriptor = architecture.surfaces.descriptor('diagram.ddn.viewer', {
      toolInstanceId: 'ddn-viewer-one', restoreRef: 'diagram-one',
      projectId: 'project-one',
    });
    const viewer = await architecture.surfaces.restore(descriptor, {
      services: {[PROJECT_ASSET_SERVICE_ID]: service},
    });
    expect(React.isValidElement(viewer)).toBe(true);
    expect(viewer.type).toBe(DDNViewerSurface);
    expect(viewer.props.snapshot).toBe(asset.content);

    const designerDescriptor = architecture.surfaces.descriptor(
      'diagram.ddn.designer', {toolInstanceId: 'ddn-designer-one',
        restoreRef: 'diagram-one', projectId: 'project-one'}
    );
    const designer = await architecture.surfaces.restore(designerDescriptor, {
      services: {[PROJECT_ASSET_SERVICE_ID]: service},
    });
    expect(designer.type).toBe(DDNDesignerSurface);
    await designer.props.saveAsset({assetRef: asset.assetRef});
    expect(client.saveDDNAsset).toHaveBeenCalledTimes(1);
    expect(client.asset).toHaveBeenCalledWith('project-one', 'diagram-one');
  });

  it('opens through commands and enforces edit permission and close policy', async () => {
    const architecture = host({asset: jest.fn().mockResolvedValue(asset)});
    await architecture.modules.activate('cdeadmin.ddn');
    const openSurface = jest.fn().mockResolvedValue('opened');
    await expect(architecture.commands.execute('ddn.viewer.open', {
      projectId: 'project-one', assetId: 'diagram-one',
    }, {openSurface})).resolves.toBe('opened');
    expect(openSurface).toHaveBeenCalledWith('diagram.ddn.viewer',
      expect.objectContaining({restoreRef: 'diagram-one'}));
    await expect(architecture.commands.execute('ddn.designer.open', {
      projectId: 'project-one', assetId: 'diagram-one',
    }, {openSurface, currentUser: {permissions: []}})).rejects
      .toMatchObject({code: 'permission_denied'});
    const descriptor = architecture.surfaces.descriptor('diagram.ddn.designer', {
      toolInstanceId: 'ddn-designer-one', restoreRef: 'diagram-one',
      projectId: 'project-one',
    });
    await expect(architecture.surfaces.toolRegistry.canClose(descriptor, {dirty: true}))
      .resolves.toEqual({allowed: false,
        reason: 'Save or discard the DDN changes first.'});
  });

  it('routes every surface toolbar action through non-macro command authority', async () => {
    const architecture = host({asset: jest.fn().mockResolvedValue(asset)});
    await architecture.modules.activate('cdeadmin.ddn');
    const invoke = jest.fn().mockReturnValue('complete');
    for(const id of ['ddn.viewer.fit', 'ddn.viewer.export',
      'ddn.designer.save', 'ddn.designer.undo', 'ddn.designer.redo',
      'ddn.designer.create', 'ddn.designer.export']) {
      await expect(architecture.commands.execute(id, {}, {invoke, enabled: true}))
        .resolves.toBe('complete');
      expect(architecture.commands.get(id).macroCallable).toBe(false);
      await expect(architecture.commands.execute(id, {}, {invoke, enabled: false}))
        .rejects.toMatchObject({code: 'command_disabled'});
    }
    expect(invoke).toHaveBeenCalledTimes(7);
  });

  it('fails closed when the asset is not a DDN workspace', async () => {
    const architecture = host({asset: jest.fn().mockResolvedValue({
      asset_type: 'query', content: {},
    })});
    await architecture.modules.activate('cdeadmin.ddn');
    const service = await architecture.services.resolve(PROJECT_ASSET_SERVICE_ID);
    const descriptor = architecture.surfaces.descriptor('diagram.ddn.viewer', {
      toolInstanceId: 'ddn-viewer-one', restoreRef: 'query-one',
      projectId: 'project-one',
    });
    await expect(architecture.surfaces.restore(descriptor, {
      services: {[PROJECT_ASSET_SERVICE_ID]: service},
    })).rejects.toThrow('not a DDN workspace');
  });
});
