/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';

export const GRID_CELL_TYPES = Object.freeze({
  TEXT: 'text',
  NUMBER: 'number',
  BOOLEAN: 'boolean',
  DATE: 'date',
  DATETIME: 'datetime',
  JSON: 'json',
  PROGRESS: 'progress',
  STATUS: 'status',
  COMMENT: 'comment',
});

function cellValue({column, row}) {
  return row?.[column.key];
}

function displayValue(value, nullText='NULL') {
  if(value === null) {
    return <span className='CDEGrid-null'>{nullText}</span>;
  }
  if(value === undefined) {
    return '';
  }
  return String(value);
}

export function TextCell(props) {
  const value = cellValue(props);
  const formatted = props.column.valueFormatter?.(value, props.row);
  return <span title={value == null ? '' : String(value)}>
    {displayValue(formatted ?? value, props.column.nullText)}
  </span>;
}

export function NumberCell(props) {
  const value = cellValue(props);
  if(value === null || value === undefined || value === '') {
    return displayValue(value, props.column.nullText);
  }
  const formatted = props.column.valueFormatter?.(value, props.row);
  if(formatted !== undefined && formatted !== null) {
    return <span title={String(value)}>{String(formatted)}</span>;
  }
  if(typeof value !== 'number' && typeof value !== 'bigint') {
    return <span title={String(value)}>{String(value)}</span>;
  }
  if(typeof value === 'number' && !Number.isFinite(value)) {
    return <span title={String(value)}>{String(value)}</span>;
  }
  const formatter = new Intl.NumberFormat(
    props.column.locale,
    props.column.formatOptions
  );
  return <span title={String(value)}>{formatter.format(value)}</span>;
}

export function BooleanCell(props) {
  const value = cellValue(props);
  if(value === null || value === undefined) {
    return displayValue(value, props.column.nullText);
  }
  const checked = value === true || value === 1 ||
    ['true', '1', 't', 'yes', 'on'].includes(String(value).toLowerCase());
  return <input
    type='checkbox'
    checked={checked}
    readOnly
    tabIndex={-1}
    aria-label={checked ? 'True' : 'False'}
    className='CDEGrid-boolean'
  />;
}

export function DateTimeCell(props) {
  const value = cellValue(props);
  if(value === null || value === undefined || value === '') {
    return displayValue(value, props.column.nullText);
  }
  const date = value instanceof Date ? value : new Date(value);
  if(Number.isNaN(date.valueOf())) {
    return <span title={String(value)}>{String(value)}</span>;
  }
  const type = props.column.cellType || props.column.dataType;
  const options = props.column.formatOptions || (type === GRID_CELL_TYPES.DATE ?
    {year: 'numeric', month: '2-digit', day: '2-digit'} :
    {dateStyle: 'short', timeStyle: 'medium'});
  return <time dateTime={date.toISOString()}>
    {new Intl.DateTimeFormat(props.column.locale, options).format(date)}
  </time>;
}

export function JsonCell(props) {
  const value = cellValue(props);
  if(value === null || value === undefined) {
    return displayValue(value, props.column.nullText);
  }
  let formatted;
  try {
    formatted = typeof value === 'string' ? value : JSON.stringify(value);
  } catch {
    formatted = String(value);
  }
  return <code title={formatted}>{formatted}</code>;
}

export function ProgressCell(props) {
  const rawValue = Number(cellValue(props));
  const first = Number(props.column.minimum ?? 0);
  const second = Number(props.column.maximum ?? 100);
  const minimum = Math.min(first, second);
  const maximum = Math.max(first, second);
  const value = Number.isFinite(rawValue) ?
    Math.min(maximum, Math.max(minimum, rawValue)) : minimum;
  const range = maximum - minimum;
  const percent = range > 0 ? ((value - minimum) / range) * 100 : 0;
  const label = props.column.valueFormatter?.(value, props.row) ??
    `${Math.round(percent)}%`;
  return <div
    className='CDEGrid-progress'
    role='progressbar'
    aria-valuemin={minimum}
    aria-valuemax={maximum}
    aria-valuenow={value}
    aria-valuetext={String(label)}
  >
    <span className='CDEGrid-progressTrack' aria-hidden='true'>
      <span style={{width: `${percent}%`}} />
    </span>
    <span className='CDEGrid-progressLabel'>{label}</span>
  </div>;
}

function safeTone(tone) {
  return ['success', 'warning', 'error', 'info', 'neutral'].includes(tone) ?
    tone : 'neutral';
}

export function StatusCell(props) {
  const value = cellValue(props);
  const status = props.column.statusMap?.[value] || {};
  const tone = safeTone(status.tone || props.column.tone || 'neutral');
  const label = status.label ?? displayValue(value, props.column.nullText);
  return <span className={`CDEGrid-status CDEGrid-status--${tone}`}>
    <span className='CDEGrid-statusMarker' aria-hidden='true' />
    {label}
  </span>;
}

export function CommentCell(props) {
  const value = cellValue(props);
  const comment = props.column.commentGetter?.(props.row, value) ??
    props.row?.[props.column.commentKey];
  return <span
    className={comment ? 'CDEGrid-comment CDEGrid-comment--present' :
      'CDEGrid-comment'}
    title={comment || (value == null ? '' : String(value))}
  >
    {displayValue(value, props.column.nullText)}
  </span>;
}

const CELL_RENDERERS = Object.freeze({
  [GRID_CELL_TYPES.TEXT]: TextCell,
  [GRID_CELL_TYPES.NUMBER]: NumberCell,
  [GRID_CELL_TYPES.BOOLEAN]: BooleanCell,
  [GRID_CELL_TYPES.DATE]: DateTimeCell,
  [GRID_CELL_TYPES.DATETIME]: DateTimeCell,
  [GRID_CELL_TYPES.JSON]: JsonCell,
  [GRID_CELL_TYPES.PROGRESS]: ProgressCell,
  [GRID_CELL_TYPES.STATUS]: StatusCell,
  [GRID_CELL_TYPES.COMMENT]: CommentCell,
});

export function rendererForCellType(cellType) {
  return CELL_RENDERERS[cellType] || CELL_RENDERERS[GRID_CELL_TYPES.TEXT];
}

const cellPropTypes = {
  column: PropTypes.object.isRequired,
  row: PropTypes.object.isRequired,
};

[TextCell, NumberCell, BooleanCell, DateTimeCell, JsonCell, ProgressCell,
  StatusCell, CommentCell].forEach((component) => {
  component.propTypes = cellPropTypes;
});
