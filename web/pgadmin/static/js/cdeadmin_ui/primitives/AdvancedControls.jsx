/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {useRef, useState} from 'react';
import PropTypes from 'prop-types';
import {
  Autocomplete,
  Box,
  ButtonGroup,
  FormControl,
  FormControlLabel,
  FormHelperText,
  FormLabel,
  InputAdornment,
  LinearProgress,
  MenuItem,
  Radio as MuiRadio,
  RadioGroup,
  Slider as MuiSlider,
  TextField as MuiTextField,
  ToggleButton,
  ToggleButtonGroup,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import ClearIcon from '@mui/icons-material/Clear';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import {Button, IconButton} from './Button';

function optionLabel(option) {
  return typeof option === 'object' ? String(option.label ?? option.value ?? '') :
    String(option ?? '');
}

export function SplitButton({children, options=[], onClick, onSelect,
  disabled=false, label='More actions'}) {
  const [open, setOpen] = useState(false);
  return <Box sx={{position: 'relative', display: 'inline-flex'}}>
    <ButtonGroup disabled={disabled}>
      <Button onClick={onClick}>{children}</Button>
      <IconButton label={label} onClick={() => setOpen((value) => !value)}>
        <ArrowDropDownIcon />
      </IconButton>
    </ButtonGroup>
    {open && <Box role="menu" sx={{position: 'absolute', top: '100%', right: 0,
      zIndex: 'var(--cde-layer-menu)', minWidth: 220, bgcolor: 'background.paper',
      border: '1px solid', borderColor: 'divider'}}>
      {options.map((option) => <Button key={option.value} role="menuitem"
        disabled={option.disabled} sx={{display: 'flex', width: '100%',
          justifyContent: 'flex-start', minHeight: 'var(--cde-menu-row-height)'}}
        onClick={() => { setOpen(false); onSelect?.(option.value); }}>
        {option.label}
      </Button>)}
    </Box>}
  </Box>;
}

SplitButton.propTypes = {
  children: PropTypes.node,
  options: PropTypes.array,
  onClick: PropTypes.func,
  onSelect: PropTypes.func,
  disabled: PropTypes.bool,
  label: PropTypes.string,
};

export function ComboBox({options=[], value=null, onChange, freeEntry=false,
  label, validationMessage, ...props}) {
  return <Autocomplete
    options={options}
    value={value}
    freeSolo={freeEntry}
    getOptionLabel={optionLabel}
    onChange={(_event, next) => onChange?.(next)}
    onInputChange={(_event, next, reason) => {
      if(freeEntry && reason === 'input') onChange?.(next);
    }}
    renderInput={(params) => <MuiTextField {...params} label={label}
      error={Boolean(validationMessage)} helperText={validationMessage} />}
    {...props}
  />;
}

ComboBox.propTypes = {
  options: PropTypes.array,
  value: PropTypes.any,
  onChange: PropTypes.func,
  freeEntry: PropTypes.bool,
  label: PropTypes.node,
  validationMessage: PropTypes.node,
};

export function Radio({label, value, options=[], onChange, disabled=false}) {
  return <FormControl disabled={disabled}>
    {label && <FormLabel>{label}</FormLabel>}
    <RadioGroup value={value} onChange={(event) => onChange?.(event.target.value)}>
      {options.map((option) => <FormControlLabel key={option.value}
        value={option.value} disabled={option.disabled}
        control={<MuiRadio />} label={option.label} />)}
    </RadioGroup>
  </FormControl>;
}

Radio.propTypes = {
  label: PropTypes.node,
  value: PropTypes.any,
  options: PropTypes.array,
  onChange: PropTypes.func,
  disabled: PropTypes.bool,
};

export function Slider({label, value, onChange, showValue=true, ...props}) {
  return <Box>
    {label && <FormLabel>{label}</FormLabel>}
    <MuiSlider value={value} valueLabelDisplay={showValue ? 'auto' : 'off'}
      onChange={(_event, next) => onChange?.(next)} {...props} />
  </Box>;
}

Slider.propTypes = {
  label: PropTypes.node,
  value: PropTypes.oneOfType([PropTypes.number, PropTypes.array]),
  onChange: PropTypes.func,
  showValue: PropTypes.bool,
};

export function MultiSelect({label, options=[], value=[], onChange,
  validationMessage, ...props}) {
  return <Autocomplete multiple disableCloseOnSelect options={options}
    value={value} getOptionLabel={optionLabel}
    onChange={(_event, next) => onChange?.(next)}
    limitTags={2}
    renderInput={(params) => <MuiTextField {...params} label={label}
      error={Boolean(validationMessage)} helperText={validationMessage} />}
    {...props} />;
}

MultiSelect.propTypes = ComboBox.propTypes;

export function SegmentedControl({label, options=[], value, onChange,
  disabled=false}) {
  return <FormControl disabled={disabled}>
    {label && <FormLabel>{label}</FormLabel>}
    <ToggleButtonGroup exclusive value={value}
      onChange={(_event, next) => next !== null && onChange?.(next)}>
      {options.map((option) => <ToggleButton key={option.value}
        value={option.value} disabled={option.disabled}>
        {option.label}
      </ToggleButton>)}
    </ToggleButtonGroup>
  </FormControl>;
}

SegmentedControl.propTypes = Radio.propTypes;

export function SearchField({label='Filter items', value='', onChange,
  loading=false, resultCount, ...props}) {
  return <MuiTextField label={label} value={value}
    onChange={(event) => onChange?.(event.target.value)}
    onKeyDown={(event) => {
      if(event.key === 'Escape' && value) {
        event.stopPropagation();
        onChange?.('');
      }
      props.onKeyDown?.(event);
    }}
    InputProps={{
      startAdornment: <InputAdornment position="start"><SearchIcon /></InputAdornment>,
      endAdornment: <InputAdornment position="end">
        {loading && <LinearProgress aria-label="Searching" sx={{width: 24}} />}
        {value && <IconButton label="Clear search" onClick={() => onChange?.('')}>
          <ClearIcon />
        </IconButton>}
      </InputAdornment>,
    }}
    helperText={resultCount === undefined ? undefined : `${resultCount} results`}
    {...props}
  />;
}

SearchField.propTypes = {
  label: PropTypes.node,
  value: PropTypes.string,
  onChange: PropTypes.func,
  loading: PropTypes.bool,
  resultCount: PropTypes.number,
  onKeyDown: PropTypes.func,
};

function NativeTemporalField({type, label, value='', onChange,
  validationMessage, context, ...props}) {
  const help = [context, validationMessage].filter(Boolean).join(' — ');
  return <MuiTextField type={type} label={label} value={value}
    onChange={(event) => onChange?.(event.target.value)}
    error={Boolean(validationMessage)} helperText={help || undefined}
    InputLabelProps={{shrink: true}} {...props} />;
}

NativeTemporalField.propTypes = {
  type: PropTypes.string.isRequired,
  label: PropTypes.node,
  value: PropTypes.string,
  onChange: PropTypes.func,
  validationMessage: PropTypes.node,
  context: PropTypes.node,
};

export function DateField(props) {
  return <NativeTemporalField type="date" {...props} />;
}

export function TimeField({timezone, ...props}) {
  return <NativeTemporalField type="time" context={timezone ?
    `Timezone: ${timezone}` : undefined} {...props} />;
}

TimeField.propTypes = {timezone: PropTypes.string};

export function UnitNumberField({label, value='', unit, units=[], onChange,
  onUnitChange, validationMessage, ...props}) {
  const unitControl = units.length ? <MuiTextField select variant="standard"
    aria-label={`${label || 'Value'} unit`} value={unit}
    onChange={(event) => onUnitChange?.(event.target.value)}>
    {units.map((item) => <MenuItem key={item.value ?? item}
      value={item.value ?? item}>{item.label ?? item}</MenuItem>)}
  </MuiTextField> : <Box component="span">{unit}</Box>;
  return <MuiTextField type="number" label={label} value={value}
    onChange={(event) => {
      const number = event.target.value === '' ? '' : Number(event.target.value);
      onChange?.(number, unit);
    }}
    error={Boolean(validationMessage)} helperText={validationMessage}
    InputProps={{endAdornment: <InputAdornment position="end">
      {unitControl}
    </InputAdornment>}} {...props} />;
}

UnitNumberField.propTypes = {
  label: PropTypes.node,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  unit: PropTypes.string.isRequired,
  units: PropTypes.array,
  onChange: PropTypes.func,
  onUnitChange: PropTypes.func,
  validationMessage: PropTypes.node,
};

export function DurationField(props) {
  return <UnitNumberField units={[
    {value: 'ms', label: 'ms'}, {value: 's', label: 'seconds'},
    {value: 'min', label: 'minutes'}, {value: 'h', label: 'hours'},
    {value: 'd', label: 'days'},
  ]} {...props} />;
}

export function PasswordStrength({state='unknown', message}) {
  const values = {unknown: 0, invalid: 25, acceptable: 67, strong: 100};
  return <Box role="status" aria-live="polite">
    <LinearProgress variant="determinate" value={values[state]}
      color={state === 'invalid' ? 'error' : state === 'strong' ? 'success' : 'primary'} />
    <FormHelperText>{message || ({unknown: 'Strength not evaluated',
      invalid: 'Password does not meet provider policy',
      acceptable: 'Password meets provider policy',
      strong: 'Password exceeds provider policy'}[state])}</FormHelperText>
  </Box>;
}

PasswordStrength.propTypes = {
  state: PropTypes.oneOf(['unknown', 'invalid', 'acceptable', 'strong']),
  message: PropTypes.node,
};

export function FilePickerButton({label='Choose file', accept, multiple=false,
  directory=false, onFiles, disabled=false, loading=false}) {
  const ref = useRef(null);
  return <>
    <input ref={ref} hidden type="file" accept={accept} multiple={multiple}
      disabled={disabled} {...(directory ? {webkitdirectory: ''} : {})}
      onChange={(event) => onFiles?.(Array.from(event.target.files), event)} />
    <Button startIcon={<UploadFileIcon />} disabled={disabled} loading={loading}
      onClick={() => ref.current?.click()}>{label}</Button>
  </>;
}

FilePickerButton.propTypes = {
  label: PropTypes.node,
  accept: PropTypes.string,
  multiple: PropTypes.bool,
  directory: PropTypes.bool,
  onFiles: PropTypes.func,
  disabled: PropTypes.bool,
  loading: PropTypes.bool,
};
