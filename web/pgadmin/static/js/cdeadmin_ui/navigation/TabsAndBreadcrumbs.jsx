/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import {Breadcrumbs as MuiBreadcrumbs, Box, Link as MuiLink} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import {IconButton} from '../primitives/Button';
import {StatusDot} from '../feedback/Indicators';

export function Tab({label, active=false, dirty=false, attention=false,
  disabled=false, closable=false, icon, onActivate, onClose, onDragStart}) {
  return <Box role="tab" aria-selected={active} aria-disabled={disabled || undefined}
    tabIndex={active && !disabled ? 0 : -1} draggable={!disabled && Boolean(onDragStart)}
    onDragStart={onDragStart}
    onClick={() => !disabled && onActivate?.()}
    onMouseDown={(event) => {
      if(event.button === 1 && closable && !disabled) {
        event.preventDefault(); onClose?.();
      }
    }}
    onKeyDown={(event) => {
      if(!disabled && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault(); onActivate?.();
      }
      if(!disabled && closable && event.key === 'Delete' && event.shiftKey) onClose?.();
    }}
    sx={{height: 'var(--cde-tab-height)', minWidth: 96, maxWidth: 240,
      display: 'flex', alignItems: 'center', gap: 0.5, px: 1,
      borderBottom: active ? '2px solid' : '2px solid transparent',
      borderColor: active ? 'primary.main' : 'transparent',
      bgcolor: active ? 'background.paper' : 'transparent',
      color: disabled ? 'text.disabled' : 'text.primary', cursor: 'default'}}>
    {attention && <StatusDot status="warning" label="Attention required" />}
    {icon}
    <Box component="span" title={String(label)} sx={{flex: 1, minWidth: 0,
      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'}}>
      {label}{dirty ? ' •' : ''}
    </Box>
    {closable && <IconButton label={`Close ${label}`} disabled={disabled}
      onClick={(event) => { event.stopPropagation(); onClose?.(); }}>
      <CloseIcon />
    </IconButton>}
  </Box>;
}

Tab.propTypes = {
  label: PropTypes.node.isRequired,
  active: PropTypes.bool,
  dirty: PropTypes.bool,
  attention: PropTypes.bool,
  disabled: PropTypes.bool,
  closable: PropTypes.bool,
  icon: PropTypes.node,
  onActivate: PropTypes.func,
  onClose: PropTypes.func,
  onDragStart: PropTypes.func,
};
export function Breadcrumbs({items=[], onNavigate}) {
  return <MuiBreadcrumbs maxItems={6} separator="/" aria-label="Current location"
    sx={{height: 28, alignItems: 'center'}}>
    {items.map((item, index) => index === items.length - 1 ?
      <Box component="span" aria-current="page" key={item.id ?? item.label}>
        {item.label}
      </Box> : <MuiLink component="button" type="button" underline="hover"
        key={item.id ?? item.label} onClick={() => onNavigate?.(item)}>
        {item.label}
      </MuiLink>)}
  </MuiBreadcrumbs>;
}

Breadcrumbs.propTypes = {
  items: PropTypes.array,
  onNavigate: PropTypes.func,
};
