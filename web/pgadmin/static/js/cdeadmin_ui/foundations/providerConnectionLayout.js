/////////////////////////////////////////////////////////////
// ScratchRobin CDE Administrator — connection-form layout
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
/////////////////////////////////////////////////////////////

// Match the ordinary three-column layout while allowing font enlargement
// and narrow task windows to reduce the column count. Native values must
// remain readable rather than becoming ellipses inside a fixed-pixel cell.
export const providerConnectionFieldGridSx = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 17.5rem), 1fr))',
  gap: 2,
  minWidth: 0,
  alignItems: 'start',
  '& .MuiFormControl-root': {minWidth: 0},
  // MUI's selected-value slot also uses a two-class selector. The extra
  // grid class keeps this scoped override ahead of that slot's own rule.
  '&& .MuiSelect-select': {
    whiteSpace: 'normal', overflowWrap: 'anywhere', textOverflow: 'clip',
    height: 'auto',
  },
};
