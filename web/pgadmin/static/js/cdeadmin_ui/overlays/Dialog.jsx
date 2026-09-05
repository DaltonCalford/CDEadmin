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

export function Dialog({open, title, children, actions, onClose, ...props}) {
  const titleId = useId();
  return <MuiDialog
    open={open}
    onClose={onClose}
    aria-labelledby={titleId}
    {...props}
  >
    <DialogTitle id={titleId}>{title}</DialogTitle>
    <DialogContent>{children}</DialogContent>
    {actions && <DialogActions>{actions}</DialogActions>}
  </MuiDialog>;
}

Dialog.propTypes = {
  open: PropTypes.bool.isRequired,
  title: PropTypes.node.isRequired,
  children: PropTypes.node,
  actions: PropTypes.node,
  onClose: PropTypes.func,
};
