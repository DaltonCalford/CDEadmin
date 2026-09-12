/////////////////////////////////////////////////////////////
// Provider-aware replication topology workbench surfaces.
/////////////////////////////////////////////////////////////

import {useEffect, useMemo, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {Button} from '../../primitives/Button';
import {Select} from '../../primitives/Choice';
import {TextArea, TextField} from '../../primitives/Field';
import {SearchField} from '../../primitives/AdvancedControls';
import {TreeRow} from '../../navigation/AdvancedNavigation';
import {Toolbar} from '../../layout/WorkbenchChrome';
import {Badge, Banner, ProgressBar, Skeleton} from '../../feedback/Indicators';
import {EmptyState} from '../../feedback/EmptyState';
import DataGrid from '../../data/DataGrid';
import GraphSurface from '../../visualization/GraphSurface';
import {createReplicationContent, validateFailoverPlan} from './contracts';
import {topologyGraph} from './ReplicationEngine';

export const REPLICATION_SURFACES = Object.freeze([
  {id: 'topology_explorer', title: 'Topology Explorer'},
  {id: 'participant_inspector', title: 'Participant Inspector'},
  {id: 'replication_link_inspector', title: 'Replication Link Inspector'},
  {id: 'lag_history', title: 'Lag History'},
  {id: 'failover_planner', title: 'Failover Planner'},
  {id: 'events', title: 'Events'},
]);

function useSession(service, id) {
  const [session, setSession] = useState(() => service.get(id));
  useEffect(() => service.subscribe((next) => { if(next.id === id) setSession(next); }),
    [service, id]);
  return session;
}
function permitted(user, permission) { return user.permissions?.includes(permission) === true; }
function liveAllowed(session, user, permission='replication.view') {
  return permitted(user, permission) && !['loading', 'empty', 'stale', 'partial', 'disconnected',
    'read_only', 'permission_denied', 'validation_error', 'background_task_active']
    .includes(session.state);
}
function safeReadAllowed(session, user) {
  return permitted(user, 'replication.view') && !['loading', 'empty', 'stale', 'disconnected',
    'permission_denied', 'background_task_active'].includes(session.state);
}
function authoredEditAllowed(session, user, permission) {
  return permitted(user, permission) && !['loading', 'empty', 'read_only',
    'permission_denied', 'background_task_active'].includes(session.state);
}
function json(text, label, fallback={}) {
  if(!String(text ?? '').trim()) return fallback;
  const value = JSON.parse(text); if(!value || typeof value !== 'object') throw new TypeError(
    `${label} must be JSON.`
  );
  return value;
}
function optionalJson(text, label) {
  if(!String(text ?? '').trim() || String(text).trim() === 'null') return null;
  return json(text, label);
}
function selectedTopology(session) {
  return session.liveTopologies.find((item) => item.id === session.selectedTopologyId) ??
    session.content.savedTopologies.find((item) => item.id === session.selectedTopologyId) ?? null;
}
function selectedLayout(session) {
  return session.content.visualLayouts.find((item) => item.topologyId === session.selectedTopologyId) ?? {};
}

function StateBoundary({session, children}) {
  const limits = session.providerStatuses.flatMap((item) => [
    ...(item.warnings ?? []), ...(item.limitations ?? []),
  ]);
  return <>
    {session.state === 'loading' && <Box sx={{p: 2}}><Skeleton lines={8} /></Box>}
    {session.state === 'empty' && <EmptyState
      message="No replication scope is configured. Add a saved topology scope before discovery." />}
    {session.state === 'background_task_active' && <Box sx={{p: 1}}>
      <ProgressBar label="Replication background task" status="indeterminate" />
    </Box>}
    {session.error && <Box sx={{p: 1}}><Banner status="error">{session.error}</Banner></Box>}
    {!session.error && session.state === 'permission_denied' && <Box sx={{p: 1}}>
      <Banner status="error">Permission denied. Provider support remains a separate state.</Banner>
    </Box>}
    {['stale', 'partial', 'disconnected', 'read_only', 'validation_error']
      .includes(session.state) && <Box sx={{p: 1}}><Banner status="warning">
        Replication Topology is {session.state.replaceAll('_', ' ')}. Last safe data remains visible.
    </Banner></Box>}
    {limits.length > 0 && <Box sx={{p: 1}}><Banner status="warning">
      Provider limitations: {[...new Set(limits)].join(' ')}
    </Banner></Box>}
    {children}
  </>;
}
StateBoundary.propTypes = {session: PropTypes.object, children: PropTypes.node};

function TopologyExplorer({session, service, invoke, currentUser, onNavigate}) {
  const topology = selectedTopology(session); const [mode, setMode] = useState('graph');
  const [error, setError] = useState('');
  const call = (command, args={}) => Promise.resolve(invoke(command, args)).catch(
    (exception) => setError(exception.message));
  if(!topology) return <EmptyState message="Select or configure a replication topology scope." />;
  const graph = topologyGraph(topology, selectedLayout(session));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Live topology commands">
      <Button onClick={() => setMode('graph')}>Graph</Button>
      <Button onClick={() => setMode('table')}>Health</Button>
      <Button onClick={() => onNavigate('lag_history')}>Lag</Button>
      <Button disabled={!safeReadAllowed(session, currentUser)}
        onClick={() => call('replication.topology.refresh', {topologyId: topology.id})}>Refresh</Button>
      <Button disabled={!liveAllowed(session, currentUser)}
        onClick={() => call('replication.snapshot.create', {topologyId: topology.id})}>Snapshot</Button>
    </Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    <Box sx={{flex: 1, minHeight: 300}}><GraphSurface label="Live replication topology"
      nodes={graph.nodes} edges={graph.edges} mode={mode}
      selectedId={session.selectedParticipantId} selectedEdgeId={session.selectedLinkId}
      onSelect={(participantId) => service.select(session.id, {participantId})}
      onSelectEdge={(linkId) => service.select(session.id, {linkId})}
      onPositionChange={authoredEditAllowed(session, currentUser,
        'replication.plan_failover') ?
        (participantId, position) => service.updateLayout(session.id, topology.id,
          participantId, position, {currentUser}) : undefined} /></Box>
    <Box role="table" aria-label="Replication topology text alternative" sx={{p: 1}}>
      {topology.participants.map((item) => <Box role="row" key={item.id}
        sx={{display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr'}}>
        <Box role="cell">{item.name}</Box><Box role="cell">{item.normalizedRole}</Box>
        <Box role="cell">{item.nativeRole}</Box><Box role="cell">{item.health.overall}</Box>
      </Box>)}
    </Box>
  </Box>;
}
TopologyExplorer.propTypes = {session: PropTypes.object, service: PropTypes.object,
  invoke: PropTypes.func, currentUser: PropTypes.object, onNavigate: PropTypes.func};

function ParticipantInspector({session, service}) {
  const topology = selectedTopology(session);
  const participant = topology?.participants.find((item) => item.id === session.selectedParticipantId);
  const rows = (topology?.participants ?? []).map((item) => ({...item,
    resource: item.resourceRef.canonical, positionType: item.position?.positionType ?? '',
    rawPosition: item.position?.rawValue ?? '', storageText: JSON.stringify(item.storage),
    connectionsText: JSON.stringify(item.connections), nativeText: JSON.stringify(item.nativeDetails)}));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    {participant && <Box role="region" aria-label="Selected replication participant" sx={{p: 1}}>
      <Badge label={`${participant.normalizedRole} / ${participant.nativeRole}`} />
      <Box>Native state: {participant.nativeState}</Box>
      <Box>Position: {participant.position?.positionType ?? 'unknown'} · {
        participant.position?.rawValue ?? 'unknown'}</Box>
    </Box>}
    {!rows.length ? <EmptyState message="Discover a topology to inspect participants." /> :
      <DataGrid gridId="replication/participants" aria-label="Replication participants"
        rows={rows} readOnly enableRowSelect rowKeyGetter={(row) => row.id}
        onItemSelect={(row) => row && service.select(session.id, {participantId: row.id})}
        columns={[{key: 'name', name: 'Participant'}, {key: 'normalizedRole', name: 'Category'},
          {key: 'nativeRole', name: 'Native role'}, {key: 'nativeState', name: 'Native state'},
          {key: 'resource', name: 'Resource'}, {key: 'positionType', name: 'Position type'},
          {key: 'rawPosition', name: 'Native position'}, {key: 'storageText', name: 'Storage'},
          {key: 'connectionsText', name: 'Connections'}, {key: 'nativeText', name: 'Native metadata'}]} />}
  </Box>;
}
ParticipantInspector.propTypes = {session: PropTypes.object, service: PropTypes.object};

function LinkInspector({session, service, invoke, currentUser}) {
  const topology = selectedTopology(session);
  const selected = topology?.links.find((item) => item.id === session.selectedLinkId);
  const [error, setError] = useState(''); const call = (action, link) => Promise.resolve(invoke(
    `replication.link.${action}`, {topologyId: topology.id, linkId: link.id}
  )).catch((exception) => setError(exception.message));
  const latest = new Map(); (topology?.lagSamples ?? []).forEach((sample) => latest.set(sample.linkId, sample));
  const rows = (topology?.links ?? []).map((item) => ({...item,
    source: item.sourceParticipantId, target: item.targetParticipantId,
    lag: latest.has(item.id) ? `${latest.get(item.id).value} ${latest.get(item.id).unit}` : 'unknown',
    checkpointText: JSON.stringify(item.checkpoint), errorsText: item.errors.join(' ')}));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Replication link controls">
      <Button disabled={!selected || !liveAllowed(session, currentUser, 'replication.control')}
        onClick={() => call('pause', selected)}>Pause selected link</Button>
      <Button disabled={!selected || !liveAllowed(session, currentUser, 'replication.control')}
        onClick={() => call('resume', selected)}>Resume selected link</Button>
    </Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    {!rows.length ? <EmptyState message="Discover a topology to inspect replication links." /> :
      <DataGrid gridId="replication/links" aria-label="Replication links" rows={rows} readOnly
        enableRowSelect rowKeyGetter={(row) => row.id}
        onItemSelect={(row) => row && service.select(session.id, {linkId: row.id})}
        columns={[{key: 'name', name: 'Link'}, {key: 'source', name: 'Source'},
          {key: 'target', name: 'Target'}, {key: 'mechanism', name: 'Native mechanism'},
          {key: 'nativeState', name: 'Native state'}, {key: 'lag', name: 'Lag'},
          {key: 'checkpointText', name: 'Checkpoint'}, {key: 'errorsText', name: 'Errors'}]} />}
  </Box>;
}
LinkInspector.propTypes = {session: PropTypes.object, service: PropTypes.object,
  invoke: PropTypes.func, currentUser: PropTypes.object};

function LagHistory({session}) {
  const topology = selectedTopology(session); const samples = topology?.lagSamples ?? [];
  const maximum = Math.max(1, ...samples.map((item) => item.value));
  const points = samples.map((item, index) => `${20 + index * 50},${180 - item.value / maximum * 150}`)
    .join(' ');
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    {!samples.length ? <EmptyState message="No provider-reported or adapter-calculated lag samples." /> : <>
      <Box component="svg" role="img" aria-label={`Replication lag history: ${samples.length} samples`}
        viewBox={`0 0 ${Math.max(300, samples.length * 50 + 40)} 200`} sx={{height: 220}}>
        <title>Replication lag history</title><polyline points={points} fill="none"
          stroke="currentColor" strokeWidth="2" />
      </Box>
      <DataGrid gridId="replication/lag-history" aria-label="Replication lag history table"
        rows={samples} readOnly rowKeyGetter={(row) => row.id}
        columns={[{key: 'linkId', name: 'Link'}, {key: 'observedAt', name: 'Observed'},
          {key: 'value', name: 'Value'}, {key: 'unit', name: 'Unit'},
          {key: 'source', name: 'Source'}]} />
    </>}
  </Box>;
}
LagHistory.propTypes = {session: PropTypes.object};

function FailoverPlanner({session, invoke, currentUser}) {
  const topology = selectedTopology(session); const [error, setError] = useState('');
  const initial = session.content.failoverPlans.find((item) => item.topologyId === topology?.id);
  const [draft, setDraft] = useState({id: initial?.id ?? '', name: initial?.name ?? '',
    candidateParticipantId: initial?.candidateParticipantId ?? '', targetRole: initial?.targetRole ?? '',
    preconditions: JSON.stringify(initial?.preconditions ?? [], null, 2),
    commands: JSON.stringify(initial?.commands ?? [], null, 2),
    expectedTopology: JSON.stringify(initial?.expectedTopology ?? {}, null, 2),
    verification: JSON.stringify(initial?.verification ?? [], null, 2),
    rollback: JSON.stringify(initial?.rollback ?? [], null, 2), dataLossRisk: initial?.dataLossRisk ?? '',
    estimatedRPO: JSON.stringify(initial?.estimatedRPO ?? null),
    estimatedRTO: JSON.stringify(initial?.estimatedRTO ?? null), nativeDetails: '{}',
    confirmationRef: '', environment: '', connection: ''});
  const plan = () => validateFailoverPlan({id: draft.id, name: draft.name,
    topologyId: topology.id, candidateParticipantId: draft.candidateParticipantId,
    targetRole: draft.targetRole, preconditions: json(draft.preconditions, 'Preconditions', []),
    commands: json(draft.commands, 'Commands', []),
    expectedTopology: json(draft.expectedTopology, 'Expected topology'),
    verification: json(draft.verification, 'Verification', []),
    rollback: json(draft.rollback, 'Rollback', []), dataLossRisk: draft.dataLossRisk,
    estimatedRPO: optionalJson(draft.estimatedRPO, 'Estimated RPO'),
    estimatedRTO: optionalJson(draft.estimatedRTO, 'Estimated RTO'),
    nativeDetails: json(draft.nativeDetails, 'Failover native details')});
  const call = (command, args) => { try { setError(''); Promise.resolve(invoke(command, args))
    .catch((exception) => setError(exception.message)); } catch(exception) { setError(exception.message); }};
  if(!topology) return <EmptyState message="Select a topology before planning failover." />;
  const savedPlan = session.content.failoverPlans.find((item) => item.id === draft.id);
  const review = session.failoverReviews.find((item) => item.planId === draft.id);
  return <Box sx={{height: '100%', overflow: 'auto', p: 1}}>
    <Banner status="warning">Low lag alone never proves promotion safety. Provider quorum,
      candidate, position and data-loss checks must all pass.</Banner>
    {error && <Banner status="error">{error}</Banner>}
    <Box sx={{display: 'grid', gap: 1, gridTemplateColumns: 'repeat(3,minmax(190px,1fr))'}}>
      <TextField label="Plan ID" value={draft.id}
        onChange={(event) => setDraft({...draft, id: event.target.value})} />
      <TextField label="Plan name" value={draft.name}
        onChange={(event) => setDraft({...draft, name: event.target.value})} />
      <Select label="Candidate participant" value={draft.candidateParticipantId}
        options={topology.participants.map((item) => ({value: item.id,
          label: `${item.name} · ${item.nativeRole}`}))}
        onChange={(value) => setDraft({...draft, candidateParticipantId: value})} />
      <TextField label="Provider-native target role" value={draft.targetRole}
        onChange={(event) => setDraft({...draft, targetRole: event.target.value})} />
      <TextArea label="Preconditions JSON" value={draft.preconditions}
        onChange={(event) => setDraft({...draft, preconditions: event.target.value})} />
      <TextArea label="Provider commands JSON" value={draft.commands}
        onChange={(event) => setDraft({...draft, commands: event.target.value})} />
      <TextArea label="Expected topology JSON" value={draft.expectedTopology}
        onChange={(event) => setDraft({...draft, expectedTopology: event.target.value})} />
      <TextArea label="Verification JSON" value={draft.verification}
        onChange={(event) => setDraft({...draft, verification: event.target.value})} />
      <TextArea label="Rollback JSON" value={draft.rollback}
        onChange={(event) => setDraft({...draft, rollback: event.target.value})} />
      <TextArea label="Data-loss risk" value={draft.dataLossRisk}
        onChange={(event) => setDraft({...draft, dataLossRisk: event.target.value})} />
      <TextArea label="Estimated RPO JSON" value={draft.estimatedRPO}
        onChange={(event) => setDraft({...draft, estimatedRPO: event.target.value})} />
      <TextArea label="Estimated RTO JSON" value={draft.estimatedRTO}
        onChange={(event) => setDraft({...draft, estimatedRTO: event.target.value})} />
      <TextArea label="Provider-native failover details JSON" value={draft.nativeDetails}
        onChange={(event) => setDraft({...draft, nativeDetails: event.target.value})} />
      <Button disabled={!authoredEditAllowed(session, currentUser,
        'replication.plan_failover')}
      onClick={() => { try { call('replication.failover.plan', {plan: plan()}); }
      catch(exception) { setError(exception.message); }}}>Save reviewable plan</Button>
      <Button disabled={!savedPlan || !liveAllowed(session, currentUser,
        'replication.plan_failover')} onClick={() => call('replication.failover.validate',
        {planId: draft.id})}>Validate plan</Button>
      <TextField label="Confirmation reference" value={draft.confirmationRef}
        onChange={(event) => setDraft({...draft, confirmationRef: event.target.value})} />
      <TextField label="Target environment" value={draft.environment}
        onChange={(event) => setDraft({...draft, environment: event.target.value})} />
      <TextField label="Target connection" value={draft.connection}
        onChange={(event) => setDraft({...draft, connection: event.target.value})} />
      <Button disabled={!review?.valid || !permitted(currentUser, 'replication.execute_failover')}
        onClick={() => call('replication.failover.arm', {planId: draft.id,
          confirmationRef: draft.confirmationRef, environment: draft.environment,
          connection: draft.connection})}>Arm exact plan revision</Button>
      <Button intent="destructive" disabled={session.armedPlan?.planId !== draft.id ||
        !liveAllowed(session, currentUser, 'replication.execute_failover')}
      onClick={() => call('replication.failover.execute', {planId: draft.id})}>
        Execute armed failover</Button>
    </Box>
    {review && <Banner status={review.valid ? 'success' : 'error'}>
      Provider safety review: {review.valid ? 'all required evidence passed' : review.details.join(' ')}
    </Banner>}
  </Box>;
}
FailoverPlanner.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object};

function Events({session}) {
  const rows = session.liveTopologies.flatMap((item) => item.events);
  return !rows.length ? <EmptyState message="No provider topology changes have been observed." /> :
    <DataGrid gridId="replication/events" aria-label="Replication topology events" rows={rows}
      readOnly rowKeyGetter={(row) => row.id} columns={[{key: 'occurredAt', name: 'Time'},
        {key: 'type', name: 'Type'}, {key: 'topologyId', name: 'Topology'},
        {key: 'participantId', name: 'Participant'}, {key: 'linkId', name: 'Link'},
        {key: 'cause', name: 'Cause'}]} />;
}
Events.propTypes = {session: PropTypes.object};

export function ReplicationWorkspace({service, sessionId, surface='topology_explorer',
  executeCommand, currentUser={}, onStateChange}) {
  const session = useSession(service, sessionId); const [active, setActive] = useState(surface);
  const [filter, setFilter] = useState('');
  const invoke = (command, args={}) => executeCommand ? executeCommand(command, args) :
    Promise.reject(new Error('Replication command authority is unavailable.'));
  useEffect(() => onStateChange?.(session), [onStateChange, session]);
  const body = useMemo(() => {
    const common = {session, service, invoke, currentUser, onNavigate: setActive};
    if(active === 'participant_inspector') return <ParticipantInspector {...common} />;
    if(active === 'replication_link_inspector') return <LinkInspector {...common} />;
    if(active === 'lag_history') return <LagHistory {...common} />;
    if(active === 'failover_planner') return <FailoverPlanner {...common} />;
    if(active === 'events') return <Events {...common} />;
    return <TopologyExplorer {...common} />;
  }, [active, session]);
  return <Box sx={{height: '100%', display: 'grid', gridTemplateColumns: '288px minmax(0,1fr)'}}>
    <Box component="nav" aria-label="REPLICATION" sx={{overflow: 'auto', borderRight: '1px solid',
      borderColor: 'divider'}}>
      <SearchField label="Filter replication surfaces" value={filter} onChange={setFilter} />
      {REPLICATION_SURFACES.filter((item) => item.title.toLowerCase().includes(filter.toLowerCase()))
        .map((item) => <TreeRow key={item.id} id={item.id} label={item.title} level={0}
          selected={active === item.id} onSelect={() => { setActive(item.id);
            service.select(sessionId, {surface: item.id}); }} />)}
    </Box>
    <StateBoundary session={session}><Box component="main" sx={{height: '100%', minWidth: 0}}>
      {body}</Box></StateBoundary>
  </Box>;
}
ReplicationWorkspace.propTypes = {service: PropTypes.object, sessionId: PropTypes.string,
  surface: PropTypes.string, executeCommand: PropTypes.func, currentUser: PropTypes.object,
  onStateChange: PropTypes.func};

export function ReplicationNavigator({service, onOpen}) {
  const [query, setQuery] = useState(''); const [sessions, setSessions] = useState(service.list());
  useEffect(() => service.subscribe(() => setSessions(service.list())), [service]);
  const filtered = sessions.filter((item) => `${item.content.name} ${item.id}`
    .toLowerCase().includes(query.toLowerCase()));
  return <Box><SearchField label="Filter replication assets" value={query} onChange={setQuery} />
    {!filtered.length && <EmptyState message="No replication assets are open." />}
    {filtered.map((session) => <TreeRow key={session.id} id={session.id}
      label={session.content.name || session.id} level={0} trailing={<Badge label={session.state} />}
      onSelect={() => onOpen?.(session.id, 'topology_explorer')}
      onOpen={() => onOpen?.(session.id, 'topology_explorer')} />)}
  </Box>;
}
ReplicationNavigator.propTypes = {service: PropTypes.object, onOpen: PropTypes.func};

export function replicationInspector(session) {
  const topology = selectedTopology(session);
  const participant = topology?.participants.find((item) => item.id === session.selectedParticipantId);
  const link = topology?.links.find((item) => item.id === session.selectedLinkId);
  return {topology: topology?.name ?? null, participant: participant ? {name: participant.name,
    normalizedRole: participant.normalizedRole, nativeRole: participant.nativeRole,
    nativeState: participant.nativeState, position: participant.position} : null,
  link: link ? {name: link.name, mechanism: link.mechanism, nativeState: link.nativeState,
    position: link.position, health: link.health.overall} : null};
}

export function replaceTopologyDefinition(session, topology) {
  return createReplicationContent({...session.content, savedTopologies: [
    ...session.content.savedTopologies.filter((item) => item.id !== topology.id), topology,
  ]});
}
