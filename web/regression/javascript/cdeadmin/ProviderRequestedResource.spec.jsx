/////////////////////////////////////////////////////////////
// CDEadmin - Multi-engine Database Administration
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//////////////////////////////////////////////////////////////

import {act, render, screen} from '@testing-library/react';
import {useLayoutEffect} from 'react';
import ProviderWorkspaceContent, {VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import getApiInstance from '../../../pgadmin/static/js/api_instance';

jest.mock('../../../pgadmin/static/js/api_instance');

describe('exact requested provider resource', () => {
  it('does not offer object controls when an editor has no selected object', () => {
    const post = jest.fn();
    render(<VisualAdministration objectEditor post={post} setError={jest.fn()}
      resources={[]} selectedResource={null} initialResourceKind="table"
      initialOperationId="drop" catalog={{objects: [{resource_kind: 'table',
        title: 'Table', operations: [{operation_id: 'drop', title: 'Drop',
          target_required: true, form: {fields: []}}]}]}} />);
    expect(screen.queryByRole('tablist', {name: 'Selected object operations'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: 'Refresh object properties'})).not.toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });
  it.each([
    ['table', 1], ['table', 2], ['domain', 1], ['domain', 2], ['sequence', 1], ['sequence', 2],
  ])('never substitutes another %s when the requested object is missing (%s remaining)', async (kind, count) => {
    const items = Array.from({length: count}, (_, index) => ({
      resource_id: kind + ':KEEP' + index, resource_kind: kind, display_name: 'KEEP' + index,
    }));
    const api = {get: jest.fn(async () => ({data: {data: {
      endpoint: {provider_id: 'org.cdeadmin.firebird', verified_runtime_family: 'firebird'},
      languages: [], resource_page: {generation: 'g1', items},
      visual_admin: {objects: [{resource_kind: kind, title: kind, operations: [{
        operation_id: 'drop', title: 'Drop', target_required: true,
        confirmation_required: true, form: {fields: []},
      }]}]},
    }}})), post: jest.fn(async (_url, {request}) => ({data: {data:
      items.find(item => item.resource_id === request.resource_id),
    }}))};
    getApiInstance.mockReturnValue(api);
    await act(async () => {
      render(<ProviderWorkspaceContent endpointUrl="/workspace/owned"
        initialTab="administration" initialContext={{resource_id: kind + ':MISSING',
          resource_kind: kind, operation_id: 'drop'}} />);
    });
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls.filter(([, body]) => body.action === 'resource_inspect')).toHaveLength(0);
    expect(screen.queryByRole('button', {name: 'Validate and preview'})).not.toBeInTheDocument();
    expect(screen.getByText(/requested provider object is unavailable/i)).toBeInTheDocument();
  });

  it.each([false, true])('preserves registered database target translation (service=%s)', async (service) => {
    const targetId = 'retained-database-id';
    const database = {resource_id: service ? 'database-service-target:' + targetId : 'database:D',
      resource_kind: 'database', display_name: 'D', extensions: service ? {
        cdeadmin: {database_target_id: targetId, service_scope_only: true},
      } : {}};
    const api = {get: jest.fn(async () => ({data: {data: {
      endpoint: {provider_id: 'org.cdeadmin.firebird'}, languages: [],
      database_targets: {targets: [{target_id: targetId, database: '/owned/D.fdb'}]},
      resource_page: {generation: 'g1', items: [database]},
      visual_admin: {objects: [{resource_kind: 'database', title: 'Database', operations: [{
        operation_id: 'backup', title: 'Backup', target_required: true, form: {fields: []},
      }]}]},
    }}})), post: jest.fn(async () => ({data: {data: database}}))};
    getApiInstance.mockReturnValue(api);
    await act(async () => {
      render(<ProviderWorkspaceContent endpointUrl="/workspace/owned"
        initialTab="administration" initialContext={{resource_id: targetId,
          database_target_id: targetId, resource_kind: 'database', operation_id: 'backup'}} />);
    });
    expect(screen.getByRole('combobox', {name: 'Target resource'})).toHaveTextContent('D');
    expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeEnabled();
    if (service) expect(api.post).not.toHaveBeenCalled();
    else expect(api.post).toHaveBeenCalledWith('/workspace/owned', {action: 'resource_inspect', request: {
      resource_id: database.resource_id, generation: 'g1', database_target_id: targetId,
    }});
  });

  it.each(['response', 'error'])('ignores an obsolete workspace %s after connection change', async (mode) => {
    const old = {resource_id: 'table:OLD', resource_kind: 'table', display_name: 'OLD'};
    const current = {...old, resource_id: 'table:CURRENT', display_name: 'CURRENT'};
    const workspace = item => ({data: {data: {
      endpoint: {provider_id: 'org.cdeadmin.firebird'}, languages: [],
      resource_page: {generation: item.resource_id, items: [item]},
      visual_admin: {objects: [{resource_kind: 'table', title: 'Table', operations: [{
        operation_id: 'alter', title: 'Alter', target_required: true, form: {fields: []},
      }]}]},
    }}});
    let resolveOld, rejectOld;
    const pending = new Promise((resolve, reject) => { resolveOld = resolve; rejectOld = reject; });
    const api = {get: jest.fn(url => url === '/old' ? pending : Promise.resolve(workspace(current))),
      post: jest.fn(async (_url, {request}) => ({data: {data:
        request.resource_id === current.resource_id ? current : old,
      }}))};
    getApiInstance.mockReturnValue(api);
    const context = item => ({resource_id: item.resource_id, resource_kind: 'table', operation_id: 'alter'});
    const {rerender} = render(<ProviderWorkspaceContent endpointUrl="/old"
      initialTab="administration" initialContext={context(old)} />);
    await act(async () => {
      rerender(<ProviderWorkspaceContent endpointUrl="/current"
        initialTab="administration" initialContext={context(current)} />);
    });
    expect(screen.getByRole('combobox', {name: 'Target resource'})).toHaveTextContent('CURRENT');
    await act(async () => {
      if (mode === 'response') resolveOld(workspace(old));
      else rejectOld(new Error('Obsolete connection failed'));
    });
    expect(screen.getByRole('combobox', {name: 'Target resource'})).toHaveTextContent('CURRENT');
    expect(screen.queryByText('Obsolete connection failed')).not.toBeInTheDocument();
    expect(api.post.mock.calls.filter(([url, body]) => url === '/current' &&
      body.action === 'resource_inspect' && body.request.resource_id === old.resource_id)).toHaveLength(0);
  });

  it('does not continue paging a workspace after it is closed', async () => {
    let resolveBootstrap;
    const api = {get: jest.fn(() => new Promise(resolve => { resolveBootstrap = resolve; })),
      post: jest.fn(async () => ({data: {data: {generation: 'g1', items: []}}}))};
    getApiInstance.mockReturnValue(api);
    const {unmount} = render(<ProviderWorkspaceContent endpointUrl="/owned"
      initialTab="administration" initialContext={{resource_id: 'table:MISSING',
        resource_kind: 'table', operation_id: 'drop'}} />);
    unmount();
    await act(async () => {
      resolveBootstrap({data: {data: {resource_page: {
        generation: 'g1', items: [], next_cursor: 'owned-next-page',
      }}}});
    });
    expect(api.post).not.toHaveBeenCalled();
  });

  it.each(['found', 'missing', 'changed-generation'])(
    'resolves the exact requested object across catalog pages: %s', async (mode) => {
      const requested = {resource_id: 'table:REQUESTED', resource_kind: 'table', display_name: 'REQUESTED'};
      const other = {...requested, resource_id: 'table:OTHER', display_name: 'OTHER'};
      const api = {get: jest.fn(async () => ({data: {data: {
        endpoint: {}, languages: [], resource_page: {generation: 'g1', items: [other], next_cursor: 'next'},
        visual_admin: {objects: [{resource_kind: 'table', title: 'Table', operations: [{
          operation_id: 'alter', title: 'Alter', target_required: true, form: {fields: []},
        }]}]},
      }}})), post: jest.fn(async (_url, {action}) => ({data: {data: action === 'resource_page' ? {
        generation: mode === 'changed-generation' ? 'g2' : 'g1',
        items: mode === 'missing' ? [] : [requested],
      } : requested}}))};
      getApiInstance.mockReturnValue(api);
      await act(async () => {
        render(<ProviderWorkspaceContent endpointUrl="/owned" initialTab="administration"
          initialContext={{resource_id: requested.resource_id, resource_kind: 'table', operation_id: 'alter'}} />);
      });
      expect(api.post).toHaveBeenCalledWith('/owned', {action: 'resource_page', request: {
        continuation: 'next', generation: 'g1',
      }});
      if (mode === 'found') {
        expect(screen.getByRole('combobox', {name: 'Target resource'})).toHaveTextContent('REQUESTED');
        expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeEnabled();
      } else {
        expect(screen.queryByRole('button', {name: 'Validate and preview'})).not.toBeInTheDocument();
        expect(screen.getByText(mode === 'missing' ? /requested provider object is unavailable/i :
          /Provider objects changed while resolving/)).toBeInTheDocument();
        expect(api.post.mock.calls.filter(([, body]) => body.action === 'resource_inspect')).toHaveLength(0);
      }
    });

  it.each(['registered-query', 'unregistered-query', 'target-free-create', 'missing-parent',
    'native-database-id', 'ambiguous-database'])(
    'distinguishes connection scope from a native object: %s', async (mode) => {
      const query = mode.endsWith('query');
      const create = mode === 'target-free-create';
      const parent = mode === 'missing-parent';
      const kind = parent ? 'column' : create ? 'table' : 'database';
      const operation = create || parent ? 'create' : 'drop';
      const resource = {resource_id: 'database:D', resource_kind: 'database', display_name: 'D'};
      const items = query || create ? [] : parent ? [{resource_id: 'table:KEEP',
        resource_kind: 'table', display_name: 'KEEP'}] : [resource];
      if (mode === 'ambiguous-database') items.push({...resource, resource_id: 'database:E', display_name: 'E'});
      const api = {get: jest.fn(async () => ({data: {data: {
        endpoint: {}, languages: [{language_profile: 'firebird-sql', title: 'Firebird SQL'}],
        database_targets: {targets: mode === 'unregistered-query' ? [] : [{target_id: 'owned-db'}]},
        resource_page: {generation: 'g1', items},
        visual_admin: {objects: [{resource_kind: kind, title: kind, operations: [{
          operation_id: operation, title: operation, target_required: !create,
          target_resource_kinds: parent ? ['table'] : [kind], form: {fields: []},
        }]}]},
      }}})), post: jest.fn()};
      getApiInstance.mockReturnValue(api);
      const context = query ? {resource_id: 'owned-db'} : create ? {
        resource_kind: kind, operation_id: operation,
      } : parent ? {parent_resource_id: 'table:MISSING', resource_kind: kind, operation_id: operation} : {
        resource_id: mode === 'native-database-id' ? 'database:MISSING' : 'owned-db',
        database_target_id: 'owned-db', resource_kind: kind, operation_id: operation,
      };
      await act(async () => {
        render(<ProviderWorkspaceContent endpointUrl="/owned"
          initialTab={query ? 'studio' : 'administration'} initialContext={context} />);
      });
      if (mode === 'registered-query') {
        expect(screen.getByRole('region', {name: 'Provider query workspace'})).toBeInTheDocument();
      } else if (create) {
        expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeEnabled();
      } else {
        expect(screen.queryByRole('button', {name: 'Validate and preview'})).not.toBeInTheDocument();
        expect(screen.queryByRole('region', {name: 'Provider query workspace'})).not.toBeInTheDocument();
        expect(screen.getByText(/requested provider object is unavailable/i)).toBeInTheDocument();
      }
      expect(api.post).not.toHaveBeenCalled();
    });

  it('hides the prior actionable workspace in the first render of a replacement load', async () => {
    const object = {resource_id: 'table:A', resource_kind: 'table', display_name: 'A'};
    const api = {get: jest.fn(url => url === '/old' ? Promise.resolve({data: {data: {
      endpoint: {}, languages: [], resource_page: {generation: 'g1', items: [object]},
      visual_admin: {objects: [{resource_kind: 'table', title: 'Table', operations: [{
        operation_id: 'alter', title: 'Alter', target_required: true, form: {fields: []},
      }]}]},
    }}}) : new Promise(() => {})), post: jest.fn(async () => ({data: {data: object}}))};
    getApiInstance.mockReturnValue(api);
    const snapshots = [];
    function Observe({url}) {
      useLayoutEffect(() => {
        snapshots.push(screen.queryByRole('button', {name: 'Validate and preview'}));
      }, [url]);
      return <ProviderWorkspaceContent endpointUrl={url} initialTab="administration"
        initialContext={{resource_id: object.resource_id, resource_kind: 'table', operation_id: 'alter'}} />;
    }
    let view;
    await act(async () => { view = render(<Observe url="/old" />); });
    expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeEnabled();
    view.rerender(<Observe url="/new" />);
    expect(snapshots.at(-1)).toBeNull();
    expect(screen.queryByRole('button', {name: 'Validate and preview'})).not.toBeInTheDocument();
  });
});
