/////////////////////////////////////////////////////////////
// Governed AI Assistant workbench surfaces.
/////////////////////////////////////////////////////////////

import {useEffect, useMemo, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {Button} from '../../primitives/Button';
import {Select} from '../../primitives/Choice';
import {TextArea, TextField} from '../../primitives/Field';
import {TreeRow} from '../../navigation/AdvancedNavigation';
import {Toolbar} from '../../layout/WorkbenchChrome';
import {Badge, Banner, ProgressBar, Skeleton} from '../../feedback/Indicators';
import {EmptyState} from '../../feedback/EmptyState';
import DataGrid from '../../data/DataGrid';

export const AI_SURFACES = Object.freeze([
  {id: 'assistant_dock', title: 'Assistant Dock'},
  {id: 'evidence_viewer', title: 'Evidence Viewer'},
  {id: 'plan_review', title: 'Plan Review'},
  {id: 'diff_review', title: 'Diff Review'},
  {id: 'ai_settings', title: 'AI Settings'},
  {id: 'ai_audit', title: 'AI Audit'},
]);

function useSession(service, id) {
  const [session, setSession] = useState(() => service.get(id));
  useEffect(() => service.subscribe((next) => { if(next.id === id) setSession(next); }), [service, id]);
  return session;
}
function permitted(user, permission) { return user.permissions?.includes(permission) === true; }
function invoke(execute, command, args, setError) {
  setError(''); return Promise.resolve(execute(command, args)).catch((error) => setError(error.message));
}
function referenceLabel(reference) {
  return reference?.canonical ?? (reference?.projectId ? `${reference.projectId}/${reference.assetId}` :
    reference?.id) ?? '';
}
function StateBoundary({session, children}) {
  const limitations = session.providerStatuses.flatMap((item) => [
    ...(item.warnings ?? []), ...(item.limitations ?? []),
  ]);
  return <>
    {session.state === 'loading' && <Box sx={{p: 2}}><Skeleton lines={8} /></Box>}
    {session.state === 'empty' && <EmptyState message="Create or open an AI asset to begin." />}
    {session.state === 'background_task_active' && <ProgressBar label="AI background task"
      status="indeterminate" />}
    {session.error && <Banner status="error">{session.error}</Banner>}
    {!session.error && session.state === 'runtime_failure' && <Banner status="error">
      AI runtime failure. Authored definitions and prior evidence remain available.</Banner>}
    {session.state === 'permission_denied' && <Banner status="error">
      Permission denied. No model or provider operation was attempted.</Banner>}
    {['stale', 'partial', 'disconnected', 'read_only', 'validation_error'].includes(session.state) &&
      <Banner status="warning">AI Assistant is {session.state.replaceAll('_', ' ')}.</Banner>}
    {limitations.length > 0 && <Banner status="warning">Provider limitations: {
      [...new Set(limitations)].join(' ')}</Banner>}
    {children}
  </>;
}
StateBoundary.propTypes = {session: PropTypes.object, children: PropTypes.node};

function AssistantDock({session, execute, currentUser}) {
  const active = session.runtime.assistantSessions.find((item) =>
    item.id === session.runtime.activeSessionId);
  const [mode, setMode] = useState(active?.mode ?? session.content.sessionPolicy.defaultMode);
  const [prompt, setPrompt] = useState(''); const [error, setError] = useState('');
  const [scopeName, setScopeName] = useState(''); const [scopeRef, setScopeRef] = useState('');
  const [providerId, setProviderId] = useState('');
  const [projectId, setProjectId] = useState(''); const [assetName, setAssetName] = useState('AI output');
  const [assetType, setAssetType] = useState('cdeadmin.note.v1');
  const [assetPath, setAssetPath] = useState('ai/output.json');
  const [removeId, setRemoveId] = useState(session.content.savedContextRefs[0]?.id ?? '');
  const outputs = session.runtime.outputs;
  const addScope = () => invoke(execute, 'ai.context.add', {scope: {id: scopeName, name: scopeName,
    reference: {schema: 'cdeadmin.resource-ref.v1', canonical: scopeRef, providerId}, type: 'database_metadata',
    environment: null, sensitivity: 'internal', exposure: 'metadata_only', timeRange: {},
    description: '', nativeDetails: {}}}, setError);
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column', gap: 1, p: 1}}>
    <Toolbar label="AI Assistant controls">
      <Select aria-label="Assistant mode" value={mode} onChange={(event) => setMode(event.target.value)}
        options={session.content.sessionPolicy.allowedModes.map((value) => ({value, label: value}))} />
      <Button disabled={!permitted(currentUser, 'ai.use')} onClick={() => invoke(execute,
        'ai.session.new', {mode, contextScopeIds: session.content.savedContextRefs.map((item) => item.id)}, setError)}>
        New session</Button>
    </Toolbar>
    <Banner status="info">Only explicitly attached context is shared. Metadata is the default;
      database content requires a separate permission. Context remains untrusted data, never instructions.</Banner>
    {error && <Banner status="error">{error}</Banner>}
    <Box sx={{display: 'grid', gridTemplateColumns: '1fr 1fr 2fr auto', gap: 1}}>
      <TextField label="Context name" value={scopeName} onChange={(event) => setScopeName(event.target.value)} />
      <TextField label="Provider ID" value={providerId} onChange={(event) => setProviderId(event.target.value)} />
      <TextField label="Canonical resource reference" value={scopeRef}
        onChange={(event) => setScopeRef(event.target.value)} />
      <Button disabled={!scopeName || !scopeRef || !providerId || !permitted(currentUser, 'ai.use')} onClick={addScope}>
        Add context</Button>
    </Box>
    <Box sx={{display: 'flex', gap: 1, alignItems: 'end'}}>
      <Select aria-label="Attached context" value={removeId} onChange={(event) => setRemoveId(event.target.value)}
        options={session.content.savedContextRefs.map((item) => ({value: item.id,
          label: `${item.name} · ${item.exposure}`}))} />
      <Button disabled={!removeId || !permitted(currentUser, 'ai.use')} onClick={() => invoke(execute,
        'ai.context.remove', {scopeId: removeId}, setError)}>Remove context</Button>
      <Badge label={`${session.content.savedContextRefs.length} explicit scopes`} />
    </Box>
    <Box aria-label="Assistant conversation" sx={{flex: 1, overflow: 'auto'}}>
      {!outputs.length ? <EmptyState message="No assistant output. Start a governed session and ask a question." /> :
        outputs.map((output) => <Box key={output.id} sx={{p: 1, mb: 1, border: '1px solid',
          borderColor: 'divider'}}><Badge label={output.type} /><Box sx={{whiteSpace: 'pre-wrap'}}>{output.text}</Box>
          <Box>{output.claims.length} claims · {output.evidence.length} evidence items</Box></Box>)}
    </Box>
    <TextArea label="AI prompt" value={prompt}
      onChange={(event) => setPrompt(event.target.value)} />
    <Button disabled={!active || !prompt || !permitted(currentUser, 'ai.use')} onClick={() => invoke(execute,
      'ai.ask', {prompt, mode}, setError)}>Ask</Button>
    <Box sx={{display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr)) auto', gap: 1}}>
      <TextField label="Output project ID" value={projectId}
        onChange={(event) => setProjectId(event.target.value)} />
      <TextField label="Output asset type" value={assetType}
        onChange={(event) => setAssetType(event.target.value)} />
      <TextField label="Output asset name" value={assetName}
        onChange={(event) => setAssetName(event.target.value)} />
      <TextField label="Output asset path" value={assetPath}
        onChange={(event) => setAssetPath(event.target.value)} />
      <Button disabled={!outputs.length || !projectId || !permitted(currentUser, 'ai.propose')}
        onClick={() => invoke(execute, 'ai.output.save_as_asset', {outputId: outputs.at(-1).id,
          projectId, assetType, schemaName: assetType, name: assetName, path: assetPath}, setError)}>
        Save latest output</Button>
    </Box>
  </Box>;
}
AssistantDock.propTypes = {session: PropTypes.object, execute: PropTypes.func, currentUser: PropTypes.object};

function EvidenceViewer({session, service}) {
  const rows = session.runtime.outputs.flatMap((output) => output.evidence.map((evidence) => ({...evidence,
    outputId: output.id, source: referenceLabel(evidence.sourceRef), claimsText: evidence.claimIds.join(', ')})));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Evidence viewer"><Badge label={`${rows.length} cited evidence records`} /></Toolbar>
    {!rows.length ? <EmptyState message="No cited evidence is available." /> : <DataGrid
      gridId="ai/evidence" aria-label="AI evidence" rows={rows} readOnly enableRowSelect
      rowKeyGetter={(row) => `${row.outputId}:${row.id}`} onItemEnter={(row) => row && service.select(
        session.id, {selectedId: `${row.outputId}:${row.id}`})} columns={[
        {key: 'classification', name: 'Classification'}, {key: 'kind', name: 'Kind'},
        {key: 'summary', name: 'Summary'}, {key: 'source', name: 'Source'},
        {key: 'revision', name: 'Revision'}, {key: 'claimsText', name: 'Claims'},
      ]} />}
  </Box>;
}
EvidenceViewer.propTypes = {session: PropTypes.object, service: PropTypes.object};

function PlanReview({session, execute, currentUser}) {
  const [planId, setPlanId] = useState(session.runtime.activePlanId ?? session.content.savedPlans[0]?.id ?? '');
  const plan = session.content.savedPlans.find((item) => item.id === planId);
  const [revision, setRevision] = useState(plan?.targetRevision ?? '');
  const [selected, setSelected] = useState(plan?.actions[0] ? [plan.actions[0].id] : []);
  const [confirmationRef, setConfirmationRef] = useState('');
  const [environment, setEnvironment] = useState('development'); const [connection, setConnection] = useState(
    plan?.actions[0] ? `resource:${plan.actions[0].targetRef.canonical ?? ''}` : '');
  const [error, setError] = useState(''); const rows = (plan?.actions ?? []).map((action) => ({...action,
    target: referenceLabel(action.targetRef), effectsText: action.effects.join(', '),
    permissionsText: action.permissions.join(', '), valid: action.validation.valid === true ? 'valid' : 'invalid'}));
  const latest = session.runtime.outputs.findLast?.((item) => item.plan)?.plan ??
    [...session.runtime.outputs].reverse().find((item) => item.plan)?.plan;
  const firstActionId = plan?.actions[0]?.id ?? '';
  useEffect(() => setSelected(firstActionId ? [firstActionId] : []), [firstActionId]);
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column', gap: 1, p: 1}}>
    <Toolbar label="AI plan review controls">
      <Select aria-label="Saved plan" value={planId} onChange={(event) => setPlanId(event.target.value)}
        options={session.content.savedPlans.map((item) => ({value: item.id,
          label: `${item.name} · revision ${item.revision}`}))} />
      <Button disabled={!latest || !permitted(currentUser, 'ai.propose')} onClick={() => invoke(execute,
        'ai.plan.create', {plan: latest}, setError)}>Save proposed plan</Button>
      <Button disabled={!plan || !permitted(currentUser, 'ai.propose')} onClick={() => invoke(execute,
        'ai.plan.validate', {planId, currentRevision: revision}, setError)}>Validate</Button>
    </Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    {!plan ? <EmptyState message="No structured plan is available." /> : <DataGrid gridId="ai/plan"
      aria-label="AI proposed plan actions" rows={rows} readOnly enableRowSelect rowKeyGetter={(row) => row.id}
      onItemSelect={(rowIndex) => setSelected([rows[rowIndex].id])}
      onItemClick={(rowIndex) => setSelected([rows[rowIndex].id])} columns={[
        {key: 'id', name: 'Action'}, {key: 'commandId', name: 'Registered command'},
        {key: 'target', name: 'Target'}, {key: 'effectsText', name: 'Effects'},
        {key: 'permissionsText', name: 'Permissions'}, {key: 'valid', name: 'Validation'},
      ]} />}
    <Box sx={{display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 1}}>
      <TextField label="Current target revision" value={revision}
        onChange={(event) => setRevision(event.target.value)} />
      <TextField label="Environment" value={environment}
        onChange={(event) => setEnvironment(event.target.value)} />
      <TextField label="Target identity" value={connection}
        onChange={(event) => setConnection(event.target.value)} />
      <TextField label="Confirmation reference" value={confirmationRef}
        onChange={(event) => setConfirmationRef(event.target.value)} />
    </Box>
    <Box sx={{display: 'flex', gap: 1}}>
      <Button disabled={!selected.length || !permitted(currentUser, 'ai.execute_approved')}
        onClick={() => invoke(execute, 'ai.action.approve', {planId, actionIds: selected,
          currentRevision: revision, reason: 'Reviewed in plan workspace'}, setError)}>Approve selected</Button>
      <Button disabled={!selected.length || !permitted(currentUser, 'ai.execute_approved')}
        onClick={() => invoke(execute, 'ai.action.reject', {planId, actionIds: selected,
          currentRevision: revision, reason: 'Rejected in plan workspace'}, setError)}>Reject selected</Button>
      <Button disabled={!plan || !confirmationRef || !permitted(currentUser, 'ai.execute_approved')}
        onClick={() => invoke(execute, 'ai.plan.execute', {planId, currentRevision: revision,
          confirmationRef, environment, connection}, setError)}>Execute approved plan</Button>
    </Box>
  </Box>;
}
PlanReview.propTypes = {session: PropTypes.object, execute: PropTypes.func, currentUser: PropTypes.object};

function DiffReview({session, service}) {
  const rows = session.runtime.outputs.filter((item) => item.diff && Object.keys(item.diff).length).map((item) => ({
    id: item.id, type: item.type, summary: item.text, diff: JSON.stringify(item.diff, null, 2)}));
  return <Box sx={{height: '100%'}}>{!rows.length ? <EmptyState message="No proposed diffs are available." /> :
    <DataGrid gridId="ai/diffs" aria-label="AI proposed diffs" rows={rows} readOnly enableRowSelect
      rowKeyGetter={(row) => row.id} onItemEnter={(row) => row && service.select(session.id,
        {selectedId: row.id})} columns={[{key: 'type', name: 'Type'}, {key: 'summary', name: 'Summary'},
        {key: 'diff', name: 'Draft diff'}]} />}</Box>;
}
DiffReview.propTypes = {session: PropTypes.object, service: PropTypes.object};

function AISettings({session}) {
  const policyRows = [{id: 'model', setting: 'Model profile reference',
    value: referenceLabel(session.content.modelProfileRef)},
  {id: 'modes', setting: 'Allowed modes', value: session.content.sessionPolicy.allowedModes.join(', ')},
  {id: 'read', setting: 'Allowed read tools', value: session.content.sessionPolicy.allowedReadToolIds.join(', ')},
  {id: 'proposal', setting: 'Allowed proposal commands',
    value: session.content.sessionPolicy.allowedProposalCommandIds.join(', ')},
  {id: 'steps', setting: 'Maximum plan steps', value: session.content.sessionPolicy.maximumPlanSteps},
  {id: 'evidence', setting: 'Evidence required', value: String(session.content.sessionPolicy.requireEvidence)},
  {id: 'persistence', setting: 'Persist messages',
    value: String(session.content.conversationPersistencePolicy.persistMessages)},
  {id: 'retention', setting: 'Retention days',
    value: session.content.conversationPersistencePolicy.retentionDays ?? 'not retained'}];
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Banner status="info">Model credentials remain referenced through the credential service and are never
      serialized into this asset. Settings are authored in the versioned project asset.</Banner>
    <DataGrid gridId="ai/settings" aria-label="AI model and governance settings" rows={policyRows}
      readOnly rowKeyGetter={(row) => row.id} columns={[{key: 'setting', name: 'Setting'},
        {key: 'value', name: 'Configured value'}]} />
  </Box>;
}
AISettings.propTypes = {session: PropTypes.object};

function AIAudit({session}) {
  const rows = [...session.history.map((item, index) => ({id: `audit-${index}`, category: 'audit',
    action: item.action, actor: item.actor, target: item.target, at: item.at, details: JSON.stringify(item.details)})),
  ...session.runtime.approvals.map((item) => ({id: item.id, category: 'approval', action: item.decision,
    actor: item.actor, target: `${item.planId}@${item.planRevision}`, at: item.approvedAt,
    details: item.actionIds.join(', ')})), ...session.tasks.map((item) => ({id: item.id, category: 'task',
    action: item.type, actor: item.owner, target: item.state, at: item.createdAt,
    details: item.error || item.message}))];
  return <Box sx={{height: '100%'}}>{!rows.length ? <EmptyState message="No AI audit activity exists." /> :
    <DataGrid gridId="ai/audit" aria-label="AI audit activity" rows={rows} readOnly rowKeyGetter={(row) => row.id}
      columns={[{key: 'category', name: 'Category'}, {key: 'action', name: 'Action'},
        {key: 'actor', name: 'Actor'}, {key: 'target', name: 'Target'}, {key: 'at', name: 'Time'},
        {key: 'details', name: 'Details'}]} />}</Box>;
}
AIAudit.propTypes = {session: PropTypes.object};

const COMPONENTS = {assistant_dock: AssistantDock, evidence_viewer: EvidenceViewer,
  plan_review: PlanReview, diff_review: DiffReview, ai_settings: AISettings, ai_audit: AIAudit};

export function AIWorkspace({service, sessionId, surface='assistant_dock', executeCommand,
  currentUser={}, onStateChange}) {
  const session = useSession(service, sessionId); const [active, setActive] = useState(surface);
  useEffect(() => setActive(surface), [surface]); useEffect(() => onStateChange?.(session),
    [onStateChange, session]); const Component = COMPONENTS[active] ?? AssistantDock;
  const navigate = (next) => { setActive(next); service.select(sessionId, {surface: next}); };
  return <Box data-testid="ai-workspace" sx={{height: '100%', display: 'grid',
    gridTemplateColumns: '288px minmax(0, 1fr) 340px'}}>
    <Box component="nav" aria-label="AI Assistant surfaces" sx={{overflow: 'auto'}}>{AI_SURFACES.map((item) =>
      <TreeRow key={item.id} label={item.title} selected={active === item.id}
        onSelect={() => navigate(item.id)} />)}</Box>
    <Box component="main" sx={{minWidth: 0, overflow: 'hidden'}}><StateBoundary session={session}>
      <Component session={session} service={service} execute={executeCommand}
        currentUser={currentUser} /></StateBoundary></Box>
    <Box component="aside" aria-label="AI Assistant inspector" sx={{overflow: 'auto', p: 1}}>
      <AIInspector session={session} /></Box>
  </Box>;
}
AIWorkspace.propTypes = {service: PropTypes.object.isRequired, sessionId: PropTypes.string.isRequired,
  surface: PropTypes.string, executeCommand: PropTypes.func.isRequired, currentUser: PropTypes.object,
  onStateChange: PropTypes.func};

export function AINavigator({service, onOpen}) {
  const [sessions, setSessions] = useState(() => service.list());
  useEffect(() => service.subscribe(() => setSessions(service.list())), [service]);
  return <Box role="tree" aria-label="AI Assistant assets">{sessions.length ? sessions.map((session) =>
    <TreeRow key={session.id} label={`${session.content.name || session.id} · ${session.state}`}
      onSelect={() => onOpen(session.id, session.surface)} />) :
    <EmptyState message="No AI Assistant assets are open." />}</Box>;
}
AINavigator.propTypes = {service: PropTypes.object.isRequired, onOpen: PropTypes.func.isRequired};

function AIInspector({session}) {
  const selected = useMemo(() => {
    for(const output of session.runtime.outputs) {
      if(output.id === session.selectedId) return output;
      const evidence = output.evidence.find((item) => `${output.id}:${item.id}` === session.selectedId);
      if(evidence) return evidence;
    }
    return session.content.savedPlans.flatMap((item) => item.actions).find((item) => item.id === session.selectedId);
  }, [session]);
  return <Box><Box component="h3">Selected proposal or evidence</Box><Box component="pre" sx={{whiteSpace: 'pre-wrap'}}>{
    JSON.stringify(selected ?? {}, null, 2)}</Box><Box component="h3">Validation</Box>
  <Badge label={session.validation.valid ? 'valid' : 'invalid'} /><Box component="h3">Provider evidence</Box>
  <Box component="pre" sx={{whiteSpace: 'pre-wrap'}}>{JSON.stringify(session.providerStatuses, null, 2)}</Box></Box>;
}
AIInspector.propTypes = {session: PropTypes.object};

export function aiInspector(session) {
  return {selectedId: session.selectedId, validation: session.validation,
    providerStatuses: session.providerStatuses, activePlanId: session.runtime.activePlanId,
    approvalCount: session.runtime.approvals.length};
}
