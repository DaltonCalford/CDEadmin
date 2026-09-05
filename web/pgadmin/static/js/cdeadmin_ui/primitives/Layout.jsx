/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import {
  Box as MuiBox,
  Divider as MuiDivider,
  Link as MuiLink,
  Paper,
  Stack as MuiStack,
} from '@mui/material';

export function Box(props) {
  return <MuiBox {...props} />;
}

export function Stack({gap='standard', ...props}) {
  const spacing = {compact: 0.5, standard: 1, comfortable: 2};
  return <MuiStack spacing={spacing[gap] ?? gap} {...props} />;
}

export function Grid({columns=1, gap='standard', ...props}) {
  const spacing = {compact: 0.5, standard: 1, comfortable: 2};
  return <MuiBox
    display="grid"
    gridTemplateColumns={typeof columns === 'number' ?
      `repeat(${columns}, minmax(0, 1fr))` : columns}
    gap={spacing[gap] ?? gap}
    {...props}
  />;
}

export function Panel({variant='flat', ...props}) {
  return <Paper
    variant={variant === 'flat' ? 'outlined' : 'elevation'}
    elevation={variant === 'raised' ? 2 : 0}
    {...props}
  />;
}

export function ScrollArea({label, ...props}) {
  return <MuiBox
    role={label ? 'region' : undefined}
    aria-label={label}
    tabIndex={label ? 0 : undefined}
    overflow="auto"
    {...props}
  />;
}

export function Divider(props) {
  return <MuiDivider {...props} />;
}

export function Link({external=false, ...props}) {
  return <MuiLink
    {...props}
    target={external ? '_blank' : props.target}
    rel={external ? 'noopener noreferrer' : props.rel}
  />;
}

Stack.propTypes = {
  gap: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};

Grid.propTypes = {
  columns: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  gap: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};

Panel.propTypes = {
  variant: PropTypes.oneOf(['flat', 'raised']),
};

ScrollArea.propTypes = {
  label: PropTypes.string,
};

Link.propTypes = {
  external: PropTypes.bool,
  target: PropTypes.string,
  rel: PropTypes.string,
};
