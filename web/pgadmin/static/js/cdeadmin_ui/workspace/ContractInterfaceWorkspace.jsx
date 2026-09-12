/////////////////////////////////////////////////////////////
// Contract-driven workbench composition shared by governed modules.
/////////////////////////////////////////////////////////////

import {useEffect, useMemo, useRef, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import {Button, IconButton} from '../primitives/Button';
import {SearchField} from '../primitives/AdvancedControls';
import {ListRow, Pagination, TreeRow} from '../navigation/AdvancedNavigation';
import {Tab} from '../navigation/TabsAndBreadcrumbs';
import {Drawer, InspectorSection, StatusBar, Toolbar} from
  '../layout/WorkbenchChrome';
import {Badge, Banner, ProgressBar, Skeleton, StatusDot} from
  '../feedback/Indicators';
import {EmptyState} from '../feedback/EmptyState';
import {Dialog} from '../overlays/Dialog';
import DataGrid from '../data/DataGrid';
import ContractForm from '../foundations/ContractForm';

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

function shortScreenId(screenId, moduleId) {
  return String(screenId).replace(`${moduleId}.`, '');
}

function screenTitle(screen, moduleId) {
  return shortScreenId(screen.screen_id, moduleId).split('_').map((part) =>
    part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

function commandLabel(command) {
  return command.replace(/^(?:ai|discovery)\./, '').split('.').flatMap((part) =>
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

function ScreenState({screen, state, error, hasData, onPrimaryAction,
  formBindings, moduleId}) {
  if(state === 'loading') return <Box sx={{p: 2}}><Skeleton lines={8}
    label={`Loading ${screenTitle(screen, moduleId)}`} /></Box>;
  if(state === 'permission') return <Banner status="error">
    {screen.states.permission}</Banner>;
  if(state === 'error') return <Banner status="error">
    {error || screen.states.error}</Banner>;
  if(state === 'disconnected' || state === 'stale') return <Banner status="warning">
    {screen.states.disconnected_or_stale}</Banner>;
  if(state === 'partial') return <Banner status="warning">
    Partial authoritative results are visible. Missing results are not inferred.</Banner>;
  if(state === 'background') return <ProgressBar
    label={`${screenTitle(screen, moduleId)} background task`} status="indeterminate" />;
  if(state === 'invalid') return <Banner status="warning">
    Validation is incomplete. Authored content is preserved for correction.</Banner>;
  if((state === 'empty' || !hasData) && !formBindings[screen.screen_id].length) {
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
  formBindings: PropTypes.object.isRequired,
  moduleId: PropTypes.string.isRequired,
};

function ScreenData({screen, rows: rawRows, columns, namespace, moduleId,
  onRowSelect, onRowOpen}) {
  const rows = normalizedRows(rawRows);
  if(!rows.length) return null;
  if(screen.components.includes('ListRow')) return <Box role="listbox"
    aria-label={`${screenTitle(screen, moduleId)} records`}
    sx={{overflow: 'auto', minHeight: 120}}>{rows.map((row) => <ListRow
      key={row.__rowKey} label={row.name ?? row.label ?? row.id ?? row.value}
      trailing={row.provider ?? row.type} status={row.status === 'error' ?
        'error' : row.status === 'warning' ? 'warning' : 'default'}
      onSelect={() => onRowSelect?.(row)} onOpen={() => onRowOpen?.(row)} />)}
  </Box>;
  return <Box component="section" aria-label={`${screenTitle(screen, moduleId)} records`}
    sx={{height: 260, minHeight: 160, borderTop: '1px solid',
      borderColor: 'divider'}}>
    <DataGrid gridId={`${namespace}-interface/screen/${shortScreenId(screen.screen_id, moduleId)}`}
      aria-label={`${screenTitle(screen, moduleId)} records`} rows={rows}
      columns={screenColumns(rows, columns)} readOnly enableRowSelect
      rowKeyGetter={(row) => row.__rowKey} />
  </Box>;
}

ScreenData.propTypes = {
  screen: PropTypes.object.isRequired,
  rows: PropTypes.array,
  columns: PropTypes.array,
  namespace: PropTypes.string.isRequired,
  moduleId: PropTypes.string.isRequired,
  onRowSelect: PropTypes.func,
  onRowOpen: PropTypes.func,
};

function ScreenNavigator({activeScreenId, onNavigate, groups, screens,
  moduleId, moduleLabel}) {
  return <Box component="nav" aria-label={`${moduleLabel} screens`}
    sx={{height: '100%', overflow: 'auto', borderRight: '1px solid',
      borderColor: 'divider'}}>
    <Box role="tree" aria-label={`${moduleLabel} screen tree`}>
      {groups.map((group) => <Box key={group.id} role="group"
        aria-label={group.label}>
        <Box component="h2" sx={{m: 0, px: 1, minHeight: 30,
          display: 'flex', alignItems: 'center', fontSize: '0.75rem',
          color: 'text.secondary'}}>{group.label}</Box>
        {group.screens.map((shortId) => {
          const id = `${moduleId}.${shortId}`;
          return <TreeRow key={id} level={2} label={screenTitle(screens[id], moduleId)}
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
  groups: PropTypes.array.isRequired,
  screens: PropTypes.object.isRequired,
  moduleId: PropTypes.string.isRequired,
  moduleLabel: PropTypes.string.isRequired,
};

function displayValue(value) {
  if(value === null || value === undefined || value === '') return null;
  if(typeof value === 'string' || typeof value === 'number' ||
      typeof value === 'boolean') return String(value);
  return JSON.stringify(safeDisplayData(value), null, 2);
}

function ScreenInspector({screen, data, moduleId}) {
  return <Box component="aside" aria-label={`${screenTitle(screen, moduleId)} inspector`}
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

ScreenInspector.propTypes = {screen: PropTypes.object.isRequired, data: PropTypes.object,
  moduleId: PropTypes.string.isRequired};

function DrawerContent({page, data, namespace}) {
  const value = data[page] ?? data[page.toLowerCase().replaceAll(' ', '_')];
  if(Array.isArray(value) && value.length) {
    const rows = normalizedRows(value);
    return <Box sx={{height: '100%', minHeight: 120}}><DataGrid
      gridId={`${namespace}-interface/drawer/${page}`} aria-label={`${page} records`}
      rows={rows} columns={screenColumns(rows)} readOnly
      rowKeyGetter={(row) => row.__rowKey} /></Box>;
  }
  if(displayValue(value)) return <Box component="pre" sx={{m: 0,
    whiteSpace: 'pre-wrap', overflowWrap: 'anywhere'}}>{displayValue(value)}</Box>;
  return <EmptyState message={`No ${page.toLowerCase()} is available.`} />;
}

DrawerContent.propTypes = {page: PropTypes.string, data: PropTypes.object,
  namespace: PropTypes.string.isRequired};

export function ContractInterfaceWorkspace({screenId,
  screenState='default', error='', data=EMPTY_LIST, columns=EMPTY_LIST,
  initialValues=EMPTY_OBJECT, formContext=EMPTY_OBJECT,
  fieldOptions=EMPTY_OBJECT, gridColumns=EMPTY_OBJECT, resources=EMPTY_LIST,
  assets=EMPTY_LIST, connections=EMPTY_LIST, commandAvailability=EMPTY_OBJECT,
  inspectorData=EMPTY_OBJECT, drawerData=EMPTY_OBJECT, pageSize=50,
  executeCommand, onNavigate, onDetach, onFormAction, onFormValuesChange,
  onRowSelect, onRowOpen, screenTabs=EMPTY_OBJECT, onScreenTabChange,
  contractConfig}) {
  const {defaultScreenId, moduleId, moduleLabel, namespace, forms, screens,
    bindings, groups} = contractConfig;
  const [activeScreenId, setActiveScreenId] = useState(
    screenId || defaultScreenId);
  const [activeFormId, setActiveFormId] = useState('');
  const [drawerPage, setDrawerPage] = useState('');
  const [drawerHeight, setDrawerHeight] = useState(220);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [activeScreenTab, setActiveScreenTab] = useState('');
  const navigatorRef = useRef(null);
  const workbenchRef = useRef(null);
  const inspectorRef = useRef(null);
  const drawerRef = useRef(null);
  useEffect(() => setActiveScreenId(screenId || defaultScreenId),
    [defaultScreenId, screenId]);
  const screen = screens[activeScreenId] || screens[defaultScreenId];
  const formIds = bindings[screen.screen_id];
  useEffect(() => setActiveFormId(formIds[0] || ''), [screen.screen_id]);
  useEffect(() => setDrawerPage(screen.bottom_drawer_pages?.[0] || ''),
    [screen.screen_id]);
  useEffect(() => { setQuery(''); setPage(1); }, [screen.screen_id]);
  const tabs = Array.isArray(screenTabs) ? screenTabs :
    (screenTabs[screen.screen_id] || screenTabs[shortScreenId(
      screen.screen_id, moduleId)] || EMPTY_LIST);
  useEffect(() => setActiveScreenTab(tabs[0]?.id ?? tabs[0] ?? ''),
    [screen.screen_id, tabs]);
  const form = forms[activeFormId || formIds[0]];
  const rows = Array.isArray(data) ? data :
    (data[screen.screen_id] || data[shortScreenId(screen.screen_id, moduleId)] || []);
  const filteredRows = query ? rows.filter((row) => JSON.stringify(row)
    .toLowerCase().includes(query.toLowerCase())) : rows;
  const visibleRows = screen.components.includes('Pagination') ?
    filteredRows.slice((page - 1) * pageSize, page * pageSize) : filteredRows;
  const screenColumnsValue = Array.isArray(columns) ? columns :
    (columns[screen.screen_id] || columns[shortScreenId(screen.screen_id, moduleId)] || []);
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
  const screenDataAttribute = {[`data-${namespace}-screen`]: screen.screen_id};
  return <Box data-testid={`${namespace}-interface-workspace`} {...screenDataAttribute}
    onKeyDown={(event) => {
      if(!(event.ctrlKey || event.metaKey)) return;
      const target = {'1': navigatorRef, '2': workbenchRef, '3': inspectorRef,
        '4': drawerRef}[event.key];
      if(target?.current) { event.preventDefault(); target.current.focus(); }
    }}
    sx={{height: '100%', minHeight: 360, display: 'flex', flexDirection: 'column',
      bgcolor: 'background.paper'}}>
    <Toolbar label={`${screenTitle(screen, moduleId)} toolbar`} trailing={
      <Box sx={{display: 'flex', alignItems: 'center', gap: 0.5}}>
        <Badge label={screen.primary_surface_type.replaceAll('_', ' ')} />
        {onDetach && <IconButton label={`Detach ${screenTitle(screen, moduleId)}`}
          onClick={() => onDetach(screen.screen_id)}><OpenInNewIcon /></IconButton>}
      </Box>}>
      <Box component="strong" sx={{mr: 1}}>{screenTitle(screen, moduleId)}</Box>
      {screen.components.includes('StatusDot') && <StatusDot
        status={screenState === 'default' ? 'success' :
          screenState === 'error' ? 'error' : 'warning'}
        label={`${screenTitle(screen, moduleId)} ${screenState}`} />}
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
        <ScreenNavigator activeScreenId={screen.screen_id} onNavigate={navigate}
          groups={groups} screens={screens} moduleId={moduleId}
          moduleLabel={moduleLabel} />
      </Box>}
      <Box component="main" ref={workbenchRef} tabIndex={-1}
        aria-label={`${screenTitle(screen, moduleId)} workbench`}
        sx={{minWidth: 0, minHeight: 0, overflow: 'hidden', display: 'flex',
          flexDirection: 'column'}}>
        <Box sx={{p: 1, borderBottom: '1px solid', borderColor: 'divider'}}>
          <Box component="p" sx={{m: 0}}>{screen.purpose}</Box>
        </Box>
        {screen.components.includes('SearchField') && <Box sx={{p: 1}}>
          <SearchField label={`Search ${screenTitle(screen, moduleId)}`} value={query}
            onChange={(next) => { setQuery(next); setPage(1); }}
            resultCount={filteredRows.length} /></Box>}
        {screen.components.includes('ProgressBar') && <Box sx={{px: 1}}>
          <ProgressBar label={`${screenTitle(screen, moduleId)} progress`}
            value={Number.isFinite(rows[0]?.progress) ? rows[0].progress : undefined}
            status={screenState === 'error' ? 'error' : 'indeterminate'} /></Box>}
        {screen.components.includes('Tabs') && tabs.length > 0 &&
          <Box role="tablist" aria-label={`${screenTitle(screen, moduleId)} views`}
            sx={{display: 'flex', overflowX: 'auto', borderBottom: '1px solid',
              borderColor: 'divider'}}>{tabs.map((tab) => {
              const id = tab.id ?? tab; const label = tab.label ?? tab;
              return <Tab key={id} label={label} active={activeScreenTab === id}
                onActivate={() => { setActiveScreenTab(id);
                  onScreenTabChange?.(id, screen.screen_id); }} />;
            })}</Box>}
        {formIds.length > 1 && <Box role="tablist" aria-label="Available task forms"
          sx={{display: 'flex', overflowX: 'auto', borderBottom: '1px solid',
            borderColor: 'divider'}}>
          {formIds.map((formId) => <Button key={formId} role="tab"
            aria-selected={form?.form_id === formId}
            onClick={() => setActiveFormId(formId)}>{forms[formId].title}</Button>)}
        </Box>}
        <ScreenState screen={screen} state={screenState} error={error}
          hasData={rows.length > 0 || Boolean(form)}
          onPrimaryAction={() => invoke(screen.entry_commands[0])}
          formBindings={bindings} moduleId={moduleId} />
        <Box sx={{flex: 1, minHeight: 0, overflow: 'hidden'}}>
          {form && screenState !== 'loading' && <ContractForm key={form.form_id}
            form={form} initialValues={formInitialValues} state={screenState}
            error={error} context={context} fieldOptions={fieldOptions}
            gridColumns={gridColumns} resources={resources} assets={assets}
            connections={connections} executeCommand={executeCommand}
            onAction={onFormAction} onValuesChange={onFormValuesChange}
            namespace={namespace} formCatalog={forms} />}
          {!form && !rows.length && screenState === 'default' && <EmptyState
            message={screen.states.empty}
            actionLabel={screen.entry_commands[0] ?
              commandLabel(screen.entry_commands[0]) : undefined}
            onAction={() => invoke(screen.entry_commands[0])} />}
        </Box>
        <ScreenData screen={screen} rows={visibleRows} columns={screenColumnsValue}
          namespace={namespace} moduleId={moduleId} onRowSelect={onRowSelect}
          onRowOpen={onRowOpen} />
        {screen.components.includes('Pagination') && <Box sx={{px: 1}}>
          <Pagination page={page} pageSize={pageSize} count={filteredRows.length}
            loading={screenState === 'loading'} onChange={setPage}
            label={`${screenTitle(screen, moduleId)} result pages`} /></Box>}
      </Box>
      {hasRegion('inspector') && <Box ref={inspectorRef} tabIndex={-1}
        sx={{minWidth: 0, minHeight: 0,
          '@media (max-width: 900px)': {height: 180}}}>
        <ScreenInspector screen={screen} data={inspectorData} moduleId={moduleId} />
      </Box>}
    </Box>
    {hasRegion('bottom_drawer') && screen.bottom_drawer_pages?.length > 0 &&
      <Box ref={drawerRef} tabIndex={-1}>
        <Drawer label={`${screenTitle(screen, moduleId)} bottom drawer`} height={drawerHeight}
          onHeightChange={setDrawerHeight}>
          <Box role="tablist" aria-label="Bottom drawer pages"
            sx={{display: 'flex', borderBottom: '1px solid', borderColor: 'divider'}}>
            {screen.bottom_drawer_pages.map((page) => <Button key={page} role="tab"
              aria-selected={drawerPage === page}
              onClick={() => setDrawerPage(page)}>{page}</Button>)}
          </Box>
          <Box role="tabpanel" sx={{p: 1, overflow: 'auto'}}>
            <DrawerContent page={drawerPage} data={drawerData} namespace={namespace} />
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

ContractInterfaceWorkspace.propTypes = {
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
  onRowSelect: PropTypes.func,
  onRowOpen: PropTypes.func,
  screenTabs: PropTypes.oneOfType([PropTypes.array, PropTypes.object]),
  onScreenTabChange: PropTypes.func,
  contractConfig: PropTypes.object.isRequired,
};

export {screenTitle};

export function ContractInterfaceSurface({screenId, open=true,
  onClose, screenState='default', error='', initialValues=EMPTY_OBJECT,
  formContext=EMPTY_OBJECT, fieldOptions=EMPTY_OBJECT, gridColumns=EMPTY_OBJECT,
  resources=EMPTY_LIST, assets=EMPTY_LIST, connections=EMPTY_LIST,
  executeCommand, onFormAction, onFormValuesChange,
  contractConfig, ...workspaceProps}) {
  const {defaultScreenId, moduleId, namespace, forms, screens, bindings} =
    contractConfig;
  const screen = screens[screenId] || screens[defaultScreenId];
  if(screen.primary_surface_type !== 'dialog') {
    return <ContractInterfaceWorkspace {...workspaceProps} screenId={screen.screen_id}
      screenState={screenState} error={error} initialValues={initialValues}
      formContext={formContext} fieldOptions={fieldOptions} gridColumns={gridColumns}
      resources={resources} assets={assets} connections={connections}
      executeCommand={executeCommand} onFormAction={onFormAction}
      onFormValuesChange={onFormValuesChange} contractConfig={contractConfig} />;
  }
  const form = forms[bindings[screen.screen_id][0]];
  return <Dialog open={open} title={screenTitle(screen, moduleId)} size="medium"
    onClose={onClose}>
    <Box sx={{height: 'min(720px, calc(100vh - 160px))', minHeight: 360}}>
      <ContractForm form={form}
        initialValues={initialValues[form.form_id] || initialValues}
        state={screenState} error={error} context={formContext}
        fieldOptions={fieldOptions} gridColumns={gridColumns}
        resources={resources} assets={assets} connections={connections}
        executeCommand={executeCommand} onAction={onFormAction}
        onValuesChange={onFormValuesChange} onClose={onClose}
        namespace={namespace} formCatalog={forms} />
    </Box>
  </Dialog>;
}

ContractInterfaceSurface.propTypes = {
  screenId: PropTypes.string,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  screenState: ContractInterfaceWorkspace.propTypes.screenState,
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
  contractConfig: PropTypes.object.isRequired,
};

export default ContractInterfaceWorkspace;
