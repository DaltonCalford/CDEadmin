import {execFileSync} from 'child_process';
import path from 'path';
import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import portfolio from '../../../pgadmin/cdeadmin/visual_admin/portfolio_catalog.json';

const cwd = path.resolve(__dirname, '../../../..');
const form = portfolio.forms.firebird_database_create;

async function mount() {
  const post = jest.fn(async ({action, request}) => {
    if (action === 'visual_admin_validate') {
      const validation = JSON.parse(execFileSync('python3', ['-c',
        'import json,sys; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
        'print(json.dumps(ADMINISTRATION.validate(json.load(sys.stdin))))',
      ], {cwd, input: JSON.stringify(request), encoding: 'utf8'}));
      return {...validation, valid: validation.errors.length === 0};
    }
    return {plan_id: 'owned', plan_digest: 'owned-digest',
      state: 'ready', execution_available: true};
  });
  await act(async () => render(<VisualAdministration focused resources={[]}
    post={post} setError={jest.fn()} initialResourceKind="database"
    initialOperationId="create" catalog={{objects: [{resource_kind: 'database',
      title: 'Database', operations: [{operation_id: 'create', title: 'Create database',
        target_required: false, form}]}]}} />));
  fireEvent.change(screen.getByLabelText(/Absolute database filename/),
    {target: {value: '/owned/new.fdb'}});
  return post;
}

async function preview(post) {
  fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
  await waitFor(() => expect(post.mock.calls.some(
    ([body]) => body.action === 'visual_admin_plan')).toBe(true));
  const request = post.mock.calls.find(
    ([body]) => body.action === 'visual_admin_plan')[0].request;
  return JSON.parse(execFileSync('python3', ['-c',
    'import json,sys; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
    'r=json.load(sys.stdin); r["_provider_route"]={"host":"localhost",' +
    '"port":53050,"database_create_root":"/owned"}; ' +
    'print(json.dumps(ADMINISTRATION.plan(r)["provider_payload"]["compiled"]))',
  ], {cwd, input: JSON.stringify(request), encoding: 'utf8'}));
}

describe('Firebird persistent creation buffers', () => {
  it('leaves omitted native buffers out of the creation options', async () => {
    const post = await mount();
    expect(screen.getByLabelText('Stored database page buffers')).toHaveValue(null);
    expect((await preview(post)).create_options).not.toHaveProperty('stored_page_buffers');
  });

  it.each([0, 50, 64, 131072, 2147483646])('preserves explicit %s through native planning', async (value) => {
    const post = await mount();
    fireEvent.change(screen.getByLabelText('Stored database page buffers'), {target: {value: String(value)}});
    const compiled = await preview(post);
    expect(compiled.create_options.stored_page_buffers).toBe(value);
    expect(compiled.endpoint_database).toBe('/owned/new.fdb');
    expect(compiled.create_options).not.toHaveProperty('attachment_cache_pages');
  });

  it('clearing a previously entered value restores omission', async () => {
    const post = await mount();
    const input = screen.getByLabelText('Stored database page buffers');
    fireEvent.change(input, {target: {value: '64'}});
    fireEvent.change(input, {target: {value: ''}});
    expect((await preview(post)).create_options).not.toHaveProperty('stored_page_buffers');
  });

  it.each([-1, 1, 49, 64.5, 2147483647])('refuses %s before generating an executable plan', async (value) => {
    const post = await mount();
    fireEvent.change(screen.getByLabelText('Stored database page buffers'), {target: {value: String(value)}});
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await screen.findByText(/Firebird stored page buffers must be zero or an integer/);
    expect(post.mock.calls.some(([body]) => body.action === 'visual_admin_plan')).toBe(false);
    expect(post.mock.calls.some(([body]) => body.action === 'visual_admin_apply')).toBe(false);
  });
});

describe('Firebird creation sweep threshold', () => {
  const label = 'Automatic sweep interval (transaction gap)';

  it('omits an untouched sweep interval rather than inventing a default', async () => {
    const post = await mount();
    expect(screen.getByLabelText(label)).toHaveValue(null);
    expect((await preview(post)).create_options).not.toHaveProperty('sweep_interval');
  });

  it.each([0, 1, 20000, 50000, 2147483647])('preserves exact interval %s through planning', async (value) => {
    const post = await mount();
    fireEvent.change(screen.getByLabelText(label), {target: {value: String(value)}});
    fireEvent.change(screen.getByLabelText('Stored database page buffers'), {target: {value: '64'}});
    const options = (await preview(post)).create_options;
    expect(options.sweep_interval).toBe(value);
    expect(options.stored_page_buffers).toBe(64);
    expect(options).not.toHaveProperty('attachment_cache_pages');
  });

  it('clearing an explicit zero restores native omission', async () => {
    const post = await mount();
    const input = screen.getByLabelText(label);
    fireEvent.change(input, {target: {value: '0'}});
    fireEvent.change(input, {target: {value: ''}});
    expect((await preview(post)).create_options).not.toHaveProperty('sweep_interval');
  });

  it.each([-1, 1.5, 2147483648])('refuses interval %s before planning or execution', async (value) => {
    const post = await mount();
    fireEvent.change(screen.getByLabelText(label), {target: {value: String(value)}});
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await screen.findByText(/Firebird sweep interval must be an integer/);
    expect(post.mock.calls.some(([body]) => body.action === 'visual_admin_plan')).toBe(false);
    expect(post.mock.calls.some(([body]) => body.action === 'visual_admin_apply')).toBe(false);
  });
});
