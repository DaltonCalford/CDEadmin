/////////////////////////////////////////////////////////////
// Migration Planning provider-aware workbench surfaces.
/////////////////////////////////////////////////////////////

import {useEffect, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {Button} from '../../primitives/Button';
import {Select} from '../../primitives/Choice';
import {TextField} from '../../primitives/Field';
import {TreeRow} from '../../navigation/AdvancedNavigation';
import {Toolbar} from '../../layout/WorkbenchChrome';
import {Badge, Banner, ProgressBar, Skeleton} from '../../feedback/Indicators';
import {EmptyState} from '../../feedback/EmptyState';
import DataGrid from '../../data/DataGrid';
import {cutoverReadiness, rollbackState} from './MigrationEngine';

export const MIGRATION_SURFACES = Object.freeze([
  {id: 'migration_portfolio', title: 'Migration Portfolio'},
  {id: 'assessment', title: 'Assessment'},
  {id: 'mapping_designer', title: 'Mapping Designer'},
  {id: 'schema_plan', title: 'Schema Plan'},
  {id: 'data_movement', title: 'Data Movement'},
  {id: 'validation', title: 'Validation'},
  {id: 'cutover_runbook', title: 'Cutover Runbook'},
  {id: 'run_monitor', title: 'Run Monitor'},
]);

function useSession(service, id) {
  const [session, setSession] = useState(() => service.get(id));
  useEffect(() => service.subscribe((next) => { if(next.id === id) setSession(next); }),
    [service, id]); return session;
}
function permitted(user, permission) { return user.permissions?.includes(permission) === true; }
function actionable(session, user, permission) {
  return permitted(user, permission) && !['loading', 'read_only', 'permission_denied',
    'disconnected', 'background_task_active'].includes(session.state);
}
function run(invoke, command, args, setError) {
  setError(''); return Promise.resolve(invoke(command, args)).catch((error) => setError(error.message));
}

function StateBoundary({session, children}) {
  const limitations = session.providerStatuses.flatMap((item) => [
    ...(item.warnings ?? []), ...(item.limitations ?? []),
  ]);
  return <>
    {session.state === 'loading' && <Box sx={{p: 2}}><Skeleton lines={8} /></Box>}
    {session.state === 'empty' && <EmptyState
      message="No source and target are configured. Create or open a migration project." />}
    {session.state === 'background_task_active' && <Box sx={{p: 1}}>
      <ProgressBar label="Migration background task" status="indeterminate" />
    </Box>}
    {session.error && <Box sx={{p: 1}}><Banner status="error">{session.error}</Banner></Box>}
    {!session.error && session.state === 'runtime_failure' && <Box sx={{p: 1}}>
      <Banner status="error">Migration runtime failure. Review Problems and retry explicitly.</Banner>
    </Box>}
    {!session.error && session.state === 'permission_denied' && <Banner status="error">
      Permission denied. Provider support is reported separately.
    </Banner>}
    {['stale', 'partial', 'disconnected', 'read_only', 'validation_error'].includes(session.state) &&
      <Box sx={{p: 1}}><Banner status="warning">Migration is {session.state.replaceAll('_', ' ')}.
        Last safe authored and runtime evidence remains visible.</Banner></Box>}
    {limitations.length > 0 && <Box sx={{p: 1}}><Banner status="warning">
      Provider limitations: {[...new Set(limitations)].join(' ')}</Banner></Box>}
    {children}
  </>;
}
StateBoundary.propTypes = {session: PropTypes.object, children: PropTypes.node};

function Portfolio({session, service, currentUser}) {
  const rows = [{id: session.id, name: session.content.name || session.id,
    strategy: session.content.strategy, phase: session.content.phase, state: session.state,
    source: session.content.sourceBinding?.canonical ?? 'Not configured',
    target: session.content.targetBinding?.canonical ?? 'Not configured'}];
  return <Box sx={{height: '100%'}}><Toolbar label="Migration portfolio controls">
    <Badge label={`${session.content.phase} · ${session.state}`} /></Toolbar>
  <DataGrid gridId="migration/portfolio" aria-label="Migration portfolio" rows={rows} readOnly
    enableRowSelect rowKeyGetter={(row) => row.id} onItemEnter={(row) => service.select(
      session.id, {selectedId: row?.id})} columns={[{key: 'name', name: 'Migration'},
      {key: 'strategy', name: 'Strategy'}, {key: 'phase', name: 'Phase'},
      {key: 'state', name: 'State'}, {key: 'source', name: 'Source'},
      {key: 'target', name: 'Target'}]} />
  {!permitted(currentUser, 'migration.view') && <Banner status="error">Migration view permission is required.</Banner>}
  </Box>;
}
Portfolio.propTypes = {session: PropTypes.object, service: PropTypes.object,
  currentUser: PropTypes.object};

function Assessment({session, invoke, currentUser}) {
  const [error, setError] = useState(''); const assessment = session.content.assessment;
  const rows = (assessment?.findings ?? []).map((item) => ({...item,
    source: item.sourceRef.canonical, target: item.targetRef?.canonical ?? '',
    evidenceLabel: item.evidenceSource, status: item.waived ? 'waived' : item.category}));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Migration assessment controls"><Button
      disabled={!actionable(session, currentUser, 'migration.execute')}
      onClick={() => run(invoke, 'migration.assess.run', {}, setError)}>Run assessment</Button></Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    {!assessment ? <EmptyState message="Run assessment to discover compatibility, evidence and blockers." /> :
      <DataGrid gridId="migration/assessment" aria-label="Migration compatibility assessment"
        rows={rows} readOnly enableRowSelect rowKeyGetter={(row) => row.id}
        columns={[{key: 'source', name: 'Source object'}, {key: 'objectKind', name: 'Kind'},
          {key: 'target', name: 'Target object'}, {key: 'status', name: 'Compatibility'},
          {key: 'evidenceLabel', name: 'Evidence source'}, {key: 'message', name: 'Finding'}]} />}
  </Box>;
}
Assessment.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object};

function MappingDesigner({session, service, invoke, currentUser}) {
  const [decision, setDecision] = useState('accepted'); const [error, setError] = useState('');
  const rows = session.content.mappingSet.map((item) => ({...item,
    source: item.sourceRef.canonical, target: item.targetRef?.canonical ?? 'Unmapped',
    loss: item.lossy ? item.lossAcknowledged ? 'acknowledged loss' : 'unacknowledged loss' : 'none'}));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Migration mapping controls"><Select label="Decision" value={decision}
      onChange={setDecision} options={['accepted', 'rejected', 'manual'].map((value) => ({value,
        label: value.replaceAll('_', ' ')}))} /><Button
      disabled={!actionable(session, currentUser, 'migration.edit') || !session.selectedId}
      onClick={() => run(invoke, 'migration.mapping.accept', {mappingId: session.selectedId,
        decision}, setError)}>Apply reviewed decision</Button></Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    {!rows.length ? <EmptyState message="Assessment has not produced mapping decisions." /> :
      <DataGrid gridId="migration/mappings" aria-label="Migration mapping decisions" rows={rows}
        readOnly enableRowSelect rowKeyGetter={(row) => row.id}
        onItemEnter={(row) => row && service.select(session.id, {selectedId: row.id})}
        columns={[{key: 'source', name: 'Source'}, {key: 'sourceNativeType', name: 'Source type'},
          {key: 'target', name: 'Target'}, {key: 'targetNativeType', name: 'Target type'},
          {key: 'category', name: 'Classification'}, {key: 'decision', name: 'Decision'},
          {key: 'loss', name: 'Loss'}, {key: 'expression', name: 'Transformation'}]} />}
  </Box>;
}
MappingDesigner.propTypes = {session: PropTypes.object, service: PropTypes.object,
  invoke: PropTypes.func, currentUser: PropTypes.object};

function SchemaPlan({session, invoke, currentUser}) {
  const [error, setError] = useState(''); const operations = session.content.schemaPlan?.operations ?? [];
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Migration schema-plan controls"><Button
      disabled={!actionable(session, currentUser, 'migration.edit')}
      onClick={() => run(invoke, 'migration.plan.validate', {}, setError)}>Validate plan</Button>
    <Button disabled={!actionable(session, currentUser, 'migration.execute') ||
      !session.planValidation.valid} onClick={() => run(invoke, 'migration.dry_run', {}, setError)}>
      Dry run</Button></Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    {session.planValidation.errors.length > 0 && <Banner status="error">
      {session.planValidation.errors.join(' ')}</Banner>}
    {!operations.length ? <EmptyState message="No Schema Comparison plan is bound." /> :
      <DataGrid gridId="migration/schema-plan" aria-label="Ordered migration schema plan"
        rows={operations} readOnly rowKeyGetter={(row) => row.id}
        columns={[{key: 'id', name: 'Operation'}, {key: 'action', name: 'Action'},
          {key: 'risk', name: 'Risk'}, {key: 'dependencies', name: 'Dependencies',
            renderCell: ({row}) => row.dependencies.join(', ')},
          {key: 'preconditions', name: 'Preconditions', renderCell: ({row}) => row.preconditions.length},
          {key: 'rollback', name: 'Rollback', renderCell: ({row}) => row.rollback.length}]} />}
  </Box>;
}
SchemaPlan.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object};

function DataMovement({session, invoke, currentUser}) {
  const [error, setError] = useState(''); const checkpoints = new Map(
    session.runtime.checkpoints.map((item) => [item.unitId, item]));
  const rows = session.content.dataMovePlan.units.map((unit) => ({...unit,
    source: unit.sourceRef.canonical, target: unit.targetRef.canonical,
    checkpoint: checkpoints.get(unit.id)?.state ?? 'not started',
    rows: checkpoints.get(unit.id)?.rowDocumentCount ?? 0,
    bytes: checkpoints.get(unit.id)?.byteCount ?? 0,
    throughput: checkpoints.get(unit.id)?.nativeDetails?.throughput ?? 'not reported'}));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Migration data-movement controls"><Button
      disabled={!actionable(session, currentUser, 'migration.execute') || !rows.length}
      onClick={() => run(invoke, 'migration.copy.start', {}, setError)}>
      {checkpoints.size ? 'Resume from committed checkpoints' : 'Start initial copy'}</Button></Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    {!rows.length ? <EmptyState message="No data-copy units are configured." /> :
      <DataGrid gridId="migration/data-movement" aria-label="Migration copy streams" rows={rows}
        readOnly rowKeyGetter={(row) => row.id} columns={[{key: 'id', name: 'Copy unit'},
          {key: 'source', name: 'Source'}, {key: 'target', name: 'Target'},
          {key: 'batchSize', name: 'Batch size'}, {key: 'parallelism', name: 'Parallelism'},
          {key: 'checkpoint', name: 'Checkpoint'}, {key: 'rows', name: 'Rows/documents'},
          {key: 'bytes', name: 'Bytes'}, {key: 'throughput', name: 'Throughput'}]} />}
  </Box>;
}
DataMovement.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object};

function Validation({session, invoke, currentUser}) {
  const [error, setError] = useState(''); const results = new Map(
    session.runtime.validationResults.map((item) => [item.id, item]));
  const rows = session.content.validationPlan.map((item) => ({...item,
    source: item.sourceRef.canonical, target: item.targetRef.canonical,
    result: results.get(item.id)?.state ?? 'not run',
    sourceValue: results.get(item.id)?.sourceValue ?? '', targetValue: results.get(item.id)?.targetValue ?? ''}));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Migration validation controls"><Button
      disabled={!actionable(session, currentUser, 'migration.execute') || !rows.length}
      onClick={() => run(invoke, 'migration.verify.run', {}, setError)}>Run verification</Button></Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    {!rows.length ? <EmptyState message="No verification definitions are configured." /> :
      <DataGrid gridId="migration/validation" aria-label="Migration validation results" rows={rows}
        readOnly rowKeyGetter={(row) => row.id} columns={[{key: 'id', name: 'Verification'},
          {key: 'type', name: 'Method'}, {key: 'source', name: 'Source'},
          {key: 'target', name: 'Target'}, {key: 'blocking', name: 'Blocking'},
          {key: 'result', name: 'Result'}, {key: 'sourceValue', name: 'Source value'},
          {key: 'targetValue', name: 'Target value'}]} />}
  </Box>;
}
Validation.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object};

function CutoverRunbook({session, invoke, currentUser}) {
  const plan = session.content.cutoverPlan; const readiness = cutoverReadiness(
    session.content, {...session.runtime, checkpoints: undefined});
  const rollback = rollbackState(session.content.rollbackPlan, session.runtime.completedCutoverSteps);
  const [confirmationRef, setConfirmationRef] = useState(''); const [error, setError] = useState('');
  const rows = (plan?.steps ?? []).map((item) => ({...item,
    state: session.runtime.completedCutoverSteps.includes(item.id) ? 'complete' : 'pending'}));
  return <Box sx={{height: '100%', overflow: 'auto'}}>
    {!readiness.ready && <Banner status="warning">Cutover blockers: {readiness.blockers.join(' ')}</Banner>}
    <Banner status={rollback.available ? 'info' : 'error'}>{rollback.message}</Banner>
    <Toolbar label="Migration cutover controls"><TextField label="Confirmation reference"
      value={confirmationRef} onChange={(event) => setConfirmationRef(event.target.value)} />
    <Button disabled={!actionable(session, currentUser, 'migration.cutover') ||
        !readiness.ready || !confirmationRef} onClick={() => run(invoke, 'migration.cutover.arm',
      {confirmationRef, environment: plan.targetEnvironment,
        connection: plan.targetConnection}, setError)}>Arm cutover</Button>
    <Button disabled={!actionable(session, currentUser, 'migration.cutover') || !session.arm}
      onClick={() => run(invoke, 'migration.cutover.execute', {}, setError)}>Execute cutover</Button>
    <Button disabled={!actionable(session, currentUser, 'migration.rollback') || !rollback.available}
      onClick={() => run(invoke, 'migration.rollback.execute', {}, setError)}>Execute rollback</Button>
    </Toolbar>{error && <Banner status="error">{error}</Banner>}
    {session.arm && <Banner status="warning">Armed for {session.arm.environment} / {
      session.arm.connection} until {session.arm.expiresAt}.</Banner>}
    {!plan ? <EmptyState message="No cutover plan is configured." /> :
      <DataGrid gridId="migration/cutover" aria-label="Migration cutover runbook" rows={rows}
        readOnly rowKeyGetter={(row) => row.id} columns={[{key: 'id', name: 'Step'},
          {key: 'label', name: 'Action'}, {key: 'state', name: 'State'},
          {key: 'providerMutation', name: 'Live mutation'},
          {key: 'dependencies', name: 'Dependencies', renderCell: ({row}) => row.dependencies.join(', ')}]} />}
  </Box>;
}
CutoverRunbook.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  currentUser: PropTypes.object};

function RunMonitor({session, service}) {
  const rows = session.runs.map((run) => ({...run, evidenceText: JSON.stringify(run.evidence)}));
  const tasks = session.tasks ?? [];
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    {session.activeTaskId && <Banner status="info">Active shared task: {session.activeTaskId}</Banner>}
    <Box role="tree" aria-label="Migration task graph">{tasks.map((task) => <TreeRow
      key={task.id} label={`${task.label} · ${task.state} · ${
        task.dependencies.length ? `after ${task.dependencies.join(', ')}` : 'root task'}`}
      onSelect={() => service.select(session.id, {selectedId: task.id})} />)}</Box>
    {!rows.length && !tasks.length ? <EmptyState message="No migration phases have executed." /> :
      <DataGrid gridId="migration/runs" aria-label="Migration run history" rows={rows} readOnly
        enableRowSelect rowKeyGetter={(row) => row.id} onItemEnter={(row) => row && service.select(
          session.id, {selectedId: row.id})} columns={[{key: 'id', name: 'Run'},
          {key: 'phase', name: 'Phase'}, {key: 'state', name: 'State'},
          {key: 'at', name: 'Time'}, {key: 'evidenceText', name: 'Evidence'}]} />}
    <Box aria-label="Migration live metrics" sx={{p: 1}}>CDC lag: {
      session.runtime.cdcLag ?? 'not reported'} · copy concurrency: {
      session.content.dataMovePlan.concurrency}</Box>
  </Box>;
}
RunMonitor.propTypes = {session: PropTypes.object, service: PropTypes.object};

const COMPONENTS = {migration_portfolio: Portfolio, assessment: Assessment,
  mapping_designer: MappingDesigner, schema_plan: SchemaPlan, data_movement: DataMovement,
  validation: Validation, cutover_runbook: CutoverRunbook, run_monitor: RunMonitor};

export function MigrationWorkspace({service, sessionId, surface='migration_portfolio',
  executeCommand, currentUser={}, onStateChange}) {
  const session = useSession(service, sessionId); const [active, setActive] = useState(surface);
  useEffect(() => setActive(surface), [surface]);
  useEffect(() => onStateChange?.(session), [onStateChange, session]);
  const Component = COMPONENTS[active] ?? Portfolio;
  const navigate = (next) => { setActive(next); service.select(sessionId, {surface: next}); };
  return <Box data-testid="migration-workspace" sx={{height: '100%', display: 'grid',
    gridTemplateColumns: '288px minmax(0, 1fr) 340px'}}>
    <Box component="nav" aria-label="Migration surfaces" sx={{overflow: 'auto'}}>
      {MIGRATION_SURFACES.map((item) => <TreeRow key={item.id} label={item.title}
        selected={active === item.id} onSelect={() => navigate(item.id)} />)}
    </Box>
    <Box component="main" sx={{minWidth: 0, overflow: 'hidden'}}><StateBoundary session={session}>
      <Component session={session} service={service} invoke={executeCommand}
        currentUser={currentUser} onNavigate={navigate} />
    </StateBoundary></Box>
    <Box component="aside" aria-label="Migration inspector" sx={{overflow: 'auto', p: 1}}>
      <MigrationInspector session={session} />
    </Box>
  </Box>;
}
MigrationWorkspace.propTypes = {service: PropTypes.object.isRequired,
  sessionId: PropTypes.string.isRequired, surface: PropTypes.string,
  executeCommand: PropTypes.func.isRequired, currentUser: PropTypes.object,
  onStateChange: PropTypes.func};

export function MigrationNavigator({service, onOpen}) {
  const [sessions, setSessions] = useState(() => service.list());
  useEffect(() => service.subscribe(() => setSessions(service.list())), [service]);
  return <Box role="tree" aria-label="Migration projects">{sessions.length ? sessions.map((session) =>
    <TreeRow key={session.id} label={session.content.name || session.id}
      badge={`${session.content.phase} · ${session.state}`}
      onSelect={() => onOpen(session.id, session.surface)} />) :
    <EmptyState message="No migration projects are open." />}</Box>;
}
MigrationNavigator.propTypes = {service: PropTypes.object.isRequired, onOpen: PropTypes.func.isRequired};

function MigrationInspector({session}) {
  const mapping = session.content.mappingSet.find((item) => item.id === session.selectedId);
  const operation = session.content.schemaPlan?.operations.find((item) => item.id === session.selectedId);
  const selected = mapping ?? operation ?? session.runs.find((item) => item.id === session.selectedId) ??
    session.tasks?.find((item) => item.id === session.selectedId);
  const rollback = rollbackState(session.content.rollbackPlan, session.runtime.completedCutoverSteps);
  return <Box><Box component="h3">Selected operation</Box><pre>{JSON.stringify(selected ?? {}, null, 2)}</pre>
    <Box component="h3">Source/target mapping</Box><Box>{mapping?.sourceRef.canonical ??
      session.content.sourceBinding?.canonical ?? 'Not selected'} → {mapping?.targetRef?.canonical ??
      session.content.targetBinding?.canonical ?? 'Not selected'}</Box>
    <Box component="h3">Risk</Box><Box>{operation?.risk ?? (mapping?.lossy ? 'lossy mapping' : 'None selected')}</Box>
    <Box component="h3">Preconditions</Box><Box>{operation?.preconditions.length ?? 0}</Box>
    <Box component="h3">Rollback</Box><Badge label={rollback.classification ?? 'not configured'} /></Box>;
}
MigrationInspector.propTypes = {session: PropTypes.object};

export function migrationInspector(session) {
  const rollback = rollbackState(session.content.rollbackPlan, session.runtime.completedCutoverSteps);
  return {phase: session.content.phase, strategy: session.content.strategy,
    selectedId: session.selectedId, source: session.content.sourceBinding,
    target: session.content.targetBinding, planValidation: session.planValidation,
    readiness: cutoverReadiness(session.content, {...session.runtime, checkpoints: undefined}),
    rollback, arm: session.arm};
}
