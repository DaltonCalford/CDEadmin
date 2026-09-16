import {execFileSync} from 'child_process';
import path from 'path';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {DatabaseTargetWorkspace, ServerProfileWorkspace}
  from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import manifest from '../../../pgadmin/cdeadmin/providers/firebird/provider_manifest.json';

const cwd = path.resolve(__dirname, '../../../..');
const bootstrap = 'from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
  'from pgadmin.cdeadmin.endpoints.profiles import registration_profile; ' +
  'from pgadmin.cdeadmin.endpoints import EndpointService; ' +
  'import json,sys; p=registration_profile("firebird-native"); ';
const belongs = field => field.field_id.startsWith('parallel_workers');
const databaseFields = JSON.parse(execFileSync('python3', ['-c', bootstrap +
  'print(json.dumps(p["form_contract"]["database"]["forms"]["edit"]["fields"]))'],
{cwd, encoding: 'utf8'})).filter(belongs);
const serverFields = manifest.registration.connection_fields.filter(belongs);

function mount(scope, value) {
  const configuration = {parallel_workers_policy: 'CUSTOM', parallel_workers: value};
  const post = jest.fn(async ({request}) => {
    const code = bootstrap + 'v=json.load(sys.stdin); v.pop("target_id",None);\n' +
      'try:\n' + (scope === 'server' ?
      ' v.update(name="Owned",host="localhost",port=53050,database_create_root="/owned"); ' +
        'r=EndpointService._server_form_values(p,"edit",v)\n' :
      ' r=EndpointService._database_form_values(p,"edit",v)\n') +
      ' print(json.dumps({"valid":True}))\n' +
      'except Exception as e: print(json.dumps({"valid":False,"error":str(e)}))';
    const result = JSON.parse(execFileSync('python3', ['-c', code],
      {cwd, input: JSON.stringify(request), encoding: 'utf8'}));
    if (!result.valid) throw new Error(result.error);
    return {};
  });
  const setError = jest.fn();
  const form = {form_id: `owned-workers-${scope}`, title: 'Save owned target',
    fields: scope === 'server' ? serverFields : databaseFields};
  if (scope === 'server') {
    render(<ServerProfileWorkspace registration={{primary_route: {
      route_id: 'owned-route', configuration}, forms: {forms: {edit: form}},
    }} post={post} setError={setError} />);
  } else {
    render(<DatabaseTargetWorkspace initialCatalog={{forms: {form_set_id: 'owned',
      forms: {edit: form}}, target_management: true, active_target_id: 'owned-target',
    targets: [{target_id: 'owned-target', configuration}],
    }} visualCatalog={{objects: []}} resources={[]} post={post} setError={setError}
    initialMode="edit" focused />);
  }
  return {post, setError, button: screen.getByRole('button', {
    name: scope === 'server' ? 'Save endpoint profile' : 'Save owned target'})};
}

describe.each(['server', 'database'])('Firebird %s worker preferences', scope => {
  let height;
  beforeEach(() => { height = window.innerHeight; window.innerHeight = 1200; });
  afterEach(() => { window.innerHeight = height; });

  it.each([0, 1, 2, 4, 5, 32767])('saves the exact request %s', async value => {
    const {post, button} = mount(scope, value);
    expect(screen.getByRole('spinbutton', {name: 'Requested parallel workers'})).toHaveValue(value);
    fireEvent.click(button);
    await waitFor(() => expect(post).toHaveBeenCalled());
    await expect(post.mock.results[0].value).resolves.toEqual({});
    expect(post.mock.calls[0][0].request.parallel_workers).toBe(value);
  });

  it.each([-1, 32768, 1.5])('rejects invalid numeric request %s', async value => {
    const {post, button, setError} = mount(scope, value);
    fireEvent.click(button);
    await waitFor(() => expect(post).toHaveBeenCalled());
    await waitFor(() => expect(setError).toHaveBeenCalledWith(expect.any(String)));
  });

  it('requires a count and preserves zero entered by the user', async () => {
    const {post, button} = mount(scope, 2);
    const input = screen.getByRole('spinbutton', {name: 'Requested parallel workers'});
    fireEvent.change(input, {target: {value: ''}});
    expect(button).toBeDisabled();
    fireEvent.change(input, {target: {value: '0'}});
    fireEvent.click(button);
    await waitFor(() => expect(post).toHaveBeenCalled());
    await expect(post.mock.results[0].value).resolves.toEqual({});
    expect(post.mock.calls[0][0].request.parallel_workers).toBe(0);
  });

  it.each(scope === 'database' ? ['Native default', 'Use server preference'] : ['Native default'])(
    'omits stale requests for %s', async choice => {
      const {post, button} = mount(scope, 32767);
      fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Initial parallel-worker policy'}));
      fireEvent.click(await screen.findByRole('option', {name: choice, exact: true}));
      expect(screen.queryByRole('spinbutton', {name: 'Requested parallel workers'})).not.toBeInTheDocument();
      fireEvent.click(button);
      await waitFor(() => expect(post).toHaveBeenCalled());
      await expect(post.mock.results[0].value).resolves.toEqual({});
      expect(post.mock.calls[0][0].request).not.toHaveProperty('parallel_workers');
    });
});
