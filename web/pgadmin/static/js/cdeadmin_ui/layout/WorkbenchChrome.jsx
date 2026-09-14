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
import {Box, Collapse} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import DragIndicatorIcon from '@mui/icons-material/DragIndicator';
import {IconButton} from '../primitives/Button';

export function Toolbar({label='Toolbar', children, trailing}) {
  return <Box role="toolbar" aria-label={label}
    sx={{minHeight: 'var(--cde-toolbar-height)', display: 'flex',
      alignItems: 'center', gap: 0.5, px: 0.5, borderBottom: '1px solid',
      borderColor: 'divider', overflow: 'hidden'}}>
    {children}
    {trailing && <Box sx={{ml: 'auto'}}>{trailing}</Box>}
  </Box>;
}

Toolbar.propTypes = {
  label: PropTypes.string,
  children: PropTypes.node,
  trailing: PropTypes.node,
};

export function StatusBar({status='normal', label='Surface status', children}) {
  return <Box role="status" aria-label={label} data-status={status}
    sx={{height: 'var(--cde-status-height)', flex: '0 0 auto', display: 'flex',
      alignItems: 'center', px: 1, gap: 1, borderTop: '1px solid',
      borderColor: status === 'normal' ? 'divider' :
        status === 'warning' ? 'warning.main' : 'error.main'}}>
    {children}
  </Box>;
}

StatusBar.propTypes = {
  status: PropTypes.oneOf(['normal', 'warning', 'disconnected']),
  label: PropTypes.string,
  children: PropTypes.node,
};

export function Splitter({orientation='vertical', value, min=0, max=Infinity,
  onChange, label='Resize panels'}) {
  const start = useRef(null);
  const coordinate = (event) => orientation === 'vertical' ? event.clientX : event.clientY;
  const clamp = (next) => Math.max(min, Math.min(max, next));
  const begin = (event) => {
    event.currentTarget.setPointerCapture?.(event.pointerId);
    start.current = {coordinate: coordinate(event), value};
  };
  const move = (event) => {
    if(!start.current) return;
    onChange?.(clamp(start.current.value + coordinate(event) - start.current.coordinate));
  };
  const end = (event) => {
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    start.current = null;
  };
  const vertical = orientation === 'vertical';
  return <Box role="separator" tabIndex={0} aria-label={label}
    aria-orientation={orientation} aria-valuenow={Math.round(value)}
    aria-valuemin={min} aria-valuemax={Number.isFinite(max) ? max : undefined}
    onPointerDown={begin} onPointerMove={move} onPointerUp={end}
    onKeyDown={(event) => {
      const decrease = vertical ? event.key === 'ArrowLeft' : event.key === 'ArrowUp';
      const increase = vertical ? event.key === 'ArrowRight' : event.key === 'ArrowDown';
      if(!decrease && !increase) return;
      event.preventDefault();
      const step = event.shiftKey ? 16 : 4;
      onChange?.(clamp(value + (increase ? step : -step)));
    }}
    sx={{position: 'relative', flex: '0 0 auto', cursor: vertical ?
      'col-resize' : 'row-resize', width: vertical ?
      'calc(var(--cde-resize-handle-size, 8) * 1px)' : '100%', height: vertical ?
      '100%' : 'calc(var(--cde-resize-handle-size, 8) * 1px)', outline: 0,
    '&::after': {content: '""', position: 'absolute', bgcolor: 'divider',
      ...(vertical ? {width: 1, top: 0, bottom: 0, left: '50%'} :
        {height: 1, left: 0, right: 0, top: '50%'})},
    '&:hover::after, &:focus-visible::after': {bgcolor: 'primary.main'}}} />;
}

Splitter.propTypes = {
  orientation: PropTypes.oneOf(['vertical', 'horizontal']),
  value: PropTypes.number.isRequired,
  min: PropTypes.number,
  max: PropTypes.number,
  onChange: PropTypes.func,
  label: PropTypes.string,
};

export function Drawer({open=true, label='Drawer', children, height=240,
  onHeightChange}) {
  return <Box component="section" aria-label={label} hidden={!open}
    sx={{height: open ? `min(60vh, ${Math.max(120, height)}px)` : 0,
      display: open ? 'flex' : 'none', flexDirection: 'column', minHeight: 120,
      borderTop: '1px solid', borderColor: 'divider'}}>
    <Splitter orientation="horizontal" value={height} min={120}
      max={Math.round((window.innerHeight || 800) * 0.6)}
      onChange={onHeightChange} label={`Resize ${label}`} />
    {children}
  </Box>;
}

Drawer.propTypes = {
  open: PropTypes.bool,
  label: PropTypes.string,
  children: PropTypes.node,
  height: PropTypes.number,
  onHeightChange: PropTypes.func,
};

export function DropZone({label='Drop items', validate=() => ({valid: true}),
  onDrop, children}) {
  const [state, setState] = useState({kind: 'idle', reason: ''});
  const inspect = (event) => {
    event.preventDefault();
    const result = validate(event.dataTransfer) || {};
    setState({kind: result.valid === false ? 'drag_invalid' : 'drag_valid',
      reason: String(result.reason || '')});
    if(result.valid !== false) event.dataTransfer.dropEffect = 'copy';
    return result;
  };
  return <Box role="group" aria-label={label} tabIndex={0}
    data-drop-state={state.kind}
    onDragEnter={inspect} onDragOver={inspect}
    onDragLeave={() => setState({kind: 'idle', reason: ''})}
    onKeyDown={(event) => event.key === 'Escape' &&
      setState({kind: 'idle', reason: ''})}
    onDrop={(event) => {
      const result = inspect(event);
      if(result.valid !== false) onDrop?.(event.dataTransfer, event);
      setState({kind: 'idle', reason: ''});
    }}
    sx={{minHeight: 64, m: 0.5, border: '2px solid',
      borderStyle: state.kind === 'drag_invalid' ? 'dashed' : 'solid',
      borderColor: state.kind === 'drag_invalid' ? 'error.main' :
        state.kind === 'drag_valid' ? 'primary.main' : 'divider',
      bgcolor: state.kind === 'drag_valid' ? 'action.selected' : 'transparent',
      cursor: state.kind === 'drag_invalid' ? 'not-allowed' : 'default'}}>
    {children}
    {state.reason && <Box role="alert" sx={{color: 'error.main'}}>{state.reason}</Box>}
  </Box>;
}

DropZone.propTypes = {
  label: PropTypes.string,
  validate: PropTypes.func,
  onDrop: PropTypes.func,
  children: PropTypes.node,
};

function CollapsibleSection({title, children, defaultExpanded=true,
  disabled=false, locked=false, kind='section'}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  return <Box component="section" data-section-kind={kind}
    sx={{borderBottom: '1px solid', borderColor: 'divider'}}>
    <Box component="h3" sx={{m: 0, minHeight: 28, display: 'flex',
      alignItems: 'center', fontSize: '0.75rem', fontWeight: 600}}>
      <IconButton label={`${expanded ? 'Collapse' : 'Expand'} ${title}`}
        disabled={disabled || locked} onClick={() => setExpanded((value) => !value)}>
        {expanded ? <ExpandMoreIcon /> : <ChevronRightIcon />}
      </IconButton>
      {title}
    </Box>
    <Collapse in={expanded}><Box sx={{p: kind === 'inspector' ? 1.5 : 0,
      pb: 1.5}}>{children}</Box></Collapse>
  </Box>;
}

CollapsibleSection.propTypes = {
  title: PropTypes.node.isRequired,
  children: PropTypes.node,
  defaultExpanded: PropTypes.bool,
  disabled: PropTypes.bool,
  locked: PropTypes.bool,
  kind: PropTypes.string,
};

export function InspectorSection(props) {
  return <CollapsibleSection kind="inspector" {...props} />;
}

export function FormSection(props) {
  return <CollapsibleSection kind="form" {...props} />;
}

export function ToolboxItem({label, icon, payload, onInsert, disabled=false}) {
  return <Box role="button" tabIndex={disabled ? -1 : 0}
    aria-disabled={disabled || undefined} draggable={!disabled}
    onDoubleClick={() => !disabled && onInsert?.(payload)}
    onKeyDown={(event) => {
      if(!disabled && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault(); onInsert?.(payload);
      }
    }}
    onDragStart={(event) => {
      if(disabled) return;
      event.dataTransfer.effectAllowed = 'copy';
      event.dataTransfer.setData('application/x-cdeadmin-toolbox-item',
        JSON.stringify(payload));
    }}
    sx={{height: 32, display: 'flex', alignItems: 'center', gap: 0.5, px: 0.5,
      cursor: disabled ? 'not-allowed' : 'grab', '&:hover': {bgcolor: 'action.hover'}}}>
    <DragIndicatorIcon aria-hidden="true" sx={{fontSize: 16}} />
    <Box sx={{width: 18, display: 'grid', placeItems: 'center'}}>{icon}</Box>
    <Box component="span">{label}</Box>
  </Box>;
}

ToolboxItem.propTypes = {
  label: PropTypes.node.isRequired,
  icon: PropTypes.node,
  payload: PropTypes.object.isRequired,
  onInsert: PropTypes.func,
  disabled: PropTypes.bool,
};
