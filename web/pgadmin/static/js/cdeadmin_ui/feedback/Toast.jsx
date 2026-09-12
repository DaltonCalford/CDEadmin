/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {useEffect, useState} from 'react';
import PropTypes from 'prop-types';
import {Alert, Box} from '@mui/material';
import {ProgressBar} from './Indicators';

const DURATIONS = {success: 6000, info: 6000, warning: 10000, error: 0,
  progress: 0};

export function Toast({open=true, status='info', title, children, progress,
  onClose, duration}) {
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if(!open || paused || !onClose) return undefined;
    const timeout = duration ?? DURATIONS[status];
    if(!timeout) return undefined;
    const timer = setTimeout(() => onClose('timeout'), timeout);
    return () => clearTimeout(timer);
  }, [open, paused, status, duration, onClose]);
  if(!open) return null;
  const severity = status === 'progress' ? 'info' : status;
  return <Alert severity={severity} square onClose={() => onClose?.('dismiss')}
    role={status === 'error' ? 'alert' : 'status'}
    onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}
    sx={{width: 360, maxWidth: 'calc(100vw - 24px)', border: '1px solid',
      borderColor: `${severity}.main`, bgcolor: 'background.paper'}}>
    {title && <Box component="strong" sx={{display: 'block'}}>{title}</Box>}
    {children}
    {status === 'progress' && <ProgressBar value={progress} />}
  </Alert>;
}

Toast.propTypes = {
  open: PropTypes.bool,
  status: PropTypes.oneOf(['success', 'info', 'warning', 'error', 'progress']),
  title: PropTypes.node,
  children: PropTypes.node,
  progress: PropTypes.number,
  onClose: PropTypes.func,
  duration: PropTypes.number,
};
