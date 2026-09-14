import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import catalog from '../../../pgadmin/cdeadmin/visual_admin/portfolio_catalog.json';

describe('Firebird physical file policy controls', () => {
  let originalHeight;
  beforeEach(() => {
    originalHeight = window.innerHeight;
    // Shared jsdom setup supplies 800px rectangles rather than real layout.
    window.innerHeight = 1200;
  });
  afterEach(() => { window.innerHeight = originalHeight; });
  it.each([
    ['restore_physical', 'firebird_restore_physical', 'restore_flags', 'Physical restore options'],
    ['fixup_database', 'firebird_fixup_database', 'fixup_flags', 'Fixup options'],
  ])('keeps %s identity policy visual and invalidates changed plans', async (operation, form, field, label) => {
    const post = jest.fn(async ({action}) => action === 'visual_admin_validate' ?
      {valid: true} : {plan_id: 'owned', plan_digest: 'd', state: 'ready',
        execution_available: true});
    render(<VisualAdministration focused resources={[]} post={post} setError={jest.fn()}
      initialResourceKind="database" initialOperationId={operation}
      catalog={{objects: [{resource_kind: 'database', title: 'Database', operations: [{
        operation_id: operation, title: 'File operation', target_required: false,
        form: catalog.forms[form],
      }]}]}} />);
    expect(screen.getByText(/Default: new database GUID and replication counter reset to zero/))
      .toBeInTheDocument();
    expect(screen.getByText(/does not secure exclusive server-wide access/)).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole('combobox', {name: label}));
    expect(screen.queryByRole('option', {name: 'Sequence mode'})).not.toBeInTheDocument();
    const preserve = screen.getByRole('option', {name: 'Preserve database GUID and replication counter'});
    fireEvent.click(preserve);
    if (operation === 'fixup_database') {
      expect(screen.queryByRole('option', {name: /Apply increments/})).not.toBeInTheDocument();
    } else {
      expect(screen.getByRole('option', {name: 'Apply increments to an existing offline database'}))
        .toBeInTheDocument();
    }
    fireEvent.keyDown(screen.getByRole('listbox'), {key: 'Escape'});
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeEnabled());
    expect(post.mock.calls.find(([body]) => body.action === 'visual_admin_plan')[0]
      .request.draft[field]).toEqual(['SEQUENCE']);
    fireEvent.mouseDown(screen.getByRole('combobox', {name: label}));
    fireEvent.click(screen.getByRole('option', {name: 'Preserve database GUID and replication counter'}));
    fireEvent.keyDown(screen.getByRole('listbox'), {key: 'Escape'});
    expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(post.mock.calls.filter(([body]) => body.action === 'visual_admin_plan'))
      .toHaveLength(2));
    expect(post.mock.calls.filter(([body]) => body.action === 'visual_admin_plan')[1][0]
      .request.draft).not.toHaveProperty(field);
  });
});
