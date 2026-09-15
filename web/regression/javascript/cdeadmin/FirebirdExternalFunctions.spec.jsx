import {execFileSync} from 'child_process';
import path from 'path';
import {act, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {VisualAdministration, submittedFieldValue} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';

const cwd = path.resolve(__dirname, '../../../..');
const forms = JSON.parse(execFileSync('python3', ['-c',
  'import json; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
  'from pgadmin.cdeadmin.providers.firebird.external_functions import form; ' +
  'print(json.dumps({action: form(action, ADMINISTRATION._field) ' +
  'for action in ["create", "alter", "comment", "drop"]}))',
], {cwd, encoding: 'utf8'}));

async function mount(action, native={}) {
  const resource = {resource_id: 'owned', resource_kind: 'external-function',
    display_name: 'Owned', display_path: ['Owned'], extensions: {firebird: {native}}};
  const post = jest.fn(async ({action: task}) => {
    if (task === 'resource_inspect') return resource;
    if (task === 'visual_admin_validate') return {valid: true};
    return {plan_id: 'owned', plan_digest: 'd', state: 'ready', execution_available: true};
  });
  await act(async () => render(<VisualAdministration focused resources={[resource]} post={post}
    setError={jest.fn()} initialResourceKind="external-function" initialOperationId={action}
    selectedResource={resource} catalog={{objects: [{resource_kind: 'external-function',
      title: 'Legacy external function', operations: [{operation_id: action, title: action,
        target_required: action !== 'create', form: forms[action]}]}]}} />));
  return post;
}

async function preview(post, action) {
  fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
  await waitFor(() => expect(post.mock.calls.some(([body]) => body.action === 'visual_admin_plan')).toBe(true));
  const request = post.mock.calls.filter(([body]) => body.action === 'visual_admin_plan').slice(-1)[0][0].request;
  // Compile the actual submitted visual draft using the production compiler.
  const sql = JSON.parse(execFileSync('python3', ['-c',
    'import json,sys; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
    'from pgadmin.cdeadmin.providers.firebird.external_functions import compile_operation; ' +
    'request=json.load(sys.stdin); print(json.dumps(compile_operation(' +
    'request["operation_id"], request["draft"], request.get("target_resource"))))',
  ], {cwd, input: JSON.stringify(request), encoding: 'utf8'}));
  expect(request.operation_id).toBe(action);
  return {draft: request.draft, sql};
}

function select(element, value) {
  fireEvent.mouseDown(element);
  fireEvent.click(screen.getByRole('option', {name: value, exact: true}));
}

describe('Firebird external function forms', () => {
  let height;
  beforeEach(() => { height = window.innerHeight; window.innerHeight = 1200; });
  afterEach(() => { window.innerHeight = height; });

  it('submits ordered native argument controls without hidden type defaults', async () => {
    const post = await mount('create');
    fireEvent.change(screen.getByLabelText(/Function name/), {target: {value: 'Owned'}});
    fireEvent.change(screen.getByLabelText(/Exported entry point/), {target: {value: 'owned'}});
    fireEvent.change(screen.getByLabelText(/Installed library/), {target: {value: 'owned_udf'}});
    const list = screen.getByRole('group', {name: 'Ordered input arguments'});
    fireEvent.click(within(list).getByRole('button', {name: 'Add Ordered input arguments item'}));
    const result = await preview(post, 'create');
    expect(result.draft.arguments).toEqual([{data_type: 'INTEGER', mechanism: 'REFERENCE'}]);
    expect(result.sql[0]).toContain('"Owned" INTEGER RETURNS INTEGER');
    select(within(list).getByLabelText(/Data type/), 'Varchar');
    fireEvent.change(within(list).getByLabelText(/Length/), {target: {value: '80'}});
    expect((await preview(post, 'create')).sql[0]).toContain('VARCHAR(80)');
    select(within(list).getByLabelText(/Data type/), 'Integer');
    expect((await preview(post, 'create')).draft.arguments[0]).not.toHaveProperty('length');
    select(within(list).getByLabelText(/Data type/), 'Varchar');
    expect(within(list).getByLabelText(/Length/)).toHaveValue(80);
  });

  it('omits return-type defaults when selecting a return parameter', async () => {
    const post = await mount('create');
    fireEvent.change(screen.getByLabelText(/Function name/), {target: {value: 'Owned'}});
    fireEvent.change(screen.getByLabelText(/Exported entry point/), {target: {value: 'owned'}});
    fireEvent.change(screen.getByLabelText(/Installed library/), {target: {value: 'owned_udf'}});
    const list = screen.getByRole('group', {name: 'Ordered input arguments'});
    fireEvent.click(within(list).getByRole('button', {name: 'Add Ordered input arguments item'}));
    select(within(list).getByLabelText(/Input mechanism/), 'Descriptor');
    select(screen.getByLabelText(/Return declaration/), 'Parameter');
    fireEvent.change(screen.getByLabelText(/Return parameter position/), {target: {value: '1'}});
    const result = await preview(post, 'create');
    expect(result.draft).not.toHaveProperty('return_data_type');
    expect(result.draft).not.toHaveProperty('return_mechanism');
    expect(result.sql[0]).toContain('INTEGER BY DESCRIPTOR RETURNS PARAMETER 1');
  });

  it.each(['Entry Point', 'Module Name', 'Both'])('prefills and limits alteration to %s', async (mode) => {
    const post = await mount('alter', {entrypoint: 'owned', module_name: 'owned_udf'});
    select(screen.getByLabelText(/Alter library binding/), mode);
    const result = await preview(post, 'alter');
    expect(result.draft).not.toHaveProperty('arguments');
    expect(result.sql[0]).toContain('ALTER EXTERNAL FUNCTION "Owned"');
    if (mode === 'Entry Point') expect(result.draft).not.toHaveProperty('module_name');
    if (mode === 'Module Name') expect(result.draft).not.toHaveProperty('entrypoint');
  });

  it('submits an explicit empty comment', async () => {
    const post = await mount('comment', {description: 'Old'});
    fireEvent.change(screen.getByLabelText(/Comment/), {target: {value: ''}});
    expect((await preview(post, 'comment')).sql).toEqual(['COMMENT ON FUNCTION "Owned" IS NULL']);
  });

  it('submits an exact drop confirmation without a fabricated cascade switch', async () => {
    const post = await mount('drop');
    fireEvent.change(screen.getByLabelText(/Confirm function name/), {target: {value: 'Owned'}});
    expect((await preview(post, 'drop')).sql).toEqual(['DROP EXTERNAL FUNCTION "Owned"']);
    expect(screen.queryByLabelText(/cascade/i)).not.toBeInTheDocument();
  });

  it('preserves unknown and malformed list values for provider rejection', () => {
    const field = forms.create.fields.find(item => item.field_id === 'arguments');
    const input = [{data_type: 'INTEGER', length: 32, unknown: 'retained'}, null, 'bad'];
    expect(submittedFieldValue(field, input)).toEqual([
      {data_type: 'INTEGER', unknown: 'retained'}, null, 'bad']);
    expect(input[0].length).toBe(32);
    expect(submittedFieldValue(field, 'invalid')).toBe('invalid');
  });

  it('recurses through object and array fields without altering stored draft values', () => {
    const array = forms.create.fields.find(item => item.field_id === 'arguments');
    const field = {object_editor: {fields: [{...array, field_id: 'nested'}]}};
    const value = {nested: [{data_type: 'INTEGER', length: 32}], extra: 'keep'};
    expect(submittedFieldValue(field, value)).toEqual({nested: [{data_type: 'INTEGER'}], extra: 'keep'});
    expect(value.nested[0].length).toBe(32);
    expect(submittedFieldValue(field, null)).toBeNull();
    expect(submittedFieldValue({array_editor: {item_kind: 'string'}}, ['one', 'two'])).toEqual(['one', 'two']);
  });
});
