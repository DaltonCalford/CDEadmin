import {fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import PropTypes from 'prop-types';
import {VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import catalog from '../../../pgadmin/cdeadmin/visual_admin/portfolio_catalog.json';

const label = 'Ordered backup files';
const form = catalog.forms.firebird_restore_physical;

function RestoreForm({post}) {
  return <VisualAdministration focused resources={[]} post={post} setError={jest.fn()}
    initialResourceKind="database" initialOperationId="restore_physical"
    catalog={{objects: [{resource_kind: 'database', title: 'Database', operations: [{
      operation_id: 'restore_physical', title: 'Physical restore', target_required: false, form,
    }]}]}} />;
}
RestoreForm.propTypes = {post: PropTypes.func.isRequired};

describe('Firebird ordered restore files', () => {
  let height;
  beforeEach(() => { height = window.innerHeight; window.innerHeight = 1200; });
  afterEach(() => { window.innerHeight = height; });

  it('adds, edits, reorders and removes files and previews the exact native array', async () => {
    const post = jest.fn(async ({action}) => action === 'visual_admin_validate' ?
      {valid: true} : {plan_id: 'owned', plan_digest: 'd', state: 'ready', execution_available: true});
    render(<RestoreForm post={post} />);
    const group = screen.getByRole('group', {name: label});
    expect(group).toHaveAccessibleDescription(form.fields[0].help);
    expect(within(group).queryByRole('textbox')).not.toBeInTheDocument();
    const add = screen.getByRole('button', {name: `Add ${label} item`});
    for (const [index, path] of ['/owned/full.nbk', '/owned/next.nbk', '/owned/unused.nbk'].entries()) {
      fireEvent.click(add);
      fireEvent.change(screen.getByRole('textbox', {name: `${label} ${index + 1}`}),
        {target: {value: path}});
    }
    expect(screen.getByRole('button', {name: `${label} 1: Move up`})).toBeDisabled();
    expect(screen.getByRole('button', {name: `${label} 3: Move down`})).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: `${label} 2: Move up`}));
    expect(screen.getByRole('textbox', {name: `${label} 1`})).toHaveValue('/owned/next.nbk');
    fireEvent.click(screen.getByRole('button', {name: `${label} 1: Move down`}));
    fireEvent.click(screen.getByRole('button', {name: `${label} 3: Remove`}));
    fireEvent.change(screen.getByRole('textbox', {name: /Restored database filename/}),
      {target: {value: '/owned/restored.fdb'}});
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeEnabled());
    const draft = post.mock.calls.find(([body]) => body.action === 'visual_admin_plan')[0].request.draft;
    expect(draft.backup_files).toEqual(['/owned/full.nbk', '/owned/next.nbk']);
    expect(draft.restore_database).toBe('/owned/restored.fdb');
    fireEvent.click(screen.getByRole('button', {name: `${label} 2: Move up`}));
    expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeDisabled();
    const toolbar = screen.getByRole('button', {name: `${label} 1: Move up`}).parentElement;
    expect(getComputedStyle(toolbar).flexWrap).toBe('wrap');
  });

  it('prevents list changes while validation is in flight', async () => {
    let finish;
    const post = jest.fn(() => new Promise((resolve) => { finish = resolve; }));
    render(<RestoreForm post={post} />);
    fireEvent.click(screen.getByRole('button', {name: `Add ${label} item`}));
    fireEvent.change(screen.getByRole('textbox', {name: `${label} 1`}),
      {target: {value: '/owned/full.nbk'}});
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const group = screen.getByRole('group', {name: label});
    for (const button of within(group).getAllByRole('button')) expect(button).toBeDisabled();
    expect(within(group).getByRole('textbox')).toBeDisabled();
    finish({valid: false, errors: [{message: 'Test validation denial'}]});
    await waitFor(() => expect(screen.getByRole('button', {name: `Add ${label} item`})).toBeEnabled());
    expect(within(group).getByRole('textbox')).toHaveValue('/owned/full.nbk');
  });
});
