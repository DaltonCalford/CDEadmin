/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import {CircularProgress, Box, Typography} from '@mui/material';
import {styled} from '@mui/material/styles';

const Root = styled(Box)(({theme}) => ({
  position: 'absolute',
  inset: 0,
  backgroundColor: theme.otherVars.loader.backgroundColor,
  color: theme.otherVars.loader.color,
  zIndex: 1000,
  display: 'flex',
  '& .CdeProgressOverlay-body, & .Loader-loaderBody': {
    color: theme.otherVars.loader.color,
    display: 'flex',
    alignItems: 'center',
    margin: 'auto',
    gap: theme.spacing(1),
  },
  '& .CdeProgressOverlay-icon, & .Loader-icon': {
    color: theme.otherVars.loader.color,
  },
  '& .CdeProgressOverlay-message, & .Loader-message': {
    fontSize: '1rem',
  },
}));

export function ProgressOverlay({message, style, autoEllipsis=false,
  ...props}) {
  if(!message) {
    return <></>;
  }
  return <Root
    style={style}
    data-label="loader"
    role="status"
    aria-live="polite"
    aria-busy="true"
    {...props}
  >
    <Box className="CdeProgressOverlay-body Loader-loaderBody">
      <CircularProgress className="CdeProgressOverlay-icon Loader-icon" />
      <Typography className="CdeProgressOverlay-message Loader-message">
        {message}{autoEllipsis ? '...' : ''}
      </Typography>
    </Box>
  </Root>;
}

ProgressOverlay.propTypes = {
  message: PropTypes.string,
  style: PropTypes.oneOfType([PropTypes.object, PropTypes.array]),
  autoEllipsis: PropTypes.bool,
};
