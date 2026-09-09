/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import getApiInstance from 'sources/api_instance';
import {ManageTreeNodes} from 'sources/tree/tree_nodes';

jest.mock('sources/api_instance');

describe('CDEadmin provider tree hierarchy', () => {
  it('loads retained databases from the endpoint-owned child URL', async () => {
    const get = jest.fn().mockResolvedValue({data: {data: [{
      id: 'cde_database_target_example',
      _id: 'database-target-id',
      _pid: 7,
      _type: 'cde_database_target',
      label: 'cdeadmin_demo.fdb',
      inode: true,
      children_url: '/browser/server/cde_workspace/1/7?navigator=token',
    }]}});
    getApiInstance.mockReturnValue({get});
    const nodes = new ManageTreeNodes();
    await nodes.addNode(null, '/browser/server-7', {
      id: 'server-7',
      _id: 7,
      _pid: 1,
      _type: 'server',
      label: 'localhost',
      inode: true,
      cde_endpoint: true,
      children_url: '/browser/server/children/1/7',
    });

    const children = await nodes.readNode('/browser/server-7');

    expect(get).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith(
      '/browser/server/children/1/7', {timeout: 30000},
    );
    expect(children).toHaveLength(1);
    expect(children[0].metadata.data).toMatchObject({
      _type: 'cde_database_target',
      label: 'cdeadmin_demo.fdb',
    });
  });

  it('returns already-loaded directory children without another request', async () => {
    const get = jest.fn();
    getApiInstance.mockReturnValue({get});
    const nodes = new ManageTreeNodes();
    await nodes.addNode(null, '/browser/firebird', {
      id: 'firebird', _id: 'firebird', _pid: null,
      _type: 'engine_type', label: 'Firebird', inode: true,
    });
    await nodes.addNode('/browser/firebird', '/browser/firebird/localhost', {
      id: 'localhost', _id: 7, _pid: 1,
      _type: 'server', label: 'localhost', inode: true,
    });

    const children = await nodes.readNode('/browser/firebird');

    expect(children).toHaveLength(1);
    expect(get).not.toHaveBeenCalled();
  });
});
