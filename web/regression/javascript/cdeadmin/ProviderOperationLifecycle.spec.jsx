/////////////////////////////////////////////////////////////
// ScratchRobin CDE Administrator — provider task lifecycle
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
/////////////////////////////////////////////////////////////

import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import {ModalCloseGuardContext} from '../../../pgadmin/static/js/helpers/ModalCloseGuard';

const readyPlan = {plan_id: 'owned-plan', plan_digest: 'digest',
  state: 'ready', execution_available: true};
const catalog = {objects: [{resource_kind: 'database', title: 'Database',
  operations: [{operation_id: 'database_statistics', title: 'Database statistics',
    target_required: false, form: {fields: []}},
  {operation_id: 'backup', title: 'Backup', target_required: false,
    form: {fields: []}}]},
{resource_kind: 'table', title: 'Table', operations: [{operation_id: 'inspect',
  title: 'Inspect table', target_required: false, form: {fields: []}}]}]};

function pending() {
  let resolve, reject;
  const promise = new Promise((accept, decline) => {
    resolve = accept;
    reject = decline;
  });
  return {promise, resolve, reject};
}

describe('provider operation close and admission safety', () => {
  it.each([
    ['visual_admin_validate', false], ['visual_admin_validate', true],
    ['visual_admin_plan', false], ['visual_admin_plan', true],
    ['visual_admin_apply', false], ['visual_admin_apply', true],
    ['follow_up', false], ['follow_up', true],
  ])('retains the task during %s (failure=%s)', async (phase, failure) => {
    const request = pending();
    const guards = new Set();
    const register = (guard) => {
      guards.add(guard);
      return () => guards.delete(guard);
    };
    const setError = jest.fn();
    const post = jest.fn(async ({action}) => {
      if (action === phase) return request.promise;
      if (action === 'visual_admin_validate') return {valid: true};
      if (action === 'visual_admin_plan') return readyPlan;
      return {provider_result: {accepted: true}};
    });
    const followUp = jest.fn(() => phase === 'follow_up' ?
      request.promise : Promise.resolve());
    const view = render(<ModalCloseGuardContext.Provider value={register}>
      <VisualAdministration catalog={catalog} resources={[]}
        post={post} setError={setError} onMutationApplied={followUp} />
    </ModalCloseGuardContext.Provider>);
    expect(guards.size).toBe(1);
    const guard = [...guards][0];
    expect(guard()).toBe(true);
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    if (['visual_admin_apply', 'follow_up'].includes(phase)) {
      await waitFor(() => expect(screen.getByRole('button',
        {name: 'Apply provider plan'})).toBeEnabled());
      fireEvent.click(screen.getByRole('button', {name: 'Apply provider plan'}));
    }
    await waitFor(() => {
      if (phase === 'follow_up') expect(followUp).toHaveBeenCalledTimes(1);
      else expect(post.mock.calls.some(([body]) => body.action === phase)).toBe(true);
    });
    act(() => expect(guard()).toBe(false));
    expect(screen.getByLabelText('Provider task close deferred')).toHaveTextContent(
      'Closing a task does not cancel or roll back a native operation.');
    for (const name of ['Database', 'Table', 'Database statistics', 'Backup',
      'Validate and preview', 'Apply provider plan']) {
      expect(screen.getByRole('button', {name, exact: true})).toBeDisabled();
    }
    const count = post.mock.calls.length;
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    fireEvent.click(screen.getByRole('button', {name: 'Apply provider plan'}));
    expect(post).toHaveBeenCalledTimes(count);
    await act(async () => {
      if (failure) request.reject(new Error('Native request failed'));
      else request.resolve(phase === 'visual_admin_validate' ? {valid: true} :
        phase === 'visual_admin_plan' ? readyPlan : {provider_result: {accepted: true}});
    });
    expect(guard()).toBe(true);
    expect(screen.queryByLabelText('Provider task close deferred'))
      .not.toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeEnabled();
    if (['visual_admin_apply', 'follow_up'].includes(phase)) {
      expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeDisabled();
      expect(post.mock.calls.filter(([body]) => body.action === 'visual_admin_apply'))
        .toHaveLength(1);
    }
    if (failure && phase === 'follow_up') {
      expect(screen.getByText(/object metadata could not be reloaded/))
        .toHaveTextContent('Do not repeat the operation.');
    }
    view.unmount();
    expect(guards.size).toBe(0);
  });
});
