import {execFileSync} from 'child_process';
import path from 'path';
import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';

const cwd = path.resolve(__dirname, '../../../..');
const forms = JSON.parse(execFileSync('python3', ['-c',
  'import json; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
  'from pgadmin.cdeadmin.providers.firebird import sequences as s; ' +
  'print(json.dumps({op: s.form(op, ADMINISTRATION._field) ' +
  'for op in s.OPERATIONS - {"inspect"}}))',
], {cwd, encoding: 'utf8'}));

async function mount(action, native={}) {
  const resource = {resource_id: 'owned', resource_kind: 'sequence',
    display_name: 'Owned', display_path: ['Owned'], extensions: {firebird: {native}}};
  const post = jest.fn(async ({action: task}) => {
    if (task === 'resource_inspect') return resource;
    if (task === 'visual_admin_validate') return {valid: true};
    return {plan_id: 'owned', plan_digest: 'd', state: 'ready', execution_available: true};
  });
  await act(async () => render(<VisualAdministration focused resources={[resource]} post={post}
    setError={jest.fn()} initialResourceKind="sequence" initialOperationId={action}
    selectedResource={resource} catalog={{objects: [{resource_kind: 'sequence',
      title: 'Sequence', operations: [{operation_id: action, title: action,
        target_required: !['create', 'create_or_alter'].includes(action), form: forms[action]}]}]}} />));
  return post;
}

async function preview(post) {
  fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
  await waitFor(() => expect(post.mock.calls.some(([body]) => body.action === 'visual_admin_plan')).toBe(true));
  const request = post.mock.calls.filter(([body]) => body.action === 'visual_admin_plan').slice(-1)[0][0].request;
  const sql = JSON.parse(execFileSync('python3', ['-c',
    'import json,sys; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
    'from pgadmin.cdeadmin.providers.firebird.sequences import compile_operation; ' +
    'r=json.load(sys.stdin); print(json.dumps(compile_operation(' +
    'r["operation_id"], r["draft"], r.get("target_resource"))))',
  ], {cwd, input: JSON.stringify(request), encoding: 'utf8'}));
  return {draft: request.draft, sql};
}

function change(label, value) {
  fireEvent.change(screen.getByLabelText(label), {target: {value}});
}

describe('Firebird native sequence forms', () => {
  let height;
  beforeEach(() => { height = window.innerHeight; window.innerHeight = 1200; });
  afterEach(() => { window.innerHeight = height; });

  it.each(['-9223372036854775808', '9007199254740993', '9223372036854775807'])(
    'preserves exact current value %s through prefill and submission', async (value) => {
      const post = await mount('set_current', {state: {current_value: value}});
      expect(screen.getByLabelText(/Current generator value/)).toHaveValue(value);
      expect(screen.getByLabelText(/Current generator value/)).toHaveAttribute('type', 'text');
      change(/Confirm sequence name/, 'Owned');
      const result = await preview(post);
      expect(result.draft.current).toBe(value);
      expect(result.sql).toEqual(['SET GENERATOR "Owned" TO ' + value]);
      expect(screen.queryByLabelText(/Next generated value/)).toBeNull();
    });

  it('creates a sequence without inventing unsupported range or cache options', async () => {
    const post = await mount('create');
    change(/Sequence name/, 'Owned');
    change(/Start value/, '-9223372036854775808');
    change(/Increment by/, '2147483647');
    expect((await preview(post)).sql[0]).toBe(
      'CREATE SEQUENCE "Owned" START WITH -9223372036854775808 INCREMENT BY 2147483647');
    expect(screen.queryByLabelText(/cache|cycle|maximum|minimum/i)).toBeNull();
  });

  it('keeps ordinary comment edits separate from a next-value restart', async () => {
    const post = await mount('alter', {initial_value: '10', increment: 2, description: 'Old'});
    expect(screen.getByLabelText(/Next generated value/)).toHaveValue('');
    expect(screen.getByLabelText(/Increment by/)).toHaveValue(2);
    change(/^Comment/, 'New');
    const result = await preview(post);
    expect(result.draft).not.toHaveProperty('restart');
    expect(result.draft).not.toHaveProperty('increment');
    expect(result.sql).toEqual(['COMMENT ON SEQUENCE "Owned" IS \'New\'']);
  });

  it('submits explicit restart using next-value semantics', async () => {
    const post = await mount('alter', {increment: -1});
    change(/Next generated value/, '9223372036854775807');
    expect((await preview(post)).sql).toEqual([
      'ALTER SEQUENCE "Owned" RESTART WITH 9223372036854775807']);
  });

  it('preserves the stored initial value exactly when recreating', async () => {
    const post = await mount('recreate', {initial_value: '-9223372036854775808', increment: 1});
    expect(screen.getByLabelText(/Start value/)).toHaveValue('-9223372036854775808');
    change(/Confirm sequence name/, 'Owned');
    expect((await preview(post)).sql[0]).toBe(
      'RECREATE SEQUENCE "Owned" START WITH -9223372036854775808 INCREMENT BY 1');
  });

  it('uses the native create-or-alter statement and explicit options', async () => {
    const post = await mount('create_or_alter');
    change(/Sequence name/, 'Owned');
    change(/Increment by/, '-2147483647');
    expect((await preview(post)).sql).toEqual([
      'CREATE OR ALTER SEQUENCE "Owned" INCREMENT BY -2147483647']);
  });

  it('removes comments explicitly without changing position', async () => {
    const post = await mount('comment', {description: 'Old'});
    change(/^Comment/, '');
    expect((await preview(post)).sql).toEqual(['COMMENT ON SEQUENCE "Owned" IS NULL']);
  });

  it('drops only the exact confirmed sequence without fabricated cascade', async () => {
    const post = await mount('drop');
    change(/Confirm sequence name/, 'Owned');
    expect((await preview(post)).sql).toEqual(['DROP SEQUENCE "Owned"']);
    expect(screen.queryByLabelText(/cascade/i)).toBeNull();
  });
});
