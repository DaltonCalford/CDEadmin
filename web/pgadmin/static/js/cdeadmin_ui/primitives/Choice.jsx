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
  Checkbox as MuiCheckbox,
  FormControlLabel,
  MenuItem,
  Switch as MuiSwitch,
  TextField as MuiTextField,
} from '@mui/material';

export function Checkbox({label, checked=false, onChange, ...props}) {
  return <FormControlLabel
    label={label}
    control={<MuiCheckbox
      checked={checked}
      onChange={(event) => onChange?.(event.target.checked, event)}
      {...props}
    />}
  />;
}

Checkbox.propTypes = {
  label: PropTypes.node.isRequired,
  checked: PropTypes.bool,
  onChange: PropTypes.func,
};

export function Switch({label, checked=false, onChange, ...props}) {
  return <FormControlLabel
    label={label}
    control={<MuiSwitch
      checked={checked}
      onChange={(event) => onChange?.(event.target.checked, event)}
      {...props}
    />}
  />;
}

Switch.propTypes = Checkbox.propTypes;

export function Select({options=[], value='', onChange, ...props}) {
  return <MuiTextField
    select
    value={value}
    onChange={(event) => onChange?.(event.target.value, event)}
    {...props}
  >
    {options.map((option) => <MenuItem
      key={option.value}
      value={option.value}
      disabled={option.disabled}
    >
      {option.label}
    </MenuItem>)}
  </MuiTextField>;
}

Select.propTypes = {
  options: PropTypes.arrayOf(PropTypes.shape({
    value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]).isRequired,
    label: PropTypes.node.isRequired,
    disabled: PropTypes.bool,
  })),
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  onChange: PropTypes.func,
};
