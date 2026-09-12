/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

const MENU_NAME = /^[a-z][a-z0-9_-]*$/;

export const CDEADMIN_MENU_STRUCTURE = Object.freeze([
  Object.freeze({label: 'File', name: 'file', id: 'mnu_file', index: 0,
    addSeprator: true, hasDynamicMenuItems: false}),
  Object.freeze({label: 'Edit', name: 'edit', id: 'mnu_edit',
    index: 1, addSeprator: true, hasDynamicMenuItems: false}),
  Object.freeze({label: 'View', name: 'view', id: 'mnu_view', index: 2,
    addSeprator: true, hasDynamicMenuItems: false}),
  Object.freeze({label: 'Navigate', name: 'navigate', id: 'mnu_navigate',
    index: 3, addSeprator: true, hasDynamicMenuItems: false}),
  Object.freeze({label: 'Run', name: 'run', id: 'mnu_run', index: 4,
    addSeprator: true, hasDynamicMenuItems: false}),
  Object.freeze({label: 'Data', name: 'data', id: 'mnu_data', index: 5,
    addSeprator: true, hasDynamicMenuItems: false}),
  Object.freeze({label: 'Project', name: 'project', id: 'mnu_project', index: 6,
    addSeprator: true, hasDynamicMenuItems: false}),
  Object.freeze({label: 'Tools', name: 'tools', id: 'mnu_tools', index: 7,
    addSeprator: true, hasDynamicMenuItems: false}),
  Object.freeze({label: 'Window', name: 'window', id: 'mnu_window', index: 8,
    addSeprator: true, hasDynamicMenuItems: false}),
  Object.freeze({label: 'Help', name: 'help', id: 'mnu_help', index: 9,
    addSeprator: false, hasDynamicMenuItems: false}),
]);

// Compatibility alias retained until all callers use the final CDEadmin name.
export const PROVISIONAL_MENU_STRUCTURE = CDEADMIN_MENU_STRUCTURE;

function normalizeMenu(input, index) {
  const name = String(input?.name ?? '').trim();
  if(!MENU_NAME.test(name)) throw new TypeError(`Invalid menu name: ${name}`);
  return Object.freeze({
    label: String(input.label ?? name),
    name,
    id: String(input.id ?? `mnu_${name}`),
    index: Number.isFinite(input.index) ? input.index : index,
    addSeprator: Boolean(input.addSeprator),
    hasDynamicMenuItems: Boolean(input.hasDynamicMenuItems),
  });
}

export class MenuStructureRegistry {
  constructor(structure=CDEADMIN_MENU_STRUCTURE) {
    this.replace(structure);
  }

  replace(structure) {
    if(!Array.isArray(structure) || structure.length === 0) {
      throw new TypeError('Menu structure must contain at least one menu.');
    }
    const names = new Set();
    this.structure = Object.freeze(structure.map((menu, index)=>{
      const normalized = normalizeMenu(menu, index);
      if(names.has(normalized.name)) {
        throw new Error(`Duplicate menu name: ${normalized.name}`);
      }
      names.add(normalized.name);
      return normalized;
    }));
    return this.structure;
  }

  get() {
    return this.structure;
  }

  surfaces() {
    // Legacy contribution names remain accepted while their presentation is
    // mapped into the normative CDEadmin menu taxonomy.
    return new Set([
      'context', 'connectors', 'management', 'object',
      ...this.structure.map((menu)=>menu.name),
    ]);
  }
}

export class MenuBindingRegistry {
  constructor() {
    this.bindings = new Map();
  }

  register(input) {
    const surface = String(input?.surface ?? '').trim();
    const commandId = String(input?.commandId ?? '').trim();
    const name = String(input?.name ?? commandId).trim();
    if(!surface || !commandId || !name) {
      throw new TypeError('Menu bindings require surface, commandId, and name.');
    }
    const key = `${surface}:${name}:${String(input.node ?? '')}`;
    const binding = Object.freeze({
      key, surface, commandId, name,
      category: String(input.category ?? 'common'),
      node: input.node ?? null,
      priority: Number.isFinite(input.priority) ? input.priority : 10,
      commandArguments: input.commandArguments ?? {},
    });
    this.bindings.set(key, binding);
    return binding;
  }

  list(surface=null) {
    return [...this.bindings.values()].filter(
      (binding)=>surface === null || binding.surface === surface
    );
  }
}

export const menuStructureRegistry = new MenuStructureRegistry();
export const menuBindingRegistry = new MenuBindingRegistry();
