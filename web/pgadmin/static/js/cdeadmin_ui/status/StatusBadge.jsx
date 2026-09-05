/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import {Box} from '@mui/material';

const STATUS_COLORS = {
  neutral: 'text.secondary',
  success: 'success.main',
  warning: 'warning.main',
  error: 'error.main',
  information: 'primary.main',
};

export function StatusBadge({label, status='neutral', live=false}) {
  return <Box
    component="span"
    role={live ? 'status' : undefined}
    aria-live={live ? 'polite' : undefined}
    sx={{display: 'inline-flex', alignItems: 'center', gap: 0.5}}
  >
    <Box
      component="span"
      aria-hidden="true"
      sx={{
        width: '0.7em',
        height: '0.7em',
        borderRadius: '50%',
        bgcolor: STATUS_COLORS[status],
        border: '1px solid',
        borderColor: 'currentColor',
      }}
    />
    <Box component="span">{label}</Box>
  </Box>;
}

StatusBadge.propTypes = {
  label: PropTypes.node.isRequired,
  status: PropTypes.oneOf(Object.keys(STATUS_COLORS)),
  live: PropTypes.bool,
};
