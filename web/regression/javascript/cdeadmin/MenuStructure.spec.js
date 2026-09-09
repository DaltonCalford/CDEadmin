/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {
  MenuBindingRegistry, MenuStructureRegistry, PROVISIONAL_MENU_STRUCTURE,
} from 'sources/cdeadmin_ui/commands/MenuStructure';

describe('CDEadmin menu structure and bindings', () => {
  it('provides a provisional Connectors surface without fixing final layout', () => {
    const registry = new MenuStructureRegistry();
    expect(registry.get().map((menu)=>menu.name)).toContain('connectors');
    expect(registry.surfaces().has('connectors')).toBe(true);
  });

  it('can replace the complete visual structure independently of commands', () => {
    const registry = new MenuStructureRegistry(PROVISIONAL_MENU_STRUCTURE);
    registry.replace([{name: 'workspace', label: 'Workspace'}]);
    expect(registry.get()).toEqual([
      expect.objectContaining({name: 'workspace', label: 'Workspace'}),
    ]);
  });

  it('binds one command to multiple presentation surfaces', () => {
    const bindings = new MenuBindingRegistry();
    bindings.register({
      surface: 'connectors',
      commandId: 'connector.visibility.redis.set',
      name: 'show_redis',
    });
    bindings.register({
      surface: 'context',
      commandId: 'connector.visibility.redis.set',
      name: 'show_redis_here',
      node: 'engine_type',
    });
    expect(bindings.list()).toHaveLength(2);
    expect(bindings.list('connectors')[0].commandId)
      .toBe('connector.visibility.redis.set');
  });
});
