/////////////////////////////////////////////////////////////
// ETL Designer provider-neutral workbench surfaces.
/////////////////////////////////////////////////////////////

import React, {useEffect, useMemo, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {Button} from '../../primitives/Button';
import {NumberField, TextArea, TextField} from '../../primitives/Field';
import {Checkbox, Select} from '../../primitives/Choice';
import {SearchField} from '../../primitives/AdvancedControls';
import {ResourcePicker, SecretPicker, TreeRow} from '../../navigation/AdvancedNavigation';
import {Tab} from '../../navigation/TabsAndBreadcrumbs';
import {Toolbar} from '../../layout/WorkbenchChrome';
import {Badge, Banner, ProgressBar, Skeleton} from '../../feedback/Indicators';
import {EmptyState} from '../../feedback/EmptyState';
import DataGrid from '../../data/DataGrid';
import GraphSurface from '../../visualization/GraphSurface';
import {
  ETL_ERROR_ACTIONS, ETL_EXECUTION_LOCATIONS, ETL_NODE_FAMILIES,
  ETL_PIPELINE_MODES,
} from './contracts';

export const ETL_SURFACES = Object.freeze([
  {id: 'pipeline_designer', title: 'Pipeline Designer'},
  {id: 'mapping_editor', title: 'Mapping Editor'},
  {id: 'schema_propagation', title: 'Schema Propagation'},
  {id: 'preview', title: 'Preview'},
  {id: 'deployment', title: 'Deployment'},
  {id: 'run_monitor', title: 'Run Monitor'},
  {id: 'schedule', title: 'Schedule'},
]);

const TOOLBOX = Object.freeze({Sources: ['source'], Transforms: ['filter', 'project', 'map',
  'join', 'lookup', 'aggregate', 'sort', 'union', 'split', 'deduplicate', 'window', 'script'],
Control: ['checkpoint', 'branch', 'merge', 'custom_provider'], Quality: ['quality_gate'],
Targets: ['sink'], 'Saved Components': []});

function useSession(service, sessionId) {
  const [session, setSession] = useState(() => service.get(sessionId));
  useEffect(() => service.subscribe((next) => {
    if(next.id === sessionId) setSession(next);
  }), [service, sessionId]);
  return session;
}

function permitted(user, permission) {
  return user.permissions?.includes(permission) === true;
}

function json(text, label, fallback={}) {
  if(!String(text ?? '').trim()) return fallback;
  const value = JSON.parse(text);
  if(!value || typeof value !== 'object') throw new TypeError(`${label} must be JSON.`);
  return value;
}

function status(state) {
  if(['runtime_failure', 'validation_error', 'permission_denied'].includes(state)) return 'error';
  if(['stale', 'partial', 'disconnected', 'read_only'].includes(state)) return 'warning';
  return state === 'ready' ? 'success' : 'info';
}

function StateBoundary({session, children}) {
  const limitations = session.providerStatuses.flatMap((item) => [
    ...(item.warnings ?? []), ...(item.limitations ?? []),
  ]);
  return <>
    {session.state === 'background_task_active' && <Box sx={{p: 1}}>
      <ProgressBar label="ETL background task" status="indeterminate" />
    </Box>}
    {session.state === 'loading' && <Box sx={{p: 2}}><Skeleton lines={8} /></Box>}
    {session.error && <Box sx={{p: 1}}><Banner status="error">{session.error}</Banner></Box>}
    {!session.error && session.state === 'permission_denied' && <Box sx={{p: 1}}>
      <Banner status="error">Permission denied. Provider support has not been marked unsupported.</Banner>
    </Box>}
    {!session.error && session.state === 'validation_error' && <Box sx={{p: 1}}>
      <Banner status="error">Pipeline validation failed. Review the Problems surface.</Banner>
    </Box>}
    {['stale', 'partial', 'disconnected', 'read_only'].includes(session.state) &&
      <Box sx={{p: 1}}><Banner status="warning">
        ETL Designer is {session.state.replaceAll('_', ' ')}. Authored pipeline state remains visible.
      </Banner></Box>}
    {limitations.length > 0 && <Box sx={{p: 1}}><Banner status="warning">
      Provider limitations: {[...new Set(limitations)].join(' ')}
    </Banner></Box>}
    {children}
  </>;
}
StateBoundary.propTypes = {session: PropTypes.object.isRequired, children: PropTypes.node};

function defaultPorts(kind) {
  const input = kind === 'source' ? [] : [{id: 'input', name: 'Input', direction: 'input',
    mode: 'either', schemaState: 'unknown', fields: []}];
  const output = kind === 'sink' ? [] : [{id: 'output', name: 'Output', direction: 'output',
    mode: 'either', schemaState: 'unknown', fields: []}];
  return [...input, ...output];
}

function Toolbox({onAdd, allowed}) {
  return <Box aria-label="ETL toolbox" sx={{width: 240, minWidth: 200, overflow: 'auto',
    borderRight: '1px solid', borderColor: 'divider'}}>
    {Object.entries(TOOLBOX).map(([group, kinds]) => <Box key={group} sx={{p: 0.5}}>
      <Box component="h3" sx={{m: 0, py: 0.5, fontSize: 'inherit'}}>{group}</Box>
      {kinds.length ? kinds.map((kind) => <Button key={kind} disabled={!allowed}
        draggable={allowed} onDragStart={(event) => {
          event.dataTransfer.setData('application/x-cdeadmin-etl-node', JSON.stringify({kind,
            label: kind.replaceAll('_', ' ')})); event.dataTransfer.effectAllowed = 'copy';
        }} onClick={() => onAdd(kind)}>{kind.replaceAll('_', ' ')}</Button>) :
        <Box sx={{color: 'text.secondary'}}>No saved components</Box>}
    </Box>)}
  </Box>;
}
Toolbox.propTypes = {onAdd: PropTypes.func, allowed: PropTypes.bool};

function PipelineDesigner({session, invoke, service, currentUser, onNavigate,
  resources, credentialReferences}) {
  const [kind, setKind] = useState('source'); const [name, setName] = useState('');
  const [resourceRef, setResourceRef] = useState(''); const [ports, setPorts] = useState('');
  const [error, setError] = useState(''); const [mode, setMode] = useState('graph');
  const [resourcePicker, setResourcePicker] = useState(false);
  const [credentialPicker, setCredentialPicker] = useState(false);
  const [dragState, setDragState] = useState('idle');
  const [edgeDraft, setEdgeDraft] = useState({id: '', from: '', to: '',
    mappingPolicy: 'explicit', deliveryGuarantee: 'unknown', partitioning: '{}',
    ordering: '{}', nativeDetails: '{}'});
  const [parameter, setParameter] = useState({id: '', name: '', type: 'text',
    required: false, secret: false, credentialRef: null, default: '', description: '',
    nativeDetails: '{}'});
  const [testDraft, setTestDraft] = useState({id: '', name: '', definition: '{}'});
  const [pipelineDraft, setPipelineDraft] = useState({name: session.content.name,
    description: session.content.description, mode: session.content.mode});
  const selectedNode = session.content.nodes.find((item) => item.id === session.selectedNodeId);
  const [nodeDraft, setNodeDraft] = useState(null);
  useEffect(() => setNodeDraft(selectedNode ? {id: selectedNode.id, name: selectedNode.name,
    kind: selectedNode.kind, config: JSON.stringify(selectedNode.config, null, 2),
    resourceRef: selectedNode.resourceRef ? JSON.stringify(selectedNode.resourceRef, null, 2) : '',
    ports: JSON.stringify(selectedNode.ports, null, 2),
    capabilityRequirements: JSON.stringify(selectedNode.capabilityRequirements, null, 2),
    executionPreference: selectedNode.executionPreference,
    errorAction: selectedNode.errorRoute?.action ?? 'none',
    errorMaximumAttempts: selectedNode.errorRoute?.maximumAttempts ?? 0,
    errorTargetRef: selectedNode.errorRoute?.targetRef ? JSON.stringify(
      selectedNode.errorRoute.targetRef, null, 2) : '',
    retryPolicy: JSON.stringify(selectedNode.errorRoute?.retryPolicy ?? {}, null, 2),
    checkpointEnabled: selectedNode.checkpointEnabled,
    nativeDetails: JSON.stringify(selectedNode.nativeDetails, null, 2)} : null),
  [selectedNode]);
  const canEdit = permitted(currentUser, 'etl.edit') && !['read_only', 'permission_denied',
    'background_task_active'].includes(session.state);
  const add = (nodeKind=kind, coordinates=null) => {
    try {
      let sequence = 1;
      while(session.content.nodes.some((item) => item.id === `${nodeKind}-${sequence}`)) sequence++;
      const id = `${nodeKind}-${sequence}`;
      const parsedPorts = ports ? json(ports, 'Ports', []) : defaultPorts(nodeKind);
      const ref = resourceRef ? json(resourceRef, 'ResourceRef') : null;
      const node = {id, name: name || `${nodeKind.replaceAll('_', ' ')} ${
        session.content.nodes.length + 1}`, kind: nodeKind, config: {}, resourceRef: ref,
      ports: parsedPorts, capabilityRequirements: [], executionPreference: 'cdeadmin_runtime',
      checkpointEnabled: nodeKind === 'checkpoint', nativeDetails: {}};
      setError('');
      if(coordinates) invoke('etl.pipeline.create', {content: {...session.content,
        nodes: [...session.content.nodes, node], visualLayout: {
          ...session.content.visualLayout, [id]: coordinates}}});
      else invoke('etl.node.add', {node});
    } catch(exception) { setError(exception.message); }
  };
  const nodes = session.content.nodes.map((node, index) => ({...node,
    level: Math.max(0, session.validation.order.indexOf(node.id)),
    position: session.content.visualLayout[node.id],
    namespace: node.resourceRef?.provider ?? node.executionPreference ?? `step-${index + 1}`}));
  const edges = session.content.edges.map((edge) => ({...edge, from: edge.fromNodeId,
    to: edge.toNodeId, type: edge.mappingPolicy}));
  const outputs = session.content.nodes.flatMap((nodeValue) => nodeValue.ports.filter(
    (item) => item.direction === 'output').map((item) => ({value: `${nodeValue.id}/${item.id}`,
    label: `${nodeValue.name}.${item.name} · ${item.mode}`})));
  const inputs = session.content.nodes.flatMap((nodeValue) => nodeValue.ports.filter(
    (item) => item.direction === 'input').map((item) => ({value: `${nodeValue.id}/${item.id}`,
    label: `${nodeValue.name}.${item.name} · ${item.mode}`})));
  const connect = () => { try { const [fromNodeId, fromPort] = edgeDraft.from.split('/');
    const [toNodeId, toPort] = edgeDraft.to.split('/'); setError('');
    invoke('etl.edge.connect', {edge: {id: edgeDraft.id, fromNodeId, fromPort, toNodeId, toPort,
      mappingPolicy: edgeDraft.mappingPolicy, mappings: [],
      deliveryGuarantee: edgeDraft.deliveryGuarantee,
      partitioning: json(edgeDraft.partitioning, 'Edge partitioning'),
      ordering: json(edgeDraft.ordering, 'Edge ordering'),
      nativeDetails: json(edgeDraft.nativeDetails, 'Edge native details')}});
  } catch(exception) { setError(exception.message); }};
  const saveParameter = () => { try { const value = {...parameter,
    name: parameter.name || parameter.id, default: parameter.secret ? null : parameter.default,
    credentialRef: parameter.secret ? parameter.credentialRef : null,
    description: parameter.description,
    nativeDetails: json(parameter.nativeDetails, 'Parameter native details')};
  const parameters = session.content.parameters.some(
    (item) => item.id === value.id) ? session.content.parameters.map((item) =>
      item.id === value.id ? value : item) : [...session.content.parameters, value]; setError('');
  invoke('etl.pipeline.create', {content: {...session.content, parameters}});
  } catch(exception) { setError(exception.message); }};
  const saveTest = () => { try { const value = {id: testDraft.id,
    name: testDraft.name || testDraft.id,
    definition: json(testDraft.definition, 'Pipeline test definition')};
  const tests = session.content.tests.some((item) => item.id === value.id) ?
    session.content.tests.map((item) => item.id === value.id ? value : item) :
    [...session.content.tests, value]; setError('');
  invoke('etl.pipeline.create', {content: {...session.content, tests}});
  } catch(exception) { setError(exception.message); }};
  const savePipeline = () => { try { setError(''); invoke('etl.pipeline.create', {content: {
    ...session.content, name: pipelineDraft.name, description: pipelineDraft.description,
    mode: pipelineDraft.mode}}); } catch(exception) { setError(exception.message); }};
  const saveNode = () => { try {
    const errorRoute = nodeDraft.errorAction === 'none' ? null : {
      action: nodeDraft.errorAction, maximumAttempts: Number(nodeDraft.errorMaximumAttempts),
      targetRef: nodeDraft.errorTargetRef ? json(nodeDraft.errorTargetRef,
        'Error route target') : null,
      retryPolicy: json(nodeDraft.retryPolicy, 'Retry policy'), nativeDetails: {}};
    const value = {id: nodeDraft.id, name: nodeDraft.name, kind: nodeDraft.kind,
      config: json(nodeDraft.config, 'Node configuration'),
      resourceRef: nodeDraft.resourceRef ? json(nodeDraft.resourceRef, 'ResourceRef') : null,
      ports: json(nodeDraft.ports, 'Ports', []),
      capabilityRequirements: json(nodeDraft.capabilityRequirements,
        'Capability requirements', []), executionPreference: nodeDraft.executionPreference,
      errorRoute, checkpointEnabled: nodeDraft.checkpointEnabled,
      nativeDetails: json(nodeDraft.nativeDetails, 'Native details')};
    setError(''); invoke('etl.pipeline.create', {content: {...session.content,
      nodes: session.content.nodes.map((item) => item.id === value.id ? value : item)}});
  } catch(exception) { setError(exception.message); }};
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="ETL pipeline commands">
      <Button onClick={() => invoke('etl.pipeline.validate')}>Validate</Button>
      <Button disabled={!permitted(currentUser, 'etl.preview') ||
        !permitted(currentUser, 'etl.view_samples')} onClick={() => onNavigate('preview')}>Preview</Button>
      <Button disabled={!permitted(currentUser, 'etl.deploy')} onClick={() => onNavigate('deployment')}>
        Deploy</Button>
      <Button onClick={() => setMode(mode === 'graph' ? 'table' : 'graph')}>
        {mode === 'graph' ? 'Accessible table' : 'Graph view'}</Button>
    </Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    <Box role="region" aria-label="ETL pipeline definition editor"
      sx={{display: 'grid', gap: 1, p: 1, gridTemplateColumns: 'repeat(4,minmax(160px,1fr))'}}>
      <TextField label="Pipeline name" value={pipelineDraft.name}
        onChange={(event) => setPipelineDraft({...pipelineDraft, name: event.target.value})} />
      <TextField label="Pipeline description" value={pipelineDraft.description}
        onChange={(event) => setPipelineDraft({...pipelineDraft, description: event.target.value})} />
      <Select label="Pipeline mode" value={pipelineDraft.mode}
        options={ETL_PIPELINE_MODES.map((value) => ({value,
          label: value.replaceAll('_', ' ')}))}
        onChange={(value) => setPipelineDraft({...pipelineDraft, mode: value})} />
      <Button disabled={!canEdit || !pipelineDraft.name.trim()} onClick={savePipeline}>
        Save pipeline definition</Button>
    </Box>
    <Box sx={{display: 'flex', gap: 1, p: 1, flexWrap: 'wrap'}}>
      <Select label="Node family" value={kind} options={ETL_NODE_FAMILIES.map((value) => ({
        value, label: value.replaceAll('_', ' ')}))} onChange={setKind} />
      <TextField label="Node name" value={name} onChange={(event) => setName(event.target.value)} />
      <TextField label="ResourceRef JSON (provider nodes)" value={resourceRef}
        onChange={(event) => setResourceRef(event.target.value)} />
      <Button onClick={() => setResourcePicker(true)}>Select provider resource</Button>
      <TextField label="Ports JSON (optional)" value={ports}
        onChange={(event) => setPorts(event.target.value)} />
      <Button intent="primary" disabled={!canEdit} onClick={() => add()}>Add node</Button>
    </Box>
    <Box role="region" aria-label="ETL port connection editor"
      sx={{display: 'grid', gap: 1, p: 1, gridTemplateColumns: 'repeat(5,minmax(150px,1fr))'}}>
      <TextField label="Edge ID" value={edgeDraft.id}
        onChange={(event) => setEdgeDraft({...edgeDraft, id: event.target.value})} />
      <Select label="Output port" value={edgeDraft.from} options={outputs}
        onChange={(value) => setEdgeDraft({...edgeDraft, from: value})} />
      <Select label="Input port" value={edgeDraft.to} options={inputs}
        onChange={(value) => setEdgeDraft({...edgeDraft, to: value})} />
      <TextField label="Mapping policy" value={edgeDraft.mappingPolicy}
        onChange={(event) => setEdgeDraft({...edgeDraft, mappingPolicy: event.target.value})} />
      <Select label="Delivery guarantee" value={edgeDraft.deliveryGuarantee}
        options={['unknown', 'best_effort', 'at_most_once', 'at_least_once', 'exactly_once']
          .map((value) => ({value, label: value.replaceAll('_', ' ')}))}
        onChange={(value) => setEdgeDraft({...edgeDraft, deliveryGuarantee: value})} />
      <TextField label="Edge partitioning JSON" value={edgeDraft.partitioning}
        onChange={(event) => setEdgeDraft({...edgeDraft, partitioning: event.target.value})} />
      <TextField label="Edge ordering JSON" value={edgeDraft.ordering}
        onChange={(event) => setEdgeDraft({...edgeDraft, ordering: event.target.value})} />
      <TextField label="Edge native details JSON" value={edgeDraft.nativeDetails}
        onChange={(event) => setEdgeDraft({...edgeDraft, nativeDetails: event.target.value})} />
      <Button disabled={!canEdit || !edgeDraft.id || !edgeDraft.from || !edgeDraft.to}
        onClick={connect}>Connect typed ports</Button>
    </Box>
    <Box role="region" aria-label="ETL parameter editor"
      sx={{display: 'grid', gap: 1, p: 1, gridTemplateColumns: 'repeat(5,minmax(140px,1fr))'}}>
      <TextField label="Parameter ID" value={parameter.id}
        onChange={(event) => setParameter({...parameter, id: event.target.value})} />
      <TextField label="Parameter name" value={parameter.name}
        onChange={(event) => setParameter({...parameter, name: event.target.value})} />
      <TextField label="Parameter type" value={parameter.type}
        onChange={(event) => setParameter({...parameter, type: event.target.value})} />
      <TextField label="Non-secret default" value={parameter.default} disabled={parameter.secret}
        onChange={(event) => setParameter({...parameter, default: event.target.value})} />
      <TextField label="Parameter description" value={parameter.description}
        onChange={(event) => setParameter({...parameter, description: event.target.value})} />
      <TextField label="Parameter native details JSON" value={parameter.nativeDetails}
        onChange={(event) => setParameter({...parameter, nativeDetails: event.target.value})} />
      <Checkbox label="Required parameter" checked={parameter.required}
        onChange={(value) => setParameter({...parameter, required: value})} />
      <Checkbox label="Secret parameter" checked={parameter.secret}
        onChange={(value) => setParameter({...parameter, secret: value,
          default: value ? '' : parameter.default})} />
      <Button disabled={!parameter.secret} onClick={() => setCredentialPicker(true)}>
        Select credential reference</Button>
      <Box>{parameter.credentialRef?.canonical ?? parameter.credentialRef?.id ??
        'No credential reference selected'}</Box>
      <Button disabled={!canEdit || !parameter.id || (parameter.secret &&
        !parameter.credentialRef)} onClick={saveParameter}>Save parameter</Button>
    </Box>
    <Box role="region" aria-label="ETL pipeline test editor"
      sx={{display: 'grid', gap: 1, p: 1, gridTemplateColumns: 'repeat(4,minmax(160px,1fr))'}}>
      <TextField label="Pipeline test ID" value={testDraft.id}
        onChange={(event) => setTestDraft({...testDraft, id: event.target.value})} />
      <TextField label="Pipeline test name" value={testDraft.name}
        onChange={(event) => setTestDraft({...testDraft, name: event.target.value})} />
      <TextArea label="Pipeline test definition JSON" value={testDraft.definition}
        onChange={(event) => setTestDraft({...testDraft, definition: event.target.value})} />
      <Button disabled={!canEdit || !testDraft.id} onClick={saveTest}>Save pipeline test</Button>
    </Box>
    {nodeDraft && <Box role="region" aria-label="ETL selected node editor"
      sx={{display: 'grid', gap: 1, p: 1, maxHeight: 310, overflow: 'auto',
        gridTemplateColumns: 'repeat(4,minmax(170px,1fr))'}}>
      <TextField label="Selected node ID" value={nodeDraft.id} disabled />
      <TextField label="Selected node name" value={nodeDraft.name}
        onChange={(event) => setNodeDraft({...nodeDraft, name: event.target.value})} />
      <Select label="Selected node family" value={nodeDraft.kind}
        options={ETL_NODE_FAMILIES.map((value) => ({value, label: value.replaceAll('_', ' ')}))}
        onChange={(value) => setNodeDraft({...nodeDraft, kind: value})} />
      <Select label="Execution preference" value={nodeDraft.executionPreference}
        options={ETL_EXECUTION_LOCATIONS.map((value) => ({value,
          label: value.replaceAll('_', ' ')}))}
        onChange={(value) => setNodeDraft({...nodeDraft, executionPreference: value})} />
      <TextArea label="Node configuration JSON" value={nodeDraft.config}
        onChange={(event) => setNodeDraft({...nodeDraft, config: event.target.value})} />
      <TextArea label="Node ResourceRef JSON" value={nodeDraft.resourceRef}
        onChange={(event) => setNodeDraft({...nodeDraft, resourceRef: event.target.value})} />
      <TextArea label="Typed ports JSON" value={nodeDraft.ports}
        onChange={(event) => setNodeDraft({...nodeDraft, ports: event.target.value})} />
      <TextArea label="Capability requirements JSON" value={nodeDraft.capabilityRequirements}
        onChange={(event) => setNodeDraft({...nodeDraft,
          capabilityRequirements: event.target.value})} />
      <Select label="Error action" value={nodeDraft.errorAction}
        options={['none', ...ETL_ERROR_ACTIONS].map((value) => ({value,
          label: value.replaceAll('_', ' ')}))}
        onChange={(value) => setNodeDraft({...nodeDraft, errorAction: value})} />
      <NumberField label="Error maximum attempts" min={0} max={100}
        value={nodeDraft.errorMaximumAttempts} onChange={(event) => setNodeDraft({...nodeDraft,
          errorMaximumAttempts: event.target.value})} />
      <TextArea label="Error target reference JSON" value={nodeDraft.errorTargetRef}
        onChange={(event) => setNodeDraft({...nodeDraft, errorTargetRef: event.target.value})} />
      <TextArea label="Retry policy JSON" value={nodeDraft.retryPolicy}
        onChange={(event) => setNodeDraft({...nodeDraft, retryPolicy: event.target.value})} />
      <Checkbox label="Checkpoint enabled" checked={nodeDraft.checkpointEnabled}
        onChange={(value) => setNodeDraft({...nodeDraft, checkpointEnabled: value})} />
      <TextArea label="Provider-native metadata JSON" value={nodeDraft.nativeDetails}
        onChange={(event) => setNodeDraft({...nodeDraft, nativeDetails: event.target.value})} />
      <Button intent="primary" disabled={!canEdit || !nodeDraft.name.trim()}
        onClick={saveNode}>Save selected node</Button>
    </Box>}
    <Box sx={{display: 'flex', flex: 1, minHeight: 0}}>
      <Toolbox allowed={canEdit} onAdd={add} />
      <Box aria-label="Pipeline designer semantic drop target" sx={{flex: 1, minWidth: 0}}
        data-drop-state={dragState} onDragEnter={(event) => setDragState(canEdit &&
          event.dataTransfer.types.includes('application/x-cdeadmin-etl-node') ? 'valid' : 'invalid')}
        onDragLeave={() => setDragState('idle')}
        onDragOver={(event) => { if(canEdit && event.dataTransfer.types.includes(
          'application/x-cdeadmin-etl-node')) { event.preventDefault(); setDragState('valid');
          event.dataTransfer.dropEffect = 'copy'; } else setDragState('invalid'); }}
        onDrop={(event) => { event.preventDefault(); if(!canEdit) return;
          try { const payload = JSON.parse(event.dataTransfer.getData(
            'application/x-cdeadmin-etl-node'));
          if(!ETL_NODE_FAMILIES.includes(payload.kind)) throw new Error('Invalid ETL drag payload.');
          const bounds = event.currentTarget.getBoundingClientRect();
          const clientX = Number.isFinite(Number(event.clientX)) ? Number(event.clientX) : bounds.left;
          const clientY = Number.isFinite(Number(event.clientY)) ? Number(event.clientY) : bounds.top;
          add(payload.kind, {x: Math.max(0, Math.round(clientX - bounds.left)),
            y: Math.max(0, Math.round(clientY - bounds.top))}); setDragState('idle');
          } catch(exception) { setDragState('invalid'); setError(exception.message); }}}>
        {dragState !== 'idle' && <Box role="status">{dragState === 'valid' ?
          'Drop to add this node to the project pipeline.' :
          'This item cannot be dropped on the ETL pipeline.'}</Box>}
        {!nodes.length ? <EmptyState message="Add a source, transformations and a target. Drag and keyboard/button alternatives are available." /> :
          <GraphSurface label="ETL pipeline graph" nodes={nodes} edges={edges} mode={mode}
            selectedId={session.selectedNodeId} selectedEdgeId={session.selectedEdgeId}
            onSelect={(nodeId) => service.select(session.id, {nodeId})}
            onSelectEdge={(edgeId) => service.select(session.id, {edgeId})} />}
      </Box>
    </Box>
    <ResourcePicker open={resourcePicker} items={resources} selected={[]}
      onClose={() => setResourcePicker(false)} onConfirm={(item) => {
        setResourcePicker(false); if(item) setResourceRef(JSON.stringify(item.reference));
      }} />
    <SecretPicker open={credentialPicker} items={credentialReferences} selected={[]}
      onClose={() => setCredentialPicker(false)} onConfirm={(item) => {
        setCredentialPicker(false); if(item) setParameter({...parameter,
          credentialRef: item.reference});
      }} />
  </Box>;
}
PipelineDesigner.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  service: PropTypes.object, currentUser: PropTypes.object, onNavigate: PropTypes.func,
  resources: PropTypes.array, credentialReferences: PropTypes.array};

function MappingEditor({session, invoke, currentUser}) {
  const [edgeId, setEdgeId] = useState(session.selectedEdgeId ?? session.content.edges[0]?.id ?? '');
  const edge = session.content.edges.find((item) => item.id === edgeId);
  const [source, setSource] = useState(''); const [target, setTarget] = useState('');
  const [sourceType, setSourceType] = useState(''); const [semanticType, setSemanticType] = useState('');
  const [targetType, setTargetType] = useState(''); const [conversion, setConversion] = useState('identity');
  const [nullPolicy, setNullPolicy] = useState('preserve'); const [nullable, setNullable] = useState(true);
  const [precision, setPrecision] = useState(''); const [scale, setScale] = useState('');
  const [timezone, setTimezone] = useState(''); const [encoding, setEncoding] = useState('');
  const [nativeDetails, setNativeDetails] = useState('{}');
  const [lossy, setLossy] = useState(false); const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState(''); const rows = edge?.mappings ?? [];
  const add = () => {
    try { const mapping = {id: `${source}->${target}`, source, target,
      sourceNativeType: sourceType || null, semanticType: semanticType || null,
      targetNativeType: targetType || null, conversion, nullPolicy, nullable,
      precision: precision === '' ? null : Number(precision), scale: scale === '' ? null : Number(scale),
      timezone: timezone || null, encoding: encoding || null,
      lossy, lossAcknowledged: acknowledged,
      nativeDetails: json(nativeDetails, 'Mapping native details')};
    setError(''); invoke('etl.mapping.edit', {edgeId, mappings: [...rows.filter(
      (item) => item.id !== mapping.id), mapping]}); } catch(exception) { setError(exception.message); }
  };
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column', p: 1}}>
    {error && <Banner status="error">{error}</Banner>}
    <Select label="Pipeline edge" value={edgeId} options={session.content.edges.map((item) => ({
      value: item.id, label: `${item.fromNodeId}.${item.fromPort} → ${item.toNodeId}.${item.toPort}`}))}
    onChange={setEdgeId} />
    {!edge ? <EmptyState message="Connect two typed ports before editing mappings." /> : <>
      <Box sx={{display: 'grid', gap: 1, gridTemplateColumns: 'repeat(4,minmax(140px,1fr))'}}>
        <TextField label="Source field ID/path" value={source}
          onChange={(event) => setSource(event.target.value)} />
        <TextField label="Target field ID/path" value={target}
          onChange={(event) => setTarget(event.target.value)} />
        <TextField label="Source native type" value={sourceType}
          onChange={(event) => setSourceType(event.target.value)} />
        <TextField label="Semantic type" value={semanticType}
          onChange={(event) => setSemanticType(event.target.value)} />
        <TextField label="Target native type" value={targetType}
          onChange={(event) => setTargetType(event.target.value)} />
        <TextField label="Conversion" value={conversion}
          onChange={(event) => setConversion(event.target.value)} />
        <TextField label="Null policy" value={nullPolicy}
          onChange={(event) => setNullPolicy(event.target.value)} />
        <Checkbox label="Target nullable" checked={nullable} onChange={setNullable} />
        <NumberField label="Precision" value={precision}
          onChange={(event) => setPrecision(event.target.value)} />
        <NumberField label="Scale" value={scale}
          onChange={(event) => setScale(event.target.value)} />
        <TextField label="Timezone" value={timezone}
          onChange={(event) => setTimezone(event.target.value)} />
        <TextField label="Encoding" value={encoding}
          onChange={(event) => setEncoding(event.target.value)} />
        <TextArea label="Mapping native details JSON" value={nativeDetails}
          onChange={(event) => setNativeDetails(event.target.value)} />
        <Checkbox label="Lossy conversion" checked={lossy} onChange={setLossy} />
        <Checkbox label="Loss explicitly acknowledged" checked={acknowledged}
          onChange={setAcknowledged} />
        <Button intent="primary" disabled={!permitted(currentUser, 'etl.edit') || !source ||
          !target || (lossy && !acknowledged)} onClick={add}>Save mapping</Button>
      </Box>
      <Box sx={{flex: 1, minHeight: 160}}><DataGrid gridId="etl/mappings"
        aria-label="ETL source and target field mappings" rows={rows} readOnly
        rowKeyGetter={(row) => row.id} columns={[{key: 'source', name: 'Source'},
          {key: 'sourceNativeType', name: 'Source type'},
          {key: 'semanticType', name: 'Semantic type'}, {key: 'target', name: 'Target'},
          {key: 'targetNativeType', name: 'Target type'}, {key: 'conversion', name: 'Conversion'},
          {key: 'lossy', name: 'Lossy'}, {key: 'lossAcknowledged', name: 'Acknowledged'}]} /></Box>
    </>}
  </Box>;
}
MappingEditor.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object};

function SchemaPropagation({session}) {
  const rows = session.schemaPropagation.records.map((item) => ({...item,
    path: `${item.fromNodeId}.${item.fromPort} → ${item.toNodeId}.${item.toPort}`,
    problemsText: item.problems.join(', ')}));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Box sx={{p: 1}}><Banner status={session.schemaPropagation.compatible ? 'success' : 'error'}>
      {session.schemaPropagation.compatible ? 'Schemas propagate without known incompatibility.' :
        'Schema propagation contains incompatibilities.'}</Banner></Box>
    {!rows.length ? <EmptyState message="Connect pipeline ports to inspect propagated schemas." /> :
      <DataGrid gridId="etl/schema-propagation" aria-label="ETL propagated schemas" rows={rows}
        readOnly rowKeyGetter={(row) => row.id} columns={[{key: 'path', name: 'Edge'},
          {key: 'sourceSchemaState', name: 'Source schema'},
          {key: 'targetSchemaState', name: 'Target schema'},
          {key: 'mappingCount', name: 'Mappings'}, {key: 'state', name: 'State'},
          {key: 'problemsText', name: 'Problems'}]} />}
  </Box>;
}
SchemaPropagation.propTypes = {session: PropTypes.object};

function Preview({session, invoke, currentUser}) {
  const [limit, setLimit] = useState(100); const [scope, setScope] = useState('{}');
  const [error, setError] = useState(''); const preview = session.previews[0];
  const run = () => { try { setError(''); invoke('etl.preview.run', {limit: Number(limit),
    streamScope: json(scope, 'Stream scope')}); } catch(exception) { setError(exception.message); }};
  const output = preview?.stages.flatMap((stage) => Array.isArray(stage.output) ? stage.output.map(
    (value, index) => ({id: `${stage.nodeId}:${index}`, nodeId: stage.nodeId,
      value: JSON.stringify(value)})) : []) ?? [];
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="ETL preview commands"><NumberField label="Record limit" value={limit}
      min={1} max={10000} onChange={(event) => setLimit(event.target.value)} />
    <TextField label="Stream time/partition scope JSON" value={scope}
      onChange={(event) => setScope(event.target.value)} />
    <Button disabled={!permitted(currentUser, 'etl.preview') ||
      !permitted(currentUser, 'etl.view_samples') ||
      session.state === 'background_task_active'} onClick={run}>Run bounded preview</Button></Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    {!preview ? <EmptyState message="Run a bounded, read-only preview. Sink nodes show proposed output only." /> : <>
      <Box sx={{height: '42%'}}><DataGrid gridId="etl/preview-stages" aria-label="ETL preview stages"
        rows={preview.stages} readOnly rowKeyGetter={(row) => row.nodeId} columns={[
          {key: 'nodeId', name: 'Step'}, {key: 'state', name: 'State'},
          {key: 'rows', name: 'Rows'}, {key: 'bytes', name: 'Bytes'},
          {key: 'written', name: 'Sink write performed'}]} /></Box>
      <Box sx={{height: '58%'}}><DataGrid gridId="etl/preview-data" aria-label="ETL preview data"
        rows={output} readOnly rowKeyGetter={(row) => row.id} columns={[
          {key: 'nodeId', name: 'Step'}, {key: 'value', name: 'Bounded sample'}]} /></Box>
    </>}
  </Box>;
}
Preview.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object};

function Deployment({session, invoke, currentUser}) {
  const [id, setId] = useState(session.content.deployments[0]?.id ?? '');
  const deployment = session.content.deployments.find((item) => item.id === id);
  const [name, setName] = useState(''); const [environment, setEnvironment] = useState('development');
  const [bindings, setBindings] = useState('[]'); const [parameters, setParameters] = useState('{}');
  const [resourceLimits, setResourceLimits] = useState('{}');
  const [nativeDetails, setNativeDetails] = useState('{}');
  const [error, setError] = useState('');
  const save = () => { try { const value = {id, name: name || id, environment,
    bindings: json(bindings, 'Deployment bindings', []),
    parameterBindings: json(parameters, 'Parameter bindings'),
    resourceLimits: json(resourceLimits, 'Deployment resource limits'),
    nativeDetails: json(nativeDetails, 'Deployment native details')};
  const values = session.content.deployments.some((item) => item.id === id) ?
    session.content.deployments.map((item) => item.id === id ? value : item) :
    [...session.content.deployments, value]; setError('');
  invoke('etl.pipeline.create', {content: {...session.content, deployments: values}});
  } catch(exception) { setError(exception.message); }};
  return <Box sx={{height: '100%', overflow: 'auto', p: 1}}>
    {error && <Banner status="error">{error}</Banner>}
    <Box sx={{display: 'grid', gap: 1, gridTemplateColumns: 'repeat(3,minmax(180px,1fr))'}}>
      <Select label="Existing deployment" value={id} options={session.content.deployments.map(
        (item) => ({value: item.id, label: `${item.name} · ${item.environment}`}))}
      onChange={(value) => { setId(value); const item = session.content.deployments.find(
        (candidate) => candidate.id === value); if(item) { setName(item.name);
        setEnvironment(item.environment); setBindings(JSON.stringify(item.bindings));
        setParameters(JSON.stringify(item.parameterBindings));
        setResourceLimits(JSON.stringify(item.resourceLimits));
        setNativeDetails(JSON.stringify(item.nativeDetails)); }}} />
      <TextField label="Deployment ID" value={id} onChange={(event) => setId(event.target.value)} />
      <TextField label="Deployment name" value={name}
        onChange={(event) => setName(event.target.value)} />
      <TextField label="Environment" value={environment}
        onChange={(event) => setEnvironment(event.target.value)} />
      <TextArea label="Node/resource bindings JSON" value={bindings}
        onChange={(event) => setBindings(event.target.value)} />
      <TextArea label="Parameter bindings JSON (CredentialRefs for sensitive parameters)"
        value={parameters} onChange={(event) => setParameters(event.target.value)} />
      <TextArea label="Deployment resource limits JSON" value={resourceLimits}
        onChange={(event) => setResourceLimits(event.target.value)} />
      <TextArea label="Deployment native details JSON" value={nativeDetails}
        onChange={(event) => setNativeDetails(event.target.value)} />
      <Button intent="primary" disabled={!permitted(currentUser, 'etl.edit') || !id ||
        !environment} onClick={save}>Save authored deployment</Button>
      <Button disabled={!permitted(currentUser, 'etl.deploy') || !deployment ||
        session.state === 'background_task_active'}
      onClick={() => invoke('etl.pipeline.deploy', {deploymentId: id})}>
        Validate provider deployment</Button>
    </Box>
    <DataGrid gridId="etl/deployments" aria-label="ETL environment deployments"
      rows={session.content.deployments.map((item) => ({...item,
        validation: session.deploymentResults.find((result) =>
          result.deploymentId === item.id)?.state ?? 'not validated', bindingCount: item.bindings.length}))}
      readOnly rowKeyGetter={(row) => row.id} columns={[{key: 'name', name: 'Deployment'},
        {key: 'environment', name: 'Environment'}, {key: 'bindingCount', name: 'Bindings'},
        {key: 'validation', name: 'Provider validation'}]} />
  </Box>;
}
Deployment.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object};

function RunMonitor({session, invoke, currentUser}) {
  const [deploymentId, setDeploymentId] = useState(session.content.deployments[0]?.id ?? '');
  const [parameters, setParameters] = useState('{}'); const run = session.runs[0];
  const runRows = session.runs.map((item) => ({...item, stagesCount: item.stages.length,
    checkpointText: item.checkpoint ? `${item.checkpoint.nodeId}` : ''}));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="ETL run commands"><Select label="Deployment" value={deploymentId}
      options={session.content.deployments.map((item) => ({value: item.id, label: item.name}))}
      onChange={setDeploymentId} />
    <TextField label="Runtime parameters JSON" value={parameters}
      onChange={(event) => setParameters(event.target.value)} />
    <Button disabled={!permitted(currentUser, 'etl.execute') || !deploymentId ||
      session.state === 'background_task_active'} onClick={() => invoke('etl.run.start', {
      deploymentId, parameters: json(parameters, 'Runtime parameters')})}>Start run</Button>
    <Button disabled={!permitted(currentUser, 'etl.execute') || !session.activeTaskId}
      onClick={() => invoke('etl.run.cancel', {taskId: session.activeTaskId})}>Cancel active task</Button>
    <Button disabled={!permitted(currentUser, 'etl.execute') || !run?.checkpoint ||
      !['failed', 'cancelled'].includes(run?.state)}
    onClick={() => invoke('etl.run.resume', {runId: run.id})}>Resume from checkpoint</Button></Toolbar>
    {!runRows.length ? <EmptyState message="Validate a deployment, then run it through provider-native stages." /> : <>
      <Box sx={{height: '42%'}}><DataGrid gridId="etl/runs" aria-label="ETL pipeline runs"
        rows={runRows} readOnly rowKeyGetter={(row) => row.id} columns={[
          {key: 'id', name: 'Run'}, {key: 'environment', name: 'Environment'},
          {key: 'state', name: 'State'}, {key: 'stagesCount', name: 'Steps'},
          {key: 'deliveryGuarantee', name: 'Weakest guarantee'},
          {key: 'checkpointText', name: 'Checkpoint'}]} /></Box>
      <Box sx={{height: '58%'}}><DataGrid gridId="etl/run-stages" aria-label="ETL run stages"
        rows={run?.stages ?? []} readOnly rowKeyGetter={(row) => row.nodeId} columns={[
          {key: 'nodeId', name: 'Step'}, {key: 'state', name: 'State'},
          {key: 'rows', name: 'Rows'}, {key: 'bytes', name: 'Bytes'},
          {key: 'throughput', name: 'Throughput'},
          {key: 'deliveryGuarantee', name: 'Guarantee'}]} /></Box>
    </>}
  </Box>;
}
RunMonitor.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object};

function Schedule({session, invoke, currentUser}) {
  const [draft, setDraft] = useState({id: '', name: '', enabled: true, trigger: 'cron',
    expression: '', timezone: 'UTC', deploymentId: session.content.deployments[0]?.id ?? '',
    dependencyRefs: '[]', parameters: '{}', nativeDetails: '{}'});
  const [error, setError] = useState('');
  const save = () => { try { const value = {...draft,
    dependencyRefs: json(draft.dependencyRefs, 'Dependency references', []),
    parameters: json(draft.parameters, 'Schedule parameters'),
    nativeDetails: json(draft.nativeDetails, 'Schedule native details')};
  const values = session.content.schedules.some((item) => item.id === value.id) ?
    session.content.schedules.map((item) => item.id === value.id ? value : item) :
    [...session.content.schedules, value]; setError('');
  invoke('etl.pipeline.create', {content: {...session.content, schedules: values}});
  } catch(exception) { setError(exception.message); }};
  return <Box sx={{height: '100%', overflow: 'auto', p: 1}}>
    {error && <Banner status="error">{error}</Banner>}
    <DataGrid gridId="etl/schedules" aria-label="ETL schedules" rows={session.content.schedules}
      readOnly rowKeyGetter={(row) => row.id} columns={[{key: 'name', name: 'Schedule'},
        {key: 'enabled', name: 'Enabled'}, {key: 'trigger', name: 'Trigger'},
        {key: 'expression', name: 'Expression'}, {key: 'timezone', name: 'Timezone'},
        {key: 'deploymentId', name: 'Deployment'}]} />
    <Box sx={{display: 'grid', gap: 1, gridTemplateColumns: 'repeat(3,minmax(180px,1fr))'}}>
      {['id', 'name', 'trigger', 'expression', 'timezone', 'dependencyRefs', 'parameters',
        'nativeDetails'].map(
        (field) => <TextField key={field} label={['dependencyRefs', 'parameters',
          'nativeDetails'].includes(field) ?
          `${field} JSON` : field} value={draft[field]} onChange={(event) => setDraft({...draft,
          [field]: event.target.value})} />)}
      <Select label="Deployment" value={draft.deploymentId} options={session.content.deployments.map(
        (item) => ({value: item.id, label: item.name}))}
      onChange={(value) => setDraft({...draft, deploymentId: value})} />
      <Checkbox label="Enabled" checked={draft.enabled}
        onChange={(value) => setDraft({...draft, enabled: value})} />
      <Button intent="primary" disabled={!permitted(currentUser, 'etl.edit') || !draft.id ||
        !draft.expression || !draft.deploymentId} onClick={save}>Save schedule</Button>
    </Box>
  </Box>;
}
Schedule.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object};

const VIEWS = Object.freeze({pipeline_designer: PipelineDesigner,
  mapping_editor: MappingEditor, schema_propagation: SchemaPropagation,
  preview: Preview, deployment: Deployment, run_monitor: RunMonitor,
  schedule: Schedule});

export function ETLWorkspace({service, sessionId, surface='pipeline_designer', executeCommand,
  currentUser={}, resources=[], credentialReferences=[], onStateChange}) {
  const session = useSession(service, sessionId); const [active, setActive] = useState(surface);
  useEffect(() => onStateChange?.(session), [session, onStateChange]);
  const invoke = async (commandId, args={}) => {
    try { return await executeCommand(commandId, args, {sessionId, service, currentUser}); }
    catch(error) { const code = String(error.message).split(':')[0].replaceAll(' ', '_');
      service.reportError(sessionId, error, code); return undefined; }
  };
  const View = VIEWS[active] ?? PipelineDesigner;
  return <Box data-module="cdeadmin.etl"
    sx={{height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column'}}>
    <Box role="tablist" aria-label="ETL Designer surfaces"
      sx={{display: 'flex', overflowX: 'auto', borderBottom: '1px solid', borderColor: 'divider'}}>
      {ETL_SURFACES.map((item) => <Tab key={item.id} label={item.title}
        active={active === item.id} attention={item.id === 'schema_propagation' &&
          !session.schemaPropagation.compatible} onActivate={() => setActive(item.id)} />)}
    </Box>
    <Box sx={{px: 1, py: 0.5, display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap'}}>
      <Badge status={status(session.state)} label={session.state.replaceAll('_', ' ')} />
      <Box>{session.content.nodes.length} nodes · {session.content.edges.length} edges</Box>
      <Badge label={session.content.mode.replaceAll('_', ' ')} />
      {session.providerStatuses.map((item) => <Badge key={item.providerId}
        label={`${item.providerId}: ${item.supportState}`}
        status={item.supportState.startsWith('supported') ? 'success' : 'warning'} />)}
      {session.dirty && <Badge status="warning" label="Unsaved" />}
    </Box>
    <Box sx={{flex: 1, minHeight: 0, overflow: 'hidden'}}><StateBoundary session={session}>
      <View session={session} invoke={invoke} service={service} currentUser={currentUser}
        onNavigate={setActive} resources={resources} credentialReferences={credentialReferences} />
    </StateBoundary></Box>
  </Box>;
}
ETLWorkspace.propTypes = {service: PropTypes.object.isRequired,
  sessionId: PropTypes.string.isRequired,
  surface: PropTypes.oneOf(ETL_SURFACES.map((item) => item.id)),
  executeCommand: PropTypes.func.isRequired, currentUser: PropTypes.object,
  resources: PropTypes.array, credentialReferences: PropTypes.array,
  onStateChange: PropTypes.func};

export function etlInspector(session) {
  const node = session?.content.nodes.find((item) => item.id === session.selectedNodeId);
  const edge = session?.content.edges.find((item) => item.id === session.selectedEdgeId);
  const provider = session?.providerStatuses.find((item) => item.providerId ===
    (node?.resourceRef?.provider ?? node?.resourceRef?.providerId));
  return {'Node configuration': node ? `${node.name} · ${node.kind}` : 'None selected',
    'Ports & schema': node ? `${node.ports.length} typed ports` : edge ?
      `${edge.fromNodeId}.${edge.fromPort} → ${edge.toNodeId}.${edge.toPort}` : 'None',
    Parameters: `${session?.content.parameters.length ?? 0} pipeline parameters`,
    'Error policy': node?.errorRoute?.action ?? 'No node selected',
    'Provider limits': provider?.limitations?.join(', ') || 'Unknown until provider evidence exists'};
}

export function ETLNavigator({service, onOpen}) {
  const [sessions, setSessions] = useState(() => service.list());
  const [expanded, setExpanded] = useState(() => new Set());
  const [query, setQuery] = useState('');
  useEffect(() => service.subscribe(() => setSessions(service.list())), [service]);
  const filtered = useMemo(() => sessions.filter((session) => !query ||
    `${session.content.name} ${session.id}`.toLowerCase().includes(query.toLowerCase())),
  [query, sessions]);
  return <Box role="tree" aria-label="ETL pipelines" sx={{height: '100%', overflow: 'auto'}}>
    <Box sx={{p: 0.5}}><SearchField label="Search ETL pipelines" value={query}
      onChange={setQuery} resultCount={filtered.length} /></Box>
    {!filtered.length && <EmptyState message="No ETL pipeline assets are open." />}
    {filtered.map((session) => { const open = expanded.has(session.id); return <React.Fragment
      key={session.id}><TreeRow label={session.content.name || session.id} level={1}
        expandable expanded={open} trailing={<Badge label={session.content.mode} />}
        onToggle={() => setExpanded((prior) => { const next = new Set(prior);
          open ? next.delete(session.id) : next.add(session.id); return next; })}
        onOpen={() => onOpen?.(session.id, 'pipeline_designer')} />
      {open && ETL_SURFACES.map((item) => <TreeRow key={item.id} label={item.title}
        level={2} onOpen={() => onOpen?.(session.id, item.id)} />)}
    </React.Fragment>; })}
  </Box>;
}
ETLNavigator.propTypes = {service: PropTypes.object.isRequired, onOpen: PropTypes.func};
