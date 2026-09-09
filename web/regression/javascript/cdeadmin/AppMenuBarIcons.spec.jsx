/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {render, screen} from '@testing-library/react';
import {MenuCommandLabel} from 'sources/AppMenuBar';

describe('CDEadmin application menu command icons', () => {
  it('renders an explicit semantic command icon beside its label', () => {
    render(<MenuCommandLabel menuItem={{
      label: 'Backup database',
      iconKey: 'action.backup',
    }} />);

    expect(screen.getByText('Backup database')).toBeInTheDocument();
    expect(document.querySelector('[data-icon-key="action.backup"]'))
      .toBeInTheDocument();
  });

  it('infers an icon and retains a visible fallback for unknown commands', () => {
    const {rerender} = render(<MenuCommandLabel menuItem={{
      label: 'Disconnect server',
    }} />);
    expect(document.querySelector('[data-icon-key="action.disconnect"]'))
      .toBeInTheDocument();

    rerender(<MenuCommandLabel menuItem={{label: 'Special provider task'}} />);
    expect(document.querySelector('[data-icon-key="command.default"]'))
      .toBeInTheDocument();
  });
});
