/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {SurfaceRegistry} from 'sources/cdeadmin_ui/platform/SurfaceRegistry';
import {ToolFactoryRegistry} from 'sources/cdeadmin_ui/workspace/ToolRegistry';
import {SurfaceHost} from 'sources/cdeadmin_ui/workspace/SurfaceHost';
import {WorkbenchContextService} from
  'sources/cdeadmin_ui/platform/PlatformRegistry';

function docker() {
  const panels = new Map();
  const listeners = new Map();
  return {
    panels,
    openTab: jest.fn((panel) => panels.set(panel.id, panel)),
    close: jest.fn((id) => panels.delete(id)),
    find: jest.fn((id) => panels.get(id)),
    focus: jest.fn(),
    eventBus: {registerListener: jest.fn((name, listener) => {
      listeners.set(name, listener);
      return jest.fn(() => listeners.delete(name));
    })},
    fire: (name, value) => listeners.get(name)?.(value),
    closeRequest: (id) => listeners.get('closing')?.(id),
  };
}

function create() {
  const tools = new ToolFactoryRegistry();
  const surfaces = new SurfaceRegistry(tools);
  surfaces.register({
    id: 'diagram.test.designer', moduleId: 'module.test',
    title: 'Test Designer', iconKey: 'tool.erd', editable: true,
    restore: (_descriptor, context) => ({content: context.value}),
    checkpoint: (descriptor, context) => ({descriptor, state: context.state}),
    canClose: (_descriptor, context) => context.dirty ?
      {allowed: false, reason: 'unsaved'} : {allowed: true},
  });
  const dock = docker();
  const blocked = jest.fn();
  const workbenchContext = new WorkbenchContextService();
  const host = new SurfaceHost(dock, surfaces, {referencePanelId: 'main',
    contextService: workbenchContext,
    context: () => ({value: 42, onCloseBlocked: blocked})});
  return {tools, surfaces, dock, blocked, host, workbenchContext};
}

describe('workbench surface host', () => {
  it('restores registered surfaces into real dock panels with durable identity', async () => {
    const {dock, host, workbenchContext} = create();
    const opened = await host.open('diagram.test.designer', {
      toolInstanceId: 'designer-one', restoreRef: 'asset-one',
      projectId: 'project-one',
    });
    expect(opened.existing).toBe(false);
    expect(opened.panel.content).toEqual({content: 42});
    expect(opened.panel.metaData.toolDescriptor.restoreRef).toBe('asset-one');
    expect(dock.openTab).toHaveBeenCalledWith(
      expect.objectContaining({id: 'designer-one'}), 'main', 'middle', true
    );
    expect(workbenchContext.snapshot()).toEqual(expect.objectContaining({
      surfaceId: 'diagram.test.designer', surfaceTitle: 'Test Designer',
      projectId: 'project-one', assetId: 'asset-one',
    }));
  });

  it('focuses an already-open logical instance instead of duplicating it', async () => {
    const {dock, host} = create();
    const input = {toolInstanceId: 'designer-one', restoreRef: 'asset-one',
      projectId: 'project-one'};
    await host.open('diagram.test.designer', input);
    const second = await host.open('diagram.test.designer', input);
    expect(second.existing).toBe(true);
    expect(dock.openTab).toHaveBeenCalledTimes(1);
    expect(dock.focus).toHaveBeenCalledWith('designer-one');
  });

  it('tracks dock focus transitions and clears stale surface context', async () => {
    const {dock, host, workbenchContext} = create();
    await host.open('diagram.test.designer', {toolInstanceId: 'designer-one',
      restoreRef: 'asset-one', projectId: 'project-one'});
    dock.panels.set('legacy-one', {id: 'legacy-one'});
    expect(host.activatePanel('designer-one')).toBe(true);
    host.setSurfaceState('designer-one', {dirty: true});
    dock.fire('active', 'legacy-one');
    expect(workbenchContext.snapshot().surfaceId).toBe('');
    dock.fire('active', 'designer-one');
    expect(workbenchContext.snapshot()).toEqual(expect.objectContaining({
      surfaceId: 'diagram.test.designer', dirty: true,
    }));
  });

  it('enforces close policy using current state and permits clean closure', async () => {
    const {dock, blocked, host, workbenchContext} = create();
    await host.open('diagram.test.designer', {toolInstanceId: 'designer-one',
      restoreRef: 'asset-one', projectId: 'project-one'});
    host.setSurfaceState('designer-one', {dirty: true});
    expect(workbenchContext.snapshot().dirty).toBe(true);
    await expect(host.close('designer-one')).resolves.toBe(false);
    expect(blocked).toHaveBeenCalledWith(expect.any(Object), 'unsaved');
    expect(dock.close).not.toHaveBeenCalled();
    host.setSurfaceState('designer-one', {dirty: false});
    await expect(host.close('designer-one')).resolves.toBe(true);
    expect(dock.close).toHaveBeenCalledWith('designer-one', true);
    expect(workbenchContext.snapshot().surfaceId).toBe('');
  });

  it('checkpoints through the owning factory and tears down listeners', async () => {
    const {dock, host} = create();
    await host.open('diagram.test.designer', {toolInstanceId: 'designer-one',
      restoreRef: 'asset-one', projectId: 'project-one'});
    host.setSurfaceState('designer-one', {state: 'saved'});
    await expect(host.checkpoint('designer-one')).resolves.toMatchObject({
      state: 'saved', descriptor: {toolInstanceId: 'designer-one'},
    });
    const removers = dock.eventBus.registerListener.mock.results.map(
      (result) => result.value
    );
    host.dispose();
    removers.forEach((remove) => expect(remove).toHaveBeenCalled());
  });
});
