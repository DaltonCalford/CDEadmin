/////////////////////////////////////////////////////////////
// Slice 14: exhaustive Discovery form and screen verification.
/////////////////////////////////////////////////////////////

import {cleanup, fireEvent, render, screen, waitFor, within} from
  '@testing-library/react';
import {withTheme} from '../fake_theme';
import {
  DISCOVERY_FORM_BY_ID, DISCOVERY_INTERFACE_FORMS,
  DISCOVERY_INTERFACE_SCREENS, DISCOVERY_INTERFACE_SURFACE_SUMMARY,
  DISCOVERY_SCREEN_BY_ID, DISCOVERY_SCREEN_FORM_BINDINGS,
  DiscoveryContractForm, DiscoveryInterfaceSurface,
  DiscoveryInterfaceWorkspace, evaluateDiscoveryCondition,
  initialDiscoveryFormValues, isDiscoveryFormActionEnabled,
  redactedDiscoveryFormValues, transientDiscoveryFormSubmission,
  validateDiscoveryFormValues,
} from 'sources/cdeadmin_ui/modules/discovery_intelligence';

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

const resources = [{id: 'resource-1', label: 'Orders database', type: 'database'}];
const assets = [{id: 'asset-1', label: 'Certified sales model', type: 'model'}];
const connections = [{id: 'connection-1', label: 'Discovery index',
  provider: 'OpenSearch'}];

function conditionsFor(form) {
  return Object.fromEntries([
    ...form.sections.flatMap((section) => section.fields.flatMap((field) =>
      [field.visibility, field.enabled])),
    ...form.actions.map((action) => action.enabled_when),
  ].filter((condition) => !['always', 'readonly'].includes(condition) &&
    !/^[a-zA-Z0-9_.-]+\s*(?:==|!=)\s*[a-zA-Z0-9_.-]+(?:\s+or\s+.*)?$/.test(condition))
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
      if(field.component === 'NumberField') return [field.id, 1];
      if(field.component === 'DurationField') return [field.id, 'P30D'];
      if(['MultiSelect', 'DataGrid'].includes(field.component)) return [field.id,
        field.component === 'DataGrid' ? [{id: 'row-1', status: 'valid'}] : ['configured']];
      if(field.component === 'AssetPicker') return [field.id, assets[0]];
      if(field.component === 'ResourcePicker') return [field.id, resources[0]];
      if(field.component === 'ConnectionSelector') return [field.id, connections[0].id];
      return [field.id, field.options?.[0] ?? 'configured'];
    }));
}

function mountForm(form, props={}) {
  const Component = withTheme(DiscoveryContractForm);
  return render(<Component form={form} initialValues={validValuesFor(form)}
    resources={resources} assets={assets} connections={connections}
    context={{conditions: conditionsFor(form),
      actionAvailability: Object.fromEntries(form.actions.map((action) =>
        [action.id, true]))}} {...props} />);
}

function mountWorkspace(props={}) {
  const Component = withTheme(DiscoveryInterfaceWorkspace);
  return render(<Component executeCommand={jest.fn().mockResolvedValue({})}
    resources={resources} assets={assets} connections={connections}
    formContext={{conditions: Object.fromEntries(DISCOVERY_INTERFACE_FORMS
      .flatMap((form) => Object.entries(conditionsFor(form))))}}
    {...props} />);
}

afterEach(cleanup);

describe('Discovery Interface contract catalogue', () => {
  test('loads exactly 30 gap-free forms and 38 gap-free screens', () => {
    expect(DISCOVERY_INTERFACE_SURFACE_SUMMARY).toMatchObject({forms: 30,
      screens: 38, commands: 50});
    expect(DISCOVERY_INTERFACE_SURFACE_SUMMARY.components).toEqual([
      'AssetPicker', 'Checkbox', 'CodeEditor', 'ComboBox', 'ConnectionSelector',
      'DataGrid', 'DateField', 'DurationField', 'MultiSelect', 'NumberField',
      'ResourcePicker', 'SearchField', 'SecretField', 'SegmentedControl', 'Select',
      'TextArea', 'TextField', 'ToggleSwitch']);
    expect(DISCOVERY_INTERFACE_FORMS.every((form) => !form.spec_gaps.length)).toBe(true);
    expect(DISCOVERY_INTERFACE_SCREENS.every((item) => !item.spec_gaps.length)).toBe(true);
  });

  test('makes every form reachable and every screen addressable', () => {
    expect(Object.keys(DISCOVERY_SCREEN_FORM_BINDINGS)).toHaveLength(38);
    expect(new Set(Object.values(DISCOVERY_SCREEN_FORM_BINDINGS).flat())).toEqual(
      new Set(Object.keys(DISCOVERY_FORM_BY_ID)));
    expect(Object.keys(DISCOVERY_SCREEN_BY_ID)).toHaveLength(38);
  });

  test('evaluates Discovery comparisons and fails closed for unknown policy prose', () => {
    expect(evaluateDiscoveryCondition('decision == CERTIFIED_WITH_CONDITIONS',
      {decision: 'CERTIFIED_WITH_CONDITIONS'})).toBe(true);
    expect(evaluateDiscoveryCondition('type != cdeadmin_managed',
      {type: 'scratchbird'})).toBe(true);
    expect(evaluateDiscoveryCondition('curation permission', {})).toBe(false);
    expect(evaluateDiscoveryCondition('curation permission', {},
      {conditions: {'curation permission': true}})).toBe(true);
  });

  test('validates and redacts semantic-index credentials without contract mutation', () => {
    const form = DISCOVERY_FORM_BY_ID['discovery.embedding_config'];
    const values = initialDiscoveryFormValues(form, {credential: 'vault:embedding'});
    expect(validateDiscoveryFormValues(form, values).valid).toBe(false);
    expect(redactedDiscoveryFormValues(form, values)).not.toHaveProperty('credential');
    const submission = transientDiscoveryFormSubmission(form, values);
    expect(submission.values.credential).toBe('vault:embedding');
    expect(submission.persistedValues).not.toHaveProperty('credential');
    expect(form.sections[0].fields.find(({id}) => id === 'credential').default).toBeNull();
  });

  test('enables declared local rules and fails closed on unknown authority', () => {
    const form = DISCOVERY_FORM_BY_ID['discovery.quick_search'];
    const action = form.actions[0];
    expect(isDiscoveryFormActionEnabled(action, form, {text: 'orders'},
      {valid: true}, {})).toBe(true);
    expect(isDiscoveryFormActionEnabled({...action, enabled_when: 'unknown rule'},
      form, {}, {valid: true}, {})).toBe(false);
  });
});

describe('all 30 Discovery forms', () => {
  test.each(DISCOVERY_INTERFACE_FORMS.map((form) => [form.form_id, form]))(
    'renders every declared field and action for %s', (_id, form) => {
      const conditionalValues = form.form_id === 'discovery.certification_review' ?
        {decision: 'CERTIFIED_WITH_CONDITIONS'} :
        form.form_id === 'discovery.index_backend' ?
          {type: 'external_search_adapter'} : {};
      const {container} = mountForm(form, {initialValues: {
        ...validValuesFor(form), ...conditionalValues}});
      expect(container.querySelector(`[data-discovery-form="${form.form_id}"]`)).toBeTruthy();
      const seen = new Set();
      const collect = () => container.querySelectorAll('[data-discovery-field]')
        .forEach((field) => seen.add(field.dataset.discoveryField));
      collect();
      if(form.kind === 'wizard') {
        for(let step = 0; step < form.sections.length; step += 1) {
          fireEvent.click(screen.getByRole('button', {name: 'Next'})); collect();
        }
      }
      expect(seen).toEqual(new Set(form.sections.flatMap((section) =>
        section.fields.map((field) => field.id))));
      for(const action of form.actions) {
        expect(screen.getByRole('button', {name: action.label})).toBeInTheDocument();
      }
    });

  test('preserves ISO duration semantics while changing native duration units', () => {
    const originalInnerHeight = window.innerHeight;
    Object.defineProperty(window, 'innerHeight', {configurable: true, value: 1600});
    const onValuesChange = jest.fn();
    mountForm(DISCOVERY_FORM_BY_ID['discovery.access_request'], {onValuesChange});
    const duration = document.querySelector('[data-discovery-field="duration"] input');
    expect(duration).toBeTruthy();
    expect(duration).toHaveValue(30);
    fireEvent.change(duration, {target: {value: '45'}});
    expect(onValuesChange).toHaveBeenLastCalledWith(expect.objectContaining({duration: 'P45D'}));
    fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Duration unit'}));
    fireEvent.click(screen.getByRole('option', {name: 'hours'}));
    expect(onValuesChange).toHaveBeenLastCalledWith(expect.objectContaining({duration: 'PT45H'}));
    Object.defineProperty(window, 'innerHeight',
      {configurable: true, value: originalInnerHeight});
  });

  test('honors conditional certification and backend fields', () => {
    let mounted = mountForm(DISCOVERY_FORM_BY_ID['discovery.certification_review'],
      {initialValues: {...validValuesFor(
        DISCOVERY_FORM_BY_ID['discovery.certification_review']),
      decision: 'CERTIFIED'}});
    expect(screen.queryByLabelText('Conditions')).not.toBeInTheDocument();
    mounted.unmount();
    mounted = mountForm(DISCOVERY_FORM_BY_ID['discovery.certification_review'],
      {initialValues: {...validValuesFor(
        DISCOVERY_FORM_BY_ID['discovery.certification_review']),
      decision: 'CERTIFIED_WITH_CONDITIONS'}});
    expect(screen.getByLabelText('Conditions')).toBeVisible();
    mounted.unmount();
    mounted = mountForm(DISCOVERY_FORM_BY_ID['discovery.index_backend'],
      {initialValues: {...validValuesFor(DISCOVERY_FORM_BY_ID['discovery.index_backend']),
        type: 'cdeadmin_managed'}});
    expect(screen.queryByLabelText('Connection / service reference')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Credential reference')).not.toBeInTheDocument();
    mounted.unmount();
    mounted = mountForm(DISCOVERY_FORM_BY_ID['discovery.index_backend'],
      {initialValues: {...validValuesFor(DISCOVERY_FORM_BY_ID['discovery.index_backend']),
        type: 'external_search_adapter'}});
    expect(screen.getByLabelText('Connection / service reference')).toBeVisible();
    expect(screen.getByLabelText('Credential reference')).toBeVisible();
    mounted.unmount();
  });

  test('never exposes an embedding credential through persistence callbacks', () => {
    const onValuesChange = jest.fn();
    mountForm(DISCOVERY_FORM_BY_ID['discovery.embedding_config'], {onValuesChange});
    fireEvent.change(screen.getByLabelText('Credential reference'),
      {target: {value: 'vault:embedding/private'}});
    expect(onValuesChange).toHaveBeenLastCalledWith(expect.not.objectContaining({
      credential: expect.anything()}));
  });

  test.each(['loading', 'empty', 'error', 'permission-denied', 'disconnected',
    'stale', 'invalid'])('renders the %s state without discarding authored form identity',
    (state) => {
      const {container} = mountForm(DISCOVERY_FORM_BY_ID['discovery.metric'],
        {state, error: state === 'error' ? 'Discovery failure' : ''});
      expect(container.querySelector('[data-discovery-form="discovery.metric"]')).toBeTruthy();
      const expected = {empty: /has no saved values/i,
        error: 'Discovery failure', 'permission-denied': /Permission denied/i,
        disconnected: /provider is disconnected/i, stale: /provider data is stale/i,
        invalid: /Correct the identified/i}[state];
      if(state === 'loading') expect(screen.getByLabelText(
        'Loading Metric Definition')).toBeVisible();
      else expect(screen.getByText(expected)).toBeVisible();
    });

  test('implements external catalogue setup as a four-stage accessible wizard', () => {
    const form = DISCOVERY_FORM_BY_ID['discovery.external_catalog_source'];
    mountForm(form);
    expect(screen.getByRole('status', {name: 'Wizard progress'}))
      .toHaveTextContent('Step 1 of 4');
    for(let step = 0; step < 3; step += 1) {
      fireEvent.click(screen.getByRole('button', {name: 'Next'}));
    }
    expect(screen.getByRole('status', {name: 'Wizard progress'}))
      .toHaveTextContent('Step 4 of 4: Review');
    expect(screen.getByText('Review')).toBeVisible();
  });
});

describe('all 38 Discovery screens', () => {
  test.each(DISCOVERY_INTERFACE_SCREENS.map((item) => [item.screen_id, item]))(
    'composes %s with its exact toolbar and host regions', (screenId, contract) => {
      const {container} = mountWorkspace({screenId});
      expect(container.querySelector(`[data-discovery-screen="${screenId}"]`)).toBeTruthy();
      const title = contract.screen_id.split('.').at(-1).split('_').map((part) =>
        part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
      const toolbar = screen.getByRole('toolbar', {name: `${title} toolbar`});
      for(const command of contract.toolbar_commands) {
        const label = command.replace(/^discovery\./, '').split('.').flatMap((part) =>
          part.split('_')).map((part) => part.charAt(0).toUpperCase() +
          part.slice(1)).join(' ');
        expect(within(toolbar).getByRole('button', {name: label})).toBeInTheDocument();
      }
      if(contract.host_regions.includes('navigator')) {
        expect(screen.getByRole('navigation',
          {name: 'Discovery Intelligence screens'})).toBeVisible();
      }
      if(contract.host_regions.includes('inspector')) {
        expect(screen.getByRole('complementary', {name: /inspector/i})).toBeVisible();
      }
    });

  test('navigates the full Discovery catalogue and reports canonical identity', () => {
    const onNavigate = jest.fn(); mountWorkspace({onNavigate});
    fireEvent.click(screen.getByRole('treeitem', {name: 'Index Health'}));
    expect(onNavigate).toHaveBeenCalledWith(
      'cdeadmin.discovery_intelligence.index_health');
    expect(document.querySelector(
      '[data-discovery-screen="cdeadmin.discovery_intelligence.index_health"]')).toBeTruthy();
  });

  test('routes toolbar commands with the Discovery screen identity', () => {
    const executeCommand = jest.fn().mockResolvedValue({});
    mountWorkspace({screenId: 'cdeadmin.discovery_intelligence.visibility_test',
      executeCommand});
    fireEvent.click(screen.getByRole('button', {name: 'Visibility Test'}));
    expect(executeCommand).toHaveBeenCalledWith('discovery.visibility.test',
      {screenId: 'cdeadmin.discovery_intelligence.visibility_test'});
  });

  test('filters and paginates large Discovery search results', () => {
    const data = Array.from({length: 55}, (_item, index) => ({id: `result-${index}`,
      name: `entity-${index}`, provider: index % 2 ? 'Firebird' : 'MongoDB'}));
    mountWorkspace({screenId: 'cdeadmin.discovery_intelligence.search_results',
      data, pageSize: 10});
    expect(screen.getByRole('navigation', {name: 'Search Results result pages'}))
      .toHaveTextContent('1–10 of 55');
    fireEvent.click(screen.getByRole('button', {name: 'Next page'}));
    expect(screen.getByText('entity-10')).toBeVisible();
    const searches = screen.getAllByLabelText('Search Search Results');
    fireEvent.change(searches[0], {target: {value: 'entity-54'}});
    expect(screen.getByRole('navigation', {name: 'Search Results result pages'}))
      .toHaveTextContent('1–1 of 1');
  });

  test('uses native list rows, functional tabs and a tabular graph alternative', () => {
    let mounted = mountWorkspace({data: [
      {id: 'entity-1', name: 'Orders', provider: 'Firebird'}]});
    expect(screen.getByRole('listbox', {name: 'Discovery Home records'})).toBeVisible();
    expect(screen.getByRole('option', {name: /Orders/})).toBeVisible();
    mounted.unmount();
    const onScreenTabChange = jest.fn();
    mounted = mountWorkspace({screenId: 'cdeadmin.discovery_intelligence.data_360',
      screenTabs: [{id: 'overview', label: 'Overview'},
        {id: 'lineage', label: 'Lineage'}], onScreenTabChange});
    fireEvent.click(screen.getByRole('tab', {name: 'Lineage'}));
    expect(onScreenTabChange).toHaveBeenCalledWith('lineage',
      'cdeadmin.discovery_intelligence.data_360');
    mounted.unmount();
    mounted = mountWorkspace({screenId:
      'cdeadmin.discovery_intelligence.related_graph', data: [
      {id: 'edge-1', from: 'orders', to: 'customers', relation: 'lineage'}]});
    expect(screen.getByRole('table', {name: 'Related Graph records'})).toBeVisible();
    mounted.unmount();
  });

  test('protects secret inspector and drawer content', () => {
    mountWorkspace({inspectorData: {'Selected result': {apiKey: 'raw-key', owner: 'data'}},
      drawerData: {Problems: {credential: 'raw-credential', result: 'safe'}}});
    expect(screen.getAllByText(/\[protected\]/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/raw-key|raw-credential/)).not.toBeInTheDocument();
  });

  test('hosts dialog-class Discovery screens as accessible dialogs', () => {
    const Component = withTheme(DiscoveryInterfaceSurface);
    render(<Component screenId="cdeadmin.discovery_intelligence.access_request"
      executeCommand={jest.fn()} resources={resources} assets={assets} />);
    expect(screen.getByRole('dialog', {name: 'Access Request'})).toBeVisible();
    expect(screen.getByRole('form', {name: 'Request Data Access'})).toBeVisible();
  });

  test.each(['loading', 'empty', 'error', 'permission', 'disconnected', 'stale',
    'partial', 'invalid', 'background'])('makes the %s screen state explicit',
    (screenState) => {
      mountWorkspace({screenState,
        error: screenState === 'error' ? 'Discovery unavailable' : ''});
      expect(document.querySelector('[data-discovery-screen]')).toHaveAttribute(
        'data-discovery-screen',
        'cdeadmin.discovery_intelligence.discovery_home');
      if(screenState === 'loading') expect(screen.getByLabelText(
        'Loading Discovery Home')).toBeVisible();
      else expect(screen.getAllByRole(['error', 'permission'].includes(screenState) ?
        'alert' : 'status').length).toBeGreaterThan(0);
    });

  test('supports authorized detachment and keyboard region focus', async() => {
    const onDetach = jest.fn(); const {container} = mountWorkspace({onDetach});
    fireEvent.click(screen.getByRole('button', {name: 'Detach Discovery Home'}));
    expect(onDetach).toHaveBeenCalledWith(
      'cdeadmin.discovery_intelligence.discovery_home');
    fireEvent.keyDown(container.querySelector('[data-discovery-screen]'),
      {key: '1', ctrlKey: true});
    await waitFor(() => expect(screen.getByRole('navigation',
      {name: 'Discovery Intelligence screens'}).parentElement).toHaveFocus());
  });

  test('retains every required region at narrow viewport width', () => {
    const originalWidth = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', {configurable: true, value: 640});
    mountWorkspace();
    expect(screen.getByRole('navigation',
      {name: 'Discovery Intelligence screens'})).toBeInTheDocument();
    expect(screen.getByRole('main', {name: 'Discovery Home workbench'}))
      .toBeInTheDocument();
    expect(screen.getByRole('complementary', {name: 'Discovery Home inspector'}))
      .toBeInTheDocument();
    expect(screen.getAllByRole('status').length).toBeGreaterThan(0);
    Object.defineProperty(window, 'innerWidth',
      {configurable: true, value: originalWidth});
  });
});
