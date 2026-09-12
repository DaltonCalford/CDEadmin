/////////////////////////////////////////////////////////////
// CDEadmin context-sensitive Inspector, Drawer and Status hosts.
/////////////////////////////////////////////////////////////

import React, {useEffect, useMemo, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {
  bottomRegistry, diagnosticsService, inspectorRegistry, statusRegistry, toolboxRegistry,
  workbenchContextService,
} from '../platform/PlatformRegistry';
import {Button} from '../primitives/Button';
import {EmptyState} from '../feedback/EmptyState';
import {InspectorSection, ToolboxItem} from '../layout/WorkbenchChrome';
import {Badge, StatusDot} from '../feedback/Indicators';
import {SafeText} from '../feedback/SafeText';

function useContext(service) {
  const [context, setContext] = useState(() => service.snapshot());
  useEffect(() => service.subscribe(setContext), [service]);
  return context;
}

function ContributionValue({value}) {
  if(React.isValidElement(value)) return value;
  if(value === undefined || value === null || value === '') {
    return <EmptyState message="No information is available for this context." />;
  }
  if(Array.isArray(value)) {
    if(!value.length) return <EmptyState message="No items are reported." />;
    return <Box component="ul" sx={{m: 0, pl: 2}}>
      {value.map((item, index) => <Box component="li"
        key={item?.id ?? index}><SafeText text={typeof item === 'object' ?
          item.message ?? item.label ?? JSON.stringify(item) : String(item)} /></Box>)}
    </Box>;
  }
  if(typeof value === 'object') {
    return <Box component="dl" sx={{m: 0, display: 'grid',
      gridTemplateColumns: 'minmax(80px, auto) 1fr', gap: 0.5}}>
      {Object.entries(value).map(([key, item]) => <React.Fragment key={key}>
        <Box component="dt" sx={{fontWeight: 600}}>{key}</Box>
        <Box component="dd" sx={{m: 0}}><SafeText text={
          typeof item === 'object' ? JSON.stringify(item) : String(item)
        } /></Box>
      </React.Fragment>)}
    </Box>;
  }
  return <SafeText text={String(value)} />;
}

ContributionValue.propTypes = {value: PropTypes.any};

function TabbedHost({label, pages, emptyMessage}) {
  const available = pages.filter((page) => page.visible !== false);
  const [selected, setSelected] = useState('');
  const activeId = available.some((page) => page.id === selected) ?
    selected : available[0]?.id;
  const active = available.find((page) => page.id === activeId);
  if(!active) return <EmptyState message={emptyMessage} />;
  return <Box component="section" aria-label={label}
    sx={{height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0}}>
    <Box role="tablist" aria-label={label + ' pages'}
      sx={{display: 'flex', minHeight: 'var(--cde-tab-height)',
        overflowX: 'auto', borderBottom: '1px solid', borderColor: 'divider'}}>
      {available.map((page) => <Button key={page.id} role="tab"
        aria-selected={page.id === activeId} onClick={() => setSelected(page.id)}
        sx={{borderBottom: page.id === activeId ? '2px solid' : '2px solid transparent',
          borderColor: page.id === activeId ? 'primary.main' : 'transparent'}}>
        {page.label}
      </Button>)}
    </Box>
    <Box role="tabpanel" aria-label={active.label}
      sx={{flex: 1, minHeight: 0, overflow: 'auto'}}>{active.content}</Box>
  </Box>;
}

TabbedHost.propTypes = {
  label: PropTypes.string.isRequired,
  pages: PropTypes.array.isRequired,
  emptyMessage: PropTypes.string.isRequired,
};

export function InspectorHost({corePages=[], contextService=workbenchContextService,
  registry=inspectorRegistry, toolbox=toolboxRegistry}) {
  const context = useContext(contextService);
  const pages = useMemo(() => [
    ...corePages,
    ...registry.resolve(context).map((contribution) => ({
      id: contribution.id, label: contribution.label,
      content: <InspectorSection title={contribution.label}>
        <ContributionValue value={contribution.render?.(context) ??
          contribution.value?.(context) ?? contribution.value} />
      </InspectorSection>,
    })),
    ...toolbox.resolve(context).map((contribution) => ({
      id: contribution.id, label: contribution.label,
      content: <Box aria-label={contribution.label}>
        {(typeof contribution.items === 'function' ?
          contribution.items(context) : contribution.items ?? []).map((item) => {
          const value = typeof item === 'string' ? {id: item, label: item} : item;
          const canInsert = typeof context.insertToolboxItem === 'function';
          return <ToolboxItem key={value.id} label={value.label}
            payload={value.payload ?? {type: value.id}}
            disabled={!canInsert || value.disabled}
            onInsert={(payload) => context.insertToolboxItem(payload)} />;
        })}
      </Box>,
    })),
  ], [context, corePages, registry, toolbox]);
  return <TabbedHost label="Inspector and Toolbox" pages={pages}
    emptyMessage="Select a live resource or authored asset to inspect it." />;
}

InspectorHost.propTypes = {
  corePages: PropTypes.array,
  contextService: PropTypes.object,
  registry: PropTypes.object,
  toolbox: PropTypes.object,
};

function ProblemsPage({diagnostics}) {
  if(!diagnostics.length) return <EmptyState message="No problems are reported." />;
  return <Box component="ul" sx={{m: 0, p: 1, pl: 3}}>
    {diagnostics.map((item) => <li key={item.id}>
      <Badge status={item.severity === 'error' || item.severity === 'critical' ?
        'error' : item.severity === 'warning' ? 'warning' : 'info'}
      label={item.severity} /> <SafeText text={item.message} />
    </li>)}
  </Box>;
}

ProblemsPage.propTypes = {diagnostics: PropTypes.array.isRequired};

function TasksPage({tasks}) {
  if(!tasks.length) return <EmptyState message="No tasks have been submitted." />;
  return <Box component="ul" sx={{m: 0, p: 1, pl: 3}}>
    {tasks.map((task) => <li key={task.id}>
      <StatusDot status={['completed', 'succeeded', 'succeeded_with_warnings']
        .includes(task.state) ? 'success' :
        task.state === 'failed' || task.state === 'cancelled' ? 'error' : 'warning'}
      label={task.state} /> <SafeText text={task.label + ' — ' +
        (task.message || Math.round(task.progress * 100) + '%')} />
    </li>)}
  </Box>;
}

TasksPage.propTypes = {tasks: PropTypes.array.isRequired};

export function BottomDrawerHost({corePages=[], diagnostics=diagnosticsService,
  tasks: taskService, contextService=workbenchContextService,
  registry=bottomRegistry}) {
  const [, setRevision] = useState(0);
  const context = useContext(contextService);
  useEffect(() => {
    const removers = [];
    if(typeof diagnostics.subscribe === 'function') {
      removers.push(diagnostics.subscribe(() => setRevision((value) => value + 1)));
    }
    if(typeof taskService?.subscribe === 'function') {
      removers.push(taskService.subscribe(() => setRevision((value) => value + 1)));
    }
    return () => removers.forEach((remove) => remove());
  }, [diagnostics, taskService]);
  const pages = [
    {id: 'problems', label: 'Problems',
      content: <ProblemsPage diagnostics={diagnostics.list()} />},
    ...corePages,
    ...registry.resolve(context).map((contribution) => ({
      id: contribution.id, label: contribution.label,
      content: <ContributionValue value={contribution.render?.(context) ??
        contribution.value?.(context) ?? contribution.value} />,
    })),
    {id: 'tasks', label: 'Tasks',
      content: <TasksPage tasks={taskService?.list?.() ?? []} />},
  ];
  return <TabbedHost label="Bottom tools" pages={pages}
    emptyMessage="No bottom tool pages are registered." />;
}

BottomDrawerHost.propTypes = {
  corePages: PropTypes.array,
  diagnostics: PropTypes.object,
  tasks: PropTypes.object,
  contextService: PropTypes.object,
  registry: PropTypes.object,
};

export function ActiveStatusHost({contextService=workbenchContextService,
  registry=statusRegistry}) {
  const context = useContext(contextService);
  const contributions = registry.resolve(context);
  return <Box role="status" aria-label="Active workbench status"
    sx={{display: 'flex', alignItems: 'center', gap: 1,
      minWidth: 0, width: '100%'}}>
    <SafeText text={context.surfaceTitle || 'CDEadmin workbench'} />
    {context.projectId && <Badge label={'Project: ' + context.projectId} />}
    {context.connectionState && <Badge status={context.connectionState === 'connected' ?
      'success' : context.connectionState === 'disconnected' ? 'error' : 'warning'}
    label={context.connectionState} />}
    {context.transactionState && context.transactionState !== 'none' &&
      <Badge status="warning" label={'Transaction: ' + context.transactionState} />}
    {contributions.map((contribution) => <Badge key={contribution.id}
      label={contribution.label + ': ' +
        (contribution.value?.(context) ?? contribution.value ?? '')} />)}
    <Box sx={{ml: 'auto'}}><SafeText text={context.message ?? ''} /></Box>
  </Box>;
}

ActiveStatusHost.propTypes = {
  contextService: PropTypes.object,
  registry: PropTypes.object,
};
