/////////////////////////////////////////////////////////////
// ETL surfaces, state, accessibility and interaction gates.
/////////////////////////////////////////////////////////////

import {fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {ETLNavigator, ETLWorkspace, etlInspector} from 'sources/cdeadmin_ui/modules/etl';
import usePreferences from '../../../pgadmin/preferences/static/js/store';

const resource = (provider, name) => ({schema: 'cdeadmin.resource-ref.v1', provider,
  canonical: `cde-resource://${provider}/local/database/demo/${name}`});
const field = {schema: 'cdeadmin.etl-schema-field.v1', id: 'id', name: 'ID',
  nativeType: 'INTEGER', semanticType: 'integer', nullable: false, precision: null,
  scale: null, timezone: null, encoding: null, path: null, nativeDetails: {}};
const port = (id, direction) => ({schema: 'cdeadmin.etl-port.v1', id, name: id, direction,
  mode: 'batch', schemaState: 'known', schemaRef: null, fields: [field], cardinality: null,
  ordering: [], partitioning: {}, watermark: {}, nativeDetails: {}});
const node = (id, kind, provider, refName) => ({schema: 'cdeadmin.etl-node.v1', id, name: id,
  kind, config: {}, resourceRef: resource(provider, refName), ports: kind === 'source' ?
    [port('out', 'output')] : [port('in', 'input')], capabilityRequirements: [],
  executionPreference: kind === 'source' ? 'source_pushdown' : 'target_pushdown',
  errorRoute: null, checkpointEnabled: kind === 'source', nativeDetails: {}});
const mapping = {schema: 'cdeadmin.etl-schema-mapping.v1', id: 'id', source: 'id', target: 'id',
  sourceNativeType: 'INTEGER', semanticType: 'integer', targetNativeType: 'BIGINT',
  conversion: 'widen', nullPolicy: 'preserve', nullable: false, precision: null, scale: null,
  timezone: null, encoding: null, lossy: false, lossAcknowledged: false, nativeDetails: {}};
const edge = {schema: 'cdeadmin.etl-edge.v1', id: 'flow', fromNodeId: 'source', fromPort: 'out',
  toNodeId: 'sink', toPort: 'in', mappingPolicy: 'explicit', mappings: [mapping],
  deliveryGuarantee: 'at_least_once', partitioning: {}, ordering: {}, nativeDetails: {}};
const deployment = {schema: 'cdeadmin.etl-deployment.v1', id: 'dev', name: 'Development',
  environment: 'development', bindings: [], parameterBindings: {}, resourceLimits: {},
  nativeDetails: {}};
const schedule = {schema: 'cdeadmin.etl-schedule.v1', id: 'nightly', name: 'Nightly', enabled: true,
  trigger: 'cron', expression: '0 1 * * *', timezone: 'UTC', deploymentId: 'dev',
  dependencyRefs: [], parameters: {}, nativeDetails: {}};
const content = {schema: 'cdeadmin.etl.asset.v1', schemaVersion: 1, moduleId: 'cdeadmin.etl',
  name: 'Orders pipeline', description: 'Cross-engine movement', mode: 'batch', parameters: [],
  nodes: [node('source', 'source', 'firebird', 'SOURCE'), node('sink', 'sink', 'mongodb', 'TARGET')],
  edges: [edge], deployments: [deployment], schedules: [schedule], tests: [],
  visualLayout: {}, extensions: {}};
const preview = {id: 'preview-one', limit: 2, sideEffects: false, stages: [
  {nodeId: 'source', state: 'succeeded', rows: 2, bytes: 20, output: [{id: 1}, {id: 2}],
    written: false}, {nodeId: 'sink', state: 'proposed', rows: 2, bytes: 20,
    output: [{id: 1}, {id: 2}], written: false}]};
const run = {id: 'run-one', deploymentId: 'dev', environment: 'development', parameters: {},
  resumedFrom: null, state: 'failed', stages: [{nodeId: 'source', state: 'succeeded', rows: 2,
    bytes: 20, throughput: 2, deliveryGuarantee: 'exactly_once'}],
  checkpoint: {nodeId: 'source', position: {offset: 2}}, deliveryGuarantee: 'exactly_once',
  error: 'target disconnected'};

function value(overrides={}) {
  return {schema: 'cdeadmin.etl-session.v1', id: 'etl-one', content, state: 'ready', dirty: true,
    selectedNodeId: 'source', selectedEdgeId: 'flow', validation: {valid: true, deployable: true,
      details: [], warnings: [], order: ['source', 'sink']}, schemaPropagation: {compatible: true,
      records: [{id: 'flow', fromNodeId: 'source', fromPort: 'out', toNodeId: 'sink', toPort: 'in',
        sourceSchemaState: 'known', targetSchemaState: 'known', mappingCount: 1,
        state: 'known', problems: []}]}, providerStatuses: [{providerId: 'firebird',
      supportState: 'supported_native', warnings: [], limitations: []}], previews: [preview],
    deploymentResults: [{deploymentId: 'dev', state: 'validated'}], runs: [run],
    activeTaskId: null, problems: [], history: [{action: 'edit'}], error: '', ...overrides};
}

function fakeService(session=value()) {
  const listeners = new Set();
  return {get: jest.fn(() => session), list: jest.fn(() => [session]),
    subscribe: jest.fn((listener) => { listeners.add(listener); return () => listeners.delete(listener); }),
    select: jest.fn(), reportError: jest.fn(), listeners};
}

function mount(surface='pipeline_designer', overrides={}, properties={}) {
  const session = value(overrides); const service = fakeService(session);
  const executeCommand = jest.fn().mockResolvedValue({id: 'task-one'});
  const Component = withTheme(ETLWorkspace);
  render(<Component service={service} sessionId="etl-one" surface={surface}
    executeCommand={executeCommand} currentUser={{permissions: ['etl.view', 'etl.edit',
      'etl.preview', 'etl.view_samples', 'etl.execute', 'etl.deploy', 'etl.admin']}}
    {...properties} />);
  return {session, service, executeCommand};
}

describe('ETLWorkspace', () => {
  beforeEach(() => {
    usePreferences.setState({data: [], version: Date.now(), isLoading: false, failed: false});
  });

  it('renders all seven surfaces and an accessible graph/table route', () => {
    const {service} = mount(); expect(screen.getAllByRole('tab')).toHaveLength(7);
    expect(screen.getByRole('img', {name: /ETL pipeline graph/})).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'source, source, firebird'}));
    expect(service.select).toHaveBeenCalledWith('etl-one', {nodeId: 'source'});
    fireEvent.click(screen.getByRole('button', {name: 'Accessible table'}));
    expect(screen.getByLabelText('ETL pipeline graph table')).toBeVisible();
  });

  it('adds all node families through the registered command and keyboard alternative', () => {
    const {executeCommand} = mount(); const original = console.error.getMockImplementation();
    console.error.mockImplementation(() => {});
    fireEvent.mouseDown(screen.getByRole('combobox',
      {name: 'Node family'})); fireEvent.click(screen.getByRole('option', {name: 'filter'}));
    console.error.mockImplementation(original); console.error.mockClear();
    fireEvent.change(screen.getByLabelText('Node name'), {target: {value: 'Keep active'}});
    fireEvent.click(screen.getByRole('button', {name: 'Add node'}));
    expect(executeCommand).toHaveBeenCalledWith('etl.node.add', {node: expect.objectContaining({
      kind: 'filter', name: 'Keep active', ports: expect.arrayContaining([
        expect.objectContaining({direction: 'input'}),
        expect.objectContaining({direction: 'output'})])})}, expect.any(Object));
  });

  it('accepts only semantic ETL drag payloads and records layout separately', async () => {
    const {executeCommand} = mount(); const target = screen.getByLabelText(
      'Pipeline designer semantic drop target'
    ); const data = {'application/x-cdeadmin-etl-node': JSON.stringify({kind: 'aggregate',
      label: 'aggregate'})}; const dataTransfer = {types: Object.keys(data), effectAllowed: '',
      dropEffect: '', setData: (type, payload) => { data[type] = payload; },
      getData: (type) => data[type]};
    fireEvent.drop(target, {dataTransfer, clientX: 200, clientY: 120});
    await waitFor(() => expect(executeCommand).toHaveBeenCalledWith('etl.pipeline.create', {
      content: expect.objectContaining({nodes: expect.arrayContaining([
        expect.objectContaining({kind: 'aggregate'})]), visualLayout: expect.objectContaining({
        'aggregate-1': expect.objectContaining({x: expect.any(Number), y: expect.any(Number)}),
      })}),
    }, expect.any(Object)));
    expect(executeCommand).toHaveBeenCalledTimes(1);
  });

  it('communicates valid and invalid semantic drop targets', () => {
    mount(); const target = screen.getByLabelText('Pipeline designer semantic drop target');
    fireEvent.dragEnter(target, {dataTransfer: {types: ['text/plain']}});
    expect(screen.getByRole('status')).toHaveTextContent('cannot be dropped');
    fireEvent.dragEnter(target, {dataTransfer: {types: ['application/x-cdeadmin-etl-node']}});
    expect(screen.getByRole('status')).toHaveTextContent('Drop to add');
    fireEvent.dragLeave(target); expect(target).toHaveAttribute('data-drop-state', 'idle');
  });

  it('authors every typed-edge policy field through one command', () => {
    const {executeCommand} = mount(); const original = console.error.getMockImplementation();
    console.error.mockImplementation(() => {});
    fireEvent.change(screen.getByLabelText('Edge ID'), {target: {value: 'second-flow'}});
    fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Output port'}));
    fireEvent.click(screen.getByRole('option', {name: /source.out/}));
    fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Input port'}));
    fireEvent.click(screen.getByRole('option', {name: /sink.in/}));
    console.error.mockImplementation(original); console.error.mockClear();
    fireEvent.change(screen.getByLabelText('Mapping policy'), {target: {value: 'by_name'}});
    fireEvent.change(screen.getByLabelText('Edge partitioning JSON'),
      {target: {value: '{"keys":["tenant"]}'}});
    fireEvent.change(screen.getByLabelText('Edge ordering JSON'),
      {target: {value: '{"fields":["timestamp"]}'}});
    fireEvent.change(screen.getByLabelText('Edge native details JSON'),
      {target: {value: '{"providerHint":"bulk"}'}});
    fireEvent.click(screen.getByRole('button', {name: 'Connect typed ports'}));
    expect(executeCommand).toHaveBeenCalledWith('etl.edge.connect', {edge:
      expect.objectContaining({id: 'second-flow', mappingPolicy: 'by_name',
        partitioning: {keys: ['tenant']}, ordering: {fields: ['timestamp']},
        nativeDetails: {providerHint: 'bulk'}})}, expect.any(Object));
  });

  it('edits pipeline identity, mode and the complete selected-node contract', () => {
    const {executeCommand} = mount();
    fireEvent.change(screen.getByLabelText('Pipeline name'), {target: {value: 'Revised orders'}});
    fireEvent.click(screen.getByRole('button', {name: 'Save pipeline definition'}));
    expect(executeCommand).toHaveBeenCalledWith('etl.pipeline.create', {content:
      expect.objectContaining({name: 'Revised orders', mode: 'batch'})}, expect.any(Object));
    fireEvent.change(screen.getByLabelText('Selected node name'), {target: {value: 'Read orders'}});
    fireEvent.change(screen.getByLabelText('Node configuration JSON'),
      {target: {value: '{"predicate":"ACTIVE = 1"}'}});
    fireEvent.click(screen.getByLabelText('Checkpoint enabled'));
    fireEvent.click(screen.getByRole('button', {name: 'Save selected node'}));
    expect(executeCommand).toHaveBeenCalledWith('etl.pipeline.create', {content:
      expect.objectContaining({nodes: expect.arrayContaining([expect.objectContaining({
        id: 'source', name: 'Read orders', config: {predicate: 'ACTIVE = 1'},
        checkpointEnabled: false})])})}, expect.any(Object));
  });

  it('authors deterministic pipeline tests as project definition data', () => {
    const {executeCommand} = mount();
    fireEvent.change(screen.getByLabelText('Pipeline test ID'), {target: {value: 'row-count'}});
    fireEvent.change(screen.getByLabelText('Pipeline test name'), {target: {value: 'Rows exist'}});
    fireEvent.change(screen.getByLabelText('Pipeline test definition JSON'),
      {target: {value: '{"minimum":1}'}});
    fireEvent.click(screen.getByRole('button', {name: 'Save pipeline test'}));
    expect(executeCommand).toHaveBeenCalledWith('etl.pipeline.create', {content:
      expect.objectContaining({tests: [expect.objectContaining({id: 'row-count',
        definition: {minimum: 1}})]})}, expect.any(Object));
  });

  it('uses resource and credential reference pickers without exposing raw secrets', async () => {
    const resources = [{id: 'orders', label: 'Orders source', type: 'table', reference:
      resource('firebird', 'ORDERS')}];
    const credentials = [{id: 'vault-etl', label: 'ETL vault entry', type: 'credential',
      reference: {schema: 'cdeadmin.credential-ref.v1', scheme: 'keyring', id: 'etl-dev'}}];
    const {executeCommand} = mount('pipeline_designer', {}, {resources,
      credentialReferences: credentials});
    fireEvent.click(screen.getByRole('button', {name: 'Select provider resource'}));
    fireEvent.doubleClick(screen.getByRole('option', {name: /Orders source/}));
    await waitFor(() => expect(screen.queryByRole('dialog', {
      name: 'Select provider resource'})).not.toBeInTheDocument());
    expect(screen.getByLabelText('ResourceRef JSON (provider nodes)').value)
      .toContain('cde-resource://firebird');
    fireEvent.change(screen.getByLabelText('Parameter ID'), {target: {value: 'password'}});
    fireEvent.click(screen.getByLabelText('Secret parameter'));
    fireEvent.click(screen.getByRole('button', {name: 'Select credential reference'}));
    fireEvent.doubleClick(screen.getByRole('option', {name: /ETL vault entry/}));
    await waitFor(() => expect(screen.queryByRole('dialog', {
      name: 'Select credential reference'})).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', {name: 'Save parameter'}));
    expect(executeCommand).toHaveBeenCalledWith('etl.pipeline.create', {content:
      expect.objectContaining({parameters: [expect.objectContaining({secret: true,
        default: null, credentialRef: expect.objectContaining({id: 'etl-dev'})})]})},
    expect.any(Object));
  });

  it('edits explicit native/semantic mappings with loss acknowledgment', () => {
    const {executeCommand} = mount('mapping_editor');
    fireEvent.change(screen.getByLabelText('Source field ID/path'), {target: {value: 'amount'}});
    fireEvent.change(screen.getByLabelText('Target field ID/path'), {target: {value: 'total'}});
    fireEvent.change(screen.getByLabelText('Conversion'), {target: {value: 'decimal_to_int'}});
    fireEvent.change(screen.getByLabelText('Null policy'), {target: {value: 'reject'}});
    fireEvent.change(screen.getByLabelText('Precision'), {target: {value: '18'}});
    fireEvent.change(screen.getByLabelText('Scale'), {target: {value: '2'}});
    fireEvent.change(screen.getByLabelText('Timezone'), {target: {value: 'UTC'}});
    fireEvent.change(screen.getByLabelText('Encoding'), {target: {value: 'UTF8'}});
    fireEvent.click(screen.getByLabelText('Lossy conversion'));
    expect(screen.getByRole('button', {name: 'Save mapping'})).toBeDisabled();
    fireEvent.click(screen.getByLabelText('Loss explicitly acknowledged'));
    fireEvent.click(screen.getByRole('button', {name: 'Save mapping'}));
    expect(executeCommand).toHaveBeenCalledWith('etl.mapping.edit', {edgeId: 'flow',
      mappings: expect.arrayContaining([expect.objectContaining({source: 'amount', target: 'total',
        nullPolicy: 'reject', precision: 18, scale: 2, timezone: 'UTC', encoding: 'UTF8',
        lossy: true, lossAcknowledged: true})])}, expect.any(Object));
  });

  it('shows schema propagation and incompatibilities as a bounded table', () => {
    mount('schema_propagation');
    expect(screen.getByText('Schemas propagate without known incompatibility.')).toBeVisible();
    expect(screen.getByLabelText('ETL propagated schemas')).toBeVisible();
    expect(screen.getByText('source.out → sink.in')).toBeVisible();
  });

  it('runs bounded preview with explicit stream scope and renders proposed sink output', () => {
    const {executeCommand} = mount('preview');
    expect(screen.getByLabelText('ETL preview stages')).toBeVisible();
    expect(screen.getByLabelText('ETL preview data')).toBeVisible();
    expect(screen.getAllByText('false').length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText('Record limit'), {target: {value: '25'}});
    fireEvent.change(screen.getByLabelText('Stream time/partition scope JSON'),
      {target: {value: '{"partition":0}'}});
    fireEvent.click(screen.getByRole('button', {name: 'Run bounded preview'}));
    expect(executeCommand).toHaveBeenCalledWith('etl.preview.run', {limit: 25,
      streamScope: {partition: 0}}, expect.any(Object));
  });

  it('authors deployment bindings separately from provider deployment validation', () => {
    const {executeCommand} = mount('deployment');
    fireEvent.change(screen.getByLabelText('Deployment name'), {target: {value: 'Dev revised'}});
    fireEvent.click(screen.getByRole('button', {name: 'Save authored deployment'}));
    expect(executeCommand).toHaveBeenCalledWith('etl.pipeline.create', {content:
      expect.objectContaining({deployments: [expect.objectContaining({name: 'Dev revised'})]})},
    expect.any(Object));
    fireEvent.click(screen.getByRole('button', {name: 'Validate provider deployment'}));
    expect(executeCommand).toHaveBeenCalledWith('etl.pipeline.deploy', {deploymentId: 'dev'},
      expect.any(Object));
  });

  it('starts, cancels and resumes runs using distinct commands', () => {
    const {executeCommand} = mount('run_monitor', {activeTaskId: 'task-active'});
    fireEvent.click(screen.getByRole('button', {name: 'Start run'}));
    expect(executeCommand).toHaveBeenCalledWith('etl.run.start', {deploymentId: 'dev',
      parameters: {}}, expect.any(Object));
    fireEvent.click(screen.getByRole('button', {name: 'Cancel active task'}));
    expect(executeCommand).toHaveBeenCalledWith('etl.run.cancel', {taskId: 'task-active'},
      expect.any(Object));
    fireEvent.click(screen.getByRole('button', {name: 'Resume from checkpoint'}));
    expect(executeCommand).toHaveBeenCalledWith('etl.run.resume', {runId: 'run-one'},
      expect.any(Object));
  });

  it('authors schedules with environment deployment identity and dependency refs', () => {
    const {executeCommand} = mount('schedule');
    fireEvent.change(screen.getByLabelText('id'), {target: {value: 'hourly'}});
    fireEvent.change(screen.getByLabelText('name'), {target: {value: 'Hourly'}});
    fireEvent.change(screen.getByLabelText('expression'), {target: {value: '0 * * * *'}});
    fireEvent.click(screen.getByRole('button', {name: 'Save schedule'}));
    expect(executeCommand).toHaveBeenCalledWith('etl.pipeline.create', {content:
      expect.objectContaining({schedules: expect.arrayContaining([
        expect.objectContaining({id: 'hourly', deploymentId: 'dev'})])})}, expect.any(Object));
  });

  it('preserves authored state and reports provider failures', async () => {
    const failure = new Error('provider_unavailable: source offline');
    const service = fakeService(); const executeCommand = jest.fn().mockRejectedValue(failure);
    const Component = withTheme(ETLWorkspace);
    render(<Component service={service} sessionId="etl-one" surface="preview"
      executeCommand={executeCommand} currentUser={{permissions: ['etl.preview',
        'etl.view_samples']}} />);
    fireEvent.click(screen.getByRole('button', {name: 'Run bounded preview'}));
    await waitFor(() => expect(service.reportError).toHaveBeenCalledWith(
      'etl-one', failure, 'provider_unavailable'));
    expect(service.get().content).toBe(content);
  });

  it.each([['permission_denied', 'Permission denied'],
    ['validation_error', 'Pipeline validation failed'], ['runtime_failure', 'runtime_failure'],
    ['partial', 'ETL Designer is partial'], ['disconnected', 'ETL Designer is disconnected'],
    ['read_only', 'ETL Designer is read only']])('renders %s explicitly', (state, message) => {
    mount('pipeline_designer', {state, error: state === 'runtime_failure' ? 'runtime_failure' : ''});
    expect(screen.getAllByText(new RegExp(message, 'i'))[0]).toBeVisible();
  });

  it.each([['loading', 'Loading'], ['background_task_active', 'ETL background task']])(
    'renders %s while retaining navigation', (state, label) => {
      mount('pipeline_designer', {state});
      expect(screen.getByRole('status', {name: label})).toBeVisible();
      expect(screen.getByRole('tablist', {name: 'ETL Designer surfaces'})).toBeVisible();
    });

  it('renders the empty state with keyboard-operable creation controls', () => {
    mount('pipeline_designer', {state: 'empty', content: {...content, name: '', nodes: [], edges: [],
      deployments: [], schedules: []}, providerStatuses: [], previews: [], runs: []});
    expect(screen.getByText(/Add a source, transformations and a target/)).toBeVisible();
    expect(screen.getByRole('button', {name: 'Add node'})).toBeEnabled();
  });

  it('keeps provider warnings and limits visible', () => {
    mount('pipeline_designer', {state: 'partial', providerStatuses: [{providerId: 'firebird',
      supportState: 'partial', warnings: ['Streaming is unavailable.'],
      limitations: ['Only batch sources are supported.']}]});
    expect(screen.getByText(/Streaming is unavailable.*Only batch sources/)).toBeVisible();
  });

  it('disables authoring, preview, deployment and execution without exact permissions', () => {
    mount('pipeline_designer', {}, {currentUser: {permissions: ['etl.view']}});
    expect(screen.getByRole('button', {name: 'Add node'})).toBeDisabled();
    expect(screen.getByRole('button', {name: 'Preview'})).toBeDisabled();
    expect(screen.getByRole('button', {name: 'Deploy'})).toBeDisabled();
  });

  it.each(['cdeadmin_standard', 'compact_expert', 'low_vision', 'high_contrast_light'])(
    'remains operable under %s presentation', (profile) => {
      usePreferences.setState({data: [{id: 1, module: 'misc', name: 'accessibility_profile',
        value: profile}], version: Date.now(), isLoading: false, failed: false});
      const {service} = mount(); fireEvent.click(screen.getByRole('button', {
        name: 'source, source, firebird'}));
      expect(service.select).toHaveBeenCalledWith('etl-one', {nodeId: 'source'});
    });

  it('provides node configuration, port, parameter, error and provider inspector facts', () => {
    expect(etlInspector(value())).toEqual({'Node configuration': 'source · source',
      'Ports & schema': '1 typed ports', Parameters: '0 pipeline parameters',
      'Error policy': 'No node selected',
      'Provider limits': 'Unknown until provider evidence exists'});
  });
});

describe('ETLNavigator', () => {
  it('provides a real tree with every independently openable surface', () => {
    const service = fakeService(); const onOpen = jest.fn(); const Navigator = withTheme(ETLNavigator);
    render(<Navigator service={service} onOpen={onOpen} />);
    const item = screen.getByRole('treeitem', {name: /Orders pipeline/});
    fireEvent.click(within(item).getByRole('button', {name: /Expand Orders pipeline/}));
    for(const label of ['Pipeline Designer', 'Mapping Editor', 'Schema Propagation', 'Preview',
      'Deployment', 'Run Monitor', 'Schedule']) expect(screen.getByRole('treeitem',
      {name: label})).toBeVisible();
    fireEvent.doubleClick(screen.getByRole('treeitem', {name: 'Run Monitor'}));
    expect(onOpen).toHaveBeenCalledWith('etl-one', 'run_monitor');
  });
});
