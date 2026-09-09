/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////
import React, {
  useCallback, useContext, useEffect, useMemo, useState,
} from 'react';
import { DataGrid, Row } from 'react-data-grid';
import { Box, useTheme } from '@mui/material';
import PropTypes from 'prop-types';
import CustomPropTypes from '../custom_prop_types';
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import gettext from 'sources/gettext';
import { styled } from '@mui/material/styles';
import {normalizeGridColumns} from 'sources/cdeadmin_ui/data/gridColumns';
import {
  applyGridLayout, columnKeys, loadGridLayout, moveGridColumn, saveGridLayout,
} from 'sources/cdeadmin_ui/data/gridState';

const StyledReactDataGrid = styled(DataGrid)(({theme})=>({
  '&.ReactGrid-root': {
    height: '100%',
    color: theme.palette.text.primary,
    backgroundColor: theme.otherVars?.qtDatagridBg ||
      theme.palette.background.default,
    fontFamily: 'inherit',
    fontSize: 'inherit',
    border: 'none',
    userSelect: 'none',
    '--rdg-selection-color': theme.palette.primary.main,
    '--rdg-selection-width': 'var(--cde-focus-width, 2px)',
    '--rdg-border-color': 'var(--cde-color-border)',
    '--rdg-border-width': '1px',
    '--rdg-background-color': theme.otherVars?.qtDatagridBg ||
      theme.palette.background.default,
    '--rdg-header-background-color': theme.otherVars?.headerBg ||
      theme.palette.background.paper,
    '--rdg-row-hover-background-color': theme.palette.action?.hover,
    '--rdg-row-selected-background-color': theme.palette.primary.light,
    '--rdg-cell-frozen-box-shadow':
      `3px 0 6px -3px ${theme.otherVars?.borderColor || theme.palette.divider}`,
    '& .rdg-cell': {
      paddingInline: 'var(--cde-grid-cell-padding, 8px)',
      fontWeight: 'normal',
      whiteSpace: 'pre',
      minWidth: 0,
      '.ReactGrid-hasSelectColumn &[aria-colindex="1"]': {
        padding: 0,
      },
      '&[aria-selected=true]:not([aria-colindex="1"]):not([role="columnheader"])': {
        outlineWidth: '0px',
        outlineOffset: '0px',
      },
      '& .rdg-cell-value': {
        height: '100%',
      },
      '&.CDEGrid-cell--number': {
        textAlign: 'end',
        fontVariantNumeric: 'tabular-nums',
      },
      '&.CDEGrid-cell--json code': {
        fontFamily: theme.typography.fontFamilySourceCode,
      },
      '&.CDEGrid-cell--readOnly': {
        backgroundImage: `linear-gradient(135deg, transparent 0,
          transparent 48%, ${theme.otherVars?.borderColor || theme.palette.divider} 49%,
          ${theme.otherVars?.borderColor || theme.palette.divider} 51%, transparent 52%, transparent)`,
        backgroundSize: '8px 8px',
        backgroundPosition: 'right top',
        backgroundRepeat: 'no-repeat',
      },
      '&.rdg-cell-copied[aria-selected=false][role="gridcell"]': {
        backgroundColor: 'inherit',
      }
    },
    '& .rdg-header-row .rdg-cell': {
      padding: 0,

      '& .rdg-header-sort-name': {
        margin: 'auto 0',
      }
    },
    '& .rdg-header-row': {
      backgroundColor: theme.otherVars?.headerBg ||
        theme.palette.background.paper,
      fontWeight: theme.typography.fontWeightBold,
      boxShadow: `0 1px 0 ${theme.otherVars?.borderColor || theme.palette.divider}`,
    },
    '& .rdg-row': {
      backgroundColor: theme.palette.background.default,
      transition: 'background-color var(--cde-motion-fast)',
      '&[aria-selected=true]': {
        backgroundColor: theme.palette.primary.light,
        color: theme.otherVars?.qtDatagridSelectFg ||
          theme.palette.primary.contrastText,
      },
    }
  },
  '&.CDEGrid-striped .rdg-row.CDEGrid-row--alternate': {
    backgroundColor: theme.palette.action?.hover,
  },
  '&.CDEGrid-lines--horizontal .rdg-cell': {
    borderInlineEndWidth: 0,
  },
  '&.CDEGrid-lines--vertical .rdg-cell': {
    borderBlockEndWidth: 0,
  },
  '&.CDEGrid-lines--none .rdg-cell': {
    borderWidth: 0,
  },
  '& .CDEGrid-null': {
    color: theme.palette.text.muted,
    fontStyle: 'italic',
  },
  '& .CDEGrid-boolean': {
    accentColor: theme.palette.primary.main,
    margin: 0,
  },
  '& .CDEGrid-progress': {
    display: 'grid',
    gridTemplateColumns: 'minmax(3rem, 1fr) auto',
    alignItems: 'center',
    gap: '0.5em',
    width: '100%',
  },
  '& .CDEGrid-progressTrack': {
    height: '0.55em',
    border: `1px solid ${theme.otherVars?.borderColor || theme.palette.divider}`,
    backgroundColor: theme.palette.background.default,
    '& > span': {
      display: 'block',
      height: '100%',
      backgroundColor: theme.palette.primary.main,
    },
  },
  '& .CDEGrid-progressLabel': {
    fontVariantNumeric: 'tabular-nums',
  },
  '& .CDEGrid-status': {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '0.45em',
  },
  '& .CDEGrid-statusMarker': {
    width: '0.7em',
    height: '0.7em',
    borderRadius: '50%',
    border: `1px solid ${theme.otherVars?.borderColor || theme.palette.divider}`,
    backgroundColor: theme.palette.text.muted,
  },
  '& .CDEGrid-status--success .CDEGrid-statusMarker': {
    backgroundColor: theme.palette.success.main,
  },
  '& .CDEGrid-status--warning .CDEGrid-statusMarker': {
    backgroundColor: theme.palette.warning.main,
  },
  '& .CDEGrid-status--error .CDEGrid-statusMarker': {
    backgroundColor: theme.palette.error.main,
  },
  '& .CDEGrid-status--info .CDEGrid-statusMarker': {
    backgroundColor: theme.palette.primary.main,
  },
  '& .CDEGrid-comment': {
    display: 'block',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  '& .CDEGrid-comment--present::after': {
    content: '""',
    position: 'absolute',
    insetInlineEnd: 0,
    insetBlockStart: 0,
    borderStyle: 'solid',
    borderWidth: '0 0 8px 8px',
    borderColor: `transparent transparent ${theme.palette.warning.main}
      transparent`,
  },
  '& .CDEGrid-row--warning': {
    boxShadow: `inset 3px 0 ${theme.palette.warning.main}`,
  },
  '& .CDEGrid-row--error, & .CDEGrid-row--deleted': {
    boxShadow: `inset 3px 0 ${theme.palette.error.main}`,
  },
  '& .CDEGrid-row--success, & .CDEGrid-row--inserted': {
    boxShadow: `inset 3px 0 ${theme.palette.success.main}`,
  },
  '& .CDEGrid-row--modified': {
    boxShadow: `inset 3px 0 ${theme.palette.primary.main}`,
  },
  '&.ReactGrid-cellSelection': {
    '& .rdg-cell': {
      '&[aria-selected=true]:not([aria-colindex="1"]):not([role="columnheader"])': {
        outlineWidth: 'var(--cde-focus-width, 2px)',
        outlineOffset: 'calc(var(--cde-focus-width, 2px) * -1)',
        backgroundColor: theme.palette.primary.light,
        color: theme.otherVars?.qtDatagridSelectFg ||
          theme.palette.primary.contrastText,
      }
    },
  },
}));

export const GridContextUtils = React.createContext();

const VALID_ROW_STATES = new Set([
  'warning', 'error', 'success', 'inserted', 'modified', 'deleted',
]);

function defaultStorage() {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

function rowStateClass(state) {
  const name = typeof state === 'string' ? state : state?.state;
  return VALID_ROW_STATES.has(name) ? `CDEGrid-row--${name}` : '';
}

function CutomSortIcon({sortDirection}) {
  if(sortDirection == 'DESC') {
    return <KeyboardArrowDownIcon style={{fontSize: '1.2rem'}} />;
  } else if(sortDirection == 'ASC') {
    return <KeyboardArrowUpIcon style={{fontSize: '1.2rem'}} />;
  }
  return <></>;
}
CutomSortIcon.propTypes = {
  sortDirection: PropTypes.string,
};

export function CustomRow({inTest=false, ...props}) {
  const gridUtils = useContext(GridContextUtils);
  const handleKeyDown = (e)=>{
    if(e.code == 'Tab' || e.code == 'ArrowRight' || e.code == 'ArrowLeft') {
      e.stopPropagation();
    }
    if(e.code == 'Enter') {
      gridUtils.onItemEnter?.(props.row);
    }
  };
  const isRowSelected = props.selectedCellIdx >= 0;
  useEffect(()=>{
    if(isRowSelected) {
      gridUtils.onItemSelect?.(props.rowIdx);
    }
  }, [props.selectedCellIdx]);
  if(inTest) {
    return <div data-test='test-div' tabIndex={-1} onKeyDown={handleKeyDown}></div>;
  }

  const onCellClick = (args) => {
    gridUtils.onItemClick?.(args.rowIdx);
    props.onRowClick?.(args.row);
  };

  const onCellDoubleClick = (args, e) => {
    // check if grid default is prevented.
    props.onCellDoubleClick?.(args, e);
    if(e.isGridDefaultPrevented()) return;
    gridUtils.onItemEnter?.(args.row, e);
  };

  return (
    <Row {...props} onKeyDown={handleKeyDown} onCellClick={onCellClick} onCellDoubleClick={onCellDoubleClick}
      selectCell={(row, column)=>props.selectCell(row, column)} aria-selected={isRowSelected}/>
  );
}
CustomRow.propTypes = {
  inTest: PropTypes.bool,
  row: PropTypes.object,
  selectedCellIdx: PropTypes.number,
  onRowClick: PropTypes.func,
  rowIdx: PropTypes.number,
  selectCell: PropTypes.func,
};

export default function PgReactDataGrid({
  gridRef, className, hasSelectColumn=true, onItemEnter, onItemSelect,
  onItemClick, noRowsText, noRowsIcon, columns=[], renderers,
  gridId, persistColumnState=false, columnStateStorage,
  onGridLayoutChange, onColumnResize, onColumnsReorder,
  readOnly=false, stripedRows=true, showGridLines='both', rowStateGetter,
  rowClass, selectionMode, enableCellSelect=false, enableRangeSelection=false,
  ...props
}) {

  const theme = useTheme();
  const presentationRowHeight = theme.cdeadminPresentation?.gridRowHeight;
  const rowHeight = presentationRowHeight ?? props.rowHeight;
  const presentationHeaderHeight =
    theme.cdeadminPresentation?.gridHeaderHeight;
  const headerRowHeight = presentationHeaderHeight ?
    Math.max(presentationHeaderHeight, props.headerRowHeight ?? 0) :
    props.headerRowHeight;
  const summaryRowHeight = presentationRowHeight ?
    Math.max(presentationRowHeight, props.summaryRowHeight ?? 0) :
    props.summaryRowHeight;
  const storage = columnStateStorage ?? defaultStorage();
  const [layout, setLayout] = useState(() => persistColumnState ?
    loadGridLayout(storage, gridId) : null);

  useEffect(() => {
    setLayout(persistColumnState ? loadGridLayout(storage, gridId) : null);
  }, [gridId, persistColumnState, storage]);

  const normalizedColumns = useMemo(() => normalizeGridColumns(
    columns, {readOnly}
  ), [columns, readOnly]);
  const managedColumns = useMemo(() => applyGridLayout(
    normalizedColumns, layout
  ), [normalizedColumns, layout]);

  let finalClassName = ['ReactGrid-root'];
  hasSelectColumn && finalClassName.push('ReactGrid-hasSelectColumn');
  (enableCellSelect || selectionMode === 'cell' || selectionMode === 'range') &&
    finalClassName.push('ReactGrid-cellSelection');
  stripedRows && finalClassName.push('CDEGrid-striped');
  finalClassName.push(`CDEGrid-lines--${showGridLines}`);
  readOnly && finalClassName.push('CDEGrid-readOnly');
  className && finalClassName.push(className);
  const valObj = useMemo(() => ({onItemEnter, onItemSelect, onItemClick}), [onItemEnter, onItemSelect, onItemClick]);

  const renderRow = useCallback((key, props) => {
    return <CustomRow key={key} {...props} />;
  }, []);

  const renderSortStatus = useCallback((props) => {
    return <CutomSortIcon {...props} />;
  }, []);

  const finalRenderers = useMemo(() => ({
    renderRow,
    renderSortStatus,
    noRowsFallback: <Box
      role='status'
      textAlign='center'
      gridColumn='1/-1'
      p={1}
    >{noRowsIcon}{noRowsText || gettext('No rows found.')}</Box>,
    ...renderers,
  }), [renderRow, renderSortStatus, noRowsIcon, noRowsText, renderers]);

  const finalRowClass = useCallback((row, rowIdx) => [
    stripedRows && rowIdx % 2 === 1 ? 'CDEGrid-row--alternate' : '',
    rowStateClass(rowStateGetter?.(row, rowIdx)),
    rowClass?.(row, rowIdx),
  ].filter(Boolean).join(' '), [stripedRows, rowStateGetter, rowClass]);

  const updateLayout = useCallback((nextLayout) => {
    setLayout(nextLayout);
    if(persistColumnState) {
      saveGridLayout(storage, gridId, nextLayout);
    }
    onGridLayoutChange?.(nextLayout);
  }, [persistColumnState, storage, gridId, onGridLayoutChange]);

  const handleColumnResize = useCallback((column, width) => {
    const nextLayout = {
      schemaVersion: 1,
      order: layout?.order?.length ? layout.order : columnKeys(managedColumns),
      widths: {...layout?.widths, [column.key]: width},
    };
    updateLayout(nextLayout);
    onColumnResize?.(column, width);
  }, [layout, managedColumns, updateLayout, onColumnResize]);

  const handleColumnsReorder = useCallback((sourceKey, targetKey) => {
    const nextLayout = {
      schemaVersion: 1,
      order: moveGridColumn(managedColumns, sourceKey, targetKey),
      widths: {...layout?.widths},
    };
    updateLayout(nextLayout);
    onColumnsReorder?.(sourceKey, targetKey);
  }, [layout, managedColumns, updateLayout, onColumnsReorder]);

  return (
    <GridContextUtils.Provider value={valObj}>
      <StyledReactDataGrid
        ref={gridRef}
        className={finalClassName.join(' ')}
        columns={managedColumns}
        renderers={finalRenderers}
        rowClass={finalRowClass}
        onColumnResize={handleColumnResize}
        onColumnsReorder={handleColumnsReorder}
        enableRangeSelection={selectionMode === 'range' ||
          enableRangeSelection}
        aria-label={props['aria-label'] || gettext('Data grid')}
        {...props}
        rowHeight={rowHeight}
        headerRowHeight={headerRowHeight}
        summaryRowHeight={summaryRowHeight}
      />
    </GridContextUtils.Provider>
  );
}

PgReactDataGrid.propTypes = {
  gridRef: CustomPropTypes.ref,
  className: CustomPropTypes.className,
  hasSelectColumn: PropTypes.bool,
  enableCellSelect: PropTypes.bool,
  enableRangeSelection: PropTypes.bool,
  onItemEnter: PropTypes.func,
  onItemSelect: PropTypes.func,
  onItemClick: PropTypes.func,
  noRowsText: PropTypes.string,
  noRowsIcon: PropTypes.object,
  columns: PropTypes.array,
  renderers: PropTypes.object,
  gridId: PropTypes.string,
  persistColumnState: PropTypes.bool,
  columnStateStorage: PropTypes.object,
  onGridLayoutChange: PropTypes.func,
  onColumnResize: PropTypes.func,
  onColumnsReorder: PropTypes.func,
  readOnly: PropTypes.bool,
  stripedRows: PropTypes.bool,
  showGridLines: PropTypes.oneOf(['both', 'horizontal', 'vertical', 'none']),
  rowStateGetter: PropTypes.func,
  rowClass: PropTypes.func,
  selectionMode: PropTypes.oneOf(['cell', 'range', 'row']),
};
