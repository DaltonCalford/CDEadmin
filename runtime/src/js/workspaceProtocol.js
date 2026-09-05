/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

const SAFE_ID = /^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,255}$/;
const PLACEMENTS = new Set([
  'before-tab', 'after-tab', 'middle', 'left', 'right', 'top', 'bottom',
  'float', 'new-window', 'maximize',
]);

function safeId(value, label, required=true) {
  const normalized = String(value ?? '').trim();
  if((!normalized && required) || (normalized && !SAFE_ID.test(normalized))) {
    throw new TypeError(`${label} is invalid.`);
  }
  return normalized;
}

export function validateWindowRegistration(value={}) {
  return Object.freeze({
    windowId: safeId(value.windowId, 'Window ID', false),
    workspaceId: safeId(value.workspaceId, 'Workspace ID'),
    role: ['main', 'workspace', 'detached-tool'].includes(value.role) ?
      value.role : 'workspace',
  });
}

export function validatePlacementEvent(value={}) {
  if(value.schema !== 'cdeadmin.workspace-placement-event.v1') {
    throw new TypeError('Workspace placement event schema is invalid.');
  }
  const placement = String(value.placement ?? '');
  if(!PLACEMENTS.has(placement)) {
    throw new TypeError('Workspace placement is invalid.');
  }
  const revision = Number(value.revision);
  if(!Number.isSafeInteger(revision) || revision < 0) {
    throw new TypeError('Workspace placement revision is invalid.');
  }
  return Object.freeze({
    schema: value.schema,
    toolInstanceId: safeId(value.toolInstanceId, 'Tool instance ID'),
    toolKind: safeId(value.toolKind, 'Tool kind'),
    placement,
    revision,
  });
}

export function publicDisplay(display, primaryDisplayId) {
  const area = display.workArea;
  return Object.freeze({
    id: String(display.id),
    label: display.label || `Display ${display.id}`,
    primary: display.id === primaryDisplayId,
    scaleFactor: Number(display.scaleFactor),
    workArea: Object.freeze({
      x: Number(area.x),
      y: Number(area.y),
      width: Number(area.width),
      height: Number(area.height),
    }),
  });
}
