/////////////////////////////////////////////////////////////
// Provider-aware distributed tracing workbench surfaces.
/////////////////////////////////////////////////////////////

import {useEffect, useState} from 'react';
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
import {criticalPath, traceDuration} from './TracingEngine';

export const TRACING_SURFACES = Object.freeze([
  {id: 'trace_search', title: 'Trace Search'},
  {id: 'trace_waterfall', title: 'Trace Waterfall'},
  {id: 'span_inspector', title: 'Span Inspector'},
  {id: 'service_resource_map', title: 'Service / Resource Map'},
  {id: 'query_correlation', title: 'Query Correlation'},
  {id: 'ingestion_sampling', title: 'Ingestion & Sampling'},
]);

function useSession(service, id) {
  const [session, setSession] = useState(() => service.get(id));
  useEffect(() => service.subscribe((next) => { if(next.id === id) setSession(next); }),
    [service, id]); return session;
}
function permitted(user, permission) { return user.permissions?.includes(permission) === true; }
function safeRead(session, user) {
  return permitted(user, 'trace.view') && !['loading', 'empty', 'permission_denied',
    'disconnected', 'background_task_active'].includes(session.state);
}
function authoredEdit(session, user, permission) {
  return permitted(user, permission) && !['loading', 'read_only', 'permission_denied',
    'background_task_active'].includes(session.state);
}
function selectedTrace(session) {
  return session.result.items.find((trace) => trace.traceId === session.selectedTraceId) ?? null;
}
function selectedSpan(session) {
  const trace = selectedTrace(session);
  return trace?.spans.find((span) => span.spanId === session.selectedSpanId) ?? null;
}

function StateBoundary({session, children}) {
  const limitations = session.sourceStatuses.flatMap((item) => [
    ...(item.warnings ?? []), ...(item.limitations ?? []),
  ]);
  return <>
    {session.state === 'loading' && <Box sx={{p: 2}}><Skeleton lines={8} /></Box>}
    {session.state === 'empty' && <EmptyState
      message="No tracing source or saved search is configured. Configure a source to begin." />}
    {session.state === 'background_task_active' && <Box sx={{p: 1}}>
      <ProgressBar label="Tracing background task" status="indeterminate" />
    </Box>}
    {session.error && <Box sx={{p: 1}}><Banner status="error">{session.error}</Banner></Box>}
    {!session.error && session.state === 'permission_denied' && <Box sx={{p: 1}}>
      <Banner status="error">Permission denied. Provider support remains a separate state.</Banner>
    </Box>}
    {['stale', 'partial', 'disconnected', 'read_only', 'validation_error']
      .includes(session.state) && <Box sx={{p: 1}}><Banner status="warning">
        Distributed Tracing is {session.state.replaceAll('_', ' ')}. Last safe data remains visible.
    </Banner></Box>}
    {limitations.length > 0 && <Box sx={{p: 1}}><Banner status="warning">
      Provider limitations: {[...new Set(limitations)].join(' ')}
    </Banner></Box>}
    {children}
  </>;
}
StateBoundary.propTypes = {session: PropTypes.object, children: PropTypes.node};

function TraceSearch({session, invoke, currentUser, onNavigate}) {
  const [query, setQuery] = useState(''); const [status, setStatus] = useState('');
  const [serviceName, setServiceName] = useState(''); const [database, setDatabase] = useState('');
  const [error, setError] = useState('');
  const run = () => Promise.resolve(invoke('trace.search', {filters: {
    text: query || undefined, status: status || undefined, service: serviceName || undefined,
    database: database || undefined}, pageSize: 100})).catch((exception) => setError(exception.message));
  const rows = session.result.items.map((trace) => ({traceId: trace.traceId,
    rootOperation: trace.spans.find((span) => span.spanId === trace.rootSpanId)?.name ?? '',
    startTime: trace.startTime, duration: traceDuration(trace), status: trace.status,
    sourceId: trace.sourceId, spans: trace.spans.length}));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Trace search controls">
      <SearchField label="Trace text filter" value={query} onChange={setQuery} />
      <TextField label="Service" value={serviceName} onChange={(event) => setServiceName(event.target.value)} />
      <TextField label="Database" value={database} onChange={(event) => setDatabase(event.target.value)} />
      <Select label="Status" value={status} onChange={setStatus}
        options={[{label: 'All', value: ''}, {label: 'Unset', value: 'UNSET'},
          {label: 'OK', value: 'OK'}, {label: 'Error', value: 'ERROR'}]} />
      <Button disabled={!safeRead(session, currentUser)} onClick={run}>Search</Button>
    </Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    <Box sx={{px: 1}}>Searchable range: {session.result.searchableRange.from ?? 'unknown'} — {
      session.result.searchableRange.to ?? 'unknown'} · redacted {session.result.redacted} · rejected {
      session.result.rejected}</Box>
    {!rows.length ? <EmptyState message="No traces match the bounded search." /> :
      <DataGrid gridId="tracing/search" aria-label="Trace search results" rows={rows} readOnly
        enableRowSelect rowKeyGetter={(row) => row.traceId}
        onItemEnter={(row) => {
          if(!row) return;
          Promise.resolve(invoke('trace.open', {traceId: row.traceId}))
            .then(() => onNavigate('trace_waterfall')).catch((exception) => setError(exception.message));
        }}
        columns={[{key: 'traceId', name: 'Trace ID', renderCell: ({row}) =>
          <Button onClick={() => Promise.resolve(invoke('trace.open', {traceId: row.traceId}))
            .then(() => onNavigate('trace_waterfall')).catch((exception) => setError(exception.message))}>
            Open trace {row.traceId}
          </Button>}, {key: 'rootOperation', name: 'Root operation'},
        {key: 'startTime', name: 'Start'}, {key: 'duration', name: 'Duration (ms)'},
        {key: 'status', name: 'Status'}, {key: 'spans', name: 'Spans'},
        {key: 'sourceId', name: 'Source'}]} />}
    {session.result.nextCursor && <Button disabled={!safeRead(session, currentUser)}
      onClick={() => invoke('trace.search', {filters: {}, cursor: session.result.nextCursor,
        pageSize: 100})}>Next page</Button>}
  </Box>;
}
TraceSearch.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object, onNavigate: PropTypes.func};

function TraceWaterfall({session, service, onNavigate}) {
  const trace = selectedTrace(session); const [collapsed, setCollapsed] = useState(new Set());
  if(!trace) return <EmptyState message="Open a trace from Trace Search to view its waterfall." />;
  const start = Date.parse(trace.startTime); const total = Math.max(1, traceDuration(trace));
  const critical = new Set(criticalPath(trace).spans);
  const byParent = new Map(); trace.spans.forEach((span) => byParent.set(span.parentSpanId,
    [...(byParent.get(span.parentSpanId) ?? []), span]));
  const visible = [];
  function append(span, depth) {
    visible.push({span, depth});
    if(!collapsed.has(span.spanId)) (byParent.get(span.spanId) ?? []).forEach((child) => append(child, depth + 1));
  }
  append(trace.spans.find((span) => span.spanId === trace.rootSpanId), 0);
  const toggle = (spanId) => setCollapsed((current) => {
    const next = new Set(current); if(next.has(spanId)) next.delete(spanId); else next.add(spanId); return next;
  });
  return <Box sx={{height: '100%', overflow: 'auto'}}>
    <Toolbar label="Trace waterfall controls">
      <Button onClick={() => setCollapsed(new Set())}>Expand all</Button>
      <Button onClick={() => setCollapsed(new Set(trace.spans.map((span) => span.spanId)))}>Collapse all</Button>
      <Button onClick={() => onNavigate('span_inspector')}>Inspect span</Button>
    </Toolbar>
    <Box role="tree" aria-label="Trace waterfall">
      {visible.map(({span, depth}) => {
        const left = (Date.parse(span.startTime) - start) / total * 100;
        const width = Math.max(0.5, (Date.parse(span.endTime) - Date.parse(span.startTime)) / total * 100);
        return <Box role="treeitem" aria-level={depth + 1} key={span.spanId}
          aria-selected={session.selectedSpanId === span.spanId} tabIndex={0}
          onClick={() => service.select(session.id, {spanId: span.spanId})}
          onKeyDown={(event) => { if(event.key === 'Enter') service.select(session.id, {spanId: span.spanId}); }}
          sx={{display: 'grid', gridTemplateColumns: '240px 1fr', minHeight: 34, alignItems: 'center'}}>
          <Button onClick={(event) => { event.stopPropagation(); toggle(span.spanId); }}
            aria-label={`${collapsed.has(span.spanId) ? 'Expand' : 'Collapse'} ${span.name}`}>
            {' '.repeat(depth * 2)}{collapsed.has(span.spanId) ? '▸' : '▾'} {span.name}
          </Button>
          <Box sx={{position: 'relative', height: 22}}><Box title={`${span.name}: ${span.startTime} — ${span.endTime}`}
            sx={{position: 'absolute', left: `${left}%`, width: `${width}%`, minWidth: 2,
              height: 18, border: critical.has(span.spanId) ? '2px solid currentColor' : '1px solid currentColor'}} /></Box>
        </Box>;
      })}
    </Box>
  </Box>;
}
TraceWaterfall.propTypes = {session: PropTypes.object, service: PropTypes.object,
  onNavigate: PropTypes.func};

function SpanInspector({session, invoke}) {
  const span = selectedSpan(session); const [error, setError] = useState('');
  if(!span) return <EmptyState message="Select a span in the trace waterfall." />;
  const attributes = Object.entries(span.attributes).map(([key, value]) => ({id: key, key,
    value: typeof value === 'string' ? value : JSON.stringify(value)}));
  return <Box sx={{height: '100%', display: 'grid', gridTemplateRows: 'auto 1fr'}}>
    <Toolbar label="Span actions"><Button onClick={() => invoke('trace.copy_id',
      {traceId: span.traceId, spanId: span.spanId})}>Copy span ID</Button>
    <Button onClick={() => invoke('trace.resource.open', {traceId: span.traceId,
      spanId: span.spanId}).catch((exception) => setError(exception.message))}>Open resource</Button></Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    <Box sx={{overflow: 'auto', p: 1}}><Badge label={`${span.kind} · ${span.status}`} />
      <Box>Name: {span.name}</Box><Box>Span: {span.spanId}</Box><Box>Parent: {span.parentSpanId ?? 'root'}</Box>
      <DataGrid gridId="tracing/span-attributes" aria-label="Span attributes" rows={attributes} readOnly
        rowKeyGetter={(row) => row.id} columns={[{key: 'key', name: 'Attribute'},
          {key: 'value', name: 'Value'}]} />
      <Box component="h3">Events</Box><pre>{JSON.stringify(span.events, null, 2)}</pre>
      <Box component="h3">Links</Box><pre>{JSON.stringify(span.links, null, 2)}</pre>
      <Box component="h3">Resource</Box><pre>{JSON.stringify(span.resource, null, 2)}</pre>
    </Box>
  </Box>;
}
SpanInspector.propTypes = {session: PropTypes.object, invoke: PropTypes.func};

function ServiceResourceMap({session, service, currentUser}) {
  const [from, setFrom] = useState(session.result.searchableRange.from ?? '');
  const [to, setTo] = useState(session.result.searchableRange.to ?? '');
  const map = session.serviceMap;
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Observed service map controls">
      <TextField label="Observed from" value={from} onChange={(event) => setFrom(event.target.value)} />
      <TextField label="Observed to" value={to} onChange={(event) => setTo(event.target.value)} />
      <Button disabled={!safeRead(session, currentUser) || !from || !to}
        onClick={() => service.aggregateMap(session.id, {from, to}, {currentUser})}>Aggregate</Button>
    </Toolbar>
    {!map ? <EmptyState message="Aggregate selected traces for an observed-time-window map." /> : <>
      <Banner status="info">{map.label}. This is observed evidence, not timeless topology.</Banner>
      <Box sx={{flex: 1, minHeight: 300}}><GraphSurface label="Observed service resource map"
        nodes={map.nodes.map((node) => ({...node, kind: 'observed service', namespace: `${node.traceCount} spans`}))}
        edges={map.edges.map((edge) => ({...edge, type: `${edge.observedCalls} observed calls`}))} mode="graph" /></Box>
      <DataGrid gridId="tracing/service-map" aria-label="Observed service map table" readOnly
        rows={map.edges} rowKeyGetter={(row) => row.id}
        columns={[{key: 'from', name: 'Source'}, {key: 'to', name: 'Target'},
          {key: 'observedCalls', name: 'Observed calls'}, {key: 'errorCount', name: 'Errors'}]} />
    </>}
  </Box>;
}
ServiceResourceMap.propTypes = {session: PropTypes.object, service: PropTypes.object,
  currentUser: PropTypes.object};

function QueryCorrelation({session, invoke}) {
  const trace = selectedTrace(session); const rows = (trace?.spans ?? []).flatMap((span) =>
    span.correlationRefs.map((reference, index) => ({id: `${span.spanId}:${index}`,
      spanId: span.spanId, spanName: span.name, schema: reference.schema,
      reference: reference.canonical ?? reference.id ?? `${reference.projectId}/${reference.assetId}`,
      raw: reference})));
  const [error, setError] = useState('');
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    {error && <Banner status="error">{error}</Banner>}
    {!rows.length ? <EmptyState message="The selected trace has no evidenced CDEadmin correlations." /> :
      <DataGrid gridId="tracing/query-correlation" aria-label="Trace query correlations" rows={rows}
        readOnly enableRowSelect rowKeyGetter={(row) => row.id}
        onItemEnter={(row) => {
          if(!row) return;
          invoke(row.schema === 'cdeadmin.resource-ref.v1' ? 'trace.resource.open' :
            'trace.query.open', {traceId: trace.traceId, spanId: row.spanId})
            .catch((exception) => setError(exception.message));
        }}
        columns={[{key: 'spanName', name: 'Span'}, {key: 'spanId', name: 'Span ID'},
          {key: 'schema', name: 'Reference type'}, {key: 'reference', name: 'Reference'}]} />}
  </Box>;
}
QueryCorrelation.propTypes = {session: PropTypes.object, invoke: PropTypes.func};

function IngestionSampling({session, invoke, currentUser}) {
  const [sourceText, setSourceText] = useState(JSON.stringify(session.content.sourceConfigs[0] ?? {
    id: 'internal', name: 'CDEadmin internal tracing', sourceType: 'cdeadmin_internal',
    enabled: true, endpoint: null, providerId: null, resourceRef: null, credentialRef: null,
    transport: {}, sensitivityPolicyId: null, nativeDetails: {}}, null, 2));
  const [policyText, setPolicyText] = useState(JSON.stringify(session.content.samplingPolicies[0] ?? {
    id: 'default', name: 'Default safe policy', rate: 1, tailCriteria: {},
    sensitiveAttributePolicy: {captureRawStatements: false, capturePayloads: false},
    retention: {}, enabled: true}, null, 2)); const [error, setError] = useState('');
  const call = (command, text) => {
    try { return Promise.resolve(invoke(command, command.endsWith('configure') ?
      {source: JSON.parse(text)} : {policy: JSON.parse(text)})).catch(
      (exception) => setError(exception.message));
    } catch(exception) { setError(exception.message); return null; }
  };
  const rows = session.content.sourceConfigs.map((source) => ({...source,
    status: session.sourceStatuses.find((item) => item.sourceId === source.id)?.supportState ?? 'unknown',
    counts: JSON.stringify(session.sourceCounters.find((item) => item.sourceId === source.id) ?? {})}));
  return <Box sx={{height: '100%', overflow: 'auto', p: 1}}>
    {error && <Banner status="error">{error}</Banner>}
    <DataGrid gridId="tracing/sources" aria-label="Trace ingestion sources" rows={rows} readOnly
      rowKeyGetter={(row) => row.id} columns={[{key: 'name', name: 'Source'},
        {key: 'sourceType', name: 'Type'}, {key: 'status', name: 'Support'},
        {key: 'counts', name: 'Accepted / dropped / rejected / redacted'}]} />
    <Box component="fieldset"><legend>Trace source configuration</legend>
      <TextArea label="Source JSON" value={sourceText} onChange={(event) => setSourceText(event.target.value)} />
      <Button disabled={!authoredEdit(session, currentUser, 'trace.configure_source')}
        onClick={() => call('trace.source.configure', sourceText)}>Validate and save source</Button>
    </Box>
    <Box component="fieldset"><legend>Sampling and sensitivity policy</legend>
      <TextArea label="Sampling policy JSON" value={policyText} onChange={(event) => setPolicyText(event.target.value)} />
      <Button disabled={!authoredEdit(session, currentUser, 'trace.admin')}
        onClick={() => call('trace.sampling.update', policyText)}>Validate and save policy</Button>
    </Box>
  </Box>;
}
IngestionSampling.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object};

const SURFACE_COMPONENTS = {trace_search: TraceSearch, trace_waterfall: TraceWaterfall,
  span_inspector: SpanInspector, service_resource_map: ServiceResourceMap,
  query_correlation: QueryCorrelation, ingestion_sampling: IngestionSampling};

export function TracingWorkspace({service, sessionId, surface='trace_search', executeCommand,
  currentUser={}, onStateChange}) {
  const session = useSession(service, sessionId); const [active, setActive] = useState(surface);
  useEffect(() => { setActive(surface); }, [surface]);
  useEffect(() => { onStateChange?.(session); }, [onStateChange, session]);
  const Component = SURFACE_COMPONENTS[active] ?? TraceSearch;
  const navigate = (next) => { setActive(next); service.select(sessionId, {surface: next}); };
  return <Box data-testid="tracing-workspace" sx={{height: '100%', display: 'grid',
    gridTemplateColumns: '288px minmax(0, 1fr) 340px'}}>
    <Box component="nav" aria-label="Tracing surfaces" sx={{overflow: 'auto'}}>
      {TRACING_SURFACES.map((item) => <TreeRow key={item.id} label={item.title}
        selected={active === item.id} onSelect={() => navigate(item.id)} />)}
    </Box>
    <Box component="main" sx={{minWidth: 0, overflow: 'hidden'}}><StateBoundary session={session}>
      <Component session={session} service={service} invoke={executeCommand}
        currentUser={currentUser} onNavigate={navigate} />
    </StateBoundary></Box>
    <Box component="aside" aria-label="Tracing inspector" sx={{overflow: 'auto', p: 1}}>
      <TracingInspector session={session} />
    </Box>
  </Box>;
}
TracingWorkspace.propTypes = {service: PropTypes.object.isRequired, sessionId: PropTypes.string.isRequired,
  surface: PropTypes.string, executeCommand: PropTypes.func.isRequired,
  currentUser: PropTypes.object, onStateChange: PropTypes.func};

export function TracingNavigator({service, onOpen}) {
  const [sessions, setSessions] = useState(() => service.list());
  useEffect(() => service.subscribe(() => setSessions(service.list())), [service]);
  return <Box role="tree" aria-label="Tracing assets">{sessions.length ? sessions.map((session) =>
    <TreeRow key={session.id} label={session.content.name || session.id}
      badge={session.state} onSelect={() => onOpen(session.id, session.selection.surface)} />) :
    <EmptyState message="No tracing assets are open." />}</Box>;
}
TracingNavigator.propTypes = {service: PropTypes.object.isRequired, onOpen: PropTypes.func.isRequired};

function TracingInspector({session}) {
  const trace = selectedTrace(session); const span = selectedSpan(session);
  return <Box><Box component="h3">Span</Box><Box>{span?.name ?? 'No span selected'}</Box>
    <Box component="h3">Resource</Box><pre>{JSON.stringify(span?.resource ?? {}, null, 2)}</pre>
    <Box component="h3">Attributes</Box><pre>{JSON.stringify(span?.attributes ?? {}, null, 2)}</pre>
    <Box component="h3">Events</Box><Box>{span?.events.length ?? 0}</Box>
    <Box component="h3">Links</Box><Box>{span?.links.length ?? 0}</Box>
    <Box component="h3">CDEadmin correlation</Box><Box>{span?.correlationRefs.length ?? 0}</Box>
    {trace && <Badge label={trace.status} />}</Box>;
}
TracingInspector.propTypes = {session: PropTypes.object};

export function tracingInspector(session) {
  const trace = selectedTrace(session); const span = selectedSpan(session);
  return {traceId: trace?.traceId ?? null, spanId: span?.spanId ?? null,
    span: span?.name ?? null, status: span?.status ?? trace?.status ?? null,
    resource: span?.resource ?? {}, attributes: span?.attributes ?? {},
    events: span?.events ?? [], links: span?.links ?? [], correlations: span?.correlationRefs ?? []};
}
