import {execFileSync} from 'child_process';
import path from 'path';
import {act, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';

// Exercise the forms actually emitted by the provider, not a copied schema.
const forms = JSON.parse(execFileSync('python3', ['-c',
  'import json; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
  'print(json.dumps({kind + "." + action: ADMINISTRATION._form(kind, action) ' +
  'for kind, actions in [("collation", ["create", "comment", "drop"]), ' +
  '("character-set", ["alter", "comment"])] for action in actions}))',
], {cwd: path.resolve(__dirname, '../../../..'), encoding: 'utf8'}));

async function mount(kind, action, native = {}) {
  const resource = {resource_id: 'owned', resource_kind: kind, display_name: 'UTF8',
    display_path: ['UTF8'], extensions: {firebird: {native: {
      system_object: kind === 'character-set', ...native,
    }}}};
  const post = jest.fn(async ({action: task}) => {
    if (task === 'resource_inspect') return resource;
    if (task === 'visual_admin_validate') return {valid: true};
    return {plan_id: 'owned', plan_digest: 'd', state: 'ready', execution_available: true};
  });
  await act(async () => render(<VisualAdministration focused resources={[resource]} post={post} setError={jest.fn()}
    initialResourceKind={kind} initialOperationId={action} selectedResource={resource}
    catalog={{objects: [{resource_kind: kind, title: kind, operations: [{
      operation_id: action, title: action, target_required: action !== 'create',
      allow_system_target: true, form: forms[`${kind}.${action}`],
    }]}]}} />));
  return post;
}

async function preview(post) {
  fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
  await waitFor(() => expect(post.mock.calls.some(([body]) => body.action === 'visual_admin_plan')).toBe(true));
  return post.mock.calls.filter(([body]) => body.action === 'visual_admin_plan').slice(-1)[0][0].request.draft;
}

function select(label, value) {
  fireEvent.mouseDown(screen.getByLabelText(label));
  fireEvent.click(screen.getByRole('option', {name: value, exact: true}));
}

describe('Firebird character metadata forms', () => {
  let height;
  beforeEach(() => { height = window.innerHeight; window.innerHeight = 1200; });
  afterEach(() => { window.innerHeight = height; });
  it('submits native flags and ordered specific attributes with empty removal values', async () => {
    const post = await mount('collation', 'create');
    fireEvent.change(screen.getByLabelText(/Collation name/), {target: {value: 'Owned'}});
    fireEvent.change(screen.getByLabelText(/Existing collation/), {target: {value: 'UNICODE'}});
    select(/Space padding/, 'No Pad');
    select(/Case comparison/, 'Insensitive');
    select(/Accent comparison/, 'Insensitive');
    const list = screen.getByRole('group', {name: 'Specific collation attributes'});
    for (let i = 0; i < 2; i++) fireEvent.click(within(list).getByRole('button', {name: 'Add Specific collation attributes item'}));
    within(list).getAllByLabelText(/Attribute name/).forEach(input =>
      fireEvent.change(input, {target: {value: 'NUMERIC-SORT'}}));
    fireEvent.change(within(list).getAllByLabelText(/Attribute value/)[0], {target: {value: '1'}});
    const draft = await preview(post);
    expect(draft).toMatchObject({name: 'Owned', character_set: 'UTF8', base_collation: 'UNICODE',
      padding: 'NO_PAD', case_sensitivity: 'INSENSITIVE', accent_sensitivity: 'INSENSITIVE',
      specific_attributes: [{name: 'NUMERIC-SORT', value: '1'}, {name: 'NUMERIC-SORT', value: ''}]});
    expect(screen.queryByLabelText(/Definition/)).not.toBeInTheDocument();
  });

  it('hides and omits inapplicable source fields when changing implementation mode', async () => {
    const post = await mount('collation', 'create');
    fireEvent.change(screen.getByLabelText(/Collation name/), {target: {value: 'Owned'}});
    fireEvent.change(screen.getByLabelText(/Existing collation/), {target: {value: 'UNICODE'}});
    select(/Collation source/, 'External');
    expect(screen.queryByLabelText(/Existing collation/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Installed external name/), {target: {value: 'UNICODE'}});
    const external = await preview(post);
    expect(external.external_name).toBe('UNICODE');
    expect(external).not.toHaveProperty('base_collation');
    select(/Collation source/, 'Same Name');
    expect(screen.queryByLabelText(/Installed external name/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeDisabled();
    const same = await preview(post);
    expect(same).not.toHaveProperty('base_collation');
    expect(same).not.toHaveProperty('external_name');
  });

  it.each(['collation', 'character-set'])('prefills and explicitly clears %s comments', async kind => {
    const post = await mount(kind, 'comment', {description: 'Existing comment'});
    const comment = screen.getByLabelText(/Comment/);
    expect(comment).toHaveValue('Existing comment');
    fireEvent.change(comment, {target: {value: ''}});
    expect(await preview(post)).toEqual({description: ''});
  });

  it('prefills and changes only the database character-set default', async () => {
    const post = await mount('character-set', 'alter', {default_collation: 'UTF8'});
    const input = screen.getByLabelText(/Default collation/);
    expect(input).toHaveValue('UTF8');
    fireEvent.change(input, {target: {value: 'UNICODE'}});
    expect(await preview(post)).toEqual({default_collation: 'UNICODE'});
  });

  it('requires explicit collation drop confirmation without unrelated fields', async () => {
    const post = await mount('collation', 'drop');
    expect(screen.getByLabelText(/Confirm collation name/)).toBeRequired();
    fireEvent.change(screen.getByLabelText(/Confirm collation name/), {target: {value: 'UTF8'}});
    expect(await preview(post)).toEqual({confirmation: 'UTF8'});
    expect(screen.queryByLabelText(/Default collation/)).not.toBeInTheDocument();
  });
});
