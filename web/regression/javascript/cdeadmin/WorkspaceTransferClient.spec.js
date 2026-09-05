/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {WorkspaceTransferClient} from
  'sources/cdeadmin_ui/workspace/WorkspaceTransferClient';

describe('CDEadmin workspace transfer API client', () => {
  it('uses owner-scoped resource URLs and unwraps response data', async () => {
    const api = {
      get: jest.fn(()=>Promise.resolve({data: {data: {revision: 3}}})),
      put: jest.fn(()=>Promise.resolve({data: {data: {revision: 0}}})),
      post: jest.fn(()=>Promise.resolve({
        data: {data: {status: 'prepared'}},
      })),
    };
    const client = new WorkspaceTransferClient(api);

    await expect(client.state('workspace one')).resolves.toEqual({
      revision: 3,
    });
    expect(api.get).toHaveBeenCalledWith(
      '/cdeadmin/api/workspaces/workspace%20one'
    );
    await client.registerTool('workspace-one', 'query:1', {schema: 'v1'});
    expect(api.put).toHaveBeenCalledWith(
      '/cdeadmin/api/workspaces/workspace-one/tools/query%3A1',
      {descriptor: {schema: 'v1'}}
    );
    await client.acknowledge('move.proof', {
      restored_tool_instance_id: 'query:1', checkpoint_revision: 1,
    });
    expect(api.post).toHaveBeenCalledWith(
      '/cdeadmin/api/workspace-moves/move/acknowledge',
      {restored_tool_instance_id: 'query:1', checkpoint_revision: 1},
      {headers: {'X-CDEadmin-Workspace-Move': 'move.proof'}}
    );
  });
});
