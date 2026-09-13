/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {useRef} from 'react';
import PropTypes from 'prop-types';
import CircularProgress from '@mui/material/CircularProgress';
import {
  DefaultButton,
  PgIconButton,
  PrimaryButton,
} from '../../components/Buttons';

export function Button({intent='neutral', loading=false, children, onClick,
  ...props}) {
  const lastMouseActivation = useRef(0);
  const invokeOnce = (event) => {
    if(event.detail > 0) {
      const now = Date.now();
      if(now - lastMouseActivation.current <= 500) return;
      lastMouseActivation.current = now;
    }
    onClick?.(event);
  };
  if(intent === 'primary') {
    return <PrimaryButton
      {...props}
      onClick={invokeOnce}
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
    onClick={invokeOnce}
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
  onClick: PropTypes.func,
};

export function IconButton({label, title, icon, children, ...props}) {
  const accessibleLabel = label || title;
  if(!accessibleLabel) {
    throw new TypeError('IconButton requires a label or title.');
  }
  return <PgIconButton
    aria-label={accessibleLabel}
    title={title || accessibleLabel}
    icon={icon ?? children}
    {...props}
  />;
}

IconButton.propTypes = {
  label: PropTypes.string,
  title: PropTypes.string,
  icon: PropTypes.node,
  children: PropTypes.node,
};
