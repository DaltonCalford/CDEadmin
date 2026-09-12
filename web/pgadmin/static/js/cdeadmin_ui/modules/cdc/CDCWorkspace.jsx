/////////////////////////////////////////////////////////////
// CDC Designer provider-neutral workbench surfaces.
/////////////////////////////////////////////////////////////

import {useEffect, useMemo, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {Button} from '../../primitives/Button';
import {NumberField, TextArea, TextField} from '../../primitives/Field';
import {Checkbox, Select} from '../../primitives/Choice';
import {SearchField} from '../../primitives/AdvancedControls';
import {ResourcePicker, SecretPicker, TreeRow} from '../../navigation/AdvancedNavigation';
import {Toolbar} from '../../layout/WorkbenchChrome';
import {Badge, Banner, ProgressBar, Skeleton} from '../../feedback/Indicators';
import {EmptyState} from '../../feedback/EmptyState';
import DataGrid from '../../data/DataGrid';
import GraphSurface from '../../visualization/GraphSurface';
import {
  CDC_CAPTURE_MECHANISMS, CDC_DELIVERY_GUARANTEES, CDC_EVOLUTION_ACTIONS,
  CDC_SCHEMA_CHANGE_CLASSES, CDC_SNAPSHOT_MODES, CDC_START_KINDS,
  createCDCContent,
} from './contracts';

export const CDC_SURFACES = Object.freeze([
  {id: 'cdc_designer', title: 'CDC Designer'},
  {id: 'source_capture', title: 'Source & Capture'},
  {id: 'event_mapping', title: 'Event Mapping'},
  {id: 'schema_evolution', title: 'Schema Evolution'},
  {id: 'run_monitor', title: 'Run Monitor'},
  {id: 'event_inspector', title: 'Event Inspector'},
  {id: 'replay', title: 'Replay'},
]);

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

const AUTHORING_BLOCKED_STATES = Object.freeze([
  'loading', 'read_only', 'permission_denied', 'background_task_active',
]);
const LIVE_BLOCKED_STATES = Object.freeze([
  'loading', 'empty', 'stale', 'partial', 'disconnected', 'read_only',
  'permission_denied', 'validation_error', 'background_task_active',
]);

function canAuthor(session, user) {
  return permitted(user, 'cdc.edit') && !AUTHORING_BLOCKED_STATES.includes(session.state);
}

function canReadProvider(session, user) {
  return permitted(user, 'cdc.view') && ![
    'loading', 'empty', 'stale', 'disconnected', 'permission_denied',
    'background_task_active',
  ].includes(session.state);
}

function canExecute(session, user, permission='cdc.execute') {
  return permitted(user, permission) && !LIVE_BLOCKED_STATES.includes(session.state);
}

function json(text, label, fallback={}) {
  if(!String(text ?? '').trim()) return fallback;
  const value = JSON.parse(text);
  if(!value || typeof value !== 'object') throw new TypeError(`${label} must be JSON.`);
  return value;
}

function refText(reference) {
  return reference ? JSON.stringify(reference, null, 2) : '';
}

function StateBoundary({session, children}) {
  const limitations = session.providerStatuses.flatMap((item) => [
    ...(item.warnings ?? []), ...(item.limitations ?? []),
  ]);
  return <>
    {session.state === 'background_task_active' && <Box sx={{p: 1}}>
      <ProgressBar label="CDC background task" status="indeterminate" />
    </Box>}
    {session.state === 'loading' && <Box sx={{p: 2}}><Skeleton lines={8} /></Box>}
    {session.state === 'empty' && <Box sx={{p: 1}}><EmptyState
      message="CDC definition is not configured. Define a source and sink before validation or execution."
    /></Box>}
    {session.error && <Box sx={{p: 1}}><Banner status="error">{session.error}</Banner></Box>}
    {!session.error && session.state === 'permission_denied' && <Box sx={{p: 1}}>
      <Banner status="error">Permission denied. Provider support remains distinct.</Banner>
    </Box>}
    {!session.error && session.state === 'validation_error' && <Box sx={{p: 1}}>
      <Banner status="error">CDC definition validation failed. Review Problems.</Banner>
    </Box>}
    {['stale', 'partial', 'disconnected', 'read_only'].includes(session.state) &&
      <Box sx={{p: 1}}><Banner status="warning">
        CDC Designer is {session.state.replaceAll('_', ' ')}. Authored state remains visible.
      </Banner></Box>}
    {limitations.length > 0 && <Box sx={{p: 1}}><Banner status="warning">
      Provider limitations: {[...new Set(limitations)].join(' ')}
    </Banner></Box>}
    {children}
  </>;
}
StateBoundary.propTypes = {session: PropTypes.object.isRequired, children: PropTypes.node};

function DefinitionEditor({session, invoke, service, currentUser, onNavigate}) {
  const [name, setName] = useState(session.content.name);
  const [description, setDescription] = useState(session.content.description);
  const [error, setError] = useState('');
  const save = () => { try {
    const candidate = createCDCContent({...session.content, name, description});
    setError(''); invoke('cdc.definition.create', {content: candidate});
  } catch(exception) { setError(exception.message); }};
  const nodes = [
    {id: 'source', name: session.content.source?.resourceRef.canonical ?? 'Source not configured',
      kind: 'source', level: 0, namespace: session.content.source?.captureMechanism ?? 'unknown'},
    {id: 'capture', name: session.content.source?.nativeMechanism ?? 'Capture not configured',
      kind: 'capture', level: 1, namespace: session.content.source?.captureMechanism ?? 'unknown'},
    {id: 'transform', name: `${session.content.filters.length + session.content.transforms.length} transforms`,
      kind: 'transform', level: 2, namespace: 'normalized envelope'},
    {id: 'sink', name: session.content.sink?.resourceRef.canonical ?? 'Sink not configured',
      kind: 'sink', level: 3, namespace: session.content.deliveryPolicy.guarantee},
  ];
  const edges = [{id: 'source-capture', from: 'source', to: 'capture', type: 'capture'},
    {id: 'capture-transform', from: 'capture', to: 'transform', type: 'event'},
    {id: 'transform-sink', from: 'transform', to: 'sink', type: 'delivery'}];
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="CDC flow commands">
      <Button disabled={!canReadProvider(session, currentUser)}
        onClick={() => invoke('cdc.validate')}>Validate</Button>
      <Button onClick={() => onNavigate('source_capture')}>Source</Button>
      <Button onClick={() => onNavigate('event_mapping')}>Transform</Button>
      <Button disabled={!canExecute(session, currentUser)}
        onClick={() => onNavigate('run_monitor')}>Start</Button>
    </Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    <Box role="region" aria-label="CDC definition identity" sx={{display: 'grid', gap: 1, p: 1,
      gridTemplateColumns: 'minmax(180px,1fr) minmax(260px,2fr) auto'}}>
      <TextField label="Definition name" value={name}
        onChange={(event) => setName(event.target.value)} />
      <TextField label="Description" value={description}
        onChange={(event) => setDescription(event.target.value)} />
      <Button intent="primary" disabled={!canAuthor(session, currentUser)} onClick={save}>
        Save definition</Button>
    </Box>
    <Box sx={{flex: 1, minHeight: 260}}><GraphSurface label="CDC flow" nodes={nodes} edges={edges}
      mode="graph" selectedId={session.selection.stage}
      onSelect={(stage) => service.select(session.id, {stage})} /></Box>
    <Box role="table" aria-label="CDC flow text alternative" sx={{p: 1}}>
      {nodes.map((node) => <Box role="row" key={node.id} sx={{display: 'flex', gap: 1}}>
        <Box role="cell">{node.id}</Box><Box role="cell">{node.name}</Box>
        <Box role="cell">{node.namespace}</Box></Box>)}
    </Box>
  </Box>;
}
DefinitionEditor.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  service: PropTypes.object,
  currentUser: PropTypes.object, onNavigate: PropTypes.func};

function SourceCapture({session, invoke, currentUser, resources, credentialReferences}) {
  const source = session.content.source;
  const [draft, setDraft] = useState({resourceRef: refText(source?.resourceRef),
    captureMechanism: source?.captureMechanism ?? 'log_based',
    nativeMechanism: source?.nativeMechanism ?? '', privileges:
      (source?.requiredPrivileges ?? []).join('\n'), credentialRef: source?.credentialRef ?? null,
    startKind: source?.startPosition.kind ?? 'latest', startValue: source?.startPosition.value ?? '',
    startNative: JSON.stringify(source?.startPosition.nativeDetails ?? {}, null, 2),
    snapshotMode: source?.snapshotPolicy.mode ?? 'none',
    snapshotConsistency: source?.snapshotPolicy.consistency ?? '',
    batchSize: source?.snapshotPolicy.batchSize ?? '',
    snapshotNative: JSON.stringify(source?.snapshotPolicy.nativeDetails ?? {}, null, 2),
    nativeDetails: JSON.stringify(source?.nativeDetails ?? {}, null, 2)});
  const [resourceOpen, setResourceOpen] = useState(false);
  const [credentialOpen, setCredentialOpen] = useState(false); const [error, setError] = useState('');
  const save = () => { try {
    const value = {resourceRef: json(draft.resourceRef, 'Source ResourceRef'),
      captureMechanism: draft.captureMechanism, nativeMechanism: draft.nativeMechanism,
      requiredPrivileges: draft.privileges.split('\n').map((item) => item.trim()).filter(Boolean),
      credentialRef: draft.credentialRef,
      startPosition: {kind: draft.startKind, value: draft.startKind === 'latest' ? null :
        draft.startValue, nativeDetails: json(draft.startNative, 'Start-position native details')},
      snapshotPolicy: {mode: draft.snapshotMode, consistency: draft.snapshotConsistency || null,
        batchSize: draft.batchSize === '' ? null : Number(draft.batchSize),
        nativeDetails: json(draft.snapshotNative, 'Snapshot native details')},
      nativeDetails: json(draft.nativeDetails, 'Source native details')};
    const candidate = createCDCContent({...session.content, source: value});
    setError(''); invoke('cdc.definition.create', {content: candidate});
  } catch(exception) { setError(exception.message); }};
  return <Box sx={{height: '100%', overflow: 'auto', p: 1}}>
    <Toolbar label="Source and capture commands">
      <Button disabled={!source || !canReadProvider(session, currentUser)}
        onClick={() => invoke('cdc.validate')}>Validate capabilities and privileges</Button>
    </Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    <Box sx={{display: 'grid', gap: 1, gridTemplateColumns: 'repeat(3,minmax(180px,1fr))'}}>
      <TextArea label="Source ResourceRef JSON" value={draft.resourceRef}
        onChange={(event) => setDraft({...draft, resourceRef: event.target.value})} />
      <Button onClick={() => setResourceOpen(true)}>Choose source resource</Button>
      <Button onClick={() => setCredentialOpen(true)}>Choose credential reference</Button>
      <Select label="Normalized capture mechanism" value={draft.captureMechanism}
        options={CDC_CAPTURE_MECHANISMS.map((value) => ({value, label: value}))}
        onChange={(value) => setDraft({...draft, captureMechanism: value})} />
      <TextField label="Exact provider-native mechanism" value={draft.nativeMechanism}
        onChange={(event) => setDraft({...draft, nativeMechanism: event.target.value})} />
      <TextArea label="Required provider privileges (one per line)" value={draft.privileges}
        onChange={(event) => setDraft({...draft, privileges: event.target.value})} />
      <Select label="Start position" value={draft.startKind}
        options={CDC_START_KINDS.map((value) => ({value, label: value}))}
        onChange={(value) => setDraft({...draft, startKind: value})} />
      <TextField label="Native start cursor/value" value={draft.startValue}
        disabled={draft.startKind === 'latest'}
        onChange={(event) => setDraft({...draft, startValue: event.target.value})} />
      <TextArea label="Start-position native details JSON" value={draft.startNative}
        onChange={(event) => setDraft({...draft, startNative: event.target.value})} />
      <Select label="Initial snapshot policy" value={draft.snapshotMode}
        options={CDC_SNAPSHOT_MODES.map((value) => ({value, label: value}))}
        onChange={(value) => setDraft({...draft, snapshotMode: value})} />
      <TextField label="Snapshot consistency" value={draft.snapshotConsistency}
        onChange={(event) => setDraft({...draft, snapshotConsistency: event.target.value})} />
      <NumberField label="Incremental batch size" value={draft.batchSize}
        onChange={(event) => setDraft({...draft, batchSize: event.target.value})} />
      <TextArea label="Snapshot native details JSON" value={draft.snapshotNative}
        onChange={(event) => setDraft({...draft, snapshotNative: event.target.value})} />
      <TextArea label="Source native details JSON" value={draft.nativeDetails}
        onChange={(event) => setDraft({...draft, nativeDetails: event.target.value})} />
      <Box><Badge label={draft.credentialRef ? 'CredentialRef selected' : 'No credential reference'} />
        <Button intent="primary" disabled={!canAuthor(session, currentUser)} onClick={save}>
          Save source and capture</Button></Box>
    </Box>
    <ResourcePicker open={resourceOpen} items={resources} selected={[]}
      onClose={() => setResourceOpen(false)} onConfirm={(item) => {
        setResourceOpen(false); if(item) setDraft({...draft, resourceRef: refText(item.reference)});
      }} />
    <SecretPicker open={credentialOpen} items={credentialReferences} selected={[]}
      onClose={() => setCredentialOpen(false)} onConfirm={(item) => {
        setCredentialOpen(false); if(item) setDraft({...draft, credentialRef: item.reference});
      }} />
  </Box>;
}
SourceCapture.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object, resources: PropTypes.array, credentialReferences: PropTypes.array};

function EventMapping({session, invoke, currentUser, resources, credentialReferences}) {
  const sink = session.content.sink;
  const [sinkDraft, setSinkDraft] = useState({resourceRef: refText(sink?.resourceRef),
    credentialRef: sink?.credentialRef ?? null, serialization: sink?.serialization ?? '',
    nativeDetails: JSON.stringify(sink?.nativeDetails ?? {}, null, 2),
    guarantee: session.content.deliveryPolicy.guarantee,
    deduplication: JSON.stringify(session.content.deliveryPolicy.deduplication, null, 2),
    proof: JSON.stringify(session.content.deliveryPolicy.proof, null, 2),
    deliveryNative: JSON.stringify(session.content.deliveryPolicy.nativeDetails, null, 2)});
  const [transform, setTransform] = useState({id: '', name: '', kind: 'mapping', enabled: true,
    rules: '{}', schemaMapping: '{}', nativeDetails: '{}'});
  const [resourceOpen, setResourceOpen] = useState(false);
  const [credentialOpen, setCredentialOpen] = useState(false); const [error, setError] = useState('');
  const saveSink = () => { try {
    const value = {resourceRef: json(sinkDraft.resourceRef, 'Sink ResourceRef'),
      credentialRef: sinkDraft.credentialRef, serialization: sinkDraft.serialization,
      nativeDetails: json(sinkDraft.nativeDetails, 'Sink native details')};
    const deliveryPolicy = {guarantee: sinkDraft.guarantee,
      deduplication: json(sinkDraft.deduplication, 'Delivery deduplication'),
      proof: json(sinkDraft.proof, 'Delivery proof'),
      nativeDetails: json(sinkDraft.deliveryNative, 'Delivery native details')};
    const candidate = createCDCContent({...session.content, sink: value, deliveryPolicy});
    setError(''); invoke('cdc.definition.create', {content: candidate});
  } catch(exception) { setError(exception.message); }};
  const saveTransform = () => { try {
    const value = {...transform, name: transform.name || transform.id,
      rules: json(transform.rules, 'Transform rules'),
      schemaMapping: json(transform.schemaMapping, 'Transform schema mapping'),
      nativeDetails: json(transform.nativeDetails, 'Transform native details')};
    const key = value.kind === 'filter' ? 'filters' : 'transforms';
    const values = session.content[key].some((item) => item.id === value.id) ?
      session.content[key].map((item) => item.id === value.id ? value : item) :
      [...session.content[key], value];
    const candidate = createCDCContent({...session.content, [key]: values});
    setError(''); invoke('cdc.definition.create', {content: candidate});
  } catch(exception) { setError(exception.message); }};
  const rows = [...session.content.filters, ...session.content.transforms].map((item) => ({...item,
    rulesText: JSON.stringify(item.rules), mappingText: JSON.stringify(item.schemaMapping)}));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column', overflow: 'auto'}}>
    {error && <Banner status="error">{error}</Banner>}
    <Box sx={{display: 'grid', gap: 1, p: 1, gridTemplateColumns: 'repeat(4,minmax(180px,1fr))'}}>
      <TextArea label="Sink ResourceRef JSON" value={sinkDraft.resourceRef}
        onChange={(event) => setSinkDraft({...sinkDraft, resourceRef: event.target.value})} />
      <Button onClick={() => setResourceOpen(true)}>Choose sink resource</Button>
      <Button onClick={() => setCredentialOpen(true)}>Choose credential reference</Button>
      <TextField label="Serialization" value={sinkDraft.serialization}
        onChange={(event) => setSinkDraft({...sinkDraft, serialization: event.target.value})} />
      <Select label="Declared delivery guarantee" value={sinkDraft.guarantee}
        options={CDC_DELIVERY_GUARANTEES.map((value) => ({value, label: value}))}
        onChange={(value) => setSinkDraft({...sinkDraft, guarantee: value})} />
      <TextArea label="Deduplication JSON" value={sinkDraft.deduplication}
        onChange={(event) => setSinkDraft({...sinkDraft, deduplication: event.target.value})} />
      <TextArea label="End-to-end proof JSON" value={sinkDraft.proof}
        onChange={(event) => setSinkDraft({...sinkDraft, proof: event.target.value})} />
      <TextArea label="Delivery native details JSON" value={sinkDraft.deliveryNative}
        onChange={(event) => setSinkDraft({...sinkDraft, deliveryNative: event.target.value})} />
      <TextArea label="Sink native details JSON" value={sinkDraft.nativeDetails}
        onChange={(event) => setSinkDraft({...sinkDraft, nativeDetails: event.target.value})} />
      <Button intent="primary" disabled={!canAuthor(session, currentUser)} onClick={saveSink}>
        Save sink and delivery</Button>
    </Box>
    <Box sx={{display: 'grid', gap: 1, p: 1, gridTemplateColumns: 'repeat(4,minmax(160px,1fr))'}}>
      <TextField label="Transform ID" value={transform.id}
        onChange={(event) => setTransform({...transform, id: event.target.value})} />
      <TextField label="Name" value={transform.name}
        onChange={(event) => setTransform({...transform, name: event.target.value})} />
      <Select label="Kind" value={transform.kind} options={['filter', 'mapping', 'redaction']
        .map((value) => ({value, label: value}))}
      onChange={(value) => setTransform({...transform, kind: value})} />
      <Checkbox label="Enabled" checked={transform.enabled}
        onChange={(value) => setTransform({...transform, enabled: value})} />
      <TextArea label="Rules JSON" value={transform.rules}
        onChange={(event) => setTransform({...transform, rules: event.target.value})} />
      <TextArea label="Schema mapping JSON" value={transform.schemaMapping}
        onChange={(event) => setTransform({...transform, schemaMapping: event.target.value})} />
      <TextArea label="Native details JSON" value={transform.nativeDetails}
        onChange={(event) => setTransform({...transform, nativeDetails: event.target.value})} />
      <Button intent="primary" disabled={!canAuthor(session, currentUser) || !transform.id}
        onClick={saveTransform}>Save transform</Button>
    </Box>
    <Box sx={{flex: 1, minHeight: 180}}><DataGrid gridId="cdc/event-mapping"
      aria-label="CDC event filters mappings and redactions" rows={rows} readOnly
      rowKeyGetter={(row) => row.id} columns={[{key: 'id', name: 'ID'}, {key: 'name', name: 'Name'},
        {key: 'kind', name: 'Kind'}, {key: 'enabled', name: 'Enabled'},
        {key: 'rulesText', name: 'Rules'}, {key: 'mappingText', name: 'Schema mapping'}]} /></Box>
    <ResourcePicker open={resourceOpen} items={resources} selected={[]}
      onClose={() => setResourceOpen(false)} onConfirm={(item) => {
        setResourceOpen(false); if(item) setSinkDraft({...sinkDraft, resourceRef: refText(item.reference)});
      }} />
    <SecretPicker open={credentialOpen} items={credentialReferences} selected={[]}
      onClose={() => setCredentialOpen(false)} onConfirm={(item) => {
        setCredentialOpen(false); if(item) setSinkDraft({...sinkDraft, credentialRef: item.reference});
      }} />
  </Box>;
}
EventMapping.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object, resources: PropTypes.array, credentialReferences: PropTypes.array};

function SchemaEvolution({session, invoke, service, currentUser}) {
  const policy = session.content.schemaEvolutionPolicy;
  const [defaultAction, setDefaultAction] = useState(policy.defaultAction);
  const [change, setChange] = useState({id: '', classification: 'unknown',
    action: 'pause_and_review', description: '', detectedSchemaVersion: '', mapping: '{}',
    nativeDetails: '{}'}); const [error, setError] = useState('');
  const save = () => { try {
    const value = {...change, detectedSchemaVersion: change.detectedSchemaVersion || null,
      mapping: json(change.mapping, 'Schema-change mapping'),
      nativeDetails: json(change.nativeDetails, 'Schema-change native details')};
    const changes = policy.changes.some((item) => item.id === value.id) ?
      policy.changes.map((item) => item.id === value.id ? value : item) : [...policy.changes, value];
    const candidate = createCDCContent({...session.content,
      schemaEvolutionPolicy: {defaultAction, changes, nativeDetails: policy.nativeDetails}});
    setError(''); invoke('cdc.definition.create', {content: candidate});
  } catch(exception) { setError(exception.message); }};
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    {error && <Banner status="error">{error}</Banner>}
    <Box sx={{display: 'grid', gap: 1, p: 1, gridTemplateColumns: 'repeat(4,minmax(160px,1fr))'}}>
      <Select label="Default policy" value={defaultAction}
        options={CDC_EVOLUTION_ACTIONS.map((value) => ({value, label: value}))}
        onChange={setDefaultAction} />
      <TextField label="Change ID" value={change.id}
        onChange={(event) => setChange({...change, id: event.target.value})} />
      <Select label="Classification" value={change.classification}
        options={CDC_SCHEMA_CHANGE_CLASSES.map((value) => ({value, label: value}))}
        onChange={(value) => setChange({...change, classification: value})} />
      <Select label="Resolution" value={change.action}
        options={CDC_EVOLUTION_ACTIONS.map((value) => ({value, label: value}))}
        onChange={(value) => setChange({...change, action: value})} />
      <TextField label="Detected schema version" value={change.detectedSchemaVersion}
        onChange={(event) => setChange({...change, detectedSchemaVersion: event.target.value})} />
      <TextArea label="Description" value={change.description}
        onChange={(event) => setChange({...change, description: event.target.value})} />
      <TextArea label="Mapping JSON" value={change.mapping}
        onChange={(event) => setChange({...change, mapping: event.target.value})} />
      <TextArea label="Native details JSON" value={change.nativeDetails}
        onChange={(event) => setChange({...change, nativeDetails: event.target.value})} />
      <Button intent="primary" disabled={!canAuthor(session, currentUser) || !change.id}
        onClick={save}>Save policy and change</Button>
      <Button disabled={!canReadProvider(session, currentUser) || !change.id}
        onClick={() => service.validateSchemaChange(session.id, change.id, {currentUser})}>
        Validate selected change</Button>
    </Box>
    {!session.schemaChanges.length ? <EmptyState message="No source schema changes detected." /> :
      <DataGrid gridId="cdc/schema-evolution" aria-label="CDC schema evolution changes"
        rows={session.schemaChanges} readOnly rowKeyGetter={(row) => row.id}
        columns={[{key: 'id', name: 'Change'}, {key: 'classification', name: 'Classification'},
          {key: 'action', name: 'Resolution'}, {key: 'detectedSchemaVersion', name: 'Schema version'},
          {key: 'description', name: 'Description'}]} />}
  </Box>;
}
SchemaEvolution.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  service: PropTypes.object, currentUser: PropTypes.object};

function RunMonitor({session, invoke, currentUser}) {
  const [error, setError] = useState(''); const latest = session.runs[0];
  const [alert, setAlert] = useState({id: '', event: 'lag', maximum: '', enabled: true,
    nativeDetails: '{}'});
  const call = (command, args={}) => Promise.resolve(invoke(command, args)).catch(
    (exception) => setError(exception.message)
  );
  const rows = session.runs.map((run) => ({...run,
    snapshotCheckpointText: JSON.stringify(run.snapshotCheckpoint),
    streamCheckpointText: JSON.stringify(run.streamCheckpoint), errorsText: run.errors.join(' ')}));
  const saveAlert = () => { try {
    const value = {id: alert.id, event: alert.event, enabled: alert.enabled,
      threshold: {maximum: Number(alert.maximum)},
      nativeDetails: json(alert.nativeDetails, 'Alert native details')};
    const alerts = session.content.alerts.some((item) => item.id === value.id) ?
      session.content.alerts.map((item) => item.id === value.id ? value : item) :
      [...session.content.alerts, value];
    const candidate = createCDCContent({...session.content, alerts}); setError('');
    invoke('cdc.definition.create', {content: candidate});
  } catch(exception) { setError(exception.message); }};
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="CDC run controls">
      <Button disabled={!canExecute(session, currentUser)}
        onClick={() => call('cdc.run.start')}>Start</Button>
      <Button disabled={!latest || !canExecute(session, currentUser)}
        onClick={() => call('cdc.run.pause')}>Pause</Button>
      <Button disabled={!latest || !canExecute(session, currentUser)}
        onClick={() => call('cdc.run.resume')}>Resume</Button>
      <Button disabled={!latest || !canExecute(session, currentUser)}
        onClick={() => call('cdc.run.stop')}>Stop</Button>
      <Button disabled={!latest || !canReadProvider(session, currentUser)}
        onClick={() => call('cdc.checkpoint.inspect')}>Refresh checkpoint</Button>
    </Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    <Box role="region" aria-label="CDC lag alert editor" sx={{display: 'grid', gap: 1, p: 1,
      gridTemplateColumns: 'repeat(6,minmax(120px,1fr))'}}>
      <TextField label="Alert ID" value={alert.id}
        onChange={(event) => setAlert({...alert, id: event.target.value})} />
      <TextField label="Alert event" value={alert.event}
        onChange={(event) => setAlert({...alert, event: event.target.value})} />
      <NumberField label="Lag threshold" value={alert.maximum}
        onChange={(event) => setAlert({...alert, maximum: event.target.value})} />
      <Checkbox label="Alert enabled" checked={alert.enabled}
        onChange={(value) => setAlert({...alert, enabled: value})} />
      <TextArea label="Alert native details JSON" value={alert.nativeDetails}
        onChange={(event) => setAlert({...alert, nativeDetails: event.target.value})} />
      <Button disabled={!canAuthor(session, currentUser) || !alert.id || alert.maximum === ''}
        onClick={saveAlert}>Save lag alert</Button>
    </Box>
    {!rows.length ? <EmptyState message="Start a validated CDC definition to create a run." /> :
      <DataGrid gridId="cdc/runs" aria-label="CDC run state lag throughput checkpoints and errors"
        rows={rows} readOnly rowKeyGetter={(row) => row.id}
        columns={[{key: 'id', name: 'Run'}, {key: 'state', name: 'State'},
          {key: 'lag', name: 'Lag'}, {key: 'throughput', name: 'Throughput'},
          {key: 'snapshotCheckpointText', name: 'Snapshot checkpoint'},
          {key: 'streamCheckpointText', name: 'Stream checkpoint'},
          {key: 'errorsText', name: 'Errors'}]} />}
  </Box>;
}
RunMonitor.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object};

function EventInspector({session, service, currentUser}) {
  const [limit, setLimit] = useState(100); const [paths, setPaths] = useState('');
  const [error, setError] = useState(''); const allowed = permitted(currentUser, 'cdc.view_payloads');
  const load = () => service.inspectEvents(session.id, {limit: Number(limit),
    redactedPaths: paths.split('\n').map((item) => item.trim()).filter(Boolean)}, {currentUser})
    .catch((exception) => setError(exception.message));
  const rows = session.events.map((event, index) => ({id: event.eventId ?? `event-${index}`,
    operation: event.operation, captureTime: event.captureTime,
    transactionId: event.transactionId ?? '', sourcePosition: JSON.stringify(event.sourcePosition),
    key: JSON.stringify(event.key), before: JSON.stringify(event.before), after: JSON.stringify(event.after),
    metadata: JSON.stringify(event.metadata), redacted: event.payloadRedacted}));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="CDC event sample controls">
      <NumberField label="Bounded sample size" value={limit} min={1} max={1000}
        onChange={(event) => setLimit(event.target.value)} />
      <TextArea label="Additional redacted paths (one per line)" value={paths}
        onChange={(event) => setPaths(event.target.value)} />
      <Button disabled={!canReadProvider(session, currentUser)} onClick={load}>
        Load bounded sample</Button>
      <Badge label={allowed ? 'Payload permission granted' : 'Payloads redacted'} />
    </Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    {!rows.length ? <EmptyState message="Load a bounded event sample. Payload access is separately authorized." /> :
      <DataGrid gridId="cdc/event-inspector" aria-label="CDC bounded redacted event samples"
        rows={rows} readOnly rowKeyGetter={(row) => row.id}
        columns={[{key: 'id', name: 'Event'}, {key: 'operation', name: 'Operation'},
          {key: 'captureTime', name: 'Captured'}, {key: 'transactionId', name: 'Transaction'},
          {key: 'sourcePosition', name: 'Source position'}, {key: 'key', name: 'Key'},
          {key: 'before', name: 'Before'}, {key: 'after', name: 'After'},
          {key: 'metadata', name: 'Metadata'}, {key: 'redacted', name: 'Redacted'}]} />}
  </Box>;
}
EventInspector.propTypes = {session: PropTypes.object, service: PropTypes.object,
  currentUser: PropTypes.object};

function Replay({session, invoke, currentUser, resources}) {
  const review = session.replayReview; const [resourceOpen, setResourceOpen] = useState(false);
  const [draft, setDraft] = useState({from: '', to: '', target: '', estimatedEventCount: '',
    idempotencyAssessment: '', schemaCompatibility: '', production: false,
    productionConfirmed: false, nativeDetails: '{}'}); const [error, setError] = useState('');
  const prepare = () => { try { setError(''); invoke('cdc.replay.prepare', {request: {...draft,
    target: json(draft.target, 'Replay target'), estimatedEventCount:
      draft.estimatedEventCount === '' ? null : Number(draft.estimatedEventCount),
    nativeDetails: json(draft.nativeDetails, 'Replay native details')}});
  } catch(exception) { setError(exception.message); }};
  return <Box sx={{height: '100%', overflow: 'auto', p: 1}}>
    {error && <Banner status="error">{error}</Banner>}
    <Banner status="warning">Replay is always a separate bounded task and never rewinds the live stream.</Banner>
    <Box sx={{display: 'grid', gap: 1, gridTemplateColumns: 'repeat(3,minmax(180px,1fr))'}}>
      <TextField label="From source position/time" value={draft.from}
        onChange={(event) => setDraft({...draft, from: event.target.value})} />
      <TextField label="To source position/time" value={draft.to}
        onChange={(event) => setDraft({...draft, to: event.target.value})} />
      <NumberField label="Estimated event count" value={draft.estimatedEventCount}
        onChange={(event) => setDraft({...draft, estimatedEventCount: event.target.value})} />
      <TextArea label="Target ResourceRef JSON" value={draft.target}
        onChange={(event) => setDraft({...draft, target: event.target.value})} />
      <Button onClick={() => setResourceOpen(true)}>Choose replay target</Button>
      <TextArea label="Idempotency/dedup assessment" value={draft.idempotencyAssessment}
        onChange={(event) => setDraft({...draft, idempotencyAssessment: event.target.value})} />
      <TextArea label="Schema compatibility validation" value={draft.schemaCompatibility}
        onChange={(event) => setDraft({...draft, schemaCompatibility: event.target.value})} />
      <TextArea label="Replay native details JSON" value={draft.nativeDetails}
        onChange={(event) => setDraft({...draft, nativeDetails: event.target.value})} />
      <Checkbox label="Production target" checked={draft.production}
        onChange={(value) => setDraft({...draft, production: value,
          productionConfirmed: value ? draft.productionConfirmed : false})} />
      <Checkbox label="I explicitly confirm production replay" checked={draft.productionConfirmed}
        disabled={!draft.production}
        onChange={(value) => setDraft({...draft, productionConfirmed: value})} />
      <Button disabled={!canExecute(session, currentUser, 'cdc.replay')}
        onClick={prepare}>Prepare replay</Button>
      <Button intent="primary" disabled={!review?.valid ||
        !canExecute(session, currentUser, 'cdc.replay')}
      onClick={() => invoke('cdc.replay.execute')}>Execute reviewed replay</Button>
    </Box>
    {review && <Box sx={{mt: 1}}><Banner status={review.valid ? 'success' : 'error'}>
      Prepared by {review.preparedBy} at {review.preparedAt}. {review.warnings.join(' ')}
    </Banner></Box>}
    <ResourcePicker open={resourceOpen} items={resources} selected={[]}
      onClose={() => setResourceOpen(false)} onConfirm={(item) => {
        setResourceOpen(false); if(item) setDraft({...draft, target: refText(item.reference)});
      }} />
  </Box>;
}
Replay.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object, resources: PropTypes.array};

export function CDCWorkspace({service, sessionId, surface='cdc_designer', executeCommand,
  currentUser={}, resources=[], credentialReferences=[], onStateChange}) {
  const session = useSession(service, sessionId); const [active, setActive] = useState(surface);
  const [navigationQuery, setNavigationQuery] = useState('');
  const invoke = (command, args={}) => executeCommand ? executeCommand(command, args) :
    Promise.reject(new Error('CDC command authority is unavailable.'));
  useEffect(() => onStateChange?.(session), [onStateChange, session]);
  const body = useMemo(() => {
    const common = {session, invoke, service, currentUser, resources, credentialReferences,
      onNavigate: setActive};
    if(active === 'source_capture') return <SourceCapture {...common} />;
    if(active === 'event_mapping') return <EventMapping {...common} />;
    if(active === 'schema_evolution') return <SchemaEvolution {...common} />;
    if(active === 'run_monitor') return <RunMonitor {...common} />;
    if(active === 'event_inspector') return <EventInspector {...common} />;
    if(active === 'replay') return <Replay {...common} />;
    return <DefinitionEditor {...common} />;
  }, [active, session]);
  return <Box sx={{height: '100%', display: 'grid', gridTemplateColumns: '288px minmax(0,1fr)'}}>
    <Box component="nav" aria-label="CDC DESIGN" sx={{overflow: 'auto', borderRight: '1px solid',
      borderColor: 'divider'}}>
      <SearchField label="Filter CDC surfaces" value={navigationQuery}
        onChange={setNavigationQuery} />
      {CDC_SURFACES.filter((item) => item.title.toLowerCase().includes(
        navigationQuery.toLowerCase())).map((item) => <TreeRow key={item.id} id={item.id} label={item.title}
        level={0} selected={active === item.id} onSelect={() => {
          setActive(item.id); service.select(sessionId, {surface: item.id});
        }} />)}
    </Box>
    <StateBoundary session={session}><Box component="main" sx={{height: '100%', minWidth: 0}}>
      {body}</Box></StateBoundary>
  </Box>;
}
CDCWorkspace.propTypes = {service: PropTypes.object.isRequired, sessionId: PropTypes.string.isRequired,
  surface: PropTypes.string, executeCommand: PropTypes.func, currentUser: PropTypes.object,
  resources: PropTypes.array, credentialReferences: PropTypes.array,
  onStateChange: PropTypes.func};

export function CDCNavigator({service, onOpen}) {
  const [query, setQuery] = useState(''); const [sessions, setSessions] = useState(service.list());
  useEffect(() => service.subscribe(() => setSessions(service.list())), [service]);
  const filtered = sessions.filter((session) => `${session.content.name} ${session.id}`
    .toLowerCase().includes(query.toLowerCase()));
  return <Box><SearchField label="Filter CDC definitions" value={query} onChange={setQuery} />
    {!filtered.length && <EmptyState message="No CDC definitions. Use Create CDC Definition." />}
    {filtered.map((session) => <TreeRow key={session.id} id={session.id}
      label={session.content.name || session.id} level={0}
      trailing={<Badge label={session.state} />}
      onSelect={() => onOpen?.(session.id, 'cdc_designer')}
      onOpen={() => onOpen?.(session.id, 'cdc_designer')} />)}
  </Box>;
}
CDCNavigator.propTypes = {service: PropTypes.object.isRequired, onOpen: PropTypes.func};

export function cdcInspector(session) {
  const run = session.runs[0]; return {selectedStage: session.selection.stage,
    nativeMechanism: session.content.source?.nativeMechanism ?? 'not configured',
    checkpoint: session.checkpoints.stream ?? 'not available',
    delivery: session.content.deliveryPolicy.guarantee,
    evolutionPolicy: session.content.schemaEvolutionPolicy.defaultAction,
    runState: run?.state ?? 'not running'};
}
