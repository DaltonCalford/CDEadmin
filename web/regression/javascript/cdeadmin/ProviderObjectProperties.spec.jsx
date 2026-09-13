import {act, render, screen, waitFor} from '@testing-library/react';
import ProviderObjectProperties from '../../../pgadmin/misc/properties/ProviderObjectProperties';
import getApiInstance from '../../../pgadmin/static/js/api_instance';

jest.mock('../../../pgadmin/static/js/api_instance');
jest.mock('../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent', () => ({
  ObjectInspectorSection: ({resource}) => <div>{resource?.display_name}</div>,
}));

describe('provider selection inspector', () => {
  it('uses the target-scoped URL, exact resource identity and generation', async () => {
    const api = {get: jest.fn().mockResolvedValue({data: {data: {
      resource_page: {generation: 'g1'}, visual_admin: {objects: []},
    }}}), post: jest.fn().mockResolvedValue({data: {data: {
      display_name: 'Orders',
    }}})};
    getApiInstance.mockReturnValue(api);
    render(<ProviderObjectProperties nodeData={{cde_workspace_url: '/scope?database_target_id=db1',
      cde_database_target_id: 'db1',
      cde_resource_id: 'table:1', cde_resource_kind: 'table'}} />);
    expect(await screen.findByText('Orders')).toBeVisible();
    expect(api.post).toHaveBeenCalledWith('/scope?database_target_id=db1', {
      action: 'resource_inspect', request: {resource_id: 'table:1', generation: 'g1',
        database_target_id: 'db1'},
    }, expect.objectContaining({signal: expect.any(AbortSignal)}));
  });

  it('never replaces the current selection with a late response', async () => {
    let finishFirst;
    const api = {get: jest.fn().mockResolvedValue({data: {data: {}}}),
      post: jest.fn().mockImplementation((_url, payload) =>
        payload.request.resource_id === 'first' ? new Promise((resolve) => {
          finishFirst = resolve;
        }) : Promise.resolve({data: {data: {display_name: 'Second'}}}))};
    getApiInstance.mockReturnValue(api);
    const node = {cde_workspace_url: '/scope', cde_resource_kind: 'table'};
    const {rerender} = render(<ProviderObjectProperties nodeData={{...node, cde_resource_id: 'first'}} />);
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    rerender(<ProviderObjectProperties nodeData={{...node, cde_resource_id: 'second'}} />);
    expect(await screen.findByText('Second')).toBeVisible();
    await act(async () => finishFirst({data: {data: {display_name: 'First'}}}));
    expect(screen.queryByText('First')).not.toBeInTheDocument();
    expect(screen.getByText('Second')).toBeVisible();
  });

  it('surfaces permission failures instead of pretending metadata is empty', async () => {
    getApiInstance.mockReturnValue({get: jest.fn().mockRejectedValue({
      response: {data: {errormsg: 'Permission denied'}},
    })});
    render(<ProviderObjectProperties nodeData={{cde_workspace_url: '/scope',
      cde_resource_id: 'table:1', cde_resource_kind: 'table'}} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Permission denied');
  });
});
