/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

export const GRID_LAYOUT_SCHEMA_VERSION = 1;
const GRID_LAYOUT_PREFIX = 'cdeadmin.grid.layout.v1.';
const UNSAFE_KEYS = new Set(['__proto__', 'prototype', 'constructor']);

function safeKey(key) {
  return typeof key === 'string' && key.length > 0 && key.length <= 512 &&
    !UNSAFE_KEYS.has(key);
}

function childrenOf(column) {
  return Array.isArray(column?.children) ? column.children : null;
}

export function columnKeys(columns=[]) {
  return columns.flatMap((column) => {
    const children = childrenOf(column);
    return children ? columnKeys(children) : [column.key];
  }).filter(safeKey);
}

function sanitizeLayout(value) {
  if(!value || value.schemaVersion !== GRID_LAYOUT_SCHEMA_VERSION ||
      !Array.isArray(value.order) || typeof value.widths !== 'object' ||
      value.widths === null) {
    return null;
  }
  const order = [...new Set(value.order.filter(safeKey))];
  const widths = Object.entries(value.widths).reduce((result, [key, width]) => {
    if(safeKey(key) && Number.isFinite(width) && width >= 20 &&
        width <= 10000) {
      result[key] = width;
    }
    return result;
  }, {});
  return {schemaVersion: GRID_LAYOUT_SCHEMA_VERSION, order, widths};
}

export function gridLayoutStorageKey(gridId) {
  if(typeof gridId !== 'string' || !gridId.trim() || gridId.length > 512) {
    return null;
  }
  return `${GRID_LAYOUT_PREFIX}${encodeURIComponent(gridId.trim())}`;
}

export function loadGridLayout(storage, gridId) {
  const key = gridLayoutStorageKey(gridId);
  if(!storage || !key) return null;
  try {
    return sanitizeLayout(JSON.parse(storage.getItem(key)));
  } catch {
    return null;
  }
}

export function saveGridLayout(storage, gridId, layout) {
  const key = gridLayoutStorageKey(gridId);
  const safeLayout = sanitizeLayout({
    schemaVersion: GRID_LAYOUT_SCHEMA_VERSION,
    order: layout?.order || [],
    widths: layout?.widths || {},
  });
  if(!storage || !key || !safeLayout) return false;
  try {
    storage.setItem(key, JSON.stringify(safeLayout));
    return true;
  } catch {
    return false;
  }
}

export function clearGridLayout(storage, gridId) {
  const key = gridLayoutStorageKey(gridId);
  if(!storage || !key) return false;
  try {
    storage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

function firstRank(column, ranks) {
  const keys = columnKeys([column]);
  return keys.reduce((rank, key) => Math.min(rank, ranks.get(key) ?? Infinity),
    Infinity);
}

export function applyGridLayout(columns=[], layout) {
  if(!layout) return columns;
  const ranks = new Map(layout.order.map((key, index) => [key, index]));
  const decorate = (items) => items.map((column, originalIndex) => {
    const children = childrenOf(column);
    const next = children ? {...column, children: decorate(children)} : {
      ...column,
      ...(layout.widths[column.key] ? {width: layout.widths[column.key]} : {}),
    };
    return {column: next, originalIndex};
  }).sort((left, right) => {
    const leftRank = firstRank(left.column, ranks);
    const rightRank = firstRank(right.column, ranks);
    if(leftRank === rightRank) return left.originalIndex - right.originalIndex;
    return leftRank - rightRank;
  }).map(({column}) => column);
  return decorate(columns);
}

export function moveGridColumn(columns=[], sourceKey, targetKey) {
  const order = columnKeys(columns);
  const sourceIndex = order.indexOf(sourceKey);
  const targetIndex = order.indexOf(targetKey);
  if(sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) {
    return order;
  }
  order.splice(sourceIndex, 1);
  order.splice(targetIndex, 0, sourceKey);
  return order;
}
