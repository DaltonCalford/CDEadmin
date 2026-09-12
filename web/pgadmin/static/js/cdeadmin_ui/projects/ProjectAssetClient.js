/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import getApiInstance from 'sources/api_instance';

const ROOT = '/cdeadmin/api/projects';

function segment(value) {
  return encodeURIComponent(String(value));
}
function data(response) {
  return response?.data?.data;
}

export class ProjectAssetClientError extends Error {
  constructor(error) {
    const response = error?.response?.data;
    super(response?.errormsg || error?.message || 'Project asset request failed.');
    this.name = 'ProjectAssetClientError';
    this.code = response?.code || 'project_asset_request_failed';
    this.status = error?.response?.status ?? 0;
    this.cause = error;
  }
}

async function invoke(request) {
  try {
    return data(await request());
  } catch(error) {
    throw new ProjectAssetClientError(error);
  }
}

/** Authenticated frontend boundary for authored projects and immutable assets. */
export class ProjectAssetClient {
  constructor(api=getApiInstance()) {
    this.api = api;
  }

  listProjects() {
    return invoke(() => this.api.get(ROOT));
  }

  createProject(projectId, request={}) {
    return invoke(() => this.api.put(
      `${ROOT}/${segment(projectId)}`, request
    ));
  }

  project(projectId) {
    return invoke(() => this.api.get(`${ROOT}/${segment(projectId)}`));
  }

  updateProject(projectId, request) {
    return invoke(() => this.api.post(
      `${ROOT}/${segment(projectId)}`, request
    ));
  }

  deleteProject(projectId, expectedRevision) {
    return invoke(() => this.api.delete(
      `${ROOT}/${segment(projectId)}`,
      {data: {expected_revision: expectedRevision}}
    ));
  }

  asset(projectId, assetId, version=null) {
    const query = version === null ? '' : `?version=${segment(version)}`;
    return invoke(() => this.api.get(
      `${ROOT}/${segment(projectId)}/assets/${segment(assetId)}${query}`
    ));
  }

  revisions(projectId, assetId) {
    return invoke(() => this.api.get(
      `${ROOT}/${segment(projectId)}/assets/${segment(assetId)}/revisions`
    ));
  }

  saveAsset(projectId, assetId, request) {
    return invoke(() => this.api.put(
      `${ROOT}/${segment(projectId)}/assets/${segment(assetId)}`, request
    ));
  }

  saveDDNAsset(payload) {
    const reference = payload?.assetRef;
    return this.saveAsset(reference?.projectId, reference?.assetId, payload);
  }

  deleteAsset(projectId, assetId, expectedVersion) {
    return invoke(() => this.api.delete(
      `${ROOT}/${segment(projectId)}/assets/${segment(assetId)}`,
      {data: {expected_version: expectedVersion}}
    ));
  }

  setMember(projectId, request) {
    return invoke(() => this.api.put(
      `${ROOT}/${segment(projectId)}/members`, request
    ));
  }

  removeMember(projectId, principalType, principalId) {
    return invoke(() => this.api.delete(
      `${ROOT}/${segment(projectId)}/members/` +
      `${segment(principalType)}/${segment(principalId)}`
    ));
  }
}
