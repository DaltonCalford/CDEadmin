/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {surfaceRegistry} from '../platform/SurfaceRegistry';
import {workbenchContextService} from '../platform/PlatformRegistry';

export class SurfaceHost {
  constructor(docker, registry=surfaceRegistry, options={}) {
    if(!docker?.openTab || !docker?.close) {
      throw new TypeError('Surface host requires a workbench docking authority.');
    }
    this.docker = docker;
    this.registry = registry;
    this.referencePanelId = options.referencePanelId ?? 'id-main';
    this.context = options.context ?? (() => ({}));
    this.contextService = options.contextService ?? workbenchContextService;
    this.states = new Map();
    this.closing = new Set();
    this.activeToolId = '';
    this.removeClosingListener = docker.eventBus?.registerListener?.(
      'closing', (toolId) => this.close(toolId)
    ) ?? (() => {});
    this.removeActiveListener = docker.eventBus?.registerListener?.(
      'active', (toolId) => this.activatePanel(toolId)
    ) ?? (() => {});
  }

  setSurfaceState(toolId, state) {
    this.states.set(toolId, Object.freeze({
      ...this.surfaceState(toolId), ...state,
    }));
    if(this.activeToolId === toolId) this.contextService.update(state);
  }

  surfaceState(toolId) { return this.states.get(toolId) ?? Object.freeze({}); }

  async open(surfaceId, input={}, context={}) {
    const descriptor = this.registry.descriptor(surfaceId, input);
    const existing = this.docker.find?.(descriptor.toolInstanceId);
    if(existing) {
      this.docker.focus?.(descriptor.toolInstanceId);
      this.activate(descriptor.toolInstanceId, descriptor, surfaceId);
      return Object.freeze({descriptor, panel: existing, existing: true});
    }
    const restoreContext = {...this.context(), ...context,
      setSurfaceState: (state) => this.setSurfaceState(
        descriptor.toolInstanceId, state
      )};
    const content = await this.registry.restore(descriptor, restoreContext);
    const panel = {
      id: descriptor.toolInstanceId,
      title: descriptor.presentation.title,
      icon: descriptor.presentation.iconKey,
      content,
      closable: true,
      manualClose: true,
      detachable: descriptor.capabilities.detachable,
      group: 'playground',
      toolDescriptor: descriptor,
      metaData: {toolDescriptor: descriptor, surfaceId},
    };
    this.docker.openTab(panel, this.referencePanelId, 'middle', true);
    this.activate(descriptor.toolInstanceId, descriptor, surfaceId);
    return Object.freeze({descriptor, panel, existing: false});
  }

  activate(toolId, descriptor, surfaceId='') {
    this.activeToolId = toolId;
    this.contextService.update({
      surfaceId: surfaceId || descriptor.toolKind,
      surfaceTitle: descriptor.presentation.title,
      projectId: descriptor.projectId ?? '',
      assetId: descriptor.restoreRef ?? '',
      ...this.surfaceState(toolId),
    });
  }

  activatePanel(toolId) {
    const panel = this.docker.find?.(toolId);
    const descriptor = panel?.metaData?.toolDescriptor ??
      panel?.internal?.toolDescriptor;
    if(!descriptor) {
      if(this.activeToolId) this._clearActive();
      return false;
    }
    this.activate(toolId, descriptor, panel.metaData?.surfaceId);
    return true;
  }

  async close(toolId, {force=false}={}) {
    if(this.closing.has(toolId)) return false;
    const panel = this.docker.find?.(toolId);
    if(!panel) return false;
    const descriptor = panel.metaData?.toolDescriptor ??
      panel.internal?.toolDescriptor;
    if(!descriptor || force) {
      this.docker.close(toolId, true);
      this.states.delete(toolId);
      if(this.activeToolId === toolId) this._clearActive();
      return true;
    }
    this.closing.add(toolId);
    try {
      const decision = await this.registry.toolRegistry.canClose(
        descriptor, {...this.context(), ...this.surfaceState(toolId)}
      );
      if(!decision.allowed) {
        this.context().onCloseBlocked?.(descriptor, decision.reason);
        return false;
      }
      this.docker.close(toolId, true);
      this.states.delete(toolId);
      if(this.activeToolId === toolId) this._clearActive();
      return true;
    } finally {
      this.closing.delete(toolId);
    }
  }

  _clearActive() {
    this.activeToolId = '';
    this.contextService.update({
      surfaceId: '', surfaceTitle: '', projectId: '', assetId: '',
      persistence: 'clean', validation: [],
    });
  }

  async checkpoint(toolId) {
    const panel = this.docker.find?.(toolId);
    const descriptor = panel?.metaData?.toolDescriptor ??
      panel?.internal?.toolDescriptor;
    if(!descriptor) throw new Error(`Surface is not open: ${toolId}`);
    return this.registry.toolRegistry.checkpoint(
      descriptor, {...this.context(), ...this.surfaceState(toolId)}
    );
  }

  dispose() {
    this.removeClosingListener();
    this.removeActiveListener();
    this.states.clear();
    this.closing.clear();
    if(this.activeToolId) this._clearActive();
  }
}
