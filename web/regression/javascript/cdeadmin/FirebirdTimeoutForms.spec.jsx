import {execFileSync} from 'child_process';
import path from 'path';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {DatabaseTargetWorkspace}
  from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';

const cwd = path.resolve(__dirname, '../../../..');
const bootstrap = 'from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
  'from pgadmin.cdeadmin.endpoints.profiles import registration_profile; ' +
  'from pgadmin.cdeadmin.endpoints import EndpointService; ' +
  'import json,sys; p=registration_profile("firebird-native"); ';
const keys = ['transaction_lock_timeout', 'statement_timeout_ms', 'session_idle_timeout_seconds'];
const fields = JSON.parse(execFileSync('python3', ['-c', bootstrap +
  'print(json.dumps(p["form_contract"]["database"]["forms"]["edit"]["fields"]))'],
{cwd, encoding: 'utf8'})).filter(field => keys.includes(field.field_id));

describe.each(fields)('Firebird timeout control $field_id', field => {
  it.each([0, 12, field.minimum, field.maximum, 1.5])('validates submitted value %s', async value => {
    const post = jest.fn(async ({request}) => {
      const code = bootstrap + 'v=json.load(sys.stdin); v.pop("target_id",None);\n' +
        'try:\n r=EndpointService._database_form_values(p,"edit",v); ' +
        'print(json.dumps({"valid":True,"values":r}))\n' +
        'except Exception as e: print(json.dumps({"valid":False,"error":str(e)}))';
      const result = JSON.parse(execFileSync('python3', ['-c', code],
        {cwd, input: JSON.stringify(request), encoding: 'utf8'}));
      if (!result.valid) throw new Error(result.error);
      return result.values;
    });
    const setError = jest.fn();
    const form = {form_id: 'owned-timeouts', title: 'Save timeout preferences', fields};
    render(<DatabaseTargetWorkspace initialCatalog={{forms: {form_set_id: 'owned',
      forms: {edit: form}}, target_management: true, active_target_id: 'owned-target',
    targets: [{target_id: 'owned-target', configuration: {[field.field_id]: 12}}],
    }} visualCatalog={{objects: []}} resources={[]} post={post} setError={setError}
    initialMode="edit" focused />);
    fireEvent.change(screen.getByRole('spinbutton', {name: field.label}),
      {target: {value: String(value)}});
    fireEvent.click(screen.getByRole('button', {name: 'Save timeout preferences'}));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0][0].request[field.field_id]).toBe(value);
    if (value === 1.5) {
      await waitFor(() => expect(setError).toHaveBeenCalledWith(expect.stringContaining('integer')));
    } else {
      await expect(post.mock.results[0].value).resolves.toHaveProperty(field.field_id, value);
      expect(setError).not.toHaveBeenCalledWith(expect.any(String));
    }
  });
});
