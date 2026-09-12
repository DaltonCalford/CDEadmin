/////////////////////////////////////////////////////////////
// Complete 26-screen workbench composition for the AI Interface module.
/////////////////////////////////////////////////////////////

import {useEffect, useMemo, useRef, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import {Button, IconButton} from '../../primitives/Button';
import {SearchField} from '../../primitives/AdvancedControls';
import {Pagination, TreeRow} from '../../navigation/AdvancedNavigation';
import {Drawer, InspectorSection, StatusBar, Toolbar} from
  '../../layout/WorkbenchChrome';
import {Badge, Banner, ProgressBar, Skeleton, StatusDot} from
  '../../feedback/Indicators';
import {EmptyState} from '../../feedback/EmptyState';
import {Dialog} from '../../overlays/Dialog';
import DataGrid from '../../data/DataGrid';
import AIContractForm from './AIContractForm';
import {AI_FORM_BY_ID, AI_SCREEN_BY_ID,
  AI_SCREEN_FORM_BINDINGS, AI_SCREEN_GROUPS, shortAIScreenId} from
  './AIInterfaceContracts';

const DEFAULT_SCREEN = 'cdeadmin.ai_interface.ai_workbench';
const EMPTY_OBJECT = Object.freeze({});
const EMPTY_LIST = Object.freeze([]);
const SECRET_FIELD = /(?:password|passwd|secret|private.?key|credential|access.?token|api.?key)/i;

function safeDisplayData(value, key='') {
  if(SECRET_FIELD.test(key) && !/(?:credential|secret)(?:Ref|_ref)$/i.test(key)) {
    return '[protected]';
  }
  if(Array.isArray(value)) return value.map((item) => safeDisplayData(item));
  if(value && typeof value === 'object') return Object.fromEntries(
    Object.entries(value).map(([childKey, child]) =>
      [childKey, safeDisplayData(child, childKey)]));
  return value;
}

function screenTitle(screen) {
  return shortAIScreenId(screen.screen_id).split('_').map((part) =>
    part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

function commandLabel(command) {
  return command.replace(/^ai\./, '').split('.').flatMap((part) =>
    part.split('_')).map((part) => part.charAt(0).toUpperCase() +
    part.slice(1)).join(' ');
}

function normalizedRows(rows) {
  return rows.map((row, index) => {
    const source = safeDisplayData(
      row && typeof row === 'object' ? row : {value: row});
    return Object.fromEntries([...Object.entries(source).map(([key, value]) =>
      [key, value && typeof value === 'object' ? JSON.stringify(value) : value]),
    ['__rowKey', source.id ?? index]]);
  });
}

function screenColumns(rows, configured=[]) {
  if(configured.length) return configured;
  const keys = [...new Set(rows.flatMap((row) => Object.keys(row)))]
    .filter((key) => key !== '__rowKey');
  return (keys.length ? keys : ['value']).map((key) => ({key, name:
    key.replaceAll('_', ' ').replace(/^./, (value) => value.toUpperCase())}));
}

function ScreenState({screen, state, error, hasData, onPrimaryAction}) {
  if(state === 'loading') return <Box sx={{p: 2}}><Skeleton lines={8}
    label={`Loading ${screenTitle(screen)}`} /></Box>;
  if(state === 'permission') return <Banner status="error">
    {screen.states.permission}</Banner>;
  if(state === 'error') return <Banner status="error">
    {error || screen.states.error}</Banner>;
  if(state === 'disconnected' || state === 'stale') return <Banner status="warning">
    {screen.states.disconnected_or_stale}</Banner>;
  if(state === 'partial') return <Banner status="warning">
    Partial authoritative results are visible. Missing results are not inferred.</Banner>;
  if(state === 'background') return <ProgressBar
    label={`${screenTitle(screen)} background task`} status="indeterminate" />;
  if(state === 'invalid') return <Banner status="warning">
    Validation is incomplete. Authored content is preserved for correction.</Banner>;
  if((state === 'empty' || !hasData) && !AI_SCREEN_FORM_BINDINGS[screen.screen_id].length) {
    return <EmptyState message={screen.states.empty}
      actionLabel={screen.entry_commands[0] ? commandLabel(screen.entry_commands[0]) : undefined}
      onAction={onPrimaryAction} />;
  }
  return null;
}

ScreenState.propTypes = {
  screen: PropTypes.object.isRequired,
  state: PropTypes.string,
  error: PropTypes.string,
  hasData: PropTypes.bool,
  onPrimaryAction: PropTypes.func,
};

function ScreenData({screen, rows: rawRows, columns}) {
  const rows = normalizedRows(rawRows);
  if(!rows.length) return null;
  return <Box component="section" aria-label={`${screenTitle(screen)} records`}
    sx={{height: 260, minHeight: 160, borderTop: '1px solid',
      borderColor: 'divider'}}>
    <DataGrid gridId={`ai-interface/screen/${shortAIScreenId(screen.screen_id)}`}
      aria-label={`${screenTitle(screen)} records`} rows={rows}
      columns={screenColumns(rows, columns)} readOnly enableRowSelect
      rowKeyGetter={(row) => row.__rowKey} />
  </Box>;
}

ScreenData.propTypes = {
  screen: PropTypes.object.isRequired,
  rows: PropTypes.array,
  columns: PropTypes.array,
};

function ScreenNavigator({activeScreenId, onNavigate}) {
  return <Box component="nav" aria-label="AI Interface screens"
    sx={{height: '100%', overflow: 'auto', borderRight: '1px solid',
      borderColor: 'divider'}}>
    <Box role="tree" aria-label="AI Interface screen tree">
      {AI_SCREEN_GROUPS.map((group) => <Box key={group.id} role="group"
        aria-label={group.label}>
        <Box component="h2" sx={{m: 0, px: 1, minHeight: 30,
          display: 'flex', alignItems: 'center', fontSize: '0.75rem',
          color: 'text.secondary'}}>{group.label}</Box>
        {group.screens.map((shortId) => {
          const id = `cdeadmin.ai_interface.${shortId}`;
          return <TreeRow key={id} level={2} label={screenTitle(AI_SCREEN_BY_ID[id])}
            selected={activeScreenId === id} onSelect={() => onNavigate(id)}
            onOpen={() => onNavigate(id)} />;
        })}
      </Box>)}
    </Box>
  </Box>;
}

ScreenNavigator.propTypes = {
  activeScreenId: PropTypes.string.isRequired,
  onNavigate: PropTypes.func.isRequired,
};

function displayValue(value) {
  if(value === null || value === undefined || value === '') return null;
  if(typeof value === 'string' || typeof value === 'number' ||
      typeof value === 'boolean') return String(value);
  return JSON.stringify(safeDisplayData(value), null, 2);
}

function ScreenInspector({screen, data}) {
  return <Box component="aside" aria-label={`${screenTitle(screen)} inspector`}
    sx={{height: '100%', overflow: 'auto', borderLeft: '1px solid',
      borderColor: 'divider'}}>
    {(screen.inspector_sections || []).map((section) => {
      const value = data[section] ?? data[section.toLowerCase().replaceAll(' ', '_')];
      return <InspectorSection key={section} title={section}>
        {displayValue(value) ? <Box component="pre" sx={{m: 0,
          whiteSpace: 'pre-wrap', overflowWrap: 'anywhere'}}>{displayValue(value)}</Box> :
          <EmptyState message={`No ${section.toLowerCase()} is selected.`} />}
      </InspectorSection>;
    })}
    <InspectorSection title="Contract bindings">
      {Object.entries(screen.bindings).map(([kind, values]) => <Box key={kind}
        sx={{mb: 1}}><Box component="strong">{kind}</Box>
        <Box>{values.join(', ') || 'None'}</Box></Box>)}
    </InspectorSection>
  </Box>;
}

ScreenInspector.propTypes = {screen: PropTypes.object.isRequired, data: PropTypes.object};

function DrawerContent({page, data}) {
  const value = data[page] ?? data[page.toLowerCase().replaceAll(' ', '_')];
  if(Array.isArray(value) && value.length) {
    const rows = normalizedRows(value);
    return <Box sx={{height: '100%', minHeight: 120}}><DataGrid
      gridId={`ai-interface/drawer/${page}`} aria-label={`${page} records`}
      rows={rows} columns={screenColumns(rows)} readOnly
      rowKeyGetter={(row) => row.__rowKey} /></Box>;
  }
  if(displayValue(value)) return <Box component="pre" sx={{m: 0,
    whiteSpace: 'pre-wrap', overflowWrap: 'anywhere'}}>{displayValue(value)}</Box>;
  return <EmptyState message={`No ${page.toLowerCase()} is available.`} />;
}

DrawerContent.propTypes = {page: PropTypes.string, data: PropTypes.object};

export function AIInterfaceWorkspace({screenId=DEFAULT_SCREEN,
  screenState='default', error='', data=EMPTY_LIST, columns=EMPTY_LIST,
  initialValues=EMPTY_OBJECT, formContext=EMPTY_OBJECT,
  fieldOptions=EMPTY_OBJECT, gridColumns=EMPTY_OBJECT, resources=EMPTY_LIST,
  assets=EMPTY_LIST, connections=EMPTY_LIST, commandAvailability=EMPTY_OBJECT,
  inspectorData=EMPTY_OBJECT, drawerData=EMPTY_OBJECT, pageSize=50,
  executeCommand, onNavigate, onDetach, onFormAction, onFormValuesChange}) {
  const [activeScreenId, setActiveScreenId] = useState(screenId);
  const [activeFormId, setActiveFormId] = useState('');
  const [drawerPage, setDrawerPage] = useState('');
  const [drawerHeight, setDrawerHeight] = useState(220);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const navigatorRef = useRef(null);
  const workbenchRef = useRef(null);
  const inspectorRef = useRef(null);
  const drawerRef = useRef(null);
  useEffect(() => setActiveScreenId(screenId), [screenId]);
  const screen = AI_SCREEN_BY_ID[activeScreenId] || AI_SCREEN_BY_ID[DEFAULT_SCREEN];
  const formIds = AI_SCREEN_FORM_BINDINGS[screen.screen_id];
  useEffect(() => setActiveFormId(formIds[0] || ''), [screen.screen_id]);
  useEffect(() => setDrawerPage(screen.bottom_drawer_pages?.[0] || ''),
    [screen.screen_id]);
  useEffect(() => { setQuery(''); setPage(1); }, [screen.screen_id]);
  const form = AI_FORM_BY_ID[activeFormId || formIds[0]];
  const rows = Array.isArray(data) ? data :
    (data[screen.screen_id] || data[shortAIScreenId(screen.screen_id)] || []);
  const filteredRows = query ? rows.filter((row) => JSON.stringify(row)
    .toLowerCase().includes(query.toLowerCase())) : rows;
  const visibleRows = screen.components.includes('Pagination') ?
    filteredRows.slice((page - 1) * pageSize, page * pageSize) : filteredRows;
  const screenColumnsValue = Array.isArray(columns) ? columns :
    (columns[screen.screen_id] || columns[shortAIScreenId(screen.screen_id)] || []);
  const unavailable = (command) => typeof executeCommand !== 'function' ||
    screenState === 'permission' || screenState === 'loading' ||
    commandAvailability[command] === false;
  const invoke = (command, args={}) => {
    if(!command || unavailable(command)) return Promise.resolve(undefined);
    return Promise.resolve(executeCommand?.(command, {
      screenId: screen.screen_id, ...args}));
  };
  const navigate = (next) => {
    setActiveScreenId(next); onNavigate?.(next);
  };
  const hasRegion = (region) => screen.host_regions.includes(region);
  const formInitialValues = initialValues[form?.form_id] || initialValues;
  const context = useMemo(() => ({...formContext,
    actionAvailability: {...commandAvailability,
      ...(formContext.actionAvailability || {})}}),
  [commandAvailability, formContext]);
  return <Box data-testid="ai-interface-workspace" data-ai-screen={screen.screen_id}
    onKeyDown={(event) => {
      if(!(event.ctrlKey || event.metaKey)) return;
      const target = {'1': navigatorRef, '2': workbenchRef, '3': inspectorRef,
        '4': drawerRef}[event.key];
      if(target?.current) { event.preventDefault(); target.current.focus(); }
    }}
    sx={{height: '100%', minHeight: 360, display: 'flex', flexDirection: 'column',
      bgcolor: 'background.paper'}}>
    <Toolbar label={`${screenTitle(screen)} toolbar`} trailing={
      <Box sx={{display: 'flex', alignItems: 'center', gap: 0.5}}>
        <Badge label={screen.primary_surface_type.replaceAll('_', ' ')} />
        {onDetach && <IconButton label={`Detach ${screenTitle(screen)}`}
          onClick={() => onDetach(screen.screen_id)}><OpenInNewIcon /></IconButton>}
      </Box>}>
      <Box component="strong" sx={{mr: 1}}>{screenTitle(screen)}</Box>
      {screen.components.includes('StatusDot') && <StatusDot
        status={screenState === 'default' ? 'success' :
          screenState === 'error' ? 'error' : 'warning'}
        label={`${screenTitle(screen)} ${screenState}`} />}
      {screen.toolbar_commands.map((command) => <Button key={command}
        disabled={unavailable(command)} onClick={() => invoke(command)}>
        {commandLabel(command)}</Button>)}
    </Toolbar>
    <Box sx={{display: 'grid', flex: 1, minHeight: 0,
      gridTemplateColumns: hasRegion('navigator') ?
        'minmax(208px, 18%) minmax(0, 1fr) minmax(240px, 22%)' :
        'minmax(0, 1fr) minmax(240px, 22%)',
      '@media (max-width: 900px)': {display: 'flex', flexDirection: 'column'}}}>
      {hasRegion('navigator') && <Box ref={navigatorRef} tabIndex={-1}
        sx={{minWidth: 0, minHeight: 0,
          '@media (max-width: 900px)': {height: 180}}}>
        <ScreenNavigator activeScreenId={screen.screen_id} onNavigate={navigate} />
      </Box>}
      <Box component="main" ref={workbenchRef} tabIndex={-1}
        aria-label={`${screenTitle(screen)} workbench`}
        sx={{minWidth: 0, minHeight: 0, overflow: 'hidden', display: 'flex',
          flexDirection: 'column'}}>
        <Box sx={{p: 1, borderBottom: '1px solid', borderColor: 'divider'}}>
          <Box component="p" sx={{m: 0}}>{screen.purpose}</Box>
        </Box>
        {screen.components.includes('SearchField') && <Box sx={{p: 1}}>
          <SearchField label={`Search ${screenTitle(screen)}`} value={query}
            onChange={(next) => { setQuery(next); setPage(1); }}
            resultCount={filteredRows.length} /></Box>}
        {screen.components.includes('ProgressBar') && <Box sx={{px: 1}}>
          <ProgressBar label={`${screenTitle(screen)} progress`}
            value={Number.isFinite(rows[0]?.progress) ? rows[0].progress : undefined}
            status={screenState === 'error' ? 'error' : 'indeterminate'} /></Box>}
        {formIds.length > 1 && <Box role="tablist" aria-label="Available task forms"
          sx={{display: 'flex', overflowX: 'auto', borderBottom: '1px solid',
            borderColor: 'divider'}}>
          {formIds.map((formId) => <Button key={formId} role="tab"
            aria-selected={form?.form_id === formId}
            onClick={() => setActiveFormId(formId)}>{AI_FORM_BY_ID[formId].title}</Button>)}
        </Box>}
        <ScreenState screen={screen} state={screenState} error={error}
          hasData={rows.length > 0 || Boolean(form)}
          onPrimaryAction={() => invoke(screen.entry_commands[0])} />
        <Box sx={{flex: 1, minHeight: 0, overflow: 'hidden'}}>
          {form && screenState !== 'loading' && <AIContractForm key={form.form_id}
            form={form} initialValues={formInitialValues} state={screenState}
            error={error} context={context} fieldOptions={fieldOptions}
            gridColumns={gridColumns} resources={resources} assets={assets}
            connections={connections} executeCommand={executeCommand}
            onAction={onFormAction} onValuesChange={onFormValuesChange} />}
          {!form && !rows.length && screenState === 'default' && <EmptyState
            message={screen.states.empty}
            actionLabel={screen.entry_commands[0] ?
              commandLabel(screen.entry_commands[0]) : undefined}
            onAction={() => invoke(screen.entry_commands[0])} />}
        </Box>
        <ScreenData screen={screen} rows={visibleRows} columns={screenColumnsValue} />
        {screen.components.includes('Pagination') && <Box sx={{px: 1}}>
          <Pagination page={page} pageSize={pageSize} count={filteredRows.length}
            loading={screenState === 'loading'} onChange={setPage}
            label={`${screenTitle(screen)} result pages`} /></Box>}
      </Box>
      {hasRegion('inspector') && <Box ref={inspectorRef} tabIndex={-1}
        sx={{minWidth: 0, minHeight: 0,
          '@media (max-width: 900px)': {height: 180}}}>
        <ScreenInspector screen={screen} data={inspectorData} />
      </Box>}
    </Box>
    {hasRegion('bottom_drawer') && screen.bottom_drawer_pages?.length > 0 &&
      <Box ref={drawerRef} tabIndex={-1}>
        <Drawer label={`${screenTitle(screen)} bottom drawer`} height={drawerHeight}
          onHeightChange={setDrawerHeight}>
          <Box role="tablist" aria-label="Bottom drawer pages"
            sx={{display: 'flex', borderBottom: '1px solid', borderColor: 'divider'}}>
            {screen.bottom_drawer_pages.map((page) => <Button key={page} role="tab"
              aria-selected={drawerPage === page}
              onClick={() => setDrawerPage(page)}>{page}</Button>)}
          </Box>
          <Box role="tabpanel" sx={{p: 1, overflow: 'auto'}}>
            <DrawerContent page={drawerPage} data={drawerData} />
          </Box>
        </Drawer>
      </Box>}
    {hasRegion('status_bar') && <StatusBar status={screenState === 'disconnected' ?
      'disconnected' : ['stale', 'partial', 'invalid'].includes(screenState) ?
        'warning' : 'normal'}>
      {(screen.status_bar_contributions || []).map((item) =>
        <Badge key={item} label={item} />)}
      <Box component="span">{screenState}</Box>
    </StatusBar>}
  </Box>;
}

AIInterfaceWorkspace.propTypes = {
  screenId: PropTypes.string,
  screenState: PropTypes.oneOf(['default', 'loading', 'empty', 'error',
    'permission', 'disconnected', 'stale', 'partial', 'invalid', 'background']),
  error: PropTypes.string,
  data: PropTypes.oneOfType([PropTypes.array, PropTypes.object]),
  columns: PropTypes.oneOfType([PropTypes.array, PropTypes.object]),
  initialValues: PropTypes.object,
  formContext: PropTypes.object,
  fieldOptions: PropTypes.object,
  gridColumns: PropTypes.object,
  resources: PropTypes.array,
  assets: PropTypes.array,
  connections: PropTypes.array,
  commandAvailability: PropTypes.object,
  inspectorData: PropTypes.object,
  drawerData: PropTypes.object,
  pageSize: PropTypes.number,
  executeCommand: PropTypes.func,
  onNavigate: PropTypes.func,
  onDetach: PropTypes.func,
  onFormAction: PropTypes.func,
  onFormValuesChange: PropTypes.func,
};

export {DEFAULT_SCREEN, screenTitle};

export function AIInterfaceSurface({screenId=DEFAULT_SCREEN, open=true,
  onClose, screenState='default', error='', initialValues=EMPTY_OBJECT,
  formContext=EMPTY_OBJECT, fieldOptions=EMPTY_OBJECT, gridColumns=EMPTY_OBJECT,
  resources=EMPTY_LIST, assets=EMPTY_LIST, connections=EMPTY_LIST,
  executeCommand, onFormAction, onFormValuesChange, ...workspaceProps}) {
  const screen = AI_SCREEN_BY_ID[screenId] || AI_SCREEN_BY_ID[DEFAULT_SCREEN];
  if(screen.primary_surface_type !== 'dialog') {
    return <AIInterfaceWorkspace {...workspaceProps} screenId={screen.screen_id}
      screenState={screenState} error={error} initialValues={initialValues}
      formContext={formContext} fieldOptions={fieldOptions} gridColumns={gridColumns}
      resources={resources} assets={assets} connections={connections}
      executeCommand={executeCommand} onFormAction={onFormAction}
      onFormValuesChange={onFormValuesChange} />;
  }
  const form = AI_FORM_BY_ID[AI_SCREEN_FORM_BINDINGS[screen.screen_id][0]];
  return <Dialog open={open} title={screenTitle(screen)} size="medium"
    onClose={onClose}>
    <Box sx={{height: 'min(720px, calc(100vh - 160px))', minHeight: 360}}>
      <AIContractForm form={form}
        initialValues={initialValues[form.form_id] || initialValues}
        state={screenState} error={error} context={formContext}
        fieldOptions={fieldOptions} gridColumns={gridColumns}
        resources={resources} assets={assets} connections={connections}
        executeCommand={executeCommand} onAction={onFormAction}
        onValuesChange={onFormValuesChange} onClose={onClose} />
    </Box>
  </Dialog>;
}

AIInterfaceSurface.propTypes = {
  screenId: PropTypes.string,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  screenState: AIInterfaceWorkspace.propTypes.screenState,
  error: PropTypes.string,
  initialValues: PropTypes.object,
  formContext: PropTypes.object,
  fieldOptions: PropTypes.object,
  gridColumns: PropTypes.object,
  resources: PropTypes.array,
  assets: PropTypes.array,
  connections: PropTypes.array,
  executeCommand: PropTypes.func,
  onFormAction: PropTypes.func,
  onFormValuesChange: PropTypes.func,
};

export default AIInterfaceWorkspace;
