/////////////////////////////////////////////////////////////
// Data Contract surface interaction, state and navigation gates.
/////////////////////////////////////////////////////////////

import {fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {
  ContractNavigator, ContractWorkspace, contractInspector,
} from 'sources/cdeadmin_ui/modules/data_contract';
import usePreferences from '../../../pgadmin/preferences/static/js/store';

const resourceRef = {schema: 'cdeadmin.resource-ref.v1', provider: 'mongodb',
  canonical: 'cde-resource://mongodb/local/database/sales/collection/orders'};
const element = {id: 'orders', name: 'Orders', logicalType: 'document', parentId: null,
  description: 'Order documents', classification: 'confidential', required: true,
  constraints: {required: ['order_id']}, physicalDefinition: {collection: 'orders'},
  authoritativeDefinitionRefs: [{schema: 'cdeadmin.external-ref.v1', id: 'orders-api'}],
  extensions: {native: 'document'}};
const binding = {id: 'orders-production', elementId: 'orders', targetRef: resourceRef,
  environment: 'production', bindingStatus: 'observed', observedRevision: 'catalog:42',
  nativeDetails: {collection: 'orders'}};
const content = {schema: 'cdeadmin.contract.asset.v1', schemaVersion: 1,
  moduleId: 'cdeadmin.contract', name: 'Orders contract', domain: 'sales',
  description: 'Governed order documents', contractVersion: '1.0.0', status: 'draft',
  elements: [element], bindings: [binding], qualityObligations: [{id: 'orders-complete',
    elementId: 'orders', severity: 'critical', qualityRef: {schema: 'cdeadmin.asset-ref.v1',
      projectId: 'p', assetId: 'orders-quality'}, importedDefinition: null,
    threshold: {ratio: 1}, description: 'All orders are complete'}],
  sla: [{id: 'freshness', elementId: 'orders', measure: 'freshness', target: 5,
    comparison: '<=', unit: 'minutes', window: {rolling: '15m'}, description: 'Freshness',
    extensions: {timezone: 'UTC'}}],
  team: [{id: 'owner', name: 'Product owner', principalRefs: [
    {schema: 'cdeadmin.external-ref.v1', id: 'group:sales'}],
  responsibilities: ['definition'], accessExpectations: [], support: {channel: '#sales'},
  extensions: {}}],
  roles: [{id: 'reader', name: 'Contract reader', principalRefs: [
    {schema: 'cdeadmin.external-ref.v1', id: 'group:analysts'}],
  responsibilities: [], accessExpectations: ['read'], support: {}, extensions: {}}],
  servers: [{id: 'mongo-production', providerId: 'mongodb', environment: 'production',
    interface: 'document', resourceRef, apiRef: null, nativeDetails: {replicaSet: 'rs0'}}],
  authoritativeDefinitions: [{id: 'orders-api', type: 'openapi',
    uri: 'https://example.invalid/orders.yaml', assetRef: null,
    description: 'Orders API', extensions: {}}], extensions: {owner: 'sales'}};
const complianceRun = {id: 'compliance-one', summary: {compliant: false, partial: true,
  dimensions: [{dimension: 'contract_structure', compliant: true, details: []},
    {dimension: 'schema', compliant: false, details: ['Provider schema changed']}]},
drift: [{bindingId: 'orders-production', state: 'provider_changed',
  observedRevision: 'catalog:43', differences: [{path: '/elements/orders'}]}]};

function value(overrides={}) {
  return {schema: 'cdeadmin.contract-session.v1', id: 'contract-one', content,
    state: 'ready', dirty: true, validation: {valid: true, details: []},
    selectedElementId: 'orders', selectedBindingId: 'orders-production',
    providerStatuses: [{providerId: 'mongodb', supportState: 'supported_native'}],
    metadataComparison: {comparisons: [{bindingId: 'orders-production',
      drift: {state: 'provider_changed'}}]}, complianceRuns: [complianceRun],
    drift: complianceRun.drift, versions: [content, {...content, contractVersion: '2.0.0',
      description: 'Revised contract'}], versionDiff: {identical: false,
      differences: [{path: '/description', state: 'changed',
        left: 'Governed order documents', right: 'Revised contract'}]},
    interoperability: {apiVersion: 'v3.1'}, activeTaskId: null, problems: [],
    history: [{at: '2026-09-11T14:00:00Z', action: 'edit'}], error: '', ...overrides};
}

function fakeService(session=value()) {
  const listeners = new Set();
  return {get: jest.fn(() => session), list: jest.fn(() => [session]),
    subscribe: jest.fn((listener) => { listeners.add(listener);
      return () => listeners.delete(listener); }), select: jest.fn(), validate: jest.fn(),
    compareVersions: jest.fn(), reportError: jest.fn(), listeners};
}

function mount(surface='contract_explorer', overrides={}, properties={}) {
  const session = value(overrides); const service = fakeService(session);
  const executeCommand = jest.fn().mockResolvedValue({apiVersion: 'v3.1'});
  const Component = withTheme(ContractWorkspace);
  render(<Component service={service} sessionId="contract-one" surface={surface}
    executeCommand={executeCommand} currentUser={{permissions: ['contract.view', 'contract.edit',
      'contract.activate', 'contract.compliance', 'contract.import_export']}} {...properties} />);
  return {session, service, executeCommand};
}

describe('ContractWorkspace', () => {
  beforeEach(() => {
    usePreferences.setState({data: [], version: Date.now(), isLoading: false, failed: false});
  });

  it('renders all seven surfaces and filters/selects native logical types', () => {
    const {service} = mount();
    expect(screen.getAllByRole('tab')).toHaveLength(7);
    expect(screen.getByLabelText('Data Contract elements')).toBeVisible();
    expect(screen.getByText('document')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Search contract elements'),
      {target: {value: 'Order documents'}});
    fireEvent.click(screen.getByRole('button', {name: 'Orders'}));
    expect(service.select).toHaveBeenCalledWith('contract-one', {elementId: 'orders'});
  });

  it('edits fundamentals, arbitrary logical schema objects, interfaces and definitions', () => {
    const {executeCommand} = mount('contract_editor');
    fireEvent.change(screen.getByLabelText('Contract name'),
      {target: {value: 'Orders vNext'}});
    fireEvent.click(screen.getByRole('button', {name: 'Save fundamentals'}));
    expect(executeCommand).toHaveBeenCalledWith('contract.create', {content:
      expect.objectContaining({name: 'Orders vNext', elements: [element]})}, expect.any(Object));
    fireEvent.change(screen.getByLabelText('logicalType'), {target: {value: 'time-series'}});
    fireEvent.change(screen.getByLabelText('constraints JSON'),
      {target: {value: '{"retention":"30d"}'}});
    fireEvent.click(screen.getByRole('button', {name: 'Save element'}));
    expect(executeCommand).toHaveBeenCalledWith('contract.create', {content:
      expect.objectContaining({elements: [expect.objectContaining({logicalType: 'time-series',
        constraints: {retention: '30d'}})]})}, expect.any(Object));
    const serverEditor = screen.getByRole('region', {name: 'Contract server binding editor'});
    for(const [label, input] of [['id', 'mongo-test'], ['providerId', 'mongodb'],
      ['environment', 'test'], ['interface', 'document'],
      ['resourceRef JSON', JSON.stringify(resourceRef)]]) {
      fireEvent.change(within(serverEditor).getByLabelText(label), {target: {value: input}});
    }
    fireEvent.click(screen.getByRole('button', {name: 'Save serving interface'}));
    expect(executeCommand).toHaveBeenCalledWith('contract.create', {content:
      expect.objectContaining({servers: expect.arrayContaining([
        expect.objectContaining({id: 'mongo-test', interface: 'document'})])})}, expect.any(Object));
    const definitionEditor = screen.getByRole('region', {name: 'Authoritative definition editor'});
    for(const [label, input] of [['id', 'schema-doc'], ['type', 'json-schema'],
      ['uri', 'https://example.invalid/schema.json']]) {
      fireEvent.change(within(definitionEditor).getByLabelText(label), {target: {value: input}});
    }
    fireEvent.click(screen.getByRole('button', {name: 'Save authoritative definition'}));
    expect(executeCommand).toHaveBeenCalledWith('contract.create', {content:
      expect.objectContaining({authoritativeDefinitions: expect.arrayContaining([
        expect.objectContaining({id: 'schema-doc', type: 'json-schema'})])})}, expect.any(Object));
  });

  it('reports malformed authored JSON inline without dispatching a mutation', () => {
    const {executeCommand} = mount('contract_editor'); const before = executeCommand.mock.calls.length;
    fireEvent.change(screen.getByLabelText('constraints JSON'), {target: {value: '[1,2]'}});
    fireEvent.click(screen.getByRole('button', {name: 'Save element'}));
    expect(screen.getByRole('alert')).toHaveTextContent('Element constraints must be a JSON object');
    expect(executeCommand).toHaveBeenCalledTimes(before);
  });

  it('binds and re-compares an exact provider resource without altering authored schema', async () => {
    const resources = [{id: 'orders-live', label: 'Orders collection', type: 'collection',
      path: 'mongodb/sales', reference: resourceRef}];
    const {executeCommand, service} = mount('schema_resource_binding', {}, {resources});
    fireEvent.click(screen.getByRole('button', {name: 'Select provider resource'}));
    fireEvent.doubleClick(screen.getByRole('option', {name: /Orders collection/}));
    await waitFor(() => expect(screen.queryByRole('dialog',
      {name: 'Select provider resource'})).not.toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('id'), {target: {value: 'orders-test'}});
    fireEvent.change(screen.getByLabelText('elementId'), {target: {value: 'orders'}});
    fireEvent.click(screen.getByRole('button', {name: 'Save resource binding'}));
    expect(executeCommand).toHaveBeenCalledWith('contract.bind_resource', {binding:
      expect.objectContaining({id: 'orders-test', targetRef: resourceRef,
        nativeDetails: {}})}, expect.any(Object));
    fireEvent.click(screen.getByRole('button', {name: 'Compare live metadata'}));
    expect(executeCommand).toHaveBeenCalledWith('contract.sync_metadata',
      {bindingId: 'orders-production'}, expect.any(Object));
    expect(service.select).not.toHaveBeenCalled();
  });

  it('authors independent quality obligations and service levels', () => {
    const {executeCommand} = mount('quality_sla');
    fireEvent.click(screen.getByRole('button', {name: 'orders-complete'}));
    fireEvent.change(screen.getAllByLabelText('description')[0],
      {target: {value: 'Every order is complete'}});
    fireEvent.click(screen.getByRole('button', {name: 'Save quality obligation'}));
    expect(executeCommand).toHaveBeenCalledWith('contract.create', {content:
      expect.objectContaining({qualityObligations: [expect.objectContaining({
        description: 'Every order is complete', qualityRef: expect.any(Object)})]})},
    expect.any(Object));
    fireEvent.click(screen.getByRole('button', {name: 'freshness'}));
    fireEvent.change(screen.getByLabelText('target'), {target: {value: '3'}});
    fireEvent.click(screen.getByRole('button', {name: 'Save service level'}));
    expect(executeCommand).toHaveBeenCalledWith('contract.create', {content:
      expect.objectContaining({sla: [expect.objectContaining({target: 3,
        measure: 'freshness'})]})}, expect.any(Object));
  });

  it('keeps team/support and access roles as separately authored sections', () => {
    const {executeCommand} = mount('team_roles_support');
    fireEvent.click(screen.getByRole('button', {name: 'Product owner'}));
    fireEvent.change(screen.getByLabelText('support JSON'),
      {target: {value: '{"channel":"#orders"}'}});
    fireEvent.click(screen.getByRole('button', {name: 'Save role'}));
    expect(executeCommand).toHaveBeenCalledWith('contract.create', {content:
      expect.objectContaining({team: [expect.objectContaining({
        support: {channel: '#orders'}})]})}, expect.any(Object));
    const original = console.error.getMockImplementation(); console.error.mockImplementation(() => {});
    fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Ownership section'}));
    fireEvent.click(screen.getByRole('option', {name: 'Access roles'}));
    console.error.mockImplementation(original); console.error.mockClear();
    expect(screen.getByRole('button', {name: 'Contract reader'})).toBeVisible();
  });

  it('distinguishes valid authored structure from failed live compliance and drift', () => {
    const {executeCommand} = mount('compliance');
    expect(screen.getByText('Structure: valid')).toBeVisible();
    expect(screen.getByLabelText('Data Contract compliance dimensions')).toBeVisible();
    expect(screen.getByLabelText('Data Contract provider drift')).toBeVisible();
    expect(screen.getByText('provider_changed')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Run live compliance'}));
    expect(executeCommand).toHaveBeenCalledWith('contract.compliance.run',
      {qualityResults: []}, expect.any(Object));
  });

  it('creates immutable versions and compares chosen revisions', () => {
    const {executeCommand, service} = mount('version_diff');
    expect(screen.getByLabelText('Data Contract version differences')).toBeVisible();
    expect(screen.getByText('/description')).toBeVisible();
    fireEvent.change(screen.getByLabelText('New contract version'),
      {target: {value: '3.0.0'}});
    fireEvent.click(screen.getByRole('button', {name: 'Create immutable version'}));
    expect(executeCommand).toHaveBeenCalledWith('contract.version.create',
      {version: '3.0.0'}, expect.any(Object));
    fireEvent.click(screen.getByRole('button', {name: 'Compare versions'}));
    expect(service.compareVersions).toHaveBeenCalledWith('contract-one', '1.0.0', '2.0.0');
  });

  it('imports and exports version-declared ODCS without using the canonical preview as source', async () => {
    const onExport = jest.fn(); const {executeCommand} = mount('contract_editor', {}, {onExport});
    const source = {apiVersion: 'v3.1', kind: 'DataContract', name: 'Events', version: '7'};
    fireEvent.change(screen.getByLabelText('ODCS source JSON'),
      {target: {value: JSON.stringify(source)}});
    expect(screen.getByLabelText('Canonical contract preview')).toHaveAttribute('readonly');
    fireEvent.click(screen.getByRole('button', {name: 'Import ODCS source'}));
    expect(executeCommand).toHaveBeenCalledWith('contract.import.odcs', {source},
      expect.any(Object));
    fireEvent.change(screen.getByLabelText('ODCS export apiVersion'),
      {target: {value: 'v3.2'}});
    fireEvent.click(screen.getByRole('button', {name: 'Export ODCS metadata'}));
    await waitFor(() => expect(onExport).toHaveBeenCalledWith({apiVersion: 'v3.1'}));
    expect(executeCommand).toHaveBeenCalledWith('contract.export.odcs',
      {apiVersion: 'v3.2'}, expect.any(Object));
  });

  it('preserves authored state visibly and disables live work while stale', () => {
    const session = value({state: 'stale', error: 'Observed metadata is stale'});
    const service = fakeService(session); const executeCommand = jest.fn();
    const Component = withTheme(ContractWorkspace);
    render(<Component service={service} sessionId="contract-one" surface="compliance"
      executeCommand={executeCommand}
      currentUser={{permissions: ['contract.compliance']}} />);
    expect(screen.getAllByRole('alert').map((node) => node.textContent).join(' '))
      .toContain('Observed metadata is stale');
    expect(screen.getByText(/Authored state remains visible/)).toBeVisible();
    expect(screen.getByRole('button', {name: 'Run live compliance'})).toBeDisabled();
  });

  it('records a provider failure without silently discarding the contract', async () => {
    const failure = new Error('provider_unavailable: MongoDB is offline');
    const service = fakeService(); const executeCommand = jest.fn().mockRejectedValue(failure);
    const Component = withTheme(ContractWorkspace);
    render(<Component service={service} sessionId="contract-one" surface="compliance"
      executeCommand={executeCommand}
      currentUser={{permissions: ['contract.compliance']}} />);
    fireEvent.click(screen.getByRole('button', {name: 'Run live compliance'}));
    await waitFor(() => expect(service.reportError).toHaveBeenCalledWith(
      'contract-one', failure, 'provider_unavailable'));
    expect(service.get().content).toBe(content);
  });

  it.each([
    ['permission_denied', 'Permission denied'],
    ['validation_error', 'Contract validation failed'],
    ['runtime_failure', 'last runtime operation failed'],
    ['partial', 'Data Contract Manager is partial'],
    ['disconnected', 'Data Contract Manager is disconnected'],
    ['read_only', 'Data Contract Manager is read only'],
  ])('renders the %s state explicitly', (state, message) => {
    mount('contract_explorer', {state, error: ''});
    expect(screen.getAllByText(new RegExp(message, 'i'))[0]).toBeVisible();
  });

  it.each([
    ['loading', 'Loading'],
    ['background_task_active', 'Data Contract background task'],
  ])('renders a bounded %s state while retaining the workbench', (state, label) => {
    mount('contract_explorer', {state});
    expect(screen.getByRole('status', {name: label})).toBeVisible();
    expect(screen.getByRole('tablist', {name: 'Data Contract Manager surfaces'})).toBeVisible();
  });

  it('renders the not-configured state with a valid next action instead of a blank panel', () => {
    const emptyContent = {...content, name: '', domain: '', description: '',
      elements: [], bindings: [], qualityObligations: [], sla: []};
    mount('contract_explorer', {state: 'empty', content: emptyContent,
      providerStatuses: [], complianceRuns: [], dirty: false});
    expect(screen.getByText('Create logical contract objects or properties.')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Open Contract Editor'}));
    expect(screen.getByRole('region', {name: 'Contract fundamentals'})).toBeVisible();
  });

  it('keeps provider warnings and limitations visible rather than silently truncating them', () => {
    mount('contract_explorer', {state: 'partial', providerStatuses: [{providerId: 'mongodb',
      supportState: 'partial', warnings: ['Schema discovery was bounded.'],
      limitations: ['Views are unavailable.']}]});
    expect(screen.getByText(/Schema discovery was bounded.*Views are unavailable/)).toBeVisible();
  });

  it('disables unauthorized authoring, activation, interoperability and live commands', () => {
    mount('contract_editor', {}, {currentUser: {permissions: ['contract.view']}});
    for(const label of ['Save fundamentals', 'Apply lifecycle status', 'Save element',
      'Save serving interface', 'Save authoritative definition', 'Import ODCS source',
      'Export ODCS metadata']) {
      expect(screen.getByRole('button', {name: label})).toBeDisabled();
    }
  });

  it.each(['cdeadmin_standard', 'compact_expert', 'low_vision', 'high_contrast_light'])(
    'remains operable under the %s presentation profile', (profile) => {
      usePreferences.setState({data: [{id: 1, module: 'misc', name: 'accessibility_profile',
        value: profile}], version: Date.now(), isLoading: false, failed: false});
      const {service} = mount();
      fireEvent.click(screen.getByRole('button', {name: 'Orders'}));
      expect(service.select).toHaveBeenCalledWith('contract-one', {elementId: 'orders'});
    });

  it('produces object-specific inspector facts without exposing credentials', () => {
    expect(contractInspector(value())).toEqual({
      'Selected element': 'Orders · document',
      'Resource binding': resourceRef.canonical, References: 'orders-api',
      'Quality obligations': 'orders-complete', History: '1 auditable session events'});
  });
});

describe('ContractNavigator', () => {
  it('provides a real tree for contracts and every task-oriented surface', () => {
    const service = fakeService(); const onOpen = jest.fn();
    const Navigator = withTheme(ContractNavigator); render(<Navigator service={service}
      onOpen={onOpen} />);
    const asset = screen.getByRole('treeitem', {name: /Orders contract/});
    fireEvent.click(within(asset).getByRole('button', {name: /Expand Orders contract/}));
    for(const label of ['Fundamentals', 'Schema', 'Quality', 'SLA', 'Servers', 'Team',
      'Roles', 'Definitions', 'Resource Bindings', 'Compliance', 'Versions']) {
      expect(screen.getByRole('treeitem', {name: label})).toBeVisible();
    }
    fireEvent.doubleClick(screen.getByRole('treeitem', {name: 'Compliance'}));
    expect(onOpen).toHaveBeenCalledWith('contract-one', 'compliance');
  });
});
