/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {fireEvent, render, screen} from '@testing-library/react';
import ContextMenu from 'sources/cdeadmin_ui/navigation/ContextMenu';
import {withTheme} from '../fake_theme';

describe('CDEadmin semantic context menu', () => {
  it('renders and executes semantic provider actions', () => {
    const execute = jest.fn();
    const Component = withTheme(ContextMenu);
    render(<Component
      label="Table actions"
      position={{x: 10, y: 10}}
      onClose={jest.fn()}
      menuItems={[{
        schemaVersion: 1,
        id: 'refresh-table',
        label: 'Refresh table',
        intent: 'default',
        iconKey: 'action.refresh',
        enabled: true,
        requiresConfirmation: false,
        execute,
        children: [],
      }]}
    />);

    const item = screen.getByRole('menuitem', {name: 'Refresh table'});
    expect(item).toHaveAttribute('data-action-id', 'refresh-table');
    expect(document.querySelector('[data-icon-key="action.refresh"]'))
      .toBeInTheDocument();
    fireEvent.click(item);
    expect(execute).toHaveBeenCalledTimes(1);
  });

  it('retains the reason an action is disabled', () => {
    const Component = withTheme(ContextMenu);
    render(<Component
      label="Database actions"
      position={{x: 10, y: 10}}
      onClose={jest.fn()}
      menuItems={[{
        id: 'drop-database',
        label: 'Drop database',
        callback: jest.fn(),
        isDisabled: true,
        disabledReason: 'Disconnect active sessions first.',
      }]}
    />);

    expect(screen.getByRole('menuitem', {name: 'Drop database'}))
      .toHaveAttribute('title', 'Disconnect active sessions first.');
  });
});
