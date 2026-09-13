/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {
  isProviderContextNode, providerContextMenuItems,
  providerTreeContextActions, registerProviderMenuCategories,
} from 'pgbrowser/ProviderContextMenu';

const action = (overrides={}) => ({
  schema: 'cdeadmin.context-action.v1',
  command_id: 'endpoint.firebird.verify',
  command_version: 1,
  label: 'Verify Firebird endpoint...',
  handler: 'verify_endpoint',
  icon_key: 'action.connect',
  menu_group: 'connection',
  priority: 10,
  mutation_class: 'read',
  arguments: {profile_id: 'firebird-native'},
  enabled: true,
  macro_callable: true,
  ...overrides,
});

describe('provider-owned Object Explorer menus', () => {
  it('puts common object actions directly on the popup, not in a submenu', () => {
    const menus = providerTreeContextActions({cde_context_actions: [action({
      command_id: 'resource.sqlite.table.create', label: 'New Table',
      menu_group: 'common', handler: 'open_workspace',
    })]});
    expect(menus).toHaveLength(1);
    expect(menus[0].label).toBe('New Table');
    expect(menus[0].children).toBeUndefined();
    expect(typeof menus[0].execute).toBe('function');
  });
  it('recognizes an explicit empty provider menu as authoritative', () => {
    expect(isProviderContextNode({cde_context_actions: []})).toBe(true);
    expect(providerContextMenuItems({cde_context_actions: []})).toEqual([]);
    expect(isProviderContextNode({_type: 'server'})).toBe(false);
  });

  it('maps admitted descriptors into command-backed menu options', () => {
    const node = {cde_context_actions: [action()]};
    const menus = providerContextMenuItems(node);
    expect(menus).toHaveLength(1);
    expect(menus[0]).toEqual(expect.objectContaining({
      commandId: 'endpoint.firebird.verify',
      label: 'Verify Firebird endpoint...',
      iconKey: 'action.connect',
      category: 'connection',
      intent: 'read',
      macroCallable: true,
    }));
    expect(menus[0].enable(node)).toBe(true);
    expect(menus[0].commandArguments({itemData: node})).toEqual({
      profile_id: 'firebird-native',
    });
  });

  it('binds arguments to the context-clicked node', () => {
    const first = action({
      command_id: 'resource.mysql.table.alter',
      handler: 'open_workspace',
      arguments: {resource_id: 'table:first'},
    });
    const current = {cde_context_actions: [{...first,
      enabled: false,
      arguments: {resource_id: 'table:current'},
    }]};
    const menu = providerContextMenuItems({
      cde_context_actions: [first],
    })[0];
    expect(menu.enable(current)).toBe(true);
    expect(menu.commandArguments({itemData: current})).toEqual({
      resource_id: 'table:first',
    });
  });

  it('drops forged schemas and browser handlers', () => {
    const node = {cde_context_actions: [
      action({schema: 'attacker.action.v1'}),
      action({handler: 'eval'}),
    ]};
    expect(providerContextMenuItems(node)).toEqual([]);
  });

  it('registers stable user-facing provider menu group labels', () => {
    const browser = {menu_categories: {}};
    registerProviderMenuCategories(browser, [{category: 'diagnostics'}, {
      category: 'database-lifecycle',
    }]);
    expect(browser.menu_categories.diagnostics.label).toBe(
      'Diagnostics and verification'
    );
    expect(browser.menu_categories['database-lifecycle'].label).toBe(
      'Definition and lifecycle'
    );
  });

  it('adapts authoritative provider actions for the new tree menu', () => {
    const node = {cde_context_actions: [
      action({
        command_id: 'database.firebird.repair_database',
        label: 'Repair database (gfix)...',
        handler: 'open_workspace',
        menu_group: 'maintenance',
        mutation_class: 'destructive',
        requires_confirmation: true,
      }),
      action({
        command_id: 'database.firebird.backup_logical',
        label: 'Logical backup (gbak)...',
        handler: 'open_workspace',
        menu_group: 'backup',
      }),
    ]};
    const groups = providerTreeContextActions(node, {id: 'database'});
    expect(groups.map((group)=>group.label)).toEqual([
      'Backup', 'Maintenance',
    ]);
    expect(groups[1].children[0]).toEqual(expect.objectContaining({
      id: 'database.firebird.repair_database',
      label: 'Repair database (gfix)...',
      intent: 'destructive',
      requiresConfirmation: true,
    }));
    expect(typeof groups[1].children[0].execute).toBe('function');
  });
});
