/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {useCallback, useEffect, useMemo, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {Icon} from '../icons';
import {IconButton} from '../primitives/Button';
import {Drawer, Splitter, StatusBar, Toolbar} from '../layout/WorkbenchChrome';

export const WORKBENCH_LAYOUT_SCHEMA = 'cdeadmin.workbench-layout.v1';
export const ACTIVITY_RAIL_WIDTH = 56;
export const DEFAULT_WORKBENCH_LAYOUT = Object.freeze({
  schema: WORKBENCH_LAYOUT_SCHEMA,
  navigationWidth: 288,
  inspectorWidth: 340,
  drawerHeight: 240,
  navigationVisible: true,
  inspectorVisible: true,
  drawerVisible: false,
  activeActivity: 'activity.data',
});

function bounded(value, minimum, maximum, fallback) {
  return Number.isFinite(value) ? Math.max(minimum, Math.min(maximum, value)) : fallback;
}

export function normalizeWorkbenchLayout(value={}) {
  return Object.freeze({
    ...DEFAULT_WORKBENCH_LAYOUT,
    navigationWidth: bounded(value.navigationWidth, 220, 480, 288),
    inspectorWidth: bounded(value.inspectorWidth, 280, 520, 340),
    drawerHeight: bounded(value.drawerHeight, 120, 720, 240),
    navigationVisible: value.navigationVisible !== false,
    inspectorVisible: value.inspectorVisible !== false,
    drawerVisible: value.drawerVisible === true,
    activeActivity: typeof value.activeActivity === 'string' ?
      value.activeActivity : 'activity.data',
  });
}

export class WorkbenchLayoutStore {
  constructor(storage=typeof window === 'undefined' ? null : window.localStorage,
    key='cdeadmin.workbench-layout.v1') {
    this.storage = storage;
    this.key = key;
  }

  load(fallback=DEFAULT_WORKBENCH_LAYOUT) {
    try {
      const value = JSON.parse(this.storage?.getItem(this.key) || '{}');
      return value.schema && value.schema !== WORKBENCH_LAYOUT_SCHEMA ?
        normalizeWorkbenchLayout(fallback) :
        normalizeWorkbenchLayout(value.schema ? value : fallback);
    } catch {
      return normalizeWorkbenchLayout(fallback);
    }
  }

  save(value) {
    const normalized = normalizeWorkbenchLayout(value);
    this.storage?.setItem(this.key, JSON.stringify(normalized));
    return normalized;
  }

  reset() {
    this.storage?.removeItem(this.key);
    return DEFAULT_WORKBENCH_LAYOUT;
  }
}

function ActivityRail({activities, active, onChange}) {
  return <Box component="nav" aria-label="Application activities"
    sx={{width: ACTIVITY_RAIL_WIDTH, flex: `0 0 ${ACTIVITY_RAIL_WIDTH}px`,
      borderRight: '1px solid', borderColor: 'divider',
      bgcolor: 'background.navigation', overflowY: 'auto', overflowX: 'hidden'}}>
    {activities.map((activity) => {
      const selected = activity.id === active;
      return <IconButton key={activity.id}
        data-cdeadmin-qa-key={`activity-${activity.id}`}
        data-selected={selected ? 'true' : 'false'}
        data-visual-scale={selected ? '1.15' : '1'}
        data-visual-brightness={selected ? '1' : '0.85'}
        label={activity.label} aria-current={selected ? 'page' : undefined}
        disabled={activity.disabled === true}
        onClick={() => {
          if(activity.disabled) return;
          const accepted = activity.onSelect?.();
          if(accepted === false) return;
          onChange(activity.id, activity.navigationVisible !== false);
        }}
        sx={{width: 48, height: 48, mx: '4px', my: '4px',
          transform: selected ? 'scale(1.15)' : 'scale(1)',
          filter: selected ? 'brightness(1)' : 'brightness(0.85)',
          transformOrigin: 'center', zIndex: selected ? 1 : 0,
          transition: 'transform 120ms ease, filter 120ms ease',
          '@media (prefers-reduced-motion: reduce)': {transition: 'none'},
          borderLeft: selected ? '3px solid' : '3px solid transparent',
          borderColor: selected ? 'primary.main' : 'transparent'}}>
        <Icon iconKey={activity.iconKey || 'command.default'} decorative size="20px" />
      </IconButton>;
    })}
  </Box>;
}

ActivityRail.propTypes = {
  activities: PropTypes.array.isRequired,
  active: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};

export function WorkbenchShell({activities, navigationViews, children,
  inspector, drawer, status, store: suppliedStore, initialLayout,
  onLayoutChange, navigationTitle, activeActivityOverride,
  inspectorTitle='Inspector',
  drawerTitle='Problems, Output, Tasks and Logs'}) {
  const store = useMemo(
    () => suppliedStore ?? new WorkbenchLayoutStore(), [suppliedStore]
  );
  const [layout, setLayout] = useState(() => store.load(initialLayout));
  const update = useCallback((changes) => setLayout((current) =>
    store.save({...current, ...changes})
  ), [store]);
  useEffect(() => onLayoutChange?.(layout), [layout, onLayoutChange]);
  useEffect(() => {
    const listener = (event) => {
      const detail = typeof event.detail === 'string' ? {
        activityId: event.detail, navigationVisible: true,
      } : event.detail;
      update({activeActivity: detail.activityId,
        navigationVisible: detail.navigationVisible !== false});
    };
    window.addEventListener('cdeadmin:show-activity', listener);
    return () => window.removeEventListener('cdeadmin:show-activity', listener);
  }, [update]);

  const knownActivities = activities.length ? activities : [{
    id: 'activity.data', label: 'Data Explorer', iconKey: 'object.database',
  }];
  const requestedActive = activeActivityOverride || layout.activeActivity;
  const active = knownActivities.some((item) => item.id === requestedActive) ?
    requestedActive : knownActivities[0].id;
  const activity = knownActivities.find((item) => item.id === active);
  const navigation = navigationViews[active] ?? activity?.render?.() ?? null;

  return <Box data-cdeadmin-shell="zero-grey" sx={{height: '100%', minHeight: 0,
    display: 'flex', bgcolor: 'background.default', color: 'text.primary'}}>
    <ActivityRail activities={knownActivities} active={active}
      onChange={(activeActivity, navigationVisible) => update({
        activeActivity, navigationVisible,
      })} />
    {layout.navigationVisible && <Box component="aside" aria-label={activity?.label}
      sx={{width: layout.navigationWidth, flex: `0 0 ${layout.navigationWidth}px`,
        minWidth: 0, display: 'flex', flexDirection: 'column',
        bgcolor: 'background.navigation'}}>
      <Toolbar label="Navigation controls" trailing={<IconButton
        label="Hide navigation" onClick={() => update({navigationVisible: false})}>×</IconButton>}>
        <Box component="strong">{navigationTitle || activity?.label}</Box>
      </Toolbar>
      <Box sx={{flex: 1, minHeight: 0}}>{navigation}</Box>
    </Box>}
    {layout.navigationVisible && <Splitter value={layout.navigationWidth}
      min={220} max={480} onChange={(navigationWidth) => update({navigationWidth})}
      label="Resize navigation" />}
    <Box component="main" aria-label="Main workbench"
      sx={{flex: 1, minWidth: 0, minHeight: 0, display: 'flex',
        flexDirection: 'column', bgcolor: 'background.workspace'}}>
      <Box sx={{flex: 1, minHeight: 0, position: 'relative'}}>{children}</Box>
      <Drawer open={layout.drawerVisible} label={drawerTitle}
        height={layout.drawerHeight}
        onHeightChange={(drawerHeight) => update({drawerHeight})}>
        {drawer}
      </Drawer>
      <StatusBar>{status}</StatusBar>
    </Box>
    {layout.inspectorVisible && <Splitter value={layout.inspectorWidth}
      min={280} max={520} onChange={(inspectorWidth) => update({inspectorWidth})}
      label="Resize Inspector" />}
    {layout.inspectorVisible && <Box component="aside" aria-label="Inspector"
      sx={{width: layout.inspectorWidth, flex: `0 0 ${layout.inspectorWidth}px`,
        minWidth: 0, display: 'flex', flexDirection: 'column',
        bgcolor: 'background.elevated'}}>
      <Toolbar label="Inspector controls" trailing={<IconButton
        label="Hide Inspector" onClick={() => update({inspectorVisible: false})}>×</IconButton>}>
        <Box component="strong">{inspectorTitle}</Box>
      </Toolbar>
      <Box sx={{flex: 1, minHeight: 0, overflow: 'auto'}}>{inspector}</Box>
    </Box>}
    {!layout.navigationVisible && <IconButton label="Show navigation"
      onClick={() => update({navigationVisible: true})}
      sx={{position: 'absolute', left: ACTIVITY_RAIL_WIDTH, top: 0,
        zIndex: 'popover'}}>☰</IconButton>}
    {!layout.inspectorVisible && <IconButton label="Show Inspector"
      onClick={() => update({inspectorVisible: true})}
      sx={{position: 'absolute', right: 0, top: 0, zIndex: 'popover'}}>ⓘ</IconButton>}
    <IconButton label={layout.drawerVisible ? 'Hide bottom drawer' : 'Show bottom drawer'}
      onClick={() => update({drawerVisible: !layout.drawerVisible})}
      sx={{position: 'absolute', right: layout.inspectorVisible ?
        layout.inspectorWidth + 8 : 8, bottom: 'var(--cde-status-height)',
      zIndex: 'popover'}}>▤</IconButton>
  </Box>;
}

WorkbenchShell.propTypes = {
  activities: PropTypes.array,
  navigationViews: PropTypes.object,
  children: PropTypes.node,
  inspector: PropTypes.node,
  drawer: PropTypes.node,
  status: PropTypes.node,
  store: PropTypes.object,
  initialLayout: PropTypes.object,
  onLayoutChange: PropTypes.func,
  activeActivityOverride: PropTypes.string,
  navigationTitle: PropTypes.node,
  inspectorTitle: PropTypes.node,
  drawerTitle: PropTypes.string,
};

WorkbenchShell.defaultProps = {
  activities: [], navigationViews: {},
};
