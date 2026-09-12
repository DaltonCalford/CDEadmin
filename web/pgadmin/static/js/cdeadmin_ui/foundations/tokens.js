/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import tokenDocument from './specification/ui-tokens.json';
import componentDocument from './specification/component-contracts.json';
import stateDocument from './specification/state-taxonomy.json';
import shortcutDocument from './specification/standard-shortcuts.json';
import menuDocument from './specification/menu-taxonomy.json';
import dialogDocument from './specification/standard-dialogs.json';
import screenSpecSchemaDocument from './specification/screen-spec.schema.json';
import moduleManifestSchemaDocument from './specification/module-manifest.schema.json';
import svgManifestDocument from './specification/svg-manifest.json';

function immutable(value) {
  if(!value || typeof value !== 'object' || Object.isFrozen(value)) return value;
  Object.values(value).forEach(immutable);
  return Object.freeze(value);
}

export const ZERO_GREY_TOKENS = immutable(tokenDocument);
export const COMPONENT_CONTRACTS = immutable(componentDocument);
export const STATE_TAXONOMY = immutable(stateDocument);
export const STANDARD_SHORTCUTS = immutable(shortcutDocument);
export const MENU_TAXONOMY = immutable(menuDocument);
export const STANDARD_DIALOGS = immutable(dialogDocument);
export const SCREEN_SPEC_SCHEMA = immutable(screenSpecSchemaDocument);
export const MODULE_MANIFEST_SCHEMA = immutable(moduleManifestSchemaDocument);
export const SVG_REFERENCE_MANIFEST = immutable(svgManifestDocument);

export const DESIGN_SYSTEM_COMPONENTS = Object.freeze(
  Object.keys(COMPONENT_CONTRACTS.components)
);

export function designTokens(theme='light', profile='standard') {
  const themeTokens = ZERO_GREY_TOKENS.theme[theme];
  const profileTokens = ZERO_GREY_TOKENS.profiles[profile];
  if(!themeTokens) throw new TypeError(`Unknown design-system theme: ${theme}`);
  if(!profileTokens) {
    throw new TypeError(`Unknown presentation profile: ${profile}`);
  }
  return Object.freeze({
    theme: themeTokens,
    profile: profileTokens,
    geometry: ZERO_GREY_TOKENS.geometry,
    spacing: ZERO_GREY_TOKENS.spacing,
    layers: ZERO_GREY_TOKENS.layers,
    timing: ZERO_GREY_TOKENS.timing_ms,
    type: ZERO_GREY_TOKENS.type,
    fonts: ZERO_GREY_TOKENS.fonts,
    dataVisualization: ZERO_GREY_TOKENS.data_visualization,
  });
}

export function componentContract(name) {
  const contract = COMPONENT_CONTRACTS.components[name];
  if(!contract) throw new TypeError(`Unknown design-system component: ${name}`);
  return contract;
}
