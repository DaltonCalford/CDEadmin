/////////////////////////////////////////////////////////////
// Data Lineage surface interaction, state and navigation gates.
/////////////////////////////////////////////////////////////

import {fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {
  LineageNavigator, LineageWorkspace,
} from 'sources/cdeadmin_ui/modules/lineage';
import usePreferences from '../../../pgadmin/preferences/static/js/store';

const resourceRef = (id) => ({schema: 'cdeadmin.resource-ref.v1', provider: 'firebird',
  canonical: `cde-resource://firebird/local/%2F/table/${id}`});
const evidence = {id: 'ev-one', origin: 'inferred', confidence: 0.6,
  capturedAt: '2026-09-11T12:00:00Z', stale: false, details: {source: 'parser'}};
const edge = {id: 'edge-one', from: 'source', to: 'target', type: 'derives',
  origin: 'inferred', confidence: 0.6, presentationState: 'inferred', suppressed: false,
  evidence: [evidence], fieldLineage: [{id: 'field-one', sourceField: 'source.id',
    targetField: 'target.id', transformation: 'DIRECT', expressionRef: null,
    evidenceIds: ['ev-one']}], nativeDetails: {}};

function value(overrides={}) {
  return {schema: 'cdeadmin.lineage.session.v1', id: 'lineage-one', state: 'ready',
    dirty: false, error: '', problems: [], activeTaskId: null,
    content: {scopeRefs: [resourceRef('source')], sourcePolicies: [], savedFilters: {},
      curatedEdges: [], suppressedInferenceRules: [], snapshotRefs: []},
    graph: {revision: 'revision-one', nodes: [
      {id: 'source', name: 'Source', kind: 'table', namespace: 'demo',
        reference: resourceRef('source'), nativeDetails: {}},
      {id: 'target', name: 'Target', kind: 'view', namespace: 'demo',
        reference: resourceRef('target'), nativeDetails: {critical: true}},
    ], edges: [edge]}, selectedNodeId: 'source', selectedEdgeId: 'edge-one',
    traversal: null, impact: {risk: [{nodeId: 'target', classification: 'restricted',
      risk: 'high'}], riskSummary: {high: 1, medium: 0, low: 0}},
    snapshots: [{id: 'before'}, {id: 'after'}], snapshotDiff: {nodes: {
      added: ['new-node'], removed: [], changed: []}, edges: {added: [],
      removed: ['old-edge'], changed: []}},
    ingestion: [{providerId: 'firebird', supportState: 'supported_native',
      providerVersion: '5.0.4', warnings: [], evidence: {declared: 1},
      readCapabilities: ['graph'], writeCapabilities: [],
      discoveryCapabilities: ['catalog'], limitations: []}],
    imports: [{id: 'ol-one'}], history: [], ...overrides};
}

function fakeService(session=value()) {
  const listeners = new Set();
  return {get: jest.fn(() => session), list: jest.fn(() => [session]),
    subscribe: jest.fn((listener) => { listeners.add(listener);
      return () => listeners.delete(listener); }),
    select: jest.fn(), reportError: jest.fn(), listeners};
}

function mount(surface, overrides={}, properties={}) {
  const session = value(overrides); const service = fakeService(session);
  const executeCommand = jest.fn().mockResolvedValue('openlineage-export');
  const Component = withTheme(LineageWorkspace);
  render(<Component service={service} sessionId="lineage-one" surface={surface}
    executeCommand={executeCommand} {...properties} />);
  return {session, service, executeCommand};
}

describe('LineageWorkspace', () => {
  beforeEach(() => {
    usePreferences.setState({data: [], version: Date.now(), isLoading: false, failed: false});
  });

  it('renders all six surfaces and provides selectable graph nodes and edges', () => {
    const {service, executeCommand} = mount('lineage_explorer');
    expect(screen.getAllByRole('tab')).toHaveLength(6);
    fireEvent.click(screen.getByRole('button', {name: /Target, view, demo/}));
    expect(service.select).toHaveBeenCalledWith('lineage-one', {nodeId: 'target'});
    fireEvent.click(screen.getByRole('button', {name: /derives edge from source to target/}));
    expect(service.select).toHaveBeenCalledWith('lineage-one', {edgeId: 'edge-one'});
    fireEvent.click(screen.getByRole('button', {name: 'Trace selected'}));
    expect(executeCommand).toHaveBeenCalledWith('lineage.node.trace_downstream',
      {nodeId: 'source', depth: 1}, expect.objectContaining({sessionId: 'lineage-one'}));
  });

  it('shows exact field mappings and routes evidence inspection through selection', () => {
    const {service} = mount('field_lineage');
    expect(screen.getByLabelText('Field lineage transformations')).toBeVisible();
    expect(screen.getByText('source.id')).toBeVisible();
    expect(screen.getByText('DIRECT')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Inspect evidence'}));
    expect(service.select).toHaveBeenCalledWith('lineage-one', {edgeId: 'edge-one'});
  });

  it('renders bounded impact results and invokes background impact analysis', () => {
    const {executeCommand} = mount('impact_analysis');
    expect(screen.getByLabelText('Lineage impact results')).toBeVisible();
    expect(screen.getByText('restricted')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Analyze selected node'}));
    expect(executeCommand).toHaveBeenCalledWith('lineage.impact.run',
      {nodeId: 'source', depth: 10}, expect.any(Object));
  });

  it('renders snapshot history differences and compares two selected snapshots', () => {
    const {executeCommand} = mount('timeline_snapshot_compare');
    expect(screen.getByText('new-node')).toBeVisible();
    expect(screen.getByText('old-edge')).toBeVisible();
    fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Earlier snapshot'}));
    fireEvent.click(screen.getByRole('option', {name: 'before'}));
    fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Later snapshot'}));
    fireEvent.click(screen.getByRole('option', {name: 'after'}));
    // JSDOM has no viewport geometry; discard MUI's layout-only popover warning.
    console.error.mockClear();
    fireEvent.click(screen.getByRole('button', {name: 'Compare'}));
    expect(executeCommand).toHaveBeenCalledWith('lineage.snapshot.compare',
      {leftId: 'before', rightId: 'after'}, expect.any(Object));
  });

  it('preserves evidence while exposing curation and inference suppression', () => {
    const {executeCommand} = mount('evidence_inspector');
    expect(screen.getByLabelText('Lineage evidence')).toBeVisible();
    expect(screen.getByText(/parser/)).toBeVisible();
    fireEvent.change(screen.getByLabelText('Curation note'), {target: {value: 'Reviewed'}});
    fireEvent.click(screen.getByRole('button', {name: 'Curate without deleting evidence'}));
    expect(executeCommand).toHaveBeenCalledWith('lineage.edge.curate',
      {edgeId: 'edge-one', type: 'derives', note: 'Reviewed'}, expect.any(Object));
    fireEvent.click(screen.getByRole('button', {name: 'Suppress inference'}));
    expect(executeCommand).toHaveBeenCalledWith('lineage.edge.suppress_inference',
      {edgeId: 'edge-one'}, expect.any(Object));
  });

  it('imports and exports OpenLineage without replacing provider ingestion status', async () => {
    const onExport = jest.fn(); const {executeCommand} = mount('ingestion_status', {}, {onExport});
    expect(screen.getByText('firebird')).toBeVisible();
    const event = {eventType: 'COMPLETE', eventTime: '2026-09-11T12:00:00Z',
      run: {runId: 'run-one'}, job: {namespace: 'demo', name: 'copy'}};
    fireEvent.change(screen.getByLabelText('OpenLineage RunEvent JSON'),
      {target: {value: JSON.stringify(event)}});
    fireEvent.click(screen.getByRole('button', {name: 'Import OpenLineage'}));
    expect(executeCommand).toHaveBeenCalledWith('lineage.import.openlineage',
      {events: event}, expect.any(Object));
    fireEvent.click(screen.getByRole('button', {name: 'Export lossless OpenLineage'}));
    await waitFor(() => expect(onExport).toHaveBeenCalledWith('openlineage-export'));
  });

  it('keeps stale evidence visible and publishes normalized failures', async () => {
    const failure = new Error('provider_unavailable: Firebird is offline');
    const service = fakeService(value({state: 'stale', error: 'Last scan expired'}));
    const executeCommand = jest.fn().mockRejectedValue(failure);
    const Component = withTheme(LineageWorkspace);
    render(<Component service={service} sessionId="lineage-one"
      executeCommand={executeCommand} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Last scan expired');
    expect(screen.getByText(/Last safe evidence remains visible/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Refresh evidence'}));
    await waitFor(() => expect(service.reportError).toHaveBeenCalledWith(
      'lineage-one', failure, 'provider_unavailable'
    ));
  });

  it.each([
    'cdeadmin_standard', 'compact_expert', 'low_vision', 'high_contrast_light',
  ])('remains keyboard-operable under the %s presentation profile', (profile) => {
    usePreferences.setState({data: [{id: 1, module: 'misc',
      name: 'accessibility_profile', value: profile}], version: Date.now(),
    isLoading: false, failed: false});
    const {service} = mount('lineage_explorer');
    const target = screen.getByRole('button', {name: /Target, view, demo/});
    fireEvent.keyDown(target, {key: 'Enter'});
    expect(service.select).toHaveBeenCalledWith('lineage-one', {nodeId: 'target'});
  });
});

describe('LineageNavigator', () => {
  it('provides a real tree hierarchy for assets, scopes, sources and snapshots', () => {
    const service = fakeService(); const onOpen = jest.fn();
    const Navigator = withTheme(LineageNavigator);
    render(<Navigator service={service} onOpen={onOpen} />);
    const asset = screen.getByRole('treeitem', {name: /lineage-one/});
    fireEvent.click(within(asset).getByRole('button', {name: /Expand lineage-one/}));
    const snapshots = screen.getByRole('treeitem', {name: /Snapshots/});
    fireEvent.doubleClick(snapshots);
    expect(onOpen).toHaveBeenCalledWith('lineage-one', 'timeline_snapshot_compare');
    expect(screen.getByRole('treeitem', {name: /Scopes/})).toBeVisible();
    expect(screen.getByRole('treeitem', {name: /Sources/})).toBeVisible();
  });
});
