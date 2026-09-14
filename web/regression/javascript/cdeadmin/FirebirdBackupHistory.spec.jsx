/////////////////////////////////////////////////////////////
// ScratchRobin CDE Administrator — native backup history form
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
/////////////////////////////////////////////////////////////
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import catalog from '../../../pgadmin/cdeadmin/visual_admin/portfolio_catalog.json';

describe('Firebird physical backup history controls', () => {
  let originalHeight;
  beforeEach(() => {
    originalHeight = window.innerHeight;
    // setup-jest supplies 800px element rectangles, not real layout.
    window.innerHeight = 1200;
  });
  afterEach(() => { window.innerHeight = originalHeight; });
  it.each(['NATIVE', 'ON', 'OFF'])('previews the exact %s backup I/O policy', async (mode) => {
    const post = jest.fn(async ({action}) => action === 'visual_admin_validate' ?
      {valid: true} : {plan_id: 'owned', plan_digest: 'd', state: 'ready', execution_available: true});
    render(<VisualAdministration focused resources={[]} post={post} setError={jest.fn()}
      initialResourceKind="database" initialOperationId="backup_physical"
      catalog={{objects: [{resource_kind: 'database', title: 'Database', operations: [{
        operation_id: 'backup_physical', title: 'Physical backup', target_required: false,
        form: catalog.forms.firebird_backup_physical,
      }]}]}} />);
    fireEvent.change(screen.getByRole('textbox', {name: /Physical backup filename/}),
      {target: {value: '/owned/backup.nbk'}});
    const policy = screen.getByRole('combobox', {name: /Backup read I\/O policy/});
    expect(policy).toHaveTextContent('Native default');
    fireEvent.mouseDown(policy);
    fireEvent.click(screen.getByRole('option', {name: mode === 'NATIVE' ?
      'Native default' : `Direct reads ${mode}`}));
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeEnabled());
    const draft = post.mock.calls.find(([body]) => body.action === 'visual_admin_plan')[0].request.draft;
    expect(draft.direct_io_mode).toBe(mode);
    expect(draft).not.toHaveProperty('direct_io');
  });

  it('does not offer an ineffective direct-I/O field on physical restore', () => {
    render(<VisualAdministration focused resources={[]} post={jest.fn()} setError={jest.fn()}
      initialResourceKind="database" initialOperationId="restore_physical"
      catalog={{objects: [{resource_kind: 'database', title: 'Database', operations: [{
        operation_id: 'restore_physical', title: 'Physical restore', target_required: false,
        form: catalog.forms.firebird_restore_physical,
      }]}]}} />);
    expect(screen.queryByRole('combobox', {name: /I\/O/})).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', {name: /I\/O/})).not.toBeInTheDocument();
    expect(catalog.forms.firebird_restore_physical.fields.some((field) =>
      ['direct_io', 'direct_io_mode'].includes(field.field_id))).toBe(false);
  });
  it.each(['ROWS', 'DAYS'])('previews exact %s retention and omits it when disabled', async (unit) => {
    const post = jest.fn(async ({action}) => action === 'visual_admin_validate' ?
      {valid: true} : {plan_id: 'owned', plan_digest: 'd', state: 'ready',
        execution_available: true});
    render(<VisualAdministration focused resources={[]} post={post}
      setError={jest.fn()} initialResourceKind="database" initialOperationId="backup_physical"
      catalog={{objects: [{resource_kind: 'database', title: 'Database', operations: [{
        operation_id: 'backup_physical', title: 'Physical backup', target_required: false,
        form: catalog.forms.firebird_backup_physical,
      }]}]}} />);
    expect(screen.queryByRole('combobox', {name: /Keep backup history by/}))
      .not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox',
      {name: /Physical backup filename on the Firebird server/}),
    {target: {value: '/owned/backup.nbk'}});
    fireEvent.click(screen.getByRole('checkbox', {name: 'Clean backup history after backup'}));
    expect(screen.getByText('Pruning can remove level/GUID lookup records. Backup files are kept.'))
      .toBeInTheDocument();
    expect(screen.getByRole('spinbutton', {name: /History retention count/})).toHaveValue(1);
    fireEvent.mouseDown(screen.getByRole('combobox', {name: /Keep backup history by/}));
    fireEvent.click(screen.getByRole('option', {name: unit === 'ROWS' ?
      'Newest rows (timestamp cutoff)' : 'Calendar days including today'}));
    fireEvent.change(screen.getByRole('spinbutton', {name: /History retention count/}),
      {target: {value: '7'}});
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeEnabled());
    expect(post).toHaveBeenCalledWith(expect.objectContaining({
      action: 'visual_admin_plan', request: expect.objectContaining({
        draft: expect.objectContaining({clean_history: true,
          history_keep_unit: unit, history_keep_value: 7}),
      }),
    }));
    fireEvent.click(screen.getByRole('checkbox', {name: 'Clean backup history after backup'}));
    expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeDisabled();
    expect(screen.queryByRole('spinbutton', {name: /History retention count/}))
      .not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(post.mock.calls.filter(([body]) =>
      body.action === 'visual_admin_plan')).toHaveLength(2));
    const draft = post.mock.calls.filter(([body]) =>
      body.action === 'visual_admin_plan')[1][0].request.draft;
    expect(draft.clean_history).toBe(false);
    expect(draft).not.toHaveProperty('history_keep_unit');
    expect(draft).not.toHaveProperty('history_keep_value');
  });
});
