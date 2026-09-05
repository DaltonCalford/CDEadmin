/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import getApiInstance from 'sources/api_instance';

const ROOT = '/cdeadmin/api';

function segment(value) {
  return encodeURIComponent(String(value));
}

function payload(response) {
  return response?.data?.data;
}

function moveRequest(moveToken) {
  const moveId = String(moveToken).split('.', 1)[0];
  return {
    moveId: segment(moveId),
    config: {headers: {'X-CDEadmin-Workspace-Move': moveToken}},
  };
}

/** Authenticated client for authoritative workspace placement changes. */
export class WorkspaceTransferClient {
  constructor(api=getApiInstance()) {
    this.api = api;
  }

  async ensureWorkspace(workspaceId, request={}) {
    return payload(await this.api.put(
      `${ROOT}/workspaces/${segment(workspaceId)}`, request
    ));
  }

  async state(workspaceId) {
    return payload(await this.api.get(
      `${ROOT}/workspaces/${segment(workspaceId)}`
    ));
  }

  async registerWindow(workspaceId, windowId, request={}) {
    return payload(await this.api.put(
      `${ROOT}/workspaces/${segment(workspaceId)}/windows/` +
      segment(windowId), request
    ));
  }

  async registerTool(workspaceId, toolId, descriptor) {
    return payload(await this.api.put(
      `${ROOT}/workspaces/${segment(workspaceId)}/tools/${segment(toolId)}`,
      {descriptor}
    ));
  }

  async checkpoint(workspaceId, toolId, request={}) {
    return payload(await this.api.post(
      `${ROOT}/workspaces/${segment(workspaceId)}/tools/${segment(toolId)}` +
      '/checkpoints', request
    ));
  }

  async prepare(workspaceId, toolId, request) {
    return payload(await this.api.post(
      `${ROOT}/workspaces/${segment(workspaceId)}/tools/${segment(toolId)}` +
      '/moves/prepare', request
    ));
  }

  async acknowledge(moveToken, request) {
    const move = moveRequest(moveToken);
    return payload(await this.api.post(
      `${ROOT}/workspace-moves/${move.moveId}/acknowledge`,
      request, move.config
    ));
  }

  async commit(moveToken) {
    const move = moveRequest(moveToken);
    return payload(await this.api.post(
      `${ROOT}/workspace-moves/${move.moveId}/commit`, {}, move.config
    ));
  }

  async abort(moveToken, reason='') {
    const move = moveRequest(moveToken);
    return payload(await this.api.post(
      `${ROOT}/workspace-moves/${move.moveId}/abort`, {reason}, move.config
    ));
  }
}
