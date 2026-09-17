import {execFileSync} from 'child_process';
import path from 'path';
import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {ObjectInspectorSection, VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';

const cwd = path.resolve(__dirname, '../../../..');
const forms = JSON.parse(execFileSync('python3', ['-c',
  'import json; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
  'from pgadmin.cdeadmin.providers.firebird import packages as p; ' +
  'print(json.dumps({op: p.form(op, ADMINISTRATION._field) ' +
  'for op in sorted(p.OPERATIONS - {"inspect"})}))',
], {cwd, encoding: 'utf8'}));

const owner = {resource_id: 'package-id', resource_kind: 'package', display_name: 'PK'};
const member = {resource_id: 'member-id', resource_kind: 'function',
  display_name: 'F', display_path: ['PK', 'F'], extensions: {firebird: {native: {
    package: 'PK', administration: {allowed_operations: ['inspect', 'grant', 'revoke'],
      definition_owner: owner, reason: 'Defined by the package header and body.'},
  }}}};

async function memberEditor(onOpenDefinitionOwner) {
  const setError = jest.fn();
  const post = jest.fn(async () => member);
  await act(async () => render(<VisualAdministration objectEditor
    resources={[member]} selectedResource={member} resourceGeneration="owned-generation"
    initialResourceKind="function" initialOperationId="inspect"
    post={post} setError={setError} onOpenDefinitionOwner={onOpenDefinitionOwner}
    catalog={{objects: [{resource_kind: 'function', title: 'Function',
      editor: {sections: ['properties', 'privileges']},
      operations: ['inspect', 'alter', 'drop', 'grant', 'revoke'].map((op) => ({
        operation_id: op, title: op, target_required: true,
        execution_available: true, form: {fields: []},
      }))}]}} />));
  return {post, setError};
}

describe('Firebird package definitions', () => {
  it('shows and clears the native invalid-body warning when metadata changes', () => {
    const warning = execFileSync('python3', ['-c',
      'from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
      'from pgadmin.cdeadmin.providers.firebird.packages import INVALID_BODY_WARNING; ' +
      'print(INVALID_BODY_WARNING)',
    ], {cwd, encoding: 'utf8'}).trim();
    const resource = (invalid) => ({...owner, extensions: {firebird: {native: {
      body_status: {validity: invalid ? 'invalid' : 'valid', source_available: true},
      catalog_warnings: invalid ? [warning] : [],
      property_sections: ['properties'],
    }}}});
    const {rerender} = render(<ObjectInspectorSection resource={resource(true)} />);
    expect(screen.getByRole('alert')).toHaveTextContent(warning);
    rerender(<ObjectInspectorSection resource={resource(false)} />);
    expect(screen.queryByText(warning)).toBeNull();
  });

  it('submits an unchanged displayed body when recreating the package', async () => {
    const header = 'BEGIN FUNCTION F RETURNS INTEGER; END';
    const body = 'BEGIN FUNCTION F RETURNS INTEGER AS BEGIN RETURN 1; END END';
    const selected = {...owner, display_path: ['PK'], extensions: {firebird: {native: {
      package_sql_security: 'INVOKER', header_source: header, body_source: body,
    }}}};
    const post = jest.fn(async ({action}) => {
      if (action === 'resource_inspect') return selected;
      if (action === 'visual_admin_validate') return {valid: true};
      return {plan_id: 'owned', plan_digest: 'd', state: 'ready', execution_available: true};
    });
    await act(async () => render(<VisualAdministration focused resources={[selected]}
      selectedResource={selected} post={post} setError={jest.fn()}
      initialResourceKind="package" initialOperationId="recreate"
      catalog={{objects: [{resource_kind: 'package', title: 'Package', operations: [{
        operation_id: 'recreate', title: forms.recreate.title, target_required: true,
        form: forms.recreate,
      }]}]}} />));
    await waitFor(() => expect(screen.getByLabelText(/Public header/)).toHaveValue(header));
    expect(screen.getByLabelText(/Package body/)).toHaveValue(body);
    fireEvent.change(screen.getByLabelText(/Confirm package name/), {target: {value: 'PK'}});
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(post.mock.calls.some(([request]) => request.action === 'visual_admin_plan')).toBe(true));
    const request = post.mock.calls.find(([value]) => value.action === 'visual_admin_plan')[0].request;
    expect(request.draft.body).toBe(body);
    expect(request.draft.header).toBe(header);
    expect(request.draft.sql_security).toBe('INVOKER');
    const sql = JSON.parse(execFileSync('python3', ['-c',
      'import json,sys; from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION; ' +
      'from pgadmin.cdeadmin.providers.firebird.packages import compile_operation; ' +
      'r=json.load(sys.stdin); print(json.dumps(compile_operation(' +
      'r["operation_id"], r["draft"], r.get("target_resource"))))',
    ], {cwd, input: JSON.stringify(request), encoding: 'utf8'}));
    expect(sql.slice(0, 2)).toEqual([
      'RECREATE PACKAGE "PK" SQL SECURITY INVOKER AS ' + header,
      'CREATE PACKAGE BODY "PK" AS ' + body,
    ]);
  });

  it('hides invalid standalone DDL and opens the exact owning package', async () => {
    const open = jest.fn(async () => {});
    await memberEditor(open);
    expect(screen.queryByRole('tab', {name: 'alter', exact: true})).toBeNull();
    expect(screen.queryByRole('tab', {name: 'drop', exact: true})).toBeNull();
    expect(screen.getByRole('tab', {name: 'grant', exact: true})).toBeInTheDocument();
    const button = screen.getByRole('button', {name: 'Open owning package: PK'});
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await waitFor(() => expect(open).toHaveBeenCalledWith(owner));
  });

  it('keeps navigation failures visible and permits a deliberate retry', async () => {
    const open = jest.fn().mockRejectedValue(new Error('Owner catalog expired'));
    const {setError} = await memberEditor(open);
    const button = screen.getByRole('button', {name: 'Open owning package: PK'});
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await waitFor(() => expect(setError).toHaveBeenCalledWith('Owner catalog expired'));
    expect(button).toBeEnabled();
    expect(open).toHaveBeenCalledTimes(1);
  });

  it('disables duplicate owning-object requests while the first is pending', async () => {
    let finish;
    const open = jest.fn(() => new Promise((resolve) => { finish = resolve; }));
    await memberEditor(open);
    const button = screen.getByRole('button', {name: 'Open owning package: PK'});
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    fireEvent.click(button);
    expect(open).toHaveBeenCalledTimes(1);
    await act(async () => finish());
    expect(button).toBeEnabled();
  });

  it.each(['INHERIT', 'INVOKER', 'DEFINER'])(
    'prefills the native %s security setting when altering the header', async (security) => {
      const selected = {...owner, display_path: ['PK'], extensions: {firebird: {native: {
        package_sql_security: security, header_source: 'BEGIN PROCEDURE P; END',
        body_source: 'BEGIN PROCEDURE P AS BEGIN END END',
      }}}};
      const post = jest.fn(async () => selected);
      await act(async () => render(<VisualAdministration focused
        resources={[selected]} selectedResource={selected} post={post} setError={jest.fn()}
        initialResourceKind="package" initialOperationId="alter"
        catalog={{objects: [{resource_kind: 'package', title: 'Package', operations: [{
          operation_id: 'alter', title: forms.alter.title, target_required: true,
          form: forms.alter,
        }]}]}} />));
      await waitFor(() => expect(screen.getByLabelText(/Public header/)).toHaveValue('BEGIN PROCEDURE P; END'));
      expect(screen.getByLabelText(/SQL SECURITY/)).toHaveTextContent(new RegExp(security, 'i'));
      expect(screen.queryByLabelText(/Package body/)).toBeNull();
      expect(screen.getByText(/existing body is marked invalid/)).toBeInTheDocument();
    });
});
