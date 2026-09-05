/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

export const TOOL_DESCRIPTOR_SCHEMA = 'cdeadmin.tool-instance.v1';

export const TOOL_KINDS = Object.freeze({
  QUERY_EDITOR: 'query_editor',
  NATIVE_TERMINAL: 'native_terminal',
  ERD: 'erd',
  SCHEMA_DIFF: 'schema_diff',
  DEBUGGER: 'debugger',
  GENERIC: 'generic_tool',
});

export const TOOL_PLACEMENT_MODES = Object.freeze({
  DOCKED: 'docked',
  FLOATING: 'floating',
  DETACHED: 'detached',
});

const VALID_PLACEMENTS = new Set(Object.values(TOOL_PLACEMENT_MODES));
const SAFE_ID = /^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,255}$/;
const FORBIDDEN_KEY = /(?:password|passwd|secret|credential|private.?key|access.?token|refresh.?token|csrf|query|sql|document.?content|form.?params|tool.?url)/i;

function text(value, fallback='') {
  return value === null || value === undefined ? fallback : String(value);
}

function safeId(value, label, {required=false}={}) {
  const normalized = text(value).trim();
  if(!normalized && !required) return '';
  if(!SAFE_ID.test(normalized)) {
    throw new TypeError(`${label} must be a stable opaque identifier.`);
  }
  return normalized;
}

function optionalInteger(value, label, fallback=null) {
  if(value === null || value === undefined || value === '') return fallback;
  const normalized = Number(value);
  if(!Number.isSafeInteger(normalized) || normalized < 0) {
    throw new TypeError(`${label} must be a non-negative integer.`);
  }
  return normalized;
}

export function assertSecretFreeToolValue(value, path='descriptor') {
  if(value === null || value === undefined) return;
  if(Array.isArray(value)) {
    value.forEach((item, index)=>assertSecretFreeToolValue(
      item, `${path}[${index}]`
    ));
    return;
  }
  if(typeof value !== 'object') return;
  Object.entries(value).forEach(([key, child])=>{
    if(FORBIDDEN_KEY.test(key)) {
      throw new TypeError(`Sensitive field is forbidden in tool descriptors: ${path}.${key}`);
    }
    assertSecretFreeToolValue(child, `${path}.${key}`);
  });
}

export function createToolDescriptor(input={}) {
  assertSecretFreeToolValue(input);
  const toolInstanceId = safeId(
    input.toolInstanceId, 'Tool instance ID', {required: true}
  );
  const toolKind = safeId(input.toolKind, 'Tool kind', {required: true});
  const placementMode = VALID_PLACEMENTS.has(input.placement?.mode) ?
    input.placement.mode : TOOL_PLACEMENT_MODES.DOCKED;
  const descriptor = {
    schema: TOOL_DESCRIPTOR_SCHEMA,
    schemaVersion: 1,
    toolInstanceId,
    toolKind,
    restoreRef: safeId(
      input.restoreRef ?? toolInstanceId, 'Tool restore reference',
      {required: true}
    ),
    projectId: safeId(input.projectId, 'Project ID'),
    context: Object.freeze({
      providerId: safeId(input.context?.providerId, 'Provider ID'),
      endpointId: optionalInteger(input.context?.endpointId, 'Endpoint ID'),
      routeId: safeId(input.context?.routeId, 'Route ID'),
      instanceId: safeId(input.context?.instanceId, 'Instance ID'),
      databaseTargetId: optionalInteger(
        input.context?.databaseTargetId, 'Database target ID'
      ),
      sessionHandle: safeId(input.context?.sessionHandle, 'Session handle'),
      resultHandle: safeId(input.context?.resultHandle, 'Result handle'),
      operationHandle: safeId(
        input.context?.operationHandle, 'Operation handle'
      ),
    }),
    presentation: Object.freeze({
      title: text(input.presentation?.title, toolKind),
      iconKey: text(input.presentation?.iconKey, 'tool.query'),
    }),
    placement: Object.freeze({
      mode: placementMode,
      workspaceId: safeId(
        input.placement?.workspaceId ?? 'default', 'Workspace ID',
        {required: true}
      ),
      windowId: safeId(
        input.placement?.windowId ?? 'main', 'Window ID', {required: true}
      ),
      dockArea: safeId(
        input.placement?.dockArea ?? 'main', 'Dock area', {required: true}
      ),
      tabOrder: optionalInteger(input.placement?.tabOrder, 'Tab order', 0),
      revision: optionalInteger(input.placement?.revision, 'Placement revision', 0),
    }),
    state: Object.freeze({
      dirty: Boolean(input.state?.dirty),
      transactionState: text(input.state?.transactionState, 'unknown'),
      connectionState: text(input.state?.connectionState, 'unknown'),
      sharedSession: Boolean(input.state?.sharedSession),
    }),
    capabilities: Object.freeze({
      detachable: Boolean(input.capabilities?.detachable),
      duplicable: Boolean(input.capabilities?.duplicable),
      requiresLiveSession: Boolean(input.capabilities?.requiresLiveSession),
    }),
  };
  return Object.freeze(descriptor);
}

const PANEL_KIND_PREFIXES = Object.freeze([
  ['id-query-tool_', TOOL_KINDS.QUERY_EDITOR],
  ['id-psql-tool_', TOOL_KINDS.NATIVE_TERMINAL],
  ['id-erd-tool_', TOOL_KINDS.ERD],
  ['id-schema-diff-tool_', TOOL_KINDS.SCHEMA_DIFF],
  ['id-debugger-tool_', TOOL_KINDS.DEBUGGER],
]);

export function toolKindFromPanelId(panelId) {
  const id = text(panelId);
  return PANEL_KIND_PREFIXES.find(([prefix])=>id.startsWith(prefix))?.[1] ??
    TOOL_KINDS.GENERIC;
}
