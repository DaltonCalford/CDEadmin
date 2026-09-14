import {fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import catalog from '../../../pgadmin/cdeadmin/visual_admin/portfolio_catalog.json';

describe('Firebird secondary database storage files', () => {
  let height;
  beforeEach(() => { height = window.innerHeight; window.innerHeight = 1200; });
  afterEach(() => { window.innerHeight = height; });

  it('preserves primary identity and pairs secondary capacities through edits', async () => {
    const post = jest.fn(async ({action}) => action === 'visual_admin_validate' ?
      {valid: true} : {plan_id: 'owned', plan_digest: 'd', state: 'ready', execution_available: true});
    render(<VisualAdministration focused resources={[]} post={post} setError={jest.fn()}
      initialResourceKind="database" initialOperationId="restore_logical"
      catalog={{objects: [{resource_kind: 'database', title: 'Database', operations: [{
        operation_id: 'restore_logical', title: 'Restore', target_required: false,
        form: catalog.forms.firebird_restore_logical,
      }]}]}} />);
    fireEvent.change(screen.getByLabelText(/Backup filename on the Firebird server/),
      {target: {value: 'backup.fbk'}});
    fireEvent.change(screen.getByLabelText(/Restored database filename on the Firebird server/),
      {target: {value: 'primary.fdb'}});
    expect(screen.queryByRole('group', {name: 'Secondary database files in order'})).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Primary database file allocation/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', {name: 'Restore database across multiple files'}));
    const primary = screen.getByLabelText(/Primary database file allocation/);
    expect(primary).toHaveValue(256);
    expect(primary).toHaveAttribute('min', '1');
    expect(primary).toHaveAttribute('max', '2147483647');
    const list = screen.getByRole('group', {name: 'Secondary database files in order'});
    expect(within(list).getByText(/database storage files, not backup volumes/)).toBeInTheDocument();
    for (let i = 0; i < 3; i++) fireEvent.click(within(list).getByRole('button', {name: 'Add Secondary database files in order item'}));
    const names = within(list).getAllByLabelText(/Database filename/);
    const sizes = within(list).getAllByRole('spinbutton');
    ['secondary-one.fdb', 'secondary-two.fdb', 'last.fdb'].forEach((name, i) =>
      fireEvent.change(names[i], {target: {value: name}}));
    fireEvent.change(sizes[0], {target: {value: '64'}});
    fireEvent.change(sizes[1], {target: {value: '128'}});
    expect(sizes[0]).toHaveAttribute('min', '1');
    fireEvent.click(within(list).getByRole('button', {name: 'Secondary database files in order 2: Move up'}));
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeEnabled());
    const draft = post.mock.calls.find(([body]) => body.action === 'visual_admin_plan')[0].request.draft;
    expect(draft.restore_database).toBe('primary.fdb');
    expect(draft.primary_file_pages).toBe(256);
    expect(draft.database_file_volumes).toEqual([
      {filename: 'secondary-two.fdb', pages: 128},
      {filename: 'secondary-one.fdb', pages: 64},
      {filename: 'last.fdb', pages: ''},
    ]);
    expect(draft).not.toHaveProperty('database_file_pages');
    expect(draft).not.toHaveProperty('additional_database_files');
    fireEvent.change(primary, {target: {value: '512'}});
    expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeDisabled();
    fireEvent.click(within(list).getByRole('button', {name: 'Secondary database files in order 1: Remove'}));
    fireEvent.click(screen.getByRole('checkbox', {name: 'Restore database across multiple files'}));
    expect(screen.queryByRole('group', {name: 'Secondary database files in order'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(post.mock.calls.filter(([body]) => body.action === 'visual_admin_plan')).toHaveLength(2));
    const single = post.mock.calls.filter(([body]) => body.action === 'visual_admin_plan')[1][0].request.draft;
    expect(single.restore_database).toBe('primary.fdb');
    expect(single).not.toHaveProperty('primary_file_pages');
    expect(single).not.toHaveProperty('database_file_volumes');
  });
});
