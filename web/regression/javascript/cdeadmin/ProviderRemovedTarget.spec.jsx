/////////////////////////////////////////////////////////////
// CDEadmin - Multi-engine Database Administration
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//////////////////////////////////////////////////////////////

import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';

jest.mock('../../../pgadmin/static/js/api_instance');

describe('provider task target removal', () => {
  let originalHeight;
  beforeEach(() => {
    // The shared virtual-grid fixture gives every element an 800px height.
    // Supply a fitting viewport when exercising the real MUI selector.
    originalHeight = window.innerHeight;
    window.innerHeight = 1200;
  });
  afterEach(() => { window.innerHeight = originalHeight; });
  it.each([false, true])('does not select another object after removal (explicit selection=%s)', async (explicit) => {
    const original = {resource_id: 'table:OWNED', resource_kind: 'table', display_name: 'OWNED'};
    const remaining = {resource_id: 'table:KEEP', resource_kind: 'table', display_name: 'KEEP'};
    const catalog = {objects: [{resource_kind: 'table', title: 'Table', operations: [{
      operation_id: 'drop', title: 'Drop', target_required: true,
      confirmation_required: true, form: {fields: []},
    }]}]};
    const post = jest.fn(async ({action}) => {
      if (action === 'resource_inspect') return original;
      if (action === 'visual_admin_validate') return {valid: true};
      return {plan_id: 'owned-plan', plan_digest: 'owned-digest', state: 'ready', execution_available: true};
    });
    const props = {catalog, post, setError: jest.fn()};
    const {rerender} = render(<VisualAdministration {...props}
      resources={[original, remaining]} selectedResource={explicit ? original : null} />);
    const preview = () => screen.getByRole('button', {name: 'Validate and preview'});
    const apply = () => screen.getByRole('button', {name: 'Apply provider plan'});
    await waitFor(() => expect(preview()).toBeEnabled());
    await act(async () => { fireEvent.click(preview()); });
    fireEvent.click(screen.getByRole('checkbox', {name: 'I confirm this provider-planned operation.'}));
    expect(apply()).toBeEnabled();
    rerender(<VisualAdministration {...props} resources={[remaining]} selectedResource={null} />);
    await waitFor(() => expect(screen.queryByLabelText('Provider plan preview')).not.toBeInTheDocument());
    expect(screen.getByRole('combobox', {name: 'Target resource'})).not.toHaveTextContent('KEEP');
    expect(screen.getByText(/Select an object explicitly before preparing another operation/)).toBeInTheDocument();
    expect(preview()).toBeDisabled();
    expect(apply()).toBeDisabled();
    const count = post.mock.calls.length;
    fireEvent.click(preview());
    fireEvent.click(apply());
    expect(post).toHaveBeenCalledTimes(count);
    // Reappearing on a later catalog page is not a new user selection.
    rerender(<VisualAdministration {...props} resources={[original, remaining]} selectedResource={null} />);
    expect(screen.getByRole('combobox', {name: 'Target resource'})).not.toHaveTextContent('OWNED');
    expect(preview()).toBeDisabled();
    // A new object becomes actionable only after a deliberate user selection.
    fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Target resource'}));
    fireEvent.click(await screen.findByRole('option', {name: 'KEEP'}));
    await act(async () => { fireEvent.click(preview()); });
    expect(post).toHaveBeenLastCalledWith({action: 'visual_admin_plan', request: {
      resource_kind: 'table', operation_id: 'drop', target_resource: remaining, draft: {},
    }});
    expect(screen.getByRole('checkbox', {name: 'I confirm this provider-planned operation.'})).not.toBeChecked();
    expect(apply()).toBeDisabled();
  });

  it('accepts explicit target changes and initializes newly opened scopes', async () => {
    const original = {resource_id: 'table:A', resource_kind: 'table', display_name: 'A'};
    const next = {...original, resource_id: 'table:B', display_name: 'B'};
    const catalog = {objects: [{resource_kind: 'table', title: 'Table', operations: [{
      operation_id: 'alter', title: 'Alter', target_required: true, form: {fields: []},
    }]}]};
    const post = jest.fn(async ({request}) => request.resource_id === original.resource_id ? original : next);
    const props = {catalog, post, setError: jest.fn()};
    const {rerender} = render(<VisualAdministration {...props} resources={[]} />);
    expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeDisabled();
    rerender(<VisualAdministration {...props} resources={[original, next]} />);
    expect(screen.getByRole('combobox', {name: 'Target resource'})).toHaveTextContent('A');
    rerender(<VisualAdministration {...props} resources={[next]} />);
    expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeDisabled();
    rerender(<VisualAdministration {...props} resources={[next]} selectedResource={next} />);
    await waitFor(() => expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeEnabled());
    expect(screen.getByRole('combobox', {name: 'Target resource'})).toHaveTextContent('B');
    const otherConnection = jest.fn(async () => original);
    rerender(<VisualAdministration {...props} post={otherConnection} resources={[original]} selectedResource={null} />);
    expect(screen.getByRole('combobox', {name: 'Target resource'})).toHaveTextContent('A');
    expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeEnabled();
  });

  it('keeps a focused object pinned to its inspected defaults', async () => {
    const original = {resource_id: 'table:A', resource_kind: 'table', display_name: 'A'};
    const next = {...original, resource_id: 'table:B', display_name: 'B'};
    const post = jest.fn(async ({action}) => action === 'resource_inspect' ? original : {valid: true});
    render(<VisualAdministration post={post} setError={jest.fn()}
      resources={[original, next]} selectedResource={original}
      catalog={{objects: [{resource_kind: 'table', title: 'Table', operations: [{
        operation_id: 'alter', title: 'Alter', target_required: true, form: {fields: []},
      }]}]}} />);
    await waitFor(() => expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeEnabled());
    fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Target resource'}));
    fireEvent.click(await screen.findByRole('option', {name: 'B'}));
    expect(screen.getByRole('combobox', {name: 'Target resource'})).toHaveTextContent('A');
    await act(async () => { fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'})); });
    expect(post).toHaveBeenLastCalledWith({action: 'visual_admin_plan', request: {
      resource_kind: 'table', operation_id: 'alter', target_resource: original, draft: {},
    }});
  });

  it('initializes a different operation target kind without inheriting the removed selection', () => {
    const table = {resource_id: 'table:A', resource_kind: 'table', display_name: 'A'};
    const view = {resource_id: 'view:V', resource_kind: 'view', display_name: 'V'};
    const catalog = kind => ({objects: [{resource_kind: 'table', title: 'Table', operations: [{
      operation_id: 'inspect', title: 'Inspect', target_required: true,
      target_resource_kinds: [kind], form: {fields: []},
    }]}]});
    const props = {post: jest.fn(), setError: jest.fn()};
    const {rerender} = render(<VisualAdministration {...props} catalog={catalog('table')} resources={[table, view]} />);
    expect(screen.getByRole('combobox', {name: 'Target resource'})).toHaveTextContent('A');
    rerender(<VisualAdministration {...props} catalog={catalog('table')} resources={[view]} />);
    expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeDisabled();
    rerender(<VisualAdministration {...props} catalog={catalog('view')} resources={[view]} />);
    expect(screen.getByRole('combobox', {name: 'Target resource'})).toHaveTextContent('V');
    expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeEnabled();
  });
});
