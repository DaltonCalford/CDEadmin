/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {useState} from 'react';
import PropTypes from 'prop-types';
import {InputAdornment, TextField as MuiTextField} from '@mui/material';
import VisibilityIcon from '@mui/icons-material/Visibility';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import {IconButton} from './Button';

export function TextField({validationMessage, ...props}) {
  return <MuiTextField
    {...props}
    error={Boolean(validationMessage) || props.error}
    helperText={validationMessage || props.helperText}
  />;
}

export function NumberField(props) {
  return <TextField {...props} type="number" inputMode="decimal" />;
}

export function TextArea({rows=4, ...props}) {
  return <TextField {...props} multiline rows={rows} />;
}

export function SecretField({
  allowReveal=false,
  revealed,
  onRevealChange,
  allowCopy=false,
  autoComplete='current-password',
  ...props
}) {
  const [internalRevealed, setInternalRevealed] = useState(false);
  const isRevealed = revealed ?? internalRevealed;
  const toggle = () => {
    const next = !isRevealed;
    if(revealed === undefined) setInternalRevealed(next);
    onRevealChange?.(next);
  };
  return <TextField
    {...props}
    type={allowReveal && isRevealed ? 'text' : 'password'}
    autoComplete={autoComplete}
    onCopy={(event) => {
      if(!allowCopy) event.preventDefault();
      props.onCopy?.(event);
    }}
    InputProps={{...props.InputProps, endAdornment: allowReveal ?
      <InputAdornment position="end">
        <IconButton label={isRevealed ? 'Hide secret' : 'Reveal secret'}
          onClick={toggle} edge="end">
          {isRevealed ? <VisibilityOffIcon /> : <VisibilityIcon />}
        </IconButton>
      </InputAdornment> : props.InputProps?.endAdornment}}
  />;
}

TextField.propTypes = {
  error: PropTypes.bool,
  helperText: PropTypes.node,
  validationMessage: PropTypes.node,
};

NumberField.propTypes = TextField.propTypes;

TextArea.propTypes = {
  ...TextField.propTypes,
  rows: PropTypes.number,
};

SecretField.propTypes = {
  ...TextField.propTypes,
  allowReveal: PropTypes.bool,
  revealed: PropTypes.bool,
  onRevealChange: PropTypes.func,
  allowCopy: PropTypes.bool,
  autoComplete: PropTypes.string,
  onCopy: PropTypes.func,
  InputProps: PropTypes.object,
};
