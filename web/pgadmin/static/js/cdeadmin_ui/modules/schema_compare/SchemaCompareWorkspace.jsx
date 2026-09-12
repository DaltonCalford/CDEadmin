/////////////////////////////////////////////////////////////
// Schema Comparison module surfaces in the shared CDEadmin shell.
/////////////////////////////////////////////////////////////

import React, {useEffect, useMemo, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {Button} from '../../primitives/Button';
import {Select} from '../../primitives/Choice';
import {TextArea} from '../../primitives/Field';
import {SearchField} from '../../primitives/AdvancedControls';
import {ResourcePicker, AssetPicker, TreeRow} from '../../navigation/AdvancedNavigation';
import {Tab} from '../../navigation/TabsAndBreadcrumbs';
import {Toolbar} from '../../layout/WorkbenchChrome';
import {Banner, Badge, ProgressBar, Skeleton} from '../../feedback/Indicators';
import {EmptyState} from '../../feedback/EmptyState';
import DataGrid from '../../data/DataGrid';

export const SCHEMA_COMPARE_SURFACES = Object.freeze([
  {id: 'compare_setup', title: 'Compare Setup'},
  {id: 'diff_tree', title: 'Diff Tree'},
  {id: 'object_diff', title: 'Object Diff'},
  {id: 'mapping_review', title: 'Mapping Review'},
  {id: 'change_plan', title: 'Change Plan'},
  {id: 'apply_export_review', title: 'Apply/Export Review'},
]);

function useSession(service, sessionId) {
  const [session, setSession] = useState(() => service.get(sessionId));
  useEffect(() => service.subscribe((next) => {
    if(next.id === sessionId) setSession(next);
  }), [service, sessionId]);
  return session;
}

function statusColor(state) {
  if(['runtime_failure', 'validation_error', 'permission_denied'].includes(state)) return 'error';
  if(['stale', 'partial', 'disconnected'].includes(state)) return 'warning';
  if(state === 'ready') return 'success';
  return 'info';
}

function StateBoundary({session, children}) {
  return <>
    {session.state === 'background_task_active' && <Box sx={{p: 1}}>
      <ProgressBar label="Schema Comparison task" status="indeterminate" />
    </Box>}
    {session.state === 'loading' && <Box sx={{p: 2}}><Skeleton lines={8} /></Box>}
    {session.error && <Box sx={{p: 1}}><Banner status="error">{session.error}</Banner></Box>}
    {['stale', 'partial', 'disconnected', 'read_only'].includes(session.state) &&
      <Box sx={{p: 1}}><Banner status="warning">
        {session.state === 'stale' ?
          'The displayed result is stale. Run comparison again before planning or applying.' :
          `Schema Comparison is ${session.state.replace('_', ' ')}.`}
      </Banner></Box>}
    {children}
  </>;
}

StateBoundary.propTypes = {session: PropTypes.object.isRequired, children: PropTypes.node};

function SourceCard({side, reference, onChooseResource, onChooseAsset, disabled}) {
  return <Box component="section" aria-label={`${side} comparison source`}
    sx={{border: '1px solid', borderColor: 'divider', p: 2, minWidth: 280, flex: 1}}>
    <Box component="h3" sx={{m: 0, mb: 1}}>{side} source</Box>
    {reference ? <Box component="dl" sx={{m: 0}}>
      <dt>Type</dt><dd>{reference.schema ?? reference.assetType}</dd>
      <dt>Identity</dt><dd>{reference.canonical ??
        `${reference.projectId}/${reference.assetId}`}</dd>
    </Box> : <EmptyState message={`Choose the ${side.toLowerCase()} source.`} />}
    <Box sx={{display: 'flex', gap: 1, mt: 2}}>
      <Button disabled={disabled} onClick={onChooseResource}>Choose live resource</Button>
      <Button disabled={disabled} onClick={onChooseAsset}>Choose project asset</Button>
    </Box>
  </Box>;
}

SourceCard.propTypes = {
  side: PropTypes.string.isRequired,
  reference: PropTypes.object,
  onChooseResource: PropTypes.func,
  onChooseAsset: PropTypes.func,
  disabled: PropTypes.bool,
};

function CompareSetup({session, invoke, resources, assets}) {
  const [picker, setPicker] = useState(null);
  const choose = (item) => {
    invoke('schema_compare.session.create', {
      configureSessionId: session.id,
      [picker.side === 'Left' ? 'leftRef' : 'rightRef']: item.reference,
    });
    setPicker(null);
  };
  const busy = session.state === 'background_task_active';
  return <Box sx={{p: 2, overflow: 'auto', height: '100%'}}>
    <Box component="h2" sx={{mt: 0}}>Compare Setup</Box>
    <Box sx={{display: 'flex', flexWrap: 'wrap', gap: 2}}>
      {['Left', 'Right'].map((side) => <SourceCard key={side} side={side}
        reference={side === 'Left' ? session.content.leftRef : session.content.rightRef}
        disabled={busy}
        onChooseResource={() => setPicker({side, kind: 'resource'})}
        onChooseAsset={() => setPicker({side, kind: 'asset'})} />)}
    </Box>
    <Box component="fieldset" sx={{mt: 2, border: '1px solid', borderColor: 'divider'}}>
      <legend>Comparison scope and normalization options</legend>
      <TextArea rows={6} fullWidth label="Options (JSON)" readOnly
        value={JSON.stringify(session.content.options, null, 2)} />
    </Box>
    <Box sx={{mt: 2}}><Button intent="primary" disabled={busy ||
      !session.content.leftRef || !session.content.rightRef}
    onClick={() => invoke('schema_compare.run')}>Run comparison</Button></Box>
    <ResourcePicker open={picker?.kind === 'resource'} items={resources}
      selected={[]} onClose={() => setPicker(null)} onConfirm={choose} />
    <AssetPicker open={picker?.kind === 'asset'} items={assets}
      selected={[]} onClose={() => setPicker(null)} onConfirm={choose} />
  </Box>;
}

CompareSetup.propTypes = {
  session: PropTypes.object.isRequired, invoke: PropTypes.func.isRequired,
  resources: PropTypes.array, assets: PropTypes.array,
};

function DiffTree({session, selectDiff, openSurface}) {
  const [filter, setFilter] = useState('');
  const [expanded, setExpanded] = useState(() => new Set());
  const groups = useMemo(() => {
    const result = new Map();
    for(const diff of session.result?.differences ?? []) {
      if(filter && !`${diff.qualifiedName} ${diff.classification}`
        .toLowerCase().includes(filter.toLowerCase())) continue;
      const values = result.get(diff.classification) ?? [];
      values.push(diff); result.set(diff.classification, values);
    }
    return [...result.entries()].sort(([left], [right]) => left.localeCompare(right));
  }, [session.result, filter]);
  if(!session.result) return <EmptyState
    message="Run a comparison to browse hierarchical differences." />;
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Box sx={{p: 1}}><SearchField label="Filter differences" value={filter}
      onChange={setFilter} resultCount={groups.reduce((n, [, values]) => n + values.length, 0)} />
    </Box>
    <Box role="tree" aria-label="Schema differences" sx={{overflow: 'auto', flex: 1}}>
      {groups.map(([classification, values]) => {
        const open = expanded.has(classification);
        return <React.Fragment key={classification}>
          <TreeRow label={`${classification.replaceAll('_', ' ')} (${values.length})`}
            level={1} expandable expanded={open}
            onToggle={() => setExpanded((prior) => {
              const next = new Set(prior); open ? next.delete(classification) :
                next.add(classification); return next;
            })} />
          {open && values.map((diff) => <TreeRow key={diff.id}
            label={diff.qualifiedName} level={2}
            selected={session.selectedDiffId === diff.id}
            trailing={<Badge label={diff.kind} />}
            onSelect={() => selectDiff(diff.id)}
            onOpen={() => { selectDiff(diff.id); openSurface('object_diff'); }} />)}
        </React.Fragment>;
      })}
    </Box>
  </Box>;
}

DiffTree.propTypes = {
  session: PropTypes.object.isRequired,
  selectDiff: PropTypes.func.isRequired,
  openSurface: PropTypes.func.isRequired,
};

function ObjectDiff({session}) {
  const diff = session.result?.differences.find((item) => item.id === session.selectedDiffId);
  if(!diff) return <EmptyState message="Select an item in Diff Tree to inspect it." />;
  const left = session.leftSnapshot?.objects?.find((item) => item.id === diff.leftId);
  const right = session.rightSnapshot?.objects?.find((item) => item.id === diff.rightId);
  return <Box sx={{height: '100%', display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', overflow: 'hidden'}}>
    {[['Left', left], ['Right', right]].map(([label, object]) => <Box key={label}
      component="section" aria-label={`${label} object definition`}
      sx={{p: 2, overflow: 'auto', borderRight: label === 'Left' ? '1px solid' : 0,
        borderColor: 'divider'}}>
      <Box component="h2" sx={{mt: 0}}>{label}</Box>
      {!object ? <EmptyState message={`No ${label.toLowerCase()} object exists.`} /> : <>
        <Badge label={object.kind} />
        <Box component="h3">{object.qualifiedName}</Box>
        <Box component="h4">Normalized metadata</Box>
        <Box component="pre" tabIndex={0} sx={{whiteSpace: 'pre-wrap'}}>
          {JSON.stringify(object.normalized, null, 2)}
        </Box>
        <Box component="h4">Provider-native metadata</Box>
        <Box component="pre" tabIndex={0} sx={{whiteSpace: 'pre-wrap'}}>
          {JSON.stringify(object.native, null, 2)}
        </Box>
      </>}
    </Box>)}
  </Box>;
}

ObjectDiff.propTypes = {session: PropTypes.object.isRequired};

function MappingReview({session, invoke}) {
  if(!session.result) return <EmptyState message="Run a comparison to review mappings." />;
  const rows = session.result.renameCandidates.map((candidate) => ({
    ...candidate, evidenceText: candidate.evidence.join(', '),
  }));
  return <Box sx={{height: '100%', minHeight: 0, p: 1}}>
    <Box component="h2" sx={{mt: 0}}>Rename and equivalence candidates</Box>
    <Banner status="info">Candidates are never accepted automatically. Review native
      semantics before accepting a mapping.</Banner>
    <Box sx={{height: 'calc(100% - 92px)', mt: 1}}>
      <DataGrid gridId="schema-compare/mappings" aria-label="Mapping candidates"
        columns={[
          {key: 'leftId', name: 'Left object'}, {key: 'rightId', name: 'Right object'},
          {key: 'kind', name: 'Kind'}, {key: 'evidenceText', name: 'Evidence'},
        ]} rows={rows} readOnly enableRowSelect rowKeyGetter={(row) => row.id}
        onItemSelect={(row) => row?.id && invoke('schema_compare.rename.accept', {
          candidateId: row.id, category: 'representational_difference',
        })} />
    </Box>
  </Box>;
}

MappingReview.propTypes = {
  session: PropTypes.object.isRequired, invoke: PropTypes.func.isRequired,
};

function ChangePlan({session, invoke}) {
  const options = [session.content.leftRef, session.content.rightRef]
    .filter((item) => item?.schema === 'cdeadmin.resource-ref.v1')
    .map((item, index) => ({value: item.canonical, label: `${index ? 'Right' : 'Left'}: `+
      item.canonical}));
  const [target, setTarget] = useState('');
  const targetRef = [session.content.leftRef, session.content.rightRef]
    .find((item) => item?.canonical === target);
  return <Box sx={{height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Change plan commands">
      <Select size="small" label="Explicit live target" value={target} options={options}
        onChange={setTarget} sx={{minWidth: 360}} />
      <Button disabled={!session.result || !targetRef}
        onClick={() => invoke('schema_compare.plan.generate', {targetRef})}>
        Generate plan
      </Button>
      <Button disabled={!session.plan}
        onClick={() => invoke('schema_compare.plan.validate')}>Validate exact plan</Button>
    </Toolbar>
    {!session.plan ? <EmptyState
      message="Select an explicit live target and generate a provider-native plan." /> :
      <Box sx={{flex: 1, minHeight: 0}}><DataGrid gridId="schema-compare/change-plan"
        aria-label="Dependency ordered change plan" readOnly
        columns={[
          {key: 'action', name: 'Action'}, {key: 'kind', name: 'Object kind'},
          {key: 'qualifiedName', name: 'Qualified name'}, {key: 'risk', name: 'Risk'},
          {key: 'reversible', name: 'Reversible'},
          {key: 'nativeStatement', name: 'Provider-native operation'},
        ]} rows={session.plan.operations} rowKeyGetter={(row) => row.id} /></Box>}
  </Box>;
}

ChangePlan.propTypes = {session: PropTypes.object.isRequired, invoke: PropTypes.func.isRequired};

function ApplyExportReview({session, invoke, onExport}) {
  const [confirmation, setConfirmation] = useState('');
  if(!session.plan) return <EmptyState message="Generate a change plan before review." />;
  return <Box sx={{p: 2, height: '100%', overflow: 'auto'}}>
    <Box component="h2" sx={{mt: 0}}>Apply and Export Review</Box>
    <Banner status={session.validation?.applyReady ? 'success' : 'warning'}>
      {session.validation?.applyReady ? 'The exact plan passed target-provider validation.' :
        'Apply is disabled until the exact plan passes target-provider validation.'}
    </Banner>
    <Box component="dl" sx={{display: 'grid', gridTemplateColumns: '180px 1fr', gap: 1}}>
      <dt>Target</dt><dd>{session.plan.targetRef.canonical}</dd>
      <dt>Operations</dt><dd>{session.plan.operations.length}</dd>
      <dt>Destructive</dt><dd>{session.plan.destructive ? 'Yes' : 'No'}</dd>
      <dt>Partial selection</dt><dd>{session.plan.partialSelection ? 'Yes' : 'No'}</dd>
    </Box>
    {session.plan.destructive && <TextArea rows={2} fullWidth
      label="Type the exact plan ID to authorize destructive apply"
      value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />}
    <Box sx={{display: 'flex', gap: 1, my: 2}}>
      <Button onClick={() => invoke('schema_compare.plan.export').then(onExport)}>
        Export versioned plan
      </Button>
      <Button intent={session.plan.destructive ? 'destructive' : 'primary'}
        disabled={!session.validation?.applyReady || (session.plan.destructive &&
          confirmation !== session.plan.id)}
        onClick={() => invoke('schema_compare.plan.apply', {confirmation})}>
        Apply validated plan
      </Button>
    </Box>
    <Box component="h3">Generated provider-native operations</Box>
    <Box component="pre" tabIndex={0} sx={{whiteSpace: 'pre-wrap'}}>
      {session.plan.operations.map((item) => item.nativeStatement).join('\n\n')}
    </Box>
  </Box>;
}

ApplyExportReview.propTypes = {
  session: PropTypes.object.isRequired, invoke: PropTypes.func.isRequired,
  onExport: PropTypes.func,
};

const VIEWS = Object.freeze({
  compare_setup: CompareSetup, diff_tree: DiffTree, object_diff: ObjectDiff,
  mapping_review: MappingReview, change_plan: ChangePlan,
  apply_export_review: ApplyExportReview,
});

export function SchemaCompareWorkspace({service, sessionId, surface='compare_setup',
  executeCommand, resources=[], assets=[], onExport=() => {}, onStateChange}) {
  const session = useSession(service, sessionId);
  const [active, setActive] = useState(surface);
  useEffect(() => onStateChange?.(session), [session, onStateChange]);
  const invoke = async (commandId, args={}) => {
    try {
      return await executeCommand(commandId, args, {sessionId, service,
        openModuleSurface: setActive});
    } catch(error) {
      service.reportError(sessionId, error,
        String(error.message).split(':')[0].replaceAll(' ', '_'));
      throw error;
    }
  };
  const View = VIEWS[active] ?? CompareSetup;
  return <Box data-module="cdeadmin.schema_compare"
    sx={{height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column'}}>
    <Box role="tablist" aria-label="Schema Comparison surfaces"
      sx={{display: 'flex', overflowX: 'auto', borderBottom: '1px solid',
        borderColor: 'divider'}}>
      {SCHEMA_COMPARE_SURFACES.map((item) => <Tab key={item.id} label={item.title}
        active={active === item.id} attention={item.id === 'mapping_review' &&
          Boolean(session.result?.renameCandidates.length)}
        onActivate={() => setActive(item.id)} />)}
    </Box>
    <Box sx={{px: 1, py: 0.5, display: 'flex', gap: 1, alignItems: 'center'}}>
      <Badge status={statusColor(session.state)} label={session.state.replaceAll('_', ' ')} />
      {session.result && <Box>{session.result.differences.length} differences</Box>}
      {session.dirty && <Badge status="warning" label="Unsaved" />}
    </Box>
    <Box sx={{flex: 1, minHeight: 0, overflow: 'hidden'}}>
      <StateBoundary session={session}>
        <View session={session} invoke={invoke} resources={resources} assets={assets}
          onExport={onExport} selectDiff={(diffId) => service.selectDiff(sessionId, diffId)}
          openSurface={setActive} />
      </StateBoundary>
    </Box>
  </Box>;
}

SchemaCompareWorkspace.propTypes = {
  service: PropTypes.object.isRequired,
  sessionId: PropTypes.string.isRequired,
  surface: PropTypes.oneOf(SCHEMA_COMPARE_SURFACES.map((item) => item.id)),
  executeCommand: PropTypes.func.isRequired,
  resources: PropTypes.array,
  assets: PropTypes.array,
  onExport: PropTypes.func,
  onStateChange: PropTypes.func,
};

export function schemaCompareInspector(session) {
  const diff = session?.result?.differences.find((item) =>
    item.id === session.selectedDiffId);
  if(!diff) return {Classification: 'No difference selected'};
  const operation = session.plan?.operations.find((item) => item.diffId === diff.id);
  return {
    Classification: diff.classification,
    Mapping: diff.matchReason || 'No accepted mapping',
    Dependencies: operation?.dependencies ?? [],
    Risk: operation?.risk ?? 'Not planned',
    'Proposed operation': operation?.action ?? 'Not planned',
  };
}

export function SchemaCompareNavigator({service, onOpen}) {
  const [sessions, setSessions] = useState(() => service.list());
  const [expanded, setExpanded] = useState(() => new Set());
  useEffect(() => service.subscribe(() => setSessions(service.list())), [service]);
  return <Box role="tree" aria-label="Schema Diff" sx={{height: '100%', overflow: 'auto'}}>
    {!sessions.length && <EmptyState message="No Schema Comparison sessions are open." />}
    {sessions.map((session) => {
      const open = expanded.has(session.id);
      const counts = session.result?.counts ?? {};
      return <React.Fragment key={session.id}>
        <TreeRow label={session.id} level={1} expandable expanded={open}
          trailing={<Badge label={session.state} status={statusColor(session.state)} />}
          onToggle={() => setExpanded((prior) => {
            const next = new Set(prior); open ? next.delete(session.id) : next.add(session.id);
            return next;
          })} onOpen={() => onOpen?.(session.id, 'compare_setup')} />
        {open && Object.entries(counts).filter(([, count]) => count > 0)
          .map(([classification, count]) => <TreeRow key={classification}
            label={`${classification.replaceAll('_', ' ')} (${count})`} level={2}
            onOpen={() => onOpen?.(session.id, 'diff_tree')} />)}
      </React.Fragment>;
    })}
  </Box>;
}

SchemaCompareNavigator.propTypes = {
  service: PropTypes.object.isRequired,
  onOpen: PropTypes.func,
};
