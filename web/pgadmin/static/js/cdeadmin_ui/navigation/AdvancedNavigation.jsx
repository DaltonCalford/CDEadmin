/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {useEffect, useMemo, useState} from 'react';
import PropTypes from 'prop-types';
import {
  Box,
  Popover as MuiPopover,
  Tooltip as MuiTooltip,
} from '@mui/material';
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import {Button, IconButton} from '../primitives/Button';
import {Checkbox, Select} from '../primitives/Choice';
import {SearchField} from '../primitives/AdvancedControls';
import {Dialog} from '../overlays/Dialog';
import {EmptyState} from '../feedback/EmptyState';
import {ProgressOverlay} from '../feedback/ProgressOverlay';
import {Badge, EnvironmentIndicator} from '../feedback/Indicators';

const EMPTY_ITEMS = Object.freeze([]);

export function Popover({open, anchorEl, onClose, children, label='Details'}) {
  return <MuiPopover open={open} anchorEl={anchorEl} onClose={onClose}
    aria-label={label} anchorOrigin={{vertical: 'bottom', horizontal: 'left'}}
    slotProps={{paper: {sx: {minWidth: 220, maxWidth: 420, borderRadius: 0,
      maxHeight: 'calc(100vh - 32px)', boxShadow: 'none', border: '1px solid',
      borderColor: 'divider'}}}}>
    {children}
  </MuiPopover>;
}

Popover.propTypes = {
  open: PropTypes.bool.isRequired,
  anchorEl: PropTypes.object,
  onClose: PropTypes.func,
  children: PropTypes.node,
  label: PropTypes.string,
};

export function Tooltip({title, children, ...props}) {
  return <MuiTooltip title={title} enterDelay={500} leaveDelay={100}
    disableInteractive arrow={false} {...props}>{children}</MuiTooltip>;
}

Tooltip.propTypes = {title: PropTypes.node.isRequired, children: PropTypes.element.isRequired};

export function ListRow({label, icon, trailing, selected=false, disabled=false,
  status='default', onSelect, onOpen}) {
  return <Box role="option" aria-selected={selected} aria-disabled={disabled || undefined}
    tabIndex={disabled ? -1 : 0} data-status={status}
    onClick={() => !disabled && onSelect?.()}
    onDoubleClick={() => !disabled && onOpen?.()}
    onKeyDown={(event) => {
      if(disabled) return;
      if(event.key === 'Enter' || event.key === ' ') {
        event.preventDefault(); onSelect?.();
      }
    }}
    sx={{height: 30, px: 1, display: 'flex', alignItems: 'center', gap: 0.5,
      bgcolor: selected ? 'action.selected' : 'transparent',
      color: disabled ? 'text.disabled' : status === 'error' ? 'error.main' :
        status === 'warning' ? 'warning.main' : 'text.primary',
      '&:hover': {bgcolor: disabled ? undefined : 'action.hover'}}}>
    <Box sx={{width: 24, display: 'grid', placeItems: 'center'}}>{icon}</Box>
    <Box sx={{flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis',
      whiteSpace: 'nowrap'}} title={String(label)}>{label}</Box>
    <Box sx={{minWidth: 32}}>{trailing}</Box>
  </Box>;
}

ListRow.propTypes = {
  label: PropTypes.node.isRequired,
  icon: PropTypes.node,
  trailing: PropTypes.node,
  selected: PropTypes.bool,
  disabled: PropTypes.bool,
  status: PropTypes.oneOf(['default', 'warning', 'error']),
  onSelect: PropTypes.func,
  onOpen: PropTypes.func,
};

export function TreeRow({label, level=1, icon, expanded=false, expandable=false,
  selected=false, disabled=false, loading=false, warning='', checked,
  indeterminate=false, onToggle, onSelect, onOpen, onCheck, onContextMenu,
  trailing}) {
  return <Box role="treeitem" aria-level={level} aria-selected={selected}
    aria-expanded={expandable ? expanded : undefined}
    aria-disabled={disabled || undefined} aria-busy={loading || undefined}
    tabIndex={disabled ? -1 : 0}
    onClick={() => !disabled && onSelect?.()}
    onDoubleClick={() => !disabled && onOpen?.()}
    onContextMenu={(event) => {
      if(disabled) return;
      event.preventDefault();
      onSelect?.();
      onContextMenu?.(event);
    }}
    onKeyDown={(event) => {
      if(disabled) return;
      if(event.key === 'ArrowRight' && expandable && !expanded) onToggle?.(true);
      else if(event.key === 'ArrowLeft' && expandable && expanded) onToggle?.(false);
      else if(event.key === 'Enter') onOpen?.();
      else if(event.key === ' ') { event.preventDefault(); onSelect?.(); }
    }}
    sx={{height: 'var(--cde-tree-row-height)', pl: `calc(${level - 1} * var(--cde-tree-indent))`,
      display: 'flex', alignItems: 'center', minWidth: 0,
      bgcolor: selected ? 'action.selected' : 'transparent',
      color: disabled ? 'text.disabled' : warning ? 'warning.main' : 'text.primary',
      '&:hover': {bgcolor: disabled ? undefined : 'action.hover'}}}>
    <Box sx={{width: 'var(--cde-tree-expander-size)', display: 'grid', placeItems: 'center'}}>
      {expandable && <IconButton label={`${expanded ? 'Collapse' : 'Expand'} ${label}`}
        onClick={(event) => { event.stopPropagation(); onToggle?.(!expanded); }}>
        {expanded ? <ExpandMoreIcon /> : <ChevronRightIcon />}
      </IconButton>}
    </Box>
    {checked !== undefined && <Checkbox label="" checked={checked}
      indeterminate={indeterminate} onChange={onCheck}
      inputProps={{'aria-label': `Select ${label}`}} />}
    <Box sx={{width: 24, display: 'grid', placeItems: 'center'}}>{icon}</Box>
    <Box component="span" title={String(label)} sx={{overflow: 'hidden',
      textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1}}>{label}</Box>
    {loading && <Box component="span">Loading…</Box>}
    {warning && <Box component="span" aria-label={warning}>!</Box>}
    <Box sx={{minWidth: 28}}>{trailing}</Box>
  </Box>;
}

TreeRow.propTypes = {
  label: PropTypes.node.isRequired,
  level: PropTypes.number,
  icon: PropTypes.node,
  expanded: PropTypes.bool,
  expandable: PropTypes.bool,
  selected: PropTypes.bool,
  disabled: PropTypes.bool,
  loading: PropTypes.bool,
  warning: PropTypes.string,
  checked: PropTypes.bool,
  indeterminate: PropTypes.bool,
  onToggle: PropTypes.func,
  onSelect: PropTypes.func,
  onOpen: PropTypes.func,
  onCheck: PropTypes.func,
  onContextMenu: PropTypes.func,
  trailing: PropTypes.node,
};

export function KeyboardShortcutHint({shortcut, disabled=false}) {
  return <Box component="kbd" aria-disabled={disabled || undefined}
    sx={{minWidth: 48, px: 0.5, textAlign: 'right', opacity: disabled ? 0.5 : 1,
      fontFamily: 'var(--cde-font-ui)', fontSize: '0.75rem'}}>{shortcut}</Box>;
}

KeyboardShortcutHint.propTypes = {
  shortcut: PropTypes.string.isRequired,
  disabled: PropTypes.bool,
};

export function Pagination({page=1, pageSize=50, count, loading=false,
  onChange, label='Result pages'}) {
  const pages = Number.isFinite(count) ? Math.max(1, Math.ceil(count / pageSize)) : null;
  const start = (page - 1) * pageSize + 1;
  const end = Number.isFinite(count) ? Math.min(count, page * pageSize) : page * pageSize;
  return <Box role="navigation" aria-label={label} sx={{minHeight: 'var(--cde-control-height)',
    display: 'flex', alignItems: 'center', gap: 0.5}}>
    <IconButton label="Previous page" disabled={loading || page <= 1}
      onClick={() => onChange?.(page - 1)}><ChevronLeftIcon /></IconButton>
    <Box component="span">Page {page}{pages ? ` of ${pages}` : ''}</Box>
    <IconButton label="Next page" disabled={loading || (pages && page >= pages)}
      onClick={() => onChange?.(page + 1)}><ChevronRightIcon /></IconButton>
    <Box component="span" sx={{ml: 1}}>{Number.isFinite(count) ?
      `${count ? start : 0}–${end} of ${count}` : `${start}–${end}`}</Box>
  </Box>;
}

Pagination.propTypes = {
  page: PropTypes.number,
  pageSize: PropTypes.number,
  count: PropTypes.number,
  loading: PropTypes.bool,
  onChange: PropTypes.func,
  label: PropTypes.string,
};

export function ColumnPicker({open, anchorEl, columns=[], visible=[], onChange,
  onReset, onClose, disabled=false}) {
  const [query, setQuery] = useState('');
  const filtered = columns.filter((column) => String(column.label ?? column.name)
    .toLowerCase().includes(query.toLowerCase()));
  return <Popover open={open} anchorEl={anchorEl} onClose={onClose}
    label="Choose visible columns">
    <Box sx={{width: 320, maxHeight: 420, display: 'flex', flexDirection: 'column'}}>
      <SearchField label="Filter columns" value={query} onChange={setQuery}
        disabled={disabled} />
      <Box sx={{overflow: 'auto', flex: 1}}>
        {filtered.map((column) => <Checkbox key={column.name}
          label={column.label ?? column.name} checked={visible.includes(column.name)}
          disabled={disabled || column.disabled}
          onChange={(checked) => onChange?.(checked ?
            [...visible, column.name] : visible.filter((name) => name !== column.name))} />)}
      </Box>
      <Box sx={{p: 1, borderTop: '1px solid', borderColor: 'divider'}}>
        <Button disabled={disabled} onClick={onReset}>Restore default columns</Button>
      </Box>
    </Box>
  </Popover>;
}

ColumnPicker.propTypes = {
  open: PropTypes.bool.isRequired,
  anchorEl: PropTypes.object,
  columns: PropTypes.array,
  visible: PropTypes.array,
  onChange: PropTypes.func,
  onReset: PropTypes.func,
  onClose: PropTypes.func,
  disabled: PropTypes.bool,
};

function EntityPicker({kind, open, title, items=EMPTY_ITEMS, selected=EMPTY_ITEMS, multiple=false,
  loading=false, error='', state='default', onClose, onConfirm}) {
  const [query, setQuery] = useState('');
  const [choice, setChoice] = useState(selected);
  useEffect(() => { if(open) setChoice(selected); }, [open, selected]);
  const filtered = items.filter((item) => `${item.label} ${item.path || ''} ${item.type || ''}`
    .toLowerCase().includes(query.toLowerCase()));
  const toggle = (item) => setChoice((current) => multiple ?
    current.some((value) => value.id === item.id) ?
      current.filter((value) => value.id !== item.id) : [...current, item] : [item]);
  return <Dialog open={open} title={title} size="medium" onClose={onClose}
    actions={<><Button onClick={onClose}>Cancel</Button>
      <Button intent="primary" disabled={!choice.length}
        onClick={() => onConfirm?.(multiple ? choice : choice[0])}>Select</Button></>}>
    <Box data-picker-kind={kind} sx={{height: 420, position: 'relative',
      display: 'flex', flexDirection: 'column'}}>
      <SearchField label={`Filter ${kind === 'asset' ? 'project assets' :
        kind === 'credential' ? 'credential references' : 'resources'}`}
      value={query} onChange={setQuery} />
      {(error || ['permission', 'disconnected'].includes(state)) && <EmptyState
        message={error || (state === 'permission' ? 'You do not have permission to view these items.' :
          'The live provider connection is disconnected.')} role="alert" />}
      {!error && !loading && state !== 'permission' && state !== 'disconnected' &&
        !filtered.length && <EmptyState
        message={`No matching ${kind === 'asset' ? 'project assets' :
          kind === 'credential' ? 'credential references' : 'resources'}`} />}
      <Box role="listbox" aria-multiselectable={multiple || undefined} sx={{overflow: 'auto'}}>
        {filtered.map((item) => <ListRow key={item.id} label={item.label}
          selected={choice.some((value) => value.id === item.id)}
          trailing={<Badge label={item.type || kind} />}
          onSelect={() => toggle(item)}
          onOpen={() => { toggle(item); if(!multiple) onConfirm?.(item); }} />)}
      </Box>
      <ProgressOverlay message={loading ? `Loading ${kind}s` : ''} />
    </Box>
  </Dialog>;
}

EntityPicker.propTypes = {
  kind: PropTypes.oneOf(['asset', 'resource', 'credential']).isRequired,
  open: PropTypes.bool.isRequired,
  title: PropTypes.string.isRequired,
  items: PropTypes.array,
  selected: PropTypes.array,
  multiple: PropTypes.bool,
  loading: PropTypes.bool,
  error: PropTypes.string,
  state: PropTypes.oneOf(['default', 'permission', 'disconnected']),
  onClose: PropTypes.func,
  onConfirm: PropTypes.func,
};

export function ResourcePicker(props) {
  return <EntityPicker kind="resource" title="Select provider resource" {...props} />;
}

export function AssetPicker(props) {
  return <EntityPicker kind="asset" title="Select project asset" {...props} />;
}

export function SecretPicker(props) {
  return <EntityPicker kind="credential" title="Select credential reference" {...props} />;
}

export function ConnectionSelector({connections=[], value='', onChange,
  state='disconnected', environment='unknown', label='Connection'}) {
  const options = connections.map((item) => ({value: item.id, label:
    `${item.provider ? `${item.provider}: ` : ''}${item.label}`}));
  return <Box sx={{display: 'flex', alignItems: 'center', gap: 0.5}}>
    <Select label={label} options={options} value={value} onChange={onChange}
      sx={{minWidth: 180, maxWidth: 360}} />
    <Badge status={state === 'connected' ? 'success' :
      ['connecting', 'warning', 'production'].includes(state) ? 'warning' :
        state === 'readonly' ? 'info' : 'error'} label={state} />
    <EnvironmentIndicator environment={environment} />
  </Box>;
}

ConnectionSelector.propTypes = {
  connections: PropTypes.array,
  value: PropTypes.any,
  onChange: PropTypes.func,
  state: PropTypes.oneOf(['connected', 'connecting', 'disconnected', 'readonly',
    'production', 'warning']),
  environment: PropTypes.string,
  label: PropTypes.string,
};

export function CommandPalette({open, commands=EMPTY_ITEMS, resources=EMPTY_ITEMS,
  assets=EMPTY_ITEMS, recent=EMPTY_ITEMS, settings=EMPTY_ITEMS, onClose,
  onInvoke, loading=false}) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const groups = useMemo(() => [
    ['Commands', commands], ['Resources', resources], ['Project Assets', assets],
    ['Recent', recent], ['Settings', settings],
  ].map(([name, values]) => [name, values.filter((item) =>
    `${item.label} ${item.description || ''}`.toLowerCase().includes(query.toLowerCase()))])
    .filter(([, values]) => values.length),
  [commands, resources, assets, recent, settings, query]);
  const flat = groups.flatMap(([, values]) => values);
  useEffect(() => setActive(0), [query, open]);
  const invoke = (item) => { if(item && !item.disabled) { onInvoke?.(item); onClose?.(); } };
  return <Dialog open={open} title="Command palette" size="medium" onClose={onClose}>
    <Box onKeyDown={(event) => {
      if(event.key === 'ArrowDown') { event.preventDefault(); setActive((value) =>
        Math.min(flat.length - 1, value + 1)); }
      if(event.key === 'ArrowUp') { event.preventDefault(); setActive((value) =>
        Math.max(0, value - 1)); }
      if(event.key === 'Enter') { event.preventDefault(); invoke(flat[active]); }
    }} sx={{width: 672, maxWidth: '100%', maxHeight: 560, position: 'relative'}}>
      <SearchField autoFocus label="Search commands, resources, assets, and settings"
        value={query} onChange={setQuery} loading={loading} />
      {!loading && !flat.length && <EmptyState message="No matching commands or items" />}
      <Box role="listbox" sx={{overflow: 'auto', maxHeight: 500}}>
        {groups.map(([group, values]) => <Box key={group}>
          <Box component="h3" sx={{m: 0, px: 1, py: 0.5, fontSize: '0.7rem',
            textTransform: 'uppercase'}}>{group}</Box>
          {values.map((item) => {
            const index = flat.indexOf(item);
            return <ListRow key={`${group}-${item.id}`} label={item.label}
              selected={active === index} disabled={item.disabled}
              trailing={item.shortcut} onSelect={() => setActive(index)}
              onOpen={() => invoke(item)} />;
          })}
        </Box>)}
      </Box>
      <ProgressOverlay message={loading ? 'Searching' : ''} />
    </Box>
  </Dialog>;
}

CommandPalette.propTypes = {
  open: PropTypes.bool.isRequired,
  commands: PropTypes.array,
  resources: PropTypes.array,
  assets: PropTypes.array,
  recent: PropTypes.array,
  settings: PropTypes.array,
  onClose: PropTypes.func,
  onInvoke: PropTypes.func,
  loading: PropTypes.bool,
};
