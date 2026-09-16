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
const belongs = field => field.field_id === 'decfloat_traps_policy' || field.field_id.startsWith('trap_');
const databaseFields = JSON.parse(execFileSync('python3', ['-c', bootstrap +
  'print(json.dumps(p["form_contract"]["database"]["forms"]["edit"]["fields"]))'],
{cwd, encoding: 'utf8'})).filter(belongs);
const serverFields = manifest.registration.connection_fields.filter(belongs);
const flags = serverFields.filter(field => field.control === 'boolean');
const label = 'Initial DECFLOAT trap policy';

function mount(scope, mask, policy='CUSTOM') {
  const configuration = {decfloat_traps_policy: policy, ...Object.fromEntries(
    flags.map((field, index) => [field.field_id, !!(mask & (1 << index))]))};
  const post = jest.fn(async ({request}) => {
    const code = bootstrap + 'v=json.load(sys.stdin); v.pop("target_id",None);\n' +
      'try:\n' + (scope === 'server' ?
      ' v.update(name="Owned",host="localhost",port=53050,database_create_root="/owned"); ' +
        'r=EndpointService._server_form_values(p,"edit",v)\n' :
      ' r=EndpointService._database_form_values(p,"edit",v)\n') +
      ' print(json.dumps({"valid":True,"values":r}))\n' +
      'except Exception as e: print(json.dumps({"valid":False,"error":str(e)}))';
    const result = JSON.parse(execFileSync('python3', ['-c', code],
      {cwd, input: JSON.stringify(request), encoding: 'utf8'}));
    if (!result.valid) throw new Error(result.error);
    return {};
  });
  const setError = jest.fn();
  const form = {form_id: `owned-traps-${scope}`, title: 'Save owned target',
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
  return {post, setError, configuration, button: screen.getByRole('button', {
    name: scope === 'server' ? 'Save endpoint profile' : 'Save owned target'})};
}

describe.each(['server', 'database'])('Firebird %s initial traps', scope => {
  let height;
  beforeEach(() => { height = window.innerHeight; window.innerHeight = 1200; });
  afterEach(() => { window.innerHeight = height; });

  it.each(Array.from({length: 31}, (_, index) => index + 1))('saves exact nonempty selection %s', async mask => {
    const {post, setError, button, configuration} = mount(scope, mask);
    flags.forEach((field, index) => expect(screen.getByRole('checkbox', {name: field.label}).checked)
      .toBe(!!(mask & (1 << index))));
    fireEvent.click(button);
    await waitFor(() => expect(post).toHaveBeenCalled());
    await expect(post.mock.results[0].value).resolves.toEqual({});
    expect(post.mock.calls[0][0].request).toMatchObject(configuration);
    expect(setError).not.toHaveBeenCalledWith(expect.stringMatching(/at least one/));
  });

  it('surfaces the real provider rejection for an empty custom selection', async () => {
    const {button, setError} = mount(scope, 0);
    fireEvent.click(button);
    await waitFor(() => expect(setError).toHaveBeenCalledWith(expect.stringMatching(/at least one/)));
  });

  it('toggling checkboxes sends changed native selections', async () => {
    const {post, button} = mount(scope, 1);
    fireEvent.click(screen.getByRole('checkbox', {name: flags[1].label}));
    fireEvent.click(screen.getByRole('checkbox', {name: flags[0].label}));
    fireEvent.click(button);
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0][0].request).toMatchObject({trap_division_by_zero: false, trap_inexact: true});
    await expect(post.mock.results[0].value).resolves.toEqual({});
  });

  it.each(scope === 'database' ? ['Native default', 'Use server preference'] : ['Native default'])(
    'hides and omits inactive selections for %s', async choice => {
      const {post, button} = mount(scope, 31);
      fireEvent.mouseDown(screen.getByRole('combobox', {name: label}));
      fireEvent.click(await screen.findByRole('option', {name: choice, exact: true}));
      flags.forEach(field => expect(screen.queryByRole('checkbox', {name: field.label})).not.toBeInTheDocument());
      fireEvent.click(button);
      await waitFor(() => expect(post).toHaveBeenCalled());
      const request = post.mock.calls[0][0].request;
      await expect(post.mock.results[0].value).resolves.toEqual({});
      flags.forEach(field => expect(request).not.toHaveProperty(field.field_id));
      expect(request.decfloat_traps_policy).toBe(choice === 'Native default' ? 'NATIVE_DEFAULT' : 'SERVER_DEFAULT');
    });
});
