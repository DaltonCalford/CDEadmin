/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {useId} from 'react';
import PropTypes from 'prop-types';
import {
  Dialog as MuiDialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material';

const WIDTHS = Object.freeze({small: 420, medium: 640, large: 960});

export function Dialog({open, title, children, actions, onClose, size='small',
  busy=false, validationError='', defaultAction, onKeyDown, ...props}) {
  const titleId = useId();
  return <MuiDialog
    {...props}
    open={open}
    onClose={onClose}
    aria-labelledby={titleId}
    PaperProps={{'aria-busy': busy || undefined}}
    onKeyDown={(event) => {
      if(event.key === 'Enter' && defaultAction && !busy &&
          !['TEXTAREA'].includes(event.target.tagName) &&
          !event.target.closest?.('[data-code-editor]')) {
        event.preventDefault();
        defaultAction();
      }
      onKeyDown?.(event);
    }}
    slotProps={{paper: {'aria-busy': busy || undefined,
      sx: {width: WIDTHS[size], maxWidth: 'calc(100vw - 48px)',
        maxHeight: 'calc(100vh - 48px)', m: 3, borderRadius: 0,
        boxShadow: 'none', border: '1px solid', borderColor: 'divider'}}}}
  >
    <DialogTitle id={titleId}>{title}</DialogTitle>
    <DialogContent sx={{p: 2.5}}>
      {validationError && <div role="alert">{validationError}</div>}
      {children}
    </DialogContent>
    {actions && <DialogActions>{actions}</DialogActions>}
  </MuiDialog>;
}

Dialog.propTypes = {
  open: PropTypes.bool.isRequired,
  title: PropTypes.node.isRequired,
  children: PropTypes.node,
  actions: PropTypes.node,
  onClose: PropTypes.func,
  size: PropTypes.oneOf(['small', 'medium', 'large']),
  busy: PropTypes.bool,
  validationError: PropTypes.node,
  defaultAction: PropTypes.func,
  onKeyDown: PropTypes.func,
};
