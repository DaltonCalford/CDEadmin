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

  it('applies safe per-entry presentation and icon placement', () => {
    render(<MenuCommandLabel menuItem={{
      label: 'Accessible query', iconKey: 'action.search',
      presentation: {fontFamily: 'Atkinson Hyperlegible', fontSize: 20,
        fontWeight: 700, color: '#123456', backgroundColor: '#FFFFFF',
        iconPosition: 'after'},
    }} />);

    const label = screen.getByText('Accessible query');
    expect(label.parentElement).toHaveStyle({fontFamily: 'Atkinson Hyperlegible',
      fontSize: '20px', fontWeight: '700', color: '#123456',
      backgroundColor: '#FFFFFF'});
    expect(label.nextElementSibling).toHaveAttribute(
      'data-icon-key', 'action.search'
    );
  });
});
