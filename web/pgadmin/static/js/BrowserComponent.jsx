/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AppMenuBar from './AppMenuBar';
import ObjectBreadcrumbs from './components/ObjectBreadcrumbs';
import {
  getDefaultGroup,
  WORKSPACE_EVENTS as LAYOUT_EVENTS,
  Workspace as Layout,
  WorkspaceController as LayoutDocker,
} from 'sources/cdeadmin_ui/workspace/Workspace';
import gettext from 'sources/gettext';
import ObjectExplorer from './tree/ObjectExplorer';
import Properties from '../../misc/properties/Properties';
import SQL from '../../misc/sql/static/js/SQL';
import Statistics from '../../misc/statistics/static/js/Statistics';
import { BROWSER_PANELS, WORKSPACES } from '../../browser/static/js/constants';
import Dependencies from '../../misc/dependencies/static/js/Dependencies';
import Dependents from '../../misc/dependents/static/js/Dependents';
import ModalProvider from './helpers/ModalProvider';
import { NotifierProvider } from './helpers/Notifier';
import ObjectExplorerToolbar from './tree/ObjectExplorer/ObjectExplorerToolbar';
import MainMoreToolbar from './helpers/MainMoreToolbar';
import Dashboard from '../../dashboard/static/js/Dashboard';
import usePreferences from '../../preferences/static/js/store';
import { getBrowser } from './utils';
import PropTypes from 'prop-types';
import Processes from '../../misc/bgprocess/static/js/Processes';
import currentUser from 'pgadmin.user_management.current_user';
import { useBeforeUnload } from './custom_hooks';
import pgWindow from 'sources/window';
import WorkspaceToolbar from '../../misc/workspaces/static/js/WorkspaceToolbar';
import { useWorkspace, WorkspaceProvider } from '../../misc/workspaces/static/js/WorkspaceProvider';
import { PgAdminProvider, usePgAdmin } from './PgAdminProvider';
import PreferencesComponent from '../../preferences/static/js/components/PreferencesComponent';
import { ApplicationStateProvider } from '../../settings/static/ApplicationStateProvider';
import { appAutoUpdateNotifier } from './helpers/appAutoUpdateNotifier';
import {
  activityRegistry,
  initializeCDEadminPlatform,
  ProjectExplorer,
  SurfaceHost,
  WorkbenchShell,
  ActiveStatusHost,
  BottomDrawerHost,
  InspectorHost,
  workbenchContextService,
} from 'sources/cdeadmin_ui';

export const processesPanelData = {
  id: BROWSER_PANELS.PROCESSES, title: gettext('Processes'), content: <Processes />, closable: true, group: 'playground'
};

export const preferencesPanelData = {
  id: BROWSER_PANELS.PREFERENCES, title: gettext('Preferences'), content: <PreferencesComponent panelId={BROWSER_PANELS.PREFERENCES} />, closable: true, manualClose: true, group: 'playground'
};

export const defaultTabsData = [
  {
    id: BROWSER_PANELS.DASHBOARD, title: gettext('Dashboard'), content: <Dashboard />, closable: true, group: 'playground'
  },
  {
    id: BROWSER_PANELS.SQL, title: gettext('SQL'), content: <SQL />, closable: true, group: 'playground'
  },
];

const getMorePanelGroup = (tabsData) => {
  return {
    ...getDefaultGroup(),
    panelExtra: () => <MainMoreToolbar tabsData={tabsData}/>
  };
};

let defaultLayout = {
  dockbox: {
    mode: 'horizontal',
    children: [
      {
        size: 100,
        id: BROWSER_PANELS.MAIN,
        group: 'playground',
        tabs: defaultTabsData.map((t)=>LayoutDocker.getPanel(t)),
        panelLock: {panelStyle: 'playground'},
      },
    ]
  },
};

function Layouts({browser, platform}) {
  const pgAdmin = usePgAdmin();
  const surfaceHost = useRef(null);
  const {
    config, enabled, currentWorkspace, isObjectExplorerVisible, setObjectExplorerVisible,
  } = useWorkspace();

  const ensureSurfaceHost = useCallback(() => {
    const docker = pgAdmin.Browser.docker.default_workspace;
    if(!docker) throw new Error('The main workbench is not ready.');
    if(!surfaceHost.current || surfaceHost.current.docker !== docker) {
      surfaceHost.current?.dispose();
      surfaceHost.current = new SurfaceHost(docker, undefined, {
        referencePanelId: BROWSER_PANELS.MAIN,
        context: () => ({
          services: {
            'project.assets': platform.projectAssets,
            'schema_compare.runtime': platform.schemaCompare,
            'lineage.runtime': platform.lineage,
            'quality.runtime': platform.quality,
            'contract.runtime': platform.contract,
            'etl.runtime': platform.etl,
            'cdc.runtime': platform.cdc,
            'replication.runtime': platform.replication,
            'tracing.runtime': platform.tracing,
          },
          commands: platform.modules.commands,
          currentUser,
          onCloseBlocked: (_descriptor, reason) =>
            pgAdmin.Browser.notifier.warning(reason),
        }),
      });
    }
    return surfaceHost.current;
  }, [pgAdmin, platform]);

  useEffect(()=>{
    // File → Reset Layout puts the layout back to its defaults, which has to
    // bring the Object Explorer back with it. The docker instance is set by
    // the ref callback below, which runs during commit, so it is available by
    // the time this effect runs.
    const docker = pgAdmin.Browser.docker.default_workspace;
    if(!docker) return;

    return docker.eventBus.registerListener(
      LAYOUT_EVENTS.RESET, ()=>setObjectExplorerVisible(true));
  }, [setObjectExplorerVisible]);

  const activities = activityRegistry.resolve();
  useEffect(() => {
    const context = () => ({
      showActivity: (activityId) => window.dispatchEvent(new CustomEvent(
        'cdeadmin:show-activity', {detail: activityId}
      )),
      createProject: () => window.dispatchEvent(new CustomEvent(
        'cdeadmin:project-create'
      )),
      service: platform.schemaCompare,
      schemaCompare: platform.schemaCompare,
      lineage: platform.lineage,
      quality: platform.quality,
      contract: platform.contract,
      etl: platform.etl,
      cdc: platform.cdc,
      replication: platform.replication,
      tracing: platform.tracing,
      currentUser,
      openSurface: (surfaceId, input) => ensureSurfaceHost().open(surfaceId, input),
    });
    pgAdmin.Browser.CDEadminCommandContext = context;
    return () => {
      if(pgAdmin.Browser.CDEadminCommandContext === context) {
        delete pgAdmin.Browser.CDEadminCommandContext;
      }
    };
  }, [ensureSurfaceHost, pgAdmin, platform.cdc, platform.contract, platform.etl,
    platform.lineage, platform.quality,
    platform.replication, platform.schemaCompare, platform.tracing]);
  useEffect(() => {
    const open = (event) => ensureSurfaceHost().open(
      `schema_compare.${event.detail.surface}`, {
        toolInstanceId: `schema-compare-${event.detail.sessionId}`,
        restoreRef: event.detail.sessionId, title: 'Schema Comparison',
      }
    ).catch((error) => pgAdmin.Browser.notifier.error(error.message));
    window.addEventListener('cdeadmin:schema-compare-open', open);
    return () => window.removeEventListener('cdeadmin:schema-compare-open', open);
  }, [ensureSurfaceHost, pgAdmin]);
  useEffect(() => {
    const open = (event) => ensureSurfaceHost().open(
      `quality.${event.detail.surface}`, {
        toolInstanceId: `quality-${event.detail.sessionId}`,
        restoreRef: event.detail.sessionId, title: 'Data Quality',
      }
    ).catch((error) => pgAdmin.Browser.notifier.error(error.message));
    window.addEventListener('cdeadmin:quality-open', open);
    return () => window.removeEventListener('cdeadmin:quality-open', open);
  }, [ensureSurfaceHost, pgAdmin]);
  useEffect(() => {
    const open = (event) => ensureSurfaceHost().open(
      `contract.${event.detail.surface}`, {
        toolInstanceId: `contract-${event.detail.sessionId}`,
        restoreRef: event.detail.sessionId, title: 'Data Contract Manager',
      }
    ).catch((error) => pgAdmin.Browser.notifier.error(error.message));
    window.addEventListener('cdeadmin:contract-open', open);
    return () => window.removeEventListener('cdeadmin:contract-open', open);
  }, [ensureSurfaceHost, pgAdmin]);
  useEffect(() => {
    const open = (event) => ensureSurfaceHost().open(
      `etl.${event.detail.surface}`, {
        toolInstanceId: `etl-${event.detail.sessionId}`,
        restoreRef: event.detail.sessionId, title: 'ETL Designer',
      }
    ).catch((error) => pgAdmin.Browser.notifier.error(error.message));
    window.addEventListener('cdeadmin:etl-open', open);
    return () => window.removeEventListener('cdeadmin:etl-open', open);
  }, [ensureSurfaceHost, pgAdmin]);
  useEffect(() => {
    const open = (event) => ensureSurfaceHost().open(
      `cdc.${event.detail.surface}`, {
        toolInstanceId: `cdc-${event.detail.sessionId}`,
        restoreRef: event.detail.sessionId, title: 'CDC Designer',
      }
    ).catch((error) => pgAdmin.Browser.notifier.error(error.message));
    window.addEventListener('cdeadmin:cdc-open', open);
    return () => window.removeEventListener('cdeadmin:cdc-open', open);
  }, [ensureSurfaceHost, pgAdmin]);
  useEffect(() => {
    const open = (event) => ensureSurfaceHost().open(
      `replication.${event.detail.surface}`, {
        toolInstanceId: `replication-${event.detail.sessionId}`,
        restoreRef: event.detail.sessionId, title: 'Replication Topology',
      }
    ).catch((error) => pgAdmin.Browser.notifier.error(error.message));
    window.addEventListener('cdeadmin:replication-open', open);
    return () => window.removeEventListener('cdeadmin:replication-open', open);
  }, [ensureSurfaceHost, pgAdmin]);
  useEffect(() => {
    const open = (event) => ensureSurfaceHost().open(
      `tracing.${event.detail.surface}`, {
        toolInstanceId: `tracing-${event.detail.sessionId}`,
        restoreRef: event.detail.sessionId, title: 'Distributed Tracing',
      }
    ).catch((error) => pgAdmin.Browser.notifier.error(error.message));
    window.addEventListener('cdeadmin:tracing-open', open);
    return () => window.removeEventListener('cdeadmin:tracing-open', open);
  }, [ensureSurfaceHost, pgAdmin]);
  useEffect(() => {
    const open = (event) => ensureSurfaceHost().open(
      `lineage.${event.detail.surface}`, {
        toolInstanceId: `lineage-${event.detail.sessionId}`,
        restoreRef: event.detail.sessionId, title: 'Data Lineage',
      }
    ).catch((error) => pgAdmin.Browser.notifier.error(error.message));
    window.addEventListener('cdeadmin:lineage-open', open);
    return () => window.removeEventListener('cdeadmin:lineage-open', open);
  }, [ensureSurfaceHost, pgAdmin]);
  const openAsset = async (project, asset) => {
    try {
      ensureSurfaceHost();
      const candidates = platform.modules.surfaces.list({
        assetType: asset.asset_type,
      });
      const editable = asset.access !== 'viewer' && asset.editor_capable;
      const surface = candidates.find((item) => editable && item.editable) ??
        candidates.find((item) => item.readOnly);
      if(!surface) {
        throw new Error('No registered surface can open asset type ' +
          asset.asset_type + '.');
      }
      await surfaceHost.current.open(surface.id, {
        toolInstanceId: surface.id + '-' + asset.asset_id,
        restoreRef: asset.asset_id,
        projectId: project.project_id,
        title: asset.name,
      });
    } catch(error) {
      pgAdmin.Browser.notifier.error(error.message);
    }
  };

  useEffect(() => () => surfaceHost.current?.dispose(), []);

  return (
    <ApplicationStateProvider>
      <div style={{height: (browser != 'Electron' ? 'calc(100% - 30px)' : '100%')}}>
        <WorkbenchShell activities={activities} initialLayout={{
          navigationVisible: !enabled || isObjectExplorerVisible,
          inspectorVisible: true,
        }} onLayoutChange={(layout) => {
          if(enabled && layout.navigationVisible !== isObjectExplorerVisible) {
            setObjectExplorerVisible(layout.navigationVisible);
          }
        }} navigationViews={{
          'activity.data': <div style={{height: '100%', display: 'flex',
            flexDirection: 'column'}}><ObjectExplorerToolbar />
            <div style={{flex: 1, minHeight: 0}}><ObjectExplorer /></div></div>,
          'activity.projects': <ProjectExplorer client={platform.projectAssets}
            onOpenAsset={openAsset}
            onSelectAsset={(project, asset) => workbenchContextService.update({
              surfaceTitle: asset.name, projectId: project.project_id,
              assetId: asset.asset_id, persistence: 'clean',
              validation: asset.validation_state === 'invalid' ?
                ['Asset validation failed'] : [],
            })} />,
        }} inspector={<InspectorHost corePages={[
          {id: 'properties', label: gettext('Properties'), content: <Properties />},
          {id: 'statistics', label: gettext('Statistics'), content: <Statistics />},
          {id: 'dependencies', label: gettext('Dependencies'), content: <Dependencies />},
          {id: 'dependents', label: gettext('Dependents'), content: <Dependents />},
        ]} />} drawer={<BottomDrawerHost tasks={platform.services.tasks}
          corePages={[{id: 'output', label: gettext('Output'),
            content: <Processes />}]} />}
        status={<ActiveStatusHost />}>
          {enabled && <WorkspaceToolbar/> }
          <Layout
            getLayoutInstance={(obj)=>{
              pgAdmin.Browser.docker.default_workspace = obj;
            }}
            defaultLayout={defaultLayout}
            layoutId='CDEadmin/Workbench/Main'
            savedLayout={pgAdmin.Browser.utils.layout['CDEadmin/Workbench/Main']}
            groups={{
              'playground': getMorePanelGroup(defaultTabsData),
            }}
            resetToTabPanel={BROWSER_PANELS.MAIN}
            enableToolEvents
            isLayoutVisible={!enabled || currentWorkspace == WORKSPACES.DEFAULT}
          />
          {enabled && config.map((item)=>(
            <Layout
              key={item.docker}
              getLayoutInstance={(obj)=>{
                pgAdmin.Browser.docker[item.docker] = obj;
                obj.eventBus.fireEvent(LAYOUT_EVENTS.INIT);
              }}
              defaultLayout={item.layout}
              layoutId={`Workspace/Layout-${item.workspace}`}
              savedLayout={pgAdmin.Browser.utils.layout[`Workspace/Layout-${item.workspace}`]}
              groups={{
                'playground': item?.tabsData ? getMorePanelGroup(item?.tabsData) : {...getDefaultGroup()},
              }}
              resetToTabPanel={BROWSER_PANELS.MAIN}
              isLayoutVisible={currentWorkspace == item.workspace}
            />
          ))}
        </WorkbenchShell>
      </div>
    </ApplicationStateProvider>
  );
}
Layouts.propTypes = {
  browser: PropTypes.string,
  platform: PropTypes.object.isRequired,
};

export default function BrowserComponent({pgAdmin}) {

  const {isLoading, failed, getPreferencesForModule} = usePreferences();
  let { name: browser } = useMemo(()=>getBrowser(), []);
  const [uiReady, setUiReady] = useState(false);
  const [platform, setPlatform] = useState(null);
  const [platformError, setPlatformError] = useState(null);
  const confirmOnClose = getPreferencesForModule('browser').confirm_on_refresh_close;
  useBeforeUnload({
    enabled: confirmOnClose,
    beforeClose: (forceClose)=>{
      window.electronUI?.focus();
      pgAdmin.Browser.notifier.confirm(
        gettext('Quit CDEadmin'),
        gettext('Are you sure you want to quit the application?'),
        function() { forceClose(); },
        function() { return true; },
        gettext('Yes'),
        gettext('No'),
        'default',
        'id-app-quit'
      );
    },
    isNewTab: true,
  });

  // Called when Install and Restart btn called for auto-update install
  function installUpdate() {
    if (window.electronUI) {
      window.electronUI.sendDataForAppUpdate({
        'install_update_now': true
      });
    }}
  
  // Listen for auto-update events from the Electron main process and display notifications
  // to the user based on the update status (e.g., update available, downloading, downloaded, installed, or error).
  if (window.electronUI && typeof window.electronUI.notifyAppAutoUpdate === 'function') {
    window.electronUI.notifyAppAutoUpdate((data)=>{
      if (data?.check_version_update) {
        pgAdmin.Browser.check_version_update(true);
      } else if (data.update_downloading) {
        appAutoUpdateNotifier('Update downloading.', 'info', null, 10000);
      } else if (data.no_update_available) {
        appAutoUpdateNotifier('No update available.', 'info', null, 10000);
      } else if (data.update_downloaded) {
        const UPDATE_DOWNLOADED_MESSAGE = gettext('An update is ready. Restart the app now to install it, or later to keep using the current version.');
        appAutoUpdateNotifier(UPDATE_DOWNLOADED_MESSAGE, 'warning', installUpdate, null, 'Update downloaded', 'update_downloaded');
      } else if (data.error) {
        appAutoUpdateNotifier(`${data.errMsg}`, 'error');
      } else if (data.update_installed) {
        const UPDATE_INSTALLED_MESSAGE = gettext('Update installed successfully!');
        appAutoUpdateNotifier(UPDATE_INSTALLED_MESSAGE, 'success');
      }
    });
  }

  useEffect(()=>{
    if(uiReady) {
      pgAdmin?.Browser?.uiloaded?.();
    }
  }, [uiReady]);

  useEffect(() => {
    let active = true;
    initializeCDEadminPlatform().then((runtime) => {
      if(active) setPlatform(runtime);
    }).catch((error) => {
      if(active) setPlatformError(error);
    });
    return () => { active = false; };
  }, []);

  if(isLoading) {
    return <></>;
  }
  if(failed) {
    return <>Failed to load preferences</>;
  }
  if(platformError) {
    return <div role="alert">CDEadmin platform initialization failed: {
      platformError.message}</div>;
  }
  if(!platform) return <></>;

  return (
    <PgAdminProvider value={pgAdmin}>
      <WorkspaceProvider>
        <ModalProvider>
          <NotifierProvider pgAdmin={pgAdmin} pgWindow={pgWindow} onReady={()=>setUiReady(true)}/>
          {browser != 'Electron' && <AppMenuBar />}
          <Layouts browser={browser} platform={platform} />
        </ModalProvider>
        <ObjectBreadcrumbs pgAdmin={pgAdmin} />
      </WorkspaceProvider>
    </PgAdminProvider>
  );
}

BrowserComponent.propTypes = {
  pgAdmin: PropTypes.object,
};
