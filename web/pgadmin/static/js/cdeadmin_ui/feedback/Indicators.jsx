/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import {Alert, Box, LinearProgress} from '@mui/material';

const COLORS = Object.freeze({
  neutral: 'text.secondary', info: 'info.main', success: 'success.main',
  warning: 'warning.main', error: 'error.main',
});

export function Badge({label, status='neutral'}) {
  return <Box component="span" sx={{display: 'inline-flex', alignItems: 'center',
    minHeight: 22, px: 1, border: '1px solid', borderColor: COLORS[status],
    color: COLORS[status], borderRadius: 0, fontSize: '0.75rem'}}>
    {label}
  </Box>;
}

Badge.propTypes = {
  label: PropTypes.node.isRequired,
  status: PropTypes.oneOf(Object.keys(COLORS)),
};

export function StatusDot({status='neutral', label}) {
  return <Box component="span" aria-label={label} title={label}
    sx={{display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
      bgcolor: COLORS[status], border: '1px solid currentColor'}} />;
}

StatusDot.propTypes = {
  status: PropTypes.oneOf(Object.keys(COLORS)),
  label: PropTypes.string.isRequired,
};

export function Banner({status='info', children, onClose}) {
  return <Alert severity={status} onClose={onClose} square role={status === 'error' ?
    'alert' : 'status'} sx={{minHeight: 36, py: 0, border: '1px solid',
    borderColor: `${status}.main`, bgcolor: 'background.paper'}}>
    {children}
  </Alert>;
}

Banner.propTypes = {
  status: PropTypes.oneOf(['info', 'warning', 'error', 'success']),
  children: PropTypes.node,
  onClose: PropTypes.func,
};

export function ValidationMessage({id, status='error', children}) {
  return <Box id={id} role={status === 'error' ? 'alert' : 'status'}
    sx={{display: 'flex', alignItems: 'center', minHeight: 16, gap: 0.5,
      color: COLORS[status], fontSize: '0.75rem'}}>
    <StatusDot status={status} label={status} />
    <span>{children}</span>
  </Box>;
}

ValidationMessage.propTypes = {
  id: PropTypes.string,
  status: PropTypes.oneOf(['info', 'warning', 'error', 'success']),
  children: PropTypes.node,
};

export function ProgressBar({value, status='determinate', label='Progress'}) {
  const determinate = Number.isFinite(value);
  const normalized = determinate ? Math.max(0, Math.min(100, value)) : undefined;
  return <Box role="status" aria-label={label} aria-live="polite">
    <LinearProgress variant={determinate ? 'determinate' : 'indeterminate'}
      value={normalized}
      color={status === 'error' ? 'error' : status === 'paused' ? 'warning' : 'primary'}
      sx={{height: 4}} />
    <Box component="span" sx={{fontSize: '0.75rem'}}>
      {determinate ? `${Math.round(normalized)}%` : status}
    </Box>
  </Box>;
}

ProgressBar.propTypes = {
  value: PropTypes.number,
  status: PropTypes.oneOf(['determinate', 'indeterminate', 'paused', 'error']),
  label: PropTypes.string,
};

export function Skeleton({lines=3, label='Loading'}) {
  return <Box role="status" aria-label={label} aria-busy="true">
    {Array.from({length: lines}, (_unused, index) => <Box key={index}
      sx={{height: 12, mb: index === lines - 1 ? 0 : 1,
        width: index === lines - 1 ? '72%' : '100%', bgcolor: 'action.hover',
        '@media (prefers-reduced-motion: no-preference)': {
          animation: 'cde-skeleton 1.5s ease-in-out infinite alternate',
        }, '@keyframes cde-skeleton': {from: {opacity: 0.45}, to: {opacity: 1}}}} />)}
  </Box>;
}

Skeleton.propTypes = {lines: PropTypes.number, label: PropTypes.string};

export function EnvironmentIndicator({environment='unknown'}) {
  const state = String(environment || 'unknown').toLowerCase();
  const status = {development: 'info', test: 'success', staging: 'warning',
    production: 'error'}[state] || 'neutral';
  return <Badge status={status} label={state.toUpperCase()} />;
}

EnvironmentIndicator.propTypes = {environment: PropTypes.string};
