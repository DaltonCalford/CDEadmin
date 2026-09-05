/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import CircularProgress from '@mui/material/CircularProgress';
import {
  DefaultButton,
  PgIconButton,
  PrimaryButton,
} from '../../components/Buttons';

export function Button({intent='neutral', loading=false, children, ...props}) {
  if(intent === 'primary') {
    return <PrimaryButton
      {...props}
      aria-busy={loading || undefined}
      disabled={loading || props.disabled}
      startIcon={loading ? <CircularProgress size="1em" /> : props.startIcon}
    >{children}</PrimaryButton>;
  }
  const color = {
    destructive: 'error',
    error: 'error',
    success: 'success',
    warning: 'warning',
  }[intent] || 'default';
  return <DefaultButton
    {...props}
    color={color}
    aria-busy={loading || undefined}
    disabled={loading || props.disabled}
    startIcon={loading ? <CircularProgress size="1em" /> : props.startIcon}
  >{children}</DefaultButton>;
}

Button.propTypes = {
  intent: PropTypes.oneOf([
    'neutral', 'primary', 'destructive', 'error', 'success', 'warning',
  ]),
  loading: PropTypes.bool,
  children: PropTypes.node,
};

export function IconButton({label, title, ...props}) {
  const accessibleLabel = label || title;
  return <PgIconButton
    aria-label={accessibleLabel}
    title={title || accessibleLabel}
    {...props}
  />;
}

IconButton.propTypes = {
  label: PropTypes.string,
  title: PropTypes.string,
};
