/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import React, { useRef, useMemo, useEffect, useCallback, useState } from 'react';
import DockLayout from 'rc-dock';
import PropTypes from 'prop-types';
import EventBus from '../EventBus';
import getApiInstance from '../../api_instance';
import url_for from 'sources/url_for';
import { PgIconButton } from '../../components/Buttons';
import CloseIcon from '@mui/icons-material/CloseRounded';
import gettext from 'sources/gettext';
import {ExpandDialogIcon, MinimizeDialogIcon } from '../../components/ExternalIcon';
import { Box } from '@mui/material';
import ErrorBoundary from '../ErrorBoundary';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ContextMenu from '../../components/ContextMenu';
import { showRenameTab } from '../../Dialogs';
import usePreferences from '../../../../preferences/static/js/store';
import _ from 'lodash';
import UtilityView from '../../UtilityView';
import ToolView, { getToolTabParams } from '../../ToolView';
import { ApplicationStateProvider, useApplicationState } from '../../../../settings/static/ApplicationStateProvider';
import { BROWSER_PANELS, WORKSPACES } from '../../../../browser/static/js/constants';
import pgWindow from 'sources/window';
import {createWorkspaceHost} from 'sources/cdeadmin_ui/workspace/WorkspaceHost';
import {toolFactoryRegistry} from 'sources/cdeadmin_ui/workspace/ToolRegistry';

export const WORKSPACE_PLACEMENTS = Object.freeze({
  BEFORE: 'before-tab',
  AFTER: 'after-tab',
  TAB: 'middle',
  LEFT: 'left',
  RIGHT: 'right',
  TOP: 'top',
  BOTTOM: 'bottom',
  FLOAT: 'float',
  DETACH: 'new-window',
  MAXIMIZE: 'maximize',
});

const VALID_WORKSPACE_PLACEMENTS = new Set(
  Object.values(WORKSPACE_PLACEMENTS)
);

export function TabTitle({id, closable, defaultInternal}) {
  const layoutDocker = React.useContext(LayoutDockerContext);
  const internal = layoutDocker?.find(id)?.internal ?? defaultInternal;
  const showServerColorIndicator = usePreferences(
    (state) => state.getPreferencesForModule('browser')?.show_server_color_indicator ?? false
  );
  const [attrs, setAttrs] = useState({
    icon: internal.icon,
    title: internal.title,
    tooltip: internal.tooltip ?? internal.title,
    bgcolor: internal.bgcolor,
    fgcolor: internal.fgcolor,
  });
  // Track visibility state to trigger re-renders when tabs switch
  const [isVisible, setIsVisible] = useState(layoutDocker?.isTabVisible(id) ?? false);

  const onContextMenu = useCallback((e)=>{
    const g = layoutDocker.find(id)?.group??'';
    if((layoutDocker.noContextGroups??[]).includes(g)) return;

    e.preventDefault();
    layoutDocker.eventBus.fireEvent(LAYOUT_EVENTS.CONTEXT, e, id);
  }, []);

  const onMouseDown = useCallback((e)=>{
    if(closable && e.button === 1) {
      e.preventDefault();
      layoutDocker.close(id);
    }
  }, [closable, id, layoutDocker]);

  useEffect(()=>{
    // Initialize visibility immediately once the effect runs and layoutObj is available
    setIsVisible(layoutDocker?.isTabVisible(id) ?? false);

    const deregister = layoutDocker.eventBus.registerListener(LAYOUT_EVENTS.REFRESH_TITLE, (panelId)=>{
      if(panelId == id) {
        const internal = layoutDocker?.find(id)?.internal??{};
        setAttrs({
          icon: internal.icon,
          title: internal.title,
          tooltip: internal.tooltip ?? internal.title,
          bgcolor: internal.bgcolor,
          fgcolor: internal.fgcolor,
        });
        layoutDocker.saveLayout();
      }
    });

    // Listen for tab activation to update visibility state
    // This ensures the color indicator appears/disappears when switching tabs
    const activeListener = layoutDocker.eventBus.registerListener(LAYOUT_EVENTS.ACTIVE, () => {
      const visible = layoutDocker?.isTabVisible(id);
      setIsVisible(visible);
    });

    // Listen for server color updates
    // This custom event is triggered specifically when server bgcolor/fgcolor changes
    const serverColorsUpdatedHandler = (serverId, colorData) => {
      const panelData = layoutDocker?.find(id);
      if (!panelData?.internal) {
        return;
      }

      const tabServerId = panelData.internal.server_id;
      if (!tabServerId || tabServerId !== serverId) {
        return;
      }

      // Update internal data and attrs with new colors
      panelData.internal.bgcolor = colorData.bgcolor || null;
      panelData.internal.fgcolor = colorData.fgcolor || null;
      if (panelData.metaData?.tabParams) {
        panelData.metaData.tabParams.bgcolor = colorData.bgcolor || null;
        panelData.metaData.tabParams.fgcolor = colorData.fgcolor || null;
      }
      setAttrs(prev => ({
        ...prev,
        bgcolor: colorData.bgcolor || null,
        fgcolor: colorData.fgcolor || null,
      }));
      // Persist the updated colors so they survive reloads
      layoutDocker.saveLayout();
    };

    // Listen to the custom server color update event
    pgWindow.pgAdmin?.Browser?.Events?.on('pgadmin:server:colors:updated', serverColorsUpdatedHandler);

    return ()=>{
      deregister?.();
      activeListener?.();
      pgWindow.pgAdmin?.Browser?.Events?.off('pgadmin:server:colors:updated', serverColorsUpdatedHandler);
    };
  }, []);

  return (
    <Box display="flex" alignItems="center" title={attrs.tooltip} onContextMenu={onContextMenu} onMouseDown={onMouseDown} width="100%">
      {attrs.icon && <span className={`dock-tab-icon ${attrs.icon}`}></span>}
      {showServerColorIndicator && attrs.bgcolor && !isVisible && (
        <Box
          component="span"
          sx={{
            width: '12px',
            height: '12px',
            borderRadius: '50%',
            backgroundColor: attrs.bgcolor,
            marginLeft: '2px',
            marginRight: '4px',
            flexShrink: 0,
            border: '1px solid rgba(0, 0, 0, 0.1)',
          }}
        />
      )}
      <span style={{textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap'}} data-visible={isVisible}>{attrs.title}</span>
      {closable && <PgIconButton title={gettext('Close')} icon={<CloseIcon style={{height: '0.7em'}} />} size="xs" noBorder onClick={()=>{
        layoutDocker.close(id);
      }} style={{margin: '-1px -10px -1px 0'}} />}
    </Box>
  );
}

TabTitle.propTypes = {
  id: PropTypes.string,
  closable: PropTypes.bool,
  defaultInternal: PropTypes.object
};

export class LayoutDocker {
  constructor(layoutId, defaultLayout, resetToTabPanel, noContextGroups) {
    this.layoutId = layoutId;
    this.defaultLayout = defaultLayout;
    /* When reset layout, we'll move the manually added tabs to this panel */
    this.resetToTabPanel = resetToTabPanel;
    // don't show context for these groups
    this.noContextGroups = noContextGroups??[];
    this.noContextGroups.push('dialogs');

    this.layoutObj = null;
    this.eventBus = new EventBus();
    this.workspaceHost = createWorkspaceHost();
  }

  close(panelId, force=false) {
    const panelData = this.find(panelId);
    if(!panelData) {
      return;
    }
    if(!panelData.internal?.closable) {
      return;
    }
    if(panelData.internal?.manualClose && !force) {
      this.eventBus.fireEvent(LAYOUT_EVENTS.CLOSING, panelId);
    } else {
      this.layoutObj.dockMove(panelData, null, 'remove');
      // rc-dock is not firing the "active" event after a tab is removed
      // and another is focussed. here we try get the new active id and
      // manually fire the active event
      const newActiveId = this.find(panelData?.parent?.id)?.activeId;
      if(newActiveId) {
        this.eventBus.fireEvent(LAYOUT_EVENTS.ACTIVE, newActiveId);
      }
    }
  }

  closeAll(panelId, exceptCurrent=false) {
    let parentData = this.find(panelId);
    if(_.isUndefined(parentData.tabs)) {
      parentData = parentData.parent;
    }
    if(parentData?.tabs) {
      parentData.tabs.filter((t)=>(t.internal?.closable && (exceptCurrent ? t.id!=panelId : true))).forEach((t)=>{
        this.close(t.id);
      });
    }
  }

  focus(panelId) {
    this.layoutObj.updateTab(panelId, null, true);
  }

  //it will navigate to nearest panel/tab
  navigatePanel() {
    this.layoutObj.navigateToPanel();
  }

  find(...args) {
    return this.layoutObj?.find(...args);
  }

  setTitle(panelId, title, icon, tooltip) {
    const panelData = this.find(panelId);
    if(!panelData) return;

    const internal = {
      ...panelData.internal,
    };
    if(title) {
      internal.title = title;
    }
    if(icon) {
      internal.icon = icon;
    }
    if(tooltip) {
      internal.tooltip = tooltip;
    }
    panelData.internal = internal;
    this.eventBus.fireEvent(LAYOUT_EVENTS.REFRESH_TITLE, panelId);
  }

  setInternalAttrs(panelId, attrs) {
    const panelData = this.find(panelId);
    panelData.internal = {
      ...panelData.internal,
      ...attrs,
    };
  }

  getInternalAttrs(panelId) {
    const panelData = this.find(panelId);
    return panelData.internal;
  }

  openDialog(panelData, width=500, height=300) {
    let panel = this.layoutObj.find(panelData.id);
    if(panel) {
      this.layoutObj.dockMove(panel, null, 'front');
    } else {
      let {width: lw, height: lh} = this.layoutObj.getLayoutSize();
      /* position in more top direction */
      lw = (lw - width)/2;
      lh = (lh - height)/5;
      this.layoutObj.dockMove({
        x: lw,
        y: lh,
        w: width,
        h: height,
        tabs: [LayoutDocker.getPanel({
          ...panelData,
          content: <ErrorBoundary>{panelData.content}</ErrorBoundary>,
          group: 'dialogs',
          closable: true,
        })],
      }, null, 'float');
    }
  }

  isTabOpen(panelId) {
    return Boolean(this.layoutObj.find(panelId));
  }

  isTabVisible(panelId) {
    let panelData = this.layoutObj?.find(panelId);
    return panelData?.parent?.activeId == panelData?.id;
  }

  openTab(panelData, refTabId, direction='middle', forceRerender=false) {
    let panel = this.layoutObj.find(panelData.id);
    if(panel) {
      if(forceRerender) {
        this.layoutObj.updateTab(panelData.id, LayoutDocker.getPanel(panelData), true);
      } else {
        this.focus(panelData.id);
      }
    } else {
      let tgtPanel = this.layoutObj.find(refTabId);
      this.layoutObj.dockMove(LayoutDocker.getPanel(panelData), tgtPanel, direction);
    }
  }

  place(panelId, placement, targetId=null) {
    if(!VALID_WORKSPACE_PLACEMENTS.has(placement)) {
      throw new TypeError(`Unsupported workspace placement: ${placement}`);
    }
    const source = this.find(panelId);
    if(!source || !this.layoutObj) {
      return false;
    }

    const toolDescriptor = source.metaData?.toolDescriptor ??
      source.internal?.toolDescriptor;
    if(placement === WORKSPACE_PLACEMENTS.DETACH) {
      if(!toolDescriptor) {
        this.eventBus.fireEvent(
          LAYOUT_EVENTS.PLACEMENT_FAILED, panelId, placement,
          'This panel has no durable tool descriptor.'
        );
        return false;
      }
      const readiness = this.workspaceHost.prepareDetach(toolDescriptor);
      if(!readiness.allowed) {
        this.eventBus.fireEvent(
          LAYOUT_EVENTS.PLACEMENT_FAILED, panelId, placement, readiness.reason
        );
        return false;
      }
      this.eventBus.fireEvent(
        LAYOUT_EVENTS.DETACH_PREPARED, panelId, readiness.descriptor
      );
    }

    let target = targetId ? this.find(targetId) : source.parent;
    if([WORKSPACE_PLACEMENTS.FLOAT, WORKSPACE_PLACEMENTS.DETACH,
      WORKSPACE_PLACEMENTS.MAXIMIZE].includes(placement)) {
      target = null;
    }
    if(!target && placement !== WORKSPACE_PLACEMENTS.FLOAT &&
      placement !== WORKSPACE_PLACEMENTS.DETACH &&
      placement !== WORKSPACE_PLACEMENTS.MAXIMIZE) {
      return false;
    }
    if([WORKSPACE_PLACEMENTS.BEFORE, WORKSPACE_PLACEMENTS.AFTER]
      .includes(placement) && !targetId) {
      return false;
    }

    this.layoutObj.dockMove(source, target, placement);
    if(toolDescriptor) {
      this.workspaceHost.publishPlacement(toolDescriptor, placement);
    }
    this.eventBus.fireEvent(LAYOUT_EVENTS.CHANGE, panelId, placement);
    return true;
  }

  moveAdjacent(panelId, placement) {
    if(![WORKSPACE_PLACEMENTS.BEFORE, WORKSPACE_PLACEMENTS.AFTER]
      .includes(placement)) {
      throw new TypeError('Adjacent movement requires before-tab or after-tab.');
    }
    const panel = this.find(panelId);
    const siblings = panel?.parent?.tabs ?? [];
    const index = siblings.findIndex((tab)=>tab.id === panelId);
    const targetIndex = placement === WORKSPACE_PLACEMENTS.BEFORE ?
      index - 1 : index + 1;
    if(index < 0 || targetIndex < 0 || targetIndex >= siblings.length) {
      return false;
    }
    return this.place(panelId, placement, siblings[targetIndex].id);
  }

  async moveWindowToAdjacentDisplay(direction) {
    try {
      const result = await this.workspaceHost.moveToAdjacentDisplay(direction);
      this.eventBus.fireEvent(
        LAYOUT_EVENTS.WINDOW_DISPLAY_CHANGED, direction, result
      );
      return true;
    } catch (error) {
      this.eventBus.fireEvent(
        LAYOUT_EVENTS.PLACEMENT_FAILED, null, 'display', error.message
      );
      return false;
    }
  }

  listPlacementTargets(panelId) {
    const source = this.find(panelId);
    const targets = [];
    const visit = (node) => {
      if(!node) return;
      if(node.children) {
        node.children.forEach(visit);
      } else if(node.tabs?.length && node !== source?.parent) {
        const active = node.tabs.find((tab)=>tab.id === node.activeId) ??
          node.tabs[0];
        targets.push(Object.freeze({
          id: active.id,
          label: String(active.internal?.title ?? active.id),
        }));
      }
    };
    const layout = this.layoutObj?.getLayout?.();
    visit(layout?.dockbox);
    visit(layout?.floatbox);
    return Object.freeze(targets);
  }

  getPlacementCapabilities(panelId) {
    const panel = this.find(panelId);
    const siblings = panel?.parent?.tabs ?? [];
    const index = siblings.findIndex((tab)=>tab.id === panelId);
    return Object.freeze({
      before: index > 0,
      after: index >= 0 && index < siblings.length - 1,
      split: Boolean(panel?.parent),
      attach: this.listPlacementTargets(panelId).length > 0,
      float: panel?.internal?.floatable !== false,
      detach: Boolean(panel?.internal?.detachable &&
        panel?.metaData?.toolDescriptor &&
        this.workspaceHost.capabilities().popoutWindow),
      detachReason: panel?.internal?.detachable &&
        !this.workspaceHost.capabilities().popoutWindow ?
        'This host cannot open another application window.' : '',
      host: this.workspaceHost.capabilities(),
    });
  }

  loadLayout(savedLayout) {
    if (!savedLayout) {
      // No saved layout - DockLayout already initialized with defaultLayout
      return;
    }
    try {
      this.layoutObj.loadLayout(JSON.parse(savedLayout));
      this.addMissingDefaultPanels();
    } catch {
      /* Fallback to default */
      this.layoutObj.loadLayout(this.defaultLayout);
    }
  }

  addMissingDefaultPanels() {
    // Flatten both layouts to get all tabs
    const flattenLayout = (box, arr) => {
      box.children.forEach((child) => {
        if (child.children) {
          flattenLayout(child, arr);
        } else {
          arr.push(...(child.tabs ?? []));
        }
      });
    };

    const flatDefault = [];
    const flatCurrent = [];
    flattenLayout(this.defaultLayout.dockbox, flatDefault);
    flattenLayout(this.layoutObj.getLayout().dockbox, flatCurrent);

    // Find tabs in default but not in saved layout
    const missingTabs = _.differenceBy(flatDefault, flatCurrent, 'id');

    // Only add non-closable tabs (closable tabs may have been intentionally removed)
    const missingNonClosableTabs = missingTabs.filter(tab => !tab.internal?.closable);

    if (missingNonClosableTabs.length === 0) return;

    // Save the active tab IDs for each panel group before adding missing tabs,
    // so that newly added tabs don't steal focus from the user's current tab.
    const savedActiveIds = [];
    const collectActiveIds = (box) => {
      box.children.forEach((child) => {
        if (child.children) {
          collectActiveIds(child);
        } else if (child.activeId) {
          savedActiveIds.push(child.activeId);
        }
      });
    };
    collectActiveIds(this.layoutObj.getLayout().dockbox);

    // Add each missing tab next to a sibling from its original panel group
    missingNonClosableTabs.forEach((tab) => {
      const siblingId = this.findSiblingTab(tab.id, flatDefault, flatCurrent);
      if (siblingId) {
        this.openTab({
          id: tab.id,
          content: tab.content,
          ...tab.internal
        }, siblingId, 'middle');
      } else if (this.resetToTabPanel) {
        // Fallback: add to the reset panel location
        this.openTab({
          id: tab.id,
          content: tab.content,
          ...tab.internal
        }, this.resetToTabPanel, 'middle');
      }
    });

    // Restore the original active tabs so newly added tabs don't steal focus
    savedActiveIds.forEach((activeId) => {
      this.focus(activeId);
    });
  }

  findSiblingTab(tabId, flatDefault, flatCurrent) {
    // Find which panel group this tab belongs to in the default layout
    const findPanelTabs = (box, targetId) => {
      for (const child of box.children) {
        if (child.children) {
          const result = findPanelTabs(child, targetId);
          if (result) return result;
        } else if (child.tabs) {
          const hasTarget = child.tabs.some(t => t.id === targetId);
          if (hasTarget) return child.tabs.map(t => t.id);
        }
      }
      return null;
    };

    const siblingIds = findPanelTabs(this.defaultLayout.dockbox, tabId);
    if (!siblingIds) return null;

    // Find a sibling that exists in current layout
    const currentIds = flatCurrent.map(t => t.id);
    return siblingIds.find(id => id !== tabId && currentIds.includes(id));
  }

  saveLayout(l) {
    let api = getApiInstance();
    if(!this.layoutId || !this.layoutObj) {
      return;
    }
    const formData = new FormData();
    formData.append('setting', this.layoutId);
    formData.append('value', JSON.stringify(l || this.layoutObj.saveLayout()));
    api.post(url_for('settings.store_bulk'), formData)
      .catch(()=>{/* No need to throw error */});
  }

  resetLayout() {
    const flatCurr = [];
    const flatDefault = [];

    // flatten the nested tabs into an array
    const flattenLayout = (box, arr)=>{
      box.children.forEach((child)=>{
        if(child.children) {
          flattenLayout(child, arr);
        } else {
          arr.push(...(child.tabs ?? []));
        }
      });
    };

    flattenLayout(this.defaultLayout.dockbox, flatDefault);
    flattenLayout(this.layoutObj.getLayout().dockbox, flatCurr);

    // Find the difference between default layout and current layout
    let saveNonDefaultTabs = _.differenceBy(flatCurr, flatDefault, 'id');

    // load the default layout
    this.layoutObj.loadLayout(this.defaultLayout);
    const focusOn = this.find(this.resetToTabPanel)?.activeId;

    // restor the tabs opened
    saveNonDefaultTabs.forEach((t)=>{
      this.openTab({
        id: t.id, content: t.content, ...t.internal
      }, this.resetToTabPanel, 'middle');
    });

    focusOn && this.focus(focusOn);
    this.saveLayout();
    // Anything that tracks state alongside the layout, e.g. whether the
    // Object Explorer is collapsed, needs to know the layout went back to
    // its defaults.
    this.eventBus.fireEvent(LAYOUT_EVENTS.RESET);
  }

  static getPanel({icon, title, closable, tooltip, renamable, manualClose,
    detachable=false, floatable=false, toolDescriptor, bgcolor, fgcolor,
    server_id, ...attrs}) {
    const internal = {
      icon: icon,
      title: title,
      tooltip: tooltip,
      closable: _.isUndefined(closable) ? manualClose : closable,
      renamable: renamable,
      manualClose: manualClose,
      detachable: detachable,
      floatable: floatable,
      toolDescriptor: toolDescriptor,
      bgcolor: bgcolor,
      fgcolor: fgcolor,
      server_id: server_id, // Store server_id to enable color updates when server properties change
    };

    return {
      cached: true,
      group: 'default',
      minWidth: 200,
      ...attrs,
      closable: false,
      title: <TabTitle id={attrs.id} closable={attrs.group!='dialogs' && closable} defaultInternal={internal}/>,
      internal: internal
    };
  }

  static moveTo(direction) {
    let dockBar = document.activeElement.closest('.dock')?.querySelector('.dock-bar.drag-initiator');
    if(dockBar) {
      let key = {
        key: 'ArrowRight', keyCode: 39, which: 39, code: 'ArrowRight',
        metaKey: false, ctrlKey: false, shiftKey: false, altKey: false,
        bubbles: true,
      };
      if(direction == 'right') {
        key = {
          ...key,
          key: 'ArrowRight', keyCode: 39, which: 39, code: 'ArrowRight'
        };
      } else if(direction == 'left') {
        key = {
          ...key,
          key: 'ArrowLeft', keyCode: 37, which: 37, code: 'ArrowLeft',
        };
      }
      dockBar.dispatchEvent(new KeyboardEvent('keydown', key));
    }
  }

  static switchPanel() {
    let currDockPanel = document.activeElement.closest('.dock-panel.dock-style-default');
    let dockLayoutPanels = currDockPanel?.closest('.dock-layout').querySelectorAll('.dock-panel.dock-style-default');
    if(dockLayoutPanels?.length > 1) {
      for(let i=0; i<dockLayoutPanels.length; i++) {
        if(dockLayoutPanels[i] == currDockPanel) {
          let newPanelIdx = (i+1)%dockLayoutPanels.length;
          dockLayoutPanels[newPanelIdx]?.querySelector('.dock-tab.dock-tab-active .dock-tab-btn')?.focus();
          break;
        }
      }
    }
  }
}

export const LayoutDockerContext = React.createContext(new LayoutDocker(null, null));

function DialogClose({panelData}) {
  const layoutDocker = React.useContext(LayoutDockerContext);
  // In a dialog, panelData is the data of the container panel and not the
  // data of actual dialog tab. panelData.activeId gives the id of dialog tab.
  return (
    <Box display="flex" alignItems="center">
      <PgIconButton title={gettext('Close')} icon={<CloseIcon />} size="xs" noBorder onClick={()=>{
        layoutDocker.close(panelData.activeId);
      }} style={{marginRight: '-4px'}}/>
    </Box>
  );
}
DialogClose.propTypes = {
  panelData: PropTypes.object
};

function getDialogsGroup() {
  return {
    disableDock: true,
    tabLocked: true,
    floatable: 'singleTab',
    moreIcon: <ExpandMoreIcon style={{height: '0.9em'}} />,
    panelExtra: (panelData) => {
      return <DialogClose panelData={panelData} />;
    }
  };
}

export function getDefaultGroup() {
  return {
    closable: false,
    maximizable: false,
    floatable: false,
    moreIcon: <ExpandMoreIcon style={{height: '0.9em', marginTop: '4px'}} />,
    panelExtra: (panelData, context) => {
      let icon = <ExpandDialogIcon style={{width: '0.7em'}}/>;
      let title = gettext('Maximise');
      if(panelData?.parent?.mode == 'maximize') {
        icon = <MinimizeDialogIcon />;
        title = gettext('Restore');
      }
      return <Box display="flex" alignItems="center">
        {Boolean(panelData.maximizable) && <PgIconButton title={title} icon={icon} size="xs" noBorder onClick={()=>{
          context.dockMove(panelData, null, 'maximize');
        }} />}
      </Box>;
    }
  };
}

export default function Layout({groups, noContextGroups, getLayoutInstance, layoutId, savedLayout, resetToTabPanel, enableToolEvents=false, isLayoutVisible=true, className, ...props}) {
  const [[contextPos, contextPanelId, contextExtraMenus], setContextPos] = React.useState([null, null, null]);
  const defaultGroups = React.useMemo(()=>({
    'dialogs': getDialogsGroup(),
    'default': getDefaultGroup(),
    ...groups,
  }), [groups]);
  const layoutDockerObj = React.useMemo(()=>new LayoutDocker(layoutId, props.defaultLayout, resetToTabPanel, noContextGroups), []);
  const prefStore = usePreferences();
  const dynamicTabsStyleRef = useRef();
  const saveAppStateRef = useRef(prefStore?.getPreferencesForModule('misc')?.save_app_state);
  const { deleteToolData } = useApplicationState();

  useEffect(()=>{
    layoutDockerObj.workspaceHost.registerWindow({
      workspaceId: layoutId || 'default',
      role: 'workspace',
    }).catch(()=>{/* Browser hosts have no registration service. */});
    const removeListener = layoutDockerObj.eventBus.registerListener(LAYOUT_EVENTS.REMOVE, (panelId)=>{
      layoutDockerObj.close(panelId);
      deleteToolData(panelId);
    });

    const contextListener = layoutDockerObj.eventBus.registerListener(LAYOUT_EVENTS.CONTEXT, (e, id, extraMenus)=>{
      setContextPos([{x: e.clientX, y: e.clientY}, id, extraMenus]);
    });
    return ()=>{
      removeListener?.();
      contextListener?.();
      layoutDockerObj.workspaceHost.dispose();
    };
  }, []);

  useEffect(()=>{
    const dynamicTabs = prefStore.getPreferencesForModule('browser')?.dynamic_tabs;
    const saveAppState = prefStore?.getPreferencesForModule('misc')?.save_app_state;

    // Add a class to set max width for non dynamic Tabs
    if(!dynamicTabs && !dynamicTabsStyleRef.current) {
      const css = '.dock-tab:not(div.dock-tab-active) { max-width: 180px; }',
        head = document.head || document.getElementsByTagName('head')[0];

      dynamicTabsStyleRef.current = document.createElement('style');
      head.appendChild(dynamicTabsStyleRef.current);
      dynamicTabsStyleRef.current.appendChild(document.createTextNode(css));
    } else if(dynamicTabs && dynamicTabsStyleRef.current) {
      dynamicTabsStyleRef.current.remove();
      dynamicTabsStyleRef.current = null;
    }

    if(!saveAppState && saveAppStateRef.current){
      layoutDockerObj.saveLayout();
    }
    saveAppStateRef.current = saveAppState;

  }, [prefStore]);

  const getTabMenuItems = (panelId)=>{
    const ret = [];
    if(panelId) {
      const panelData = layoutDockerObj?.find(panelId);
      if(_.isUndefined(panelData.tabs)) {
        if(panelData.internal.closable) {
          ret.push({
            id: 'close-tool',
            label: gettext('Close'),
            iconKey: 'action.cancel',
            callback: ()=>{
              layoutDockerObj.close(panelId);
            }
          });
        }
        if(panelData.parent?.tabs?.length > 1) {
          ret.push({
            id: 'close-other-tools',
            label: gettext('Close Others'),
            callback: ()=>{
              layoutDockerObj.closeAll(panelId, true);
            }
          });
        }
      }
      ret.push({
        id: 'close-all-tools',
        label: gettext('Close All'),
        callback: ()=>{
          layoutDockerObj.closeAll(panelId);
        }
      });
      if(panelData.internal?.renamable) {
        ret.push({
          type: 'separator',
        }, {
          label: gettext('Rename'),
          id: 'rename-tool',
          iconKey: 'action.rename',
          callback: ()=>{
            showRenameTab(panelId, layoutDockerObj);
          }
        });
      }
      const placement = layoutDockerObj.getPlacementCapabilities(panelId);
      ret.push({type: 'separator'});
      if(placement.before) {
        ret.push({
          id: 'move-tool-before',
          label: gettext('Move Before'),
          callback: ()=>layoutDockerObj.moveAdjacent(
            panelId, WORKSPACE_PLACEMENTS.BEFORE),
        });
      }
      if(placement.after) {
        ret.push({
          id: 'move-tool-after',
          label: gettext('Move After'),
          callback: ()=>layoutDockerObj.moveAdjacent(
            panelId, WORKSPACE_PLACEMENTS.AFTER),
        });
      }
      if(placement.split) {
        [
          [gettext('Move to Left Panel'), WORKSPACE_PLACEMENTS.LEFT],
          [gettext('Move to Right Panel'), WORKSPACE_PLACEMENTS.RIGHT],
          [gettext('Move to Top Panel'), WORKSPACE_PLACEMENTS.TOP],
          [gettext('Move to Bottom Panel'), WORKSPACE_PLACEMENTS.BOTTOM],
        ].forEach(([label, direction])=>ret.push({
          id: `move-tool-${direction}`,
          label,
          callback: ()=>layoutDockerObj.place(panelId, direction),
        }));
      }
      if(placement.attach) {
        ret.push({
          id: 'attach-tool-as-tab',
          label: gettext('Attach as Tab'),
          iconKey: 'action.attach',
          getMenuItems: ()=>layoutDockerObj.listPlacementTargets(panelId)
            .map((target)=>({
              id: `attach-tool-${target.id}`,
              label: target.label,
              callback: ()=>layoutDockerObj.place(
                panelId, WORKSPACE_PLACEMENTS.TAB, target.id),
            })),
        });
      }
      if(placement.float) {
        ret.push({
          id: 'float-tool',
          label: gettext('Move to Floating Panel'),
          callback: ()=>layoutDockerObj.place(
            panelId, WORKSPACE_PLACEMENTS.FLOAT),
        });
      }
      if(panelData.internal?.detachable) {
        ret.push({
          id: 'detach-tool',
          label: gettext('Detach to New Window'),
          iconKey: 'action.detach',
          enabled: placement.detach,
          disabledReason: placement.detachReason,
          callback: ()=>layoutDockerObj.place(
            panelId, WORKSPACE_PLACEMENTS.DETACH),
        });
      }
      if(placement.host.nativeWindowPlacement) {
        ret.push({
          id: 'move-window-next-display',
          label: gettext('Move Window to Next Display'),
          iconKey: 'action.move',
          callback: ()=>layoutDockerObj
            .moveWindowToAdjacentDisplay('next'),
        }, {
          id: 'move-window-previous-display',
          label: gettext('Move Window to Previous Display'),
          iconKey: 'action.move',
          callback: ()=>layoutDockerObj
            .moveWindowToAdjacentDisplay('previous'),
        });
      }
    }
    return ret;
  };

  const saveTab = (tab) => {
  // 'tab' here is the full TabData object, potentially with 'title', 'content', etc.
  // We only want to save the 'id' and any custom properties needed by loadTab.
    const savedTab = { id: tab.id };
    if (saveAppStateRef.current && tab.metaData && !BROWSER_PANELS.DEBUGGER_TOOL.includes(tab.id.split('_')[0])) {
    // add custom properties that were part of the original TabBase
      const updatedMetaData = {
        ...tab.metaData,
        tabParams: {
          ...( tab.metaData.tabParams || {}),
          cached: tab?.cached,
          internal: tab?.internal,
          workSpace: layoutDockerObj?.layoutId.split('-')[1] || WORKSPACES.DEFAULT
        },
        restore: true,
      };
      savedTab.metaData = updatedMetaData;
    }
    return savedTab;
  };

  const flatDefaultLayout = useMemo(()=>{
    const flat = [];
    const flattenLayout = (box)=>{
      box.children.forEach((child)=>{
        if(child.children) {
          flattenLayout(child);
        }
        else {
          flat.push(...(child.tabs ?? []));
        }
      });
    };
    flattenLayout(props.defaultLayout.dockbox);
    return flat;
  }, [props.defaultLayout]);

  const loadTab = (tab)=>{
    const tabData = flatDefaultLayout.find((t)=>t.id == tab.id);
    if(tab.metaData?.toolDescriptor) {
      return toolFactoryRegistry.restore(tab.metaData.toolDescriptor, {
        toolUrl: tab.metaData.toolUrl,
        formParams: tab.metaData.formParams,
        tabParams: tab.metaData.tabParams,
      });
    }
    if(!tabData && tab.metaData) {
      return LayoutDocker.getPanel(getToolTabParams(tab.id, tab.metaData.toolUrl, tab.metaData.formParams, tab.metaData.tabParams, tab.metaData?.restore));
    }
    return tabData;
  };

  const contextMenuItems = getTabMenuItems(contextPanelId)
    .concat(contextExtraMenus ? [{type: 'separator'}, ...contextExtraMenus] : []);

  return (
    <ApplicationStateProvider>
      <LayoutDockerContext.Provider value={layoutDockerObj}>
        <Box height="100%" width="100%" display={isLayoutVisible ? 'initial' : 'none'}
          className={className} >
          {useMemo(()=>(<DockLayout
            style={{
              height: '100%',
            }}
            ref={(obj)=>{
              if(obj) {
                layoutDockerObj.layoutObj = obj;
                getLayoutInstance?.(layoutDockerObj);
                layoutDockerObj.loadLayout(savedLayout);
              }
            }}
            loadTab={loadTab}
            saveTab={saveTab}
            groups={defaultGroups}
            onLayoutChange={(l, currentTabId, direction)=>{
              if(Object.values(LAYOUT_EVENTS).indexOf(direction) > -1) {
                layoutDockerObj.eventBus.fireEvent(LAYOUT_EVENTS[direction.toUpperCase()], currentTabId);
                layoutDockerObj.saveLayout(l);
              } else if(direction && direction != 'update') {
                layoutDockerObj.eventBus.fireEvent(LAYOUT_EVENTS.CHANGE, currentTabId);
                layoutDockerObj.saveLayout(l);
              }
            }}
            {...props}
          />), [])}
        </Box>
        <div id="layout-portal"></div>
        <ContextMenu menuItems={contextMenuItems} position={contextPos} onClose={()=>setContextPos([null, null, null])}
          label="Layout Context Menu" />
        {enableToolEvents && <>
          <UtilityView dockerObj={layoutDockerObj} />
          <ToolView dockerObj={layoutDockerObj} />
        </>}
      </LayoutDockerContext.Provider>
    </ApplicationStateProvider>
  );
}

Layout.propTypes = {
  groups: PropTypes.object,
  defaultLayout: PropTypes.object,
  noContextGroups: PropTypes.array,
  getLayoutInstance: PropTypes.func,
  layoutId: PropTypes.string,
  savedLayout: PropTypes.string,
  resetToTabPanel: PropTypes.string,
  enableToolEvents: PropTypes.bool,
  isLayoutVisible: PropTypes.bool,
  className: PropTypes.string
};


export const LAYOUT_EVENTS = {
  INIT: 'init',
  ACTIVE: 'active',
  REMOVE: 'remove',
  FLOAT: 'float',
  FRONT: 'front',
  MAXIMIZE: 'maximize',
  MOVE: 'move',
  CLOSING: 'closing',
  CONTEXT: 'context',
  CHANGE: 'change',
  REFRESH_TITLE: 'refresh-title',
  RESET: 'reset',
  DETACH_PREPARED: 'detach-prepared',
  PLACEMENT_FAILED: 'placement-failed',
  WINDOW_DISPLAY_CHANGED: 'window-display-changed',
};
