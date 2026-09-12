import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {
  TracingNavigator, TracingWorkspace, tracingInspector,
} from 'sources/cdeadmin_ui/modules/tracing/TracingWorkspace';
import usePreferences from '../../../pgadmin/preferences/static/js/store';
import {sessionValue, span, trace} from './TracingTestUtils';

const permissions = ['trace.view', 'trace.view_sensitive', 'trace.configure_source',
  'trace.export', 'trace.admin'];
function fakeService(session=sessionValue()) {
  const listeners = new Set(); return {get: jest.fn(() => session), list: jest.fn(() => [session]),
    subscribe: jest.fn((listener) => { listeners.add(listener); return () => listeners.delete(listener); }),
    select: jest.fn(), aggregateMap: jest.fn(), listeners};
}
function mount(surface='trace_search', overrides={}, properties={}) {
  const session = sessionValue(overrides); const service = fakeService(session);
  const executeCommand = jest.fn().mockResolvedValue(session); const Component = withTheme(TracingWorkspace);
  render(<Component service={service} sessionId="tracing-one" surface={surface}
    executeCommand={executeCommand} currentUser={{permissions}} {...properties} />);
  return {session, service, executeCommand};
}

describe('TracingWorkspace', () => {
  beforeEach(() => usePreferences.setState({data: [], version: Date.now(),
    isLoading: false, failed: false}));

  test.each([
    ['trace_search', 'Trace search results'], ['trace_waterfall', 'Trace waterfall'],
    ['span_inspector', 'Span attributes'], ['service_resource_map', 'Observed service resource map'],
    ['query_correlation', 'Trace query correlations'], ['ingestion_sampling', 'Trace ingestion sources'],
  ])('renders the complete %s surface', (surface, accessibleName) => {
    mount(surface); expect(screen.getByLabelText(accessibleName)).toBeVisible();
    expect(screen.getByRole('navigation', {name: 'Tracing surfaces'})).toBeVisible();
  });

  test('search routes through command authority and opens an exact trace', async () => {
    const {executeCommand} = mount();
    fireEvent.change(screen.getByLabelText('Trace text filter'), {target: {value: 'work'}});
    fireEvent.click(screen.getByRole('button', {name: 'Search'}));
    expect(executeCommand).toHaveBeenCalledWith('trace.search', expect.objectContaining({
      filters: expect.objectContaining({text: 'work'}), pageSize: 100}));
    fireEvent.click(screen.getByRole('button', {name: `Open trace ${trace().traceId}`}));
    await waitFor(() => expect(executeCommand).toHaveBeenCalledWith(
      'trace.open', {traceId: trace().traceId}));
  });

  test('waterfall provides hierarchical, critical-path and keyboard selection', () => {
    const {service} = mount('trace_waterfall');
    expect(screen.getByRole('treeitem', {name: /SELECT work_orders/})).toHaveAttribute('aria-level', '1');
    fireEvent.keyDown(screen.getByRole('treeitem', {name: /SELECT work_orders/}), {key: 'Enter'});
    expect(service.select).toHaveBeenCalledWith('tracing-one', {spanId: span().spanId});
    expect(screen.getByTitle(/SELECT work_orders/)).toHaveStyle({border: '2px solid currentColor'});
  });

  test('span inspector exposes unknown attributes, events, links and correlations', () => {
    const {executeCommand} = mount('span_inspector');
    expect(screen.getByText('db.query.summary')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Copy span ID'}));
    fireEvent.click(screen.getByRole('button', {name: 'Open resource'}));
    expect(executeCommand.mock.calls.map((item) => item[0])).toEqual([
      'trace.copy_id', 'trace.resource.open']);
  });

  test('service map labels observed evidence and uses a non-visual table route', () => {
    mount('service_resource_map');
    expect(screen.getByText(/This is observed evidence, not timeless topology/)).toBeVisible();
    expect(screen.getByLabelText('Observed service map table')).toBeVisible();
  });

  test('ingestion and sampling route through separate permissioned commands', () => {
    const {executeCommand} = mount('ingestion_sampling');
    fireEvent.click(screen.getByRole('button', {name: 'Validate and save source'}));
    fireEvent.click(screen.getByRole('button', {name: 'Validate and save policy'}));
    expect(executeCommand.mock.calls.map((item) => item[0])).toEqual([
      'trace.source.configure', 'trace.sampling.update']);
  });

  test.each([['permission_denied', 'Permission denied'], ['validation_error', 'validation error'],
    ['runtime_failure', 'provider failed'], ['partial', 'is partial'],
    ['disconnected', 'is disconnected'], ['read_only', 'is read only'], ['stale', 'is stale']])(
    'renders %s explicitly', (state, message) => {
      mount('trace_search', {state, error: state === 'runtime_failure' ? 'provider failed' : ''});
      expect(screen.getAllByText(new RegExp(message, 'i'))[0]).toBeVisible();
    });

  test.each([['loading', 'Loading'], ['background_task_active', 'Tracing background task']])(
    'renders %s while retaining navigation', (state, label) => {
      mount('trace_search', {state}); expect(screen.getByRole('status', {name: label})).toBeVisible();
      expect(screen.getByRole('navigation', {name: 'Tracing surfaces'})).toBeVisible();
    });

  test('blocks live and authored operations by state and permission', () => {
    mount('ingestion_sampling', {state: 'read_only'}, {currentUser: {permissions: ['trace.view']}});
    expect(screen.getByRole('button', {name: 'Validate and save source'})).toBeDisabled();
    expect(screen.getByRole('button', {name: 'Validate and save policy'})).toBeDisabled();
  });

  test.each(['cdeadmin_standard', 'compact_expert', 'low_vision', 'high_contrast_light'])(
    'remains operable under %s presentation', (profile) => {
      usePreferences.setState({data: [{id: 1, module: 'misc', name: 'accessibility_profile',
        value: profile}], version: Date.now(), isLoading: false, failed: false});
      const {executeCommand} = mount(); fireEvent.click(screen.getByRole('button', {name: 'Search'}));
      expect(executeCommand).toHaveBeenCalledWith('trace.search', expect.any(Object));
    });

  test('provides exact inspector facts for selected trace and span', () => {
    expect(tracingInspector(sessionValue())).toMatchObject({traceId: trace().traceId,
      spanId: span().spanId, span: 'SELECT work_orders', status: 'OK',
      resource: {'service.name': 'cdeadmin'}});
  });

  test('opens tracing assets through the shared activity navigator', () => {
    const service = fakeService(); const onOpen = jest.fn(); const Component = withTheme(TracingNavigator);
    render(<Component service={service} onOpen={onOpen} />);
    fireEvent.click(screen.getByRole('treeitem', {name: /Reference tracing/}));
    expect(onOpen).toHaveBeenCalledWith('tracing-one', 'trace_search');
  });
});
