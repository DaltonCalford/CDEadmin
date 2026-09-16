/////////////////////////////////////////////////////////////
// CDEadmin - Multi-engine Database Administration
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//////////////////////////////////////////////////////////////

import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';

jest.mock('../../../pgadmin/static/js/api_instance');

describe('provider plan-specific confirmation', () => {
  it.each([
    [true, 'reused-object'], [false, 'reused-object'],
    [true, 'new-id'], [false, 'new-id'],
    [true, 'new-digest'], [false, 'new-digest'],
  ])('requires fresh confirmation for target=%s and %s re-preview', async (targetRequired, mode) => {
    const first = {plan_id: 'first', plan_digest: 'digest-one',
      state: 'ready', execution_available: true};
    const second = mode === 'reused-object' ? first : {...first,
      plan_id: mode === 'new-id' ? 'second' : 'first', plan_digest: 'digest-two'};
    let plans = 0;
    const post = jest.fn(async ({action}) => {
      if (action === 'visual_admin_validate') return {valid: true};
      if (action === 'visual_admin_plan') return ++plans === 1 ? first : second;
      return {provider_result: {accepted: true}};
    });
    const refresh = jest.fn();
    render(<VisualAdministration post={post} setError={jest.fn()}
      onMutationApplied={refresh}
      resources={targetRequired ? [{resource_id: 'table:T', resource_kind: 'table', display_name: 'T'}] : []}
      catalog={{objects: [{resource_kind: 'table', title: 'Table', operations: [{
        operation_id: 'owned-test-operation', title: 'Owned test operation',
        target_required: targetRequired, confirmation_required: true, form: {fields: []},
      }]}]}} />);
    const preview = () => screen.getByRole('button', {name: 'Validate and preview'});
    const apply = () => screen.getByRole('button', {name: 'Apply provider plan'});
    const confirmation = () => screen.getByRole('checkbox',
      {name: 'I confirm this provider-planned operation.'});
    await act(async () => { fireEvent.click(preview()); });
    expect(apply()).toBeDisabled();
    fireEvent.click(confirmation());
    expect(apply()).toBeEnabled();
    await act(async () => { fireEvent.click(preview()); });
    await waitFor(() => expect(plans).toBe(2));
    expect(confirmation()).not.toBeChecked();
    expect(apply()).toBeDisabled();
    fireEvent.click(apply());
    expect(post.mock.calls.filter(([body]) => body.action === 'visual_admin_apply')).toHaveLength(0);
    fireEvent.click(confirmation());
    await act(async () => { fireEvent.click(apply()); });
    expect(post).toHaveBeenLastCalledWith({action: 'visual_admin_apply', request: {
      plan_id: second.plan_id, plan_digest: second.plan_digest, confirmed: true,
    }});
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(apply()).toBeDisabled();
  });

  it.each(['invalid-validation', 'validation-error', 'planning-error'])(
    'does not resurrect confirmation after %s and a fresh preview', async (failure) => {
      let validations = 0;
      let plans = 0;
      const setError = jest.fn();
      const post = jest.fn(async ({action}) => {
        if (action === 'visual_admin_validate') {
          validations++;
          if (validations === 2 && failure === 'invalid-validation') {
            return {valid: false, errors: [{message: 'Rejected draft'}]};
          }
          if (validations === 2 && failure === 'validation-error') throw new Error('Validation unavailable');
          return {valid: true};
        }
        if (action === 'visual_admin_plan') {
          plans++;
          if (plans === 2 && failure === 'planning-error') throw new Error('Planning unavailable');
          return {plan_id: 'plan-' + plans, plan_digest: 'digest-' + plans,
            state: 'ready', execution_available: true};
        }
        return {provider_result: {accepted: true}};
      });
      render(<VisualAdministration post={post} setError={setError} resources={[]}
        catalog={{objects: [{resource_kind: 'database', title: 'Database', operations: [{
          operation_id: 'owned-test-operation', title: 'Owned test operation',
          target_required: false, confirmation_required: true, form: {fields: []},
        }]}]}} />);
      const preview = () => screen.getByRole('button', {name: 'Validate and preview'});
      const apply = () => screen.getByRole('button', {name: 'Apply provider plan'});
      const confirmation = () => screen.getByRole('checkbox',
        {name: 'I confirm this provider-planned operation.'});
      await act(async () => { fireEvent.click(preview()); });
      fireEvent.click(confirmation());
      expect(apply()).toBeEnabled();
      // Explicit withdrawal also remains effective for the same preview.
      fireEvent.click(confirmation());
      expect(apply()).toBeDisabled();
      fireEvent.click(confirmation());
      await act(async () => { fireEvent.click(preview()); });
      expect(apply()).toBeDisabled();
      expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
      if (failure === 'invalid-validation') expect(screen.getByText('Rejected draft')).toBeInTheDocument();
      else expect(setError).toHaveBeenCalledWith(failure === 'validation-error' ?
        'Validation unavailable' : 'Planning unavailable');
      await act(async () => { fireEvent.click(preview()); });
      expect(confirmation()).not.toBeChecked();
      expect(apply()).toBeDisabled();
      expect(post.mock.calls.filter(([body]) => body.action === 'visual_admin_apply')).toHaveLength(0);
    });
});
