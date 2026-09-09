/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

export const TREE_NODE_KINDS = Object.freeze({
  ENGINE: 'engine',
  SERVER: 'server',
  INSTANCE: 'instance',
  DATABASE: 'database',
  CONTAINER: 'container',
  OBJECT: 'object',
});

const KNOWN_KINDS = new Set(Object.values(TREE_NODE_KINDS));

function text(value, fallback='') {
  return value === null || value === undefined ? fallback : String(value);
}

export function createTreeNodeDescriptor(input={}) {
  const id = text(input.id).trim();
  const label = text(input.label).trim();
  if(!id) {
    throw new TypeError('Tree node id is required.');
  }
  if(!label) {
    throw new TypeError('Tree node label is required.');
  }

  const kind = KNOWN_KINDS.has(input.kind) ?
    input.kind : TREE_NODE_KINDS.OBJECT;
  const requestedCheckType = text(input.check?.type, 'none').toLowerCase();
  const checkType = ['checkbox', 'radio'].includes(requestedCheckType) ?
    requestedCheckType : 'none';
  return Object.freeze({
    schemaVersion: 1,
    id,
    label,
    kind,
    objectType: text(input.objectType, kind),
    providerId: text(input.providerId),
    engineId: text(input.engineId),
    engineFamily: text(input.engineFamily),
    status: text(input.status),
    description: text(input.description),
    infoLabel: text(input.infoLabel),
    iconKey: text(input.iconKey, input.objectType || kind),
    childCount: Number.isFinite(Number(input.childCount)) ?
      Math.max(0, Number(input.childCount)) : 0,
    check: Object.freeze({
      type: checkType,
      checked: Boolean(input.check?.checked),
      indeterminate: checkType === 'checkbox' &&
        Boolean(input.check?.indeterminate),
      disabled: Boolean(input.check?.disabled),
      group: text(input.check?.group),
      label: text(input.check?.label, label),
    }),
    capabilities: Object.freeze({
      enabled: input.capabilities?.enabled !== false,
      expandable: Boolean(input.capabilities?.expandable),
      draggable: input.capabilities?.draggable !== false,
      editable: Boolean(input.capabilities?.editable),
      removable: Boolean(input.capabilities?.removable),
    }),
  });
}

export function descriptorFromTreeMetadata(data={}, childCount=0) {
  const objectType = text(data.object_type || data._type, 'object');
  const kind = Object.values(TREE_NODE_KINDS).includes(data.node_kind) ?
    data.node_kind :
    objectType === 'engine' || objectType === 'engine_type' ?
      TREE_NODE_KINDS.ENGINE :
      Object.values(TREE_NODE_KINDS).includes(objectType) ?
        objectType : TREE_NODE_KINDS.OBJECT;
  const rawCheckType = text(
    data.check_type ?? data.checkType ?? data.check?.type,
    data.checkable ? 'checkbox' : 'none'
  ).toLowerCase();
  const checked = data.checked === true || data._checked === true ||
    data.check_state === 'checked' || data.check?.checked === true;
  const indeterminate = data.indeterminate === true ||
    data.check_state === 'mixed' || data.check?.indeterminate === true;

  return createTreeNodeDescriptor({
    id: data.id ?? data._id ?? `${objectType}:${data._label}`,
    label: data._label ?? data.label ?? objectType,
    kind,
    objectType,
    providerId: data.provider_id,
    engineId: data.engine_id ?? data.cde_engine_id,
    engineFamily: data.engine_family,
    status: data.connection_status ?? data.status,
    description: data.description,
    infoLabel: data.info_label,
    iconKey: data.icon_key ?? data.icon,
    childCount,
    check: {
      type: rawCheckType,
      checked,
      indeterminate,
      disabled: data.check_disabled === true || data.check?.disabled === true,
      group: data.check_group ?? data.check?.group,
      label: data.check_label ?? data.check?.label,
    },
    capabilities: {
      enabled: data.disabled !== true,
      expandable: Boolean(data._type?.startsWith('coll-') || data.expandable),
      draggable: data.draggable !== false,
      editable: Boolean(data.editable),
      removable: Boolean(data.removable),
    },
  });
}

export function treeNodeAriaLabel(descriptor) {
  const parts = [descriptor.label, descriptor.objectType];
  if(descriptor.infoLabel) parts.push(descriptor.infoLabel);
  if(descriptor.status) parts.push(descriptor.status);
  if(descriptor.childCount) {
    parts.push(`${descriptor.childCount} children`);
  }
  return parts.filter(Boolean).join(', ');
}
