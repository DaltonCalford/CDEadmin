/////////////////////////////////////////////////////////////
// CDC surfaces, state, accessibility and interaction gates.
/////////////////////////////////////////////////////////////

import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {createCDCContent} from 'sources/cdeadmin_ui/modules/cdc/contracts';
import {CDCNavigator, CDCWorkspace,
  cdcInspector} from 'sources/cdeadmin_ui/modules/cdc/CDCWorkspace';
import usePreferences from '../../../pgadmin/preferences/static/js/store';
import {definition, event} from './CDCTestUtils';

const content = createCDCContent(definition({schemaEvolutionPolicy: {
  defaultAction: 'pause_and_review', nativeDetails: {}, changes: [{id: 'change-1',
    classification: 'additive_compatible', action: 'pause_and_review',
    description: 'new field', detectedSchemaVersion: '2', mapping: {}, nativeDetails: {}}]}}));
const run = {id: 'run-1', state: 'streaming', provisionTaskId: 'task-1',
  snapshotTaskId: 'task-2', streamTaskId: 'task-3', snapshotCheckpoint: {cursor: 's1'},
  streamCheckpoint: {cursor: 'c2'}, lag: 1, throughput: 20, startedAt: 'now',
  finishedAt: null, errors: []};

function value(overrides={}) {
  return {schema: 'cdeadmin.cdc-session.v1', id: 'cdc-one', content, state: 'ready', dirty: true,
    selection: {surface: 'cdc_designer', stage: 'source'}, validation: {valid: true,
      details: [], warnings: []}, providerStatuses: [{providerId: 'mongodb',
      supportState: 'supported_native', warnings: [], limitations: []}], runs: [run],
    checkpoints: {snapshot: {cursor: 's1'}, stream: {cursor: 'c2'}}, events: [event()],
    replayReview: null, schemaChanges: content.schemaEvolutionPolicy.changes,
    activeTaskId: null, problems: [], history: [], error: '', ...overrides};
}

function fakeService(session=value()) {
  const listeners = new Set();
  return {get: jest.fn(() => session), list: jest.fn(() => [session]),
    subscribe: jest.fn((listener) => { listeners.add(listener); return () => listeners.delete(listener); }),
    select: jest.fn(), inspectEvents: jest.fn().mockResolvedValue(session),
    validateSchemaChange: jest.fn().mockResolvedValue(session), listeners};
}

const permissions = ['cdc.view', 'cdc.edit', 'cdc.execute', 'cdc.replay',
  'cdc.view_payloads', 'cdc.admin'];

function mount(surface='cdc_designer', overrides={}, properties={}) {
  const session = value(overrides); const service = fakeService(session);
  const executeCommand = jest.fn().mockResolvedValue({id: 'task-one'});
  const Component = withTheme(CDCWorkspace);
  render(<Component service={service} sessionId="cdc-one" surface={surface}
    executeCommand={executeCommand} currentUser={{permissions}} resources={[
      {id: 'r1', label: 'Target', type: 'table', reference: content.sink.resourceRef}]}
    credentialReferences={[{id: 'c1', label: 'Source credential', type: 'credential',
      reference: content.source.credentialRef}]} {...properties} />);
  return {session, service, executeCommand};
}

function selectOption(label, option) {
  const original = console.error.getMockImplementation();
  console.error.mockImplementation(() => {});
  fireEvent.mouseDown(screen.getByRole('combobox', {name: label}));
  fireEvent.click(screen.getByRole('option', {name: option}));
  console.error.mockImplementation(original); console.error.mockClear();
}

describe('CDCWorkspace', () => {
  beforeEach(() => {
    usePreferences.setState({data: [], version: Date.now(), isLoading: false, failed: false});
  });

  it.each([
    ['cdc_designer', 'CDC flow'], ['source_capture', 'Source ResourceRef JSON'],
    ['event_mapping', 'Sink ResourceRef JSON'], ['schema_evolution', 'Default policy'],
    ['run_monitor', 'CDC run controls'], ['event_inspector', 'Bounded sample size'],
    ['replay', 'From source position/time'],
  ])('renders the complete %s surface', (surface, accessibleName) => {
    mount(surface);
    expect(screen.getByLabelText(accessibleName)).toBeVisible();
    expect(screen.getByRole('navigation', {name: 'CDC DESIGN'})).toBeVisible();
  });

  it('provides graph and text alternatives and semantic stage selection', () => {
    const {service} = mount();
    expect(screen.getByRole('img', {name: /CDC flow: 4 nodes and 3 edges/})).toBeVisible();
    expect(screen.getByRole('table', {name: 'CDC flow text alternative'})).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: /mongodb.*source.*oplog/i}));
    expect(service.select).toHaveBeenCalledWith('cdc-one', {stage: 'source'});
  });

  it('filters and keyboard-selects independently openable module surfaces', () => {
    const {service} = mount();
    fireEvent.change(screen.getByLabelText('Filter CDC surfaces'), {target: {value: 'Replay'}});
    expect(screen.getByRole('treeitem', {name: 'Replay'})).toBeVisible();
    expect(screen.queryByRole('treeitem', {name: 'CDC Designer'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('treeitem', {name: 'Replay'}));
    expect(service.select).toHaveBeenCalledWith('cdc-one', {surface: 'replay'});
    expect(screen.getByLabelText('From source position/time')).toBeVisible();
  });

  it('saves a complete source/capture policy through the registered command', () => {
    const {executeCommand} = mount('source_capture');
    fireEvent.change(screen.getByLabelText('Exact provider-native mechanism'),
      {target: {value: 'MongoDB change streams v2'}});
    fireEvent.click(screen.getByRole('button', {name: 'Save source and capture'}));
    expect(executeCommand).toHaveBeenCalledWith('cdc.definition.create', {content:
      expect.objectContaining({source: expect.objectContaining({
        nativeMechanism: 'MongoDB change streams v2',
        snapshotPolicy: expect.objectContaining({mode: 'initial'})})})});
  });

  it('keeps exactly-once unavailable without all proof fields', () => {
    const {executeCommand} = mount('event_mapping');
    selectOption('Declared delivery guarantee', 'exactly_once_proven');
    fireEvent.click(screen.getByRole('button', {name: 'Save sink and delivery'}));
    expect(executeCommand).not.toHaveBeenCalled();
    expect(screen.getByText(/requires sourceCapture proof/i)).toBeVisible();
  });

  it('authors event transforms without raw provider mutation', () => {
    const {executeCommand} = mount('event_mapping');
    fireEvent.change(screen.getByLabelText('Transform ID'), {target: {value: 'redact-email'}});
    selectOption('Kind', 'redaction');
    fireEvent.change(screen.getByLabelText('Rules JSON'),
      {target: {value: '{"path":"after.email"}'}});
    fireEvent.click(screen.getByRole('button', {name: 'Save transform'}));
    expect(executeCommand).toHaveBeenCalledWith('cdc.definition.create', {content:
      expect.objectContaining({transforms: expect.arrayContaining([
        expect.objectContaining({id: 'redact-email', kind: 'redaction'})])})});
  });

  it('prevents breaking schema change auto-apply in the editor', () => {
    const {executeCommand} = mount('schema_evolution');
    fireEvent.change(screen.getByLabelText('Change ID'), {target: {value: 'breaking-2'}});
    selectOption('Classification', 'breaking');
    selectOption('Resolution', 'auto_apply_compatible');
    fireEvent.click(screen.getByRole('button', {name: 'Save policy and change'}));
    expect(executeCommand).not.toHaveBeenCalled();
    expect(screen.getByText(/cannot be auto-applied/i)).toBeVisible();
  });

  it('validates a selected schema change through the shared task service', () => {
    const {service} = mount('schema_evolution');
    fireEvent.change(screen.getByLabelText('Change ID'), {target: {value: 'change-1'}});
    fireEvent.click(screen.getByRole('button', {name: 'Validate selected change'}));
    expect(service.validateSchemaChange).toHaveBeenCalledWith('cdc-one', 'change-1',
      {currentUser: {permissions}});
  });

  it('invokes every run control and checkpoint command through CommandRegistry boundary', () => {
    const {executeCommand} = mount('run_monitor');
    for(const label of ['Start', 'Pause', 'Resume', 'Stop', 'Refresh checkpoint']) {
      fireEvent.click(screen.getByRole('button', {name: label}));
    }
    expect(executeCommand.mock.calls.map((call) => call[0])).toEqual([
      'cdc.run.start', 'cdc.run.pause', 'cdc.run.resume', 'cdc.run.stop',
      'cdc.checkpoint.inspect']);
  });

  it('authors actionable lag alerts in the run workspace', () => {
    const {executeCommand} = mount('run_monitor');
    fireEvent.change(screen.getByLabelText('Alert ID'), {target: {value: 'lag-high'}});
    fireEvent.change(screen.getByLabelText('Lag threshold'), {target: {value: '30'}});
    fireEvent.click(screen.getByRole('button', {name: 'Save lag alert'}));
    expect(executeCommand).toHaveBeenCalledWith('cdc.definition.create', {content:
      expect.objectContaining({alerts: [expect.objectContaining({id: 'lag-high', event: 'lag',
        threshold: {maximum: 30}})]})});
  });

  it('loads bounded event samples and states payload authorization', async () => {
    const {service} = mount('event_inspector');
    expect(screen.getByText('Payload permission granted')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Bounded sample size'), {target: {value: '25'}});
    fireEvent.click(screen.getByRole('button', {name: 'Load bounded sample'}));
    await waitFor(() => expect(service.inspectEvents).toHaveBeenCalledWith('cdc-one',
      {limit: 25, redactedPaths: []}, {currentUser: {permissions}}));
  });

  it('requires prepared review before replay execution', () => {
    mount('replay');
    expect(screen.getByRole('button', {name: 'Execute reviewed replay'})).toBeDisabled();
    expect(screen.getByText(/never rewinds the live stream/i)).toBeVisible();
  });

  it.each([['permission_denied', 'Permission denied'],
    ['validation_error', 'definition validation failed'], ['runtime_failure', 'provider failed'],
    ['partial', 'CDC Designer is partial'], ['disconnected', 'CDC Designer is disconnected'],
    ['read_only', 'CDC Designer is read only']])('renders %s explicitly', (state, message) => {
    mount('cdc_designer', {state, error: state === 'runtime_failure' ? 'provider failed' : ''});
    expect(screen.getAllByText(new RegExp(message, 'i'))[0]).toBeVisible();
  });

  it.each([['loading', 'Loading'], ['background_task_active', 'CDC background task']])(
    'renders %s while retaining module navigation', (state, label) => {
      mount('cdc_designer', {state});
      expect(screen.getByRole('status', {name: label})).toBeVisible();
      expect(screen.getByRole('navigation', {name: 'CDC DESIGN'})).toBeVisible();
    });

  it('renders the unconfigured state without replacing the authoring surface', () => {
    mount('cdc_designer', {state: 'empty', content: createCDCContent({name: ''}),
      runs: [], events: [], schemaChanges: [], providerStatuses: []});
    expect(screen.getByText(/CDC definition is not configured/i)).toBeVisible();
    expect(screen.getByLabelText('CDC flow')).toBeVisible();
    expect(screen.getByRole('navigation', {name: 'CDC DESIGN'})).toBeVisible();
  });

  it('keeps provider warnings and limits visible', () => {
    mount('cdc_designer', {state: 'partial', providerStatuses: [{providerId: 'mongodb',
      supportState: 'partial', warnings: ['Snapshot unavailable.'],
      limitations: ['Polling only.']}]});
    expect(screen.getByText(/Snapshot unavailable.*Polling only/)).toBeVisible();
  });

  it('disables editing, execution and replay without exact permissions', () => {
    mount('run_monitor', {}, {currentUser: {permissions: ['cdc.view']}});
    expect(screen.getByRole('button', {name: 'Start'})).toBeDisabled();
    expect(screen.getByRole('button', {name: 'Pause'})).toBeDisabled();
  });

  it.each(['stale', 'disconnected'])(
    'disables live provider access while %s', (state) => {
      mount('run_monitor', {state});
      for(const label of ['Start', 'Pause', 'Resume', 'Stop', 'Refresh checkpoint']) {
        expect(screen.getByRole('button', {name: label})).toBeDisabled();
      }
    });

  it.each(['partial', 'read_only'])(
    'blocks live mutation but permits safe checkpoint inspection while %s', (state) => {
      mount('run_monitor', {state});
      for(const label of ['Start', 'Pause', 'Resume', 'Stop']) {
        expect(screen.getByRole('button', {name: label})).toBeDisabled();
      }
      expect(screen.getByRole('button', {name: 'Refresh checkpoint'})).toBeEnabled();
    });

  it('allows disconnected authored edits', () => {
    const disconnected = mount('cdc_designer', {state: 'disconnected'});
    fireEvent.click(screen.getByRole('button', {name: 'Save definition'}));
    expect(disconnected.executeCommand).toHaveBeenCalledWith('cdc.definition.create',
      expect.any(Object));
  });

  it('blocks read-only authored edits', () => {
    mount('cdc_designer', {state: 'read_only'});
    expect(screen.getByRole('button', {name: 'Save definition'})).toBeDisabled();
  });

  it.each(['cdeadmin_standard', 'compact_expert', 'low_vision', 'high_contrast_light'])(
    'remains operable under %s presentation', (profile) => {
      usePreferences.setState({data: [{id: 1, module: 'misc', name: 'accessibility_profile',
        value: profile}], version: Date.now(), isLoading: false, failed: false});
      const {service} = mount();
      fireEvent.click(screen.getByRole('button', {name: /mongodb.*source.*oplog/i}));
      expect(service.select).toHaveBeenCalledWith('cdc-one', {stage: 'source'});
    });

  it('provides exact inspector facts with separate checkpoints', () => {
    expect(cdcInspector(value())).toEqual({selectedStage: 'source',
      nativeMechanism: 'MongoDB change streams', checkpoint: {cursor: 'c2'},
      delivery: 'at_least_once', evolutionPolicy: 'pause_and_review', runState: 'streaming'});
  });
});

describe('CDCNavigator', () => {
  it('opens a CDC definition from a real tree and filters it', () => {
    const service = fakeService(); const onOpen = jest.fn(); const Navigator = withTheme(CDCNavigator);
    render(<Navigator service={service} onOpen={onOpen} />);
    fireEvent.doubleClick(screen.getByRole('treeitem', {name: /Orders CDC/}));
    expect(onOpen).toHaveBeenCalledWith('cdc-one', 'cdc_designer');
    fireEvent.change(screen.getByLabelText('Filter CDC definitions'),
      {target: {value: 'absent'}});
    expect(screen.getByText(/No CDC definitions/)).toBeVisible();
  });
});
