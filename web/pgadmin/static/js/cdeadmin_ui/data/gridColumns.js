/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {
  GRID_CELL_TYPES,
  rendererForCellType,
} from './GridCellRenderers';

const NUMERIC_TYPES = new Set([
  'bigint', 'decimal', 'double', 'float', 'int', 'integer', 'number',
  'numeric', 'real', 'smallint',
]);
const BOOLEAN_TYPES = new Set(['bool', 'boolean']);
const JSON_TYPES = new Set(['json', 'jsonb', 'document', 'object']);
const DATE_TYPES = new Set(['date']);
const DATETIME_TYPES = new Set([
  'datetime', 'timestamp', 'timestamp with time zone', 'timestamptz',
]);

export function inferGridCellType(column={}) {
  const explicit = column.cellType || column.dataType;
  if(explicit && Object.values(GRID_CELL_TYPES).includes(explicit)) {
    return explicit;
  }
  const engineType = String(column.engineType || column.type || '')
    .trim().toLowerCase();
  if(NUMERIC_TYPES.has(engineType)) return GRID_CELL_TYPES.NUMBER;
  if(BOOLEAN_TYPES.has(engineType)) return GRID_CELL_TYPES.BOOLEAN;
  if(JSON_TYPES.has(engineType)) return GRID_CELL_TYPES.JSON;
  if(DATE_TYPES.has(engineType)) return GRID_CELL_TYPES.DATE;
  if(DATETIME_TYPES.has(engineType)) return GRID_CELL_TYPES.DATETIME;
  return GRID_CELL_TYPES.TEXT;
}
function appendClass(existing, additional) {
  if(typeof existing === 'function') {
    return (row) => [existing(row), additional].filter(Boolean).join(' ');
  }
  return [existing, additional].filter(Boolean).join(' ');
}

export function normalizeGridColumns(columns=[], {readOnly=false}={}) {
  return columns.map((column) => {
    if(Array.isArray(column.children)) {
      return {
        ...column,
        children: normalizeGridColumns(column.children, {readOnly}),
      };
    }
    const cellType = inferGridCellType(column);
    const isReadOnly = readOnly || column.readOnly === true;
    const normalized = {
      ...column,
      cellType,
      renderCell: column.renderCell || rendererForCellType(cellType),
      cellClass: appendClass(column.cellClass, [
        `CDEGrid-cell--${cellType}`,
        isReadOnly ? 'CDEGrid-cell--readOnly' : '',
      ].filter(Boolean).join(' ')),
    };
    if(isReadOnly) {
      normalized.editable = false;
      normalized.renderEditCell = null;
    }
    return normalized;
  });
}
