/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {render, screen} from '@testing-library/react';
import {
  GRID_CELL_TYPES,
  BooleanCell,
  CommentCell,
  NumberCell,
  ProgressCell,
  StatusCell,
  gridExportRecords,
  gridLayoutStorageKey,
  inferGridCellType,
  loadGridLayout,
  normalizeGridColumns,
  parseGridData,
  saveGridLayout,
  serializeGridData,
} from 'sources/cdeadmin_ui/data/DataGrid';
import {
  applyGridLayout,
  moveGridColumn,
} from 'sources/cdeadmin_ui/data/gridState';

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
    values,
  };
}

describe('CDEadmin engine-aware grid contract', () => {
  it('infers common engine data types without overriding explicit types', () => {
    expect(inferGridCellType({type: 'BIGINT'})).toBe(GRID_CELL_TYPES.NUMBER);
    expect(inferGridCellType({type: 'jsonb'})).toBe(GRID_CELL_TYPES.JSON);
    expect(inferGridCellType({type: 'timestamp'}))
      .toBe(GRID_CELL_TYPES.DATETIME);
    expect(inferGridCellType({cellType: 'progress', type: 'integer'}))
      .toBe(GRID_CELL_TYPES.PROGRESS);
  });

  it('normalizes grouped columns and enforces read-only grids', () => {
    const columns = normalizeGridColumns([{
      name: 'Identity',
      children: [{key: 'id', name: 'ID', type: 'integer', editable: true}],
    }], {readOnly: true});
    const id = columns[0].children[0];

    expect(id.cellType).toBe('number');
    expect(id.renderCell).toBeDefined();
    expect(id.editable).toBe(false);
    expect(id.renderEditCell).toBeNull();
    expect(id.cellClass).toContain('CDEGrid-cell--readOnly');
  });

  it('persists only versioned order and validated widths', () => {
    const storage = memoryStorage();
    expect(saveGridLayout(storage, 'query/results', {
      order: ['name', 'id'],
      widths: {name: 240, invalid: 2},
      rows: [{password: 'never persist row data'}],
    })).toBe(true);

    const key = gridLayoutStorageKey('query/results');
    const stored = JSON.parse(storage.values.get(key));
    expect(stored).toEqual({
      schemaVersion: 1,
      order: ['name', 'id'],
      widths: {name: 240},
    });
    expect(loadGridLayout(storage, 'query/results')).toEqual(stored);
  });

  it('applies width and order while retaining newly introduced columns', () => {
    const columns = [
      {key: 'id', name: 'ID'},
      {key: 'new_column', name: 'New'},
      {key: 'name', name: 'Name'},
    ];
    const layout = {order: ['name', 'id'], widths: {name: 320}};
    const result = applyGridLayout(columns, layout);

    expect(result.map((column) => column.key))
      .toEqual(['name', 'id', 'new_column']);
    expect(result[0].width).toBe(320);
    expect(moveGridColumn(result, 'id', 'name'))
      .toEqual(['id', 'name', 'new_column']);
  });

  it('exports CSV, TSV and JSON without spreadsheet formula injection', () => {
    const columns = [
      {key: 'name', name: 'Name'},
      {key: 'secret', name: 'Secret', exportable: false},
    ];
    const rows = [{name: '=CMD()', secret: 'hidden'}];

    expect(gridExportRecords(columns, rows)).toEqual([{name: '\'=CMD()'}]);
    expect(serializeGridData(columns, rows, 'csv'))
      .toContain('\'=CMD()');
    expect(serializeGridData(columns, rows, 'tsv')).not.toContain('hidden');
    expect(JSON.parse(serializeGridData(columns, rows, 'json')))
      .toEqual([{name: '\'=CMD()'}]);
  });

  it('preserves arbitrary-precision numeric text and supports export values', () => {
    const precise = '99999999999999999999.12345678901234567890';
    render(<NumberCell column={{key: 'amount'}} row={{amount: precise}} />);

    expect(screen.getByText(precise)).toBeInTheDocument();
    expect(gridExportRecords([{
      key: 'amount',
      exportValue: (value) => `decimal:${value}`,
    }], [{amount: precise}])).toEqual([{
      amount: `decimal:${precise}`,
    }]);
  });

  it('imports structured CSV and rejects non-tabular JSON', () => {
    const parsed = parseGridData('id,name\n1,Ada\n2,Grace', 'csv');
    expect(parsed.fields).toEqual(['id', 'name']);
    expect(parsed.rows).toEqual([
      {id: '1', name: 'Ada'},
      {id: '2', name: 'Grace'},
    ]);
    expect(() => parseGridData('{"id": 1}', 'json'))
      .toThrow('array of objects');
  });

  it('renders accessible native boolean, progress, status and comments', () => {
    const rows = {
      enabled: true,
      percent: 25,
      status: 'online',
      value: 'engine',
      note: 'Provided by the engine',
    };
    render(<>
      <BooleanCell column={{key: 'enabled'}} row={rows} />
      <ProgressCell column={{key: 'percent'}} row={rows} />
      <StatusCell column={{
        key: 'status', statusMap: {online: {label: 'Online', tone: 'success'}},
      }} row={rows} />
      <CommentCell column={{key: 'value', commentKey: 'note'}} row={rows} />
    </>);

    expect(screen.getByRole('checkbox', {name: 'True'})).toBeChecked();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25');
    expect(screen.getByText('Online')).toBeInTheDocument();
    expect(screen.getByTitle('Provided by the engine')).toBeInTheDocument();
  });
});
