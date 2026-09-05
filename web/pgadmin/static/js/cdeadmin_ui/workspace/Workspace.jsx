/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

export {
  default as Workspace,
  getDefaultGroup,
  LayoutDocker as WorkspaceController,
  LayoutDockerContext as WorkspaceContext,
  LAYOUT_EVENTS as WORKSPACE_EVENTS,
  TabTitle as ToolTabTitle,
  WORKSPACE_PLACEMENTS,
} from '../../helpers/Layout';

export {
  assertSecretFreeToolValue,
  createToolDescriptor,
  TOOL_DESCRIPTOR_SCHEMA,
  TOOL_KINDS,
  TOOL_PLACEMENT_MODES,
  toolKindFromPanelId,
} from './ToolDescriptor';
export {ToolFactoryRegistry, toolFactoryRegistry} from './ToolRegistry';
export {
  createWorkspaceHost,
  WorkspaceHost,
  WORKSPACE_HOST_MODES,
} from './WorkspaceHost';
