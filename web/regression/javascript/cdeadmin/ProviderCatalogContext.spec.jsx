/////////////////////////////////////////////////////////////
// CDEadmin - Multi-engine Database Administration
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//////////////////////////////////////////////////////////////

import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import ProviderWorkspaceContent from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import getApiInstance from '../../../pgadmin/static/js/api_instance';

jest.mock('../../../pgadmin/static/js/api_instance');

describe('provider catalog response ownership', () => {
  describe.each(['endpoint', 'database-target', 'operation'])('%s change', (scope) => {
    it.each([
      ['resource_page', 'response'], ['resource_page', 'error'],
      ['resource_refresh', 'response'], ['resource_refresh', 'error'],
    ])('does not publish obsolete %s %s into another connection', async (action, outcome) => {
      const old = {resource_id: 'table:OLD', resource_kind: 'table', display_name: 'OLD'};
      const current = {...old, resource_id: 'table:CURRENT', display_name: 'CURRENT'};
      const workspace = item => ({data: {data: {
        endpoint: {}, languages: [],
        resource_page: {generation: item.resource_id, items: [item], next_cursor: 'next'},
        visual_admin: {objects: [{resource_kind: 'table', title: 'Table', operations: [{
          operation_id: 'alter', title: 'Alter', target_required: true, form: {fields: []},
        }]}]},
      }}});
      let resolveOld, rejectOld;
      const pending = new Promise((resolve, reject) => { resolveOld = resolve; rejectOld = reject; });
      const oldUrl = '/old';
      const currentUrl = scope === 'endpoint' ? '/current' : oldUrl;
      const oldContext = scope === 'database-target' ? {database_target_id: 'old-db'} : {};
      const currentContext = {resource_id: current.resource_id, resource_kind: 'table', operation_id: 'alter',
        ...(scope === 'database-target' ? {database_target_id: 'current-db'} : {})};
      const api = {
        get: jest.fn().mockResolvedValueOnce(workspace(old)).mockResolvedValue(workspace(current)),
        post: jest.fn((_url, body) => body.action === action ? pending :
          Promise.resolve({data: {data: current}})),
      };
      getApiInstance.mockReturnValue(api);
      let rerender;
      await act(async () => {
        ({rerender} = render(<ProviderWorkspaceContent endpointUrl={oldUrl} initialTab="resources"
          initialContext={oldContext} />));
      });
      await act(async () => {
        fireEvent.click(screen.getByRole('button', {name: action === 'resource_page' ?
          'Load more provider objects' : 'Refresh provider objects'}));
      });
      expect(api.post).toHaveBeenCalledWith(oldUrl, {action, request: {
        ...(action === 'resource_page' ? {continuation: 'next'} : {}),
        generation: old.resource_id,
        ...(scope === 'database-target' ? {database_target_id: 'old-db'} : {}),
      }});
      await act(async () => {
        rerender(<ProviderWorkspaceContent endpointUrl={currentUrl} initialTab="administration"
          initialContext={currentContext} />);
      });
      expect(screen.getByRole('combobox', {name: 'Target resource'})).toHaveTextContent('CURRENT');
      await act(async () => {
        if (outcome === 'response') resolveOld({data: {data: {generation: old.resource_id, items: [old]}}});
        else rejectOld(new Error('Obsolete catalog failed'));
      });
      expect(screen.getByRole('combobox', {name: 'Target resource'})).toHaveTextContent('CURRENT');
      expect(screen.queryByText('Obsolete catalog failed')).not.toBeInTheDocument();
      expect(api.post.mock.calls.filter(([url, body]) => url === currentUrl &&
      body.action === 'resource_inspect' && body.request.resource_id === old.resource_id)).toHaveLength(0);
    });
  });
  describe.each(['replacement', 'closed'])('%s administration task', (destination) => {
    it.each(['apply-response', 'apply-error', 'page-response', 'page-error', 'inspect-response', 'inspect-error'])(
      'does not continue obsolete post-administration catalog work: %s', async (phase) => {
        const old = {resource_id: 'table:OLD', resource_kind: 'table', display_name: 'OLD'};
        const current = {...old, resource_id: 'table:CURRENT', display_name: 'CURRENT'};
        const workspace = item => ({data: {data: {
          endpoint: {}, languages: [], resource_page: {generation: 'g1', items: [item]},
          visual_admin: {objects: [{resource_kind: 'table', title: 'Table', operations: [{
            operation_id: 'alter', title: 'Alter', target_required: true, form: {fields: []},
          }]}]},
        }}});
        let resolveOld, rejectOld;
        const pending = new Promise((resolve, reject) => { resolveOld = resolve; rejectOld = reject; });
        const api = {
          get: jest.fn(async url => workspace(url === '/old' ? old : current)),
          post: jest.fn(async (url, {action, request}) => {
            if (url === '/current') return {data: {data: current}};
            if ((action === 'visual_admin_apply' && phase.startsWith('apply')) ||
              (action === 'resource_page' && phase.startsWith('page')) ||
              (action === 'resource_inspect' && request.generation === 'g2')) return pending;
            return {data: {data: {
              resource_inspect: old,
              visual_admin_validate: {valid: true},
              visual_admin_plan: {plan_id: 'plan', plan_digest: 'digest', state: 'ready', execution_available: true},
              visual_admin_apply: {provider_result: {accepted: true}},
              resource_page: {generation: 'g2', items: []},
            }[action]}};
          }),
        };
        getApiInstance.mockReturnValue(api);
        const context = item => ({resource_id: item.resource_id, resource_kind: 'table', operation_id: 'alter'});
        const {rerender, unmount} = render(<ProviderWorkspaceContent endpointUrl="/old"
          initialTab="administration" initialContext={context(old)} />);
        fireEvent.click(await screen.findByRole('button', {name: 'Validate and preview'}));
        await waitFor(() => expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeEnabled());
        fireEvent.click(screen.getByRole('button', {name: 'Apply provider plan'}));
        await waitFor(() => expect(api.post.mock.calls.some(([, body]) =>
          phase.startsWith('apply') ? body.action === 'visual_admin_apply' :
            phase.startsWith('page') ? body.action === 'resource_page' :
              body.action === 'resource_inspect' && body.request.generation === 'g2')).toBe(true));
        await act(async () => {
          if (destination === 'closed') unmount();
          else rerender(<ProviderWorkspaceContent endpointUrl="/current" initialTab="administration"
            initialContext={context(current)} />);
        });
        const callsBefore = api.post.mock.calls.length;
        await act(async () => {
          if (phase.endsWith('error')) rejectOld(new Error('Obsolete administration refresh failed'));
          else resolveOld({data: {data: phase.startsWith('apply') ? {provider_result: {accepted: true}} :
            phase.startsWith('page') ? {generation: 'g2', items: []} : old}});
        });
        if (destination !== 'closed') {
          expect(screen.getByRole('combobox', {name: 'Target resource'})).toHaveTextContent('CURRENT');
        }
        expect(screen.queryByText(/Obsolete administration refresh failed/)).not.toBeInTheDocument();
        expect(api.post.mock.calls.slice(callsBefore)).toHaveLength(0);
        expect(api.post.mock.calls.filter(([, body]) => body.action === 'visual_admin_apply')).toHaveLength(1);
      });
  });

  it.each(['resource_page', 'resource_refresh'])(
    'owns loading state independently after switching during %s', async (action) => {
      const item = {resource_id: 'table:A', resource_kind: 'table', display_name: 'A'};
      const page = {generation: 'g1', items: [item], next_cursor: 'next'};
      let resolveOld, resolveCurrent;
      const old = new Promise(resolve => { resolveOld = resolve; });
      const current = new Promise(resolve => { resolveCurrent = resolve; });
      getApiInstance.mockReturnValue({
        get: jest.fn(async () => ({data: {data: {endpoint: {}, languages: [], resource_page: page}}})),
        post: jest.fn(url => url === '/old' ? old : current),
      });
      const {rerender} = render(<ProviderWorkspaceContent endpointUrl="/old" />);
      fireEvent.click(await screen.findByRole('button', {name: action === 'resource_page' ?
        'Load more provider objects' : 'Refresh provider objects'}));
      await act(async () => { rerender(<ProviderWorkspaceContent endpointUrl="/current" />); });
      expect(screen.getByRole('button', {name: 'Refresh provider objects'})).toBeEnabled();
      fireEvent.click(screen.getByRole('button', {name: 'Refresh provider objects'}));
      expect(screen.getByRole('button', {name: 'Refresh provider objects'})).toBeDisabled();
      await act(async () => { resolveOld({data: {data: page}}); });
      expect(screen.getByRole('button', {name: 'Refresh provider objects'})).toBeDisabled();
      await act(async () => { resolveCurrent({data: {data: {...page, generation: 'g2'}}}); });
      expect(screen.getByRole('button', {name: 'Refresh provider objects'})).toBeEnabled();
    });

  it.each(['resource_page', 'resource_refresh'])(
    'reports a current %s failure and permits an explicit retry', async (action) => {
      const page = {generation: 'g1', items: [], next_cursor: 'next'};
      const api = {get: jest.fn(async () => ({data: {data: {resource_page: page}}})),
        post: jest.fn().mockRejectedValueOnce(new Error('Current catalog failed'))
          .mockResolvedValue({data: {data: page}})};
      getApiInstance.mockReturnValue(api);
      render(<ProviderWorkspaceContent endpointUrl="/current" />);
      const label = action === 'resource_page' ? 'Load more provider objects' : 'Refresh provider objects';
      fireEvent.click(await screen.findByRole('button', {name: label}));
      expect(await screen.findByText('Current catalog failed')).toBeInTheDocument();
      expect(screen.getByRole('button', {name: label})).toBeEnabled();
      await act(async () => { fireEvent.click(screen.getByRole('button', {name: label})); });
      expect(screen.queryByText('Current catalog failed')).not.toBeInTheDocument();
      expect(api.post).toHaveBeenCalledTimes(2);
    });

  it.each(['apply-response', 'page-response', 'page-error'])(
    'keeps a database-creation catalog reload in its original workspace: %s', async (phase) => {
      const current = {resource_id: 'table:CURRENT', resource_kind: 'table', display_name: 'CURRENT'};
      const form = {form_id: 'owned-create', operation_id: 'create', title: 'Create database',
        supported: true, fields: []};
      const oldWorkspace = {endpoint: {}, languages: [], resource_page: {generation: 'old', items: []},
        database_targets: {targets: [], forms: {form_set_id: 'owned-database-forms',
          lifecycle_resource_kind: 'database', forms: {create: form}}},
        visual_admin: {objects: [{resource_kind: 'database', operations: [{operation_id: 'create',
          title: 'Create database', target_required: false, execution_available: true, form}]}]}};
      const newWorkspace = {endpoint: {}, languages: [], resource_page: {generation: 'new', items: [current]},
        visual_admin: {objects: [{resource_kind: 'table', title: 'Table', operations: [{operation_id: 'alter',
          title: 'Alter', target_required: true, form: {fields: []}}]}]}};
      let resolveOld, rejectOld;
      const pending = new Promise((resolve, reject) => { resolveOld = resolve; rejectOld = reject; });
      const api = {
        get: jest.fn(async url => ({data: {data: url === '/old' ? oldWorkspace : newWorkspace}})),
        post: jest.fn(async (url, {action}) => {
          if (url === '/current') return {data: {data: current}};
          if (action === (phase === 'apply-response' ? 'visual_admin_apply' : 'resource_page')) return pending;
          return {data: {data: {
            visual_admin_validate: {valid: true},
            visual_admin_plan: {plan_id: 'plan', plan_digest: 'digest', state: 'ready', execution_available: true},
            visual_admin_apply: {provider_result: {accepted: true}},
          }[action]}};
        }),
      };
      getApiInstance.mockReturnValue(api);
      const {rerender} = render(<ProviderWorkspaceContent endpointUrl="/old" initialTab="connections"
        initialContext={{database_mode: 'create'}} />);
      fireEvent.click(await screen.findByRole('button', {name: 'Validate and preview'}));
      await waitFor(() => expect(screen.getByRole('button', {name: 'Create database'})).toBeEnabled());
      fireEvent.click(screen.getByRole('button', {name: 'Create database'}));
      await waitFor(() => expect(api.post.mock.calls.some(([, body]) =>
        body.action === (phase === 'apply-response' ? 'visual_admin_apply' : 'resource_page'))).toBe(true));
      await act(async () => {
        rerender(<ProviderWorkspaceContent endpointUrl="/current" initialTab="administration"
          initialContext={{resource_id: current.resource_id, resource_kind: 'table', operation_id: 'alter'}} />);
      });
      const callsBefore = api.post.mock.calls.length;
      await act(async () => {
        if (phase === 'page-error') rejectOld(new Error('Obsolete database catalog failed'));
        else resolveOld({data: {data: phase === 'apply-response' ? {provider_result: {accepted: true}} :
          {generation: 'old-reloaded', items: []}}});
      });
      expect(screen.getByRole('combobox', {name: 'Target resource'})).toHaveTextContent('CURRENT');
      expect(screen.queryByText('Obsolete database catalog failed')).not.toBeInTheDocument();
      expect(api.post.mock.calls.slice(callsBefore)).toHaveLength(0);
    });
});
