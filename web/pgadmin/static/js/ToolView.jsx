/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import { useEffect, useLayoutEffect, useRef } from 'react';
import ReactDOM from 'react-dom/client';
import { usePgAdmin } from './PgAdminProvider';
import { BROWSER_PANELS } from '../../browser/static/js/constants';
import PropTypes from 'prop-types';
import LayoutIframeTab from './helpers/Layout/LayoutIframeTab';
import { LAYOUT_EVENTS } from './helpers/Layout';
import { useApplicationState } from '../../settings/static/ApplicationStateProvider';
import {
  createToolDescriptor,
  TOOL_KINDS,
  toolKindFromPanelId,
} from './cdeadmin_ui/workspace/ToolDescriptor';
import {toolFactoryRegistry} from './cdeadmin_ui/workspace/ToolRegistry';


function ToolForm({actionUrl, params}) {
  const formRef = useRef(null);

  useLayoutEffect(()=>{
    formRef.current?.submit();
  }, []);

  return (
    <form ref={formRef} id="tool-form" action={actionUrl} method="post" hidden>
      {Object.keys(params).map((k)=>{
        return k ? <textarea key={k} name={k} defaultValue={params[k]} /> : <></>;
      })}
    </form>
  );
}

ToolForm.propTypes = {
  actionUrl: PropTypes.string,
  params: PropTypes.object,
};

const TOOL_ICON_KEYS = Object.freeze({
  [TOOL_KINDS.QUERY_EDITOR]: 'tool.query',
  [TOOL_KINDS.NATIVE_TERMINAL]: 'tool.query',
  [TOOL_KINDS.ERD]: 'tool.erd',
  [TOOL_KINDS.SCHEMA_DIFF]: 'tool.dataflow',
  [TOOL_KINDS.DEBUGGER]: 'tool.query',
  [TOOL_KINDS.GENERIC]: 'tool.query',
});

function registerIframeToolFactories() {
  const definitions = [
    [TOOL_KINDS.QUERY_EDITOR, true, true, true],
    [TOOL_KINDS.NATIVE_TERMINAL, true, false, true],
    [TOOL_KINDS.ERD, true, true, true],
    [TOOL_KINDS.SCHEMA_DIFF, true, true, true],
    [TOOL_KINDS.DEBUGGER, false, false, true],
    [TOOL_KINDS.GENERIC, false, false, false],
  ];
  definitions.forEach(([toolKind, detachable, duplicable,
    requiresLiveSession])=>{
    if(toolFactoryRegistry.has(toolKind)) return;
    toolFactoryRegistry.register({
      toolKind,
      detachable,
      duplicable,
      requiresLiveSession,
      restore: (descriptor, context)=>{
        if(!context.toolUrl) {
          throw new Error(`Restore URL is unavailable for ${descriptor.toolInstanceId}.`);
        }
        return getToolTabParams(
          descriptor.toolInstanceId,
          context.toolUrl,
          context.formParams,
          context.tabParams,
          true,
          descriptor
        );
      },
    });
  });
}

export function getToolTabParams(panelId, toolUrl, formParams, tabParams,
  restore=false, suppliedDescriptor=null) {
  if(tabParams?.internal?.orig_title){
    tabParams.title = tabParams.internal.isDirty ? tabParams.internal.title.slice(0, -1): tabParams.internal.title;
  }

  const toolKind = toolKindFromPanelId(panelId);
  const capabilities = toolFactoryRegistry.capabilities(toolKind);
  const toolDescriptor = createToolDescriptor(suppliedDescriptor ?? {
    toolInstanceId: panelId,
    toolKind,
    restoreRef: panelId,
    presentation: {
      title: tabParams?.title ?? panelId,
      iconKey: TOOL_ICON_KEYS[toolKind],
    },
    placement: {
      workspaceId: tabParams?.workSpace ?? 'default',
      windowId: 'main',
      dockArea: 'main',
      revision: 0,
    },
    state: {
      dirty: Boolean(tabParams?.internal?.isDirty),
      transactionState: 'unknown',
      connectionState: 'unknown',
    },
    capabilities,
  });

  return {
    id: panelId,
    title: panelId,
    content: (
      <LayoutIframeTab target={panelId} src={formParams ? undefined : toolUrl}>
        {formParams && <ToolForm actionUrl={toolUrl} params={{...formParams, restore:restore, workSpace: tabParams?.workSpace }}/>}
      </LayoutIframeTab>
    ),
    closable: true,
    manualClose: true,
    detachable: tabParams?.detachable ?? toolDescriptor.capabilities.detachable,
    toolDescriptor,
    ...tabParams,
    cache: false,
    group: 'playground',
    metaData: {
      toolUrl: toolUrl,
      formParams: formParams,
      tabParams: tabParams,
      toolDescriptor,
    },
  };
}

registerIframeToolFactories();

export default function ToolView({dockerObj}) {
  const pgAdmin = usePgAdmin();
  const { deleteToolData } = useApplicationState();

  useEffect(()=>{
    pgAdmin.Browser.Events.on('pgadmin:tool:show', (panelId, toolUrl, formParams, tabParams, newTab)=>{
      if(newTab) {
        if(formParams) {
          const newWin = window.open('', '_blank');
          const div = newWin.document.createElement('div');
          newWin.document.body.appendChild(div);
          const root = ReactDOM.createRoot(div);
          root.render(
            <ToolForm actionUrl={window.location.origin+toolUrl} params={formParams}/>, div
          );
        } else {
          window.open(toolUrl);
        }
      } else {
        // Handler here will return which layout instance the tool should go in
        // case of workspace layout.
        let handler = pgAdmin.Browser.getDockerHandler?.(panelId, dockerObj);
        const deregisterRemove = handler.docker.eventBus.registerListener(LAYOUT_EVENTS.REMOVE, (closePanelId)=>{
          if(panelId == closePanelId){
            deleteToolData(panelId);
            deregisterRemove();
          }
        });

        handler.focus();
        handler.docker.openTab(
          getToolTabParams(panelId, toolUrl, formParams, tabParams),
          BROWSER_PANELS.MAIN, 'middle', true
        );
      }
    });
  }, []);
  return <></>;
}
ToolView.propTypes = {
  dockerObj: PropTypes.object
};
