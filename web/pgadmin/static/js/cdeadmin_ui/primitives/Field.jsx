/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import {TextField as MuiTextField} from '@mui/material';

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
  revealed=false,
  autoComplete='current-password',
  ...props
}) {
  return <TextField
    {...props}
    type={allowReveal && revealed ? 'text' : 'password'}
    autoComplete={autoComplete}
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
  autoComplete: PropTypes.string,
};
