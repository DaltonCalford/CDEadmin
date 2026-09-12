/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {
  commandService,
  commandRegistry,
  executeMenuCommand,
  legacyCommandId,
  menuBindingRegistry,
  registerMenuCommand,
  resolveMenuCommand,
} from 'pgbrowser/CommandMenuAdapter';

describe('CDEadmin legacy command/menu adapter', () => {
  it('creates deterministic compatibility IDs', () => {
    expect(legacyCommandId({
      name: 'Drop Table',
      module: {type: 'TABLE'},
    })).toBe('legacy.table.drop_table');
  });

  it('selects semantic SVG keys while preserving explicit icon choices', () => {
    const inferred = {
      name: 'disconnect_server',
      label: 'Disconnect server',
      callback: jest.fn(),
    };
    registerMenuCommand(inferred);
    expect(commandRegistry.get(legacyCommandId(inferred)).iconKey)
      .toBe('action.disconnect');

    const explicit = {
      name: 'custom_refresh',
      label: 'Refresh',
      iconKey: 'tool.dashboard',
      callback: jest.fn(),
    };
    registerMenuCommand(explicit);
    expect(commandRegistry.get(legacyCommandId(explicit)).iconKey)
      .toBe('tool.dashboard');
  });

  it('registers and invokes an existing menu callback through command policy', async () => {
    const callback = jest.fn();
    const menu = {
      name: 'adapter_test_action',
      commandId: 'test.adapter.invoke',
      label: 'Adapter test',
      callback,
      enable: true,
      applies: ['tools'],
    };

    expect(registerMenuCommand(menu, 'tools')).toBe('test.adapter.invoke');
    expect(commandRegistry.has('test.adapter.invoke')).toBe(true);
    expect(menuBindingRegistry.list('tools')).toEqual([
      expect.objectContaining({commandId: 'test.adapter.invoke'}),
    ]);
    expect(resolveMenuCommand(menu)).toEqual(expect.objectContaining({
      visible: true,
      enabled: true,
    }));
    await executeMenuCommand(menu);
    expect(callback).toHaveBeenCalledWith(menu, {}, expect.any(Object));
  });

  it('does not accept a forged current user through the browser service', async () => {
    const handler = jest.fn();
    commandService.register({
      id: 'test.adapter.protected',
      permission: 'protected_operation',
      execute: handler,
    });
    await expect(commandService.execute(
      'test.adapter.protected', {}, {
        currentUser: {permissions: ['protected_operation']},
      }
    )).rejects.toMatchObject({code: 'permission_denied'});
    expect(handler).not.toHaveBeenCalled();
  });

  it('materializes and executes an already-registered first-party command', async () => {
    const handler = jest.fn().mockReturnValue('opened');
    commandRegistry.register({
      id: 'test.first-party.open', label: 'Open project', execute: handler,
      surfaces: ['project'],
    });
    const option = {name: 'test_first_party_open',
      commandId: 'test.first-party.open', label: 'Open project'};
    expect(resolveMenuCommand(option)).toMatchObject({
      id: 'test.first-party.open', enabled: true,
    });
    await expect(executeMenuCommand(option)).resolves.toBe('opened');
    expect(handler).toHaveBeenCalledTimes(1);
  });
});
