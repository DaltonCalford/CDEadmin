import {execFileSync} from 'child_process';
import path from 'path';
import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {VisualAdministration, ObjectInspectorSection} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';

const cwd = path.resolve(__dirname, '../../../..');
const forms = JSON.parse(execFileSync('python3', ['-c',
  'import json; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
  'from pgadmin.cdeadmin.providers.firebird import shadows as s; ' +
  'print(json.dumps({op: s.form(op, ADMINISTRATION._field) for op in ("create", "drop")}))',
], {cwd, encoding: 'utf8'}));
const resource = {resource_id: 'owned', resource_kind: 'shadow', display_name: '7'};

async function mount(action) {
  const post = jest.fn(async ({action: task}) => {
    if (task === 'resource_inspect') return resource;
    if (task === 'visual_admin_validate') return {valid: true};
    return {plan_id: 'owned', plan_digest: 'd', state: 'ready', execution_available: true};
  });
  await act(async () => render(<VisualAdministration focused resources={[resource]} post={post}
    setError={jest.fn()} initialResourceKind="shadow" initialOperationId={action}
    selectedResource={resource} catalog={{objects: [{resource_kind: 'shadow',
      title: 'Shadow', operations: [{operation_id: action, title: action,
        target_required: action === 'drop', form: forms[action]}]}]}} />));
  return post;
}

async function preview(post) {
  fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
  await waitFor(() => expect(post.mock.calls.some(([body]) => body.action === 'visual_admin_plan')).toBe(true));
  const request = post.mock.calls.find(([body]) => body.action === 'visual_admin_plan')[0].request;
  return JSON.parse(execFileSync('python3', ['-c',
    'import json,sys; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
    'from pgadmin.cdeadmin.providers.firebird.shadows import compile_operation; ' +
    'r=json.load(sys.stdin); print(json.dumps(compile_operation(' +
    'r["operation_id"], r["draft"], r.get("target_resource"))))',
  ], {cwd, input: JSON.stringify(request), encoding: 'utf8'}));
}

function change(label, value) {
  fireEvent.change(screen.getByLabelText(label), {target: {value}});
}

describe('Firebird shadow visual tasks', () => {
  let height;
  beforeEach(() => { height = window.innerHeight; window.innerHeight = 1200; });
  afterEach(() => { window.innerHeight = height; });
  it.each(['AUTO', 'MANUAL'])('submits conditional mode %s without secondary files', async (mode) => {
    const post = await mount('create');
    change(/First shadow filename/, '/owned/first');
    fireEvent.mouseDown(screen.getByLabelText(/Failure handling/));
    fireEvent.click(screen.getByRole('option', {name: new RegExp('^' + mode + '$', 'i')}));
    fireEvent.click(screen.getByLabelText(/Conditional shadow/));
    expect(await preview(post)).toEqual([`CREATE SHADOW 1 ${mode} CONDITIONAL '/owned/first'`]);
  });

  it('removes a secondary-file draft without submitting obsolete paths', async () => {
    const post = await mount('create');
    change(/First shadow filename/, '/owned/first');
    fireEvent.click(screen.getByRole('button', {name: 'Add Secondary shadow files item'}));
    change(/^Shadow filename/, '/owned/removed');
    fireEvent.click(screen.getByRole('button', {name: 'Secondary shadow files 1: Remove'}));
    expect(await preview(post)).toEqual(['CREATE SHADOW 1 AUTO \'/owned/first\'']);
  });
  it('submits ordered native files with exact server paths and page counts', async () => {
    const post = await mount('create');
    change(/Shadow number/, '7');
    change(/First shadow filename/, '/owned/影\'s.shd');
    change(/First shadow file length/, '256');
    fireEvent.click(screen.getByRole('button', {name: 'Add Secondary shadow files item'}));
    change(/^Shadow filename/, '/owned/second.shd');
    change(/^Starting page/, '700');
    change(/^File length/, '512');
    expect(await preview(post)).toEqual([
      'CREATE SHADOW 7 AUTO \'/owned/影\'\'s.shd\' LENGTH 256 PAGES FILE \'/owned/second.shd\' STARTING AT PAGE 700 LENGTH 512 PAGES',
    ]);
  });

  it.each([true, false])('submits explicit preservation %s after bound confirmation', async (preserve) => {
    const post = await mount('drop');
    change(/Confirm shadow number/, '7');
    const control = screen.getByLabelText(/Preserve shadow files/);
    expect(control).toBeChecked();
    if (!preserve) fireEvent.click(control);
    expect(await preview(post)).toEqual(['DROP SHADOW 7 ' + (preserve ? 'PRESERVE' : 'DELETE') + ' FILE']);
  });

  it('renders native file properties and exact recreation text, not raw JSON', () => {
    const native = {property_sections: ['files', 'ddl'], files: [{
      filename: '/owned/影.shd', sequence: 0, start: 0, length: 256,
      flags: {manual: true, conditional: false},
    }], ddl: 'CREATE SHADOW 7 MANUAL \'/owned/影.shd\' LENGTH 256 PAGES;'};
    render(<ObjectInspectorSection tabbed resource={{...resource, extensions: {firebird: {native}}}} />);
    expect(screen.getByRole('tab', {name: 'Storage files'})).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('/owned/影.shd')).toBeInTheDocument();
    expect(screen.getByRole('rowheader', {name: 'manual'})).toBeInTheDocument();
    expect(screen.getByText('Yes')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', {name: 'Creation statement (DDL)'}));
    expect(screen.getByText(native.ddl)).toBeInTheDocument();
  });
});
