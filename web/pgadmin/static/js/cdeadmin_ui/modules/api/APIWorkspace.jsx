/////////////////////////////////////////////////////////////
// API Designer Zero Grey workbench surfaces.
/////////////////////////////////////////////////////////////

import {useEffect, useMemo, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {Button} from '../../primitives/Button';
import {Select} from '../../primitives/Choice';
import {TextArea, TextField} from '../../primitives/Field';
import {ResourcePicker, TreeRow} from '../../navigation/AdvancedNavigation';
import {Toolbar} from '../../layout/WorkbenchChrome';
import {Badge, Banner, ProgressBar, Skeleton} from '../../feedback/Indicators';
import {EmptyState} from '../../feedback/EmptyState';
import DataGrid from '../../data/DataGrid';
import {exportAsyncAPI, exportOpenAPI} from './APIEngine';

export const API_SURFACES = Object.freeze([
  {id: 'api_explorer', title: 'API Explorer'}, {id: 'operation_designer', title: 'Operation Designer'},
  {id: 'event_api_designer', title: 'Event API Designer'}, {id: 'schema_designer', title: 'Schema Designer'},
  {id: 'security_policy', title: 'Security & Policy'}, {id: 'test_console', title: 'Test Console'},
  {id: 'docs_preview', title: 'Docs Preview'}, {id: 'source', title: 'Source'},
]);
function useSession(service, id) { const [session, setSession] = useState(() => service.get(id));
  useEffect(() => service.subscribe((next) => { if(next.id === id) setSession(next); }), [service, id]);
  return session; }
function permitted(user, permission) { return user.permissions?.includes(permission) === true; }
function actionable(session, user, permission) { return permitted(user, permission) && ![
  'loading', 'read_only', 'permission_denied', 'disconnected', 'background_task_active',
].includes(session.state); }
function invokeSafely(execute, command, args, setError) { setError('');
  return Promise.resolve(execute(command, args)).catch((error) => setError(error.message)); }
function StateBoundary({session, children}) {
  const limitations = session.providerStatuses.flatMap((item) => [
    ...(item.warnings ?? []), ...(item.limitations ?? []),
  ]);
  return <>{session.state === 'loading' && <Box sx={{p: 2}}><Skeleton lines={8} /></Box>}
    {session.state === 'empty' && <EmptyState message="Create or import an API definition to begin." />}
    {session.state === 'background_task_active' && <ProgressBar label="API background task" status="indeterminate" />}
    {session.error && <Banner status="error">{session.error}</Banner>}
    {!session.error && session.state === 'runtime_failure' && <Banner status="error">
      API runtime failure. Authored changes remain available.</Banner>}
    {session.state === 'permission_denied' && <Banner status="error">
      Permission denied. This is distinct from provider support.</Banner>}
    {['stale', 'partial', 'disconnected', 'read_only', 'validation_error'].includes(session.state) &&
      <Banner status="warning">API definition is {session.state.replaceAll('_', ' ')}.
        Last safe authored state remains visible.</Banner>}
    {limitations.length > 0 && <Banner status="warning">Provider limitations: {
      [...new Set(limitations)].join(' ')}</Banner>}{children}</>;
}
StateBoundary.propTypes = {session: PropTypes.object, children: PropTypes.node};

function Explorer({session, service, execute, currentUser}) {
  const [error, setError] = useState('');
  const groups = [['Info', []], ['Servers', session.content.servers], ['Operations', session.content.operations],
    ['Channels', session.content.channels], ['Schemas', session.content.schemas],
    ['Security', session.content.security], ['Tests', session.content.tests]];
  return <Box sx={{height: '100%', overflow: 'auto'}}><Toolbar label="API definition controls">
    <Button disabled={!actionable(session, currentUser, 'api.view')}
      onClick={() => invokeSafely(execute, 'api.validate', {}, setError)}>Validate definition</Button>
  </Toolbar>{error && <Banner status="error">{error}</Banner>}
  <Box role="tree" aria-label="API definition explorer">{groups.map(([label, values]) => <Box key={label}>
    <TreeRow label={`${label} (${values.length})`} level={1} expandable expanded />
    {values.map((item) => <TreeRow key={item.id} label={item.name ?? item.id} level={2}
      selected={session.selectedId === item.id} onSelect={() => service.select(session.id,
        {selectedId: item.id})} />)}</Box>)}</Box></Box>;
}
Explorer.propTypes = {session: PropTypes.object, service: PropTypes.object,
  execute: PropTypes.func, currentUser: PropTypes.object};

function OperationDesigner({session, service, execute, currentUser, resources=[]}) {
  const [method, setMethod] = useState('GET'); const [path, setPath] = useState('/resource');
  const [name, setName] = useState(''); const [error, setError] = useState('');
  const [operationId, setOperationId] = useState(session.content.operations[0]?.id ?? '');
  const [bindingType, setBindingType] = useState('provider_resource_read');
  const [bindingMode, setBindingMode] = useState('read'); const [resource, setResource] = useState(null);
  const [picker, setPicker] = useState(false);
  const rows = session.content.operations.map((item) => ({...item,
    bindingType: item.binding?.type ?? 'unbound', target: item.binding?.targetRef?.canonical ?? ''}));
  const add = () => invokeSafely(execute, 'api.operation.add', {operation: {id: name,
    name, method, path, parameters: [], responses: [], securityRequirementIds: [], policyIds: [],
    tags: [], callbacks: {}, extensions: []}}, setError);
  const bind = () => invokeSafely(execute, 'api.binding.set', {operationId, binding: {
    id: `binding-${operationId}`, type: bindingType, targetRef: resource.reference, mode: bindingMode,
    command: null, inputMap: {}, outputMap: {}, nativeDetails: {}, extensions: []}}, setError);
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="API operation controls"><TextField label="Operation ID" value={name}
      onChange={(event) => setName(event.target.value)} /><Select label="Method" value={method}
      onChange={setMethod} options={['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((value) =>
        ({value, label: value}))} /><TextField label="Path" value={path}
      onChange={(event) => setPath(event.target.value)} /><Button
      disabled={!actionable(session, currentUser, 'api.edit') || !name || !path}
      onClick={add}>Add operation</Button></Toolbar>
    <Toolbar label="API data binding controls"><Select label="Operation to bind" value={operationId}
      onChange={setOperationId} options={session.content.operations.map((item) =>
        ({value: item.id, label: item.name}))} /><Select label="Binding type" value={bindingType}
      onChange={setBindingType} options={['saved_query', 'provider_resource_read', 'provider_command',
        'stored_procedure/function', 'semantic_model', 'pipeline/macro', 'custom_backend_handler']
        .map((value) => ({value, label: value}))} /><Select label="Binding mode" value={bindingMode}
      onChange={setBindingMode} options={['read', 'write', 'read_write', 'invoke', 'publish', 'subscribe']
        .map((value) => ({value, label: value}))} /><Button
      disabled={!actionable(session, currentUser, 'api.edit') || !resources.length}
      onClick={() => setPicker(true)}>Choose resource</Button><Button
      disabled={!actionable(session, currentUser, 'api.edit') || !operationId || !resource}
      onClick={bind}>Apply binding</Button><Box>{resource?.label ?? 'No resource selected'}</Box></Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    {!rows.length ? <EmptyState message="No HTTP or RPC operations are defined." /> :
      <DataGrid gridId="api/operations" aria-label="API operations" rows={rows} readOnly enableRowSelect
        rowKeyGetter={(row) => row.id} onItemEnter={(row) => row && service.select(session.id,
          {selectedId: row.id})} columns={[{key: 'method', name: 'Method'}, {key: 'path', name: 'Path'},
          {key: 'name', name: 'Operation'}, {key: 'bindingType', name: 'Binding'},
          {key: 'target', name: 'Resource'}, {key: 'idempotent', name: 'Idempotent'}]} />}
    <ResourcePicker open={picker} items={resources} selected={resource ? [resource] : []}
      onClose={() => setPicker(false)} onConfirm={(item) => { setResource(item); setPicker(false); }} />
  </Box>;
}
OperationDesigner.propTypes = {session: PropTypes.object, service: PropTypes.object,
  execute: PropTypes.func, currentUser: PropTypes.object, resources: PropTypes.array};

function EventDesigner({session, service}) {
  const rows = session.content.channels.map((item) => ({...item, servers: item.serverIds.join(', '),
    messages: item.messageIds.join(', '), bindingsText: JSON.stringify(item.bindings)}));
  return <Box sx={{height: '100%'}}>{!rows.length ? <EmptyState
    message="No AsyncAPI channels are defined. Import an AsyncAPI 3.1 contract or update the asset." /> :
    <DataGrid gridId="api/channels" aria-label="Event API channels" rows={rows} readOnly enableRowSelect
      rowKeyGetter={(row) => row.id} onItemEnter={(row) => row && service.select(session.id,
        {selectedId: row.id})} columns={[{key: 'name', name: 'Channel'}, {key: 'address', name: 'Address'},
        {key: 'servers', name: 'Servers'}, {key: 'messages', name: 'Messages'},
        {key: 'bindingsText', name: 'Protocol bindings'}]} />}</Box>;
}
EventDesigner.propTypes = {session: PropTypes.object, service: PropTypes.object};

function SchemaDesigner({session, service}) {
  const rows = session.content.schemas.flatMap((schema) => schema.fields.length ? schema.fields.map((field) => ({
    ...field, id: `${schema.id}:${field.id}`, schemaName: schema.name, kind: schema.kind,
    definitionText: JSON.stringify(field.definition)})) : [{id: schema.id, schemaName: schema.name,
    name: '(schema)', kind: schema.kind, required: '', definitionText: JSON.stringify(schema.definition)}]);
  return <Box sx={{height: '100%'}}>{!rows.length ? <EmptyState message="No reusable schemas are defined." /> :
    <DataGrid gridId="api/schemas" aria-label="API reusable schemas" rows={rows} readOnly enableRowSelect
      rowKeyGetter={(row) => row.id} onItemEnter={(row) => row && service.select(session.id,
        {selectedId: row.id.split(':')[0]})} columns={[{key: 'schemaName', name: 'Schema'},
        {key: 'name', name: 'Field'}, {key: 'kind', name: 'Kind'}, {key: 'required', name: 'Required'},
        {key: 'definitionText', name: 'Definition'}]} />}</Box>;
}
SchemaDesigner.propTypes = {session: PropTypes.object, service: PropTypes.object};

function SecurityPolicy({session, execute, currentUser}) {
  const [deploymentId, setDeploymentId] = useState(session.content.deploymentBindings[0]?.id ?? '');
  const deployment = session.content.deploymentBindings.find((item) => item.id === deploymentId);
  const [confirmationRef, setConfirmationRef] = useState(''); const [error, setError] = useState('');
  const prepare = () => invokeSafely(execute, 'api.deploy.prepare', {deploymentId, confirmationRef,
    environment: deployment.environment, connection: deployment.targetRef.schema === 'cdeadmin.resource-ref.v1' ?
      `resource:${deployment.targetRef.canonical}` : deployment.targetRef.schema === 'cdeadmin.asset-ref.v1' ?
        `asset:${deployment.targetRef.projectId}/${deployment.targetRef.assetId}` :
        `${deployment.targetRef.schema}:${deployment.targetRef.id}`}, setError);
  const security = session.content.security.map((item) => ({...item,
    credentialState: item.credentialRef ? 'credential reference configured' : 'no credential reference'}));
  const policies = session.content.policies.map((item) => ({...item, credentialState: 'not applicable'}));
  const rows = [...security, ...policies];
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}><Banner status="info">
    API assets store credential references only; raw credentials are forbidden.</Banner>
  {error && <Banner status="error">{error}</Banner>}
  {!rows.length ? <EmptyState message="No security requirements or operational policies are defined." /> :
    <DataGrid gridId="api/security" aria-label="API security and policy" rows={rows} readOnly
      rowKeyGetter={(row) => row.id} columns={[{key: 'name', name: 'Name'}, {key: 'type', name: 'Type'},
        {key: 'scheme', name: 'Scheme'}, {key: 'credentialState', name: 'Credential state'}]} />}
  <Toolbar label="API deployment preparation"><Select label="Deployment binding" value={deploymentId}
    onChange={setDeploymentId} options={session.content.deploymentBindings.map((item) =>
      ({value: item.id, label: `${item.name} · ${item.environment}`}))} />
  <TextField label="Deployment confirmation reference" value={confirmationRef}
    onChange={(event) => setConfirmationRef(event.target.value)} /><Button
    disabled={!actionable(session, currentUser, 'api.deploy_prepare') || !deployment || !confirmationRef}
    onClick={prepare}>Prepare deployment package</Button></Toolbar></Box>;
}
SecurityPolicy.propTypes = {session: PropTypes.object, execute: PropTypes.func,
  currentUser: PropTypes.object};

function TestConsole({session, execute, currentUser}) {
  const [testId, setTestId] = useState(session.content.tests[0]?.id ?? '');
  const [confirmationRef, setConfirmationRef] = useState(''); const [error, setError] = useState('');
  const test = session.content.tests.find((item) => item.id === testId);
  const run = () => invokeSafely(execute, 'api.test.run', {testId,
    confirmationRef: confirmationRef || undefined, environment: test?.environmentId}, setError);
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    {test?.mode === 'live' && <Banner status="warning">LIVE environment: {test.environmentId}</Banner>}
    <Toolbar label="API test controls"><Select label="Test" value={testId} onChange={setTestId}
      options={session.content.tests.map((item) => ({value: item.id, label: `${item.name} · ${item.mode}`}))} />
    <TextField label="Write confirmation reference" value={confirmationRef}
      onChange={(event) => setConfirmationRef(event.target.value)} /><Button
      disabled={!actionable(session, currentUser, 'api.test') || !testId}
      onClick={run}>Run selected test</Button></Toolbar>{error && <Banner status="error">{error}</Banner>}
    {!session.runtime.testResults.length ? <EmptyState message="No API test results are available." /> :
      <DataGrid gridId="api/test-results" aria-label="API test results" rows={session.runtime.testResults}
        readOnly rowKeyGetter={(row) => row.id} columns={[{key: 'testId', name: 'Test'},
          {key: 'mode', name: 'Mode'}, {key: 'environmentId', name: 'Environment'},
          {key: 'status', name: 'Status'}, {key: 'at', name: 'Time'}]} />}
  </Box>;
}
TestConsole.propTypes = {session: PropTypes.object, execute: PropTypes.func,
  currentUser: PropTypes.object};

function DocsPreview({session}) {
  return <Box component="article" aria-label="API documentation preview" sx={{p: 2, overflow: 'auto'}}>
    <Box component="h2">{session.content.info.title ?? 'Untitled API'}</Box>
    <Box>{session.content.info.description ?? ''}</Box><Box component="h3">Operations</Box>
    {session.content.operations.map((item) => <Box component="section" key={item.id} sx={{mb: 2}}>
      <Badge label={`${item.method} ${item.path}`} /><Box component="h4">{item.name}</Box>
      <Box>{item.summary || item.description}</Box><Box>Responses: {
        item.responses.map((response) => response.status).join(', ') || 'not defined'}</Box></Box>)}
    <Box component="h3">Channels</Box>{session.content.channels.map((item) =>
      <Box key={item.id}>{item.name}: {item.address}</Box>)}
  </Box>;
}
DocsPreview.propTypes = {session: PropTypes.object};

function Source({session, execute, currentUser}) {
  const initial = useMemo(() => JSON.stringify(session.content.profile === 'openapi_http' ?
    exportOpenAPI(session.content) : session.content.profile === 'asyncapi_event' ?
      exportAsyncAPI(session.content) : session.content, null, 2), [session.content]);
  const [source, setSource] = useState(initial); const [error, setError] = useState('');
  useEffect(() => setSource(initial), [initial]);
  const profile = session.content.profile;
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="API source controls"><Button disabled={!actionable(session, currentUser, 'api.edit') ||
      !['openapi_http', 'asyncapi_event'].includes(profile)} onClick={() => invokeSafely(execute,
      profile === 'openapi_http' ? 'api.import.openapi' : 'api.import.asyncapi', {source}, setError)}>
      Validate and import source</Button><Button disabled={!actionable(session, currentUser, 'api.view') ||
        !['openapi_http', 'asyncapi_event'].includes(profile)} onClick={() => invokeSafely(execute,
      profile === 'openapi_http' ? 'api.export.openapi' : 'api.export.asyncapi', {}, setError)}>
      Export source</Button></Toolbar>{error && <Banner status="error">{error}</Banner>}
    <TextArea label={`${profile} source`} value={source}
      onChange={(event) => setSource(event.target.value)} rows={20} /></Box>;
}
Source.propTypes = {session: PropTypes.object, execute: PropTypes.func, currentUser: PropTypes.object};

const COMPONENTS = {api_explorer: Explorer, operation_designer: OperationDesigner,
  event_api_designer: EventDesigner, schema_designer: SchemaDesigner, security_policy: SecurityPolicy,
  test_console: TestConsole, docs_preview: DocsPreview, source: Source};

export function APIWorkspace({service, sessionId, surface='api_explorer', executeCommand,
  currentUser={}, resources=[], onStateChange}) {
  const session = useSession(service, sessionId); const [active, setActive] = useState(surface);
  useEffect(() => setActive(surface), [surface]); useEffect(() => onStateChange?.(session),
    [onStateChange, session]); const Component = COMPONENTS[active] ?? Explorer;
  const navigate = (next) => { setActive(next); service.select(sessionId, {surface: next}); };
  return <Box data-testid="api-workspace" sx={{height: '100%', display: 'grid',
    gridTemplateColumns: '288px minmax(0, 1fr) 340px'}}><Box component="nav"
      aria-label="API Designer surfaces" sx={{overflow: 'auto'}}>{API_SURFACES.map((item) =>
        <TreeRow key={item.id} label={item.title} selected={active === item.id}
          onSelect={() => navigate(item.id)} />)}</Box><Box component="main" sx={{minWidth: 0,
      overflow: 'hidden'}}><StateBoundary session={session}><Component session={session}
        service={service} execute={executeCommand} currentUser={currentUser} resources={resources} />
      </StateBoundary></Box>
    <Box component="aside" aria-label="API Designer inspector" sx={{overflow: 'auto', p: 1}}>
      <APIInspector session={session} /></Box></Box>;
}
APIWorkspace.propTypes = {service: PropTypes.object.isRequired, sessionId: PropTypes.string.isRequired,
  surface: PropTypes.string, executeCommand: PropTypes.func.isRequired, currentUser: PropTypes.object,
  resources: PropTypes.array, onStateChange: PropTypes.func};
export function APINavigator({service, onOpen}) {
  const [sessions, setSessions] = useState(() => service.list());
  useEffect(() => service.subscribe(() => setSessions(service.list())), [service]);
  return <Box role="tree" aria-label="API definitions">{sessions.length ? sessions.map((session) =>
    <TreeRow key={session.id} label={`${session.content.info.title || session.id} · ${session.content.profile} · ${
      session.state}`} onSelect={() => onOpen(session.id, session.surface)} />) :
    <EmptyState message="No API definitions are open." />}</Box>;
}
APINavigator.propTypes = {service: PropTypes.object.isRequired, onOpen: PropTypes.func.isRequired};
function APIInspector({session}) {
  const values = [...session.content.operations, ...session.content.channels, ...session.content.schemas,
    ...session.content.security, ...session.content.tests];
  const selected = values.find((item) => item.id === session.selectedId);
  return <Box><Box component="h3">Selected schema/field</Box><pre>{JSON.stringify(selected ?? {}, null, 2)}</pre>
    <Box component="h3">Data binding</Box><pre>{JSON.stringify(selected?.binding ?? {}, null, 2)}</pre>
    <Box component="h3">Permissions</Box><Box>{selected?.securityRequirementIds?.join(', ') || 'None'}</Box>
    <Box component="h3">Version notes</Box><Box>{session.content.externalSpecVersion ?? 'Native profile'}</Box>
    <Box component="h3">Validation</Box><Badge label={session.validation.valid ? 'valid' : 'invalid'} /></Box>;
}
APIInspector.propTypes = {session: PropTypes.object};
export function apiInspector(session) { return {profile: session.content.profile,
  externalSpecVersion: session.content.externalSpecVersion, selectedId: session.selectedId,
  validation: session.validation, providerStatuses: session.providerStatuses}; }
