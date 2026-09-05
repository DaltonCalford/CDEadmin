/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {
  createTreeActionDescriptor,
  normalizeTreeActions,
  TreeActionRegistry,
} from 'sources/cdeadmin_ui/navigation/TreeActions';

describe('CDEadmin provider tree actions', () => {
  it('normalizes executable legacy menu items without losing state', () => {
    const callback = jest.fn();
    const actions = normalizeTreeActions([{
      name: 'drop-table',
      label: 'Drop table',
      callback,
      isDisabled: true,
    }], {node: {providerId: 'org.cdeadmin.firebird', objectType: 'table'}});

    expect(actions[0]).toEqual(expect.objectContaining({
      id: 'drop-table',
      intent: 'destructive',
      enabled: false,
      requiresConfirmation: true,
      providerId: 'org.cdeadmin.firebird',
      objectTypes: ['table'],
    }));
    expect(actions[0].disabledReason).toBeTruthy();
  });

  it('requires real handlers rather than accepting placeholder actions', () => {
    expect(()=>createTreeActionDescriptor({id: 'empty', label: 'Empty'}))
      .toThrow('requires an executable handler');
  });

  it('selects provider actions by provider, engine, and object type', () => {
    const registry = new TreeActionRegistry();
    const execute = jest.fn();
    registry.register({
      id: 'firebird-table-actions',
      providerId: 'org.cdeadmin.firebird',
      engineId: 'firebird',
      objectTypes: ['table'],
      actionFactory: ({node})=>[{
        id: 'recompute-statistics',
        label: `Recompute statistics for ${node.label}`,
        execute,
      }],
    });

    const actions = registry.actionsFor({node: {
      providerId: 'org.cdeadmin.firebird',
      engineId: 'firebird',
      objectType: 'table',
      label: 'INVOICES',
    }});
    expect(actions).toHaveLength(1);
    expect(actions[0].label).toContain('INVOICES');
    expect(registry.actionsFor({node: {
      providerId: 'org.cdeadmin.mongodb',
      engineId: 'mongodb',
      objectType: 'collection',
    }})).toHaveLength(0);
  });

  it('deduplicates provider actions against resolved legacy commands', () => {
    const registry = new TreeActionRegistry();
    const node = {providerId: 'org.cdeadmin.redis', objectType: 'key'};
    registry.register({
      id: 'redis-key-actions',
      providerId: 'org.cdeadmin.redis',
      objectTypes: ['key'],
      actionFactory: ()=>[{id: 'refresh', label: 'Refresh', execute: jest.fn()}],
    });
    const resolved = registry.resolve({node}, [
      {name: 'refresh', label: 'Refresh', callback: jest.fn()},
    ]);
    expect(resolved.filter((item)=>item.id === 'refresh')).toHaveLength(1);
  });
});
