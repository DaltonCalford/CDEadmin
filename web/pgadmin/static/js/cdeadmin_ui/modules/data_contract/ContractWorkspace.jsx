/////////////////////////////////////////////////////////////
// Data Contract Manager workbench surfaces.
/////////////////////////////////////////////////////////////

import React, {useEffect, useMemo, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {Button} from '../../primitives/Button';
import {NumberField, TextArea, TextField} from '../../primitives/Field';
import {Checkbox, Select} from '../../primitives/Choice';
import {SearchField} from '../../primitives/AdvancedControls';
import {ResourcePicker, TreeRow} from '../../navigation/AdvancedNavigation';
import {Tab} from '../../navigation/TabsAndBreadcrumbs';
import {Toolbar} from '../../layout/WorkbenchChrome';
import {Badge, Banner, ProgressBar, Skeleton} from '../../feedback/Indicators';
import {EmptyState} from '../../feedback/EmptyState';
import DataGrid from '../../data/DataGrid';
import {CONTRACT_STATUSES} from './contracts';

export const CONTRACT_SURFACES = Object.freeze([
  {id: 'contract_explorer', title: 'Contract Explorer'},
  {id: 'contract_editor', title: 'Contract Editor'},
  {id: 'schema_resource_binding', title: 'Schema & Resource Binding'},
  {id: 'quality_sla', title: 'Quality & SLA'},
  {id: 'team_roles_support', title: 'Team / Roles / Support'},
  {id: 'compliance', title: 'Compliance'},
  {id: 'version_diff', title: 'Version Diff'},
]);

function useSession(service, sessionId) {
  const [session, setSession] = useState(() => service.get(sessionId));
  useEffect(() => service.subscribe((next) => {
    if(next.id === sessionId) setSession(next);
  }), [service, sessionId]);
  return session;
}

function stateStatus(state) {
  if(['runtime_failure', 'validation_error', 'permission_denied'].includes(state)) return 'error';
  if(['stale', 'partial', 'disconnected'].includes(state)) return 'warning';
  if(state === 'ready') return 'success';
  return 'info';
}

function StateBoundary({session, children}) {
  const providerLimitations = session.providerStatuses.flatMap((item) => [
    ...(item.warnings ?? []), ...(item.limitations ?? []),
  ]);
  return <>
    {session.state === 'background_task_active' && <Box sx={{p: 1}}>
      <ProgressBar label="Data Contract background task" status="indeterminate" />
    </Box>}
    {session.state === 'loading' && <Box sx={{p: 2}}><Skeleton lines={8} /></Box>}
    {session.error && <Box sx={{p: 1}}><Banner status="error">{session.error}</Banner></Box>}
    {!session.error && session.state === 'permission_denied' && <Box sx={{p: 1}}>
      <Banner status="error">Permission denied. Provider capability has not been marked unsupported.</Banner>
    </Box>}
    {!session.error && session.state === 'validation_error' && <Box sx={{p: 1}}>
      <Banner status="error">Contract validation failed. Review Validation and Problems.</Banner>
    </Box>}
    {!session.error && session.state === 'runtime_failure' && <Box sx={{p: 1}}>
      <Banner status="error">The last runtime operation failed. Re-run starts a new task attempt.</Banner>
    </Box>}
    {['stale', 'partial', 'disconnected', 'read_only'].includes(session.state) &&
      <Box sx={{p: 1}}><Banner status="warning">
        Data Contract Manager is {session.state.replace('_', ' ')}. Authored state remains visible.
      </Banner></Box>}
    {providerLimitations.length > 0 && <Box sx={{p: 1}}><Banner status="warning">
      Provider limitations: {[...new Set(providerLimitations)].join(' ')}
    </Banner></Box>}
    {children}
  </>;
}
StateBoundary.propTypes = {session: PropTypes.object.isRequired, children: PropTypes.node};

function jsonObject(text, label) {
  if(!String(text ?? '').trim()) return {};
  const value = JSON.parse(text);
  if(!value || Array.isArray(value) || typeof value !== 'object') throw new TypeError(
    `${label} must be a JSON object.`
  );
  return value;
}
function jsonArray(text, label) {
  if(!String(text ?? '').trim()) return [];
  const value = JSON.parse(text);
  if(!Array.isArray(value)) throw new TypeError(`${label} must be a JSON array.`);
  return value;
}

function permitted(currentUser, permission) {
  return currentUser.permissions?.includes(permission) === true;
}

function ContractExplorer({session, service, onNavigate}) {
  const [query, setQuery] = useState('');
  const rows = useMemo(() => session.content.elements.filter((item) => !query ||
    `${item.name} ${item.logicalType} ${item.description}`.toLowerCase()
      .includes(query.toLowerCase())).map((item) => ({...item,
    bindings: session.content.bindings.filter((binding) => binding.elementId === item.id).length,
    quality: session.content.qualityObligations.filter((rule) => rule.elementId === item.id).length,
    sla: session.content.sla.filter((level) => level.elementId === item.id).length})),
  [query, session.content]);
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Contract Explorer commands"><SearchField label="Search contract elements"
      value={query} onChange={setQuery} resultCount={rows.length} /></Toolbar>
    <Box sx={{p: 1, display: 'flex', gap: 1, flexWrap: 'wrap'}}>
      <Badge label={`Domain ${session.content.domain || 'not set'}`} />
      <Badge label={`Status ${session.content.status}`} status={session.content.status === 'active' ?
        'success' : 'info'} />
      <Badge label={`Version ${session.content.contractVersion}`} />
      <Badge label={`${session.content.bindings.length} bindings`} />
    </Box>
    {!rows.length ? <EmptyState message={query ? 'No contract elements match the current search.' :
      'Create logical contract objects or properties.'}
    actionLabel={query ? undefined : 'Open Contract Editor'}
    onAction={query ? undefined : () => onNavigate('contract_editor')} /> :
      <Box sx={{flex: 1, minHeight: 0}}><DataGrid gridId="contract/explorer"
        aria-label="Data Contract elements" rows={rows} readOnly rowKeyGetter={(row) => row.id}
        columns={[{key: 'name', name: 'Element', renderCell: ({row}) => <Button
          onClick={() => service.select(session.id, {elementId: row.id})}>{row.name}</Button>},
        {key: 'logicalType', name: 'Logical type'}, {key: 'parentId', name: 'Parent'},
        {key: 'classification', name: 'Classification'}, {key: 'bindings', name: 'Bindings'},
        {key: 'quality', name: 'Quality obligations'}, {key: 'sla', name: 'Service levels'}]} />
      </Box>}
  </Box>;
}
ContractExplorer.propTypes = {session: PropTypes.object, service: PropTypes.object,
  onNavigate: PropTypes.func};

function FundamentalsEditor({session, invoke, service, currentUser}) {
  const [draft, setDraft] = useState(() => ({name: session.content.name,
    domain: session.content.domain, description: session.content.description,
    status: session.content.status, reason: ''}));
  const update = (values) => setDraft((current) => ({...current, ...values}));
  return <Box component="section" aria-label="Contract fundamentals"
    sx={{p: 1, display: 'grid', gap: 1, gridTemplateColumns: 'repeat(4,minmax(160px,1fr))'}}>
    <TextField label="Contract name" value={draft.name}
      onChange={(event) => update({name: event.target.value})} />
    <TextField label="Business domain" value={draft.domain}
      onChange={(event) => update({domain: event.target.value})} />
    <TextField label="Description" value={draft.description}
      onChange={(event) => update({description: event.target.value})} />
    <Button intent="primary" disabled={!permitted(currentUser, 'contract.edit')}
      title={!permitted(currentUser, 'contract.edit') ? 'contract.edit permission is required.' : ''}
      onClick={() => invoke('contract.create', {content: {
        ...session.content, name: draft.name, domain: draft.domain, description: draft.description,
      }})}>Save fundamentals</Button>
    <Select label="Lifecycle status" value={draft.status}
      options={CONTRACT_STATUSES.map((value) => ({value, label: value}))}
      onChange={(status) => update({status})} />
    <TextField label="Reverse-transition audit reason" value={draft.reason}
      onChange={(event) => update({reason: event.target.value})} />
    <Button disabled={!permitted(currentUser, 'contract.activate')}
      title={!permitted(currentUser, 'contract.activate') ?
        'contract.activate permission is required.' : ''}
      onClick={() => invoke('contract.status.set', {status: draft.status,
        reason: draft.reason})}>Apply lifecycle status</Button>
    <Button onClick={() => service.validate(session.id)}>Validate structure</Button>
  </Box>;
}
FundamentalsEditor.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  service: PropTypes.object, currentUser: PropTypes.object};

function ContractEditor({session, invoke, service, currentUser}) {
  const chosen = session.content.elements.find((item) => item.id === session.selectedElementId);
  const [element, setElement] = useState({id: '', name: '', logicalType: 'object', parentId: '',
    description: '', classification: '', required: false, constraints: '{}',
    physicalDefinition: '{}', extensions: '{}'});
  const [server, setServer] = useState({id: '', providerId: '', environment: '', interface: '',
    resourceRef: '', apiRef: '', nativeDetails: '{}'});
  const [definition, setDefinition] = useState({id: '', type: '', uri: '', assetRef: '',
    description: '', extensions: '{}'}); const [error, setError] = useState('');
  const [odcsSource, setOdcsSource] = useState('');
  const [apiVersion, setApiVersion] = useState(session.interoperability?.apiVersion ?? 'v3.1');
  const canImportExport = currentUser.permissions?.includes('contract.import_export');
  useEffect(() => { if(chosen) setElement({...chosen, parentId: chosen.parentId ?? '',
    classification: chosen.classification ?? '', constraints: JSON.stringify(chosen.constraints),
    physicalDefinition: JSON.stringify(chosen.physicalDefinition),
    extensions: JSON.stringify(chosen.extensions)}); }, [chosen]);
  const saveElement = () => {
    try { const value = {...element, parentId: element.parentId || null,
      classification: element.classification || null,
      constraints: jsonObject(element.constraints, 'Element constraints'),
      physicalDefinition: jsonObject(element.physicalDefinition, 'Physical definition'),
      extensions: jsonObject(element.extensions, 'Element extensions')}; setError('');
    invoke('contract.create', {content: {...session.content,
      elements: session.content.elements.some((item) => item.id === value.id) ?
        session.content.elements.map((item) => item.id === value.id ? value : item) :
        [...session.content.elements, value]}});
    } catch(exception) { setError(exception.message); }
  };
  const saveServer = () => {
    try { const value = {...server, resourceRef: server.resourceRef ?
      jsonObject(server.resourceRef, 'Server ResourceRef') : null,
    apiRef: server.apiRef ? jsonObject(server.apiRef, 'Server API reference') : null,
    nativeDetails: jsonObject(server.nativeDetails, 'Server native details')}; setError('');
    invoke('contract.create', {content: {...session.content,
      servers: session.content.servers.some((item) => item.id === value.id) ?
        session.content.servers.map((item) => item.id === value.id ? value : item) :
        [...session.content.servers, value]}}); } catch(exception) { setError(exception.message); }
  };
  const saveDefinition = () => {
    try { const value = {...definition, uri: definition.uri || null,
      assetRef: definition.assetRef ? jsonObject(definition.assetRef, 'Definition AssetRef') : null,
      extensions: jsonObject(definition.extensions, 'Definition extensions')}; setError('');
    invoke('contract.create', {content: {...session.content,
      authoritativeDefinitions: session.content.authoritativeDefinitions.some((item) =>
        item.id === value.id) ? session.content.authoritativeDefinitions.map((item) =>
          item.id === value.id ? value : item) : [...session.content.authoritativeDefinitions, value]}});
    } catch(exception) { setError(exception.message); }
  };
  return <Box sx={{height: '100%', overflow: 'auto'}}>
    <FundamentalsEditor session={session} invoke={invoke} service={service}
      currentUser={currentUser} />
    {error && <Box sx={{p: 1}}><Banner status="error">{error}</Banner></Box>}
    <Box component="section" aria-label="ODCS source and canonical preview" sx={{p: 1,
      display: 'grid', gap: 1, gridTemplateColumns: 'minmax(280px,1fr) minmax(280px,1fr)'}}>
      <Box component="h2" sx={{gridColumn: '1/-1'}}>Source / Preview</Box>
      <TextArea label="ODCS source JSON" rows={8} value={odcsSource}
        onChange={(event) => setOdcsSource(event.target.value)} />
      <TextArea label="Canonical contract preview" rows={8} InputProps={{readOnly: true}}
        value={JSON.stringify(session.content, null, 2)} />
      <Box sx={{display: 'flex', gap: 1, alignItems: 'center'}}>
        <Button disabled={!canImportExport || !odcsSource.trim()} onClick={() => {
          try { const source = JSON.parse(odcsSource); setError('');
            invoke('contract.import.odcs', {source});
          } catch(exception) { setError(`ODCS source is invalid: ${exception.message}`); }
        }}>Import ODCS source</Button>
      </Box>
      <Box sx={{display: 'flex', gap: 1, alignItems: 'center'}}>
        <TextField label="ODCS export apiVersion" value={apiVersion}
          onChange={(event) => setApiVersion(event.target.value)} />
        <Button disabled={!canImportExport || !apiVersion.trim()}
          onClick={() => invoke('contract.export.odcs', {apiVersion})}>
          Export ODCS metadata</Button>
      </Box>
    </Box>
    <Box component="section" aria-label="Logical schema element editor" sx={{p: 1,
      display: 'grid', gap: 1, gridTemplateColumns: 'repeat(4,minmax(160px,1fr))'}}>
      <Box component="h2" sx={{gridColumn: '1/-1'}}>Schema object/property</Box>
      {['id', 'name', 'logicalType', 'parentId', 'description', 'classification'].map((field) =>
        <TextField key={field} label={field} value={element[field]}
          onChange={(event) => setElement({...element, [field]: event.target.value})} />)}
      <Checkbox label="Required" checked={element.required}
        onChange={(required) => setElement({...element, required})} />
      {['constraints', 'physicalDefinition', 'extensions'].map((field) => <TextField key={field}
        label={`${field} JSON`} value={element[field]}
        onChange={(event) => setElement({...element, [field]: event.target.value})} />)}
      <Box sx={{display: 'flex', gap: 1}}><Button intent="primary" disabled={
        !permitted(currentUser, 'contract.edit') || !element.id ||
        !element.name || !element.logicalType} onClick={saveElement}>Save element</Button>
      <Button intent="danger" disabled={!permitted(currentUser, 'contract.edit') || !chosen}
        onClick={() => invoke('contract.create', {
          content: {...session.content, elements: session.content.elements.filter((item) =>
            item.id !== chosen.id && item.parentId !== chosen.id), bindings: session.content.bindings
            .filter((item) => item.elementId !== chosen.id)}})}>Remove element</Button></Box>
    </Box>
    <Box component="section" aria-label="Contract server binding editor" sx={{p: 1,
      display: 'grid', gap: 1, gridTemplateColumns: 'repeat(4,minmax(160px,1fr))'}}>
      <Box component="h2" sx={{gridColumn: '1/-1'}}>Serving interface</Box>
      {['id', 'providerId', 'environment', 'interface', 'resourceRef', 'apiRef', 'nativeDetails']
        .map((field) => <TextField key={field} label={field.includes('Ref') || field ===
          'nativeDetails' ? `${field} JSON` : field} value={server[field]}
        onChange={(event) => setServer({...server, [field]: event.target.value})} />)}
      <Button intent="primary" disabled={!permitted(currentUser, 'contract.edit') ||
        !server.id || !server.providerId || !server.environment ||
        !server.interface || (!server.resourceRef && !server.apiRef)} onClick={saveServer}>
        Save serving interface</Button>
    </Box>
    <Box component="section" aria-label="Authoritative definition editor" sx={{p: 1,
      display: 'grid', gap: 1, gridTemplateColumns: 'repeat(4,minmax(160px,1fr))'}}>
      <Box component="h2" sx={{gridColumn: '1/-1'}}>Authoritative definition</Box>
      {['id', 'type', 'uri', 'assetRef', 'description', 'extensions'].map((field) =>
        <TextField key={field} label={field === 'assetRef' || field === 'extensions' ?
          `${field} JSON` : field} value={definition[field]}
        onChange={(event) => setDefinition({...definition, [field]: event.target.value})} />)}
      <Button intent="primary" disabled={!permitted(currentUser, 'contract.edit') ||
        !definition.id || !definition.type ||
        (!definition.uri && !definition.assetRef)} onClick={saveDefinition}>
        Save authoritative definition</Button>
    </Box>
  </Box>;
}
ContractEditor.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  service: PropTypes.object, currentUser: PropTypes.object};

function ResourceBinding({session, invoke, service, resources, currentUser}) {
  const [picker, setPicker] = useState(false); const [targetRef, setTargetRef] = useState(null);
  const [draft, setDraft] = useState({id: '', elementId: session.selectedElementId ?? '',
    environment: 'development', bindingStatus: 'declared', observedRevision: '', nativeDetails: '{}'});
  const [error, setError] = useState('');
  const save = () => { try { if(!targetRef) throw new Error('Select a resource.'); setError('');
    invoke('contract.bind_resource', {binding: {...draft, targetRef,
      observedRevision: draft.observedRevision || null,
      nativeDetails: jsonObject(draft.nativeDetails, 'Binding native details')}});
  } catch(exception) { setError(exception.message); }};
  const rows = session.content.bindings.map((item) => ({...item,
    target: item.targetRef.canonical ?? item.targetRef.id}));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Schema and resource binding commands">
      <Button disabled={!permitted(currentUser, 'contract.edit')}
        title={!permitted(currentUser, 'contract.edit') ?
          'contract.edit permission is required.' : ''}
        onClick={() => setPicker(true)}>Select provider resource</Button>
      <Button title={['stale', 'disconnected', 'read_only'].includes(session.state) ?
        'Live metadata comparison is unavailable in the current state.' : ''}
      disabled={!permitted(currentUser, 'contract.compliance') ||
        !session.selectedBindingId || ['stale', 'disconnected', 'read_only',
        'permission_denied', 'background_task_active'].includes(session.state)}
      onClick={() => invoke('contract.sync_metadata', {
        bindingId: session.selectedBindingId})}>Compare live metadata</Button>
    </Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    <Box sx={{p: 1, display: 'grid', gap: 1, gridTemplateColumns: 'repeat(6,minmax(130px,1fr))'}}>
      {['id', 'elementId', 'environment', 'bindingStatus', 'observedRevision', 'nativeDetails']
        .map((field) => <TextField key={field} label={field === 'nativeDetails' ?
          'Native details JSON' : field} value={draft[field]}
        onChange={(event) => setDraft({...draft, [field]: event.target.value})} />)}
      <Box>Target: {targetRef?.canonical ?? targetRef?.id ?? 'not selected'}</Box>
      <Button intent="primary" disabled={!permitted(currentUser, 'contract.edit') ||
        !draft.id || !draft.elementId || !targetRef}
      onClick={save}>Save resource binding</Button>
    </Box>
    {!rows.length ? <EmptyState message="Bind logical elements to provider resources or assets." /> :
      <Box sx={{flex: 1, minHeight: 0}}><DataGrid gridId="contract/bindings"
        aria-label="Contract resource bindings" rows={rows} readOnly rowKeyGetter={(row) => row.id}
        columns={[{key: 'id', name: 'Binding', renderCell: ({row}) => <Button onClick={() => {
          service.select(session.id, {bindingId: row.id}); setTargetRef(row.targetRef);
          setDraft({...row, observedRevision: row.observedRevision ?? '',
            nativeDetails: JSON.stringify(row.nativeDetails)}); }}>{row.id}</Button>},
        {key: 'elementId', name: 'Element'}, {key: 'environment', name: 'Environment'},
        {key: 'bindingStatus', name: 'Status'}, {key: 'target', name: 'Target'},
        {key: 'observedRevision', name: 'Observed revision'}]} /></Box>}
    <ResourcePicker open={picker} items={resources} selected={[]} onClose={() => setPicker(false)}
      onConfirm={(item) => { setPicker(false); if(item) setTargetRef(item.reference); }} />
  </Box>;
}
ResourceBinding.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  service: PropTypes.object, resources: PropTypes.array, currentUser: PropTypes.object};

function QualitySLA({session, invoke, currentUser}) {
  const [quality, setQuality] = useState({id: '', elementId: '', severity: 'warning',
    qualityRef: '', importedDefinition: '', threshold: '{}', description: ''});
  const [sla, setSla] = useState({id: '', elementId: '', measure: 'freshness', target: 0,
    comparison: '<=', unit: '', window: '{}', description: '', extensions: '{}'});
  const [error, setError] = useState('');
  const saveQuality = () => { try { const value = {...quality,
    elementId: quality.elementId || null,
    qualityRef: quality.qualityRef ? jsonObject(quality.qualityRef, 'Quality AssetRef') : null,
    importedDefinition: quality.importedDefinition ? jsonObject(
      quality.importedDefinition, 'Imported quality definition') : null,
    threshold: jsonObject(quality.threshold, 'Quality threshold')}; setError('');
  invoke('contract.create', {content: {...session.content,
    qualityObligations: session.content.qualityObligations.some((item) => item.id === value.id) ?
      session.content.qualityObligations.map((item) => item.id === value.id ? value : item) :
      [...session.content.qualityObligations, value]}}); } catch(exception) { setError(exception.message); }};
  const saveSla = () => { try { const value = {...sla, elementId: sla.elementId || null,
    target: Number.isNaN(Number(sla.target)) ? sla.target : Number(sla.target),
    unit: sla.unit || null, window: jsonObject(sla.window, 'SLA window'),
    extensions: jsonObject(sla.extensions, 'SLA extensions')}; setError('');
  invoke('contract.create', {content: {...session.content,
    sla: session.content.sla.some((item) => item.id === value.id) ? session.content.sla.map(
      (item) => item.id === value.id ? value : item) : [...session.content.sla, value]}});
  } catch(exception) { setError(exception.message); }};
  return <Box sx={{height: '100%', overflow: 'auto', p: 1}}>
    {error && <Banner status="error">{error}</Banner>}
    <Box component="h2">Quality obligations</Box>
    <DataGrid gridId="contract/quality" aria-label="Contract quality obligations"
      rows={session.content.qualityObligations} readOnly rowKeyGetter={(row) => row.id}
      columns={[{key: 'id', name: 'Obligation', renderCell: ({row}) => <Button onClick={() =>
        setQuality({...row, elementId: row.elementId ?? '', qualityRef: row.qualityRef ?
          JSON.stringify(row.qualityRef) : '', importedDefinition: row.importedDefinition ?
          JSON.stringify(row.importedDefinition) : '', threshold: JSON.stringify(row.threshold)})}>
        {row.id}</Button>}, {key: 'elementId', name: 'Element'}, {key: 'severity', name: 'Severity'},
      {key: 'description', name: 'Description'}]} />
    <Box sx={{display: 'grid', gap: 1, gridTemplateColumns: 'repeat(4,minmax(160px,1fr))'}}>
      {Object.keys(quality).map((field) => <TextField key={field} label={field.includes('Ref') ||
        ['importedDefinition', 'threshold'].includes(field) ? `${field} JSON` : field}
      value={quality[field]} onChange={(event) => setQuality({...quality,
        [field]: event.target.value})} />)}
      <Button intent="primary" disabled={!permitted(currentUser, 'contract.edit') ||
        !quality.id || (!quality.qualityRef &&
        !quality.importedDefinition)} onClick={saveQuality}>Save quality obligation</Button>
      <Button intent="danger" disabled={!permitted(currentUser, 'contract.edit')}
        onClick={() => invoke('contract.create', {content: {
          ...session.content, qualityObligations: session.content.qualityObligations.filter(
            (item) => item.id !== quality.id)}})}>Remove quality obligation</Button>
    </Box>
    <Box component="h2">Service levels</Box>
    <DataGrid gridId="contract/sla" aria-label="Contract service levels" rows={session.content.sla}
      readOnly rowKeyGetter={(row) => row.id} columns={[{key: 'id', name: 'Service level',
        renderCell: ({row}) => <Button onClick={() => setSla({...row,
          elementId: row.elementId ?? '', unit: row.unit ?? '', window: JSON.stringify(row.window),
          extensions: JSON.stringify(row.extensions)})}>{row.id}</Button>},
      {key: 'measure', name: 'Measure'}, {key: 'comparison', name: 'Comparison'},
      {key: 'target', name: 'Target'}, {key: 'unit', name: 'Unit'}]} />
    <Box sx={{display: 'grid', gap: 1, gridTemplateColumns: 'repeat(4,minmax(160px,1fr))'}}>
      {Object.keys(sla).map((field) => field === 'target' ? <NumberField key={field} label={field}
        value={sla[field]} onChange={(event) => setSla({...sla, target: event.target.value})} /> :
        <TextField key={field} label={['window', 'extensions'].includes(field) ? `${field} JSON` :
          field} value={sla[field]} onChange={(event) => setSla({...sla,
          [field]: event.target.value})} />)}
      <Button intent="primary" disabled={!permitted(currentUser, 'contract.edit') ||
        !sla.id || !sla.measure || !sla.comparison}
      onClick={saveSla}>Save service level</Button>
      <Button intent="danger" disabled={!permitted(currentUser, 'contract.edit')}
        onClick={() => invoke('contract.create', {content: {
          ...session.content, sla: session.content.sla.filter((item) => item.id !== sla.id)}})}>
        Remove service level</Button>
    </Box>
  </Box>;
}
QualitySLA.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object};

function TeamRoles({session, invoke, currentUser}) {
  const [kind, setKind] = useState('team'); const [draft, setDraft] = useState({id: '', name: '',
    principalRefs: '[]', responsibilities: '[]', accessExpectations: '[]', support: '{}',
    extensions: '{}'}); const [error, setError] = useState('');
  const rows = session.content[kind];
  const load = (row) => setDraft({...row, principalRefs: JSON.stringify(row.principalRefs),
    responsibilities: JSON.stringify(row.responsibilities),
    accessExpectations: JSON.stringify(row.accessExpectations), support: JSON.stringify(row.support),
    extensions: JSON.stringify(row.extensions)});
  const save = () => { try { const value = {...draft,
    principalRefs: jsonArray(draft.principalRefs, 'Principal references'),
    responsibilities: jsonArray(draft.responsibilities, 'Responsibilities'),
    accessExpectations: jsonArray(draft.accessExpectations, 'Access expectations'),
    support: jsonObject(draft.support, 'Support definition'),
    extensions: jsonObject(draft.extensions, 'Role extensions')}; setError('');
  invoke('contract.create', {content: {...session.content,
    [kind]: rows.some((item) => item.id === value.id) ? rows.map((item) =>
      item.id === value.id ? value : item) : [...rows, value]}}); } catch(exception) {
    setError(exception.message); }};
  return <Box sx={{height: '100%', overflow: 'auto', p: 1}}>
    {error && <Banner status="error">{error}</Banner>}
    <Select label="Ownership section" value={kind} options={[{value: 'team', label: 'Team / support'},
      {value: 'roles', label: 'Access roles'}]} onChange={setKind} />
    <DataGrid gridId={`contract/${kind}`} aria-label="Contract team roles and support" rows={rows}
      readOnly rowKeyGetter={(row) => row.id} columns={[{key: 'name', name: 'Role',
        renderCell: ({row}) => <Button onClick={() => load(row)}>{row.name}</Button>},
      {key: 'responsibilities', name: 'Responsibilities'},
      {key: 'accessExpectations', name: 'Access expectations'}]} />
    <Box sx={{display: 'grid', gap: 1, gridTemplateColumns: 'repeat(3,minmax(200px,1fr))'}}>
      {Object.keys(draft).map((field) => <TextField key={field} label={[
        'principalRefs', 'responsibilities', 'accessExpectations', 'support', 'extensions',
      ].includes(field) ? `${field} JSON` : field} value={draft[field]}
      onChange={(event) => setDraft({...draft, [field]: event.target.value})} />)}
      <Button intent="primary" disabled={!permitted(currentUser, 'contract.edit') ||
        !draft.id || !draft.name} onClick={save}>Save role</Button>
      <Button intent="danger" disabled={!permitted(currentUser, 'contract.edit')}
        onClick={() => invoke('contract.create', {content: {
          ...session.content, [kind]: rows.filter((item) => item.id !== draft.id)}})}>Remove role</Button>
    </Box>
  </Box>;
}
TeamRoles.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object};

function Compliance({session, invoke, service, currentUser}) {
  const run = session.complianceRuns[0]; const comparisons = session.metadataComparison?.comparisons ?? [];
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Contract compliance commands">
      <Button title={['stale', 'disconnected', 'read_only'].includes(session.state) ?
        'Live compliance is unavailable in the current state.' : ''}
      disabled={!permitted(currentUser, 'contract.compliance') ||
        !session.validation.valid || !session.content.bindings.length ||
        ['stale', 'disconnected', 'read_only', 'permission_denied',
          'background_task_active'].includes(session.state)}
      onClick={() => invoke('contract.compliance.run', {qualityResults: []})}>
        Run live compliance</Button>
      <Button onClick={() => service.validate(session.id)}>Validate authored structure</Button>
    </Toolbar>
    <Box sx={{p: 1, display: 'flex', gap: 1, flexWrap: 'wrap'}}>
      {session.content.bindings.map((item) => <Badge key={item.id}
        label={`${item.environment}: ${item.targetRef.canonical ?? item.targetRef.id}`} />)}
    </Box>
    <Box sx={{p: 1}}><Banner status={session.validation.valid ? 'success' : 'error'}>
      Structure: {session.validation.valid ? 'valid' : session.validation.details.join(' ')}
    </Banner></Box>
    {!run ? <EmptyState message="Run live compliance to distinguish contract validity from observed compliance." /> : <>
      <Box sx={{height: '45%', minHeight: 160}}><DataGrid gridId="contract/compliance"
        aria-label="Data Contract compliance dimensions" rows={run.summary.dimensions}
        readOnly rowKeyGetter={(row) => row.dimension} columns={[
          {key: 'dimension', name: 'Dimension'}, {key: 'compliant', name: 'Compliant'},
          {key: 'details', name: 'Details'}]} /></Box>
      <Box sx={{height: '45%', minHeight: 160}}><DataGrid gridId="contract/drift"
        aria-label="Data Contract provider drift" rows={run.drift.map((item, index) => ({
          ...item, id: `${index}:${item.state}`, differenceCount: item.differences.length}))}
        readOnly rowKeyGetter={(row) => row.id} columns={[{key: 'state', name: 'Drift state'},
          {key: 'observedRevision', name: 'Observed revision'},
          {key: 'differenceCount', name: 'Differences'}]} /></Box>
    </>}
    {comparisons.length > 0 && <Box sx={{p: 1}}>Latest metadata comparison: {
      comparisons.map((item) => `${item.bindingId}=${item.drift.state}`).join(', ')}</Box>}
  </Box>;
}
Compliance.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  service: PropTypes.object, currentUser: PropTypes.object};

function VersionDiff({session, invoke, service, currentUser}) {
  const versions = session.versions.map((item) => item.contractVersion);
  const [newVersion, setNewVersion] = useState('');
  const [left, setLeft] = useState(versions[0] ?? ''); const [right, setRight] = useState(
    versions.at(-1) ?? ''); const rows = session.versionDiff?.differences ?? [];
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Contract version commands">
      <TextField label="New contract version" value={newVersion}
        onChange={(event) => setNewVersion(event.target.value)} />
      <Button disabled={!permitted(currentUser, 'contract.edit') || !newVersion}
        onClick={() => invoke('contract.version.create', {
          version: newVersion})}>Create immutable version</Button>
      <Select label="Left version" value={left} options={versions.map((value) => ({value,
        label: value}))} onChange={setLeft} />
      <Select label="Right version" value={right} options={versions.map((value) => ({value,
        label: value}))} onChange={setRight} />
      <Button disabled={!left || !right} onClick={() => service.compareVersions(
        session.id, left, right)}>Compare versions</Button>
    </Toolbar>
    {!session.versionDiff ? <EmptyState message="Select two immutable contract versions." /> :
      <DataGrid gridId="contract/version-diff" aria-label="Data Contract version differences"
        rows={rows} readOnly rowKeyGetter={(row) => row.path} columns={[
          {key: 'path', name: 'Canonical path'}, {key: 'state', name: 'State'},
          {key: 'left', name: 'Left'}, {key: 'right', name: 'Right'}]} />}
  </Box>;
}
VersionDiff.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  service: PropTypes.object, currentUser: PropTypes.object};

const VIEWS = Object.freeze({contract_explorer: ContractExplorer, contract_editor: ContractEditor,
  schema_resource_binding: ResourceBinding, quality_sla: QualitySLA,
  team_roles_support: TeamRoles, compliance: Compliance, version_diff: VersionDiff});

export function ContractWorkspace({service, sessionId, surface='contract_explorer',
  executeCommand, resources=[], currentUser={}, onExport=() => {}, onStateChange}) {
  const session = useSession(service, sessionId); const [active, setActive] = useState(surface);
  useEffect(() => onStateChange?.(session), [session, onStateChange]);
  const invoke = async (commandId, args={}) => {
    try { const result = await executeCommand(commandId, args,
      {sessionId, service, currentUser});
    if(commandId === 'contract.export.odcs' && result) onExport(result); return result;
    } catch(error) { const prefix = String(error.message).split(':')[0].replaceAll(' ', '_');
      service.reportError(sessionId, error, prefix); return undefined; }
  };
  const View = VIEWS[active] ?? ContractExplorer;
  return <Box data-module="cdeadmin.contract"
    sx={{height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column'}}>
    <Box role="tablist" aria-label="Data Contract Manager surfaces"
      sx={{display: 'flex', overflowX: 'auto', borderBottom: '1px solid', borderColor: 'divider'}}>
      {CONTRACT_SURFACES.map((item) => <Tab key={item.id} label={item.title}
        active={active === item.id} attention={item.id === 'compliance' &&
          session.complianceRuns[0] && !session.complianceRuns[0].summary.compliant}
        onActivate={() => setActive(item.id)} />)}
    </Box>
    <Box sx={{px: 1, py: 0.5, display: 'flex', gap: 1, alignItems: 'center'}}>
      <Badge status={stateStatus(session.state)} label={session.state.replaceAll('_', ' ')} />
      <Box>{session.content.elements.length} elements · {session.content.bindings.length} bindings</Box>
      <Badge label={`${session.content.status} · ${session.content.contractVersion}`} />
      {session.providerStatuses.map((item) => <Badge key={item.providerId}
        label={`${item.providerId}: ${item.supportState}`}
        status={item.supportState.startsWith('supported') ? 'success' : 'warning'} />)}
      {session.dirty && <Badge status="warning" label="Unsaved" />}
    </Box>
    <Box sx={{flex: 1, minHeight: 0, overflow: 'hidden'}}><StateBoundary session={session}>
      <View session={session} invoke={invoke} resources={resources} service={service}
        currentUser={currentUser} onExport={onExport} onNavigate={setActive} />
    </StateBoundary></Box>
  </Box>;
}
ContractWorkspace.propTypes = {service: PropTypes.object.isRequired,
  sessionId: PropTypes.string.isRequired,
  surface: PropTypes.oneOf(CONTRACT_SURFACES.map((item) => item.id)),
  executeCommand: PropTypes.func.isRequired, resources: PropTypes.array,
  currentUser: PropTypes.object, onExport: PropTypes.func, onStateChange: PropTypes.func};

export function contractInspector(session) {
  const element = session?.content.elements.find((item) => item.id === session.selectedElementId);
  const bindings = session?.content.bindings.filter((item) => item.elementId === element?.id) ?? [];
  return {'Selected element': element ? `${element.name} · ${element.logicalType}` : 'None selected',
    'Resource binding': bindings.map((item) => item.targetRef.canonical ?? item.targetRef.id).join(', ') ||
      'Unbound', References: element?.authoritativeDefinitionRefs?.map((item) =>
      item.id ?? item.assetId).join(', ') || 'None',
    'Quality obligations': session?.content.qualityObligations.filter((item) =>
      !item.elementId || item.elementId === element?.id).map((item) => item.id).join(', ') || 'None',
    History: `${session?.history.length ?? 0} auditable session events`};
}

export function ContractNavigator({service, onOpen}) {
  const [sessions, setSessions] = useState(() => service.list());
  const [expanded, setExpanded] = useState(() => new Set());
  useEffect(() => service.subscribe(() => setSessions(service.list())), [service]);
  return <Box role="tree" aria-label="Data Contracts" sx={{height: '100%', overflow: 'auto'}}>
    {!sessions.length && <EmptyState message="No Data Contract assets are open." />}
    {sessions.map((session) => { const open = expanded.has(session.id); return <React.Fragment
      key={session.id}><TreeRow label={session.content.name || session.id} level={1}
        expandable expanded={open} trailing={<Badge label={session.content.status} />}
        onToggle={() => setExpanded((prior) => { const next = new Set(prior);
          open ? next.delete(session.id) : next.add(session.id); return next; })}
        onOpen={() => onOpen?.(session.id, 'contract_explorer')} />
      {open && [{label: 'Fundamentals', surface: 'contract_editor'},
        {label: 'Schema', surface: 'contract_editor'},
        {label: 'Quality', surface: 'quality_sla'},
        {label: 'SLA', surface: 'quality_sla'},
        {label: 'Servers', surface: 'contract_editor'},
        {label: 'Team', surface: 'team_roles_support'},
        {label: 'Roles', surface: 'team_roles_support'},
        {label: 'Definitions', surface: 'contract_editor'},
        {label: 'Resource Bindings', surface: 'schema_resource_binding'},
        {label: 'Compliance', surface: 'compliance'},
        {label: 'Versions', surface: 'version_diff'}].map((item) => <TreeRow key={item.label}
        label={item.label} level={2} onOpen={() => onOpen?.(session.id, item.surface)} />)}
    </React.Fragment>; })}
  </Box>;
}
ContractNavigator.propTypes = {service: PropTypes.object.isRequired, onOpen: PropTypes.func};
