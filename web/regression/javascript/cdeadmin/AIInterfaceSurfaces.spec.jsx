/////////////////////////////////////////////////////////////
// Slice 07: exhaustive AI Interface form and screen verification.
/////////////////////////////////////////////////////////////

import {cleanup, fireEvent, render, screen, waitFor, within} from
  '@testing-library/react';
import {withTheme} from '../fake_theme';
import {
  AI_FORM_BY_ID, AI_INTERFACE_FORMS, AI_INTERFACE_SCREENS,
  AI_INTERFACE_SURFACE_SUMMARY, AI_SCREEN_BY_ID, AI_SCREEN_FORM_BINDINGS,
  AIContractForm, AIInterfaceSurface, AIInterfaceWorkspace, evaluateAICondition,
  initialAIFormValues, isAIFormActionEnabled, redactedAIFormValues,
  transientAIFormSubmission, validateAIFormValues,
} from 'sources/cdeadmin_ui/modules/ai_interface';

jest.mock('sources/cdeadmin_ui/editors/CodeEditor', () => function MockCodeEditor({
  label, value, onChange, disabled}) {
  return <textarea aria-label={label} value={value} disabled={disabled}
    data-code-editor="true" onChange={(event) => onChange?.(event.target.value)} />;
});

jest.mock('sources/cdeadmin_ui/data/DataGrid', () => function MockDataGrid({
  'aria-label': label, rows=[], columns=[]}) {
  return <table aria-label={label}><thead><tr>{columns.map((column) =>
    <th key={column.key}>{column.name}</th>)}</tr></thead><tbody>
    {rows.map((row, index) => <tr key={row.__rowKey ?? row.id ?? index}>
      {columns.map((column) => <td key={column.key}>{String(row[column.key] ?? '')}</td>)}
    </tr>)}
  </tbody></table>;
});

const resources = [{id: 'resource-1', label: 'Database / app', type: 'database'}];
const assets = [{id: 'asset-1', label: 'Approved policy', type: 'policy'}];
const connections = [{id: 'connection-1', label: 'AI principal', provider: 'Firebird'}];

function conditionsFor(form) {
  return Object.fromEntries([
    ...form.sections.flatMap((section) => section.fields.flatMap((field) =>
      [field.visibility, field.enabled])),
    ...form.actions.map((action) => action.enabled_when),
  ].filter((condition) => !['always', 'readonly'].includes(condition))
    .map((condition) => [condition, true]));
}

function validValuesFor(form) {
  return Object.fromEntries(form.sections.flatMap((section) => section.fields)
    .map((field) => {
      if(field.default !== null && field.default !== '' &&
          !(Array.isArray(field.default) && field.default.length === 0)) {
        return [field.id, field.default];
      }
      if(!field.required) return [field.id, field.default];
      if(['Checkbox', 'ToggleSwitch'].includes(field.component)) return [field.id, false];
      if(['NumberField', 'UnitNumberField'].includes(field.component)) return [field.id, 1];
      if(['MultiSelect', 'DataGrid'].includes(field.component)) return [field.id,
        field.component === 'DataGrid' ? [{id: 'row-1', status: 'valid'}] : ['configured']];
      if(field.component === 'AssetPicker') return [field.id, assets[0]];
      if(field.component === 'ResourcePicker') return [field.id, resources[0]];
      if(field.component === 'ConnectionSelector') return [field.id, connections[0].id];
      return [field.id, field.options?.[0] ?? 'configured'];
    }));
}

function mountForm(form, props={}) {
  const Component = withTheme(AIContractForm);
  return render(<Component form={form} initialValues={validValuesFor(form)}
    resources={resources} assets={assets}
    connections={connections} context={{conditions: conditionsFor(form),
      actionAvailability: Object.fromEntries(form.actions.map((action) =>
        [action.id, true]))}} {...props} />);
}

function mountWorkspace(props={}) {
  const Component = withTheme(AIInterfaceWorkspace);
  return render(<Component executeCommand={jest.fn().mockResolvedValue({})}
    resources={resources} assets={assets} connections={connections}
    formContext={{conditions: Object.fromEntries(AI_INTERFACE_FORMS
      .flatMap((form) => Object.entries(conditionsFor(form))))}}
    {...props} />);
}

afterEach(cleanup);

describe('AI Interface contract catalogue', () => {
  test('loads exactly 23 gap-free forms and 26 gap-free screens', () => {
    expect(AI_INTERFACE_SURFACE_SUMMARY).toMatchObject({forms: 23, screens: 26,
      commands: 46});
    expect(AI_INTERFACE_SURFACE_SUMMARY.components).toEqual([
      'AssetPicker', 'Banner', 'Checkbox', 'CodeEditor', 'ComboBox',
      'ConnectionSelector', 'DataGrid', 'DateField', 'EnvironmentIndicator',
      'MultiSelect', 'NumberField', 'ResourcePicker', 'SecretField',
      'SegmentedControl', 'Select', 'TextArea', 'TextField', 'ToggleSwitch',
      'UnitNumberField']);
    expect(AI_INTERFACE_FORMS.every((form) => form.spec_gaps.length === 0)).toBe(true);
    expect(AI_INTERFACE_SCREENS.every((item) => item.spec_gaps.length === 0)).toBe(true);
  });

  test('makes every form reachable and every screen navigable exactly once', () => {
    expect(Object.keys(AI_SCREEN_FORM_BINDINGS)).toHaveLength(26);
    expect(new Set(Object.values(AI_SCREEN_FORM_BINDINGS).flat())).toEqual(
      new Set(Object.keys(AI_FORM_BY_ID)));
    expect(Object.keys(AI_SCREEN_BY_ID)).toHaveLength(26);
  });

  test('evaluates declared comparisons and fails closed for unknown capability prose', () => {
    expect(evaluateAICondition('mode == scratchbird_sbsql',
      {mode: 'scratchbird_sbsql'})).toBe(true);
    expect(evaluateAICondition('principal_type != workload_identity',
      {principal_type: 'named'})).toBe(true);
    expect(evaluateAICondition('mode == A or mode == B', {mode: 'B'})).toBe(true);
    expect(evaluateAICondition('engine capability and policy permit', {})).toBe(false);
    expect(evaluateAICondition('engine capability and policy permit', {},
      {conditions: {'engine capability and policy permit': true}})).toBe(true);
  });

  test('initializes, validates and redacts without mutating the normative contract', () => {
    const form = AI_FORM_BY_ID['ai.database_connector'];
    const values = initialAIFormValues(form, {credential: 'credential-ref'});
    expect(values.mode).toBe('database_provider');
    expect(validateAIFormValues(form, values).valid).toBe(false);
    expect(redactedAIFormValues(form, values)).not.toHaveProperty('credential');
    const submission = transientAIFormSubmission(form, values);
    expect(submission.values.credential).toBe('credential-ref');
    expect(submission.persistedValues).not.toHaveProperty('credential');
    expect(form.sections[0].fields[0].default).toBeNull();
  });

  test('enables known local rules and fails closed on unresolved action policy', () => {
    const form = AI_FORM_BY_ID['ai.query_review'];
    const action = {...form.actions[0], enabled_when: 'query non-empty'};
    expect(isAIFormActionEnabled(action, form, {query_text: 'select'},
      {valid: true}, {})).toBe(true);
    expect(isAIFormActionEnabled({...action, enabled_when: 'external policy'},
      form, {}, {valid: true}, {})).toBe(false);
  });
});

describe('all 23 AI forms', () => {
  test.each(AI_INTERFACE_FORMS.map((form) => [form.form_id, form]))(
    'renders every declared field and action for %s', (_id, form) => {
      const {container} = mountForm(form);
      expect(container.querySelector(`[data-ai-form="${form.form_id}"]`)).toBeTruthy();
      const seen = new Set();
      const collect = () => container.querySelectorAll('[data-ai-field]')
        .forEach((field) => seen.add(field.dataset.aiField));
      collect();
      if(form.kind === 'wizard') {
        for(let step = 0; step < form.sections.length; step += 1) {
          fireEvent.click(screen.getByRole('button', {name: 'Next'})); collect();
        }
      }
      expect(seen).toEqual(new Set(form.sections.flatMap((section) =>
        section.fields.map((field) => field.id))));
      expect(container).not.toHaveTextContent('Unsupported contract component');
      for(const action of form.actions) {
        expect(screen.getByRole('button', {name: action.label})).toBeInTheDocument();
      }
    });

  test('honors conditional visibility rather than exposing incompatible access', () => {
    const originalInnerHeight = window.innerHeight;
    Object.defineProperty(window, 'innerHeight', {configurable: true, value: 1600});
    const form = AI_FORM_BY_ID['ai.database_connector'];
    const Component = withTheme(AIContractForm);
    render(<Component form={form} initialValues={{...validValuesFor(form),
      mode: 'database_provider'}} />);
    fireEvent.click(screen.getByRole('button', {name: 'Next'}));
    fireEvent.click(screen.getByRole('button', {name: 'Next'}));
    expect(screen.queryByText('Cross-surface ScratchBird query')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Back'}));
    fireEvent.click(screen.getByRole('button', {name: 'Back'}));
    fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Access mode'}));
    fireEvent.click(screen.getByRole('option', {name: 'scratchbird_sbsql'}));
    fireEvent.click(screen.getByRole('button', {name: 'Next'}));
    fireEvent.click(screen.getByRole('button', {name: 'Next'}));
    expect(screen.getByText('Cross-surface ScratchBird query')).toBeInTheDocument();
    expect(screen.getByRole('checkbox',
      {name: 'Cross-surface ScratchBird query'})).toBeDisabled();
    Object.defineProperty(window, 'innerHeight',
      {configurable: true, value: originalInnerHeight});
  });

  test('does not expose raw credential input through persistence callbacks', async() => {
    const onValuesChange = jest.fn(); const executeCommand = jest.fn().mockResolvedValue({});
    const form = AI_FORM_BY_ID['ai.database_connector'];
    mountForm(form, {onValuesChange, executeCommand});
    fireEvent.click(screen.getByRole('button', {name: 'Next'}));
    const credential = document.querySelector('[data-ai-field="credential"] input');
    expect(credential).toBeTruthy();
    fireEvent.change(credential,
      {target: {value: 'vault:credential/ai-one'}});
    expect(onValuesChange).toHaveBeenLastCalledWith(expect.not.objectContaining({
      credential: expect.anything()}));
    for(let step = 0; step < 4; step += 1) {
      fireEvent.click(screen.getByRole('button', {name: 'Next'}));
    }
    fireEvent.click(screen.getByRole('button', {name: 'Create'}));
    await waitFor(() => expect(executeCommand).toHaveBeenCalledWith(
      'ai.connector.create', expect.objectContaining({formId: 'ai.database_connector',
        values: expect.objectContaining({credential: 'vault:credential/ai-one'}),
        persistedValues: expect.not.objectContaining({credential: expect.anything()})})));
    await waitFor(() => expect(screen.getByRole('button', {name: 'Create'}))
      .not.toHaveAttribute('aria-busy', 'true'));
  });

  test('shows required validation and blocks the default action without policy override', () => {
    const form = AI_FORM_BY_ID['ai.new_session'];
    const Component = withTheme(AIContractForm);
    render(<Component form={form} state="invalid" />);
    const required = form.sections.flatMap((section) => section.fields)
      .find((field) => field.required && (field.default === null || field.default === ''));
    expect(screen.getByText(`${required.label} is required.`)).toBeVisible();
    const defaultAction = form.actions.find((action) => action.default);
    expect(screen.getByRole('button', {name: defaultAction.label})).toBeDisabled();
  });

  test.each(['loading', 'empty', 'error', 'permission-denied', 'disconnected',
    'stale', 'invalid'])('renders the %s state without discarding the form', (state) => {
    const form = AI_FORM_BY_ID['ai.tool_policy'];
    const {container} = mountForm(form, {state, error: state === 'error' ?
      'Runtime failure' : ''});
    expect(container.querySelector('[data-ai-form="ai.tool_policy"]')).toBeTruthy();
    const expected = {loading: 'Loading AI Tool / CDEadmin Capability Policy',
      empty: /no saved values/i, error: 'Runtime failure',
      'permission-denied': /Permission denied/i,
      disconnected: /provider is disconnected/i, stale: /provider data is stale/i,
      invalid: /Correct the identified/i}[state];
    if(state === 'loading') expect(screen.getByLabelText(expected)).toBeVisible();
    else expect(screen.getByText(expected)).toBeVisible();
  });

  test('keeps Enter inside an editor and maps Escape to modal close', () => {
    const close = jest.fn(); const executeCommand = jest.fn();
    let mounted = mountForm(AI_FORM_BY_ID['ai.instruction_asset'],
      {onClose: close, executeCommand});
    fireEvent.keyDown(screen.getByLabelText('Instructions'), {key: 'Enter'});
    expect(executeCommand).not.toHaveBeenCalled();
    mounted.unmount();
    mounted = mountForm(AI_FORM_BY_ID['ai.new_session'],
      {onClose: close, executeCommand});
    fireEvent.keyDown(screen.getByRole('form', {name: 'New AI Session'}),
      {key: 'Escape'});
    expect(close).toHaveBeenCalled();
    mounted.unmount();
  });

  test('implements the database connector as a six-step wizard with protected review', () => {
    const form = AI_FORM_BY_ID['ai.database_connector'];
    mountForm(form, {initialValues: {...validValuesFor(form),
      credential: 'vault:credential/ai-one'}});
    expect(screen.getByRole('status', {name: 'Wizard progress'}))
      .toHaveTextContent('Step 1 of 6');
    expect(screen.getByRole('button', {name: 'Back'})).toBeDisabled();
    for(let step = 0; step < 5; step += 1) {
      fireEvent.click(screen.getByRole('button', {name: 'Next'}));
    }
    expect(screen.getByRole('status', {name: 'Wizard progress'}))
      .toHaveTextContent('Step 6 of 6: Review');
    expect(screen.queryByText('vault:credential/ai-one')).not.toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Next'})).toBeDisabled();
  });

  test('fails closed when a command executor is not installed', () => {
    mountForm(AI_FORM_BY_ID['ai.connector_test']);
    expect(screen.getByRole('button', {name: 'Run Again'})).toBeDisabled();
  });

  test('disables connection selection when the form is permission denied', () => {
    mountForm(AI_FORM_BY_ID['ai.database_connector'],
      {state: 'permission-denied'});
    expect(screen.getByRole('combobox',
      {name: 'Base connection profile'})).toHaveAttribute('aria-disabled', 'true');
  });
});

describe('all 26 AI screens', () => {
  test.each(AI_INTERFACE_SCREENS.map((item) => [item.screen_id, item]))(
    'composes %s with its exact toolbar and host regions', (screenId, contract) => {
      const {container} = mountWorkspace({screenId});
      expect(container.querySelector(`[data-ai-screen="${screenId}"]`)).toBeTruthy();
      const toolbar = screen.getByRole('toolbar',
        {name: `${contract.screen_id.split('.').at(-1).split('_').map((part) =>
          part.charAt(0).toUpperCase() + part.slice(1)).join(' ')} toolbar`});
      for(const command of contract.toolbar_commands) {
        const label = command.replace(/^ai\./, '').split('.').flatMap((part) =>
          part.split('_')).map((part) => part.charAt(0).toUpperCase() +
          part.slice(1)).join(' ');
        expect(within(toolbar).getByRole('button', {name: label})).toBeInTheDocument();
      }
      if(contract.host_regions.includes('navigator')) {
        expect(screen.getByRole('navigation', {name: 'AI Interface screens'})).toBeVisible();
      }
      if(contract.host_regions.includes('inspector')) {
        expect(screen.getByRole('complementary', {name: /inspector/i})).toBeVisible();
      }
    });

  test('navigates through the shared screen tree and reports the authoritative identity', () => {
    const onNavigate = jest.fn(); mountWorkspace({onNavigate});
    fireEvent.click(screen.getByRole('treeitem', {name: 'Audit'}));
    expect(onNavigate).toHaveBeenCalledWith('cdeadmin.ai_interface.audit');
    expect(document.querySelector('[data-ai-screen="cdeadmin.ai_interface.audit"]')).toBeTruthy();
  });

  test('routes toolbar commands through the command boundary with screen identity', () => {
    const executeCommand = jest.fn().mockResolvedValue({});
    mountWorkspace({screenId: 'cdeadmin.ai_interface.connector_health',
      executeCommand});
    fireEvent.click(screen.getByRole('button', {name: 'Connector Test'}));
    expect(executeCommand).toHaveBeenCalledWith('ai.connector.test',
      {screenId: 'cdeadmin.ai_interface.connector_health'});
  });

  test('renders supplied records through the common grid contract', () => {
    mountWorkspace({screenId: 'cdeadmin.ai_interface.audit', data: [
      {id: 'audit-1', event: 'connector.test', outcome: 'allowed'}]});
    expect(screen.getByRole('table', {name: 'Audit records'})).toBeVisible();
    expect(screen.getByText('connector.test')).toBeVisible();
  });

  test('filters and paginates audit records without losing result identity', () => {
    const data = Array.from({length: 55}, (_item, index) => ({id: `audit-${index}`,
      event: `event-${index}`, outcome: index % 2 ? 'allowed' : 'denied'}));
    mountWorkspace({screenId: 'cdeadmin.ai_interface.audit', data, pageSize: 10});
    expect(screen.getByRole('navigation', {name: 'Audit result pages'}))
      .toHaveTextContent('1–10 of 55');
    fireEvent.click(screen.getByRole('button', {name: 'Next page'}));
    expect(screen.getByText('event-10')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Search Audit'),
      {target: {value: 'event-54'}});
    expect(screen.getByRole('navigation', {name: 'Audit result pages'}))
      .toHaveTextContent('1–1 of 1');
    expect(screen.getByText('event-54')).toBeVisible();
  });

  test('renders run progress and protected authoritative inspector/drawer data', () => {
    let mounted = mountWorkspace({screenId: 'cdeadmin.ai_interface.run_monitor',
      data: [{id: 'run-1', progress: 33}]});
    expect(screen.getByRole('status', {name: 'Run Monitor progress'}))
      .toHaveTextContent('33%');
    mounted.unmount();
    mounted = mountWorkspace({inspectorData: {Context: {apiKey: 'raw-key',
      resource: 'orders'}}, drawerData: {Plan: {credential: 'raw-credential',
      step: 'review'}}});
    expect(screen.getAllByText(/\[protected\]/)).toHaveLength(2);
    expect(screen.queryByText(/raw-key|raw-credential/)).not.toBeInTheDocument();
    expect(screen.getByText(/orders/)).toBeVisible();
    expect(screen.getByText(/review/)).toBeVisible();
    mounted.unmount();
  });

  test('disables toolbar commands when no command boundary is installed', () => {
    const Component = withTheme(AIInterfaceWorkspace);
    render(<Component screenId="cdeadmin.ai_interface.connector_health" />);
    expect(screen.getByRole('button', {name: 'Connector Test'})).toBeDisabled();
  });

  test('hosts dialog-class screens as actual accessible dialogs', () => {
    const Component = withTheme(AIInterfaceSurface);
    render(<Component screenId="cdeadmin.ai_interface.action_approval"
      executeCommand={jest.fn()} />);
    expect(screen.getByRole('dialog', {name: 'Action Approval'})).toBeVisible();
    expect(screen.getByRole('form',
      {name: 'Confirm High-Risk AI Action'})).toBeVisible();
  });

  test.each(['loading', 'empty', 'error', 'permission', 'disconnected',
    'stale', 'partial', 'invalid', 'background'])(
    'makes the %s screen state explicit', (screenState) => {
      mountWorkspace({screenState, error: screenState === 'error' ? 'AI failed' : ''});
      expect(document.querySelector('[data-ai-screen]')).toHaveAttribute(
        'data-ai-screen', 'cdeadmin.ai_interface.ai_workbench');
      if(screenState === 'loading') expect(screen.getByLabelText(
        'Loading Ai Workbench')).toBeVisible();
      else expect(screen.getAllByRole(screenState === 'error' ||
        screenState === 'permission' ? 'alert' : 'status').length).toBeGreaterThan(0);
    });

  test('supports host-authorized detachment and keyboard region focus', async() => {
    const onDetach = jest.fn(); const {container} = mountWorkspace({onDetach});
    fireEvent.click(screen.getByRole('button', {name: 'Detach Ai Workbench'}));
    expect(onDetach).toHaveBeenCalledWith('cdeadmin.ai_interface.ai_workbench');
    fireEvent.keyDown(container.querySelector('[data-ai-screen]'),
      {key: '1', ctrlKey: true});
    await waitFor(() => expect(screen.getByRole('navigation',
      {name: 'AI Interface screens'}).parentElement).toHaveFocus());
  });

  test('provides a searchable non-drag resource-picker path', async() => {
    mountWorkspace({screenId: 'cdeadmin.ai_interface.context_manager'});
    fireEvent.click(screen.getByRole('button', {name: 'Choose resource'}));
    fireEvent.change(screen.getByLabelText('Filter resources'),
      {target: {value: 'Database'}});
    const option = screen.getByRole('option', {name: /Database \/ app/});
    fireEvent.click(option);
    await waitFor(() => expect(screen.getByRole('button', {name: 'Select'}))
      .toBeEnabled());
    fireEvent.click(screen.getByRole('button', {name: 'Select'}));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByText('Database / app')).toBeVisible();
  });
});
