/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////


import { withTheme } from '../fake_theme';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { PgMenu, PgMenuItem, PgSubMenu } from '../../../pgadmin/static/js/components/Menu';

describe('Menu', ()=>{
  const ThemedPgMenu = withTheme(PgMenu);
  const eleRef = {
    current: document.createElement('button'),
  };

  describe('PgMenu', ()=>{
    const onClose = ()=>{/* on close call */};
    let ctrl;
    const ctrlMount = ()=>{
      ctrl = render(
        <ThemedPgMenu
          anchorRef={eleRef}
          onClose={onClose}
          open={false}
        />);
    };
    it('init', ()=>{
      ctrlMount();
      const menu = screen.getByRole('menu',{hidden: true});
      expect(menu.getAttribute('data-state')).toBe('closed');
    });

    it('open', ()=>{
      ctrlMount();
      ctrl.rerender(<ThemedPgMenu
        anchorRef={eleRef}
        onClose={onClose}
        open={true}
      />);
      const menu = screen.getByRole('menu');
      expect(menu.getAttribute('data-state')).toBe('open');
    });
  });

  describe('viewport-bounded menu lists', () => {
    let geometry;
    beforeEach(() => {
      geometry = jest.spyOn(HTMLElement.prototype, 'getBoundingClientRect')
        .mockImplementation(function() {
          const height = this.getAttribute('role') === 'menu' ? 2000 : 20;
          return {left: 0, top: 0, right: 220, bottom: height,
            width: 220, height, x: 0, y: 0};
        });
    });
    afterEach(() => geometry.mockRestore());

    it.each([false, true])('makes oversized menus scrollable (button=%s)', async (buttonMenu) => {
      render(<ThemedPgMenu open={!buttonMenu}
        anchorPoint={{x: 10, y: 10}}
        menuButton={buttonMenu ? <button>Open bounded menu</button> : null}>
        <PgMenuItem>Last command</PgMenuItem>
      </ThemedPgMenu>);
      if (buttonMenu) fireEvent.click(screen.getByText('Open bounded menu'));
      const menu = await screen.findByRole('menu');
      await waitFor(() => expect(menu.style.overflow).toBe('auto'));
      expect(Number.parseFloat(menu.style.maxHeight)).toBeGreaterThan(0);
      expect(Number.parseFloat(menu.style.maxHeight)).toBeLessThanOrEqual(window.innerHeight);
    });

    it('bounds nested menus independently and retains keyboard activation', async () => {
      const execute = jest.fn();
      render(<ThemedPgMenu open anchorPoint={{x: 10, y: 10}}>
        <PgSubMenu label="Maintenance">
          <PgMenuItem onClick={execute}>First command</PgMenuItem>
          <PgMenuItem disabled>Unavailable command</PgMenuItem>
          <PgMenuItem onClick={execute}>Last command</PgMenuItem>
        </PgSubMenu>
      </ThemedPgMenu>);
      const parent = screen.getByRole('menuitem', {name: /Maintenance/});
      fireEvent.keyDown(screen.getByRole('menu'), {key: 'Home'});
      await waitFor(() => expect(parent).toHaveFocus());
      fireEvent.keyDown(parent, {key: 'ArrowRight'});
      const last = await screen.findByRole('menuitem', {name: 'Last command'});
      const childMenu = last.closest('[role="menu"]');
      await waitFor(() => expect(childMenu.style.overflow).toBe('auto'));
      expect(Number.parseFloat(childMenu.style.maxHeight)).toBeLessThanOrEqual(window.innerHeight);
      // The library portals submenus out of a scrollable parent list.
      expect(parent.contains(childMenu)).toBe(false);
      fireEvent.keyDown(childMenu, {key: 'End'});
      await waitFor(() => expect(last).toHaveFocus());
      fireEvent.keyDown(last, {key: 'Enter'});
      expect(execute).toHaveBeenCalledTimes(1);
    });
  });

  describe('PgMenuItem', ()=>{
    let ctrlMenu;
    const ctrlMount = (props)=>{
      ctrlMenu = render(
        <ThemedPgMenu
          anchorRef={eleRef}
          open={false}
        >
          <PgMenuItem {...props}>Test</PgMenuItem>
        </ThemedPgMenu>
      );
      ctrlMenu.rerender(
        <ThemedPgMenu
          anchorRef={eleRef}
          open={true}
        >
          <PgMenuItem {...props}>Test</PgMenuItem>
        </ThemedPgMenu>
      );
    };

    it('init', ()=>{
      ctrlMount({
        shortcut: {
          'control': true,
          'shift': true,
          'alt': false,
          'key': {
            'key_code': 75,
            'char': 'k',
          },
        }
      });
      const menuItem = screen.getByRole('menuitem');
      expect(menuItem.textContent).toBe('Test Ctrl + Shift + K');
    });

    it('not checked', ()=>{
      ctrlMount({
        hasCheck: true,
      });
      const menuItem = screen.getByRole('menuitem');
      expect(menuItem.querySelector('[data-label="CheckIcon"]').style.visibility).toBe('hidden');
    });

    it('checked', ()=>{
      ctrlMount({
        hasCheck: true,
        checked: true,
      });
      const menuItem = screen.getByRole('menuitem');
      expect(menuItem.querySelector('[data-label="CheckIcon"]').style.visibility).toBe('');
    });


    it('checked clicked', async ()=>{
      const onClick = jest.fn();
      ctrlMount({
        hasCheck: true,
        checked: false,
        onClick: onClick,
      });
      onClick.mockClear();
      const menuItem = screen.getByRole('menuitem');
      fireEvent.click(menuItem);
      expect(onClick).toHaveBeenCalled();
    });
  });
});
