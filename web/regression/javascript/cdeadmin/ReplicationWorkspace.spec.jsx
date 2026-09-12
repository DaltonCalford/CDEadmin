import {fireEvent, render, screen} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {createReplicationContent} from 'sources/cdeadmin_ui/modules/replication/contracts';
import {
  ReplicationNavigator, ReplicationWorkspace, replicationInspector,
} from 'sources/cdeadmin_ui/modules/replication/ReplicationWorkspace';
import usePreferences from '../../../pgadmin/preferences/static/js/store';
import {
  definition, failoverReview, lagSample, topology,
} from './ReplicationTestUtils';

const content = createReplicationContent(definition());
const live = {...topology(), lagSamples: [lagSample()], events: [{id: 'role-event',
  topologyId: 'topology-one', participantId: 'replica', linkId: null, type: 'role_changed',
  occurredAt: '2026-09-11T00:00:00Z', cause: 'provider rediscovery', evidence: {},
  nativeDetails: {}}]};
const permissions = ['replication.view', 'replication.control',
  'replication.plan_failover', 'replication.execute_failover', 'replication.admin'];

function value(overrides={}) {
  return {schema: 'cdeadmin.replication-session.v1', id: 'replication-one', content,
    state: 'ready', dirty: false, selectedTopologyId: 'topology-one',
    selectedParticipantId: 'replica', selectedLinkId: 'link-one',
    selection: {surface: 'topology_explorer'}, liveTopologies: [live], providerStatuses: [{
      supportState: 'supported_native', warnings: [], limitations: []}], failoverReviews: [{
      schema: 'cdeadmin.replication-failover-review.v1', planId: 'plan-one', valid: true,
      details: [], ...failoverReview()}], armedPlan: {planId: 'plan-one'}, activeTaskId: null,
    problems: [], history: [], error: '', createdAt: 'now', updatedAt: 'now', ...overrides};
}

function fakeService(session=value()) {
  const listeners = new Set();
  return {get: jest.fn(() => session), list: jest.fn(() => [session]),
    subscribe: jest.fn((listener) => { listeners.add(listener); return () => listeners.delete(listener); }),
    select: jest.fn(), updateLayout: jest.fn(), listeners};
}

function mount(surface='topology_explorer', overrides={}, properties={}) {
  const session = value(overrides); const service = fakeService(session);
  const executeCommand = jest.fn().mockResolvedValue(session);
  const Component = withTheme(ReplicationWorkspace);
  render(<Component service={service} sessionId="replication-one" surface={surface}
    executeCommand={executeCommand} currentUser={{permissions}} {...properties} />);
  return {session, service, executeCommand};
}

describe('ReplicationWorkspace', () => {
  beforeEach(() => {
    usePreferences.setState({data: [], version: Date.now(), isLoading: false, failed: false});
  });

  it.each([
    ['topology_explorer', 'Live replication topology'],
    ['participant_inspector', 'Replication participants'],
    ['replication_link_inspector', 'Replication links'],
    ['lag_history', /Replication lag history: 1 samples/],
    ['failover_planner', 'Plan ID'],
    ['events', 'Replication topology events'],
  ])('renders the complete %s surface', (surface, accessibleName) => {
    mount(surface);
    expect(screen.getByLabelText(accessibleName)).toBeVisible();
    expect(screen.getByRole('navigation', {name: 'REPLICATION'})).toBeVisible();
  });

  it('shows both normalized and exact provider-native topology evidence', () => {
    mount();
    expect(screen.getByRole('img', {name: /Live replication topology: 2 nodes and 1 edges/}))
      .toBeVisible();
    expect(screen.getByRole('table', {name: 'Replication topology text alternative'}))
      .toBeVisible();
    expect(screen.getAllByText('writer_capable')[0]).toBeVisible();
    expect(screen.getAllByText('primary')[0]).toBeVisible();
  });

  it('filters and opens independent module surfaces', () => {
    const {service} = mount();
    fireEvent.change(screen.getByLabelText('Filter replication surfaces'),
      {target: {value: 'Failover'}});
    expect(screen.getByRole('treeitem', {name: 'Failover Planner'})).toBeVisible();
    expect(screen.queryByRole('treeitem', {name: 'Topology Explorer'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('treeitem', {name: 'Failover Planner'}));
    expect(service.select).toHaveBeenCalledWith('replication-one', {surface: 'failover_planner'});
    expect(screen.getByLabelText('Provider-native target role')).toBeVisible();
  });

  it('routes refresh and snapshot through command authority', () => {
    const {executeCommand} = mount();
    fireEvent.click(screen.getByRole('button', {name: 'Refresh'}));
    fireEvent.click(screen.getByRole('button', {name: 'Snapshot'}));
    expect(executeCommand.mock.calls).toEqual([
      ['replication.topology.refresh', {topologyId: 'topology-one'}],
      ['replication.snapshot.create', {topologyId: 'topology-one'}]]);
  });

  it('keeps topology movement visual-only and keyboard accessible', () => {
    const {service, executeCommand} = mount();
    fireEvent.keyDown(screen.getByRole('button', {
      name: /replica, read_only_replica, standby/}), {key: 'ArrowDown'});
    expect(service.updateLayout).toHaveBeenCalledWith('replication-one', 'topology-one',
      'replica', {x: 320, y: 108}, {currentUser: {permissions}});
    expect(executeCommand).not.toHaveBeenCalled();
  });

  it('selects participants and links without mutating provider state', () => {
    const {service} = mount();
    fireEvent.click(screen.getByRole('button', {name: /replica, read_only_replica, standby/}));
    fireEvent.click(screen.getByRole('button', {name: /physical streaming edge/}));
    expect(service.select).toHaveBeenCalledWith('replication-one', {participantId: 'replica'});
    expect(service.select).toHaveBeenCalledWith('replication-one', {linkId: 'link-one'});
  });

  it('routes pause and resume through command authority', () => {
    const {executeCommand} = mount('replication_link_inspector');
    fireEvent.click(screen.getByRole('button', {name: 'Pause selected link'}));
    fireEvent.click(screen.getByRole('button', {name: 'Resume selected link'}));
    expect(executeCommand.mock.calls).toEqual([
      ['replication.link.pause', {topologyId: 'topology-one', linkId: 'link-one'}],
      ['replication.link.resume', {topologyId: 'topology-one', linkId: 'link-one'}]]);
  });

  it('exposes all failover safeguards and routes exact plan workflow', () => {
    const {executeCommand} = mount('failover_planner');
    expect(screen.getByText(/Low lag alone never proves promotion safety/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Save reviewable plan'}));
    fireEvent.click(screen.getByRole('button', {name: 'Validate plan'}));
    fireEvent.change(screen.getByLabelText('Confirmation reference'),
      {target: {value: 'ticket-1'}});
    fireEvent.change(screen.getByLabelText('Target environment'),
      {target: {value: 'production'}});
    fireEvent.change(screen.getByLabelText('Target connection'),
      {target: {value: 'cluster-one'}});
    fireEvent.click(screen.getByRole('button', {name: 'Arm exact plan revision'}));
    fireEvent.click(screen.getByRole('button', {name: 'Execute armed failover'}));
    expect(executeCommand.mock.calls.map((item) => item[0])).toEqual([
      'replication.failover.plan', 'replication.failover.validate',
      'replication.failover.arm', 'replication.failover.execute']);
  });

  it.each([['permission_denied', 'Permission denied'],
    ['validation_error', 'validation error'], ['runtime_failure', 'provider failed'],
    ['partial', 'is partial'], ['disconnected', 'is disconnected'],
    ['read_only', 'is read only'], ['stale', 'is stale']])(
    'renders %s explicitly', (state, message) => {
      mount('topology_explorer', {state,
        error: state === 'runtime_failure' ? 'provider failed' : ''});
      expect(screen.getAllByText(new RegExp(message, 'i'))[0]).toBeVisible();
    });

  it.each([['loading', 'Loading'],
    ['background_task_active', 'Replication background task']])(
    'renders %s while retaining navigation', (state, label) => {
      mount('topology_explorer', {state});
      expect(screen.getByRole('status', {name: label})).toBeVisible();
      expect(screen.getByRole('navigation', {name: 'REPLICATION'})).toBeVisible();
    });

  it('renders empty state while retaining all six surface choices', () => {
    mount('topology_explorer', {state: 'empty', selectedTopologyId: null,
      content: createReplicationContent({name: ''}), liveTopologies: []});
    expect(screen.getByText(/No replication scope is configured/)).toBeVisible();
    expect(screen.getAllByRole('treeitem')).toHaveLength(6);
  });

  it.each(['stale', 'disconnected'])(
    'blocks provider access while %s', (state) => {
      mount('topology_explorer', {state});
      expect(screen.getByRole('button', {name: 'Refresh'})).toBeDisabled();
      expect(screen.getByRole('button', {name: 'Snapshot'})).toBeDisabled();
    });

  it.each(['partial', 'read_only'])(
    'allows safe discovery but blocks mutation while %s', (state) => {
      mount('topology_explorer', {state});
      expect(screen.getByRole('button', {name: 'Refresh'})).toBeEnabled();
      expect(screen.getByRole('button', {name: 'Snapshot'})).toBeDisabled();
    });

  it('blocks authored plan and visual layout edits while read only', () => {
    mount('failover_planner', {state: 'read_only'});
    expect(screen.getByRole('button', {name: 'Save reviewable plan'})).toBeDisabled();
  });

  it('separates all four permissions in the UI', () => {
    mount('replication_link_inspector', {}, {currentUser: {permissions: ['replication.view']}});
    expect(screen.getByRole('button', {name: 'Pause selected link'})).toBeDisabled();
    expect(screen.getByRole('button', {name: 'Resume selected link'})).toBeDisabled();
  });

  it('keeps provider warnings and limitations visible', () => {
    mount('topology_explorer', {state: 'partial', providerStatuses: [{supportState: 'partial',
      warnings: ['Position is unavailable.'], limitations: ['Read-only link inspection.']}]});
    expect(screen.getByText(/Position is unavailable.*Read-only link inspection/)).toBeVisible();
  });

  it.each(['cdeadmin_standard', 'compact_expert', 'low_vision', 'high_contrast_light'])(
    'remains operable under %s presentation', (profile) => {
      usePreferences.setState({data: [{id: 1, module: 'misc', name: 'accessibility_profile',
        value: profile}], version: Date.now(), isLoading: false, failed: false});
      const {executeCommand} = mount();
      fireEvent.click(screen.getByRole('button', {name: 'Refresh'}));
      expect(executeCommand).toHaveBeenCalledWith('replication.topology.refresh',
        {topologyId: 'topology-one'});
    });

  it('provides exact inspector facts for selected participant and link', () => {
    expect(replicationInspector(value())).toMatchObject({
      topology: 'PostgreSQL streaming replication', participant: {
        name: 'replica', normalizedRole: 'read_only_replica', nativeRole: 'standby',
        nativeState: 'streaming'}, link: {name: 'Primary to replica',
        mechanism: 'physical streaming', nativeState: 'streaming', health: 'healthy'}});
  });

  it('filters and opens replication assets from the activity navigator', () => {
    const service = fakeService(); const onOpen = jest.fn(); const Component = withTheme(
      ReplicationNavigator);
    render(<Component service={service} onOpen={onOpen} />);
    fireEvent.change(screen.getByLabelText('Filter replication assets'),
      {target: {value: 'Reference'}});
    fireEvent.click(screen.getByRole('treeitem', {name: /Reference topology/}));
    expect(onOpen).toHaveBeenCalledWith('replication-one', 'topology_explorer');
  });
});
