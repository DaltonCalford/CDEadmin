/////////////////////////////////////////////////////////////
// Data Lineage workbench surfaces and accessible graph views.
/////////////////////////////////////////////////////////////

import React, {useEffect, useMemo, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {Button} from '../../primitives/Button';
import {NumberField, TextArea} from '../../primitives/Field';
import {Select} from '../../primitives/Choice';
import {SearchField} from '../../primitives/AdvancedControls';
import {ResourcePicker, TreeRow} from '../../navigation/AdvancedNavigation';
import {Tab} from '../../navigation/TabsAndBreadcrumbs';
import {Toolbar} from '../../layout/WorkbenchChrome';
import {Badge, Banner, ProgressBar, Skeleton} from '../../feedback/Indicators';
import {EmptyState} from '../../feedback/EmptyState';
import DataGrid from '../../data/DataGrid';
import GraphSurface from '../../visualization/GraphSurface';
import {LINEAGE_EDGE_TYPES} from './contracts';

export const LINEAGE_SURFACES = Object.freeze([
  {id: 'lineage_explorer', title: 'Lineage Explorer'},
  {id: 'field_lineage', title: 'Field Lineage'},
  {id: 'impact_analysis', title: 'Impact Analysis'},
  {id: 'timeline_snapshot_compare', title: 'Timeline / Snapshot Compare'},
  {id: 'evidence_inspector', title: 'Evidence Inspector'},
  {id: 'ingestion_status', title: 'Ingestion Status'},
]);

function useSession(service, sessionId) {
  const [session, setSession] = useState(() => service.get(sessionId));
  useEffect(() => service.subscribe((next) => {
    if(next.id === sessionId) setSession(next);
  }), [service, sessionId]);
  return session;
}

function status(state) {
  if(['runtime_failure', 'validation_error', 'permission_denied'].includes(state)) return 'error';
  if(['stale', 'partial', 'disconnected'].includes(state)) return 'warning';
  if(state === 'ready') return 'success';
  return 'info';
}

function StateBoundary({session, children}) {
  return <>
    {session.state === 'background_task_active' && <Box sx={{p: 1}}>
      <ProgressBar label="Lineage background task" status="indeterminate" />
    </Box>}
    {session.state === 'loading' && <Box sx={{p: 2}}><Skeleton lines={8} /></Box>}
    {session.error && <Box sx={{p: 1}}><Banner status="error">{session.error}</Banner></Box>}
    {['stale', 'partial', 'disconnected', 'read_only'].includes(session.state) &&
      <Box sx={{p: 1}}><Banner status="warning">
        Lineage is {session.state.replace('_', ' ')}. Last safe evidence remains visible.
      </Banner></Box>}
    {children}
  </>;
}

StateBoundary.propTypes = {session: PropTypes.object.isRequired, children: PropTypes.node};

function graphRows(graph) {
  return graph.nodes.map((node) => ({...node,
    level: 0, referenceText: node.reference?.canonical ?? node.reference?.id ?? ''}));
}

function LineageExplorer({session, invoke, resources, service}) {
  const [direction, setDirection] = useState('out'); const [depth, setDepth] = useState(1);
  const [mode, setMode] = useState('graph'); const [query, setQuery] = useState('');
  const [picker, setPicker] = useState(false);
  const source = session.traversal ?? {nodes: session.graph.nodes, edges: session.graph.edges,
    overBudget: false};
  const overBudget = source.overBudget || source.nodes.length > 500 || source.edges.length > 1000;
  const nodes = useMemo(() => source.nodes.filter((node) => !query || `${node.name} ${
    node.namespace} ${node.kind}`.toLowerCase().includes(query.toLowerCase())).slice(0, 500),
  [source, query]);
  const visible = new Set(nodes.map((item) => item.id));
  const edges = source.edges.filter((edge) => visible.has(edge.from) && visible.has(edge.to))
    .slice(0, 1000);
  const selected = session.selectedNodeId;
  const trace = () => invoke(direction === 'in' ? 'lineage.node.trace_upstream' :
    'lineage.node.trace_downstream', {nodeId: selected, depth});
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Lineage Explorer commands">
      <Button onClick={() => setPicker(true)}>Select scope</Button>
      <Button disabled={!session.content.scopeRefs.length}
        onClick={() => invoke('lineage.graph.refresh')}>Refresh evidence</Button>
      <Select label="Direction" value={direction} onChange={setDirection}
        options={[{value: 'in', label: 'Upstream'}, {value: 'out', label: 'Downstream'}]} />
      <NumberField label="Depth" value={depth} min={1} max={10}
        onChange={(event) => setDepth(Number(event.target.value))} />
      <Select label="View" value={mode} onChange={setMode}
        options={[{value: 'graph', label: 'Graph'}, {value: 'table', label: 'Table'}]} />
      <Button disabled={!selected} onClick={trace}>Trace selected</Button>
    </Toolbar>
    <Box sx={{p: 1}}><SearchField label="Filter visible lineage" value={query}
      onChange={setQuery} resultCount={nodes.length} /></Box>
    <Box sx={{flex: 1, minHeight: 0}}>
      {!session.graph.nodes.length ? <EmptyState
        message="Select a scope and refresh to discover lineage evidence." /> :
        <GraphSurface nodes={nodes} edges={edges} mode={mode} selectedId={selected}
          onSelect={(nodeId) => service.select(session.id, {nodeId})}
          selectedEdgeId={session.selectedEdgeId}
          onSelectEdge={(edgeId) => service.select(session.id, {edgeId})}
          overBudget={overBudget} label="Lineage Explorer" />}
    </Box>
    <ResourcePicker open={picker} multiple items={resources} selected={[]}
      onClose={() => setPicker(false)} onConfirm={(items) => {
        setPicker(false); invoke('lineage.graph.refresh', {
          scopeRefs: items.map((item) => item.reference),
        });
      }} />
  </Box>;
}

LineageExplorer.propTypes = {session: PropTypes.object.isRequired,
  invoke: PropTypes.func.isRequired, resources: PropTypes.array, service: PropTypes.object};

function FieldLineage({session, service}) {
  const edge = session.graph.edges.find((item) => item.id === session.selectedEdgeId) ??
    session.graph.edges.find((item) => item.from === session.selectedNodeId ||
      item.to === session.selectedNodeId);
  if(!edge) return <EmptyState
    message="Select a lineage edge or a connected node to inspect field transformations." />;
  const rows = edge.fieldLineage.map((item) => ({...item,
    evidenceText: item.evidenceIds.join(', '),
    targetField: item.targetField ?? 'No direct target value'}));
  return <Box sx={{height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', p: 1}}>
    <Box sx={{display: 'flex', gap: 1, alignItems: 'center'}}>
      <Box component="h2" sx={{mr: 'auto'}}>Field Lineage</Box>
      <Badge label={edge.presentationState} status={status(edge.presentationState)} />
      <Button onClick={() => service.select(session.id, {edgeId: edge.id})}>Inspect evidence</Button>
    </Box>
    {!rows.length ? <EmptyState message="This edge has no field-level evidence." /> :
      <Box sx={{flex: 1, minHeight: 0}}><DataGrid gridId="lineage/fields"
        aria-label="Field lineage transformations" readOnly rows={rows}
        rowKeyGetter={(row) => row.id} columns={[
          {key: 'sourceField', name: 'Source field'},
          {key: 'targetField', name: 'Target field'},
          {key: 'transformation', name: 'Transformation'},
          {key: 'expressionRef', name: 'Expression reference'},
          {key: 'evidenceText', name: 'Evidence'},
        ]} /></Box>}
  </Box>;
}

FieldLineage.propTypes = {session: PropTypes.object.isRequired, service: PropTypes.object};

function ImpactAnalysis({session, invoke}) {
  const [depth, setDepth] = useState(10);
  const rows = session.impact?.risk ?? [];
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Impact Analysis commands">
      <NumberField label="Maximum depth" min={1} max={100} value={depth}
        onChange={(event) => setDepth(Number(event.target.value))} />
      <Button disabled={!session.selectedNodeId} onClick={() => invoke('lineage.impact.run', {
        nodeId: session.selectedNodeId, depth,
      })}>Analyze selected node</Button>
    </Toolbar>
    {!session.impact ? <EmptyState
      message="Select a node and run a bounded background impact analysis." /> : <>
      <Box sx={{p: 1, display: 'flex', gap: 1}}>
        <Badge status="error" label={`High ${session.impact.riskSummary.high}`} />
        <Badge status="warning" label={`Medium ${session.impact.riskSummary.medium}`} />
        <Badge status="success" label={`Low ${session.impact.riskSummary.low}`} />
      </Box>
      <Box sx={{flex: 1, minHeight: 0}}><DataGrid gridId="lineage/impact"
        aria-label="Lineage impact results" rows={rows} readOnly
        rowKeyGetter={(row) => row.nodeId} columns={[
          {key: 'nodeId', name: 'Affected node'},
          {key: 'classification', name: 'Classification'}, {key: 'risk', name: 'Risk'},
        ]} /></Box>
    </>}
  </Box>;
}

ImpactAnalysis.propTypes = {session: PropTypes.object.isRequired, invoke: PropTypes.func};

function SnapshotCompare({session, invoke}) {
  const choices = session.snapshots.map((item) => ({value: item.id, label: item.id}));
  const [leftId, setLeft] = useState(''); const [rightId, setRight] = useState('');
  const diff = session.snapshotDiff;
  const rows = diff ? ['nodes', 'edges'].flatMap((kind) =>
    ['added', 'removed', 'changed'].flatMap((change) => diff[kind][change].map((id) => ({
      id: `${kind}:${change}:${id}`, kind, change, objectId: id,
    })))) : [];
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Lineage snapshot commands">
      <Button disabled={!session.graph.nodes.length}
        onClick={() => invoke('lineage.snapshot.create')}>Create snapshot</Button>
      <Select label="Earlier snapshot" value={leftId} options={choices} onChange={setLeft} />
      <Select label="Later snapshot" value={rightId} options={choices} onChange={setRight} />
      <Button disabled={!leftId || !rightId || leftId === rightId}
        onClick={() => invoke('lineage.snapshot.compare', {leftId, rightId})}>Compare</Button>
    </Toolbar>
    {!diff ? <EmptyState message="Create and choose two snapshots to compare graph history." /> :
      <Box sx={{flex: 1, minHeight: 0}}><DataGrid gridId="lineage/snapshot-diff"
        aria-label="Lineage snapshot changes" rows={rows} readOnly
        rowKeyGetter={(row) => row.id} columns={[
          {key: 'kind', name: 'Kind'}, {key: 'change', name: 'Change'},
          {key: 'objectId', name: 'Stable identity'},
        ]} /></Box>}
  </Box>;
}

SnapshotCompare.propTypes = {session: PropTypes.object.isRequired, invoke: PropTypes.func};

function EvidenceInspector({session, invoke}) {
  const edge = session.graph.edges.find((item) => item.id === session.selectedEdgeId);
  const [note, setNote] = useState(''); const [type, setType] = useState('');
  if(!edge) return <EmptyState message="Select an edge to inspect all supporting evidence." />;
  const rows = edge.evidence.map((item) => ({...item,
    detailsText: JSON.stringify(item.details), referenceText: item.reference?.id ?? ''}));
  const inferenceOnly = edge.evidence.every((item) =>
    ['inferred', 'parsed_query'].includes(item.origin));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column', p: 1}}>
    <Box component="h2" sx={{mt: 0}}>Evidence for {edge.type}</Box>
    <Box sx={{display: 'flex', gap: 1, alignItems: 'end'}}>
      <Select label="Curated edge type" value={type || edge.type}
        options={LINEAGE_EDGE_TYPES.map((value) => ({value, label: value}))} onChange={setType} />
      <TextArea label="Curation note" rows={2} value={note}
        onChange={(event) => setNote(event.target.value)} />
      <Button onClick={() => invoke('lineage.edge.curate', {
        edgeId: edge.id, type: type || edge.type, note,
      })}>Curate without deleting evidence</Button>
      <Button disabled={!inferenceOnly || edge.suppressed}
        onClick={() => invoke('lineage.edge.suppress_inference', {edgeId: edge.id})}>
        Suppress inference
      </Button>
    </Box>
    <Box sx={{flex: 1, minHeight: 0, mt: 1}}><DataGrid gridId="lineage/evidence"
      aria-label="Lineage evidence" rows={rows} readOnly rowKeyGetter={(row) => row.id}
      columns={[{key: 'origin', name: 'Origin'}, {key: 'confidence', name: 'Confidence'},
        {key: 'capturedAt', name: 'Captured'}, {key: 'stale', name: 'Stale'},
        {key: 'referenceText', name: 'Reference'}, {key: 'detailsText', name: 'Details'}]} />
    </Box>
  </Box>;
}

EvidenceInspector.propTypes = {session: PropTypes.object.isRequired, invoke: PropTypes.func};

function IngestionStatus({session, invoke, onExport, service}) {
  const [source, setSource] = useState('');
  const rows = session.ingestion.map((item) => ({...item,
    warningText: item.warnings.join('; '), evidenceText: JSON.stringify(item.evidence),
    capabilitiesText: [...(item.readCapabilities ?? []),
      ...(item.writeCapabilities ?? []), ...(item.discoveryCapabilities ?? [])].join(', '),
    limitationsText: (item.limitations ?? []).join('; ')}));
  const importEvents = () => {
    let events;
    try { events = JSON.parse(source); } catch(error) {
      const failure = new Error(`external_format_invalid: ${error.message}`);
      service.reportError(session.id, failure, 'external_format_invalid');
      return undefined;
    }
    return invoke('lineage.import.openlineage', {events});
  };
  return <Box sx={{height: '100%', display: 'grid', gridTemplateRows: 'minmax(180px, 1fr) auto'}}>
    <Box sx={{minHeight: 0}}>{!rows.length ? <EmptyState
      message="No provider, parser, trace, or OpenLineage ingestion has run." /> :
      <DataGrid gridId="lineage/ingestion" aria-label="Lineage ingestion source status"
        rows={rows} readOnly rowKeyGetter={(row) => row.providerId}
        columns={[{key: 'providerId', name: 'Source'}, {key: 'supportState', name: 'Support'},
          {key: 'providerVersion', name: 'Version'}, {key: 'warningText', name: 'Warnings'},
          {key: 'capabilitiesText', name: 'Capabilities'},
          {key: 'limitationsText', name: 'Limitations'},
          {key: 'evidenceText', name: 'Evidence'}]} />}</Box>
    <Box component="section" aria-label="OpenLineage interoperability"
      sx={{p: 1, borderTop: '1px solid', borderColor: 'divider'}}>
      <TextArea fullWidth rows={5} label="OpenLineage RunEvent JSON" value={source}
        onChange={(event) => setSource(event.target.value)} />
      <Box sx={{display: 'flex', gap: 1, mt: 1}}>
        <Button disabled={!source.trim()} onClick={importEvents}>Import OpenLineage</Button>
        <Button disabled={!session.imports.length}
          onClick={() => invoke('lineage.export.openlineage').then(onExport)}>
          Export lossless OpenLineage
        </Button>
      </Box>
    </Box>
  </Box>;
}

IngestionStatus.propTypes = {session: PropTypes.object.isRequired,
  invoke: PropTypes.func, onExport: PropTypes.func, service: PropTypes.object};

const VIEWS = Object.freeze({lineage_explorer: LineageExplorer,
  field_lineage: FieldLineage, impact_analysis: ImpactAnalysis,
  timeline_snapshot_compare: SnapshotCompare, evidence_inspector: EvidenceInspector,
  ingestion_status: IngestionStatus});

export function LineageWorkspace({service, sessionId, surface='lineage_explorer',
  executeCommand, resources=[], onExport=() => {}, onStateChange}) {
  const session = useSession(service, sessionId); const [active, setActive] = useState(surface);
  useEffect(() => onStateChange?.(session), [session, onStateChange]);
  const invoke = async (commandId, args={}) => {
    try { return await executeCommand(commandId, args, {sessionId, service,
      openModuleSurface: setActive}); }
    catch(error) {
      const code = String(error.message).split(':')[0].replaceAll(' ', '_');
      service.reportError(sessionId, error, code); return undefined;
    }
  };
  const View = VIEWS[active] ?? LineageExplorer;
  return <Box data-module="cdeadmin.lineage"
    sx={{height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column'}}>
    <Box role="tablist" aria-label="Data Lineage surfaces"
      sx={{display: 'flex', overflowX: 'auto', borderBottom: '1px solid', borderColor: 'divider'}}>
      {LINEAGE_SURFACES.map((item) => <Tab key={item.id} label={item.title}
        active={active === item.id} attention={item.id === 'ingestion_status' &&
          session.problems.length > 0} onActivate={() => setActive(item.id)} />)}
    </Box>
    <Box sx={{px: 1, py: 0.5, display: 'flex', gap: 1, alignItems: 'center'}}>
      <Badge status={status(session.state)} label={session.state.replaceAll('_', ' ')} />
      <Box>{session.graph.nodes.length} nodes · {session.graph.edges.length} edges</Box>
      {session.dirty && <Badge status="warning" label="Unsaved" />}
    </Box>
    <Box sx={{flex: 1, minHeight: 0, overflow: 'hidden'}}>
      <StateBoundary session={session}><View session={session} invoke={invoke}
        resources={resources} service={service} onExport={onExport} /></StateBoundary>
    </Box>
  </Box>;
}

LineageWorkspace.propTypes = {service: PropTypes.object.isRequired,
  sessionId: PropTypes.string.isRequired,
  surface: PropTypes.oneOf(LINEAGE_SURFACES.map((item) => item.id)),
  executeCommand: PropTypes.func.isRequired, resources: PropTypes.array,
  onExport: PropTypes.func, onStateChange: PropTypes.func};

export function lineageInspector(session) {
  const node = session?.graph.nodes.find((item) => item.id === session.selectedNodeId);
  const edge = session?.graph.edges.find((item) => item.id === session.selectedEdgeId);
  return {'Selected node': node ? `${node.namespace}.${node.name}` : 'None',
    'Origin & confidence': edge ? `${edge.presentationState} · ${edge.confidence}` : 'No edge selected',
    'Field mappings': edge?.fieldLineage.length ?? 0, Evidence: edge?.evidence.length ?? 0,
    Dependents: node ? session.graph.edges.filter((item) => item.from === node.id).length : 0,
    Actions: node || edge ? 'Context commands available' : 'Select a node or edge'};
}

export function LineageNavigator({service, onOpen}) {
  const [sessions, setSessions] = useState(() => service.list());
  const [expanded, setExpanded] = useState(() => new Set());
  useEffect(() => service.subscribe(() => setSessions(service.list())), [service]);
  return <Box role="tree" aria-label="Lineage" sx={{height: '100%', overflow: 'auto'}}>
    {!sessions.length && <EmptyState message="No Lineage assets are open." />}
    {sessions.map((session) => {
      const open = expanded.has(session.id);
      return <React.Fragment key={session.id}>
        <TreeRow label={session.id} level={1} expandable expanded={open}
          trailing={<Badge label={session.state} status={status(session.state)} />}
          onToggle={() => setExpanded((prior) => {
            const next = new Set(prior); open ? next.delete(session.id) : next.add(session.id);
            return next;
          })} onOpen={() => onOpen?.(session.id, 'lineage_explorer')} />
        {open && ['Scopes', 'Sources', 'Saved Views', 'Snapshots', 'Filters'].map((label) =>
          <TreeRow key={label} label={label} level={2}
            trailing={label === 'Snapshots' ? <Badge label={session.snapshots.length} /> : null}
            onOpen={() => onOpen?.(session.id, label === 'Snapshots' ?
              'timeline_snapshot_compare' : 'lineage_explorer')} />)}
      </React.Fragment>;
    })}
  </Box>;
}

LineageNavigator.propTypes = {service: PropTypes.object.isRequired, onOpen: PropTypes.func};

export {graphRows};
