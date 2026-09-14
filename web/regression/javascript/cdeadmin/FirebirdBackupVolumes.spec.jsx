import {fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import catalog from '../../../pgadmin/cdeadmin/visual_admin/portfolio_catalog.json';

describe('Firebird paired backup volumes', () => {
  let height;
  beforeEach(() => { height = window.innerHeight; window.innerHeight = 1200; });
  afterEach(() => { window.innerHeight = height; });
  it('keeps volume filenames paired with capacities and invalidates changes', async () => {
    const post = jest.fn(async ({action}) => action === 'visual_admin_validate' ?
      {valid: true} : {plan_id: 'owned', plan_digest: 'd', state: 'ready', execution_available: true});
    render(<VisualAdministration focused resources={[]} post={post} setError={jest.fn()}
      initialResourceKind="database" initialOperationId="backup_logical"
      catalog={{objects: [{resource_kind: 'database', title: 'Database', operations: [{
        operation_id: 'backup_logical', title: 'Logical backup', target_required: false,
        form: catalog.forms.firebird_backup_logical,
      }]}]}} />);
    fireEvent.change(screen.getByLabelText(/Backup filename on the Firebird server/),
      {target: {value: 'single.fbk'}});
    expect(screen.queryByRole('group', {name: 'Ordered backup volumes'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', {name: 'Split backup across volumes'}));
    expect(screen.queryByLabelText(/Backup filename on the Firebird server/)).not.toBeInTheDocument();
    const list = screen.getByRole('group', {name: 'Ordered backup volumes'});
    expect(within(list).getByText(/Moving a volume also moves its capacity/)).toBeInTheDocument();
    for (let i = 0; i < 3; i++) fireEvent.click(within(list).getByRole('button', {name: 'Add Ordered backup volumes item'}));
    const names = within(list).getAllByLabelText(/Volume filename/);
    const capacities = within(list).getAllByRole('spinbutton');
    ['first.fbk', 'middle.fbk', 'last.fbk'].forEach((name, i) =>
      fireEvent.change(names[i], {target: {value: name}}));
    fireEvent.change(capacities[0], {target: {value: '2048'}});
    fireEvent.change(capacities[1], {target: {value: '4096'}});
    expect(capacities[0]).toHaveAttribute('min', '2048');
    expect(capacities[0]).toHaveAttribute('max', '2147483647');
    fireEvent.click(within(list).getByRole('button', {name: 'Ordered backup volumes 2: Move up'}));
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeEnabled());
    const draft = post.mock.calls.find(([body]) => body.action === 'visual_admin_plan')[0].request.draft;
    expect(draft).not.toHaveProperty('backup_file');
    expect(draft.backup_volumes).toEqual([
      {filename: 'middle.fbk', size_bytes: 4096},
      {filename: 'first.fbk', size_bytes: 2048},
      {filename: 'last.fbk', size_bytes: ''},
    ]);
    fireEvent.click(within(list).getByRole('button', {name: 'Ordered backup volumes 1: Remove'}));
    expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', {name: 'Split backup across volumes'}));
    expect(screen.queryByRole('group', {name: 'Ordered backup volumes'})).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Backup filename on the Firebird server/),
      {target: {value: 'single-new.fbk'}});
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(post.mock.calls.filter(([body]) => body.action === 'visual_admin_plan')).toHaveLength(2));
    const single = post.mock.calls.filter(([body]) => body.action === 'visual_admin_plan')[1][0].request.draft;
    expect(single.backup_file).toBe('single-new.fbk');
    expect(single).not.toHaveProperty('backup_volumes');
  });
});
