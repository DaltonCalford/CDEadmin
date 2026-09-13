/////////////////////////////////////////////////////////////
// CDEadmin main dock tab identity and presentation tests.
/////////////////////////////////////////////////////////////

import {render, screen} from '@testing-library/react';
import Theme from 'sources/Theme';
import {
  dockTabIconKey, ToolTabTitle, WorkspaceController,
} from 'sources/cdeadmin_ui/workspace/Workspace';
import rcdockOverride from 'sources/Theme/overrides/rcdock.override';

describe('CDEadmin main dock tab switcher', () => {
  it('maps product, help, and tool tabs to semantic non-PostgreSQL icons', () => {
    expect(dockTabIconKey({id: 'id-dashboard', title: 'ScratchRobin'}))
      .toBe('tool.scratchrobin');
    expect(dockTabIconKey({id: 'help', title: 'Help'})).toBe('action.help');
    expect(dockTabIconKey({id: 'sql', title: 'SQL'})).toBe('tool.query');
    expect(dockTabIconKey({icon: 'pg-font-icon icon-terminal'}))
      .toBe('action.terminal');
    expect(dockTabIconKey({icon: 'pg-font-icon icon-server'}))
      .toBe('command.default');
  });

  it('stores a semantic icon on every dock panel', () => {
    const home = WorkspaceController.getPanel({
      id: 'id-dashboard', title: 'ScratchRobin', group: 'playground',
    });
    const unknown = WorkspaceController.getPanel({
      id: 'tool-unknown', title: 'Provider utility', group: 'playground',
    });
    expect(home.internal.iconKey).toBe('tool.scratchrobin');
    expect(unknown.internal.iconKey).toBe('command.default');
  });

  it('renders the tab icon together with its visible name', () => {
    render(<Theme><ToolTabTitle id="id-dashboard" defaultInternal={{
      title: 'ScratchRobin', tooltip: 'ScratchRobin Dashboard',
      iconKey: 'tool.scratchrobin', closable: false,
    }} /></Theme>);
    expect(screen.getByText('ScratchRobin')).toBeInTheDocument();
    expect(document.querySelector('[data-icon-key="tool.scratchrobin"]'))
      .toBeInTheDocument();
    expect(document.querySelector('[data-cdeadmin-tab-id="id-dashboard"]'))
      .toBeInTheDocument();
  });

  it('defines the large 115/85 primary-tab visual treatment', () => {
    const theme = {
      custom: {icon: {contrastText: '#111'}},
      mixins: {panelBorder: {top: {}, bottom: {}}},
      otherVars: {activeBorder: '#00f', activeColor: '#00f',
        borderColor: '#999'},
      palette: {background: {default: '#fff'}, text: {primary: '#111'},
        primary: {main: '#00f', contrastText: '#fff'}},
      shape: {borderRadius: 0},
    };
    const panel = rcdockOverride(theme)['.dock-layout']['& .dock-panel'];
    expect(panel['&.dock-style-playground'][
      '& > .dock > .dock-bar:has(.dock-tab):not(:has(.dock-tab ~ .dock-tab))'
    ]).toEqual({display: 'none'});
    const main = panel['&.dock-style-playground']['&[data-dockid="id-main"]'];
    const tab = main['& > .dock > .dock-bar']['& .dock-tab'];
    expect(tab.filter).toBe(
      'brightness(var(--cde-inactive-brightness, 0.85))'
    );
    expect(tab['&.dock-tab-active']).toMatchObject({
      filter: 'brightness(1)',
      transform: 'scale(var(--cde-active-tab-scale, 1.15))',
    });
  });
});
