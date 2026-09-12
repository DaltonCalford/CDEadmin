/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {useEffect, useMemo, useRef, useState} from 'react';
import PropTypes from 'prop-types';
import {Box, Step, StepLabel, Stepper} from '@mui/material';
import {Button} from '../primitives/Button';
import {NumberField, SecretField, TextField} from '../primitives/Field';
import {Select} from '../primitives/Choice';
import {SearchField} from '../primitives/AdvancedControls';
import {Dialog} from './Dialog';
import {ListRow} from '../navigation/AdvancedNavigation';
import {ValidationMessage} from '../feedback/Indicators';
import {ZERO_GREY_TOKENS} from '../foundations/tokens';
import {contrastRatio} from '../foundations/presentation';

const APPROVED_FONTS = Object.freeze([
  {value: 'Fira Code', label: 'Fira Code'},
  {value: 'ui-monospace', label: 'System monospace'},
  {value: 'monospace', label: 'Generic monospace'},
  {value: 'sans-serif', label: 'Generic sans serif'},
  {value: 'serif', label: 'Generic serif'},
]);
const EMPTY_ITEMS = Object.freeze([]);
const EMPTY_VALUES = Object.freeze({});

export function UnsavedChangesDialog({open, asset, busy=false, onSave,
  onDiscard, onCancel}) {
  return <Dialog open={open} title={`Save changes to “${asset}”?`} size="small"
    busy={busy} onClose={onCancel} defaultAction={onSave} actions={<>
      <Button disabled={busy} onClick={onDiscard}>Don’t Save</Button>
      <Box sx={{flex: 1}} />
      <Button disabled={busy} onClick={onCancel}>Cancel</Button>
      <Button intent="primary" loading={busy} onClick={onSave}>Save</Button>
    </>}>
    Your changes have not been saved to the project authority.
  </Dialog>;
}

UnsavedChangesDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  asset: PropTypes.string.isRequired,
  busy: PropTypes.bool,
  onSave: PropTypes.func.isRequired,
  onDiscard: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

export function DestructiveConfirmationDialog({open, verb, targetIdentity,
  environment='', consequence, dependencies=EMPTY_ITEMS, reversibility,
  highImpact=false, busy=false, onConfirm, onCancel}) {
  const requiredText = highImpact && environment.toLowerCase() === 'production' ?
    targetIdentity : '';
  const [typed, setTyped] = useState('');
  useEffect(() => { if(open) setTyped(''); }, [open]);
  const confirmed = !requiredText || typed === requiredText;
  return <Dialog open={open} title={`${verb} ${targetIdentity}?`} size="medium"
    busy={busy} onClose={onCancel} actions={<>
      <Button disabled={busy} onClick={onCancel}>Cancel</Button>
      <Button intent="destructive" loading={busy} disabled={!confirmed}
        onClick={onConfirm}>{verb}</Button>
    </>}>
    <Box sx={{display: 'grid', gap: 1}}>
      <Box><strong>Target:</strong> {targetIdentity}</Box>
      {environment && <Box><strong>Environment:</strong> {environment}</Box>}
      <Box><strong>Consequence:</strong> {consequence}</Box>
      <Box><strong>Dependencies:</strong> {dependencies.length ?
        dependencies.join(', ') : 'None known'}</Box>
      <Box><strong>Reversibility:</strong> {reversibility}</Box>
      {requiredText && <TextField label={`Type “${requiredText}” to confirm`}
        value={typed} onChange={(event) => setTyped(event.target.value)} />}
    </Box>
  </Dialog>;
}

DestructiveConfirmationDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  verb: PropTypes.string.isRequired,
  targetIdentity: PropTypes.string.isRequired,
  environment: PropTypes.string,
  consequence: PropTypes.node.isRequired,
  dependencies: PropTypes.array,
  reversibility: PropTypes.node.isRequired,
  highImpact: PropTypes.bool,
  busy: PropTypes.bool,
  onConfirm: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

function initialCredentials(fields) {
  return Object.fromEntries(fields.map((field) => [field.id, field.defaultValue ?? '']));
}

export function CredentialsDialog({open, connection, authenticationMethod,
  fields=EMPTY_ITEMS, busy=false, error='', onConnect, onCancel}) {
  const [values, setValues] = useState(() => initialCredentials(fields));
  const fieldsRef = useRef(fields);
  fieldsRef.current = fields;
  const fieldSignature = JSON.stringify(fields);
  useEffect(() => {
    if(open) setValues(initialCredentials(fieldsRef.current));
  }, [open, fieldSignature]);
  const valid = fields.every((field) => !field.required || Boolean(values[field.id]));
  const submit = () => valid && !busy && onConnect(values);
  return <Dialog open={open} title={`Authenticate to ${connection}`} size="small"
    busy={busy} validationError={error} onClose={onCancel}
    defaultAction={valid ? submit : undefined} actions={<>
      <Button disabled={busy} onClick={onCancel}>Cancel</Button>
      <Button intent="primary" loading={busy} disabled={!valid}
        onClick={submit}>Connect</Button>
    </>}>
    <Box sx={{display: 'grid', gap: 1}}>
      <Box><strong>Connection:</strong> {connection}</Box>
      <Box><strong>Authentication:</strong> {authenticationMethod}</Box>
      {fields.map((field) => field.type === 'select' ?
        <Select key={field.id} label={field.label} value={values[field.id]}
          options={field.options ?? []} onChange={(value) => setValues((current) => ({
            ...current, [field.id]: value,
          }))} /> : field.type === 'secret' ?
          <SecretField key={field.id} label={field.label} value={values[field.id]}
            allowReveal={field.allowReveal !== false}
            autoComplete={field.autoComplete ?? 'current-password'}
            onChange={(event) => setValues((current) => ({
              ...current, [field.id]: event.target.value,
            }))} /> :
          <TextField key={field.id} label={field.label} value={values[field.id]}
            onChange={(event) => setValues((current) => ({
              ...current, [field.id]: event.target.value,
            }))} />)}
    </Box>
  </Dialog>;
}

CredentialsDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  connection: PropTypes.string.isRequired,
  authenticationMethod: PropTypes.string.isRequired,
  fields: PropTypes.array,
  busy: PropTypes.bool,
  error: PropTypes.node,
  onConnect: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

export function SimpleInputDialog({open, title, label, value='', validation,
  busy=false, onApply, onCancel}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => { if(open) setDraft(value); }, [open, value]);
  const message = typeof validation === 'function' ? validation(draft) : '';
  const submit = () => !message && !busy && onApply(draft);
  return <Dialog open={open} title={title} size="small" busy={busy}
    validationError={message} onClose={onCancel}
    defaultAction={!message ? submit : undefined} actions={<>
      <Button disabled={busy} onClick={onCancel}>Cancel</Button>
      <Button intent="primary" loading={busy} disabled={Boolean(message)}
        onClick={submit}>Apply</Button>
    </>}>
    <TextField label={label} value={draft} validationMessage={message}
      onChange={(event) => setDraft(event.target.value)} />
  </Dialog>;
}

SimpleInputDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  title: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  value: PropTypes.string,
  validation: PropTypes.func,
  busy: PropTypes.bool,
  onApply: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

export function FontPicker({open, value='Fira Code', size=12, onClose,
  onConfirm}) {
  const [draft, setDraft] = useState({family: value, size});
  useEffect(() => { if(open) setDraft({family: value, size}); }, [open, value, size]);
  return <Dialog open={open} title="Choose font" size="small" onClose={onClose}
    actions={<><Button onClick={onClose}>Cancel</Button><Button intent="primary"
      onClick={() => onConfirm?.(draft)}>Apply font</Button></>}>
    <Box sx={{width: 320, maxWidth: '100%', display: 'grid', gap: 1}}>
      <Select label="Font family" options={APPROVED_FONTS} value={draft.family}
        onChange={(family) => setDraft((current) => ({...current, family}))} />
      <NumberField label="Font size" value={draft.size} inputProps={{min: 8, max: 72}}
        onChange={(event) => setDraft((current) => ({...current,
          size: Number(event.target.value)}))} />
      <Box aria-label="Font preview" sx={{height: 64, p: 1, border: '1px solid',
        borderColor: 'divider', fontFamily: draft.family, fontSize: draft.size}}>
        ScratchRobin CDE Admin — Aa 0123
      </Box>
    </Box>
  </Dialog>;
}

FontPicker.propTypes = {
  open: PropTypes.bool.isRequired,
  value: PropTypes.string,
  size: PropTypes.number,
  onClose: PropTypes.func,
  onConfirm: PropTypes.func,
};

const HEX = /^#[0-9A-F]{6}$/i;

export function ColorPicker({open, value='#0077B6', background='#FFFFFF',
  minimumContrast=0, allowAlpha=false, recent=EMPTY_ITEMS, onClose, onConfirm}) {
  const [draft, setDraft] = useState(value.toUpperCase());
  useEffect(() => { if(open) setDraft(value.toUpperCase()); }, [open, value]);
  const palettes = useMemo(() => {
    const theme = [...new Set(Object.values(ZERO_GREY_TOKENS.theme.light)
      .filter((item) => HEX.test(item)))];
    const visualization = [...new Set(
      ZERO_GREY_TOKENS.data_visualization.qualitative_light
    )];
    return [
      {id: 'theme', label: 'Theme', values: theme},
      {id: 'visualization', label: 'Data Visualization', values: visualization},
      {id: 'recent', label: 'Recent', values: [...new Set(
        recent.map((item) => String(item).toUpperCase())
      )]},
    ];
  }, [recent]);
  const valid = (allowAlpha ? /^#[0-9A-F]{6}(?:[0-9A-F]{2})?$/i : HEX).test(draft);
  const contrast = valid ? contrastRatio(draft.slice(0, 7), background) : 0;
  const safe = valid && contrast >= minimumContrast;
  return <Dialog open={open} title="Choose colour" size="small" onClose={onClose}
    actions={<><Button onClick={onClose}>Cancel</Button><Button intent="primary"
      disabled={!safe} onClick={() => onConfirm?.(draft.toUpperCase())}>Apply colour</Button></>}>
    <Box sx={{width: 336, maxWidth: '100%', display: 'grid', gap: 1}}>
      {palettes.filter((section) => section.values.length).map((section) =>
        <Box component="section" aria-label={section.label} key={section.id}>
          <Box component="h3" sx={{m: 0, fontSize: '0.75rem'}}>{section.label}</Box>
          <Box role="listbox" aria-label={section.label + ' colours'}
            sx={{display: 'grid', gridTemplateColumns: 'repeat(8, 24px)', gap: '12px'}}>
            {section.values.map((color) => <Box component="button" type="button"
              role="option" aria-selected={draft === color} aria-label={color}
              title={color} key={color} onClick={() => setDraft(color)}
              sx={{width: 24, height: 24, p: 0, bgcolor: color,
                border: draft === color ? '2px solid' : '1px solid',
                borderColor: 'text.primary', cursor: 'pointer'}} />)}
          </Box>
        </Box>)}
      <Box component="section" aria-label="Custom colour">
        <Box component="h3" sx={{m: 0, fontSize: '0.75rem'}}>Custom</Box>
        <TextField label="Hex colour" value={draft}
          onChange={(event) => setDraft(event.target.value.toUpperCase())}
          validationMessage={!valid ? 'Use #RRGGBB notation.' :
            !safe ? `Contrast ${contrast.toFixed(2)} is below ${minimumContrast}.` : ''} />
      </Box>
      <Box sx={{height: 64, p: 1, bgcolor: background, color: valid ? draft : 'text.primary',
        border: '1px solid', borderColor: 'divider'}}>Colour preview</Box>
    </Box>
  </Dialog>;
}

ColorPicker.propTypes = {
  open: PropTypes.bool.isRequired,
  value: PropTypes.string,
  background: PropTypes.string,
  minimumContrast: PropTypes.number,
  allowAlpha: PropTypes.bool,
  recent: PropTypes.array,
  onClose: PropTypes.func,
  onConfirm: PropTypes.func,
};

function redactDiagnostics(value) {
  const forbidden = /password|passwd|secret|token|credential|private.?key|connection.?string/i;
  if(Array.isArray(value)) return value.map(redactDiagnostics);
  if(value && typeof value === 'object') return Object.fromEntries(
    Object.entries(value).filter(([key]) => !forbidden.test(key))
      .map(([key, child]) => [key, redactDiagnostics(child)])
  );
  return value;
}

export function AboutDialog({open, product='ScratchRobin CDE Admin', version,
  build, logo, diagnostics={}, credits, onClose, onCopyDiagnostics, onCredits}) {
  const safeDiagnostics = useMemo(() => redactDiagnostics({product, version, build,
    ...diagnostics}), [product, version, build, diagnostics]);
  const copy = async () => {
    const text = JSON.stringify(safeDiagnostics, null, 2);
    await onCopyDiagnostics?.(text);
    if(!onCopyDiagnostics && window.navigator?.clipboard) {
      await window.navigator.clipboard.writeText(text);
    }
  };
  return <Dialog open={open} title={`About ${product}`} size="medium" onClose={onClose}
    actions={<><Button onClick={copy}>Copy Diagnostics</Button>
      <Button onClick={() => onCredits?.(credits)}>Credits &amp; Licenses</Button>
      <Button intent="primary" onClick={onClose}>Close</Button></>}>
    <Box sx={{minHeight: 316, textAlign: 'center'}}>
      <Box sx={{width: 72, height: 72, mx: 'auto'}}>{logo}</Box>
      <Box component="h2">{product}</Box>
      <Box>Version {version || 'unavailable'}{build ? ` (${build})` : ''}</Box>
      <Box component="pre" sx={{mt: 2, p: 1, textAlign: 'left', overflow: 'auto',
        bgcolor: 'background.default'}}>{JSON.stringify(safeDiagnostics, null, 2)}</Box>
    </Box>
  </Dialog>;
}

AboutDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  product: PropTypes.string,
  version: PropTypes.string,
  build: PropTypes.string,
  logo: PropTypes.node,
  diagnostics: PropTypes.object,
  credits: PropTypes.any,
  onClose: PropTypes.func,
  onCopyDiagnostics: PropTypes.func,
  onCredits: PropTypes.func,
};

export function Wizard({open, title, steps=EMPTY_ITEMS, initialStep=0, onCancel, onFinish}) {
  const [active, setActive] = useState(initialStep);
  const [data, setData] = useState({});
  const [error, setError] = useState('');
  useEffect(() => { if(open) { setActive(initialStep); setData({}); setError(''); } },
    [open, initialStep]);
  const current = steps[active];
  const next = async () => {
    const result = await current?.validate?.(data);
    if(result === false || typeof result === 'string') {
      setError(typeof result === 'string' ? result : 'Complete this step before continuing.');
      return;
    }
    setError('');
    if(active === steps.length - 1) onFinish?.(data);
    else setActive((value) => value + 1);
  };
  return <Dialog open={open} title={title} size="large" onClose={onCancel}
    actions={<><Button onClick={onCancel}>Cancel</Button>
      <Button disabled={active === 0} onClick={() => setActive((value) => value - 1)}>Back</Button>
      <Button intent="primary" onClick={next}>{active === steps.length - 1 ?
        (current?.finishLabel || 'Finish') : 'Next'}</Button></>}>
    <Box sx={{width: 800, maxWidth: '100%'}}>
      <Stepper activeStep={active}>{steps.map((step) => <Step key={step.id || step.label}>
        <StepLabel error={Boolean(error) && steps[active] === step}>{step.label}</StepLabel>
      </Step>)}</Stepper>
      {error && <ValidationMessage>{error}</ValidationMessage>}
      <Box sx={{pt: 2}}>{current?.render?.({data, update: (values) =>
        setData((existing) => ({...existing, ...values}))}) ?? current?.content}</Box>
    </Box>
  </Dialog>;
}

Wizard.propTypes = {
  open: PropTypes.bool.isRequired,
  title: PropTypes.string.isRequired,
  steps: PropTypes.array.isRequired,
  initialStep: PropTypes.number,
  onCancel: PropTypes.func,
  onFinish: PropTypes.func,
};

export function PreferencesSurface({sections=EMPTY_ITEMS, values=EMPTY_VALUES,
  onPreview, onApply, onReset, restartRequired=EMPTY_ITEMS}) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(sections[0]?.id || '');
  const [draft, setDraft] = useState(values);
  useEffect(() => setDraft(values), [values]);
  const filtered = sections.filter((section) => `${section.label} ${section.keywords || ''}`
    .toLowerCase().includes(query.toLowerCase()));
  const section = filtered.find((item) => item.id === selected) || filtered[0];
  const update = (changes, visual=true) => {
    const next = {...draft, ...changes}; setDraft(next);
    if(visual) onPreview?.(next, changes);
  };
  const dirty = JSON.stringify(draft) !== JSON.stringify(values);
  return <Box data-cde-surface="preferences" sx={{height: '100%', display: 'grid',
    gridTemplateColumns: '220px minmax(0, 860px)', gridTemplateRows: 'auto 1fr auto'}}>
    <Box sx={{gridColumn: '1 / -1', p: 1}}><SearchField label="Search settings"
      value={query} onChange={setQuery} /></Box>
    <Box component="nav" aria-label="Preference sections" sx={{borderRight: '1px solid',
      borderColor: 'divider', overflow: 'auto'}}>{filtered.map((item) => <ListRow
        key={item.id} label={item.label} selected={item.id === section?.id}
        onSelect={() => setSelected(item.id)} />)}</Box>
    <Box component="main" sx={{p: 3, overflow: 'auto'}}>
      {!section && <ValidationMessage status="info">
        No preference sections match this search.
      </ValidationMessage>}
      {section?.render?.({values: draft, update}) ?? section?.content}
      {restartRequired.some((key) => draft[key] !== values[key]) &&
        <ValidationMessage status="warning">Restart required for one or more changes.</ValidationMessage>}
    </Box>
    <Box sx={{gridColumn: '1 / -1', display: 'flex', justifyContent: 'flex-end',
      gap: 1, p: 1, borderTop: '1px solid', borderColor: 'divider'}}>
      <Button disabled={!dirty} onClick={() => { setDraft(values); onReset?.(values); }}>Reset</Button>
      <Button intent="primary" disabled={!dirty} onClick={() => onApply?.(draft)}>Apply</Button>
    </Box>
  </Box>;
}

PreferencesSurface.propTypes = {
  sections: PropTypes.array,
  values: PropTypes.object,
  onPreview: PropTypes.func,
  onApply: PropTypes.func,
  onReset: PropTypes.func,
  restartRequired: PropTypes.array,
};
