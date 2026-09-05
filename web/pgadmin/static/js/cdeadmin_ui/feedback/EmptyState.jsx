/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import {styled} from '@mui/material/styles';
import {Box} from '@mui/material';
import InfoRoundedIcon from '@mui/icons-material/InfoRounded';

const Root = styled(Box)(({theme}) => ({
  color: theme.palette.text.primary,
  margin: '24px auto 12px',
  fontSize: '0.8rem',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: theme.spacing(0.5),
  minHeight: 'var(--cde-target-size, 24px)',
  height: '100%',
}));

export function EmptyState({message, icon, style, ...props}) {
  return <Root style={style} {...props}>
    {icon ?? <InfoRoundedIcon aria-hidden="true" style={{height: '1.2rem'}} />}
    <span>{message}</span>
  </Root>;
}

EmptyState.propTypes = {
  message: PropTypes.node,
  icon: PropTypes.node,
  style: PropTypes.object,
};
