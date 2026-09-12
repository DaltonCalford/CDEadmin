/////////////////////////////////////////////////////////////
// Data Quality workbench surfaces and protected result views.
/////////////////////////////////////////////////////////////

import React, {useEffect, useMemo, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {Button} from '../../primitives/Button';
import {NumberField, TextField} from '../../primitives/Field';
import {Checkbox, Select} from '../../primitives/Choice';
import {SearchField} from '../../primitives/AdvancedControls';
import {ResourcePicker, TreeRow} from '../../navigation/AdvancedNavigation';
import {Tab} from '../../navigation/TabsAndBreadcrumbs';
import {Toolbar} from '../../layout/WorkbenchChrome';
import {Badge, Banner, ProgressBar, Skeleton} from '../../feedback/Indicators';
import {EmptyState} from '../../feedback/EmptyState';
import DataGrid from '../../data/DataGrid';
import {
  QUALITY_DIMENSIONS, QUALITY_EVALUATION_MODES, QUALITY_RULE_FAMILIES,
  QUALITY_ACTION_TYPES, QUALITY_SEVERITIES, QUALITY_SAMPLING_MODES,
  QUALITY_THRESHOLD_OPERATORS,
} from './contracts';

export const QUALITY_SURFACES = Object.freeze([
  {id: 'quality_overview', title: 'Quality Overview'},
  {id: 'rule_set_designer', title: 'Rule Set Designer'},
  {id: 'data_scope_slice_designer', title: 'Data Scope / Slice Designer'},
  {id: 'validation_run_results', title: 'Validation Run Results'},
  {id: 'profiler', title: 'Profiler'},
  {id: 'quality_history', title: 'Quality History'},
  {id: 'schedule_actions', title: 'Schedule & Actions'},
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
  return <>
    {session.state === 'background_task_active' && <Box sx={{p: 1}}>
      <ProgressBar label="Data Quality background task" status="indeterminate" />
    </Box>}
    {session.state === 'loading' && <Box sx={{p: 2}}><Skeleton lines={8} /></Box>}
    {session.error && <Box sx={{p: 1}}><Banner status="error">{session.error}</Banner></Box>}
    {['stale', 'partial', 'disconnected', 'read_only'].includes(session.state) &&
      <Box sx={{p: 1}}><Banner status="warning">
        Data Quality is {session.state.replace('_', ' ')}. Last safe authored state remains visible.
      </Banner></Box>}
    {children}
  </>;
}
StateBoundary.propTypes = {session: PropTypes.object.isRequired, children: PropTypes.node};

function runRows(session) {
  return session.runs.slice(0, 1000).map((run) => ({id: run.id,
    outcome: run.summary.outcome, passed: run.summary.counts.pass,
    failed: run.summary.counts.fail, errors: run.summary.counts.error,
    modes: run.evaluationModes.join(', '), provider: run.engine.providerId,
    finishedAt: run.finishedAt}));
}

function objectSource(text, label) {
  if(!String(text ?? '').trim()) return null;
  const value = JSON.parse(text);
  if(!value || Array.isArray(value) || typeof value !== 'object') throw new TypeError(
    `${label} must be a JSON object.`
  );
  return value;
}

function ruleEditorDraft(value) {
  return {name: value.name, dimension: value.dimension,
    severity: value.severity, evaluationMode: value.evaluationMode, enabled: value.enabled,
    ratio: value.ratio, operator: value.threshold?.operator ?? 'boolean',
    value: value.threshold?.value ?? 0, lower: value.threshold?.lower ?? 0,
    upper: value.threshold?.upper ?? 1, parameters: JSON.stringify(value.parameters),
    nativeDetails: JSON.stringify(value.nativeDetails), documentation: value.documentation,
    actionIds: value.actionIds};
}

function QualityOverview({session, invoke}) {
  const recent = runRows(session); const latest = session.runs[0];
  const enabled = session.content.rules.filter((rule) => rule.enabled);
  const critical = session.runs.flatMap((run) => run.results)
    .filter((result) => result.status !== 'pass' && result.severity === 'critical').length;
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Quality Overview commands">
      <Button onClick={() => invoke('quality.ruleset.run')}
        disabled={!enabled.length || !session.content.defaultScope}>Run rule set</Button>
      <Button onClick={() => invoke('quality.baseline.capture', {})}
        disabled={!session.content.defaultScope}>Capture baseline</Button>
    </Toolbar>
    <Box sx={{display: 'flex', gap: 1, p: 1}}>
      <Badge label={`Rules ${enabled.length}/${session.content.rules.length}`} />
      <Badge label={`Recent runs ${recent.length}`} status="info" />
      <Badge label={`Critical failures ${critical}`} status={critical ? 'error' : 'success'} />
      <Badge label={`Latest ${latest?.summary.outcome ?? 'not run'}`}
        status={latest?.summary.outcome === 'pass' ? 'success' : latest ? 'warning' : 'neutral'} />
    </Box>
    {!recent.length ? <EmptyState message="No quality runs exist for this rule set." /> :
      <Box sx={{flex: 1, minHeight: 0}}><DataGrid gridId="quality/overview-runs"
        aria-label="Recent quality runs and trends" rows={recent} readOnly
        rowKeyGetter={(row) => row.id} columns={[
          {key: 'id', name: 'Run'}, {key: 'outcome', name: 'Outcome'},
          {key: 'passed', name: 'Passed'}, {key: 'failed', name: 'Failed'},
          {key: 'errors', name: 'Errors'}, {key: 'modes', name: 'Evaluation modes'},
          {key: 'provider', name: 'Provider'}, {key: 'finishedAt', name: 'Finished'},
        ]} /></Box>}
  </Box>;
}
QualityOverview.propTypes = {session: PropTypes.object, invoke: PropTypes.func};

function RuleDetailEditor({rule, session, invoke}) {
  const [draft, setDraft] = useState(() => ruleEditorDraft(rule));
  const [error, setError] = useState('');
  useEffect(() => { setDraft(ruleEditorDraft(rule)); setError(''); }, [rule]);
  const update = (values) => setDraft((current) => ({...current, ...values}));
  const save = () => {
    try {
      const threshold = draft.operator === 'boolean' ? null :
        ['between', 'outside'].includes(draft.operator) ? {operator: draft.operator,
          lower: draft.lower, upper: draft.upper} : {operator: draft.operator, value: draft.value};
      const replacement = {...rule, name: draft.name, dimension: draft.dimension,
        severity: draft.severity, evaluationMode: draft.evaluationMode,
        enabled: draft.enabled, ratio: draft.ratio, threshold,
        parameters: objectSource(draft.parameters, 'Rule parameters') ?? {},
        nativeDetails: objectSource(draft.nativeDetails, 'Rule native details') ?? {},
        documentation: draft.documentation, actionIds: draft.actionIds};
      setError(''); invoke('quality.ruleset.create', {content: {...session.content,
        rules: session.content.rules.map((item) => item.id === rule.id ? replacement : item)}});
    } catch(exception) { setError(exception.message); }
  };
  const remove = () => invoke('quality.ruleset.create', {content: {...session.content,
    rules: session.content.rules.filter((item) => item.id !== rule.id)}});
  return <Box component="section" aria-label="Selected rule editor" sx={{p: 1,
    borderTop: '1px solid', borderColor: 'divider', display: 'grid', gap: 1,
    gridTemplateColumns: 'repeat(4,minmax(140px,1fr))'}}>
    {error && <Box sx={{gridColumn: '1/-1'}}><Banner status="error">{error}</Banner></Box>}
    <TextField label="Selected rule name" value={draft.name}
      onChange={(event) => update({name: event.target.value})} />
    <Select label="Selected dimension" value={draft.dimension}
      options={QUALITY_DIMENSIONS.map((value) => ({value, label: value}))}
      onChange={(dimension) => update({dimension})} />
    <Select label="Selected severity" value={draft.severity}
      options={QUALITY_SEVERITIES.map((value) => ({value, label: value}))}
      onChange={(severity) => update({severity})} />
    <Select label="Selected evaluation mode" value={draft.evaluationMode}
      options={QUALITY_EVALUATION_MODES.map((value) => ({value, label: value}))}
      onChange={(evaluationMode) => update({evaluationMode})} />
    <Select label="Threshold operator" value={draft.operator}
      options={['boolean', ...QUALITY_THRESHOLD_OPERATORS].map((value) => ({value, label: value}))}
      onChange={(operator) => update({operator})} />
    {draft.operator !== 'boolean' && !['between', 'outside'].includes(draft.operator) &&
      <NumberField label="Threshold value" value={draft.value}
        onChange={(event) => update({value: Number(event.target.value)})} />}
    {['between', 'outside'].includes(draft.operator) && <>
      <NumberField label="Threshold lower bound" value={draft.lower}
        onChange={(event) => update({lower: Number(event.target.value)})} />
      <NumberField label="Threshold upper bound" value={draft.upper}
        onChange={(event) => update({upper: Number(event.target.value)})} />
    </>}
    <Checkbox label="Ratio threshold (stored 0..1)" checked={draft.ratio}
      onChange={(ratio) => update({ratio})} />
    <Checkbox label="Rule enabled" checked={draft.enabled}
      onChange={(enabled) => update({enabled})} />
    <TextField label="Rule parameters JSON" value={draft.parameters}
      onChange={(event) => update({parameters: event.target.value})} />
    <TextField label="Provider-native rule details JSON" value={draft.nativeDetails}
      onChange={(event) => update({nativeDetails: event.target.value})} />
    <TextField label="Rule documentation" value={draft.documentation}
      onChange={(event) => update({documentation: event.target.value})} />
    <Box sx={{gridColumn: '1/-1', display: 'flex', gap: 1, flexWrap: 'wrap'}}>
      {session.content.actions.map((action) => <Checkbox key={action.id}
        label={`Action: ${action.label}`} checked={draft.actionIds.includes(action.id)}
        onChange={(checked) => update({actionIds: checked ? [...draft.actionIds, action.id] :
          draft.actionIds.filter((id) => id !== action.id)})} />)}
    </Box>
    <Box sx={{gridColumn: '1/-1', display: 'flex', gap: 1}}>
      <Button intent="primary" disabled={!draft.name.trim()} onClick={save}>Save rule changes</Button>
      <Button intent="danger" onClick={remove}>Remove rule from rule set</Button>
    </Box>
  </Box>;
}
RuleDetailEditor.propTypes = {rule: PropTypes.object.isRequired, session: PropTypes.object.isRequired,
  invoke: PropTypes.func.isRequired};

function RuleSetDesigner({session, invoke, service}) {
  const [query, setQuery] = useState('');
  const [draft, setDraft] = useState({id: '', name: '', type: 'not_null',
    dimension: 'completeness', severity: 'warning', evaluationMode: 'exact'});
  const rules = useMemo(() => session.content.rules.filter((rule) => !query ||
    `${rule.name} ${rule.type} ${rule.dimension}`.toLowerCase().includes(query.toLowerCase())),
  [session.content.rules, query]);
  const selectedRule = session.content.rules.find((item) => item.id === session.selectedRuleId);
  const add = () => invoke('quality.rule.add', {rule: {...draft, parameters: {},
    threshold: null, enabled: true, actionIds: [], documentation: ''}});
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Rule Set Designer commands">
      <SearchField label="Search quality rules" value={query} onChange={setQuery}
        resultCount={rules.length} />
      <Button disabled={!session.selectedRuleId || !session.content.defaultScope}
        onClick={() => invoke('quality.rule.test', {ruleId: session.selectedRuleId})}>Test</Button>
    </Toolbar>
    <Box component="section" aria-label="Add typed quality rule"
      sx={{p: 1, display: 'grid', gap: 1,
        gridTemplateColumns: 'minmax(120px,1fr) minmax(160px,1.5fr) repeat(4,minmax(130px,1fr)) auto'}}>
      <TextField label="Stable rule ID" size="small" value={draft.id}
        onChange={(event) => setDraft({...draft, id: event.target.value})} />
      <TextField label="Rule name" size="small" value={draft.name}
        onChange={(event) => setDraft({...draft, name: event.target.value})} />
      <Select label="Family" size="small" value={draft.type}
        options={QUALITY_RULE_FAMILIES.map((value) => ({value, label: value}))}
        onChange={(type) => setDraft({...draft, type})} />
      <Select label="Dimension" size="small" value={draft.dimension}
        options={QUALITY_DIMENSIONS.map((value) => ({value, label: value}))}
        onChange={(dimension) => setDraft({...draft, dimension})} />
      <Select label="Severity" size="small" value={draft.severity}
        options={QUALITY_SEVERITIES.map((value) => ({value, label: value}))}
        onChange={(severity) => setDraft({...draft, severity})} />
      <Select label="Evaluation" size="small" value={draft.evaluationMode}
        options={QUALITY_EVALUATION_MODES.map((value) => ({value, label: value}))}
        onChange={(evaluationMode) => setDraft({...draft, evaluationMode})} />
      <Button disabled={!draft.id.trim() || !draft.name.trim()} onClick={add}>Add rule</Button>
    </Box>
    {!rules.length ? <EmptyState message="Create a typed rule or accept a profiler suggestion." /> :
      <Box sx={{flex: 1, minHeight: 0}}><DataGrid gridId="quality/rules"
        aria-label="Quality rule grid" rows={rules} readOnly enableRowSelect
        rowKeyGetter={(row) => row.id}
        columns={[{key: 'name', name: 'Rule', renderCell: ({row}) => <Button
          onClick={() => service.select(session.id, {ruleId: row.id})}>{row.name}</Button>},
        {key: 'type', name: 'Family'},
        {key: 'dimension', name: 'Dimension'}, {key: 'severity', name: 'Severity'},
        {key: 'evaluationMode', name: 'Evaluation mode'}, {key: 'enabled', name: 'Enabled'}]} />
      </Box>}
    {selectedRule && <RuleDetailEditor rule={selectedRule} session={session} invoke={invoke} />}
  </Box>;
}
RuleSetDesigner.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  service: PropTypes.object};

function ScopeDesigner({session, invoke, resources}) {
  const scope = session.content.defaultScope;
  const [picker, setPicker] = useState(false);
  const [mode, setMode] = useState(scope?.samplePolicy.mode ?? 'none_exact');
  const [limit, setLimit] = useState(scope?.samplePolicy.limit ??
    (scope?.samplePolicy.percentage ? scope.samplePolicy.percentage * 100 : 1000));
  const [partition, setPartition] = useState(scope?.partition ?? '');
  const [filterText, setFilterText] = useState(scope?.filter ? JSON.stringify(scope.filter) : '');
  const [windowText, setWindowText] = useState(scope?.window ? JSON.stringify(scope.window) : '');
  const [nativeText, setNativeText] = useState(scope?.nativeDetails ?
    JSON.stringify(scope.nativeDetails) : '');
  const [error, setError] = useState('');
  const apply = (resourceRef=scope?.resourceRef) => {
    if(!resourceRef) { setError('Select a provider resource before applying the scope.'); return; }
    try {
      const window = objectSource(windowText, 'Window');
      const nativeDetails = objectSource(nativeText, 'Native details') ?? {};
      const samplePolicy = ['first_n', 'random_n'].includes(mode) ? {mode, limit} :
        mode === 'percentage' ? {mode, percentage: Math.min(1, limit / 100)} :
          mode === 'partition' ? {mode, partition} :
            mode === 'time_window' ? {mode, window: window ?? {}} :
              mode === 'provider_native' ? {mode, nativeDetails} : {mode};
      const filter = objectSource(filterText, 'Filter'); setError('');
      invoke('quality.ruleset.create', {content: {...session.content,
        defaultScope: {id: scope?.id ?? `scope-${session.id}`, resourceRef,
          partition: partition || null, window, filter, samplePolicy, nativeDetails}}});
    } catch(exception) { setError(exception.message); }
  };
  const choose = (item) => {
    setPicker(false); if(item) apply(item.reference);
  };
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Data Scope commands">
      <Select label="Sampling mode" value={mode}
        options={QUALITY_SAMPLING_MODES.map((value) => ({value, label: value}))}
        onChange={(nextMode) => { setMode(nextMode);
          if(nextMode === 'percentage') setLimit((value) => Math.min(100, value)); }} />
      {['first_n', 'random_n', 'percentage'].includes(mode) && <NumberField
        label={mode === 'percentage' ? 'Percentage (0-100)' : 'Row limit'} value={limit}
        min={mode === 'percentage' ? 0.0001 : 1} max={mode === 'percentage' ? 100 : 1000000}
        onChange={(event) => setLimit(Number(event.target.value))} />}
      <Button onClick={() => setPicker(true)}>Select resource and apply scope</Button>
      <Button disabled={!scope} onClick={() => apply()}>Apply current scope</Button>
    </Toolbar>
    {error && <Box sx={{p: 1}}><Banner status="error">{error}</Banner></Box>}
    <Box component="section" aria-label="Data slice constraints"
      sx={{p: 1, display: 'grid', gap: 1, gridTemplateColumns: 'repeat(2,minmax(240px,1fr))'}}>
      <TextField label="Partition or slice identity" value={partition}
        onChange={(event) => setPartition(event.target.value)} />
      <TextField label="Filter object JSON" value={filterText}
        onChange={(event) => setFilterText(event.target.value)} />
      <TextField label="Window object JSON" value={windowText}
        onChange={(event) => setWindowText(event.target.value)} />
      <TextField label="Provider-native scope details JSON" value={nativeText}
        onChange={(event) => setNativeText(event.target.value)} />
    </Box>
    {!scope ? <EmptyState message="Choose a provider resource and bounded sampling policy." /> :
      <Box sx={{p: 2}}>
        <Box component="h2">Bound data slice</Box>
        <Box>Resource: {scope.resourceRef.canonical ?? scope.resourceRef.id}</Box>
        <Box>Sampling: {scope.samplePolicy.mode}</Box>
        <Box>Partition: {scope.partition ?? 'all permitted partitions'}</Box>
        <Box>Window: {scope.window ? JSON.stringify(scope.window) : 'not constrained'}</Box>
        <Box>Filter: {scope.filter ? JSON.stringify(scope.filter) : 'none'}</Box>
      </Box>}
    <ResourcePicker open={picker} items={resources} selected={[]} onClose={() => setPicker(false)}
      onConfirm={choose} />
  </Box>;
}
ScopeDesigner.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  resources: PropTypes.array};

function ValidationResults({session, invoke, service, canViewSamples, canExportSamples, onExport}) {
  const run = session.runs.find((item) => item.id === session.selectedRunId) ?? session.runs[0];
  const results = run?.results ?? [];
  const samples = new Map(session.failedSamples.map((sample) => [sample.resultId, sample]));
  const sampleRows = results.flatMap((result) => (samples.get(result.id)?.rows ?? [])
    .map((row, index) => ({id: `${result.id}:${index}`, ruleId: result.ruleId,
      value: JSON.stringify(row), sensitivity: samples.get(result.id).sensitivityState})));
  if(!run) return <EmptyState message="Run a rule test or rule set to inspect results." />;
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Validation result commands">
      <Select label="Run" value={run.id} options={session.runs.map((item) => ({
        value: item.id, label: `${item.id} · ${item.summary.outcome}`}))}
      onChange={(runId) => service.select(session.id, {runId})} />
      <Button onClick={() => invoke('quality.result.export', {runId: run.id, format: 'json'})
        .then(onExport)}>Export metadata result</Button>
      <Button disabled={!canExportSamples || !sampleRows.length}
        onClick={() => invoke('quality.result.export', {runId: run.id, format: 'json',
          includeSamples: true}).then(onExport)}>Export failed rows</Button>
    </Toolbar>
    <Box sx={{flex: 1, minHeight: 0}}><DataGrid gridId="quality/run-results"
      aria-label="Quality rule results" rows={results} readOnly rowKeyGetter={(row) => row.id}
      columns={[{key: 'ruleId', name: 'Rule'}, {key: 'status', name: 'Status'},
        {key: 'severity', name: 'Severity'}, {key: 'evaluationMode', name: 'Evaluation mode'},
        {key: 'observedMetric', name: 'Observed'}, {key: 'violationCount', name: 'Violations'},
        {key: 'evaluatedCount', name: 'Evaluated'}, {key: 'error', name: 'Execution error'}]} />
    </Box>
    <Box component="section" aria-label="Failed samples" sx={{height: 180, minHeight: 0}}>
      {!canViewSamples ? <Banner status="warning">
        Failed-row samples require quality.view_samples permission.
      </Banner> : !sampleRows.length ? <EmptyState message="No permitted failed-row samples." /> :
        <DataGrid gridId="quality/failed-samples" aria-label="Protected failed-row samples"
          rows={sampleRows} readOnly rowKeyGetter={(row) => row.id}
          columns={[{key: 'ruleId', name: 'Rule'}, {key: 'sensitivity', name: 'Sensitivity'},
            {key: 'value', name: 'Bounded example'}]} />}
    </Box>
  </Box>;
}
ValidationResults.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  service: PropTypes.object, canViewSamples: PropTypes.bool, canExportSamples: PropTypes.bool,
  onExport: PropTypes.func};

function Profiler({session, invoke}) {
  const [budget, setBudget] = useState(10000); const profile = session.profile;
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Profiler commands">
      <NumberField label="Maximum rows/statistical budget" min={1} max={1000000}
        value={budget} onChange={(event) => setBudget(Number(event.target.value))} />
      <Button disabled={!session.content.defaultScope}
        onClick={() => invoke('quality.profile.run', {budget})}>Run bounded profile</Button>
    </Toolbar>
    {!profile ? <EmptyState
      message="Profile a bounded data slice to obtain evidenced draft-rule suggestions." /> : <>
      <Box sx={{p: 1}}>Source revision: {profile.sourceRevision} · Budget: {profile.budget}</Box>
      <Box sx={{flex: 1, minHeight: 0}}><DataGrid gridId="quality/profile-suggestions"
        aria-label="Generated quality rule suggestions" rows={profile.suggestions} readOnly
        enableRowSelect rowKeyGetter={(row) => row.id}
        columns={[{key: 'name', name: 'Suggestion'}, {key: 'type', name: 'Family'},
          {key: 'dimension', name: 'Dimension'}, {key: 'evaluationMode', name: 'Evaluation'},
          {key: 'sourceRevision', name: 'Source revision'}]} /></Box>
      <Box sx={{p: 1, display: 'flex', gap: 1, flexWrap: 'wrap'}}>
        {profile.suggestions.map((rule) => <Button key={rule.id}
          disabled={session.content.rules.some((item) => item.id === rule.id)}
          onClick={() => invoke('quality.rule.add', {rule})}>Accept {rule.name}</Button>)}
      </Box>
    </>}
  </Box>;
}
Profiler.propTypes = {session: PropTypes.object, invoke: PropTypes.func};

function QualityHistory({session, invoke}) {
  const baselines = session.baselines.map((baseline) => ({...baseline,
    metricCount: Object.keys(baseline.metrics).length,
    drifted: baseline.drift?.drifted ?? false,
    driftDetails: baseline.drift ? baseline.drift.changes.filter((item) => item.drifted)
      .map((item) => item.metric).join(', ') : 'first baseline'}));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Quality History commands"><Button disabled={!session.content.defaultScope}
      onClick={() => invoke('quality.baseline.capture', {})}>Capture baseline</Button></Toolbar>
    {!baselines.length && !session.runs.length ? <EmptyState
      message="Capture a baseline or complete a run to build quality history." /> : <>
      <Box sx={{height: '50%', minHeight: 160}}><DataGrid gridId="quality/baselines"
        aria-label="Quality baselines and drift" rows={baselines} readOnly
        rowKeyGetter={(row) => row.id} columns={[{key: 'id', name: 'Baseline'},
          {key: 'capturedAt', name: 'Captured'}, {key: 'dataRevision', name: 'Data revision'},
          {key: 'metricCount', name: 'Metrics'}, {key: 'drifted', name: 'Drift detected'},
          {key: 'driftDetails', name: 'Changed metrics'}]} /></Box>
      <Box sx={{height: '50%', minHeight: 160}}><DataGrid gridId="quality/history-runs"
        aria-label="Quality run history" rows={runRows(session)} readOnly
        rowKeyGetter={(row) => row.id} columns={[{key: 'id', name: 'Run'},
          {key: 'outcome', name: 'Outcome'}, {key: 'passed', name: 'Passed'},
          {key: 'failed', name: 'Failed'}, {key: 'errors', name: 'Errors'},
          {key: 'modes', name: 'Evaluation modes'}, {key: 'finishedAt', name: 'Finished'}]} />
      </Box>
    </>}
  </Box>;
}
QualityHistory.propTypes = {session: PropTypes.object, invoke: PropTypes.func};

function ScheduleActions({session, invoke, resources}) {
  const [picker, setPicker] = useState(false);
  const schedule = session.content.schedule; const actions = session.content.actions;
  const [scheduleText, setScheduleText] = useState(schedule ? JSON.stringify(schedule) : '');
  const [action, setAction] = useState({id: '', label: '', type: 'notify', enabled: true,
    severities: [...QUALITY_SEVERITIES], commandId: '', parameters: '{}'});
  const [error, setError] = useState('');
  const saveSchedule = () => {
    try { const next = objectSource(scheduleText, 'Schedule'); setError('');
      invoke('quality.ruleset.create', {content: {...session.content, schedule: next}});
    } catch(exception) { setError(exception.message); }
  };
  const saveAction = () => {
    try {
      const next = {id: action.id, label: action.label, type: action.type,
        enabled: action.enabled, severities: action.severities,
        parameters: objectSource(action.parameters, 'Action parameters') ?? {},
        ...(action.type === 'call_command' ? {commandId: action.commandId} : {})};
      const exists = actions.some((item) => item.id === action.id); setError('');
      invoke('quality.ruleset.create', {content: {...session.content,
        actions: exists ? actions.map((item) => item.id === action.id ? next : item) :
          [...actions, next]}});
    } catch(exception) { setError(exception.message); }
  };
  const removeAction = () => {
    const usedBy = session.content.rules.filter((rule) => rule.actionIds.includes(action.id));
    if(usedBy.length) { setError(`Action is used by rules: ${usedBy.map((rule) => rule.id).join(', ')}`);
      return; }
    invoke('quality.ruleset.create', {content: {...session.content,
      actions: actions.filter((item) => item.id !== action.id)}});
  };
  return <Box sx={{height: '100%', overflow: 'auto', p: 2}}>
    {error && <Banner status="error">{error}</Banner>}
    <Box component="h2">Schedule</Box>
    <TextField label="Canonical schedule object JSON" value={scheduleText}
      onChange={(event) => setScheduleText(event.target.value)} />
    <Box sx={{display: 'flex', gap: 1, my: 1}}>
      <Button intent="primary" onClick={saveSchedule}>Save schedule definition</Button>
      <Button onClick={() => { setScheduleText(''); invoke('quality.ruleset.create', {
        content: {...session.content, schedule: null}}); }}>Remove schedule</Button>
    </Box>
    {!schedule && <Banner status="info">
      No production schedule is present in this authored rule-set definition.
    </Banner>}
    <Box component="h2">Failure actions</Box>
    {!actions.length ? <EmptyState message="No failure actions are configured." /> :
      <DataGrid gridId="quality/actions" aria-label="Quality failure actions" rows={actions}
        readOnly rowKeyGetter={(row) => row.id} columns={[{key: 'label', name: 'Action',
          renderCell: ({row}) => <Button onClick={() => setAction({...row,
            commandId: row.commandId ?? '', parameters: JSON.stringify(row.parameters)})}>
            {row.label}</Button>},
        {key: 'type', name: 'Type'}, {key: 'severities', name: 'Severities'},
        {key: 'commandId', name: 'Command'}, {key: 'enabled', name: 'Enabled'}]} />}
    <Box component="section" aria-label="Quality failure action editor" sx={{mt: 1,
      display: 'grid', gap: 1, gridTemplateColumns: 'repeat(3,minmax(180px,1fr))'}}>
      <TextField label="Stable action ID" value={action.id}
        onChange={(event) => setAction({...action, id: event.target.value})} />
      <TextField label="Action label" value={action.label}
        onChange={(event) => setAction({...action, label: event.target.value})} />
      <Select label="Action type" value={action.type}
        options={QUALITY_ACTION_TYPES.map((value) => ({value, label: value}))}
        onChange={(type) => setAction({...action, type})} />
      {action.type === 'call_command' && <TextField label="Registered command ID"
        value={action.commandId}
        onChange={(event) => setAction({...action, commandId: event.target.value})} />}
      <TextField label="Action parameters JSON" value={action.parameters}
        onChange={(event) => setAction({...action, parameters: event.target.value})} />
      <Checkbox label="Action enabled" checked={action.enabled}
        onChange={(enabled) => setAction({...action, enabled})} />
      <Box sx={{gridColumn: '1/-1', display: 'flex', gap: 1}}>
        {QUALITY_SEVERITIES.map((severity) => <Checkbox key={severity}
          label={`Run for ${severity}`} checked={action.severities.includes(severity)}
          onChange={(checked) => setAction({...action, severities: checked ?
            [...action.severities, severity] : action.severities.filter(
              (item) => item !== severity)})} />)}
      </Box>
      <Box sx={{gridColumn: '1/-1', display: 'flex', gap: 1}}>
        <Button intent="primary" disabled={!action.id.trim() || !action.label.trim() ||
          (action.type === 'call_command' && !action.commandId.trim())}
        onClick={saveAction}>Save failure action</Button>
        <Button intent="danger" disabled={!actions.some((item) => item.id === action.id)}
          onClick={removeAction}>Remove failure action</Button>
      </Box>
    </Box>
    <Box sx={{mt: 2}}><Button onClick={() => setPicker(true)}>Link Data Contract</Button></Box>
    <ResourcePicker open={picker} items={resources} selected={[]} onClose={() => setPicker(false)}
      onConfirm={(item) => { setPicker(false); if(item) invoke('quality.contract.sync', {
        contractRef: item.reference,
      }); }} />
  </Box>;
}
ScheduleActions.propTypes = {session: PropTypes.object, invoke: PropTypes.func,
  resources: PropTypes.array};

const VIEWS = Object.freeze({quality_overview: QualityOverview,
  rule_set_designer: RuleSetDesigner, data_scope_slice_designer: ScopeDesigner,
  validation_run_results: ValidationResults, profiler: Profiler,
  quality_history: QualityHistory, schedule_actions: ScheduleActions});

export function QualityWorkspace({service, sessionId, surface='quality_overview',
  executeCommand, resources=[], currentUser={}, onExport=() => {}, onStateChange}) {
  const session = useSession(service, sessionId); const [active, setActive] = useState(surface);
  useEffect(() => onStateChange?.(session), [session, onStateChange]);
  const invoke = async (commandId, args={}) => {
    try { return await executeCommand(commandId, args, {sessionId, service, currentUser}); }
    catch(error) {
      const prefix = String(error.message).split(':')[0].replaceAll(' ', '_');
      service.reportError(sessionId, error, prefix); return undefined;
    }
  };
  const View = VIEWS[active] ?? QualityOverview;
  return <Box data-module="cdeadmin.quality"
    sx={{height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column'}}>
    <Box role="tablist" aria-label="Data Quality surfaces"
      sx={{display: 'flex', overflowX: 'auto', borderBottom: '1px solid', borderColor: 'divider'}}>
      {QUALITY_SURFACES.map((item) => <Tab key={item.id} label={item.title}
        active={active === item.id} attention={item.id === 'validation_run_results' &&
          session.runs[0]?.summary.outcome !== 'pass'} onActivate={() => setActive(item.id)} />)}
    </Box>
    <Box sx={{px: 1, py: 0.5, display: 'flex', gap: 1, alignItems: 'center'}}>
      <Badge status={stateStatus(session.state)} label={session.state.replaceAll('_', ' ')} />
      <Box>{session.content.rules.length} rules · {session.runs.length} runs</Box>
      {session.providerStatus && <Badge label={session.providerStatus.supportState}
        status={session.providerStatus.supportState.startsWith('supported') ? 'success' : 'warning'} />}
      {session.dirty && <Badge status="warning" label="Unsaved" />}
    </Box>
    <Box sx={{flex: 1, minHeight: 0, overflow: 'hidden'}}>
      <StateBoundary session={session}><View session={session} invoke={invoke}
        resources={resources} service={service} currentUser={currentUser} onExport={onExport}
        canViewSamples={currentUser.permissions?.includes('quality.view_samples')}
        canExportSamples={currentUser.permissions?.includes('quality.export_samples')} />
      </StateBoundary>
    </Box>
  </Box>;
}
QualityWorkspace.propTypes = {service: PropTypes.object.isRequired,
  sessionId: PropTypes.string.isRequired,
  surface: PropTypes.oneOf(QUALITY_SURFACES.map((item) => item.id)),
  executeCommand: PropTypes.func.isRequired, resources: PropTypes.array,
  currentUser: PropTypes.object, onExport: PropTypes.func, onStateChange: PropTypes.func};

export function qualityInspector(session) {
  const rule = session?.content.rules.find((item) => item.id === session.selectedRuleId);
  return {'Rule definition': rule ? `${rule.name} · ${rule.type}` : 'None selected',
    Parameters: rule ? JSON.stringify(rule.parameters) : 'None',
    'Scope override': rule?.scope?.id ?? session?.content.defaultScope?.id ?? 'Not configured',
    'Failure action': rule?.actionIds.join(', ') || 'None',
    Documentation: rule?.documentation || 'No documentation'};
}

export function QualityNavigator({service, onOpen}) {
  const [sessions, setSessions] = useState(() => service.list());
  const [expanded, setExpanded] = useState(() => new Set());
  useEffect(() => service.subscribe(() => setSessions(service.list())), [service]);
  return <Box role="tree" aria-label="Data Quality" sx={{height: '100%', overflow: 'auto'}}>
    {!sessions.length && <EmptyState message="No Data Quality assets are open." />}
    {sessions.map((session) => {
      const open = expanded.has(session.id);
      return <React.Fragment key={session.id}>
        <TreeRow label={session.content.name || session.id} level={1} expandable expanded={open}
          trailing={<Badge label={session.state} status={stateStatus(session.state)} />}
          onToggle={() => setExpanded((prior) => { const next = new Set(prior);
            open ? next.delete(session.id) : next.add(session.id); return next; })}
          onOpen={() => onOpen?.(session.id, 'quality_overview')} />
        {open && [{label: 'Rule Sets', surface: 'rule_set_designer'},
          {label: 'Scopes', surface: 'data_scope_slice_designer'},
          {label: 'Runs', surface: 'validation_run_results'},
          {label: 'Baselines', surface: 'quality_history'},
          {label: 'Schedules', surface: 'schedule_actions'}].map((item) =>
          <TreeRow key={item.label} label={item.label} level={2}
            onOpen={() => onOpen?.(session.id, item.surface)} />)}
      </React.Fragment>;
    })}
  </Box>;
}
QualityNavigator.propTypes = {service: PropTypes.object.isRequired, onOpen: PropTypes.func};
