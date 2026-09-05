/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import cn from 'classnames';
import {
  resolveIconDefinition,
  semanticEngineIconKey,
  semanticObjectIconKey,
} from './registry';

export function Icon({
  iconKey,
  label,
  decorative=false,
  className,
  size,
  ...props
}) {
  const definition = resolveIconDefinition(iconKey, {label});
  const accessible = decorative ? {
    'aria-hidden': true,
  } : {
    role: 'img',
    'aria-label': label || definition.label,
  };
  const style = size ? {width: size, height: size, fontSize: size} : undefined;

  if(definition.kind === 'svg') {
    return <img
      {...props}
      {...accessible}
      className={className}
      src={definition.svgUrl}
      alt={decorative ? '' : (label || definition.label)}
      data-icon-key={definition.key}
      style={style}
    />;
  }
  return <i
    {...props}
    {...accessible}
    className={cn(className, definition.className)}
    data-icon-key={definition.key}
    style={style}
  />;
}

Icon.propTypes = {
  iconKey: PropTypes.string.isRequired,
  label: PropTypes.string,
  decorative: PropTypes.bool,
  className: PropTypes.string,
  size: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};

export function EngineIcon({engineId, ...props}) {
  return <Icon iconKey={semanticEngineIconKey(engineId)} {...props} />;
}

EngineIcon.propTypes = {
  engineId: PropTypes.string.isRequired,
};

export function ObjectIcon({objectType, iconKey, ...props}) {
  return <Icon
    iconKey={iconKey || semanticObjectIconKey(objectType)}
    {...props}
  />;
}

ObjectIcon.propTypes = {
  objectType: PropTypes.string,
  iconKey: PropTypes.string,
};
