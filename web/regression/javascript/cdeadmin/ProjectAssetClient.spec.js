/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {
  ProjectAssetClient, ProjectAssetClientError,
} from 'sources/cdeadmin_ui/projects/ProjectAssetClient';

describe('CDEadmin project and asset API client', () => {
  let api;
  let client;

  beforeEach(() => {
    const response = (value) => Promise.resolve({data: {data: value}});
    api = {
      get: jest.fn(() => response({read: true})),
      put: jest.fn(() => response({written: true})),
      post: jest.fn(() => response({updated: true})),
      delete: jest.fn(() => response({deleted: true})),
    };
    client = new ProjectAssetClient(api);
  });

  it('uses encoded owner-authorized routes for all project operations', async () => {
    await expect(client.listProjects()).resolves.toEqual({read: true});
    await client.createProject('project one', {name: 'One'});
    await client.project('project one');
    await client.updateProject('project one', {expected_revision: 2});
    await client.deleteProject('project one', 3);

    expect(api.get).toHaveBeenCalledWith('/cdeadmin/api/projects');
    expect(api.put).toHaveBeenCalledWith(
      '/cdeadmin/api/projects/project%20one', {name: 'One'}
    );
    expect(api.post).toHaveBeenCalledWith(
      '/cdeadmin/api/projects/project%20one', {expected_revision: 2}
    );
    expect(api.delete).toHaveBeenCalledWith(
      '/cdeadmin/api/projects/project%20one',
      {data: {expected_revision: 3}}
    );
  });

  it('supports current, historical, save, delete and revision asset calls', async () => {
    await client.asset('project', 'diagram:1');
    await client.asset('project', 'diagram:1', 7);
    await client.revisions('project', 'diagram:1');
    await client.saveAsset('project', 'diagram:1', {expected_version: 7});
    await client.deleteAsset('project', 'diagram:1', 8);

    expect(api.get).toHaveBeenNthCalledWith(
      1, '/cdeadmin/api/projects/project/assets/diagram%3A1'
    );
    expect(api.get).toHaveBeenNthCalledWith(
      2, '/cdeadmin/api/projects/project/assets/diagram%3A1?version=7'
    );
    expect(api.get).toHaveBeenNthCalledWith(
      3, '/cdeadmin/api/projects/project/assets/diagram%3A1/revisions'
    );
    expect(api.delete).toHaveBeenCalledWith(
      '/cdeadmin/api/projects/project/assets/diagram%3A1',
      {data: {expected_version: 8}}
    );
  });

  it('binds DDN saves and project membership to explicit identities', async () => {
    const payload = {assetRef: {
      projectId: 'project-one', assetId: 'diagram-one',
    }};
    await client.saveDDNAsset(payload);
    await client.setMember('project-one', {
      principal_type: 'role', principal_id: 4, access_level: 'viewer',
    });
    await client.removeMember('project-one', 'role', 4);

    expect(api.put).toHaveBeenNthCalledWith(
      1, '/cdeadmin/api/projects/project-one/assets/diagram-one', payload
    );
    expect(api.put).toHaveBeenNthCalledWith(
      2, '/cdeadmin/api/projects/project-one/members', expect.any(Object)
    );
    expect(api.delete).toHaveBeenCalledWith(
      '/cdeadmin/api/projects/project-one/members/role/4'
    );
  });

  it('normalizes server conflicts without losing the machine error code', async () => {
    api.put.mockRejectedValue({response: {
      status: 409,
      data: {errormsg: 'asset version has changed', code: 'asset_conflict'},
    }});
    await expect(client.saveAsset('project', 'asset', {})).rejects.toEqual(
      expect.objectContaining({
        name: 'ProjectAssetClientError', code: 'asset_conflict', status: 409,
        message: 'asset version has changed',
      })
    );
    await expect(client.saveAsset('project', 'asset', {})).rejects
      .toBeInstanceOf(ProjectAssetClientError);
  });
});
