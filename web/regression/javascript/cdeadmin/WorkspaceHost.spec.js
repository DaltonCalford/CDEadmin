/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {createToolDescriptor} from 'sources/cdeadmin_ui/workspace/ToolDescriptor';
import {
  WorkspaceHost,
  WORKSPACE_HOST_MODES,
} from 'sources/cdeadmin_ui/workspace/WorkspaceHost';

function descriptor(detachable=true) {
  return createToolDescriptor({
    toolInstanceId: 'query-1',
    toolKind: 'query_editor',
    capabilities: {detachable},
  });
}

describe('CDEadmin workspace host capabilities', () => {
  it('declares browser limits without pretending to control displays', () => {
    const host = new WorkspaceHost({open: jest.fn()});
    expect(host.capabilities()).toEqual(expect.objectContaining({
      mode: WORKSPACE_HOST_MODES.BROWSER,
      popoutWindow: true,
      nativeWindowPlacement: false,
      exactDisplayPlacement: false,
      crossWindowDrag: false,
    }));
    expect(host.prepareDetach(descriptor()).allowed).toBe(true);
  });

  it('uses the constrained desktop workspace bridge when available', () => {
    const notifyPlacement = jest.fn();
    const moveToAdjacentDisplay = jest.fn(()=>Promise.resolve({
      display: {id: '2'},
    }));
    const host = new WorkspaceHost({
      electronUI: {workspaceWindows: {
        notifyPlacement,
        moveToAdjacentDisplay,
        getCapabilities: ()=>({
          nativeWindowPlacement: true,
          exactDisplayPlacement: true,
          crossWindowDrag: false,
          sameOriginCoordination: true,
        }),
      }},
    });
    expect(host.capabilities().mode).toBe(WORKSPACE_HOST_MODES.DESKTOP);
    expect(host.capabilities().nativeWindowPlacement).toBe(true);
    expect(host.capabilities().crossWindowDrag).toBe(false);
    const message = host.publishPlacement(descriptor(), 'new-window');
    expect(message).toEqual({
      schema: 'cdeadmin.workspace-placement-event.v1',
      toolInstanceId: 'query-1',
      toolKind: 'query_editor',
      placement: 'new-window',
      revision: 0,
    });
    expect(notifyPlacement).toHaveBeenCalledWith(message);
    return expect(host.moveToAdjacentDisplay('next')).resolves.toEqual({
      display: {id: '2'},
    });
  });

  it('reports a usable reason for fixed or unsupported windows', () => {
    const fixed = new WorkspaceHost({open: jest.fn()});
    expect(fixed.prepareDetach(descriptor(false))).toEqual({
      allowed: false,
      reason: 'This tool does not support detached presentation.',
    });
    const unavailable = new WorkspaceHost({});
    expect(unavailable.prepareDetach(descriptor())).toEqual({
      allowed: false,
      reason: 'This host cannot open another application window.',
    });
    return expect(unavailable.moveToAdjacentDisplay('next'))
      .rejects.toThrow('unavailable');
  });

  it('disposes safely when a host supplies a partial channel shim', () => {
    const host = new WorkspaceHost({
      open: jest.fn(),
      BroadcastChannel: function() {
        return {postMessage: jest.fn()};
      },
    });
    expect(() => host.dispose()).not.toThrow();
    expect(host.channel).toBeNull();
  });

  it('prepares authority before exposing a transferable descriptor', async () => {
    const transferClient = {
      ensureWorkspace: jest.fn(()=>Promise.resolve({revision: 0})),
      registerWindow: jest.fn(()=>Promise.resolve({revision: 0})),
      registerTool: jest.fn(()=>Promise.resolve({checkpoint_revision: 0})),
      prepare: jest.fn(()=>Promise.resolve({
        move_token: 'move.proof', checkpoint_revision: 1,
        status: 'prepared',
      })),
      acknowledge: jest.fn(()=>Promise.resolve({status: 'acknowledged'})),
      commit: jest.fn(()=>Promise.resolve({status: 'committed'})),
      abort: jest.fn(()=>Promise.resolve({status: 'aborted'})),
    };
    const host = new WorkspaceHost({open: jest.fn()}, transferClient);
    const result = await host.prepareTransfer(descriptor(), {
      mode: 'detached', windowId: 'detached-1', dockArea: 'main',
      tabOrder: 0,
    }, {idempotencyKey: 'move-query-1'});

    expect(transferClient.ensureWorkspace).toHaveBeenCalledWith(
      'default', expect.objectContaining({name: 'default'})
    );
    expect(transferClient.registerWindow).toHaveBeenCalledWith(
      'default', 'main', expect.objectContaining({role: 'main'})
    );
    expect(transferClient.registerTool).toHaveBeenCalledWith(
      'default', 'query-1', result.descriptor
    );
    expect(transferClient.prepare).toHaveBeenCalledWith(
      'default', 'query-1', expect.objectContaining({
        expected_revision: 0,
        idempotency_key: 'move-query-1',
        destination: expect.objectContaining({
          mode: 'detached', windowId: 'detached-1', revision: 0,
        }),
      })
    );
    expect(result.transfer.status).toBe('prepared');
    await host.registerTransferDestination('default', 'detached-1', {
      role: 'detached-tool', placement: {displayId: '2'},
    });
    await host.acknowledgeTransfer('move.proof', 'query-1', 1);
    await host.commitTransfer('move.proof');
    await host.abortTransfer('move.proof', 'restore failed');
    expect(transferClient.acknowledge).toHaveBeenCalled();
    expect(transferClient.registerWindow).toHaveBeenLastCalledWith(
      'default', 'detached-1', expect.objectContaining({
        role: 'detached-tool', placement: {displayId: '2'},
      })
    );
    expect(transferClient.commit).toHaveBeenCalledWith('move.proof');
    expect(transferClient.abort).toHaveBeenCalledWith(
      'move.proof', 'restore failed'
    );
    expect(host.capabilities().crossWindowDrag).toBe(false);
  });
});
