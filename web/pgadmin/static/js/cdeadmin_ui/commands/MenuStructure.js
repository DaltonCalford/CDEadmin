/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

const MENU_NAME = /^[a-z][a-z0-9_-]*$/;
const ICON_KEY = /^[a-z0-9][a-z0-9._-]*$/;
const HEX_COLOR = /^#[0-9a-f]{6}$/i;
const FONT_FAMILY = /^[a-zA-Z0-9 ,.'"_-]{1,160}$/;

export const MENU_CUSTOMIZATION_SCHEMA = 'cdeadmin.menu-customization.v1';

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
    iconKey: ICON_KEY.test(String(input.iconKey ?? '')) ?
      String(input.iconKey) : '',
    presentation: Object.freeze({...input.presentation}),
  });
}

function bounded(value, minimum, maximum, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ?
    Math.max(minimum, Math.min(maximum, number)) : fallback;
}

export function normalizeMenuPresentation(input={}) {
  const fontFamily = String(input.fontFamily ?? '').trim();
  const color = String(input.color ?? '').trim().toUpperCase();
  const backgroundColor = String(input.backgroundColor ?? '').trim().toUpperCase();
  const result = {};
  if(FONT_FAMILY.test(fontFamily)) result.fontFamily = fontFamily;
  if(Number.isFinite(Number(input.fontSize)) && Number(input.fontSize) !== -1) {
    result.fontSize = bounded(input.fontSize, 8, 32, 14);
  }
  if(Number.isFinite(Number(input.fontWeight)) &&
      Number(input.fontWeight) !== -1) {
    result.fontWeight = bounded(input.fontWeight, 300, 900, 400);
  }
  if(HEX_COLOR.test(color)) result.color = color;
  if(HEX_COLOR.test(backgroundColor)) {
    result.backgroundColor = backgroundColor;
  }
  if(['after', 'hidden'].includes(input.iconPosition)) {
    result.iconPosition = input.iconPosition;
  }
  return Object.freeze(result);
}

export function normalizeMenuCustomizations(input={}) {
  let candidate = input;
  if(typeof candidate === 'string') {
    try {
      candidate = JSON.parse(candidate || '{}');
    } catch {
      return Object.freeze({});
    }
  }
  if(candidate?.schema === MENU_CUSTOMIZATION_SCHEMA) candidate = candidate.menus;
  if(!candidate || Array.isArray(candidate) || typeof candidate !== 'object') {
    return Object.freeze({});
  }
  const result = {};
  Object.entries(candidate).forEach(([key, value]) => {
    const name = String(key).trim().toLowerCase();
    if(!MENU_NAME.test(name) || !value || Array.isArray(value) ||
        typeof value !== 'object') return;
    const label = String(value.label ?? name).trim().slice(0, 80) || name;
    const iconKey = String(value.iconKey ?? '').trim().toLowerCase();
    result[name] = Object.freeze({
      name,
      label,
      visible: value.visible !== false,
      index: bounded(value.index, 0, 1000, 100),
      iconKey: ICON_KEY.test(iconKey) ? iconKey : '',
      presentation: normalizeMenuPresentation(value.presentation),
    });
  });
  return Object.freeze(result);
}

export function createMenuCustomizationDocument(menus={}) {
  return Object.freeze({
    schema: MENU_CUSTOMIZATION_SCHEMA,
    menus: normalizeMenuCustomizations(menus),
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

  resolve(customizations={}) {
    const overrides = normalizeMenuCustomizations(customizations);
    const defaults = new Map(this.structure.map((item)=>[item.name, item]));
    const result = this.structure.map((item) => {
      const override = overrides[item.name];
      return override ? normalizeMenu({
        ...item,
        label: override.label,
        index: override.index,
        iconKey: override.iconKey,
        presentation: override.presentation,
      }, item.index) : item;
    }).filter((item)=>overrides[item.name]?.visible !== false);
    Object.values(overrides).forEach((override) => {
      if(defaults.has(override.name) || !override.visible) return;
      result.push(normalizeMenu({
        ...override,
        id: `mnu_${override.name}`,
        addSeprator: true,
        hasDynamicMenuItems: false,
      }, result.length));
    });
    return Object.freeze(result.sort((left, right) =>
      left.index - right.index || left.label.localeCompare(right.label)
    ));
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
