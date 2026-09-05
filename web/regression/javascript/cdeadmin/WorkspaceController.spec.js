/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {
  createToolDescriptor,
  TOOL_KINDS,
  WORKSPACE_PLACEMENTS,
  WorkspaceController,
} from 'sources/cdeadmin_ui/workspace/Workspace';

function controllerFixture() {
  const first = {id: 'first', internal: {floatable: false}};
  const middle = {
    id: 'middle',
    internal: {floatable: true, detachable: true},
    metaData: {toolDescriptor: createToolDescriptor({
      toolInstanceId: 'middle',
      toolKind: TOOL_KINDS.QUERY_EDITOR,
      capabilities: {detachable: true},
    })},
  };
  const last = {id: 'last', internal: {floatable: false}};
  const parent = {id: 'panel', tabs: [first, middle, last]};
  const target = {id: 'target', internal: {title: 'Results'}};
  const targetParent = {id: 'target-panel', activeId: 'target', tabs: [target]};
  first.parent = parent;
  middle.parent = parent;
  last.parent = parent;
  target.parent = targetParent;
  const items = {
    first, middle, last, panel: parent, target, 'target-panel': targetParent,
  };
  const layoutObj = {
    find: jest.fn((id)=>items[id]),
    dockMove: jest.fn(),
    getLayout: jest.fn(()=>({
      dockbox: {children: [parent, targetParent]},
    })),
  };
  const controller = new WorkspaceController('test', {dockbox: {children: []}});
  controller.layoutObj = layoutObj;
  return {
    controller, layoutObj, first, middle, last, parent, target, targetParent,
  };
}

describe('CDEadmin workspace placement commands', () => {
  it('moves tabs before and after without a drag gesture', () => {
    const {controller, layoutObj, middle, first, last} = controllerFixture();

    expect(controller.moveAdjacent(
      'middle', WORKSPACE_PLACEMENTS.BEFORE)).toBe(true);
    expect(layoutObj.dockMove).toHaveBeenLastCalledWith(
      middle, first, WORKSPACE_PLACEMENTS.BEFORE);

    expect(controller.moveAdjacent(
      'middle', WORKSPACE_PLACEMENTS.AFTER)).toBe(true);
    expect(layoutObj.dockMove).toHaveBeenLastCalledWith(
      middle, last, WORKSPACE_PLACEMENTS.AFTER);
  });

  it('supports split, floating, and detached placement commands', () => {
    const {controller, layoutObj, middle, parent} = controllerFixture();

    expect(controller.place('middle', WORKSPACE_PLACEMENTS.RIGHT)).toBe(true);
    expect(layoutObj.dockMove).toHaveBeenLastCalledWith(
      middle, parent, WORKSPACE_PLACEMENTS.RIGHT);

    expect(controller.place('middle', WORKSPACE_PLACEMENTS.FLOAT)).toBe(true);
    expect(layoutObj.dockMove).toHaveBeenLastCalledWith(
      middle, null, WORKSPACE_PLACEMENTS.FLOAT);

    expect(controller.place('middle', WORKSPACE_PLACEMENTS.DETACH)).toBe(true);
    expect(layoutObj.dockMove).toHaveBeenLastCalledWith(
      middle, null, WORKSPACE_PLACEMENTS.DETACH);
  });

  it('reports placement capabilities for command menus', () => {
    const {controller} = controllerFixture();

    expect(controller.getPlacementCapabilities('middle')).toEqual(
      expect.objectContaining({
        before: true,
        after: true,
        split: true,
        attach: true,
        float: true,
        detach: true,
        detachReason: '',
      }));
    expect(controller.getPlacementCapabilities('first').before).toBe(false);
    expect(controller.getPlacementCapabilities('last').after).toBe(false);
  });

  it('lists named targets and attaches a tab without dragging', () => {
    const {controller, layoutObj, middle, target} = controllerFixture();

    expect(controller.listPlacementTargets('middle')).toEqual([
      {id: 'target', label: 'Results'},
    ]);
    expect(controller.place(
      'middle', WORKSPACE_PLACEMENTS.TAB, 'target')).toBe(true);
    expect(layoutObj.dockMove).toHaveBeenLastCalledWith(
      middle, target, WORKSPACE_PLACEMENTS.TAB);
  });

  it('rejects unsupported placements and missing panels safely', () => {
    const {controller} = controllerFixture();

    expect(() => controller.place('middle', 'diagonal'))
      .toThrow('Unsupported workspace placement');
    expect(controller.place('missing', WORKSPACE_PLACEMENTS.RIGHT)).toBe(false);
    expect(controller.moveAdjacent(
      'first', WORKSPACE_PLACEMENTS.BEFORE)).toBe(false);
  });

  it('reports display movement success and host failure', async () => {
    const {controller} = controllerFixture();
    controller.workspaceHost = {
      moveToAdjacentDisplay: jest.fn(()=>Promise.resolve({display: {id: '2'}})),
    };
    await expect(controller.moveWindowToAdjacentDisplay('next'))
      .resolves.toBe(true);

    controller.workspaceHost.moveToAdjacentDisplay = jest.fn(
      ()=>Promise.reject(new Error('display unavailable'))
    );
    await expect(controller.moveWindowToAdjacentDisplay('previous'))
      .resolves.toBe(false);
  });
});
