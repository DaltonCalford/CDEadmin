/////////////////////////////////////////////////////////////
// Contract-driven renderer shared by first-party governed modules.
/////////////////////////////////////////////////////////////

import {useEffect, useMemo, useState} from 'react';
import PropTypes from 'prop-types';
import {Box, FormHelperText} from '@mui/material';
import {Button} from '../primitives/Button';
import {Checkbox, Select, Switch} from '../primitives/Choice';
import {NumberField, SecretField, TextArea, TextField} from
  '../primitives/Field';
import {ComboBox, DateField, DurationField, MultiSelect, SearchField,
  SegmentedControl, UnitNumberField} from '../primitives/AdvancedControls';
import {AssetPicker, ConnectionSelector, ResourcePicker} from
  '../navigation/AdvancedNavigation';
import {FormSection, Toolbar} from '../layout/WorkbenchChrome';
import {Banner, EnvironmentIndicator, Skeleton,
  ValidationMessage} from '../feedback/Indicators';
import {EmptyState} from '../feedback/EmptyState';
import CodeEditor from '../editors/CodeEditor';
import DataGrid from '../data/DataGrid';
import {evaluateContractCondition, initialContractFormValues,
  isContractFormActionEnabled, redactedContractFormValues,
  transientContractFormSubmission, validateContractFormValues} from
  './ContractFormRuntime';

const DISABLED_STATES = new Set(['loading', 'permission', 'permission-denied']);
const EMPTY_VALUES = Object.freeze({});
const EMPTY_LIST = Object.freeze([]);

function optionRecords(options=[]) {
  return options.map((option) => typeof option === 'object' ? option :
    ({value: option, label: String(option)}));
}

function rowsFor(value) {
  if(!Array.isArray(value)) return [];
  return value.map((row, index) => typeof row === 'object' && row !== null ?
    {...row, __rowKey: row.id ?? index} : {value: row, __rowKey: index});
}

function columnsFor(rows, configured=[]) {
  if(configured.length) return configured;
  const keys = [...new Set(rows.flatMap((row) => Object.keys(row)))]
    .filter((key) => key !== '__rowKey');
  return (keys.length ? keys : ['value']).map((key) => ({key, name:
    key.replaceAll('_', ' ').replace(/^./, (value) => value.toUpperCase())}));
}

function durationParts(value) {
  const match = String(value ?? '').match(/^P(?:(\d+(?:\.\d+)?)D|T(\d+(?:\.\d+)?)(MS|S|M|H))$/);
  if(!match) return {number: value ?? '', unit: 'd'};
  if(match[1] !== undefined) return {number: Number(match[1]), unit: 'd'};
  return {number: Number(match[2]), unit: {MS: 'ms', S: 's', M: 'min', H: 'h'}[match[3]]};
}

function encodedDuration(number, unit) {
  if(number === '') return '';
  return unit === 'd' ? `P${number}D` :
    `PT${number}${{ms: 'MS', s: 'S', min: 'M', h: 'H'}[unit]}`;
}

function PickerControl({kind, field, value, onChange, items, disabled,
  state}) {
  const [open, setOpen] = useState(false);
  const multiple = Array.isArray(field.default);
  const selected = (multiple ? value : value ? [value] : [])
    .filter(Boolean).map((item, index) => typeof item === 'object' ? item :
      ({id: String(item), label: String(item), type: kind, __index: index}));
  const Picker = kind === 'asset' ? AssetPicker : ResourcePicker;
  const summary = selected.length ? selected.map((item) => item.label).join(', ') :
    `No ${kind}${multiple ? 's' : ''} selected`;
  return <Box role="group" aria-label={field.label}>
    <Box sx={{display: 'flex', alignItems: 'center', gap: 1, minWidth: 0}}>
      <Box component="span" sx={{flex: 1, overflow: 'hidden',
        textOverflow: 'ellipsis', whiteSpace: 'nowrap'}}>{summary}</Box>
      <Button disabled={disabled} onClick={() => setOpen(true)}>
        Choose {kind}{multiple ? 's' : ''}
      </Button>
      {selected.length > 0 && <Button disabled={disabled}
        onClick={() => onChange(multiple ? [] : null)}>Clear</Button>}
    </Box>
    <Picker open={open} items={items} selected={selected} multiple={multiple}
      state={state === 'permission-denied' ? 'permission' :
        state === 'disconnected' ? 'disconnected' : 'default'}
      onClose={() => setOpen(false)} onConfirm={(next) => {
        onChange(next); setOpen(false);
      }} />
  </Box>;
}

PickerControl.propTypes = {
  kind: PropTypes.oneOf(['asset', 'resource']).isRequired,
  field: PropTypes.object.isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  items: PropTypes.array,
  disabled: PropTypes.bool,
  state: PropTypes.string,
};

function GridControl({field, value, columns, namespace}) {
  const rows = rowsFor(value);
  if(!rows.length) return <Box sx={{height: 112}}><EmptyState
    message={`No ${field.label.toLowerCase()} are available.`} /></Box>;
  return <Box sx={{height: 240, minHeight: 160}}><DataGrid
    gridId={`${namespace}-interface/form/${field.id}`} aria-label={field.label}
    rows={rows} columns={columnsFor(rows, columns)} readOnly
    rowKeyGetter={(row) => row.__rowKey} /></Box>;
}

GridControl.propTypes = {
  field: PropTypes.object.isRequired,
  value: PropTypes.any,
  columns: PropTypes.array,
  namespace: PropTypes.string,
};

function FieldControl({field, value, onChange, options, gridColumns,
  resources, assets, connections, context, state, error, namespace}) {
  const disabled = DISABLED_STATES.has(state) ||
    !evaluateContractCondition(field.enabled, context.values, context);
  const common = {label: field.label, value: value ?? '', disabled,
    required: field.required, placeholder: field.placeholder || undefined,
    helperText: field.help || undefined, validationMessage: error,
    onChange: (event) => onChange(event?.target ? event.target.value : event)};
  switch(field.component) {
  case 'TextField': return <TextField {...common} fullWidth />;
  case 'NumberField': return <NumberField {...common} fullWidth
    onChange={(event) => onChange(event.target.value === '' ? '' :
      Number(event.target.value))} />;
  case 'UnitNumberField': return <UnitNumberField {...common} fullWidth
    unit={context.units?.[field.id] || 'currency'}
    units={context.unitOptions?.[field.id] || []} />;
  case 'TextArea': return <TextArea {...common} rows={4} fullWidth />;
  case 'SecretField': return <SecretField {...common} fullWidth allowReveal
    allowCopy={false} autoComplete="new-password" />;
  case 'Checkbox': return <Checkbox label={field.label}
    checked={value === true} disabled={disabled} onChange={onChange} />;
  case 'ToggleSwitch': return <Switch label={field.label}
    checked={value === true} disabled={disabled} onChange={onChange} />;
  case 'Select': return <Select label={field.label} value={value ?? ''}
    disabled={disabled} required={field.required} placeholder={field.placeholder || undefined}
    error={Boolean(error)} helperText={error || field.help || undefined}
    options={optionRecords(options)} fullWidth onChange={onChange} />;
  case 'ComboBox': return <ComboBox label={field.label}
    disabled={disabled} options={optionRecords(options)}
    value={optionRecords(options).find((option) => option.value === value) ?? value ?? null}
    validationMessage={error} freeEntry={false}
    onChange={(next) => onChange(next?.value ?? next)} />;
  case 'MultiSelect': return <MultiSelect label={field.label} disabled={disabled}
    options={options} value={Array.isArray(value) ? value : []}
    validationMessage={error} onChange={onChange} />;
  case 'SegmentedControl': return <SegmentedControl label={field.label}
    value={value} options={optionRecords(options)} disabled={disabled}
    onChange={onChange} />;
  case 'DateField': return <DateField {...common} fullWidth />;
  case 'DurationField': {
    const duration = durationParts(value);
    return <DurationField label={field.label} value={duration.number}
      unit={duration.unit} required={field.required} disabled={disabled}
      validationMessage={error} fullWidth
      onChange={(number, unit) => onChange(encodedDuration(number, unit))}
      onUnitChange={(unit) => onChange(encodedDuration(duration.number, unit))} />;
  }
  case 'SearchField': return <SearchField label={field.label}
    value={String(value ?? '')} disabled={disabled}
    placeholder={field.placeholder || undefined} onChange={onChange} />;
  case 'EnvironmentIndicator': return <Box role="group" aria-label={field.label}
    sx={{display: 'flex', alignItems: 'center', gap: 1}}>
    <Box component="span">{field.label}</Box>
    <EnvironmentIndicator environment={value || context.environment || 'unknown'} />
  </Box>;
  case 'CodeEditor': return <Box sx={{height: 220, minHeight: 140}}>
    <CodeEditor label={field.label} value={String(value ?? '')}
      onChange={onChange} readonly={disabled} disabled={disabled}
      language={context.editorLanguages?.[field.id] || 'json'}
      error={error} /></Box>;
  case 'DataGrid': return <GridControl field={field} value={value}
    columns={gridColumns} namespace={namespace} />;
  case 'ConnectionSelector': return <ConnectionSelector label={field.label}
    connections={connections} value={value ?? ''} onChange={onChange}
    disabled={disabled}
    state={context.connectionState || 'disconnected'}
    environment={context.environment || 'unknown'} />;
  case 'ResourcePicker': return <PickerControl kind="resource" field={field}
    value={value} onChange={onChange} items={resources} disabled={disabled}
    state={state} />;
  case 'AssetPicker': return <PickerControl kind="asset" field={field}
    value={value} onChange={onChange} items={assets} disabled={disabled}
    state={state} />;
  case 'Banner': return <Banner status="info">{field.label}</Banner>;
  default: throw new TypeError(`Unsupported contract form component: ${field.component}`);
  }
}

FieldControl.propTypes = {
  field: PropTypes.object.isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  options: PropTypes.array,
  gridColumns: PropTypes.array,
  resources: PropTypes.array,
  assets: PropTypes.array,
  connections: PropTypes.array,
  context: PropTypes.object,
  state: PropTypes.string,
  error: PropTypes.string,
  namespace: PropTypes.string,
};

function StateNotice({form, state, error}) {
  if(state === 'loading') return <Skeleton lines={6} label={`Loading ${form.title}`} />;
  if(state === 'empty') return <EmptyState
    message={`${form.title} has no saved values. Complete the form to continue.`} />;
  if(state === 'permission' || state === 'permission-denied') return <Banner status="error">
    Permission denied. Values remain visible when policy permits, but actions are disabled.</Banner>;
  if(state === 'disconnected') return <Banner status="warning">
    The provider is disconnected. Last safe values are preserved and live actions are disabled.</Banner>;
  if(state === 'stale') return <Banner status="warning">
    Displayed provider data is stale. Refresh before consequential execution.</Banner>;
  if(state === 'invalid') return <Banner status="warning">
    Correct the identified validation errors before continuing.</Banner>;
  if(state === 'error' || error) return <Banner status="error">
    {error || `${form.title} could not complete. Authored values are preserved.`}</Banner>;
  return null;
}

StateNotice.propTypes = {
  form: PropTypes.object.isRequired,
  state: PropTypes.string,
  error: PropTypes.string,
};

export function ContractForm({form: formInput, initialValues=EMPTY_VALUES,
  state='default', error='', context=EMPTY_VALUES, fieldOptions=EMPTY_VALUES,
  gridColumns=EMPTY_VALUES, resources=EMPTY_LIST, assets=EMPTY_LIST,
  connections=EMPTY_LIST, executeCommand, onAction, onClose, onValuesChange,
  namespace, formCatalog=EMPTY_VALUES}) {
  const form = typeof formInput === 'string' ? formCatalog[formInput] : formInput;
  if(!form) throw new TypeError('Contract form requires a known form contract.');
  const [values, setValues] = useState(() => initialContractFormValues(form, initialValues));
  const [submitted, setSubmitted] = useState(false);
  const [busyAction, setBusyAction] = useState('');
  const [runtimeError, setRuntimeError] = useState('');
  const [wizardStep, setWizardStep] = useState(0);
  useEffect(() => {
    setValues(initialContractFormValues(form, initialValues));
    setSubmitted(false); setRuntimeError(''); setWizardStep(0);
  }, [form, initialValues]);
  const runtimeContext = useMemo(() => ({...context, values}), [context, values]);
  const validation = useMemo(() => validateContractFormValues(
    form, values, runtimeContext), [form, values, runtimeContext]);
  const currentValidation = useMemo(() => form.kind === 'wizard' &&
    wizardStep < form.sections.length ? validateContractFormValues(
      {...form, sections: [form.sections[wizardStep]]}, values, runtimeContext) :
    validation, [form, runtimeContext, validation, values, wizardStep]);
  const update = (fieldId, value) => setValues((current) => {
    const next = {...current, [fieldId]: value};
    onValuesChange?.(redactedContractFormValues(form, next));
    return next;
  });
  const invoke = async(action) => {
    const actionValidation = action.id === 'next' ? currentValidation : validation;
    if(form.kind === 'wizard' && action.id === 'back' && wizardStep === 0) return;
    if(form.kind === 'wizard' && ['test', 'create'].includes(action.id) &&
        wizardStep < form.sections.length) return;
    if(!isContractFormActionEnabled(action, form, values, actionValidation,
      runtimeContext)) return;
    if(form.kind === 'wizard' && action.id === 'back') {
      setWizardStep((current) => Math.max(0, current - 1)); return;
    }
    if(form.kind === 'wizard' && action.id === 'next') {
      setSubmitted(true);
      if(currentValidation.valid) setWizardStep((current) =>
        Math.min(form.sections.length, current + 1));
      return;
    }
    if(action.command && typeof executeCommand !== 'function') return;
    setSubmitted(true); setRuntimeError(''); setBusyAction(action.id);
    const submission = transientContractFormSubmission(form, values);
    try {
      if(action.command) await executeCommand?.(action.command, submission);
      await onAction?.(action, submission);
      if(action.closes) onClose?.(action);
    } catch(actionError) {
      setRuntimeError(actionError?.message || String(actionError));
    } finally {
      setBusyAction('');
    }
  };
  const defaultAction = form.actions.find((action) => action.default);
  const actionEnabled = (action) => {
    if(form.kind === 'wizard' && action.id === 'back' && wizardStep === 0) return false;
    if(form.kind === 'wizard' && action.id === 'next' &&
        wizardStep >= form.sections.length) return false;
    if(form.kind === 'wizard' && ['test', 'create'].includes(action.id) &&
        wizardStep < form.sections.length) return false;
    return isContractFormActionEnabled(action, form, values,
      action.id === 'next' ? currentValidation : validation, runtimeContext);
  };
  const visibleSections = form.kind === 'wizard' ?
    form.sections.slice(wizardStep, wizardStep + 1) : form.sections;
  const formDataAttribute = {[`data-${namespace}-form`]: form.form_id};
  return <Box component="form" noValidate {...formDataAttribute}
    aria-label={form.title} aria-busy={state === 'loading' || Boolean(busyAction)}
    onSubmit={(event) => { event.preventDefault(); if(defaultAction) invoke(defaultAction); }}
    onKeyDown={(event) => {
      if(event.key === 'Escape' && ['dialog', 'wizard', 'confirmation'].includes(form.kind)) {
        event.stopPropagation(); onClose?.();
      }
      if(event.key === 'Enter' && defaultAction &&
          !['TEXTAREA'].includes(event.target.tagName) &&
          !event.target.closest?.('[data-code-editor]')) {
        event.preventDefault(); invoke(defaultAction);
      }
    }}
    sx={{height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column',
      bgcolor: 'background.paper'}}>
    <Toolbar label={`${form.title} actions`} trailing={
      <Box component="span" sx={{color: 'text.secondary', fontSize: '0.75rem'}}>
        {form.kind.replaceAll('_', ' ')} · {form.size}</Box>}>
      <Box component="strong">{form.title}</Box>
    </Toolbar>
    <Box sx={{p: 1.5, overflow: 'auto', flex: 1, minHeight: 0}}>
      <Box component="p" sx={{mt: 0, color: 'text.secondary'}}>{form.purpose}</Box>
      {form.kind === 'wizard' && <Box role="status" aria-label="Wizard progress"
        sx={{mb: 1}}>Step {wizardStep + 1} of {form.sections.length + 1}: {
          wizardStep < form.sections.length ? form.sections[wizardStep].title : 'Review'}</Box>}
      <StateNotice form={form} state={state} error={runtimeError || error} />
      {visibleSections.map((section) => <FormSection key={section.id}
        title={section.title}>
        {section.help && <Box sx={{mb: 1, color: 'text.secondary'}}>{section.help}</Box>}
        <Box sx={{display: 'grid', gap: 1.5,
          gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 280px), 1fr))'}}>
          {section.fields.filter((field) => evaluateContractCondition(
            field.visibility, values, runtimeContext)).map((field) =>
            <Box key={field.id} {...{[`data-${namespace}-field`]: field.id}}
              sx={{minWidth: 0, gridColumn: ['CodeEditor', 'DataGrid', 'Banner']
                .includes(field.component) ? '1 / -1' : undefined}}>
              <FieldControl field={field} value={values[field.id]}
                onChange={(value) => update(field.id, value)}
                options={fieldOptions[field.id] || field.options}
                gridColumns={gridColumns[field.id] || []} resources={resources}
                assets={assets} connections={connections} context={runtimeContext}
                state={state} namespace={namespace}
                error={(submitted || state === 'invalid') ?
                  validation.errors[field.id] : ''} />
              {field.warning && <ValidationMessage status="warning">
                {field.warning}</ValidationMessage>}
              {(submitted || state === 'invalid') && validation.errors[field.id] &&
                ['Checkbox', 'ToggleSwitch', 'SegmentedControl', 'ResourcePicker',
                  'AssetPicker', 'DataGrid', 'ConnectionSelector',
                  'EnvironmentIndicator', 'Banner'].includes(field.component) &&
                <ValidationMessage>{validation.errors[field.id]}</ValidationMessage>}
              {field.help && ['Checkbox', 'ToggleSwitch', 'SegmentedControl',
                'ResourcePicker', 'AssetPicker', 'DataGrid', 'CodeEditor',
                'ComboBox', 'MultiSelect']
                .includes(field.component) && <FormHelperText>{field.help}</FormHelperText>}
            </Box>)}
        </Box>
      </FormSection>)}
      {form.kind === 'wizard' && wizardStep === form.sections.length &&
        <FormSection title="Review"><Box component="dl" sx={{display: 'grid',
          gridTemplateColumns: 'minmax(140px, 1fr) 2fr', gap: 0.5, m: 0}}>
          {Object.entries(redactedContractFormValues(form, values)).map(([fieldId, value]) => <Box
            key={fieldId} sx={{display: 'contents'}}><Box component="dt">{fieldId}</Box>
            <Box component="dd" sx={{m: 0, overflowWrap: 'anywhere'}}>{
              typeof value === 'object' ? JSON.stringify(value) : String(value ?? '')}</Box></Box>)}
        </Box></FormSection>}
      {form.security_notes.length > 0 && <FormSection title="Security and authority"
        defaultExpanded={false}><Box component="ul" sx={{m: 0, pl: 2}}>
          {form.security_notes.map((note) => <li key={note}>{note}</li>)}
        </Box></FormSection>}
    </Box>
    <Box sx={{display: 'flex', flexWrap: 'wrap', justifyContent: 'flex-end',
      gap: 1, p: 1, borderTop: '1px solid', borderColor: 'divider'}}>
      {form.actions.map((action) => <Button key={action.id}
        intent={action.intent} loading={busyAction === action.id}
        disabled={DISABLED_STATES.has(state) || Boolean(busyAction) ||
          (Boolean(action.command) && typeof executeCommand !== 'function') ||
          !actionEnabled(action)}
        onClick={() => invoke(action)}>{action.label}</Button>)}
    </Box>
  </Box>;
}

ContractForm.propTypes = {
  form: PropTypes.oneOfType([PropTypes.string, PropTypes.object]).isRequired,
  initialValues: PropTypes.object,
  state: PropTypes.oneOf(['default', 'loading', 'empty', 'error', 'permission',
    'permission-denied', 'disconnected', 'stale', 'invalid']),
  error: PropTypes.string,
  context: PropTypes.object,
  fieldOptions: PropTypes.object,
  gridColumns: PropTypes.object,
  resources: PropTypes.array,
  assets: PropTypes.array,
  connections: PropTypes.array,
  executeCommand: PropTypes.func,
  onAction: PropTypes.func,
  onClose: PropTypes.func,
  onValuesChange: PropTypes.func,
  namespace: PropTypes.string.isRequired,
  formCatalog: PropTypes.object,
};

export default ContractForm;
