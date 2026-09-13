/////////////////////////////////////////////////////////////
// ScratchRobin interface-customization persistence contracts.
/////////////////////////////////////////////////////////////

import {
  createIconAssignmentDocument,
  normalizeIconAssignments,
} from '../icons';
import {
  createMenuCustomizationDocument,
  normalizeMenuPresentation,
  normalizeMenuCustomizations,
} from '../commands/MenuStructure';
import {
  PRESENTATION_COLOR_PREFERENCES,
  PRESENTATION_OVERRIDE_RANGES,
  PRESENTATION_PROFILES,
} from '../foundations/presentation';

export const CUSTOMIZATION_PREFERENCES = Object.freeze({
  icons: Object.freeze({module: 'browser', name: 'icon_assignments'}),
  menus: Object.freeze({module: 'browser', name: 'menu_customizations'}),
  commands: Object.freeze({module: 'browser', name: 'command_customizations'}),
});

const COMMAND_ID = /^[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*$/;
const ICON_KEY = /^[a-z0-9][a-z0-9._-]*$/;
const MENU_NAME = /^[a-z][a-z0-9_-]*$/;
const HEX_COLOR = /^#[0-9a-f]{6}$/i;
const FONT_FAMILY = /^[a-zA-Z0-9 ,.'"_-]{1,160}$/;
const APPEARANCE_ENUMS = Object.freeze({
  accessibility_density: new Set([
    'profile', 'compact', 'standard', 'comfortable', 'touch',
  ]),
  accessibility_motion: new Set(['profile', 'system', 'reduce', 'full']),
});

function objectValue(input) {
  if(input && !Array.isArray(input) && typeof input === 'object') return input;
  if(typeof input !== 'string') return {};
  try {
    const parsed = JSON.parse(input || '{}');
    return parsed && !Array.isArray(parsed) && typeof parsed === 'object' ?
      parsed : {};
  } catch {
    return {};
  }
}

export function normalizeCommandCustomizations(input={}) {
  const candidate = objectValue(input);
  const result = {};
  Object.entries(candidate).forEach(([id, value]) => {
    if(!COMMAND_ID.test(id) || !value || Array.isArray(value) ||
        typeof value !== 'object') return;
    const iconKey = String(value.iconKey ?? '').trim().toLowerCase();
    const surface = String(value.surface ?? '').trim().toLowerCase();
    const label = String(value.label ?? '').trim().slice(0, 120);
    const shortcut = String(value.shortcut ?? '').trim().slice(0, 40);
    const normalized = {};
    if(label) normalized.label = label;
    if(ICON_KEY.test(iconKey)) normalized.iconKey = iconKey;
    if(MENU_NAME.test(surface)) normalized.surface = surface;
    if(shortcut) normalized.shortcut = shortcut;
    if(value.visible === false) normalized.visible = false;
    if(value.enabled === false) normalized.enabled = false;
    if(value.presentation) {
      const presentation = normalizeMenuPresentation(value.presentation);
      if(Object.keys(presentation).length) normalized.presentation = presentation;
    }
    if(Number.isFinite(Number(value.priority))) {
      normalized.priority = Math.max(0, Math.min(10000, Number(value.priority)));
    }
    if(Object.keys(normalized).length) result[id] = Object.freeze(normalized);
  });
  return Object.freeze(result);
}

export function normalizeAppearanceCustomizations(input={}) {
  const candidate = objectValue(input);
  const result = {};
  if(PRESENTATION_PROFILES[candidate.accessibility_profile]) {
    result.accessibility_profile = candidate.accessibility_profile;
  }
  Object.entries(APPEARANCE_ENUMS).forEach(([name, values]) => {
    if(values.has(candidate[name])) result[name] = candidate[name];
  });
  ['accessibility_ui_font_family',
    'accessibility_monospace_font_family'].forEach((name) => {
    if(!(name in candidate)) return;
    const value = String(candidate[name] ?? '').trim();
    if(!value || FONT_FAMILY.test(value)) result[name] = value;
  });
  Object.entries(PRESENTATION_OVERRIDE_RANGES).forEach(([name, range]) => {
    if(candidate[name] === -1 || candidate[name] === '-1') {
      result[name] = -1;
      return;
    }
    const value = Number(candidate[name]);
    if(Number.isFinite(value)) {
      result[name] = Math.max(range[0], Math.min(range[1], value));
    }
  });
  Object.keys(PRESENTATION_COLOR_PREFERENCES).forEach((name) => {
    if(!(name in candidate)) return;
    const value = String(candidate[name] ?? '').trim().toUpperCase();
    if(!value || HEX_COLOR.test(value)) result[name] = value;
  });
  return Object.freeze(result);
}

export function readCustomizationDraft(preferenceStore) {
  const read = (key) => {
    const descriptor = CUSTOMIZATION_PREFERENCES[key];
    return preferenceStore.getPreferences(
      descriptor.module, descriptor.name
    )?.value;
  };
  return Object.freeze({
    icons: normalizeIconAssignments(read('icons')),
    menus: normalizeMenuCustomizations(read('menus')),
    commands: normalizeCommandCustomizations(read('commands')),
  });
}

function preferenceUpdate(preference, value) {
  if(!preference) throw new Error(
    'The interface-customization preference is not registered.'
  );
  return {
    category_id: preference.cid,
    id: preference.id,
    mid: preference.mid,
    name: preference.name,
    value: JSON.stringify(value),
  };
}

export async function saveCustomizationDraft(preferenceStore, draft) {
  const values = {
    icons: createIconAssignmentDocument(draft.icons),
    menus: createMenuCustomizationDocument(draft.menus),
    commands: normalizeCommandCustomizations(draft.commands),
  };
  const updates = Object.entries(CUSTOMIZATION_PREFERENCES).map(([key, descriptor]) =>
    preferenceUpdate(preferenceStore.getPreferences(
      descriptor.module, descriptor.name
    ), values[key])
  );
  await preferenceStore.setPreferences(updates);
  return readCustomizationDraft(preferenceStore);
}

export function exportCustomizationProfile(draft, appearance={}) {
  return Object.freeze({
    schema: 'cdeadmin.interface-profile.v1',
    exportedAt: new Date().toISOString(),
    presentation: normalizeAppearanceCustomizations(appearance),
    iconAssignments: createIconAssignmentDocument(draft.icons),
    menuCustomizations: createMenuCustomizationDocument(draft.menus),
    commandCustomizations: normalizeCommandCustomizations(draft.commands),
  });
}

export function importCustomizationProfile(input) {
  const candidate = objectValue(input);
  if(candidate.schema !== 'cdeadmin.interface-profile.v1') {
    throw new TypeError('The file is not a CDEadmin interface profile.');
  }
  return Object.freeze({
    appearance: normalizeAppearanceCustomizations(candidate.presentation),
    draft: Object.freeze({
      icons: normalizeIconAssignments(candidate.iconAssignments),
      menus: normalizeMenuCustomizations(candidate.menuCustomizations),
      commands: normalizeCommandCustomizations(candidate.commandCustomizations),
    }),
  });
}
