import {execFileSync} from 'child_process';
import path from 'path';
import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {ObjectInspectorSection, VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';

const cwd = path.resolve(__dirname, '../../../..');
const forms = JSON.parse(execFileSync('python3', ['-c',
  'import json; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
  'from pgadmin.cdeadmin.providers.firebird import object_privileges as p; ' +
  'print(json.dumps({kind: {action: p.form(kind, action, ADMINISTRATION._field) ' +
  'for action in ["grant", "revoke"]} for kind in p.KINDS}))',
], {cwd, encoding: 'utf8'}));

function resource(kind, native={}) {
  return {resource_id: 'owned', resource_kind: kind, display_name: 'Owned',
    display_path: kind === 'column' ? ['Relation', 'Owned'] :
      native.package ? [native.package, 'Owned'] : ['Owned'],
    extensions: {firebird: {native}}};
}

async function mount(kind, action, native={}, objectEditor=false) {
  const selected = resource(kind, native);
  const post = jest.fn(async ({action: task}) => {
    if (task === 'resource_inspect') return selected;
    if (task === 'visual_admin_validate') return {valid: true};
    return {plan_id: 'owned', plan_digest: 'd', state: 'ready', execution_available: true};
  });
  await act(async () => render(<VisualAdministration focused={!objectEditor} objectEditor={objectEditor}
    resources={[selected]} post={post}
    setError={jest.fn()} initialResourceKind={kind} initialOperationId={action}
    selectedResource={selected} catalog={{objects: [{resource_kind: kind,
      title: kind, operations: [{operation_id: action, title: action,
        target_required: true, form: forms[kind][action]}]}]}} />));
  return post;
}

function select(element, value) {
  fireEvent.mouseDown(element);
  fireEvent.click(screen.getByRole('option', {name: value, exact: true}));
  fireEvent.keyDown(screen.getByRole('listbox'), {key: 'Escape'});
}

async function preview(post) {
  fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
  await waitFor(() => expect(post.mock.calls.some(([body]) => body.action === 'visual_admin_plan')).toBe(true));
  const request = post.mock.calls.filter(([body]) => body.action === 'visual_admin_plan').slice(-1)[0][0].request;
  const sql = JSON.parse(execFileSync('python3', ['-c',
    'import json,sys; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
    'from pgadmin.cdeadmin.providers.firebird.object_privileges import compile_operation; ' +
    'r=json.load(sys.stdin); print(json.dumps(compile_operation(' +
    'r["resource_kind"],r["operation_id"],r["draft"],r["target_resource"])))',
  ], {cwd, input: JSON.stringify(request), encoding: 'utf8'}));
  return {draft: request.draft, sql};
}

describe('Firebird object-bound permission forms', () => {
  let height;
  beforeEach(() => { height = window.innerHeight; window.innerHeight = 1200; });
  afterEach(() => { window.innerHeight = height; });

  it.each(Object.keys(forms))('submits grants only against the selected %s', async (kind) => {
    const post = await mount(kind, 'grant');
    fireEvent.change(screen.getByLabelText(/Grantee name/), {target: {value: 'Reader'}});
    const choices = forms[kind].grant.fields.find((field) => field.field_id === 'privileges').options;
    select(screen.getByLabelText(/Object privileges/), choices[0].value);
    const observed = await preview(post);
    expect(observed.draft).toMatchObject({principal: 'Reader', privileges: [choices[0].value]});
    expect(observed.draft).not.toHaveProperty('object_name');
    expect(observed.draft).not.toHaveProperty('privilege_scope');
    expect(observed.sql).toContain(kind === 'column' ?
      'UPDATE ("Owned") ON TABLE "Relation"' : '"Owned" TO USER "Reader"');
    expect(screen.queryByLabelText(/Exact object name/)).not.toBeInTheDocument();
  });

  it.each(Object.keys(forms))('keeps the %s revoke confirmation and grant option explicit', async (kind) => {
    const post = await mount(kind, 'revoke');
    fireEvent.change(screen.getByLabelText(/Grantee name/), {target: {value: 'Reader'}});
    fireEvent.change(screen.getByLabelText(/Confirm exact grantee names/), {target: {value: 'Reader'}});
    const choices = forms[kind].revoke.fields.find((field) => field.field_id === 'privileges').options;
    select(screen.getByLabelText(/Object privileges/), choices[0].value);
    fireEvent.click(screen.getByLabelText(/Revoke only the grant option/));
    const observed = await preview(post);
    expect(observed.sql).toMatch(/^REVOKE GRANT OPTION FOR /);
    expect(observed.draft).toMatchObject({confirmation: 'Reader', grant_option_only: true});
    expect(screen.queryByLabelText(/With grant option/)).not.toBeInTheDocument();
  });

  it('makes the whole-package scope visible and compiles that native target', async () => {
    const post = await mount('function', 'grant', {package: 'PK'});
    expect(screen.getByText(/EXECUTE applies to the entire package/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Grantee name/), {target: {value: 'Reader'}});
    select(screen.getByLabelText(/Object privileges/), 'EXECUTE');
    expect((await preview(post)).sql).toBe('GRANT EXECUTE ON PACKAGE "PK" TO USER "Reader"');
  });

  it.each([['table', {}, 'Owned'], ['column', {}, 'Relation › Owned'],
    ['function', {package: 'PK'}, 'PK › Owned']])(
    'keeps the selected %s identity visible while editing permissions', async (kind, native, label) => {
      await mount(kind, 'grant', native, true);
      expect(screen.getByLabelText('Selected administration object')).toHaveTextContent(label);
    });

  it('opens only admitted permission tasks from the security section', () => {
    const onOperation = jest.fn();
    const descriptor = {operations: [
      {operation_id: 'grant', title: 'Grant table privileges', execution_available: true},
      {operation_id: 'revoke', title: 'Revoke table privileges', execution_available: false},
      {operation_id: 'alter', title: 'Alter table', execution_available: true},
    ]};
    const {rerender} = render(<ObjectInspectorSection tabbed resource={resource('table', {
      property_sections: ['privileges'], privileges: [],
    })} descriptor={descriptor} onOperation={onOperation} />);
    fireEvent.click(screen.getByRole('button', {name: 'Grant table privileges'}));
    expect(onOperation).toHaveBeenCalledWith('grant');
    expect(screen.getByRole('button', {name: 'Revoke table privileges'})).toBeDisabled();
    expect(screen.queryByRole('button', {name: 'Alter table'})).not.toBeInTheDocument();
    rerender(<ObjectInspectorSection tabbed loading resource={resource('table', {
      property_sections: ['privileges'],
    })} descriptor={descriptor} onOperation={onOperation} />);
    expect(screen.getByRole('button', {name: 'Grant table privileges'})).toBeDisabled();
  });

  it('distinguishes package-wide grants from an individual routine grant', () => {
    render(<ObjectInspectorSection tabbed resource={resource('function', {
      property_sections: ['privileges'], privileges: [], package_privileges: {
        package: 'PK', scope: 'Entire package, not an individual routine',
        privileges: [{grantee: 'PackageReader', privilege: 'X'}],
      },
    })} />);
    expect(screen.getByText('object privileges')).toBeInTheDocument();
    expect(screen.getByText('package privileges')).toBeInTheDocument();
    expect(screen.getByText('Entire package, not an individual routine')).toBeInTheDocument();
    expect(screen.getByText('PackageReader')).toBeInTheDocument();
  });
});
