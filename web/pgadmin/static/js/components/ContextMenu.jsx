/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import { PgMenu, PgMenuDivider, PgMenuItem, PgSubMenu } from './Menu';
import PropTypes from 'prop-types';
import gettext from 'sources/gettext';
import {Icon} from 'sources/cdeadmin_ui/icons';
import {normalizeTreeActions} from 'sources/cdeadmin_ui/navigation/TreeActions';

function ActionLabel({action}) {
  return <span style={{display: 'inline-flex', alignItems: 'center', gap: '0.5em'}}>
    {action.iconKey && <Icon iconKey={action.iconKey} decorative />}
    <span>{action.label}</span>
  </span>;
}

ActionLabel.propTypes = {
  action: PropTypes.object.isRequired,
};

export default function ContextMenu({menuItems, position, onClose, label='context'}) {
  const actions = normalizeTreeActions(menuItems);
  const renderMenuItem = (menuItem, key)=>{
    if(menuItem.type == 'separator') {
      return <PgMenuDivider key={key}/>;
    }
    if(menuItem.children?.length) {
      return <PgSubMenu
        key={key}
        label={<ActionLabel action={menuItem} />}
      >
        {menuItem.children.map((child, index)=>renderMenuItem(
          child, `${key}-${child.id ?? index}`
        ))}
      </PgSubMenu>;
    }
    const hasCheck = typeof menuItem.checked == 'boolean';

    return <PgMenuItem
      key={key}
      disabled={!menuItem.enabled}
      aria-disabled={!menuItem.enabled}
      title={menuItem.disabledReason || undefined}
      onClick={()=>{
        menuItem.execute?.();
      }}
      hasCheck={hasCheck}
      checked={menuItem.checked}
      shortcut={menuItem.shortcut}
      datalabel={menuItem.label}
      data-action-id={menuItem.id}
      data-action-intent={menuItem.intent}
      data-requires-confirmation={menuItem.requiresConfirmation || undefined}
    ><ActionLabel action={menuItem} /></PgMenuItem>;
  };

  return (
    <PgMenu
      anchorPoint={{
        x: position?.x,
        y: position?.y
      }}
      open={Boolean(position) && actions.length !=0}
      onClose={onClose}
      label={label}
      portal
    >
      {actions.length !=0 && actions.map((menuItem, i)=>renderMenuItem(
        menuItem, menuItem.id ?? i
      ))}
      {actions.length == 0 && <PgMenuItem disabled>
        {gettext('No options')}
      </PgMenuItem>}
    </PgMenu>
  );
}

ContextMenu.propTypes = {
  menuItems: PropTypes.array,
  position: PropTypes.object,
  onClose: PropTypes.func,
  label: PropTypes.string,
};
