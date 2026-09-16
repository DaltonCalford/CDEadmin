import {execFileSync} from 'child_process';
import path from 'path';
import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';

const cwd = path.resolve(__dirname, '../../../..');
const forms = JSON.parse(execFileSync('python3', ['-c',
  'import json; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
  'from pgadmin.cdeadmin.providers.firebird import views as v; ' +
  'print(json.dumps({op: v.form(op, ADMINISTRATION._field) for op in v.OPERATIONS}))',
], {cwd, encoding: 'utf8'}));

async function mount(action, native={}) {
  const resource = {resource_id: 'owned', resource_kind: 'view', display_name: 'Owned',
    display_path: ['Owned'], extensions: {firebird: {native}}};
  const post = jest.fn(async ({action: task}) => {
    if (task === 'resource_inspect') return resource;
    if (task === 'visual_admin_validate') return {valid: true};
    return {plan_id: 'owned', plan_digest: 'd', state: 'ready', execution_available: true};
  });
  await act(async () => render(<VisualAdministration focused resources={[resource]} post={post}
    setError={jest.fn()} initialResourceKind="view" initialOperationId={action}
    selectedResource={resource} catalog={{objects: [{resource_kind: 'view',
      title: 'View', operations: [{operation_id: action, title: action,
        target_required: action === 'recreate', form: forms[action]}]}]}} />));
  return post;
}

async function preview(post) {
  fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
  await waitFor(() => expect(post.mock.calls.some(([b]) => b.action === 'visual_admin_plan')).toBe(true));
  const request = post.mock.calls.filter(([b]) => b.action === 'visual_admin_plan').slice(-1)[0][0].request;
  const sql = JSON.parse(execFileSync('python3', ['-c',
    'import json,sys; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
    'from pgadmin.cdeadmin.providers.firebird.views import compile_operation; ' +
    'r=json.load(sys.stdin); print(json.dumps(compile_operation(' +
    'r["operation_id"], r["draft"], r.get("target_resource"))))',
  ], {cwd, input: JSON.stringify(request), encoding: 'utf8'}));
  return {draft: request.draft, sql};
}

function change(label, value) {
  fireEvent.change(screen.getByLabelText(label), {target: {value}});
}

describe('Firebird view replacement forms', () => {
  let height;
  beforeEach(() => { height = window.innerHeight; window.innerHeight = 1200; });
  afterEach(() => { window.innerHeight = height; });

  it('creates or alters using the native query without a PostgreSQL template', async () => {
    const post = await mount('create_or_alter');
    change(/View name/, 'V"東京');
    change(/View query/, 'SELECT 1 AS VALUE FROM RDB$DATABASE');
    expect((await preview(post)).sql).toBe(
      'CREATE OR ALTER VIEW "V""東京" AS\nSELECT 1 AS VALUE FROM RDB$DATABASE');
  });

  it('retains ordered column names and source when recreating an inspected view', async () => {
    const definition = 'SELECT 1, 2 FROM RDB$DATABASE';
    const columns = [{name: 'B'}, {name: 'A"東京'}];
    const post = await mount('recreate', {definition, view_columns: columns});
    expect(screen.getByLabelText(/View query/)).toHaveValue(definition);
    change(/Confirm view name/, 'Owned');
    const result = await preview(post);
    expect(result.draft.columns).toEqual(columns);
    expect(result.sql).toBe('RECREATE VIEW "Owned" ("B", "A""東京") AS\n' + definition);
    expect(screen.queryByLabelText(/cascade|materialized/i)).toBeNull();
  });

  it('submits an edited native CTE query as one statement', async () => {
    const post = await mount('recreate', {definition: 'SELECT 1 V FROM RDB$DATABASE'});
    const query = 'WITH Q AS (SELECT \';\' AS V FROM RDB$DATABASE) SELECT V FROM Q';
    change(/View query/, query);
    change(/Confirm view name/, 'Owned');
    expect((await preview(post)).sql).toBe('RECREATE VIEW "Owned" AS\n' + query);
  });

  it('edits, reorders, adds and removes column names through visual controls', async () => {
    const post = await mount('recreate', {definition: 'SELECT 1, 2 FROM RDB$DATABASE',
      view_columns: [{name: 'A'}, {name: 'B'}]});
    fireEvent.click(screen.getByRole('button', {name: 'Ordered view column names 2: Move up'}));
    expect(screen.getAllByLabelText(/Column name/).map((input) => input.value)).toEqual(['B', 'A']);
    fireEvent.change(screen.getAllByLabelText(/Column name/)[1], {target: {value: 'C'}});
    fireEvent.click(screen.getByRole('button', {name: 'Add Ordered view column names item'}));
    fireEvent.change(screen.getAllByLabelText(/Column name/)[2], {target: {value: 'D'}});
    fireEvent.click(screen.getByRole('button', {name: 'Ordered view column names 2: Remove'}));
    change(/Confirm view name/, 'Owned');
    expect((await preview(post)).sql).toBe(
      'RECREATE VIEW "Owned" ("B", "D") AS\nSELECT 1, 2 FROM RDB$DATABASE');
  });
});
