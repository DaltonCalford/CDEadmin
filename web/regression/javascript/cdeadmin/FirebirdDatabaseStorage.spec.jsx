import {execFileSync} from 'child_process';
import path from 'path';
import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';

const cwd = path.resolve(__dirname, '../../../..');
const database = '/owned/database.fdb';
const forms = JSON.parse(execFileSync('python3', ['-c',
  'import json; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
  'from pgadmin.cdeadmin.providers.firebird import database_storage as s; ' +
  'print(json.dumps({op: s.form(op, ADMINISTRATION._field) for op in s.OPERATIONS}))',
], {cwd, encoding: 'utf8'}));
const resource = {resource_id: 'owned', resource_kind: 'database', display_name: database};

async function mount(action) {
  const post = jest.fn(async ({action: task}) => {
    if (task === 'resource_inspect') return resource;
    if (task === 'visual_admin_validate') return {valid: true};
    return {plan_id: 'owned', plan_digest: 'd', state: 'ready', execution_available: true};
  });
  await act(async () => render(<VisualAdministration focused resources={[resource]} post={post}
    setError={jest.fn()} initialResourceKind="database" initialOperationId={action}
    selectedResource={resource} catalog={{objects: [{resource_kind: 'database',
      title: 'Database', operations: [{operation_id: action, title: action,
        target_required: true, form: forms[action]}]}]}} />));
  return post;
}

async function preview(post) {
  fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
  await waitFor(() => expect(post.mock.calls.some(([body]) => body.action === 'visual_admin_plan')).toBe(true));
  const request = post.mock.calls.find(([body]) => body.action === 'visual_admin_plan')[0].request;
  return JSON.parse(execFileSync('python3', ['-c',
    'import json,sys; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
    'from pgadmin.cdeadmin.providers.firebird.database_storage import compile_operation; ' +
    'r=json.load(sys.stdin); print(json.dumps(compile_operation(' +
    'r["operation_id"], r["draft"], "/owned/database.fdb")))',
  ], {cwd, input: JSON.stringify(request), encoding: 'utf8'}));
}

function change(label, value) {
  fireEvent.change(screen.getByLabelText(label), {target: {value}});
}

describe('Firebird database storage task forms', () => {
  let height;
  beforeEach(() => { height = window.innerHeight; window.innerHeight = 1200; });
  afterEach(() => { window.innerHeight = height; });

  it.each([
    ['drop_difference_file', 'DROP DIFFERENCE FILE'],
    ['end_backup', 'END BACKUP'],
  ])('submits only exact confirmation for %s', async (action, sql) => {
    const post = await mount(action);
    change(/Confirm database path or alias/, database);
    expect(await preview(post)).toEqual(['ALTER DATABASE ' + sql]);
  });

  it('requires explicit difference-file overwrite acknowledgment', async () => {
    const post = await mount('begin_backup');
    change(/Confirm database path or alias/, database);
    const acknowledgment = screen.getByLabelText(/I confirm the difference-file path may be overwritten/);
    expect(acknowledgment).not.toBeChecked();
    fireEvent.click(acknowledgment);
    expect(await preview(post)).toEqual(['ALTER DATABASE BEGIN BACKUP']);
  });

  it('preserves Unicode, quotes and line breaks in a literal difference path', async () => {
    const post = await mount('add_difference_file');
    change(/Confirm database path or alias/, database);
    change(/Difference filename/, '/owned/影\'s\nbackup.delta');
    expect(await preview(post)).toEqual(['ALTER DATABASE ADD DIFFERENCE FILE \'/owned/影\'\'s\nbackup.delta\'']);
  });

  it('adds and removes file records without leaving obsolete paths in the task', async () => {
    const post = await mount('add_files');
    change(/Confirm database path or alias/, database);
    fireEvent.click(screen.getByRole('button', {name: 'Add Additional database files item'}));
    change(/^Server filename/, '/owned/removed');
    fireEvent.click(screen.getByRole('button', {name: 'Additional database files 1: Remove'}));
    fireEvent.click(screen.getByRole('button', {name: 'Add Additional database files item'}));
    change(/^Server filename/, '/owned/影\'s.extra');
    change(/^Starting page/, '8192');
    change(/^File length/, '1024');
    expect(await preview(post)).toEqual([
      'ALTER DATABASE ADD FILE \'/owned/影\'\'s.extra\' STARTING AT PAGE 8192 LENGTH 1024 PAGES',
    ]);
  });

  it('submits the displayed order after moving file records in both directions', async () => {
    const post = await mount('add_files');
    change(/Confirm database path or alias/, database);
    for (const name of ['/owned/first', '/owned/second']) {
      fireEvent.click(screen.getByRole('button', {name: 'Add Additional database files item'}));
      const fields = screen.getAllByLabelText(/^Server filename/);
      fireEvent.change(fields[fields.length - 1], {target: {value: name}});
    }
    fireEvent.click(screen.getByRole('button', {name: 'Additional database files 2: Move up'}));
    expect(screen.getAllByLabelText(/^Server filename/).map(item => item.value)).toEqual(['/owned/second', '/owned/first']);
    fireEvent.click(screen.getByRole('button', {name: 'Additional database files 1: Move down'}));
    expect(await preview(post)).toEqual(['ALTER DATABASE ADD FILE \'/owned/first\' FILE \'/owned/second\'']);
  });
});
