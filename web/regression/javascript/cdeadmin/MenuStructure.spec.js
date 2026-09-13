/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {
  CDEADMIN_MENU_STRUCTURE, MenuBindingRegistry, MenuStructureRegistry,
  createMenuCustomizationDocument, normalizeMenuCustomizations,
} from 'sources/cdeadmin_ui/commands/MenuStructure';

describe('CDEadmin menu structure and bindings', () => {
  it('implements the normative provider-neutral top-level taxonomy', () => {
    const registry = new MenuStructureRegistry();
    expect(registry.get().map((menu)=>menu.name)).toEqual([
      'file', 'edit', 'view', 'navigate', 'run', 'data', 'project', 'tools',
      'window', 'help',
    ]);
    expect(registry.get().map((menu)=>menu.name)).not.toContain('object');
    expect(registry.surfaces().has('connectors')).toBe(true);
  });

  it('can replace the complete visual structure independently of commands', () => {
    const registry = new MenuStructureRegistry(CDEADMIN_MENU_STRUCTURE);
    registry.replace([{name: 'workspace', label: 'Workspace'}]);
    expect(registry.get()).toEqual([
      expect.objectContaining({name: 'workspace', label: 'Workspace'}),
    ]);
  });

  it('validates, reorders, hides, styles, and adds top-level menus', () => {
    const registry = new MenuStructureRegistry(CDEADMIN_MENU_STRUCTURE);
    const document = createMenuCustomizationDocument({
      file: {label: 'Documents', visible: true, index: 50,
        iconKey: 'action.open', presentation: {fontSize: 18,
          color: '#112233', backgroundColor: 'javascript:bad'}},
      help: {visible: false},
      analytics: {label: 'Analytics', visible: true, index: 1},
      '<unsafe>': {label: 'Unsafe'},
    });
    const resolved = registry.resolve(document);
    expect(resolved[0]).toEqual(expect.objectContaining({
      name: 'analytics', label: 'Analytics', index: 1,
    }));
    expect(resolved.find((item)=>item.name === 'file')).toEqual(
      expect.objectContaining({label: 'Documents', iconKey: 'action.open',
        presentation: expect.objectContaining({fontSize: 18,
          color: '#112233'})})
    );
    expect(resolved.find((item)=>item.name === 'file').presentation)
      .not.toHaveProperty('backgroundColor');
    expect(resolved.some((item)=>item.name === 'help')).toBe(false);
    expect(normalizeMenuCustomizations(document)['<unsafe>']).toBeUndefined();
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
