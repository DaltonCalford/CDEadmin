import { useRef } from 'react';
import PropTypes from 'prop-types';
export * from 'react-data-grid';

export const DataGrid = (
  {
    ref: _ref,
    columns=[],
    rows=[],
    ...props
  }
) => {
  const ele = useRef();
  return <div
    id={props.id}
    ref={ele}
    data-test="react-data-grid"
    data-row-height={props.rowHeight}
    data-header-row-height={props.headerRowHeight}
    role="grid"
    aria-label={props['aria-label']}
  >
    <div role="row">
      {columns.map((column) => <div role="columnheader" key={column.key}>
        {column.name}
      </div>)}
    </div>
    {rows.map((row, rowIdx) => <div role="row"
      key={props.rowKeyGetter?.(row) ?? rowIdx}>
      {columns.map((column) => <div role="gridcell" key={column.key}>
        {column.renderCell ? column.renderCell({row, column, rowIdx}) :
          (typeof row?.[column.key] === 'object' ?
            JSON.stringify(row[column.key], null, 2) :
            String(row?.[column.key] ?? ''))}
      </div>)}
    </div>)}
  </div>;
};

DataGrid.displayName = 'DataGrid';
DataGrid.propTypes = {
  id: PropTypes.any,
  columns: PropTypes.array,
  rows: PropTypes.array,
};
