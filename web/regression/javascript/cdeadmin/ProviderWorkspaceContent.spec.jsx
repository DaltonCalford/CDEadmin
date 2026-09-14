/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {useState} from 'react';
import ProviderWorkspaceContent, {
  DatabaseTargetWorkspace,
  ObjectInspectorSection,
  ResultControls,
  RecordListAdminField,
  ServerProfileWorkspace,
  VisualAdministration,
  VisualAdminField,
  inspectorSections,
  initialObjectDraft,
  semanticCrossFilter,
  visibleFieldOptions,
  changedFieldDraft,
  administrationResourceId,
} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import getApiInstance from '../../../pgadmin/static/js/api_instance';

jest.mock('../../../pgadmin/static/js/api_instance');

describe('provider structured record controls', () => {
  it('uses only a verified provider rename identity', () => {
    const target = {resource_id: 'column:T:V', resource_kind: 'column'};
    const receipt = {previous_resource_id: 'column:T:V',
      resource_id: 'column:T:new%3Aname', resource_kind: 'column',
      native_identity_verified: true, committed_by_provider: true};
    expect(administrationResourceId(target, {})).toBe('column:T:V');
    expect(administrationResourceId(target, {provider_result: {
      resource_identity_change: receipt,
    }})).toBe('column:T:new%3Aname');
    for (const invalid of [
      {previous_resource_id: 'other'}, {resource_kind: 'table'},
      {native_identity_verified: false}, {committed_by_provider: false},
      {resource_id: ''}, {resource_id: null}, {resource_id: 42},
    ]) {
      expect(() => administrationResourceId(target, {provider_result: {
        resource_identity_change: {...receipt, ...invalid},
      }})).toThrow('identity transition could not be verified');
    }
  });
  it('preserves hidden computed result types and never guesses an explicit type choice', () => {
    const field = {field_id: 'data_type', control: 'select',
      require_explicit_choice: true,
      visible_when: {field_id: 'action', in: ['TYPE', 'TYPE COMPUTED']},
      options: [{value: 'INTEGER'}, {value: 'BLOB', visible_when: {
        field_id: 'action', equals: 'TYPE COMPUTED'}}]};
    let draft = {action: 'POSITION', data_type: 'BLOB'};
    draft = changedFieldDraft([field], draft, 'action', 'COMPUTED');
    expect(draft.data_type).toBe('BLOB');
    draft = changedFieldDraft([field], draft, 'action', 'TYPE COMPUTED');
    expect(draft.data_type).toBe('BLOB');
    draft = changedFieldDraft([field], draft, 'action', 'TYPE');
    expect(draft.data_type).toBe('');
    expect(changedFieldDraft([field], draft, 'action', 'TYPE COMPUTED').data_type).toBe('');
  });

  it('prefills precise provider type attributes without stringifying numbers', () => {
    const values = {data_type: 'NUMERIC', precision: 38, scale: 21};
    const fields = Object.keys(values).map((key) => ({field_id: key,
      initial_value_path: ['type_editor', key], submit_unchanged: true}));
    expect(initialObjectDraft(fields, {extensions: {firebird: {native: {
      type_editor: values,
    }}}})).toEqual(values);
    expect(initialObjectDraft([{field_id: 'data_type', default: null,
      initial_value_path: ['type_editor', 'data_type']}], {})).toEqual({data_type: ''});
  });

  it('filters actions using inspected native context and prefills column position', () => {
    const action = {field_id: 'action', control: 'select', default: 'COMPUTED',
      option_values_path: ['alteration', 'allowed_actions'], options:
        ['POSITION', 'COMPUTED', 'IDENTITY'].map((value) => ({value, label: value}))};
    const resource = {extensions: {firebird: {native: {alteration: {
      allowed_actions: ['POSITION', 'IDENTITY', 'UNDECLARED'], position: 8,
    }}}}};
    expect(visibleFieldOptions(action, {}, resource).options.map((o) => o.value))
      .toEqual(['POSITION', 'IDENTITY']);
    expect(initialObjectDraft([action, {field_id: 'position', control: 'number',
      initial_value_path: ['alteration', 'position']}], resource))
      .toEqual({action: 'POSITION', position: 8});
    for (const missing of [null, {}, {native: {alteration: {allowed_actions: []}}},
      {native: {alteration: {allowed_actions: 'POSITION'}}}]) {
      expect(visibleFieldOptions(action, {}, missing).options).toEqual([]);
      expect(initialObjectDraft([action], missing)).toEqual({action: ''});
    }
  });

  it('updates allowed actions when a different column is inspected', async () => {
    const field = {field_id: 'action', label: 'Alteration', control: 'select',
      required: true, default: 'POSITION',
      option_values_path: ['alteration', 'allowed_actions'],
      options: ['POSITION', 'IDENTITY', 'COMPUTED'].map((value) => ({value, label: value}))};
    const target = (name, allowed) => ({resource_id: 'column:T.' + name,
      resource_kind: 'column', display_name: name,
      extensions: {firebird: {native: {alteration: {allowed_actions: allowed}}}}});
    const first = target('A', ['POSITION', 'IDENTITY']);
    const second = target('B', ['COMPUTED']);
    const view = target('C', []);
    const resources = [first, second, view];
    const post = jest.fn(({target: requested}) => Promise.resolve(requested));
    const props = {resources, post, setError: jest.fn(), initialResourceKind: 'column',
      initialOperationId: 'alter', catalog: {objects: [{resource_kind: 'column',
        title: 'Column', operations: [{operation_id: 'alter', title: 'Alter',
          target_required: true, form: {fields: [field]}}]}]}};
    // The service returns an independently inspected resource, not the stale
    // values in a previously rendered draft.
    post.mockImplementation(() => Promise.resolve(first));
    const {rerender} = render(<VisualAdministration {...props} selectedResource={first} />);
    await waitFor(() => expect(screen.getByRole('combobox', {name: /Alteration/})).toHaveTextContent('POSITION'));
    post.mockImplementation(() => Promise.resolve(second));
    rerender(<VisualAdministration {...props} selectedResource={second} />);
    await waitFor(() => expect(screen.getByRole('combobox', {name: /Alteration/})).toHaveTextContent('COMPUTED'));
    post.mockImplementation(() => Promise.resolve(view));
    rerender(<VisualAdministration {...props} selectedResource={view} />);
    await waitFor(() => expect(screen.getByRole('combobox', {name: /Alteration/})).toHaveAttribute('aria-disabled', 'true'));
  });

  it('requires both alteration and type selectors before showing a dependent field', async () => {
    const target = {resource_id: 'column:T.V', resource_kind: 'column', display_name: 'V'};
    render(<VisualAdministration selectedResource={target} resources={[target]}
      post={async () => target} setError={jest.fn()} initialResourceKind="column"
      initialOperationId="alter" catalog={{objects: [{resource_kind: 'column', operations: [
        {operation_id: 'alter', title: 'Alter', target_required: true, form: {fields: [
          {field_id: 'action', label: 'Alteration', control: 'select', default: 'POSITION',
            options: [{value: 'POSITION', label: 'Position'}, {value: 'TYPE', label: 'Type'}]},
          {field_id: 'data_type', label: 'Data type', control: 'select', default: 'INTEGER',
            options: [{value: 'INTEGER', label: 'Integer'}, {value: 'VARCHAR', label: 'Varchar'},
              {value: 'BLOB', label: 'Blob', visible_when: {field_id: 'action', equals: 'TYPE'}}]},
          {field_id: 'length', label: 'Length', control: 'number', visible_when: {all: [
            {field_id: 'action', equals: 'TYPE'}, {field_id: 'data_type', in: ['VARCHAR']},
          ]}},
        ]}},
      ]}]}} />);
    await waitFor(() => expect(screen.getByRole('combobox', {name: 'Alteration'})).not.toHaveAttribute('aria-disabled', 'true'));
    expect(screen.queryByLabelText('Length')).not.toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Data type'}));
    expect(screen.queryByRole('option', {name: 'Blob'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('option', {name: 'Integer'}));
    fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Alteration'}));
    fireEvent.click(screen.getByRole('option', {name: 'Type'}));
    expect(screen.queryByLabelText('Length')).not.toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Data type'}));
    expect(screen.getByRole('option', {name: 'Blob'})).toBeInTheDocument();
    fireEvent.click(screen.getByRole('option', {name: 'Varchar'}));
    expect(screen.getByLabelText('Length')).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Data type'}));
    fireEvent.click(screen.getByRole('option', {name: 'Blob'}));
    fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Alteration'}));
    fireEvent.click(screen.getByRole('option', {name: 'Position'}));
    expect(screen.queryByLabelText('Length')).not.toBeInTheDocument();
    expect(screen.getByRole('combobox', {name: 'Data type'})).toHaveTextContent('Integer');
  });

  it.each(['text', 'multiline', 'code', 'json', 'number', 'password', 'boolean'])('propagates disabled state to %s controls', (control) => {
    render(<VisualAdminField disabled field={{field_id: 'test', label: 'Field', control}}
      value={control === 'boolean' ? false : ''} onChange={jest.fn()} />);
    expect(screen.getByLabelText('Field')).toBeDisabled();
    if (control === 'password') expect(screen.getByRole('button', {name: 'Show password'})).toBeDisabled();
  });

  it('propagates disabled state through structured record controls', () => {
    render(<VisualAdminField disabled field={{field_id: 'items', label: 'Items',
      control: 'json', array_editor: {item_kind: 'object', fields: [
        {field_id: 'name', label: 'Name', control: 'text'},
        {field_id: 'choice', label: 'Choice', control: 'select', options: [{value: 'A', label: 'A'}]},
      ]}}} value={[{name: 'first', choice: 'A'}, {name: 'second', choice: 'A'}]} onChange={jest.fn()} />);
    for (const input of screen.getAllByRole('textbox')) expect(input).toBeDisabled();
    for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled();
    for (const choice of screen.getAllByRole('combobox')) expect(choice).toHaveAttribute('aria-disabled', 'true');
  });

  it.each(['select', 'multiselect'])('disables %s during metadata loading and renders provider guidance', async (control) => {
    const target = {resource_id: 'role:RDB$ADMIN', resource_kind: 'role', display_name: 'RDB$ADMIN'};
    let finish;
    const pending = new Promise((resolve) => { finish = resolve; });
    render(<VisualAdministration selectedResource={target} resources={[target]}
      post={() => pending} setError={jest.fn()} initialResourceKind="role"
      initialOperationId="alter" catalog={{objects: [{resource_kind: 'role', operations: [
        {operation_id: 'alter', title: 'Alter', target_required: true, form: {fields: [
          {field_id: 'choice', label: 'Mapping choice', control, help: 'Replaces the reserved mapping.',
            default: control === 'select' ? 'SET' : [], options: [{value: 'SET', label: 'SET'}, {value: 'DROP', label: 'DROP'}]},
        ]}},
      ]}]}} />);
    const choice = screen.getByRole('combobox', {name: 'Mapping choice'});
    expect(choice).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText('Replaces the reserved mapping.')).toBeInTheDocument();
    fireEvent.mouseDown(choice);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    await act(async () => { finish(target); });
    await waitFor(() => expect(choice).not.toHaveAttribute('aria-disabled', 'true'));
    fireEvent.mouseDown(choice);
    expect(screen.getByRole('option', {name: /DROP/})).toBeInTheDocument();
  });

  it.each(['RDB$ADMIN', 'OTHER_SYSTEM', 'READER'])('limits named system-role operations for %s', async (name) => {
    const target = {resource_id: 'role:' + name, resource_kind: 'role', display_name: name,
      extensions: {firebird: {native: {system_object: name !== 'READER'}}}};
    const post = jest.fn(async () => target);
    render(<VisualAdministration selectedResource={target} resources={[target]}
      post={post} setError={jest.fn()} initialResourceKind="role"
      initialOperationId="configure_admin_mapping" catalog={{objects: [{resource_kind: 'role',
        operations: [
          {operation_id: 'inspect', title: 'Inspect', form: {fields: []}},
          {operation_id: 'configure_admin_mapping', title: 'Configure Windows administrator mapping',
            target_required: true, allow_system_target: true, target_resource_names: ['RDB$ADMIN'],
            form: {fields: [{field_id: 'mapping_action', label: 'Windows administrator mapping',
              control: 'select', default: 'SET', options: [{value: 'SET', label: 'SET'}, {value: 'DROP', label: 'DROP'}]}]}},
        ]}]}} />);
    await waitFor(() => expect(post).toHaveBeenCalled());
    if (name === 'RDB$ADMIN') {
      await waitFor(() => expect(screen.getByRole('combobox', {name: 'Windows administrator mapping'})).toBeInTheDocument());
    } else {
      expect(screen.queryByRole('combobox', {name: 'Windows administrator mapping'})).not.toBeInTheDocument();
      expect(screen.queryByRole('option', {name: 'Configure Windows administrator mapping'})).not.toBeInTheDocument();
    }
  });

  it('blocks role confirmation until asynchronous inspection is complete', async () => {
    const target = {resource_id: 'role:reader', resource_kind: 'role', display_name: 'reader'};
    let finishInspection;
    const inspection = new Promise((resolve) => { finishInspection = resolve; });
    const post = jest.fn(({action}) => {
      if (action === 'resource_inspect') return inspection;
      if (action === 'visual_admin_validate') return Promise.resolve({valid: true});
      return Promise.resolve({state: 'ready', plan_id: 'drop-role', execution_available: true});
    });
    render(<VisualAdministration resources={[target]} selectedResource={target}
      initialResourceKind="role" initialOperationId="drop" post={post}
      setError={jest.fn()} catalog={{objects: [{resource_kind: 'role', title: 'Role',
        operations: [{operation_id: 'drop', title: 'Drop', target_required: true,
          form: {fields: [{field_id: 'confirmation', label: 'Confirmation',
            control: 'text', required: true}]}}]}]}} />);
    const confirmation = screen.getByRole('textbox', {name: /Confirmation/});
    expect(confirmation).toBeDisabled();
    expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeDisabled();
    await act(async () => { finishInspection(target); });
    await waitFor(() => expect(confirmation).toBeEnabled());
    fireEvent.change(confirmation, {target: {value: 'reader'}});
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(post).toHaveBeenCalledWith({action: 'visual_admin_plan', request: {
      resource_kind: 'role', operation_id: 'drop', target_resource: target,
      draft: {confirmation: 'reader'},
    }}));
    expect(confirmation).toHaveValue('reader');
  });

  it('prefills Firebird system privileges as a typed selection, not JSON text', () => {
    const privileges = ['USER_MANAGEMENT', 'PROFILE_ANY_ATTACHMENT'];
    const fields = [{field_id: 'system_privileges', control: 'multiselect',
      initial_value_path: ['system_privileges']}];
    const result = initialObjectDraft(fields, {
      extensions: {firebird: {native: {system_privileges: privileges}}},
    });
    expect(result.system_privileges).toEqual(privileges);
    expect(Array.isArray(result.system_privileges)).toBe(true);
    expect(initialObjectDraft(fields, {extensions: {firebird: {native: {
      system_privileges: [],
    }}}}).system_privileges).toEqual([]);
  });
  it('keeps decimal-text integer fields exact across input and JSON transport', () => {
    const changed = jest.fn();
    render(<VisualAdminField field={{field_id: 'topology_version',
      label: 'Topology version', control: 'text', required: true}}
    value="" onChange={changed} />);
    const input = screen.getByLabelText(/Topology version/);
    expect(input.type).toBe('text');
    for (const value of ['9007199254740993', '9223372036854775807']) {
      fireEvent.change(input, {target: {value}});
      const result = changed.mock.calls.at(-1)[0];
      expect(result).toBe(value);
      expect(JSON.parse(JSON.stringify({value: result})).value).toBe(value);
    }
  });
  it('serializes native JSON text and clones native structured records', () => {
    const rule = {score: {$gte: 0}};
    const records = [{name: 'a', type: 'INTEGER'}];
    const source = {extensions: {mongodb: {native: {rule, records}}}};
    const result = initialObjectDraft([
      {field_id: 'rule', control: 'json', initial_value_path: ['rule']},
      {field_id: 'records', control: 'json', array_editor: {item_kind: 'object'},
        initial_value_path: ['records']},
      {field_id: 'object', control: 'json', object_editor: {fields: []},
        initial_value_path: ['rule']},
    ], source);
    expect(result.rule).toBe(JSON.stringify(rule, null, 2));
    expect(result.records).toEqual(records);
    expect(result.records).not.toBe(records);
    expect(result.records[0]).not.toBe(records[0]);
    expect(result.object).toEqual(rule);
    expect(result.object).not.toBe(rule);
  });
  it('submits a prefilled rule only when explicit replacement is selected', async () => {
    const rule = {score: {$gte: 0}};
    const target = {resource_id: 'validator:items', resource_kind: 'validator',
      display_name: 'Rule', extensions: {mongodb: {native: {options: {
        validator: rule, validationAction: 'error',
      }}}}};
    const post = jest.fn(async ({action}) => {
      if (action === 'resource_inspect') return target;
      if (action === 'visual_admin_validate') return {valid: true};
      return {state: 'ready', plan_id: 'replace-rule', plan_digest: 'digest',
        execution_available: true};
    });
    render(<VisualAdministration resources={[target]} selectedResource={target}
      initialResourceKind="validator" initialOperationId="alter" post={post}
      setError={jest.fn()} catalog={{objects: [{resource_kind: 'validator',
        title: 'Validator', operations: [{operation_id: 'alter', title: 'Alter',
          target_required: true, form: {fields: [
            {field_id: 'replace_rule', label: 'Replace rule', control: 'boolean', default: false},
            {field_id: 'validator', label: 'Rule', control: 'json', default: {},
              initial_value_path: ['options', 'validator'], submit_unchanged: true,
              visible_when: {field_id: 'replace_rule', equals: true}},
            {field_id: 'validation_action', label: 'Action', control: 'text',
              initial_value_path: ['options', 'validationAction']},
          ]}}]}]}} />);
    await waitFor(() => expect(screen.getByRole('textbox', {name: 'Action'})).toHaveValue('error'));
    expect(screen.queryByRole('textbox', {name: 'Rule'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', {name: 'Replace rule'}));
    expect(screen.getByRole('textbox', {name: 'Rule'})).toHaveValue(JSON.stringify(rule, null, 2));
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(post).toHaveBeenCalledWith({action: 'visual_admin_plan', request: {
      resource_kind: 'validator', operation_id: 'alter', target_resource: target,
      draft: {replace_rule: true, validator: JSON.stringify(rule, null, 2)},
    }}));
    fireEvent.click(screen.getByRole('checkbox', {name: 'Replace rule'}));
    fireEvent.change(screen.getByRole('textbox', {name: 'Action'}), {target: {value: 'warn'}});
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(post).toHaveBeenCalledWith({action: 'visual_admin_plan', request: {
      resource_kind: 'validator', operation_id: 'alter', target_resource: target,
      draft: {replace_rule: false, validation_action: 'warn'},
    }}));
  });
  it('submits both Firebird role option removals without conflating them with membership removal', async () => {
    const target = {resource_id: 'role:readers', resource_kind: 'role', display_name: 'readers'};
    const post = jest.fn(async ({action}) => {
      if (action === 'resource_inspect') return target;
      if (action === 'visual_admin_validate') return {valid: true};
      return {state: 'ready', plan_id: 'role-options', plan_digest: 'digest', execution_available: true};
    });
    render(<VisualAdministration resources={[target]} selectedResource={target}
      initialResourceKind="role" initialOperationId="revoke" post={post}
      setError={jest.fn()} catalog={{objects: [{resource_kind: 'role', title: 'Role',
        operations: [{operation_id: 'revoke', title: 'Revoke', target_required: true,
          form: {fields: [
            {field_id: 'member', label: 'Member name', control: 'text', required: true},
            {field_id: 'default_role', label: 'Remove default status only', control: 'boolean', default: false},
            {field_id: 'admin_option_only', label: 'Revoke admin option only', control: 'boolean', default: false},
            {field_id: 'confirmation', label: 'Confirmation', control: 'text', required: true},
          ]}}]}]}} />);
    await waitFor(() => expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeEnabled());
    fireEvent.change(screen.getByRole('textbox', {name: /Member name/}), {target: {value: 'operator'}});
    fireEvent.change(screen.getByRole('textbox', {name: /Confirmation/}), {target: {value: 'readers'}});
    fireEvent.click(screen.getByRole('checkbox', {name: 'Remove default status only'}));
    fireEvent.click(screen.getByRole('checkbox', {name: 'Revoke admin option only'}));
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(post).toHaveBeenCalledWith({action: 'visual_admin_plan', request: {
      resource_kind: 'role', operation_id: 'revoke', target_resource: target,
      draft: {member: 'operator', confirmation: 'readers', default_role: true, admin_option_only: true},
    }}));
  });
  it('hides Firebird replacement privileges when clearing the complete set', async () => {
    const target = {resource_id: 'role:reader', resource_kind: 'role', display_name: 'reader'};
    const post = jest.fn(async ({action}) => {
      if (action === 'resource_inspect') return target;
      if (action === 'visual_admin_validate') return {valid: true};
      return {state: 'ready', plan_id: 'role-privileges', plan_digest: 'digest', execution_available: true};
    });
    render(<VisualAdministration resources={[target]} selectedResource={target}
      initialResourceKind="role" initialOperationId="alter" post={post}
      setError={jest.fn()} catalog={{objects: [{resource_kind: 'role', title: 'Role',
        operations: [{operation_id: 'alter', title: 'Alter', target_required: true,
          form: {fields: [
            {field_id: 'system_privileges', label: 'Replacement system privileges', control: 'multiselect', default: [],
              help_text: 'Replaces the entire privilege set.',
              options: [{value: 'USER_MANAGEMENT', label: 'USER_MANAGEMENT'}],
              visible_when: {field_id: 'drop_system_privileges', equals: false}},
            {field_id: 'drop_system_privileges', label: 'Drop all system privileges', control: 'boolean', default: false},
          ]}}]}]}} />);
    await waitFor(() => expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeEnabled());
    expect(screen.getByText('Replaces the entire privilege set.')).toBeInTheDocument();
    const selector = screen.getByRole('combobox', {name: 'Replacement system privileges'});
    fireEvent.change(selector.parentElement.querySelector('input'), {target: {value: 'USER_MANAGEMENT'}});
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(post).toHaveBeenCalledWith({action: 'visual_admin_plan', request: {
      resource_kind: 'role', operation_id: 'alter', target_resource: target,
      draft: {system_privileges: ['USER_MANAGEMENT'], drop_system_privileges: false},
    }}));
    fireEvent.click(screen.getByRole('checkbox', {name: 'Drop all system privileges'}));
    expect(screen.queryByRole('combobox', {name: 'Replacement system privileges'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(post).toHaveBeenCalledWith({action: 'visual_admin_plan', request: {
      resource_kind: 'role', operation_id: 'alter', target_resource: target,
      draft: {drop_system_privileges: true},
    }}));
  });
  it('refreshes Firebird index statistics without replaying the catalog state', async () => {
    const target = {resource_id: 'index:items:ix', resource_kind: 'index',
      display_name: 'ix', extensions: {firebird: {native: {state: {active: true}}}}};
    const post = jest.fn(async ({action}) => {
      if (action === 'resource_inspect') return target;
      if (action === 'visual_admin_validate') return {valid: true};
      return {state: 'ready', plan_id: 'index-statistics', plan_digest: 'digest',
        execution_available: true};
    });
    render(<VisualAdministration resources={[target]} selectedResource={target}
      initialResourceKind="index" initialOperationId="alter" post={post}
      setError={jest.fn()} catalog={{objects: [{resource_kind: 'index',
        title: 'Index', operations: [{operation_id: 'alter', title: 'Alter',
          target_required: true, form: {fields: [
            {field_id: 'active', label: 'Active', control: 'boolean',
              initial_value_path: ['state', 'active']},
            {field_id: 'refresh_statistics', label: 'Recalculate index statistics',
              control: 'boolean', default: false},
          ]}}]}]}} />);
    await waitFor(() => expect(screen.getByRole('checkbox', {name: 'Active'})).toBeChecked());
    fireEvent.click(screen.getByRole('checkbox', {name: 'Recalculate index statistics'}));
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(post).toHaveBeenCalledWith({action: 'visual_admin_plan', request: {
      resource_kind: 'index', operation_id: 'alter', target_resource: target,
      draft: {refresh_statistics: true},
    }}));
    fireEvent.click(screen.getByRole('checkbox', {name: 'Active'}));
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(post).toHaveBeenCalledWith({action: 'visual_admin_plan', request: {
      resource_kind: 'index', operation_id: 'alter', target_resource: target,
      draft: {active: false, refresh_statistics: true},
    }}));
  });
  it('shows TTL input only when requested and preserves zero seconds in the draft', async () => {
    const target = {resource_id: 'index:ttl', resource_kind: 'index',
      display_name: 'ttl'};
    const post = jest.fn(async ({action}) => {
      if (action === 'resource_inspect') return target;
      if (action === 'visual_admin_validate') return {valid: true};
      return {state: 'ready', plan_id: 'ttl-plan', plan_digest: 'digest',
        execution_available: true};
    });
    render(<VisualAdministration resources={[target]} selectedResource={target}
      initialResourceKind="index" initialOperationId="alter" post={post}
      setError={jest.fn()} catalog={{objects: [{resource_kind: 'index',
        title: 'Index', operations: [{operation_id: 'alter', title: 'Alter',
          target_required: true, form: {fields: [
            {field_id: 'change_ttl', label: 'Change TTL', control: 'boolean', default: false},
            {field_id: 'ttl_seconds', label: 'Expire after seconds', control: 'number',
              required: true, visible_when: {field_id: 'change_ttl', equals: true}},
          ]}}]}]}} />);
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeEnabled());
    fireEvent.click(screen.getByRole('checkbox', {name: 'Change TTL'}));
    fireEvent.change(screen.getByRole('spinbutton', {name: /Expire after seconds/}),
      {target: {value: '0'}});
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(post).toHaveBeenCalledWith({action: 'visual_admin_plan', request: {
      resource_kind: 'index', operation_id: 'alter', target_resource: target,
      draft: {change_ttl: true, ttl_seconds: 0},
    }}));
    fireEvent.click(screen.getByRole('checkbox', {name: 'Change TTL'}));
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeDisabled();
  });
  let originalHeight;
  beforeEach(() => {
    originalHeight = window.innerHeight;
    // The shared virtual-grid test setup assigns every element height 800.
    // Give select popovers a viewport that accommodates that mocked geometry.
    window.innerHeight = 1200;
  });
  afterEach(() => { window.innerHeight = originalHeight; });
  it('preserves MongoDB key order and selected native key types', () => {
    const field = {label: 'Ordered index keys', array_editor: {
      item_kind: 'object', fields: [
        {field_id: 'field', label: 'Document field path', control: 'text'},
        {field_id: 'kind', label: 'Key type', control: 'select',
          default: 'ascending', options: [
            {value: 'ascending', label: 'Ascending'},
            {value: 'descending', label: 'Descending'},
          ]},
      ],
    }};
    const onChange = jest.fn();
    render(<RecordListAdminField field={field} value={[
      {field: 'a', kind: 'ascending'}, {field: 'b', kind: 'descending'},
    ]} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', {name: 'Ordered index keys 2: Move up'}));
    expect(onChange).toHaveBeenLastCalledWith([
      {field: 'b', kind: 'descending'}, {field: 'a', kind: 'ascending'},
    ]);
    fireEvent.mouseDown(screen.getAllByRole('combobox', {name: 'Key type'})[0]);
    fireEvent.click(screen.getByRole('option', {name: 'Descending'}));
    expect(onChange).toHaveBeenLastCalledWith([
      {field: 'a', kind: 'descending'}, {field: 'b', kind: 'descending'},
    ]);
  });
  const properties = {field_id: 'properties', label: 'Constraint properties',
    control: 'json', default: {kind: 'CHECK', expression: ''},
    object_editor: {fields: [
      {field_id: 'kind', label: 'Constraint type', control: 'text'},
      {field_id: 'expression', label: 'Expression', control: 'text',
        visible_when: {field_id: 'kind', equals: 'CHECK'}},
    ]}};
  it('edits a single structured object without record-list actions', () => {
    const onChange = jest.fn();
    render(<VisualAdminField field={properties} value={undefined} onChange={onChange} />);
    expect(screen.getByLabelText('Constraint type')).toHaveValue('CHECK');
    fireEvent.change(screen.getByLabelText('Expression'), {target: {value: 'ID > 0'}});
    expect(onChange).toHaveBeenCalledWith({kind: 'CHECK', expression: 'ID > 0'});
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
  it('loads serialized objects and preserves invalid or unknown fields', () => {
    const onChange = jest.fn();
    const {rerender} = render(<VisualAdminField field={properties}
      value={'{"kind":"CHECK","expression":"ID > 0"}'} onChange={onChange} />);
    expect(screen.getByLabelText('Expression')).toHaveValue('ID > 0');
    for (const invalid of ['bad json', '[]', '{"unknown":true}', 'null']) {
      rerender(<VisualAdminField field={properties} value={invalid} onChange={onChange} />);
      expect(screen.getByRole('alert')).toHaveTextContent('original value has not been changed');
    }
    expect(onChange).not.toHaveBeenCalled();
  });
  it('submits an object draft to validation and planning and invalidates edited plans', async () => {
    const post = jest.fn(async ({action}) => action === 'visual_admin_validate' ?
      {valid: true} : {plan_id: 'constraint-plan', plan_digest: 'digest',
        state: 'ready', execution_available: true});
    render(<VisualAdministration resources={[]} post={post} setError={jest.fn()}
      initialResourceKind="constraint" initialOperationId="create"
      catalog={{objects: [{resource_kind: 'constraint', title: 'Constraint',
        operations: [{operation_id: 'create', title: 'Create', target_required: false,
          form: {fields: [properties]}}]}]}} />);
    fireEvent.change(screen.getByLabelText('Expression'), {target: {value: 'ID > 0'}});
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeEnabled());
    for (const action of ['visual_admin_validate', 'visual_admin_plan']) {
      expect(post).toHaveBeenCalledWith({action, request: {
        resource_kind: 'constraint', operation_id: 'create', target_resource: null,
        draft: {properties: {kind: 'CHECK', expression: 'ID > 0'}},
      }});
    }
    fireEvent.change(screen.getByLabelText('Expression'), {target: {value: 'ID > 10'}});
    expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeDisabled();
  });
  const field = {field_id: 'columns', label: 'Columns', array_editor: {
    item_kind: 'object', fields: [
      {field_id: 'name', label: 'Column name', control: 'text', required: true},
      {field_id: 'nullable', label: 'Nullable', control: 'boolean', default: true},
    ],
  }};
  function Harness() {
    const [value, setValue] = useState([]);
    return <><RecordListAdminField field={field} value={value} onChange={setValue} />
      <output data-testid="record-value">{JSON.stringify(value)}</output></>;
  }
  it('adds, edits, reorders and removes draft records with defaults', () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', {name: 'Add Columns item'}));
    fireEvent.change(screen.getByLabelText(/Column name/), {target: {value: 'A'}});
    expect(screen.getByTestId('record-value')).toHaveTextContent('"nullable":true');
    fireEvent.click(screen.getByRole('button', {name: 'Add Columns item'}));
    fireEvent.change(screen.getAllByLabelText(/Column name/)[1], {target: {value: 'B'}});
    fireEvent.click(screen.getByRole('button', {name: 'Columns 2: Move up'}));
    expect(screen.getAllByLabelText(/Column name/)[0]).toHaveValue('B');
    fireEvent.click(screen.getByRole('button', {name: 'Columns 1: Move down'}));
    expect(screen.getAllByLabelText(/Column name/)[0]).toHaveValue('A');
    fireEvent.click(screen.getByRole('button', {name: 'Columns 1: Remove'}));
    expect(screen.getByLabelText(/Column name/)).toHaveValue('B');
  });
  it('filters per-record type choices and resets them without changing other records', () => {
    const schema = {label: 'Columns', array_editor: {item_kind: 'object', fields: [
      {field_id: 'mode', label: 'Mode', control: 'text', default: 'stored'},
      {field_id: 'type', label: 'Type', control: 'select', default: 'INTEGER', options: [
        {value: 'INTEGER', label: 'Integer'},
        {value: 'DOMAIN', label: 'Domain', visible_when: {field_id: 'mode', equals: 'stored'}},
      ]},
    ]}};
    function Records() {
      const [value, setValue] = useState([{mode: 'stored', type: 'DOMAIN'},
        {mode: 'stored', type: 'DOMAIN'}]);
      return <><RecordListAdminField field={schema} value={value} onChange={setValue} />
        <output data-testid="record-types">{JSON.stringify(value)}</output></>;
    }
    render(<Records />);
    fireEvent.change(screen.getAllByLabelText('Mode')[0], {target: {value: 'computed'}});
    expect(JSON.parse(screen.getByTestId('record-types').textContent)).toEqual([
      {mode: 'computed', type: 'INTEGER'}, {mode: 'stored', type: 'DOMAIN'},
    ]);
    fireEvent.mouseDown(screen.getAllByLabelText('Type')[0]);
    expect(screen.queryByRole('option', {name: 'Domain'})).not.toBeInTheDocument();
    expect(screen.getByRole('option', {name: 'Integer'})).toBeInTheDocument();
  });
  it.each(['invalid JSON', '[42]', '[{"name":"A","unsupported":true}]']) (
    'preserves unsupported input %s without silently discarding it', (value) => {
      const onChange = jest.fn();
      render(<RecordListAdminField field={field} value={value} onChange={onChange} />);
      expect(screen.getByRole('alert')).toHaveTextContent('original value has not been changed');
      expect(onChange).not.toHaveBeenCalled();
      expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });
  it('edits primitive lists and accepts serialized existing lists', () => {
    const onChange = jest.fn();
    render(<RecordListAdminField field={{label: 'Index columns',
      array_editor: {item_kind: 'string'}}} value={'["A"]'} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Index columns 1'), {target: {value: 'B'}});
    expect(onChange).toHaveBeenCalledWith(['B']);
  });
  it('renders only fields applicable to the selected constraint type', () => {
    const constraint = {label: 'Constraints', array_editor: {
      item_kind: 'object', fields: [
        {field_id: 'kind', label: 'Type', control: 'text'},
        {field_id: 'expression', label: 'Check expression', control: 'text',
          visible_when: {field_id: 'kind', equals: 'CHECK'}},
        {field_id: 'columns', label: 'Columns', control: 'json', default: [],
          array_editor: {item_kind: 'string'},
          visible_when: {field_id: 'kind', in: ['UNIQUE', 'FOREIGN KEY']}},
      ],
    }};
    const onChange = jest.fn();
    const {rerender} = render(<RecordListAdminField field={constraint}
      value={[{kind: 'CHECK', expression: 'A > 0'}]} onChange={onChange} />);
    expect(screen.getByLabelText('Check expression')).toHaveValue('A > 0');
    expect(screen.queryByRole('group', {name: 'Columns'})).not.toBeInTheDocument();
    rerender(<RecordListAdminField field={constraint}
      value={[{kind: 'UNIQUE', columns: ['A']}]} onChange={onChange} />);
    expect(screen.queryByLabelText('Check expression')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Columns 1'), {target: {value: 'B'}});
    expect(onChange).toHaveBeenCalledWith([{kind: 'UNIQUE', columns: ['B']}]);
  });
});

const bootstrap = {
  endpoint: {
    provider_id: 'org.cdeadmin.mysql',
    verified_runtime_family: 'mysql',
  },
  grid_workspace: {
    schema: 'cdeadmin.provider-grid-workspace.v1',
    adoption_state: 'passed',
    activation_gates: [{
      gate_id: 'public_grid_boundary', state: 'passed',
    }],
    runtime_gate: {
      gate_id: 'live_provider_verification', state: 'passed',
    },
  },
  languages: [{
    language_profile: 'mysql-sql', title: 'MySQL SQL',
    starter_source: 'SELECT 42',
    source_presets: [{label: 'MySQL scalar query', source: 'SELECT 42'}],
  }],
  resource_page: {items: [{
    resource_id: 'database:example',
    resource_kind: 'database',
    display_name: 'example',
    authority_path: ['database', 'example'],
  }]},
  visual_admin: {
    engine_id: 'mysql',
    engine_name: 'MySQL',
    objects: [{
      resource_kind: 'database',
      title: 'Database',
      operations: [{
        operation_id: 'create',
        title: 'Create',
        mutation_class: 'admin',
        target_required: false,
        confirmation_required: false,
        blockers: ['provider_native_planner_unavailable'],
        form: {fields: [{
          field_id: 'name', label: 'Name', control: 'text', required: true,
        }]},
      }],
    }],
  },
  operational_workspace: {
    schema: 'cdeadmin.operational-workspace.v1',
    engine_id: 'mysql',
    distributed: false,
    categories: ['runtime'],
    topology: {available: false, resource_kinds: []},
    facets: [{
      facet_id: 'health',
      title: 'Server and cluster health',
      category: 'runtime',
      summary: 'Provider-reported availability and health state.',
      catalog_state: 'operational',
      unavailable_reason: null,
      resource_kinds: ['database'],
      discovered_resource_count: 1,
      operations: [{
        operation_id: 'create', resource_kind: 'database', title: 'Create',
      }],
    }],
  },
  semantic_models: {
    items: [],
    capabilities: {
      designer: true, revision_history: true, validation: true,
      lineage: true, query_builder: true, pivot_cellset: true,
      execution_available: true,
      provider_compiler: {execution_available: true},
      time_intelligence_operations: ['as_of', 'range', 'period_to_date',
        'period_comparison'],
      time_intelligence_periods: ['day', 'week', 'month', 'quarter', 'year',
        'fiscal_quarter', 'fiscal_year'],
      analytical_window_operations: ['running_sum', 'moving_average', 'lag'],
      scheduled_report_execution: false,
      analytical_profile: {
        title: 'Relational and multidimensional',
        semantic_family: 'relational',
        source_kinds: ['table', 'view', 'materialized-view'],
        source_classifications: ['fact', 'dimension', 'bridge', 'lookup'],
        dimension_kinds: ['attribute', 'time', 'geography'],
        relationship_kinds: ['join', 'bridge'],
        measure_kinds: ['aggregate', 'calculated'],
        grain_vocabulary: 'fact-key',
      },
    },
  },
};

describe('ProviderWorkspaceContent', () => {
  let originalViewportHeight;
  beforeEach(() => {
    originalViewportHeight = window.innerHeight;
    // Shared virtual-grid geometry gives every element an 800px height.
    window.innerHeight = 1200;
  });
  afterEach(() => { window.innerHeight = originalViewportHeight; });
  let api;

  beforeEach(() => {
    api = {get: jest.fn(), post: jest.fn()};
    getApiInstance.mockReturnValue(api);
    api.get.mockResolvedValue({data: {data: bootstrap}});
  });

  it('keeps stacked status messages in a keyboard-accessible bounded region', async () => {
    api.get.mockResolvedValue({data: {data: {...bootstrap,
      engine_contracts: {state: 'blocked', dialect: {state: 'blocked'},
        metrics: {state: 'passed'}}, languages: [{
        language_profile: 'firebird-sql', title: 'Firebird SQL',
        starter_source: 'SELECT 1 FROM RDB$DATABASE',
      }]}}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    const limit = await screen.findByLabelText('Maximum fetched rows');
    fireEvent.change(limit, {target: {value: '1000001'}});
    fireEvent.click(screen.getByRole('button', {name: 'Run', exact: true}));
    const status = screen.getByRole('region', {name: 'Provider workspace status'});
    expect(status).toHaveAttribute('tabindex', '0');
    expect(status).toHaveStyle({maxHeight: '40%', overflow: 'auto', flexShrink: '0'});
    expect(status).toContainElement(screen.getByText(/Maximum fetched rows must be an integer/));
    expect(status).toContainElement(screen.getByLabelText('Exact engine contract status'));
    expect(status).toContainElement(screen.getByLabelText('Provider grid activation status'));
    expect(api.post).not.toHaveBeenCalled();
  });

  it('submits ordered Firebird query parameters through its language contract', async () => {
    api.get.mockResolvedValue({data: {data: {...bootstrap, languages: [{
      language_profile: 'firebird-sql', title: 'Firebird SQL',
      starter_source: 'SELECT CAST(? AS INTEGER) FROM RDB$DATABASE',
      parameter_shape: 'array', parameter_hint: 'Ordered ? placeholders',
    }]}}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {data: {
      open_session: {session_id: 'firebird-session'},
      execute: {occurrence_id: 'firebird-occurrence'},
      poll: {occurrence: {operation: {terminal: true}}, rendered_result: null},
    }[payload.action]}}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio"
      initialContext={{database_target_id: 'firebird-target'}} />);
    const input = await screen.findByLabelText('Query parameters (JSON array)');
    expect(input).toHaveValue('[]');
    expect(screen.getByText('Ordered ? placeholders')).toBeInTheDocument();
    fireEvent.change(input, {target: {value: '[42,"text",null]'}});
    fireEvent.click(screen.getByRole('button', {name: 'Run', exact: true}));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/workspace/1', {
      action: 'execute', session_id: 'firebird-session',
      source: 'SELECT CAST(? AS INTEGER) FROM RDB$DATABASE',
      parameters: [42, 'text', null], database_target_id: 'firebird-target',
      max_rows: 1000,
    }));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(3));
  });

  it.each(['', '-1', '1.5', '1000001'])('rejects invalid Firebird row bound %s before opening a session', async (value) => {
    api.get.mockResolvedValue({data: {data: {...bootstrap, languages: [{
      language_profile: 'firebird-sql', title: 'Firebird SQL',
      starter_source: 'SELECT 1 FROM RDB$DATABASE', parameter_shape: 'array',
    }]}}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.change(await screen.findByLabelText('Maximum fetched rows'), {target: {value}});
    fireEvent.click(screen.getByRole('button', {name: 'Run', exact: true}));
    expect(await screen.findByText('Maximum fetched rows must be an integer from 0 to 1000000.'))
      .toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });

  it.each([['0', null], ['7', 7], ['1000000', 1000000]])('transports Firebird fetch bound %s without modifying source', async (value, expected) => {
    api.get.mockResolvedValue({data: {data: {...bootstrap, languages: [{
      language_profile: 'firebird-sql', title: 'Firebird SQL',
      starter_source: 'SELECT 1 FROM RDB$DATABASE', parameter_shape: 'array',
    }]}}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {data: {
      open_session: {session_id: 'fb'}, execute: {occurrence_id: 'query'},
      poll: {occurrence: {operation: {terminal: true}, result: {complete: true,
        extensions: {firebird: {payload: {fetch_observation: {
          limit_reached: true, rows_returned: 7, total_rows: null,
        }}}}}}},
    }[payload.action]}}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.change(await screen.findByLabelText('Maximum fetched rows'), {target: {value}});
    fireEvent.click(screen.getByRole('button', {name: 'Run', exact: true}));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/workspace/1', expect.objectContaining({
      action: 'execute', max_rows: expected, source: 'SELECT 1 FROM RDB$DATABASE',
    })));
    expect(await screen.findByLabelText('Firebird fetch observation'))
      .toHaveTextContent('Further rows may exist; the total was not counted.');
    expect(api.post.mock.calls.some(([, payload]) => payload.action === 'transaction_control')).toBe(false);
  });

  it.each(['{"one":42}', 'null', '42'])('rejects %s before opening a positional query session', async (value) => {
    api.get.mockResolvedValue({data: {data: {...bootstrap, languages: [{
      language_profile: 'firebird-sql', title: 'Firebird SQL',
      starter_source: 'SELECT ? FROM RDB$DATABASE', parameter_shape: 'array',
    }]}}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.change(await screen.findByLabelText('Query parameters (JSON array)'),
      {target: {value}});
    fireEvent.click(screen.getByRole('button', {name: 'Run', exact: true}));
    expect(await screen.findByText('This provider requires an ordered JSON parameter array.'))
      .toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });

  it('polls a running Firebird query and blocks competing transaction actions', async () => {
    api.get.mockResolvedValue({data: {data: {...bootstrap, languages: [{
      language_profile: 'firebird-sql', title: 'Firebird SQL',
      starter_source: 'SELECT 1 FROM RDB$DATABASE', parameter_shape: 'array',
      transaction_actions: ['commit', 'rollback'],
    }]}}});
    let polls = 0;
    api.post.mockImplementation((_url, payload) => {
      if(payload.action === 'open_session') return Promise.resolve({data: {data: {session_id: 'fb-session'}}});
      if(payload.action === 'execute') return Promise.resolve({data: {data: {occurrence_id: 'fb-query'}}});
      if(payload.action === 'close_session') return Promise.resolve({data: {data: {provider_closed: true}}});
      if(payload.action === 'transaction') return Promise.resolve({data: {data: {previous_observation_marker: true}}});
      if(payload.action === 'poll') {
        polls += 1;
        const complete = polls > 1;
        return Promise.resolve({data: {data: {occurrence: {
          operation: {terminal: complete}, result: {complete, extensions: {
            firebird: {payload: complete ? {error: {
              native_status_codes: [335544665],
            }} : {execution_state: 'running'}},
          }},
        }, rendered_result: null}}});
      }
      throw new Error('Unexpected action ' + payload.action);
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByRole('button', {name: 'Provider transaction state'}));
    expect(await screen.findByLabelText('Provider query transaction state')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', {name: 'Run', exact: true})).not.toBeDisabled());
    fireEvent.click(await screen.findByRole('button', {name: 'Run', exact: true}));
    expect(await screen.findByText('Waiting for provider query completion. A cancellation request does not confirm commit or rollback.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Provider query transaction state')).not.toBeInTheDocument();
    for(const name of ['Run', 'commit', 'rollback', 'Provider transaction state', 'Close query session']) {
      expect(screen.getByRole('button', {name, exact: true})).toBeDisabled();
    }
    expect(await screen.findByText('Firebird query did not complete. Native status codes: 335544665')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Run', exact: true})).not.toBeDisabled();
    expect(api.post.mock.calls.filter(([, payload]) => payload.action === 'execute')).toHaveLength(1);
    expect(polls).toBe(2);
  });

  it('sends cancellation without implicit commit or rollback and observes its result', async () => {
    api.get.mockResolvedValue({data: {data: {...bootstrap, languages: [{
      language_profile: 'firebird-sql', title: 'Firebird SQL',
      starter_source: 'SELECT 1 FROM RDB$DATABASE', parameter_shape: 'array',
      transaction_actions: ['commit', 'rollback'],
    }]}}});
    let cancelled = false;
    api.post.mockImplementation((_url, payload) => {
      const responses = {
        open_session: {session_id: 'fb-session'},
        execute: {occurrence_id: 'fb-query'},
        close_session: {provider_closed: true},
      };
      if(payload.action === 'cancel') {
        cancelled = true;
        return Promise.resolve({data: {data: {cancel_request_accepted: true}}});
      }
      if(payload.action === 'poll') return Promise.resolve({data: {data: {
        occurrence: {operation: {terminal: cancelled}, result: {
          complete: cancelled, extensions: {firebird: {payload: cancelled ? {
            execution_state: 'cancelled', error: {native_status_codes: [335544794]},
          } : {execution_state: 'running'}}},
        }}, rendered_result: null,
      }}});
      if(responses[payload.action]) return Promise.resolve({data: {data: responses[payload.action]}});
      throw new Error('Unexpected transaction or replay action');
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByRole('button', {name: 'Run', exact: true}));
    const cancelButton = await screen.findByRole('button', {name: 'Cancel request'});
    await waitFor(() => expect(cancelButton).not.toBeDisabled());
    fireEvent.click(cancelButton);
    expect(await screen.findByText('Firebird query did not complete. Native status codes: 335544794')).toBeInTheDocument();
    expect(api.post.mock.calls.filter(([, payload]) => payload.action === 'execute')).toHaveLength(1);
    expect(api.post.mock.calls.some(([, payload]) => payload.action === 'transaction_control')).toBe(false);
  });

  it.each([
    [{}, 'This Firebird query session cannot be reused. Close it and explicitly reconnect.'],
    [{session_reuse_blocked_reason: 'result_cleanup_failed'}, 'Firebird result cleanup failed. Do not replay the statement. Close this query session and explicitly reconnect.'],
    [{session_reuse_blocked_reason: 'cancellation_state_unknown'}, 'Firebird cancellation state is unknown. Close this query session and explicitly reconnect.'],
    [{cancel_cleanup_error_type: 'RuntimeError'}, 'Firebird cancellation state is unknown. Close this query session and explicitly reconnect.'],
  ])('blocks a poisoned Firebird session until successful release: %j', async (reason, message) => {
    api.get.mockResolvedValue({data: {data: {...bootstrap, languages: [{
      language_profile: 'firebird-sql', title: 'Firebird SQL',
      starter_source: 'SELECT 1 FROM RDB$DATABASE', parameter_shape: 'array',
      transaction_actions: ['commit', 'rollback'],
    }]}}});
    let releaseFails = true;
    api.post.mockImplementation((_url, payload) => {
      if(payload.action === 'close_session' && releaseFails) return Promise.reject(new Error('Release unavailable'));
      return Promise.resolve({data: {data: {
        open_session: {session_id: 'fb-session'},
        execute: {occurrence_id: 'fb-query'},
        poll: {occurrence: {operation: {terminal: true}, result: {
          complete: true, extensions: {firebird: {payload: {session_reuse_blocked: true, ...reason}}},
        }}, rendered_result: null},
        close_session: {provider_closed: true},
      }[payload.action]}});
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByRole('button', {name: 'Run', exact: true}));
    expect(await screen.findByText(message)).toBeInTheDocument();
    for(const name of ['Run', 'commit', 'rollback', 'Provider transaction state']) {
      expect(screen.getByRole('button', {name, exact: true})).toBeDisabled();
    }
    fireEvent.click(screen.getByRole('button', {name: 'Close query session'}));
    expect(await screen.findByText('Release unavailable')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Run', exact: true})).toBeDisabled();
    releaseFails = false;
    fireEvent.click(screen.getByRole('button', {name: 'Close query session'}));
    await waitFor(() => expect(screen.getByRole('button', {name: 'Run', exact: true})).not.toBeDisabled());
    expect(screen.getByRole('button', {name: 'Close query session'})).toBeDisabled();
    expect(api.post.mock.calls.filter(([, payload]) => payload.action === 'execute')).toHaveLength(1);
  });

  it('keeps the old language and owned session if language-switch release fails', async () => {
    api.get.mockResolvedValue({data: {data: {...bootstrap, languages: [{
      language_profile: 'firebird-sql', title: 'Firebird SQL',
      starter_source: 'SELECT 1 FROM RDB$DATABASE', parameter_shape: 'array',
    }, {language_profile: 'another-language', title: 'Another language',
      starter_source: 'native command'}]}}});
    let releaseFails = true;
    api.post.mockImplementation((_url, payload) => {
      if(payload.action === 'close_session' && releaseFails) return Promise.reject(new Error('Release unavailable'));
      return Promise.resolve({data: {data: {
        open_session: {session_id: 'fb-session'},
        execute: {occurrence_id: 'fb-query'},
        poll: {occurrence: {operation: {terminal: true}}, rendered_result: null},
        close_session: {provider_closed: true},
      }[payload.action]}});
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByRole('button', {name: 'Run', exact: true}));
    await waitFor(() => expect(screen.getByRole('button', {name: 'Close query session'})).not.toBeDisabled());
    fireEvent.mouseDown(screen.getByLabelText('Provider language'));
    fireEvent.click(await screen.findByRole('option', {name: 'Another language'}));
    expect(await screen.findByText('Release unavailable')).toBeInTheDocument();
    expect(screen.getByLabelText('Query source')).toHaveValue('SELECT 1 FROM RDB$DATABASE');
    expect(screen.getByRole('button', {name: 'Close query session'})).not.toBeDisabled();
    releaseFails = false;
    fireEvent.mouseDown(screen.getByLabelText('Provider language'));
    fireEvent.click(await screen.findByRole('option', {name: 'Another language'}));
    await waitFor(() => expect(screen.getByLabelText('Query source')).toHaveValue('native command'));
    expect(screen.getByRole('button', {name: 'Close query session'})).toBeDisabled();
  });

  it('pauses Firebird automatic polling after transport failure without replaying execution', async () => {
    api.get.mockResolvedValue({data: {data: {...bootstrap, languages: [{
      language_profile: 'firebird-sql', title: 'Firebird SQL',
      starter_source: 'SELECT 1 FROM RDB$DATABASE', parameter_shape: 'array',
    }]}}});
    let polls = 0;
    api.post.mockImplementation((_url, payload) => {
      if(payload.action === 'poll' && ++polls === 1) return Promise.reject(new Error('offline'));
      return Promise.resolve({data: {data: {
        open_session: {session_id: 'fb-session'},
        execute: {occurrence_id: 'fb-query'},
        poll: {occurrence: {operation: {terminal: true}}, rendered_result: null},
        close_session: {provider_closed: true},
      }[payload.action]}});
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByRole('button', {name: 'Run', exact: true}));
    expect(await screen.findByText('offline')).toBeInTheDocument();
    await act(async () => new Promise((resolve) => setTimeout(resolve, 650)));
    expect(polls).toBe(1);
    expect(screen.getByRole('button', {name: 'Run', exact: true})).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: 'Poll', exact: true}));
    await waitFor(() => expect(screen.getByRole('button', {name: 'Run', exact: true})).not.toBeDisabled());
    expect(polls).toBe(2);
    expect(api.post.mock.calls.filter(([, payload]) => payload.action === 'execute')).toHaveLength(1);
  });

  it('shows only provider-evidenced property sections', () => {
    const resource = {extensions: {mongodb: {native: {
      definition: {validator: {$jsonSchema: {bsonType: 'object'}}},
      indexes: [],
    }}}};
    expect(inspectorSections(resource, {editor: {
      sections: ['properties', 'definition', 'dependencies', 'security',
        'data', 'operations'],
      data_presentation: null,
    }})).toEqual([
      'properties', 'definition', 'indexes', 'operations',
    ]);
  });

  it('preserves exact provider-declared empty property sections', () => {
    const resource = {extensions: {firebird: {native: {
      property_sections: [
        'properties', 'dependencies', 'dependents', 'operations',
      ],
      dependencies: [], dependents: [],
    }}}};
    expect(inspectorSections(resource, {})).toEqual([
      'properties', 'dependencies', 'dependents', 'operations',
    ]);
  });

  it('renders and submits an exact provider-owned endpoint form', async () => {
    const post = jest.fn().mockResolvedValue({display_name: 'SQLite local'});
    render(<ServerProfileWorkspace registration={{
      display_name: 'localhost',
      primary_route: {route_id: 'route-one', configuration: {timeout: 5}},
      forms: {forms: {edit: {
        form_id: 'cdeadmin.sqlite-native.server.edit.v1',
        operation_id: 'edit', title: 'Edit SQLite 3.53 server', fields: [
          {field_id: 'name', label: 'Connection profile name',
            control: 'text', required: true},
          {field_id: 'timeout', route_key: 'timeout',
            label: 'Busy timeout (seconds)', control: 'number',
            required: false, default: 5},
        ],
      }}}}} post={post} setError={jest.fn()} />);
    expect(screen.getByText('Edit SQLite 3.53 server')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', {
      name: 'Connection profile name',
    }), {target: {value: 'SQLite local'}});
    fireEvent.click(screen.getByRole('button', {
      name: 'Save endpoint profile',
    }));
    await waitFor(() => expect(post).toHaveBeenCalledWith({
      action: 'endpoint_profile_update', request: {
        name: 'SQLite local', timeout: 5,
      },
    }));
  });

  it('edits an explicit endpoint and reveals only the typed password', async () => {
    const post = jest.fn().mockResolvedValue({display_name: 'Firebird lab'});
    render(<ServerProfileWorkspace registration={{
      display_name: 'Firebird lab', is_password_saved: true,
      primary_route: {route_id: 'route-one', configuration: {
        host: '127.0.0.10', port: 53050, user: 'SYSDBA',
      }},
      forms: {forms: {edit: {
        form_id: 'cdeadmin.firebird-native.server.edit.v1',
        operation_id: 'edit', title: 'Edit Firebird 5.0 server', fields: [
          {field_id: 'name', label: 'Connection profile name',
            control: 'text', required: true},
          {field_id: 'host', label: 'Server host or address',
            control: 'text', required: true},
          {field_id: 'port', label: 'Server port', control: 'number',
            required: true},
          {field_id: 'username', label: 'User or principal',
            control: 'text', required: false},
          {field_id: 'password', label: 'Password', control: 'password',
            required: false},
          {field_id: 'save_password',
            label: 'Save default connection credentials',
            control: 'boolean', required: false, default: false},
        ],
      }}}}} post={post} setError={jest.fn()} />);

    expect(screen.getByRole('spinbutton', {name: 'Server port'}))
      .toHaveValue(53050);
    expect(screen.getByRole('textbox', {name: 'Server host or address'}))
      .toHaveValue('127.0.0.10');
    expect(screen.getByRole('textbox', {name: 'User or principal'}))
      .toHaveValue('SYSDBA');
    expect(screen.getByRole('checkbox', {
      name: 'Save default connection credentials',
    })).toBeChecked();
    const password = screen.getByLabelText('Password');
    expect(password).toHaveAttribute('type', 'password');
    fireEvent.change(password, {target: {value: 'typed-only-secret'}});
    fireEvent.click(screen.getByRole('button', {name: 'Show password'}));
    expect(password).toHaveAttribute('type', 'text');
    fireEvent.click(screen.getByRole('button', {name: 'Hide password'}));
    expect(password).toHaveAttribute('type', 'password');
    fireEvent.click(screen.getByRole('button', {
      name: 'Save endpoint profile',
    }));
    await waitFor(() => expect(post).toHaveBeenCalledWith({
      action: 'endpoint_profile_update', request: {
        name: 'Firebird lab', host: '127.0.0.10', port: 53050,
        username: 'SYSDBA', password: 'typed-only-secret',
        save_password: true,
      },
    }));
  });

  it('requires the exact profile name before removing an endpoint', async () => {
    const post = jest.fn().mockResolvedValue({removed: true});
    const onRemoved = jest.fn();
    render(<ServerProfileWorkspace registration={{
      display_name: 'SQLite local',
      primary_route: {route_id: 'route-one', configuration: {}},
      forms: {forms: {remove: {
        form_id: 'cdeadmin.sqlite-native.server.remove.v1',
        operation_id: 'remove', title: 'Remove SQLite 3.53 server', fields: [
          {field_id: 'confirmation',
            label: 'Type the connection profile name to confirm',
            control: 'text', required: true},
        ],
      }}}}} post={post} setError={jest.fn()} initialMode="remove"
    onRemoved={onRemoved} />);
    const remove = screen.getByRole('button', {
      name: 'Remove endpoint registration',
    });
    expect(remove).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', {
      name: 'Type the connection profile name to confirm',
    }), {target: {value: 'SQLite local'}});
    expect(remove).toBeEnabled();
    fireEvent.click(remove);
    await waitFor(() => expect(post).toHaveBeenCalledWith({
      action: 'endpoint_profile_remove', request: {
        confirmation: 'SQLite local',
      },
    }));
    expect(onRemoved).toHaveBeenCalledWith({removed: true});
  });

  it('warns that incomplete catalogs are not empty databases', () => {
    render(<ObjectInspectorSection resource={{display_name: 'Example',
      resource_kind: 'database', extensions: {mysql: {native: {
        catalog_coverage: {state: 'partial', failed_query_count: 1,
          failures: [{category: 'permission_denied', error_code: '1142'}]},
      }}}}} />);
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Catalog visibility is incomplete');
    expect(screen.getByRole('alert')).toHaveTextContent('permission_denied');
    expect(screen.getByRole('alert')).toHaveTextContent('1142');
  });

  it('surfaces provider catalog warnings without interpreting markup', () => {
    render(<ObjectInspectorSection resource={{display_name: 'T',
      resource_kind: 'table', extensions: {firebird: {native: {
        catalog_warnings: [null, {}, '',
          'Unresolved grant: <script>do not execute</script>'],
      }}}}} />);
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Unresolved grant: <script>do not execute</script>');
    expect(screen.getByRole('alert').querySelector('script')).toBeNull();
  });

  it('removes inapplicable multi-selection privileges after a target change', () => {
    const fields = [{field_id: 'privileges', control: 'multiselect', options: [
      {value: 'SELECT', visible_when: {field_id: 'kind', equals: 'TABLE'}},
      {value: 'EXECUTE', visible_when: {field_id: 'kind', equals: 'PROCEDURE'}},
    ]}];
    expect(changedFieldDraft(fields, {kind: 'TABLE', privileges: ['SELECT']},
      'kind', 'PROCEDURE').privileges).toEqual([]);
    expect(changedFieldDraft(fields, {kind: 'PROCEDURE', privileges: ['EXECUTE']},
      'kind', 'PROCEDURE').privileges).toEqual(['EXECUTE']);
    expect(changedFieldDraft(fields, {kind: 'TABLE', privileges: 'SELECT'},
      'kind', 'PROCEDURE').privileges).toEqual([]);
  });

  it('opens the selected object browser and its data view without mutations', async () => {
    const table = {resource_id: 'table:assets', resource_kind: 'table',
      display_name: 'ASSETS', authority_path: ['table', 'ASSETS']};
    api.get.mockResolvedValue({data: {data: {...bootstrap,
      resource_page: {items: [table]},
      visual_admin: {objects: [{resource_kind: 'table', title: 'Table',
        editor: {sections: ['properties', 'data']}, operations: [{
          operation_id: 'inspect', title: 'Inspect table',
          execution_available: true, target_required: true,
          mutation_class: 'read', form: {fields: []},
        }]}]},
    }}});
    api.post.mockResolvedValue({data: {data: table}});
    render(<ProviderWorkspaceContent endpointUrl="/workspace/1"
      initialTab="object" initialContext={{resource_id: table.resource_id,
        resource_kind: 'table', operation_id: 'inspect'}} />);
    fireEvent.click(await screen.findByRole('button', {
      name: 'Browse object data',
    }));
    expect(await screen.findByRole('button', {name: 'Load rows'}))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Back'}));
    expect(await screen.findByRole('button', {name: 'Browse object data'}))
      .toBeInTheDocument();
    expect(api.post.mock.calls.every(([, body]) =>
      body.action === 'resource_inspect')).toBe(true);
  });

  it('keeps a sequence editor on its object and invalidates edited plans', async () => {
    const sequence = {resource_id: 'sequence:one', resource_kind: 'sequence',
      display_name: 'Sequence one', extensions: {firebird: {native: {
        state: {value: 12}, ddl: 'CREATE SEQUENCE ONE;',
        dependencies: [], privileges: [],
      }}}};
    const post = jest.fn(async ({action}) => {
      if (action === 'resource_inspect') return sequence;
      if (action === 'visual_admin_validate') return {valid: true};
      if (action === 'visual_admin_plan') return {
        plan_id: 'plan-one', plan_digest: 'digest', state: 'ready',
        execution_available: true,
      };
      return {};
    });
    render(<VisualAdministration objectEditor selectedResource={sequence}
      initialResourceKind="sequence" initialOperationId="inspect"
      resources={[sequence, {...sequence, resource_id: 'sequence:two'}]}
      post={post} setError={jest.fn()} catalog={{objects: [{
        resource_kind: 'sequence', title: 'Sequence', operations: [
          {operation_id: 'inspect', title: 'Inspect sequence',
            target_required: true, form: {fields: []}},
          {operation_id: 'create', title: 'Create sequence',
            target_required: false, form: {fields: []}},
          {operation_id: 'alter', title: 'Alter sequence',
            target_required: true, form: {fields: [{
              field_id: 'restart', label: 'Restart value', control: 'number',
            }]}},
          {operation_id: 'unsupported', title: 'Unsupported operation',
            native_supported: false, form: {fields: []}},
        ],
      }, {resource_kind: 'table', title: 'Table', operations: [{
        operation_id: 'alter', title: 'Alter table', form: {fields: []},
      }]}]}} />);
    expect(await screen.findByRole('tab', {name: 'Creation statement (DDL)'}))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', {name: 'Creation statement (DDL)'}));
    expect(screen.getByRole('tabpanel')).toHaveTextContent('CREATE SEQUENCE ONE;');
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', {name: 'Create sequence'})).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', {name: 'Alter table'})).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', {name: 'Unsupported operation'})).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', {name: 'Target resource'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', {name: 'Alter sequence'}));
    fireEvent.change(await screen.findByRole('spinbutton', {name: 'Restart value'}),
      {target: {value: '42'}});
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(screen.getByRole('button', {
      name: 'Apply provider plan',
    })).toBeEnabled());
    expect(post).toHaveBeenCalledWith({action: 'visual_admin_plan', request: {
      resource_kind: 'sequence', operation_id: 'alter',
      target_resource: sequence, draft: {restart: 42},
    }});
    fireEvent.change(screen.getByRole('spinbutton', {name: 'Restart value'}),
      {target: {value: '43'}});
    expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeDisabled();
  });

  it('loads only explicit native field mappings without rounding int64 text', () => {
    const resource = {extensions: {firebird: {native: {
      increment: -3, state: {current_value: '9223372036854775807'},
      description: '  preserve spacing  ', restart: 'do not infer this',
    }}}};
    expect(initialObjectDraft([
      {field_id: 'increment', control: 'number', initial_value_path: ['increment']},
      {field_id: 'observed', control: 'text', initial_value_path: ['state', 'current_value']},
      {field_id: 'description', control: 'multiline', initial_value_path: ['description']},
      {field_id: 'restart', control: 'text'},
    ], resource)).toEqual({increment: -3, observed: '9223372036854775807',
      description: '  preserve spacing  ', restart: ''});
  });

  it('omits unchanged loaded values and refreshes state after applying an edit', async () => {
    const object = {resource_id: 'sequence:one', resource_kind: 'sequence',
      display_name: 'Sequence one', extensions: {firebird: {native: {
        increment: 2, description: 'existing comment',
      }}}};
    let refreshed = false;
    const post = jest.fn(async ({action}) => {
      if (action === 'resource_inspect') return refreshed ? {
        ...object, extensions: {firebird: {native: {
          increment: 2, description: 'updated comment',
        }}},
      } : object;
      if (action === 'visual_admin_validate') return {valid: true};
      if (action === 'visual_admin_plan') return {plan_id: 'p', plan_digest: 'd',
        state: 'ready', execution_available: true};
      refreshed = true;
      return {provider_result: {state: 'applied'}};
    });
    render(<VisualAdministration objectEditor selectedResource={object}
      resources={[object]} initialOperationId="alter" initialResourceKind="sequence"
      post={post} setError={jest.fn()} catalog={{objects: [{
        resource_kind: 'sequence', title: 'Sequence', operations: [{
          operation_id: 'alter', title: 'Alter', target_required: true,
          form: {fields: [
            {field_id: 'increment', label: 'Increment', control: 'number',
              initial_value_path: ['increment']},
            {field_id: 'description', label: 'Comment', control: 'multiline',
              initial_value_path: ['description']},
          ]},
        }],
      }]}} />);
    await waitFor(() => expect(screen.getByRole('textbox', {name: 'Comment'}))
      .toHaveValue('existing comment'));
    expect(screen.getByRole('spinbutton', {name: 'Increment'})).toHaveValue(2);
    fireEvent.change(screen.getByRole('textbox', {name: 'Comment'}),
      {target: {value: 'updated comment'}});
    expect(screen.queryByRole('button', {name: 'Refresh object properties'}))
      .not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(screen.getByRole('button', {name: 'Apply provider plan'}))
      .toBeEnabled());
    expect(post).toHaveBeenCalledWith({action: 'visual_admin_plan', request: {
      resource_kind: 'sequence', operation_id: 'alter', target_resource: object,
      draft: {description: 'updated comment'},
    }});
    fireEvent.click(screen.getByRole('button', {name: 'Apply provider plan'}));
    await waitFor(() => expect(post.mock.calls.filter(([body]) =>
      body.action === 'resource_inspect')).toHaveLength(2));
    expect(await screen.findByRole('button', {name: 'Refresh object properties'}))
      .toBeEnabled();
    expect(screen.getByRole('textbox', {name: 'Comment'})).toHaveValue('updated comment');
    expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeDisabled();
  });

  it('shows local registration failure without discarding or replaying native success', async () => {
    const post = jest.fn(async ({action}) => {
      if (action === 'visual_admin_validate') return {valid: true};
      if (action === 'visual_admin_plan') return {plan_id: 'p', plan_digest: 'd',
        state: 'ready', execution_available: true};
      return {provider_result: {accepted: true, native_database_created: true},
        workspace_follow_up: [{action: 'register_created_database',
          state: 'failed', automatic_mutation_retry: false,
          message: 'Registration unavailable. Do not repeat the native operation.'}]};
    });
    render(<VisualAdministration resources={[]} post={post} setError={jest.fn()}
      initialResourceKind="database" initialOperationId="create"
      catalog={{objects: [{resource_kind: 'database', title: 'Database',
        operations: [{operation_id: 'create', title: 'Create database',
          target_required: false, form: {fields: []}}]}]}} />);
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(screen.getByRole('button',
      {name: 'Apply provider plan'})).toBeEnabled());
    fireEvent.click(screen.getByRole('button', {name: 'Apply provider plan'}));
    expect(await screen.findByLabelText('Connection registration follow-up required'))
      .toHaveTextContent('Do not repeat the native operation.');
    expect(screen.getByLabelText('Provider operation result'))
      .toHaveTextContent('"native_database_created": true');
    expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeDisabled();
    expect(post.mock.calls.filter(([body]) =>
      body.action === 'visual_admin_apply')).toHaveLength(1);
  });

  it.each([true, false])('preserves service outcome when released=%s', async (released) => {
    const post = jest.fn(async ({action}) => {
      if (action === 'visual_admin_validate') return {valid: true};
      if (action === 'visual_admin_plan') return {plan_id: 'p', plan_digest: 'd',
        state: 'ready', execution_available: true};
      return {provider_result: {accepted: true, driver_observation: {
        schema: 'cdeadmin.firebird-service-result.v1',
        server_completed: true, output: ['Native service result'],
        service_release: {service_handle_released: released},
      }}};
    });
    render(<VisualAdministration resources={[]} post={post} setError={jest.fn()}
      initialResourceKind="database" initialOperationId="database_statistics"
      catalog={{objects: [{resource_kind: 'database', title: 'Database',
        operations: [{operation_id: 'database_statistics', title: 'Database statistics',
          target_required: false, form: {fields: []}}]}]}} />);
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(screen.getByRole('button',
      {name: 'Apply provider plan'})).toBeEnabled());
    fireEvent.click(screen.getByRole('button', {name: 'Apply provider plan'}));
    expect(await screen.findByLabelText('Firebird native service output'))
      .toHaveTextContent('Native service result');
    if (released) {
      expect(screen.queryByLabelText('Firebird service cleanup required')).not.toBeInTheDocument();
    } else {
      expect(screen.getByLabelText('Firebird service cleanup required'))
        .toHaveTextContent('Do not replay the operation.');
    }
    expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeDisabled();
    expect(post.mock.calls.filter(([body]) =>
      body.action === 'visual_admin_apply')).toHaveLength(1);
  });

  it.each(['ok', 'reload', 'execute'])('does not replay an edit after %s outcome', async (outcome) => {
    const object = {resource_id: 'sequence:one', resource_kind: 'sequence',
      display_name: 'Sequence one'};
    const applied = {provider_result: {accepted: true}};
    const post = jest.fn(async ({action}) => {
      if (action === 'resource_inspect') return object;
      if (action === 'visual_admin_validate') return {valid: true};
      if (action === 'visual_admin_plan') return {plan_id: 'p', plan_digest: 'd',
        state: 'ready', execution_available: true};
      if (action === 'visual_admin_apply') {
        if (outcome === 'execute') throw new Error('Response lost');
        return applied;
      }
      throw new Error('Unexpected request ' + action);
    });
    const reload = jest.fn(async () => {
      if (outcome === 'reload') throw new Error('Catalog temporarily unavailable');
    });
    const setError = jest.fn();
    render(<VisualAdministration objectEditor selectedResource={object}
      resources={[object]} initialOperationId="alter" initialResourceKind="sequence"
      post={post} setError={setError} onMutationApplied={reload}
      catalog={{objects: [{resource_kind: 'sequence', operations: [{
        operation_id: 'alter', title: 'Alter', target_required: true,
        form: {fields: [{field_id: 'increment', label: 'Increment',
          control: 'number'}]},
      }]}]}} />);
    fireEvent.change(await screen.findByRole('spinbutton', {name: 'Increment'}),
      {target: {value: '3'}});
    fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
    await waitFor(() => expect(screen.getByRole('button', {name: 'Apply provider plan'}))
      .toBeEnabled());
    fireEvent.click(screen.getByRole('button', {name: 'Apply provider plan'}));
    if (outcome === 'execute') {
      await waitFor(() => expect(setError).toHaveBeenCalledWith('Response lost'));
      expect(reload).not.toHaveBeenCalled();
    } else {
      await waitFor(() => expect(reload).toHaveBeenCalledWith({
        targetResource: object, operationId: 'alter', result: applied,
      }));
    }
    expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeDisabled();
    expect(post.mock.calls.filter(([payload]) =>
      payload.action === 'visual_admin_apply')).toHaveLength(1);
    expect(post.mock.calls.filter(([payload]) =>
      payload.action === 'resource_inspect')).toHaveLength(1);
    if (outcome === 'reload') {
      expect(await screen.findByText(/Do not repeat the operation/))
        .toHaveTextContent('Catalog temporarily unavailable');
    } else {
      expect(screen.queryByText(/Do not repeat the operation/)).not.toBeInTheDocument();
    }
  });

  it.each(['procedure', 'function', 'package', 'collection', 'hash', 'relationship'])(
    'keeps %s native editing and security operations on the selected object', async (kind) => {
      const object = {resource_id: `${kind}:one`, resource_kind: kind,
        display_name: 'Object one', extensions: {test: {native: {
          definition: 'native definition', dependencies: ['dependency one'],
          dependents: ['dependent one'], privileges: [{principal: 'reader'}],
        }}}};
      const post = jest.fn(async ({action}) => {
        if (action === 'resource_inspect') return object;
        if (action === 'visual_admin_validate') return {valid: true};
        if (action === 'visual_admin_plan') return {
          plan_id: 'native-plan', plan_digest: 'native-digest',
          state: 'ready', execution_available: true,
        };
        return {provider_result: {state: 'applied'}};
      });
      render(<VisualAdministration objectEditor selectedResource={object}
        initialResourceKind={kind} initialOperationId="inspect"
        resources={[object]} post={post} setError={jest.fn()}
        catalog={{objects: [{resource_kind: kind, title: kind, operations: [
          {operation_id: 'inspect', title: 'Inspect', target_required: true,
            form: {fields: []}},
          {operation_id: 'alter', title: 'Edit native definition',
            target_required: true, confirmation_required: true,
            form: {fields: [{field_id: 'body', label: 'Native body',
              control: 'code'}]}},
          {operation_id: 'grant', title: 'Grant permissions',
            target_required: true, form: {fields: [{field_id: 'principal',
              label: 'Principal', control: 'text'}]}},
        ]}]}} />);
      fireEvent.click(await screen.findByRole('tab', {name: 'Depends on'}));
      expect(screen.getByRole('tabpanel')).toHaveTextContent('dependency one');
      fireEvent.click(screen.getByRole('tab', {name: 'Privileges and grants'}));
      expect(screen.getByRole('tabpanel')).toHaveTextContent('reader');
      fireEvent.click(screen.getByRole('tab', {name: 'Edit native definition'}));
      fireEvent.change(await screen.findByRole('textbox', {name: 'Native body'}),
        {target: {value: 'engine-native content'}});
      fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
      const confirm = await screen.findByRole('checkbox', {
        name: 'I confirm this provider-planned operation.',
      });
      expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeDisabled();
      fireEvent.click(confirm);
      fireEvent.click(screen.getByRole('button', {name: 'Apply provider plan'}));
      await waitFor(() => expect(post).toHaveBeenCalledWith({
        action: 'visual_admin_apply', request: {
          plan_id: 'native-plan', plan_digest: 'native-digest', confirmed: true,
        },
      }));
      fireEvent.click(screen.getByRole('tab', {name: 'Grant permissions'}));
      expect(await screen.findByRole('textbox', {name: 'Principal'})).toBeInTheDocument();
      expect(screen.queryByRole('textbox', {name: 'Native body'})).not.toBeInTheDocument();
      expect(screen.getByRole('button', {name: 'Apply provider plan'})).toBeDisabled();
    });

  it('renders database and server observations on one properties task', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      endpoint: {
        ...bootstrap.endpoint,
        provider_id: 'org.cdeadmin.firebird',
        verified_runtime_family: 'firebird',
        verified_runtime_version: '5.0.4',
      },
      database_targets: {
        targets: [{target_id: 'database-one', display_name: 'example.fdb',
          database: '/firebird/data/example.fdb', active: true,
          configuration: {charset: 'UTF8', transaction_isolation: 'SNAPSHOT'}}],
      },
      resource_page: {items: [{
        resource_id: 'server:Firebird', resource_kind: 'server',
        display_name: 'Firebird',
        extensions: {firebird: {native: {architecture: 'Firebird/linux'}}},
      }, {
        resource_id: 'database:example.fdb', resource_kind: 'database',
        display_name: 'example.fdb',
        extensions: {firebird: {native: {page_size: '8192',
          ods_major: '13', ods_minor: '1', sql_dialect: '3',
          default_character_set: 'UTF8', forced_writes: '1'}}},
      }]},
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="properties"
      initialContext={{resource_id: 'database-one'}} />);
    expect(await screen.findByText('example.fdb — Firebird database properties'))
      .toBeInTheDocument();
    expect(screen.getByText('Firebird server observations')).toBeInTheDocument();
    expect(screen.getByText('Firebird/linux')).toBeInTheDocument();
    expect(screen.getByText('Firebird format and dialect')).toBeInTheDocument();
    expect(screen.getByText('Firebird storage and durability')).toBeInTheDocument();
    expect(screen.getByText('Firebird connection defaults')).toBeInTheDocument();
    expect(screen.getByText('Database filename or alias')).toBeInTheDocument();
    expect(screen.getByText('/firebird/data/example.fdb')).toBeInTheDocument();
    expect(screen.getByText('8192')).toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
  });

  it('does not call an engine-provided starter empty when there are no presets', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      languages: [{language_profile: 'firebird-sql', title: 'Firebird SQL',
        starter_source: 'SELECT 42 FROM RDB$DATABASE'}],
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    await waitFor(() => expect(screen.getByLabelText('Query source'))
      .toHaveValue('SELECT 42 FROM RDB$DATABASE'));
    expect(screen.getByRole('region', {name: 'Provider query workspace'}))
      .toHaveStyle({overflow: 'auto', minHeight: '0'});
    expect(screen.queryByText(/editor is intentionally empty/)).not.toBeInTheDocument();
  });

  it('renders MariaDB properties as exact provider-specific groups', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      endpoint: {
        ...bootstrap.endpoint,
        provider_id: 'org.cdeadmin.mariadb',
        profile_id: 'mariadb-native',
        verified_runtime_family: 'mariadb',
        verified_runtime_version: '12.2.2',
      },
      database_targets: {
        targets: [{target_id: 'database-one', display_name: 'cdeadmin_demo',
          database: 'cdeadmin_demo', active: true,
          configuration: {charset: 'utf8mb4',
            collation: 'utf8mb4_general_ci'}}],
      },
      resource_page: {items: [{
        resource_id: 'server:MariaDB', resource_kind: 'server',
        display_name: 'MariaDB',
        extensions: {mariadb: {native: {version: '12.2.2-MariaDB',
          hostname: 'mariadb-qa', server_id: 1,
          default_storage_engine: 'InnoDB', tx_isolation: 'REPEATABLE-READ',
          log_bin: 1, binlog_format: 'MIXED', wsrep_on: 0}}},
      }, {
        resource_id: 'database:cdeadmin_demo', resource_kind: 'database',
        display_name: 'cdeadmin_demo',
        extensions: {mariadb: {native: {
          default_character_set: 'utf8mb4',
          default_collation: 'utf8mb4_general_ci', schema_comment: 'QA'}}},
      }]},
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="properties"
      initialContext={{resource_id: 'database-one'}} />);
    expect(await screen.findByText(
      'cdeadmin_demo — MariaDB database properties')).toBeInTheDocument();
    expect(screen.getByText('MariaDB database defaults')).toBeInTheDocument();
    expect(screen.getByText('MariaDB server identity')).toBeInTheDocument();
    expect(screen.getByText('MariaDB server configuration')).toBeInTheDocument();
    expect(screen.getByText('MariaDB session and transaction state'))
      .toBeInTheDocument();
    expect(screen.getByText('MariaDB replication and binary logging'))
      .toBeInTheDocument();
    expect(screen.getByText('REPEATABLE-READ')).toBeInTheDocument();
    expect(screen.getByText('MIXED')).toBeInTheDocument();
    expect(screen.getAllByText('Yes').length).toBeGreaterThan(0);
    expect(screen.queryByText(/\{"/)).not.toBeInTheDocument();
  });

  it('shows exact dialect and metrics blockers without engine fallbacks', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      engine_contracts: {
        state: 'blocked',
        dialect: {state: 'blocked'}, metrics: {state: 'blocked'},
      },
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    expect(await screen.findByLabelText('Exact engine contract status'))
      .toHaveTextContent('dialect, metrics');
    expect(screen.getByLabelText('Exact engine contract status'))
      .toHaveTextContent('will not substitute another engine family');
  });

  it('renders and submits the exact provider-owned database form', async () => {
    const databaseForm = {
      form_set_id: 'cdeadmin.firebird-native.database.forms.v1',
      lifecycle_resource_kind: 'database',
      forms: {
        define: {
          form_id: 'cdeadmin.firebird-native.database.define.v1',
          operation_id: 'define', title: 'Define Firebird database',
          supported: true, fields: [
            {field_id: 'database', label: 'Firebird database filename or alias',
              control: 'text', required: true},
            {field_id: 'display_name', label: 'Navigator display name',
              control: 'text', required: false},
            {field_id: 'charset', label: 'Connection character set',
              control: 'text', required: false, default: 'UTF8'},
          ],
        },
        create: {form_id: 'firebird-create', operation_id: 'create',
          title: 'Create Firebird database', supported: false,
          disabled_reason: 'Unavailable in this test.', fields: []},
      },
    };
    const post = jest.fn().mockResolvedValue({
      forms: databaseForm, target_management: true,
      targets: [], active_target_id: '',
    });
    render(<DatabaseTargetWorkspace initialCatalog={{
      forms: databaseForm, target_management: true,
      targets: [], active_target_id: '',
    }} visualCatalog={{objects: []}} resources={[]} post={post}
    setError={jest.fn()} initialMode="attach" />);

    fireEvent.change(screen.getByRole('textbox', {
      name: /Firebird database filename or alias/,
    }), {target: {value: '/firebird/data/example.fdb'}});
    fireEvent.change(screen.getByRole('textbox', {
      name: /Navigator display name/,
    }), {
      target: {value: 'Example Firebird'},
    });
    fireEvent.click(screen.getByRole('button', {
      name: 'Define Firebird database',
    }));

    await waitFor(() => expect(post).toHaveBeenCalledWith({
      action: 'database_target_attach', request: {
        database: '/firebird/data/example.fdb',
        display_name: 'Example Firebird', charset: 'UTF8',
      },
    }));
    expect(screen.queryByLabelText(
      'Database filename, path, or native name'
    )).not.toBeInTheDocument();
  });

  it('maps chart selections to provider-compiled semantic filters', () => {
    const definition = {dimensions: [{
      id: 'region', field: {source_id: 'sales', field: 'region'},
      hierarchies: [{levels: [{
        id: 'region_level',
        field: {source_id: 'sales', field: 'customer.region'},
      }]}],
    }]};
    const chart = {encodings: {x: 'region_level'}};
    expect(semanticCrossFilter(definition, chart, {value: 'North'}))
      .toEqual({
        field: {source_id: 'sales', field: 'customer.region'},
        operator: 'eq', value: 'North',
      });
    expect(semanticCrossFilter(definition, chart, {value: null}))
      .toEqual({
        field: {source_id: 'sales', field: 'customer.region'},
        operator: 'is_null',
      });
    expect(semanticCrossFilter(definition, {encodings: {x: 'measure'}},
      {value: 42})).toBeNull();
  });

  it('delivers a retained export through a named server-side profile', async () => {
    const post = jest.fn().mockResolvedValue({
      state: 'delivered', automatic_retry: false,
    });
    render(<ResultControls rendered={{
      descriptor: {result_id: 'result-one', export_formats: ['pdf']},
      page: {},
    }} history={[]} post={post} onRendered={jest.fn()}
    setError={jest.fn()} setBusy={jest.fn()} allowedFormats={['pdf']}
    deliveryProfiles={[{
      profile_id: 'archive', label: 'Report archive', kind: 's3',
      allowed_formats: ['pdf'],
    }]} />);
    fireEvent.change(screen.getByLabelText('Object filename'), {
      target: {value: 'quarterly-report.pdf'},
    });
    fireEvent.click(screen.getByText('Deliver'));
    await waitFor(() => expect(post).toHaveBeenCalledWith({
      action: 'result_delivery', request: {
        request_key: expect.stringMatching(
          /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
        ),
        result_id: 'result-one', format: 'pdf', profile_id: 'archive',
        target: {object_name: 'quarterly-report.pdf'},
      },
    }));
    expect(await screen.findByText(/Delivery state.*delivered/))
      .toBeInTheDocument();
    expect(screen.getByText(/Automatic retry is disabled/))
      .toBeInTheDocument();
  });

  it('requests the next provider-owned result page from its occurrence', async () => {
    const post = jest.fn();
    const onProviderContinuation = jest.fn().mockResolvedValue();
    render(<ResultControls rendered={{
      descriptor: {
        result_id: 'stream-page-one', export_formats: [],
        provider_continuation: 'operation-one',
      },
      page: {next_cursor: null, page_size: 500},
    }} history={[]} post={post} onRendered={jest.fn()}
    setError={jest.fn()} setBusy={jest.fn()}
    onProviderContinuation={onProviderContinuation} />);

    fireEvent.click(screen.getByText('Next result page'));

    await waitFor(() => expect(onProviderContinuation).toHaveBeenCalled());
    expect(post).not.toHaveBeenCalled();
  });

  it('loads provider resources through the workspace endpoint', async () => {
    render(<ProviderWorkspaceContent
      closeModal={jest.fn()}
      endpointUrl="/workspace/1"
    />);
    fireEvent.click(await screen.findByRole('treeitem', {name: /database/i}));
    expect(await screen.findByText('example')).toBeInTheDocument();
    expect(screen.getAllByText('database')).toHaveLength(2);
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith('/workspace/1');
  });

  it.each(['first-page', 'outside-page'])(
    'retains the natively renamed domain on the %s for the next edit', async (location) => {
      const original = {resource_id: 'domain:D', resource_kind: 'domain',
        display_name: 'D', display_path: ['D']};
      const renamed = {...original, resource_id: 'domain:E',
        display_name: 'E', display_path: ['E']};
      api.get.mockResolvedValue({data: {data: {...bootstrap,
        resource_page: {generation: 'old', items: [original]},
        visual_admin: {objects: [{resource_kind: 'domain', title: 'Domain',
          operations: [{operation_id: 'rename', title: 'Rename domain',
            target_required: true, form: {fields: [{field_id: 'new_name',
              label: 'New name', control: 'text', required: true}]}}]}]},
      }}});
      api.post.mockImplementation(async (_url, {action, request}) => {
        let value;
        if (action === 'resource_inspect') {
          value = request.resource_id === renamed.resource_id ? renamed : original;
        } else if (action === 'visual_admin_validate') {
          value = {valid: true};
        } else if (action === 'visual_admin_plan') {
          value = {plan_id: 'plan', plan_digest: 'digest',
            state: 'ready', execution_available: true};
        } else if (action === 'visual_admin_apply') {
          value = {provider_result: {resource_identity_change: {
            resource_kind: 'domain', previous_resource_id: original.resource_id,
            resource_id: renamed.resource_id, native_identity_verified: true,
            committed_by_provider: true,
          }}};
        } else if (action === 'resource_page') {
          value = {generation: 'new', items: location === 'first-page' ?
            [renamed] : [], next_cursor: 'remaining'};
        } else {
          throw new Error('Unexpected action ' + action);
        }
        return {data: {data: value}};
      });
      render(<ProviderWorkspaceContent endpointUrl="/workspace/1"
        initialTab="administration" initialContext={{resource_id: 'domain:D',
          resource_kind: 'domain', operation_id: 'rename'}} />);
      fireEvent.change(await screen.findByRole('textbox', {name: 'New name'}),
        {target: {value: 'E'}});
      fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
      await waitFor(() => expect(screen.getByRole('button',
        {name: 'Apply provider plan'})).toBeEnabled());
      fireEvent.click(screen.getByRole('button', {name: 'Apply provider plan'}));
      await waitFor(() => expect(screen.getByRole('button',
        {name: 'Validate and preview'})).toBeEnabled());
      fireEvent.change(screen.getByRole('textbox', {name: 'New name'}),
        {target: {value: 'D'}});
      fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'}));
      await waitFor(() => expect(api.post).toHaveBeenCalledWith('/workspace/1', {
        action: 'visual_admin_plan', request: {
          resource_kind: 'domain', operation_id: 'rename',
          target_resource: renamed, draft: {new_name: 'D'},
        },
      }));
      expect(screen.queryByText(/No discovered resource of this type/)).toBeNull();
    });

  it('requests credentials and reloads after a workspace 401', async () => {
    const onCredentialRequired = jest.fn();
    api.get
      .mockRejectedValueOnce({response: {status: 401}})
      .mockResolvedValueOnce({data: {data: bootstrap}});
    render(<ProviderWorkspaceContent
      closeModal={jest.fn()}
      endpointUrl="/workspace/1"
      onCredentialRequired={onCredentialRequired}
    />);
    await waitFor(() => expect(onCredentialRequired).toHaveBeenCalledTimes(1));
    expect(onCredentialRequired).toHaveBeenCalledWith(expect.any(Function));
    await act(async () => {
      onCredentialRequired.mock.calls[0][0]();
    });
    fireEvent.click(await screen.findByRole('treeitem', {name: /database/i}));
    expect(await screen.findByText('example')).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it('does not invent a generic SQL starter for a provider dialect', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      languages: [{language_profile: 'unproven-sql', title: 'Unproven SQL'}],
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    expect(await screen.findByText(
      /has not supplied an evidence-bound query template/
    )).toBeInTheDocument();
    expect(screen.getByLabelText('Query source')).toHaveValue('');
    expect(screen.queryByDisplayValue('SELECT 1')).not.toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Query source'), {
      target: {value: 'provider-owned user source'},
    });
    expect(screen.queryByText(/editor is intentionally empty/)).not.toBeInTheDocument();
  });

  it('shows workspace tabs only for explicit drag-drop composition', async () => {
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1"
      initialContext={{composition_mode: 'tabbed'}} />);
    expect(await screen.findByRole('tab', {name: 'Resource Explorer'}))
      .toBeInTheDocument();
  });

  it('uses a focused provider database dialog without workspace tabs', async () => {
    const createFields = [
      {field_id: 'database_path', label: 'Absolute database filename on the Firebird server', control: 'text', required: true},
      {field_id: 'page_size', label: 'Page size', control: 'select', required: false, default: '8192', options: [
        {value: '4096', label: '4096'}, {value: '8192', label: '8192'},
      ]},
      {field_id: 'default_charset', label: 'Default character set', control: 'text', required: false, default: 'UTF8'},
      {field_id: 'sql_dialect', label: 'Database SQL dialect', control: 'select', required: false, default: '3', options: [
        {value: '1', label: '1'}, {value: '3', label: '3'},
      ]},
    ];
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      database_targets: {
        form_set_id: 'unused', target_management: true, targets: [],
        forms: {
          form_set_id: 'cdeadmin.firebird-native.database.forms.v1',
          lifecycle_resource_kind: 'database',
          forms: {create: {
            form_id: 'cdeadmin.firebird-native.database.create.v1',
            operation_id: 'create', title: 'Create Firebird database',
            supported: true, fields: createFields,
          }},
        },
      },
      visual_admin: {
        engine_id: 'firebird', engine_name: 'Firebird', objects: [{
          resource_kind: 'database', title: 'Database', operations: [{
            operation_id: 'create', title: 'Create Firebird database',
            execution_available: true, target_required: false,
            confirmation_required: false,
            form: {form_id: 'firebird_database_create', fields: createFields},
          }],
        }],
      },
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="connections"
      initialContext={{database_mode: 'create'}} />);
    expect(await screen.findByRole('heading', {
      name: 'Create Firebird database',
    })).toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    expect(screen.getByRole('combobox', {name: 'Page size'}))
      .toHaveTextContent('8192');
    expect(screen.queryByText(/Cubes & Semantic Models/))
      .not.toBeInTheDocument();
  });

  it('maps a database target UUID to its sole provider database resource', async () => {
    const operation = {
      operation_id: 'backup_logical', title: 'Logical backup (gbak)',
      mutation_class: 'admin', target_required: true,
      target_resource_kinds: ['database'], confirmation_required: false,
      execution_available: true, graphical_ready: true, blockers: [],
      form: {form_id: 'firebird_backup_logical', fields: [{
        field_id: 'backup_file',
        label: 'Backup filename on the Firebird server',
        control: 'text', required: true,
      }]},
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      endpoint: {
        provider_id: 'org.cdeadmin.firebird',
        verified_runtime_family: 'firebird',
      },
      resource_page: {items: [{
        resource_id: 'database:cdeadmin_demo.fdb',
        resource_kind: 'database', display_name: 'cdeadmin_demo.fdb',
        authority_path: ['database', 'cdeadmin_demo.fdb'],
      }]},
      visual_admin: {
        engine_id: 'firebird', engine_name: 'Firebird', objects: [{
          resource_kind: 'database', title: 'Database',
          operations: [operation],
        }],
      },
    }}});
    api.post.mockResolvedValue({data: {data: {
      resource_id: 'database:cdeadmin_demo.fdb',
      resource_kind: 'database', display_name: 'cdeadmin_demo.fdb',
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="administration"
      initialContext={{
        resource_id: 'retained-database-target-uuid',
        resource_kind: 'database', operation_id: 'backup_logical',
      }} />);
    expect(await screen.findByRole('heading', {
      name: 'Logical backup (gbak)',
    })).toBeInTheDocument();
    expect(screen.getByRole('combobox', {name: 'Target resource'}))
      .toHaveTextContent('cdeadmin_demo.fdb');
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/workspace/1', {action: 'resource_inspect', request: {
        resource_id: 'database:cdeadmin_demo.fdb', generation: undefined,
        database_target_id: 'retained-database-target-uuid',
      }}
    ));
  });

  it('keeps the context-selected provider resource when peers share its kind', async () => {
    const operation = {
      operation_id: 'set_global', title: 'Set runtime global value',
      mutation_class: 'admin', target_required: true,
      confirmation_required: false, execution_available: true,
      graphical_ready: true, blockers: [],
      form: {form_id: 'mariadb_set_global', fields: []},
    };
    const resources = [{
      resource_id: 'system-variable:READ_ONLY',
      resource_kind: 'system-variable', display_name: 'READ_ONLY',
      authority_path: ['Configuration', 'system-variable', 'READ_ONLY'],
    }, {
      resource_id: 'system-variable:MAX_CONNECTIONS',
      resource_kind: 'system-variable', display_name: 'MAX_CONNECTIONS',
      authority_path: [
        'Configuration', 'system-variable', 'MAX_CONNECTIONS',
      ],
    }];
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: resources},
      visual_admin: {
        engine_id: 'mariadb', engine_name: 'MariaDB', objects: [{
          resource_kind: 'system-variable', title: 'System variable',
          operations: [operation],
        }],
      },
    }}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {
      data: resources.find((item) =>
        item.resource_id === payload.request.resource_id),
    }}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="administration"
      initialContext={{
        resource_id: 'system-variable:MAX_CONNECTIONS',
        resource_kind: 'system-variable', operation_id: 'set_global',
      }} />);
    expect(await screen.findByRole('heading', {
      name: 'Set runtime global value',
    })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('combobox', {
      name: 'Target resource',
    })).toHaveTextContent('MAX_CONNECTIONS'));
  });

  it('navigates the provider tree by keyboard and opens the linked editor', async () => {
    api.post.mockResolvedValue({data: {data: {
      ...bootstrap.resource_page.items[0], generation: 'generation-one',
      extensions: {mysql: {native: {character_set: 'utf8mb4'}}},
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" />);
    const branch = await screen.findByRole('treeitem', {name: /database/i});
    fireEvent.focus(branch);
    fireEvent.keyDown(branch, {key: 'ArrowRight'});
    expect(branch).toHaveAttribute('aria-expanded', 'true');
    const resource = await screen.findByRole('treeitem', {name: /example/i});
    fireEvent.keyDown(resource, {key: 'Enter'});
    expect(await screen.findByRole('navigation', {
      name: 'Selected object breadcrumb',
    })).toHaveTextContent('database / example');
    fireEvent.click(screen.getByText('Open object editor'));
    expect(await screen.findByRole('textbox', {name: /Name/}))
      .toBeInTheDocument();
    expect(await screen.findByRole('combobox', {
      name: 'Object properties task',
    })).toHaveTextContent('Summary');
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    expect(screen.getByRole('tabpanel', {name: 'properties object section'}))
      .toHaveTextContent('generation-one');
    expect(api.post).toHaveBeenCalledWith('/workspace/1', {
      action: 'resource_inspect', request: {
        resource_id: 'database:example', generation: undefined,
      },
    });
  });

  it('does not inspect an attachment-free service-operation target', async () => {
    const onCredentialRequired = jest.fn((retry) => retry());
    let applyAttempts = 0;
    const operation = {
      operation_id: 'bring_online', title: 'Bring database online',
      mutation_class: 'admin', target_required: true,
      target_resource_kinds: ['database'], confirmation_required: false,
      execution_available: true, graphical_ready: true, blockers: [],
      workspace_scope: 'server_service',
      form: {form_id: 'firebird_bring_online', fields: []},
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: [{
        resource_id: 'database-service-target:database-one',
        resource_kind: 'database', display_name: 'offline.fdb',
        extensions: {cdeadmin: {
          database_target_id: 'database-one', service_scope_only: true,
        }},
      }]},
      visual_admin: {
        engine_id: 'firebird', engine_name: 'Firebird', objects: [{
          resource_kind: 'database', title: 'Database',
          operations: [operation],
        }],
      },
    }}});
    api.post.mockImplementation((_url, payload) => {
      const responses = {
        visual_admin_validate: {valid: true, errors: []},
        visual_admin_plan: {
          state: 'ready', execution_available: true,
          plan_id: 'service-plan', plan_digest: 'service-digest',
        },
        visual_admin_apply: {
          accepted: true,
          driver_observation: {server_completed: true},
        },
      };
      if (payload.action === 'visual_admin_apply' && applyAttempts++ === 0) {
        return Promise.reject({response: {status: 401}});
      }
      return Promise.resolve({data: {data: responses[payload.action]}});
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="administration"
      onCredentialRequired={onCredentialRequired}
      initialContext={{
        resource_id: 'database-one', resource_kind: 'database',
        operation_id: 'bring_online',
      }} />);
    expect(await screen.findByRole('heading', {
      name: 'Bring database online',
    })).toBeInTheDocument();
    expect(screen.getByRole('combobox', {name: 'Target resource'}))
      .toHaveTextContent('offline.fdb');
    expect(api.post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Validate and preview'));
    await screen.findByLabelText('Provider plan preview');
    fireEvent.click(screen.getByText('Apply provider plan'));
    await screen.findByLabelText('Provider operation result');
    expect(onCredentialRequired).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith('/workspace/1', {
      action: 'visual_admin_apply', request: {
        plan_id: 'service-plan', plan_digest: 'service-digest',
        confirmed: false, database_target_id: 'database-one',
      },
    });
  });

  it('opens provider-owned object actions from the resource context menu', async () => {
    const inspectOperation = {
      operation_id: 'inspect', title: 'Inspect MySQL database',
      mutation_class: 'read', target_required: true,
      target_resource_kinds: ['database'], confirmation_required: false,
      execution_available: true, graphical_ready: true, blockers: [],
      form: {form_id: 'mysql-database-inspect', fields: []},
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      visual_admin: {
        ...bootstrap.visual_admin,
        objects: [{...bootstrap.visual_admin.objects[0],
          editor: {sections: ['definition']},
          operations: [inspectOperation]}],
      },
    }}});
    api.post.mockResolvedValue({data: {data: {
      ...bootstrap.resource_page.items[0], generation: 'generation-one',
      extensions: {mysql: {native: {
        definition: {character_set: 'utf8mb4'},
      }}},
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" />);
    fireEvent.click(await screen.findByRole('treeitem', {name: /database/i}));
    const resource = await screen.findByRole('treeitem', {name: /example/i});
    fireEvent.contextMenu(resource, {clientX: 120, clientY: 80});
    fireEvent.click(await screen.findByText('Inspect object'));
    expect(await screen.findByRole('heading', {
      name: /Inspect MySQL database/,
    })).toBeInTheDocument();
    expect(await screen.findByRole('table', {
      name: 'Native provider properties',
    }))
      .toHaveTextContent('character set');
  });

  it('surfaces unique native tasks but omits unsupported tasks', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      visual_admin: {
        ...bootstrap.visual_admin,
        objects: [{...bootstrap.visual_admin.objects[0], operations: [{
          operation_id: 'flashback', title: 'Flashback database',
          mutation_class: 'admin', target_required: true,
          execution_available: true, graphical_ready: true,
          native_supported: true, blockers: [], form: {fields: []},
        }, {
          operation_id: 'vacuum', title: 'Vacuum database',
          mutation_class: 'admin', target_required: true,
          execution_available: false, graphical_ready: true,
          native_supported: false,
          blockers: ['provider_operation_unavailable'], form: {fields: []},
        }]}],
      },
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="administration" />);
    expect(await screen.findByText('Flashback database')).toBeInTheDocument();
    expect(screen.queryByText('Vacuum database')).not.toBeInTheDocument();
  });

  it('loads generation-bound navigator continuation pages', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {
        ...bootstrap.resource_page, generation: 'generation-one',
        next_cursor: 'cursor-one', total_count: 2,
      },
    }}});
    api.post.mockResolvedValue({data: {data: {
      generation: 'generation-one', next_cursor: null, total_count: 2,
      items: [{
        resource_id: 'schema:example:public', resource_kind: 'schema',
        display_name: 'public', display_path: ['database', 'example', 'public'],
        authority_path: ['database', 'example', 'schema', 'public'],
      }],
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" />);
    fireEvent.click(await screen.findByText('Load more provider objects'));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/workspace/1', {
        action: 'resource_page', request: {
          continuation: 'cursor-one', generation: 'generation-one',
        },
      }
    ));
    fireEvent.change(screen.getByLabelText('Filter provider objects'), {
      target: {value: 'public'},
    });
    expect(await screen.findByText('public')).toBeInTheDocument();
  });

  it('refreshes only the active provider generation', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {
        ...bootstrap.resource_page, generation: 'generation-one',
      },
    }}});
    api.post.mockResolvedValue({data: {data: {
      ...bootstrap.resource_page, generation: 'generation-two',
      items: [{
        ...bootstrap.resource_page.items[0], display_name: 'refreshed',
      }],
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" />);
    fireEvent.click(await screen.findByText('Refresh provider objects'));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/workspace/1', {
        action: 'resource_refresh', request: {
          generation: 'generation-one',
        },
      }
    ));
    fireEvent.click(await screen.findByRole('treeitem', {name: /database/i}));
    fireEvent.click(await screen.findByRole('treeitem', {name: /example/i}));
    expect(await screen.findByText('refreshed')).toBeInTheDocument();
  });

  it('opens a provider session, executes and renders rows', async () => {
    api.post.mockImplementation((_url, payload) => {
      const values = {
        open_session: {session_id: 'session-one'},
        execute: {occurrence_id: 'occurrence-one'},
        poll: {
          occurrence: {operation: {terminal: true}},
          rendered_result: {
            component_reference: 'SchemaView/DataGridView',
            view_model: {
              columns: [{name: 'answer'}],
              rows: [{answer: 42}],
            },
          },
        },
      };
      return Promise.resolve({data: {data: values[payload.action]}});
    });
    render(<ProviderWorkspaceContent
      closeModal={jest.fn()}
      endpointUrl="/workspace/1"
      initialTab="studio"
      initialContext={{resource_id: 'database-one'}}
    />);
    expect(await screen.findByText('MySQL SQL')).toBeInTheDocument();
    expect(screen.getByLabelText('Provider grid activation status'))
      .toHaveTextContent('grid contract checks passed; this is not full engine qualification');
    const status = screen.getByLabelText('Provider grid activation status');
    expect(status.tagName).toBe('DETAILS');
    expect(status).not.toHaveAttribute('open');
    fireEvent.click(screen.getByText('Grid checks passed'));
    expect(status).toHaveAttribute('open');
    fireEvent.click(screen.getByText('Grid checks passed'));
    expect(status).not.toHaveAttribute('open');
    fireEvent.click(screen.getByText('Run'));
    expect(await screen.findByText('42')).toBeInTheDocument();
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(3));
    expect(api.post.mock.calls.map((call) => call[1].action)).toEqual([
      'open_session', 'execute', 'poll',
    ]);
    expect(api.post.mock.calls.map((call) =>
      call[1].database_target_id
    )).toEqual(['database-one', 'database-one', 'database-one']);
  });

  it.each(['runtime', 'structural'])('keeps %s grid failures visibly expanded', async (failure) => {
    api.get.mockResolvedValue({data: {data: {...bootstrap,
      grid_workspace: {...bootstrap.grid_workspace,
        ...(failure === 'runtime' ? {runtime_gate: {state: 'failed'}} : {
          activation_gates: [{gate_id: 'column_contract', state: 'failed'}],
        }),
      },
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    await screen.findByText('MySQL SQL');
    const warning = screen.getByRole('alert', {name: 'Provider grid activation status'});
    expect(warning).toBeVisible();
    expect(warning).toHaveTextContent(failure === 'runtime' ?
      'live provider verification' : 'column_contract');
    expect(screen.queryByText('Grid checks passed')).not.toBeInTheDocument();
  });

  it('uses provider transaction controls without inferring finality', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      languages: [{language_profile: 'mysql-sql', title: 'MySQL SQL',
        transaction_actions: ['commit', 'rollback']}],
    }}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {
      data: payload.action === 'open_session' ? {session_id: 'session-one'} : {
        session_id: 'session-one', transaction_model: 'mysql-native',
        authority_reference: 'provider:org.cdeadmin.mysql',
        provider_payload: {driver_observation_only: true},
      },
    }}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByText('commit'));
    await waitFor(() => expect(api.post).toHaveBeenLastCalledWith(
      '/workspace/1', {
        action: 'transaction_action', session_id: 'session-one',
        transaction_action: 'commit',
        database_target_id: null,
      }
    ));
    expect(await screen.findByText(/driver_observation_only/))
      .toBeInTheDocument();
    fireEvent.click(screen.getByText('Close query session'));
    await waitFor(() => expect(api.post).toHaveBeenLastCalledWith(
      '/workspace/1', {
        action: 'close_session', session_id: 'session-one',
        database_target_id: null,
      }
    ));
    expect(screen.getByText('Close query session')).toBeDisabled();
  });

  it('pages, exports, and compares endpoint-bound retained results', async () => {
    const rendered = {
      descriptor: {result_id: 'result-two', export_formats: ['json']},
      component_reference: 'SchemaView/DataGridView',
      page: {next_cursor: 'cursor-two', page_size: 1},
      view_model: {columns: [{name: 'answer'}], rows: [{answer: 42}]},
    };
    api.post.mockImplementation((_url, payload) => {
      const values = {
        open_session: {session_id: 'session-one'},
        execute: {occurrence_id: 'occurrence-one'},
        poll: {occurrence: {operation: {terminal: true}}, rendered_result: rendered},
        result_page: {...rendered, page: {next_cursor: null, page_size: 1},
          view_model: {columns: [{name: 'answer'}], rows: [{answer: 43}]}},
        result_export: {content_base64: 'W3siYW5zd2VyIjo0Mn1d',
          media_type: 'application/json', filename: 'result.json'},
      };
      return Promise.resolve({data: {data: values[payload.action]}});
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByText('Run'));
    fireEvent.click(await screen.findByText('Next result page'));
    expect(await screen.findByText('43')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Export JSON'));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/workspace/1', {
      action: 'result_export', request: {
        result_id: 'result-two', format: 'json',
      },
    }));
  });

  it('renders document results as a structured JSON tree', async () => {
    const documentBootstrap = {
      ...bootstrap,
      endpoint: {
        provider_id: 'org.cdeadmin.mongodb',
        verified_runtime_family: 'mongodb',
      },
      languages: [{
        language_profile: 'mongodb-query-api-json',
        title: 'MongoDB Query API (JSON)',
        starter_source: '{"operation":"command","database":"admin","command":{"ping":1}}',
      }],
    };
    api.get.mockResolvedValue({data: {data: documentBootstrap}});
    api.post.mockImplementation((_url, payload) => {
      const values = {
        open_session: {session_id: 'mongo-session'},
        execute: {occurrence_id: 'mongo-operation'},
        poll: {
          occurrence: {operation: {terminal: true}},
          rendered_result: {
            component_reference: 'cdeadmin/results/DocumentTreeView',
            view_model: {
              family: 'document',
              records: [{_id: {$oid: '0123456789abcdef01234567'}, value: 42}],
            },
          },
        },
      };
      return Promise.resolve({data: {data: values[payload.action]}});
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByText('Run'));
    const results = await screen.findByLabelText('Document results');
    expect(results).toHaveTextContent('0123456789abcdef01234567');
    expect(results).toHaveTextContent('42');
  });

  it('renders Neo4j graph results with an accessible element table', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      endpoint: {provider_id: 'org.cdeadmin.neo4j', verified_runtime_family: 'neo4j'},
      languages: [{
        language_profile: 'cypher', title: 'Cypher',
        starter_source: 'RETURN 1 AS value',
      }],
      visual_admin: {...bootstrap.visual_admin, model_family: 'graph'},
    }}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {data: {
      open_session: {session_id: 'neo4j-session'},
      execute: {occurrence_id: 'neo4j-operation'},
      poll: {
        occurrence: {operation: {terminal: true}},
        rendered_result: {
          component_reference: 'cdeadmin/results/GraphView',
          view_model: {family: 'graph', records: [{
            n: {kind: 'node', element_id: '4:one', labels: ['Person'],
              properties: {name: 'Alice'}},
          }]},
        },
      },
    }[payload.action]}}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByText('Run'));
    const results = await screen.findByLabelText('Graph results');
    expect(results).toHaveTextContent('Person');
    expect(results).toHaveTextContent('Alice');
    expect(screen.getByLabelText('Neo4j graph visualization')).toBeInTheDocument();
  });

  it('renders Cassandra wide-column results with native CQL types', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      endpoint: {
        provider_id: 'org.cdeadmin.cassandra',
        verified_runtime_family: 'cassandra',
      },
      languages: [{
        language_profile: 'cql-3', title: 'CQL 3',
        starter_source: 'SELECT cluster_name FROM system.local',
      }],
      visual_admin: {...bootstrap.visual_admin, model_family: 'wide-column'},
    }}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {data: {
      open_session: {session_id: 'cassandra-session'},
      execute: {occurrence_id: 'cassandra-operation'},
      poll: {
        occurrence: {operation: {terminal: true}},
        rendered_result: {
          component_reference: 'cdeadmin/results/WideColumnView',
          view_model: {
            family: 'wide_column',
            columns: [{name: 'tenant', type: 'text'},
              {name: 'payload', type: 'blob'}],
            rows: [{tenant: 'north', payload: {$binary: 'Ynl0ZXM='}}],
            native_observation: {warnings: ['Replica observation warning']},
          },
        },
      },
    }[payload.action]}}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByText('Run'));
    const results = await screen.findByLabelText('Wide-column results');
    expect(results).toHaveTextContent('tenant');
    expect(results).toHaveTextContent('blob');
    expect(results).toHaveTextContent('Ynl0ZXM=');
    expect(results).toHaveTextContent('Replica observation warning');
  });

  it('renders ClickHouse columnar results with native types', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      endpoint: {
        provider_id: 'org.cdeadmin.clickhouse',
        verified_runtime_family: 'clickhouse',
      },
      languages: [{
        language_profile: 'clickhouse-sql', title: 'ClickHouse SQL',
        starter_source: 'SELECT version() AS version',
      }],
      visual_admin: {
        ...bootstrap.visual_admin, model_family: 'columnar-analytic',
      },
    }}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {data: {
      open_session: {session_id: 'clickhouse-session'},
      execute: {occurrence_id: 'clickhouse-operation'},
      poll: {
        occurrence: {operation: {terminal: true}},
        rendered_result: {
          component_reference: 'cdeadmin/results/ColumnarView',
          view_model: {
            family: 'columnar',
            columns: [{name: 'category', type: 'LowCardinality(String)'},
              {name: 'total', type: 'Int64'}],
            rows: [{category: 'one', total: 30}],
            statistics: {rows_read: 3},
          },
        },
      },
    }[payload.action]}}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByText('Run'));
    const results = await screen.findByLabelText('Columnar results');
    expect(results).toHaveTextContent('category');
    expect(results).toHaveTextContent('LowCardinality(String)');
    expect(results).toHaveTextContent('one');
    expect(results).toHaveTextContent('30');
  });

  it('renders provider-declared administration forms and blocked plans', async () => {
    api.post.mockImplementation((_url, payload) => {
      if (payload.action === 'visual_admin_validate') {
        return Promise.resolve({data: {data: {valid: true, errors: []}}});
      }
      return Promise.resolve({data: {data: {
        state: 'blocked',
        execution_available: false,
        blockers: ['provider_native_planner_unavailable'],
      }}});
    });
    render(<ProviderWorkspaceContent
      closeModal={jest.fn()}
      endpointUrl="/workspace/1"
      initialTab="administration"
    />);
    const nameField = await screen.findByRole('textbox', {name: /Name/});
    expect(nameField).toBeInTheDocument();
    fireEvent.change(nameField, {target: {value: 'sample'}});
    fireEvent.click(screen.getByText('Validate and preview'));
    expect(await screen.findByLabelText('Provider plan preview')).toHaveTextContent(
      'provider_native_planner_unavailable'
    );
    expect(screen.getByText('Apply provider plan')).toBeDisabled();
  });

  it('opens a context command as one focused provider task form', async () => {
    render(<ProviderWorkspaceContent
      closeModal={jest.fn()}
      endpointUrl="/workspace/1"
      initialTab="administration"
      initialContext={{resource_kind: 'database', operation_id: 'create'}}
    />);
    expect(await screen.findByRole('heading', {name: 'Create'}))
      .toBeInTheDocument();
    expect(screen.queryByLabelText('Object type')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Operation')).not.toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    const section = screen.getByRole('region', {name: 'Engine task form'});
    expect(section).toHaveStyle({minWidth: '0'});
    expect(section.parentElement).toHaveStyle({gridTemplateColumns: 'minmax(0, 1fr)'});
  });

  it('shows provider-owned administration observations', async () => {
    const readyBootstrap = {
      ...bootstrap,
      visual_admin: {
        ...bootstrap.visual_admin,
        objects: [{
          ...bootstrap.visual_admin.objects[0],
          operations: [{
            ...bootstrap.visual_admin.objects[0].operations[0], blockers: [],
          }],
        }],
      },
    };
    api.get.mockResolvedValue({data: {data: readyBootstrap}});
    api.post.mockImplementation((_url, payload) => {
      const responses = {
        visual_admin_validate: {valid: true, errors: []},
        visual_admin_plan: {
          state: 'ready', execution_available: true,
          plan_id: 'plan-one', plan_digest: 'digest-one',
        },
        visual_admin_apply: {
          provider_result: {
            acknowledged: true, local_process_observation_only: true,
          },
        },
      };
      return Promise.resolve({data: {data: responses[payload.action]}});
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="administration" />);
    fireEvent.change(await screen.findByRole('textbox', {name: /Name/}), {
      target: {value: 'sample'},
    });
    fireEvent.click(screen.getByText('Validate and preview'));
    await screen.findByLabelText('Provider plan preview');
    fireEvent.click(screen.getByText('Apply provider plan'));
    const result = await screen.findByLabelText('Provider operation result');
    expect(result).toHaveTextContent('local_process_observation_only');
    expect(result).toHaveTextContent('true');
  });

  it.each([false, true])('shows native completion and registration failure=%s separately', async (registrationFailed) => {
    const createForm = {
      form_id: 'cdeadmin.mysql-native.database.create.v1',
      operation_id: 'create', title: 'Create database', supported: true,
      fields: [{
        field_id: 'name', label: 'Name', control: 'text', required: true,
      }, {
        field_id: 'definition', label: 'Provider-native definition',
        control: 'code', required: false,
      }, {
        field_id: 'options', label: 'Execution options', control: 'json',
        required: false, default: {},
      }],
    };
    const databaseTargets = {
      multiple: true, target_management: true, server_verification: true,
      active_target_id: null, legacy_route_database: null, targets: [],
      forms: {
        form_set_id: 'cdeadmin.mysql-native.database.forms.v1',
        lifecycle_resource_kind: 'database',
        forms: {create: createForm},
      },
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      endpoint: {
        ...bootstrap.endpoint, route_management_available: true,
      },
      database_targets: databaseTargets,
      visual_admin: {
        ...bootstrap.visual_admin,
        objects: [{
          ...bootstrap.visual_admin.objects[0],
          operations: [{
            ...bootstrap.visual_admin.objects[0].operations[0],
            blockers: [], execution_available: true,
          }],
        }],
      },
    }}});
    api.post.mockImplementation((_url, payload) => {
      const responses = {
        route_list: {
          supports_multiple_routes: false, default_port: 3050,
          database_targeting: {multiple: true}, connection_fields: [],
          routes: [{
            route_id: 'route-one', priority: 1,
            configuration: {host: 'localhost', port: 3050, user: 'SYSDBA'},
            health: {},
          }],
        },
        visual_admin_validate: {valid: true, errors: []},
        visual_admin_plan: {
          state: 'ready', execution_available: true,
          plan_id: 'database-plan', plan_digest: 'database-digest',
        },
        visual_admin_apply: {
          provider_result: {driver_returned: true},
          workspace_follow_up: registrationFailed ? [{
            action: 'register_created_database', state: 'failed',
            message: 'Local registration failed. Do not repeat native creation.',
          }] : [],
          database_targets: registrationFailed ? undefined : {
            ...databaseTargets, active_target_id: 'database-one',
            targets: [{
              target_id: 'database-one', active: true,
              display_name: 'sample.fdb', database: '/data/sample.fdb',
            }],
          },
        },
        resource_refresh: bootstrap.resource_page,
      };
      return Promise.resolve({data: {data: responses[payload.action]}});
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="connections" />);
    fireEvent.change(await screen.findByRole('textbox', {name: /Name/}), {
      target: {value: 'sample.fdb'},
    });
    fireEvent.click(screen.getByText('Validate and preview'));
    await screen.findByText(/database-plan/);
    fireEvent.click(screen.getByRole('button', {name: 'Create database'}));
    expect(await screen.findByText(/completed the native database operation/))
      .toBeInTheDocument();
    if(registrationFailed) {
      expect(screen.getByText('Local registration failed. Do not repeat native creation.'))
        .toBeInTheDocument();
    }
    expect(screen.getByRole('button', {name: 'Create database'})).toBeDisabled();
    expect(api.post).toHaveBeenCalledWith('/workspace/1', {
      action: 'visual_admin_apply', request: {
        plan_id: 'database-plan', plan_digest: 'database-digest',
        confirmed: false,
      },
    });
  });

  it('renders and submits typed provider multiselect fields', async () => {
    const readyBootstrap = {
      ...bootstrap,
      visual_admin: {
        ...bootstrap.visual_admin,
        objects: [{
          resource_kind: 'permission', title: 'Permission', operations: [{
            operation_id: 'grant_sql', title: 'Grant SQL privileges',
            mutation_class: 'security', target_required: false,
            confirmation_required: false, blockers: [],
            form: {fields: [
              {field_id: 'username', label: 'User', control: 'text',
                required: true},
              {field_id: 'privileges', label: 'Privileges',
                control: 'multiselect', required: true, options: [
                  {value: 'SELECT', label: 'SELECT'},
                  {value: 'INSERT', label: 'INSERT'},
                ]},
            ]},
          }],
        }],
      },
    };
    api.get.mockResolvedValue({data: {data: readyBootstrap}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {
      data: payload.action === 'visual_admin_validate' ?
        {valid: true, errors: []} : {
          state: 'ready', execution_available: true,
          plan_id: 'permission-plan', plan_digest: 'permission-digest',
        },
    }}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="administration" />);
    fireEvent.change(await screen.findByRole('textbox', {name: /User/}), {
      target: {value: 'operator'},
    });
    const privileges = screen.getByRole('combobox', {name: /Privileges/});
    const selectInput = privileges.parentElement.querySelector('input');
    fireEvent.change(selectInput, {target: {value: 'SELECT'}});
    fireEvent.click(screen.getByText('Validate and preview'));
    await screen.findByLabelText('Provider plan preview');
    expect(api.post.mock.calls[0][1].request.draft).toEqual({
      username: 'operator', privileges: ['SELECT'],
    });
  });

  it('shows durable operations as review-only after provider restart', async () => {
    api.post.mockResolvedValue({data: {data: {
      restart_safe_audit: true,
      items: [{
        operation_id: 'operation-one', operation_kind: 'backup',
        resource_kind: 'database', stage: 'completed', durable_audit: true,
        live_provider_handle_available: false, cancellable: true,
        provider_finality_authority: true,
        automatic_mutation_retry: false,
      }],
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="operations" />);
    fireEvent.click(await screen.findByText(
      'Operation progress and history'
    ));
    expect(await screen.findByText(/restart-safe audit record/)).toBeInTheDocument();
    expect(screen.getByText('Observe provider state')).toBeDisabled();
    expect(screen.getByText('Request cancellation')).toBeDisabled();
    expect(screen.getByText('Validate post-state')).toBeDisabled();
  });

  it('renders provider-declared operational facets and commands', async () => {
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="operations" />);
    expect(await screen.findAllByText('Server and cluster health'))
      .toHaveLength(2);
    expect(screen.getByText('Provider observations')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Validate and preview'}))
      .toBeInTheDocument();
    expect(screen.getByText(/does not infer success/)).toBeInTheDocument();
  });

  it('visualizes provider-authoritative distributed topology paths', async () => {
    const topologyBootstrap = {
      ...bootstrap,
      resource_page: {generation: 'topology-one', items: [{
        resource_id: 'node:one', resource_kind: 'node',
        display_name: 'node-one',
        authority_path: ['cluster-a', 'zone-one', 'node-one'],
        extensions: {provider: {native: {
          status: 'provider-online', role: 'provider-leader',
        }}},
      }]},
      operational_workspace: {
        schema: 'cdeadmin.operational-workspace.v1',
        engine_id: 'distributed-test', distributed: true,
        categories: ['distributed'],
        topology: {available: true, authority:
          'provider-resource-authority-path', resource_kinds: ['node']},
        facets: [{
          facet_id: 'topology', title: 'Topology visualization',
          category: 'distributed', summary: 'Provider topology.',
          catalog_state: 'observable', unavailable_reason: null,
          resource_kinds: ['node'], discovered_resource_count: 1,
          operations: [],
        }],
      },
    };
    api.get.mockResolvedValue({data: {data: topologyBootstrap}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="operations" />);
    expect(await screen.findByLabelText('Provider topology visualization'))
      .toHaveTextContent('cluster-a → zone-one → node-one');
    expect(screen.getByText(/provider-leader/)).toBeInTheDocument();
  });

  it('edits rows through provider-issued identity plans', async () => {
    const gridBootstrap = {
      ...bootstrap,
      resource_page: {items: [{
        resource_id: 'table:example:widgets', resource_kind: 'table',
        display_name: 'widgets', display_path: ['example', 'widgets'],
        authority_path: ['example', 'table', 'widgets'],
        extensions: {cdeadmin: {database_target_id: 'database-one'}},
      }]},
      visual_admin: {
        ...bootstrap.visual_admin,
        objects: [{
          resource_kind: 'table', title: 'Table', operations: [
            {operation_id: 'insert', execution_available: true},
            {operation_id: 'update', execution_available: true},
            {operation_id: 'delete', execution_available: true},
          ],
        }],
      },
    };
    api.get.mockResolvedValue({data: {data: gridBootstrap}});
    api.post.mockImplementation((_url, payload) => {
      const responses = {
        open_session: {session_id: 'grid-session'},
        visual_admin_rows: {
          columns: [
            {name: 'id', key: true, editable: true},
            {name: 'name', key: false, editable: true},
          ],
          rows: [{
            values: {id: 1, name: 'first'}, identity_token: 'row-one',
          }],
          editable: true,
        },
        visual_admin_validate: {valid: true, errors: []},
        visual_admin_plan: {
          state: 'ready', execution_available: true,
          plan_id: 'plan-one', plan_digest: 'digest-one',
        },
        visual_admin_apply: {provider_result: {
          accepted: true, staged_in_provider_session: true,
        }},
        transaction_action: {provider_payload: {
          driver_observation_only: true,
          finality_interpreted_by_common_code: false,
        }},
        close_session: {
          session_id: 'grid-session', provider_closed: true,
        },
      };
      return Promise.resolve({data: {data: responses[payload.action]}});
    });
    const {unmount} = render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="data" />);
    fireEvent.click(await screen.findByText('Load rows'));
    const name = await screen.findByDisplayValue('first');
    expect(screen.getByRole('textbox', {name: 'name value'})).toBe(name);
    expect(screen.getByRole('textbox', {name: 'name new value'}))
      .toHaveAttribute('placeholder', 'New value');
    fireEvent.change(name, {target: {value: 'second'}});
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(6));
    expect(api.post.mock.calls.map((call) => call[1].action)).toEqual([
      'open_session', 'visual_admin_rows', 'visual_admin_validate',
      'visual_admin_plan', 'visual_admin_apply', 'visual_admin_rows',
    ]);
    expect(api.post.mock.calls[2][1].request.draft).toEqual({
      selector: {identity_token: 'row-one'},
      changes: {name: 'second'},
      concurrency_token: 'row-one',
    });
    expect(api.post.mock.calls[0][1]).toEqual({
      action: 'open_session', language_profile: 'mysql-sql',
      database_target_id: 'database-one',
    });
    expect(api.post.mock.calls[1][1].request.continuation).toBeNull();
    expect(api.post.mock.calls[1][1].request.database_target_id)
      .toBe('database-one');
    expect(api.post.mock.calls[2][1].request.database_target_id)
      .toBe('database-one');
    expect(api.post.mock.calls[4][1].request.session_id)
      .toBe('grid-session');
    expect(api.post.mock.calls[4][1].request.database_target_id)
      .toBe('database-one');
    expect(await screen.findByLabelText('Staged provider grid changes'))
      .toHaveTextContent('1 grid change');
    fireEvent.click(screen.getByText('Rollback changes'));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(8));
    expect(api.post.mock.calls[6][1]).toEqual({
      action: 'transaction_action', session_id: 'grid-session',
      transaction_action: 'rollback',
      database_target_id: 'database-one',
    });
    fireEvent.click(screen.getByText('Close data session'));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(9));
    expect(api.post.mock.calls[8][1]).toEqual({
      action: 'close_session', session_id: 'grid-session',
      database_target_id: 'database-one',
    });
    expect(screen.getByText('Close data session')).toBeDisabled();
    unmount();
  });

  it('opens the selected provider view in the owning database scope', async () => {
    const table = {
      resource_id: 'table:ASSETS', resource_kind: 'table',
      display_name: 'ASSETS', display_path: ['ASSETS'],
    };
    const view = {
      resource_id: 'view:OPEN_WORK_ORDERS', resource_kind: 'view',
      display_name: 'OPEN_WORK_ORDERS', display_path: ['OPEN_WORK_ORDERS'],
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: [table, view]},
      visual_admin: {
        ...bootstrap.visual_admin,
        objects: [{resource_kind: 'table', operations: []}, {
          resource_kind: 'view', operations: [],
        }],
      },
    }}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {
      data: payload.action === 'open_session' ? {
        session_id: 'view-session',
      } : {
        columns: [{name: 'WORK_ORDER_ID', editable: false}],
        rows: [{values: {WORK_ORDER_ID: 1001}, identity_token: null}],
        editable: false, identity_policy: 'read-only-view',
      },
    }}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="data" initialContext={{
        resource_id: view.resource_id, resource_kind: 'view',
        database_target_id: 'firebird-database-one',
      }} />);
    expect(await screen.findByRole('combobox', {name: 'Table or view'}))
      .toHaveTextContent('OPEN_WORK_ORDERS');
    fireEvent.click(screen.getByText('Load rows'));
    expect(await screen.findByDisplayValue('1001')).toBeInTheDocument();
    expect(api.post.mock.calls[0][1]).toEqual({
      action: 'open_session', language_profile: 'mysql-sql',
      database_target_id: 'firebird-database-one',
    });
    expect(api.post.mock.calls[1][1].request.target_resource).toEqual(view);
    expect(api.post.mock.calls[1][1].request.database_target_id)
      .toBe('firebird-database-one');
    expect(screen.getByText(/This view is read-only/)).toBeInTheDocument();
  });

  it('loads and edits MongoDB documents through provider plans', async () => {
    const collection = {
      resource_id: 'mongodb:collection:example:widgets',
      resource_kind: 'collection', display_name: 'widgets',
      display_path: ['MongoDB', 'example', 'widgets'],
      authority_path: ['mongodb', 'collection', 'example', 'widgets'],
    };
    const documentBootstrap = {
      ...bootstrap,
      resource_page: {items: [collection]},
      visual_admin: {
        engine_id: 'mongodb', model_family: 'document',
        objects: [{
          resource_kind: 'document', title: 'Document', operations: [
            {operation_id: 'insert', execution_available: true},
            {operation_id: 'update', execution_available: true},
            {operation_id: 'delete', execution_available: true},
          ],
        }],
      },
    };
    api.get.mockResolvedValue({data: {data: documentBootstrap}});
    api.post.mockImplementation((_url, payload) => {
      const responses = {
        visual_admin_rows: {
          documents: [{
            _id: {$oid: '0123456789abcdef01234567'}, name: 'first',
          }],
        },
        visual_admin_validate: {valid: true, errors: []},
        visual_admin_plan: {
          state: 'ready', execution_available: true,
          plan_id: 'plan-one', plan_digest: 'digest-one',
        },
        visual_admin_apply: {provider_result: {acknowledged: true}},
      };
      return Promise.resolve({data: {data: responses[payload.action]}});
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="data" />);
    fireEvent.click(await screen.findByText('Load documents'));
    const editor = await screen.findByRole('textbox', {
      name: 'Document JSON',
    });
    fireEvent.change(editor, {target: {value: JSON.stringify({
      _id: {$oid: '0123456789abcdef01234567'}, name: 'second',
    })}});
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(5));
    expect(api.post.mock.calls[1][1].request).toEqual({
      resource_kind: 'document', operation_id: 'update',
      target_resource: collection,
      draft: {
        selector: {_id: {$oid: '0123456789abcdef01234567'}},
        changes: {name: 'second'},
      },
    });
  });

  it('opens the MongoDB collection owning the selected child resource', async () => {
    const firstCollection = {
      resource_id: 'mongodb:collection:example:first',
      resource_kind: 'collection', display_name: 'first',
      display_path: ['MongoDB', 'example', 'first'],
    };
    const selectedCollection = {
      resource_id: 'mongodb:collection:example:selected',
      resource_kind: 'collection', display_name: 'selected',
      display_path: ['MongoDB', 'example', 'selected'],
    };
    const selectedIndex = {
      resource_id: 'mongodb:index:example:selected:lookup',
      resource_kind: 'index', display_name: 'lookup',
      display_path: ['MongoDB', 'example', 'selected', 'lookup'],
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: [firstCollection, selectedCollection,
        selectedIndex]},
      visual_admin: {
        engine_id: 'mongodb', model_family: 'document',
        objects: [{resource_kind: 'document', operations: []}],
      },
    }}});
    api.post.mockResolvedValue({data: {data: {documents: []}}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="data" initialContext={{
        resource_id: selectedIndex.resource_id, resource_kind: 'index',
      }} />);
    const collectionSelector = await screen.findByRole('combobox', {
      name: 'Collection',
    });
    await waitFor(() => expect(collectionSelector)
      .toHaveTextContent('MongoDB.example.selected'));
    fireEvent.click(screen.getByText('Load documents'));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0][1].request.target_resource)
      .toEqual(selectedCollection);
  });

  it('loads and edits Neo4j nodes through provider-owned plans', async () => {
    const graph = {
      resource_id: 'neo4j:graph:neo4j', resource_kind: 'graph',
      display_name: 'neo4j', authority_path: ['neo4j', 'graph', 'neo4j'],
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: [graph]},
      visual_admin: {
        engine_id: 'neo4j', model_family: 'graph', objects: [
          {resource_kind: 'node', operations: [
            {operation_id: 'insert', execution_available: true},
            {operation_id: 'update', execution_available: true},
            {operation_id: 'delete', execution_available: true},
          ]},
          {resource_kind: 'relationship', operations: [
            {operation_id: 'insert', execution_available: true},
            {operation_id: 'update', execution_available: true},
            {operation_id: 'delete', execution_available: true},
          ]},
        ],
      },
    }}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {data: {
      visual_admin_rows: {records: [{n: {
        kind: 'node', element_id: '4:one', labels: ['Person'],
        properties: {name: 'Alice'},
      }}]},
      visual_admin_validate: {valid: true, errors: []},
      visual_admin_plan: {state: 'ready', execution_available: true,
        plan_id: 'graph-plan', plan_digest: 'graph-digest'},
      visual_admin_apply: {provider_result: {records: []}},
    }[payload.action]}}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="data" />);
    fireEvent.click(await screen.findByText('Load graph'));
    const editor = await screen.findByRole('textbox', {name: 'Node properties'});
    fireEvent.change(editor, {target: {value: '{"name":"Alicia"}'}});
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(5));
    expect(api.post.mock.calls[1][1].request).toEqual({
      resource_kind: 'node', operation_id: 'update',
      target_resource: {
        resource_kind: 'node', resource_id: 'neo4j:node:4:one',
        extensions: {neo4j: {native: {element_id: '4:one'}}},
        display_name: '4:one',
      },
      draft: {
        changes: {properties: {name: 'Alicia'}},
      },
    });
  });

  it.each([
    ['cdeadmin/results/TimeSeriesView', 'Time-series results', {
      schema: {time_field: 'time'},
      records: [{time: '2026-09-02T12:00:00Z', usage: 0.5}],
    }],
    ['cdeadmin/results/VectorView', 'Vector results', {
      records: [{id: 7, distance: 0.1, entity: {title: 'nearest'}}],
    }],
    ['cdeadmin/results/SearchView', 'Search results', {
      records: [{_index: 'docs', _id: 'one', _score: 1,
        _source: {title: 'matched'}}],
    }],
  ])('renders analytic result component %s', async (
    componentReference, accessibleName, viewModel
  ) => {
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {
      data: {
        open_session: {session_id: 'analytic-session'},
        execute: {occurrence_id: 'analytic-operation'},
        poll: {
          occurrence: {operation: {terminal: true}},
          rendered_result: {
            component_reference: componentReference,
            view_model: viewModel,
          },
        },
      }[payload.action],
    }}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    await screen.findByText('MySQL SQL');
    fireEvent.click(screen.getByText('Run'));
    expect(await screen.findByLabelText(accessibleName)).toBeInTheDocument();
  });

  it('browses provider-owned analytic data pages', async () => {
    const table = {
      resource_id: 'influxdb:table:cpu', resource_kind: 'table',
      display_name: 'cpu', display_path: ['metrics', 'cpu'],
      authority_path: ['influxdb', 'table', 'metrics', 'cpu'],
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: [table]},
      visual_admin: {
        engine_id: 'influxdb', model_family: 'time-series-analytic',
        objects: [{resource_kind: 'table', operations: []}],
      },
    }}});
    api.post.mockResolvedValue({data: {data: {
      records: [{time: '2026-09-02T12:00:00Z', usage: 0.5}],
      continuation: null,
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="data" />);
    fireEvent.click(await screen.findByText('Load data'));
    expect(await screen.findByText('0.5')).toBeInTheDocument();
    expect(api.post.mock.calls[0][1]).toEqual({
      action: 'visual_admin_rows',
      request: {target_resource: table, limit: 200, continuation: null},
    });
  });

  it('opens the analytic container owning the selected child resource', async () => {
    const firstTable = {
      resource_id: 'influxdb:table:metrics:first', resource_kind: 'table',
      display_name: 'first', display_path: ['metrics', 'first'],
    };
    const selectedTable = {
      resource_id: 'influxdb:table:metrics:selected', resource_kind: 'table',
      display_name: 'selected', display_path: ['metrics', 'selected'],
    };
    const selectedField = {
      resource_id: 'influxdb:field:metrics:selected:usage',
      resource_kind: 'field', display_name: 'usage',
      display_path: ['metrics', 'selected', 'usage'],
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: [firstTable, selectedTable, selectedField]},
      visual_admin: {
        engine_id: 'influxdb', model_family: 'time-series-analytic',
        objects: [{resource_kind: 'table', operations: []}],
      },
    }}});
    api.post.mockResolvedValue({data: {data: {records: []}}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="data" initialContext={{
        resource_id: selectedField.resource_id, resource_kind: 'field',
      }} />);
    const containerSelector = await screen.findByRole('combobox', {
      name: 'Analytic data container',
    });
    await waitFor(() => expect(containerSelector)
      .toHaveTextContent('metrics.selected'));
    fireEvent.click(screen.getByText('Load data'));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0][1].request.target_resource)
      .toEqual(selectedTable);
  });

  it('previews and explicitly confirms provider bulk imports', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      visual_admin: {...bootstrap.visual_admin, objects: [{
        ...bootstrap.visual_admin.objects[0], operations: [{
          ...bootstrap.visual_admin.objects[0].operations[0], blockers: [],
          execution_available: true,
        }],
      }]},
    }}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {
      data: payload.action === 'visual_admin_bulk_plan' ? {
        ready: true, atomicity: 'not-claimed', plans: [{plan: {
          plan_id: 'plan-one', plan_digest: 'digest-one',
          command_preview: 'CREATE DATABASE imported',
        }}],
      } : {complete: true, applied_count: 1, automatic_retry: false},
    }}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="movement" />);
    const source = await screen.findByRole('textbox', {
      name: 'Import records / provider form drafts',
    });
    fireEvent.change(source, {target: {value: '[{"name":"imported"}]'}});
    fireEvent.click(screen.getByText('Validate and preview batch'));
    expect(await screen.findByLabelText('Bulk operation preview'))
      .toHaveTextContent('CREATE DATABASE imported');
    fireEvent.click(screen.getByLabelText(
      'I confirm every provider-planned mutation in this non-atomic batch.'
    ));
    fireEvent.click(screen.getByText('Apply confirmed batch'));
    expect(await screen.findByLabelText('Bulk operation result'))
      .toHaveTextContent('applied_count');
    expect(api.post).toHaveBeenLastCalledWith('/workspace/1', {
      action: 'visual_admin_bulk_apply', request: {
        confirmed: true,
        plans: [{plan_id: 'plan-one', plan_digest: 'digest-one'}],
      },
    });
  });

  it('provides the complete semantic model designer workspace', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: [{
        resource_id: 'table:sales', resource_kind: 'table',
        display_name: 'sales', display_path: ['analytics', 'sales'],
      }]},
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="semantic" />);
    expect(await screen.findByLabelText('Model name')).toBeInTheDocument();
    expect(screen.getByLabelText('Semantic-model task'))
      .toHaveTextContent('Model');
    expect(screen.getByText(/Relational and multidimensional/))
      .toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', {
      name: 'Semantic-model task',
    }), {target: {value: 'query'}});
    expect(screen.getByLabelText('Time operation')).toBeInTheDocument();
    expect(screen.getByText('Native analytical windows')).toBeInTheDocument();
    expect(screen.getByLabelText('Window operation')).toHaveTextContent(
      'running sum'
    );
    expect(screen.getByLabelText('Drill-through fields (source.field, ...)'))
      .toBeInTheDocument();
  });

  it('uses provider-family vocabulary in the semantic model designer', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: [{
        resource_id: 'node:person', resource_kind: 'node',
        display_name: 'Person', display_path: ['graph', 'Person'],
      }]},
      semantic_models: {...bootstrap.semantic_models, capabilities: {
        ...bootstrap.semantic_models.capabilities,
        analytical_profile: {
          title: 'Graph analytics', semantic_family: 'graph',
          source_kinds: ['graph', 'node', 'relationship'],
          source_classifications: ['node-set', 'relationship-set', 'path-set'],
          dimension_kinds: ['label', 'property', 'path', 'community'],
          relationship_kinds: ['native-edge', 'path-pattern'],
          measure_kinds: ['property-aggregate', 'path-count', 'score'],
          grain_vocabulary: 'node-relationship-path',
        },
      }},
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="semantic" />);
    expect(await screen.findByText(/Graph analytics/)).toBeInTheDocument();
    expect(screen.getByRole('combobox', {name: 'Source kind'}))
      .toHaveTextContent('node');
    expect(screen.getByRole('combobox', {name: 'Classification'}))
      .toHaveTextContent('node-set');
    fireEvent.change(screen.getByRole('combobox', {
      name: 'Semantic-model task',
    }), {target: {value: 'relationships'}});
    expect(await screen.findByLabelText('Semantic relationship diagram'))
      .toHaveTextContent('node-set · node');
    expect(screen.getByRole('combobox', {name: 'Relationship kind'}))
      .toHaveTextContent('native-edge');
  });

  it('provides security, chart, dashboard, report and diagnostics workspaces', async () => {
    api.post.mockResolvedValue({data: {data: {
      schema: 'cdeadmin.semantic-query-diagnostics.v1',
      reproducibility: {model_digest: 'model-digest'},
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="semantic" />);
    fireEvent.change(await screen.findByRole('combobox', {
      name: 'Semantic-model task',
    }), {target: {value: 'security'}});
    expect(screen.getByText('Row-level security')).toBeInTheDocument();
    expect(screen.getByLabelText('Policy field')).toBeInTheDocument();
    expect(screen.getByLabelText('Trusted principal claim')).toHaveValue('user_id');
    expect(screen.getByText('Tenant filtering')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', {
      name: 'Semantic-model task',
    }), {target: {value: 'presentation'}});
    expect(screen.getByText('Chart builder')).toBeInTheDocument();
    expect(screen.getByText('Dashboard builder')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', {
      name: 'Semantic-model task',
    }), {target: {value: 'reports'}});
    expect(screen.getByText('Report builder')).toBeInTheDocument();
    expect(screen.getByLabelText('Delivery profile')).toBeInTheDocument();
    expect(screen.getByLabelText('Scheduled export format'))
      .toBeInTheDocument();
    expect(screen.getByLabelText('Recipients or object filename'))
      .toBeInTheDocument();
    expect(screen.getByText(/operator-configured worker authority/))
      .toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', {
      name: 'Semantic-model task',
    }), {target: {value: 'diagnostics'}});
    fireEvent.click(screen.getByText('Refresh query diagnostics'));
    expect(await screen.findByText(/model-digest/)).toBeInTheDocument();
    expect(api.post).toHaveBeenLastCalledWith('/workspace/1', {
      action: 'semantic_query_diagnostics', request: expect.any(Object),
    });
  });

  it('renders semantic query results in the pivot workspace', async () => {
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {
      data: {
        open_session: {session_id: 'semantic-session'},
        execute: {occurrence_id: 'semantic-operation'},
        poll: {
          occurrence: {operation: {terminal: true}},
          rendered_result: {
            component_reference: 'cdeadmin/results/CubePivotView',
            view_model: {
              family: 'cellset',
              axes: {rows: ['region'], columns: [], pages: []},
              levels: ['region'], measures: ['revenue'],
              cells: [{coordinates: {region: 'North'},
                measures: {revenue: 42.5}}], slice: [],
            },
          },
        },
      }[payload.action],
    }}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByText('Run'));
    const pivot = await screen.findByLabelText('Cube pivot results');
    expect(pivot).toHaveTextContent('North');
    expect(pivot).toHaveTextContent('42.5');
    expect(pivot).toHaveTextContent('Drill down');
    expect(pivot).toHaveTextContent('Transpose axes');
  });
});
