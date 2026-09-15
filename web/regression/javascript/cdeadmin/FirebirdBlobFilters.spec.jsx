import {execFileSync} from 'child_process';
import path from 'path';
import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import {resolveIconDefinition, semanticObjectIconKey} from '../../../pgadmin/static/js/cdeadmin_ui/icons/registry';

const cwd = path.resolve(__dirname, '../../../..');
const forms = JSON.parse(execFileSync('python3', ['-c',
  'import json; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
  'from pgadmin.cdeadmin.providers.firebird.blob_filters import form; ' +
  'print(json.dumps({action: form(action, ADMINISTRATION._field) ' +
  'for action in ["create", "comment", "drop"]}))',
], {cwd, encoding: 'utf8'}));

async function mount(action, native={}) {
  const resource = {resource_id: 'owned', resource_kind: 'blob-filter',
    display_name: 'Owned', display_path: ['Owned'], extensions: {firebird: {native}}};
  const post = jest.fn(async ({action: task}) => {
    if (task === 'resource_inspect') return resource;
    if (task === 'visual_admin_validate') return {valid: true};
    return {plan_id: 'owned', plan_digest: 'd', state: 'ready', execution_available: true};
  });
  await act(async () => render(<VisualAdministration focused resources={[resource]} post={post}
    setError={jest.fn()} initialResourceKind="blob-filter" initialOperationId={action}
    selectedResource={resource} catalog={{objects: [{resource_kind: 'blob-filter',
      title: 'BLOB filter', operations: [{operation_id: action, title: action,
        target_required: action !== 'create', form: forms[action]}]}]}} />));
  return post;
}

async function preview(post) {
  fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
  await waitFor(() => expect(post.mock.calls.some(([body]) => body.action === 'visual_admin_plan')).toBe(true));
  const request = post.mock.calls.filter(([body]) => body.action === 'visual_admin_plan').slice(-1)[0][0].request;
  const sql = JSON.parse(execFileSync('python3', ['-c',
    'import json,sys; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
    'from pgadmin.cdeadmin.providers.firebird.blob_filters import compile_operation; ' +
    'request=json.load(sys.stdin); print(json.dumps(compile_operation(' +
    'request["operation_id"], request["draft"], request.get("target_resource"))))',
  ], {cwd, input: JSON.stringify(request), encoding: 'utf8'}));
  return {draft: request.draft, sql};
}

function select(element, value) {
  fireEvent.mouseDown(element);
  fireEvent.click(screen.getByRole('option', {name: value, exact: true}));
}

function fillBindings() {
  fireEvent.change(screen.getByLabelText(/BLOB filter name/), {target: {value: 'Owned'}});
  fireEvent.change(screen.getByLabelText(/Exported entry point/), {target: {value: 'owned'}});
  fireEvent.change(screen.getByLabelText(/Installed library/), {target: {value: 'owned_filter'}});
}

describe('Firebird BLOB filter forms', () => {
  let height;
  beforeEach(() => { height = window.innerHeight; window.innerHeight = 1200; });
  afterEach(() => { window.innerHeight = height; });

  it('renders a distinct filter icon rather than a function or unknown icon', () => {
    expect(semanticObjectIconKey('blob-filter')).toBe('object.blob_filter');
    expect(resolveIconDefinition('object.blob_filter')).toMatchObject({
      key: 'object.blob_filter', kind: 'component', label: 'BLOB Filter'});
    expect(resolveIconDefinition('object.blob-filter')).toEqual(resolveIconDefinition('object.blob_filter'));
    expect(resolveIconDefinition('object.materialized-view')).toEqual(resolveIconDefinition('object.materialized_view'));
  });

  it('submits native signed subtype numbers without unrelated UDF fields', async () => {
    const post = await mount('create');
    fillBindings();
    fireEvent.change(screen.getByRole('spinbutton', {name: /^Input subtype/}), {target: {value: '-32768'}});
    fireEvent.change(screen.getByRole('spinbutton', {name: /^Output subtype/}), {target: {value: '1'}});
    const result = await preview(post);
    expect(result.sql[0]).toBe('DECLARE FILTER "Owned" INPUT_TYPE -32768 OUTPUT_TYPE 1 ENTRY_POINT \'owned\' MODULE_NAME \'owned_filter\'');
    expect(result.draft).not.toHaveProperty('arguments');
    expect(result.draft).not.toHaveProperty('input_mnemonic');
    expect(result.draft).not.toHaveProperty('output_mnemonic');
  });

  it.each(['Input', 'Output'])('preserves %s subtype drafts while submitting only the selected notation', async (side) => {
    const post = await mount('create');
    fillBindings();
    fireEvent.change(screen.getByRole('spinbutton', {name: /^Input subtype/}), {target: {value: '-81'}});
    fireEvent.change(screen.getByRole('spinbutton', {name: /^Output subtype/}), {target: {value: '1'}});
    select(screen.getByLabelText(new RegExp(side + ' subtype notation')), 'Mnemonic');
    fireEvent.change(screen.getByLabelText(new RegExp(side + ' mnemonic')), {target: {value: 'TEXT'}});
    const result = await preview(post);
    expect(result.sql[0]).toContain(side.toUpperCase() + '_TYPE "TEXT"');
    expect(result.draft).not.toHaveProperty(side.toLowerCase() + '_subtype');
    select(screen.getByLabelText(new RegExp(side + ' subtype notation')), 'Number');
    expect(screen.getByRole('spinbutton', {name: new RegExp('^' + side + ' subtype')})).toHaveValue(side === 'Input' ? -81 : 1);
    expect((await preview(post)).draft).not.toHaveProperty(side.toLowerCase() + '_mnemonic');
  });

  it('prefills comments and sends explicit removal', async () => {
    const post = await mount('comment', {description: 'Owned comment'});
    expect(screen.getByLabelText(/Comment/)).toHaveValue('Owned comment');
    fireEvent.change(screen.getByLabelText(/Comment/), {target: {value: ''}});
    expect((await preview(post)).sql).toEqual(['COMMENT ON FILTER "Owned" IS NULL']);
  });

  it('requires exact drop confirmation and explains the native cache limitation', async () => {
    const post = await mount('drop');
    expect(screen.getByText(/already cached filter code/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Confirm BLOB filter name/), {target: {value: 'Owned'}});
    expect((await preview(post)).sql).toEqual(['DROP FILTER "Owned"']);
    expect(screen.queryByLabelText(/cascade/i)).not.toBeInTheDocument();
  });
});
