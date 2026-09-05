/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {createToolDescriptor} from './ToolDescriptor';
import {WorkspaceTransferClient} from './WorkspaceTransferClient';

export const WORKSPACE_HOST_MODES = Object.freeze({
  BROWSER: 'browser',
  DESKTOP: 'desktop',
});

const CHANNEL_NAME = 'cdeadmin-workspace-placement-v1';

export class WorkspaceHost {
  constructor(
    hostWindow=typeof window === 'undefined' ? undefined : window,
    transferClient=null
  ) {
    this.window = hostWindow;
    this.desktopBridge = hostWindow?.electronUI?.workspaceWindows ?? null;
    this.mode = this.desktopBridge ?
      WORKSPACE_HOST_MODES.DESKTOP : WORKSPACE_HOST_MODES.BROWSER;
    this.channel = !this.desktopBridge && hostWindow?.BroadcastChannel ?
      new hostWindow.BroadcastChannel(CHANNEL_NAME) : null;
    this.transferClient = transferClient ?? (
      hostWindow ? new WorkspaceTransferClient() : null
    );
  }

  capabilities() {
    const desktop = this.mode === WORKSPACE_HOST_MODES.DESKTOP;
    const native = this.desktopBridge?.getCapabilities?.() ?? {};
    return Object.freeze({
      mode: this.mode,
      inWindowDocking: true,
      popoutWindow: Boolean(desktop || this.window?.open),
      nativeWindowPlacement: Boolean(native.nativeWindowPlacement),
      exactDisplayPlacement: Boolean(native.exactDisplayPlacement),
      crossWindowDrag: Boolean(native.crossWindowDrag),
      sameOriginCoordination: Boolean(
        native.sameOriginCoordination || this.channel
      ),
    });
  }

  prepareDetach(descriptor) {
    const normalized = createToolDescriptor(descriptor);
    if(!normalized.capabilities.detachable) {
      return Object.freeze({
        allowed: false,
        reason: 'This tool does not support detached presentation.',
      });
    }
    if(!this.capabilities().popoutWindow) {
      return Object.freeze({
        allowed: false,
        reason: 'This host cannot open another application window.',
      });
    }
    return Object.freeze({allowed: true, reason: '', descriptor: normalized});
  }

  async prepareTransfer(descriptor, destination, options={}) {
    const readiness = this.prepareDetach(descriptor);
    if(!readiness.allowed) return readiness;
    if(!this.transferClient) {
      throw new Error('Workspace transfer authority is unavailable.');
    }
    const normalized = readiness.descriptor;
    const workspaceId = normalized.placement.workspaceId;
    const windowId = normalized.placement.windowId;
    const target = {
      ...normalized.placement,
      ...destination,
      workspaceId,
      revision: normalized.placement.revision,
    };
    await this.transferClient.ensureWorkspace(workspaceId, {
      name: options.workspaceName ?? workspaceId,
      layout_reference: options.layoutReference ?? '',
    });
    await this.transferClient.registerWindow(workspaceId, windowId, {
      role: options.sourceWindowRole ?? 'main',
      device_profile_id: options.deviceProfileId ?? '',
      display_fingerprint: options.displayFingerprint ?? '',
      placement: options.sourceWindowPlacement ?? {},
    });
    await this.transferClient.registerTool(
      workspaceId, normalized.toolInstanceId, normalized
    );
    const prepared = await this.transferClient.prepare(
      workspaceId, normalized.toolInstanceId, {
        expected_revision: normalized.placement.revision,
        idempotency_key: options.idempotencyKey ??
          `move-${normalized.toolInstanceId}-${Date.now()}`,
        destination: target,
        checkpoint: {
          checkpoint_reference: options.checkpointReference ??
            normalized.restoreRef,
          view_state: options.viewState ?? {},
        },
      }
    );
    return Object.freeze({
      allowed: true,
      reason: '',
      descriptor: normalized,
      transfer: prepared,
    });
  }

  async acknowledgeTransfer(moveToken, toolInstanceId, checkpointRevision) {
    if(!this.transferClient) {
      throw new Error('Workspace transfer authority is unavailable.');
    }
    return this.transferClient.acknowledge(moveToken, {
      restored_tool_instance_id: toolInstanceId,
      checkpoint_revision: checkpointRevision,
    });
  }

  async registerTransferDestination(workspaceId, windowId, options={}) {
    if(!this.transferClient) {
      throw new Error('Workspace transfer authority is unavailable.');
    }
    return this.transferClient.registerWindow(workspaceId, windowId, {
      role: options.role ?? 'detached-tool',
      device_profile_id: options.deviceProfileId ?? '',
      display_fingerprint: options.displayFingerprint ?? '',
      placement: options.placement ?? {},
    });
  }

  async commitTransfer(moveToken) {
    if(!this.transferClient) {
      throw new Error('Workspace transfer authority is unavailable.');
    }
    return this.transferClient.commit(moveToken);
  }

  async abortTransfer(moveToken, reason='') {
    if(!this.transferClient) return null;
    return this.transferClient.abort(moveToken, reason);
  }

  publishPlacement(descriptor, placement) {
    const normalized = createToolDescriptor(descriptor);
    const message = Object.freeze({
      schema: 'cdeadmin.workspace-placement-event.v1',
      toolInstanceId: normalized.toolInstanceId,
      toolKind: normalized.toolKind,
      placement: String(placement),
      revision: normalized.placement.revision,
    });
    this.channel?.postMessage(message);
    this.desktopBridge?.notifyPlacement?.(message);
    return message;
  }

  registerWindow(details) {
    if(!this.desktopBridge?.registerWindow) return Promise.resolve(null);
    return this.desktopBridge.registerWindow(details);
  }

  listDisplays() {
    if(!this.desktopBridge?.listDisplays) return Promise.resolve([]);
    return this.desktopBridge.listDisplays();
  }

  moveToDisplay(displayId) {
    if(!this.desktopBridge?.moveToDisplay) {
      return Promise.reject(new Error(
        'Display placement is unavailable in this host.'
      ));
    }
    return this.desktopBridge.moveToDisplay(displayId);
  }

  moveToAdjacentDisplay(direction='next') {
    if(!['next', 'previous'].includes(direction)) {
      return Promise.reject(new TypeError('Display direction is invalid.'));
    }
    if(!this.desktopBridge?.moveToAdjacentDisplay) {
      return Promise.reject(new Error(
        'Display placement is unavailable in this host.'
      ));
    }
    return this.desktopBridge.moveToAdjacentDisplay(direction);
  }

  onPlacement(callback) {
    if(typeof callback !== 'function') {
      throw new TypeError('Workspace placement listener must be a function.');
    }
    if(this.desktopBridge?.onPlacement) {
      return this.desktopBridge.onPlacement(callback);
    }
    if(!this.channel) return ()=>{};
    const listener = (event)=>callback(event.data);
    this.channel.addEventListener('message', listener);
    return ()=>this.channel?.removeEventListener('message', listener);
  }

  dispose() {
    if(typeof this.channel?.close === 'function') this.channel.close();
    this.channel = null;
  }
}

export function createWorkspaceHost(
  hostWindow=typeof window === 'undefined' ? undefined : window,
  transferClient=null
) {
  return new WorkspaceHost(hostWindow, transferClient);
}
