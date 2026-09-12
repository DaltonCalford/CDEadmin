/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {
  COMPONENT_CONTRACTS,
  DESIGN_SYSTEM_COMPONENTS,
  DESIGN_SYSTEM_IMPLEMENTATIONS,
  MENU_TAXONOMY,
  SCREEN_SPEC_SCHEMA,
  STANDARD_DIALOGS,
  STANDARD_SHORTCUTS,
  STATE_TAXONOMY,
  SVG_REFERENCE_MANIFEST,
  ZERO_GREY_TOKENS,
  componentContract,
  designSystemComponent,
  designTokens,
} from 'sources/cdeadmin_ui';
import {
  presentationCssVariables,
  resolvePresentation,
} from 'sources/cdeadmin_ui/foundations/presentation';
import Ajv2020 from 'ajv/dist/2020';
import workbenchScreen from 'sources/cdeadmin_ui/foundations/screens/workbench-shell.json';
import projectExplorerScreen from 'sources/cdeadmin_ui/foundations/screens/project-explorer.json';
import ddnViewerScreen from 'sources/cdeadmin_ui/foundations/screens/ddn-viewer.json';
import ddnDesignerScreen from 'sources/cdeadmin_ui/foundations/screens/ddn-designer.json';
import {ddnModuleDefinition} from 'sources/cdeadmin_ui/integrations/ddn/module';

const theme = (mode) => ({
  palette: {mode, background: {default: '#FFFFFF', paper: '#FFFFFF'},
    text: {primary: '#000000', muted: '#000000'},
    primary: {main: '#000000', light: '#FFFFFF'}, success: {main: '#000000'},
    warning: {main: '#000000'}, error: {main: '#000000'}},
  otherVars: {borderColor: '#000000'},
  typography: {fontFamily: 'ignored', fontFamilySourceCode: 'ignored'},
});

describe('CDEadmin Zero-Grey executable contracts', () => {
  it('binds every one of the 63 named component contracts to an implementation', () => {
    expect(DESIGN_SYSTEM_COMPONENTS).toHaveLength(63);
    expect(Object.keys(DESIGN_SYSTEM_IMPLEMENTATIONS).sort())
      .toEqual([...DESIGN_SYSTEM_COMPONENTS].sort());
    for(const name of DESIGN_SYSTEM_COMPONENTS) {
      expect(componentContract(name).purpose).toEqual(expect.any(String));
      expect(componentContract(name).states.length).toBeGreaterThan(0);
      expect(designSystemComponent(name)).toEqual(expect.anything());
    }
  });

  it('rejects unknown themes, profiles, components, and implementations', () => {
    expect(() => designTokens('sepia')).toThrow('Unknown design-system theme');
    expect(() => designTokens('light', 'tiny')).toThrow('Unknown presentation profile');
    expect(() => componentContract('Card')).toThrow('Unknown design-system component');
    expect(() => designSystemComponent('Card')).toThrow('Unknown design-system component');
  });

  it('keeps specification documents and nested values immutable', () => {
    expect(Object.isFrozen(ZERO_GREY_TOKENS)).toBe(true);
    expect(Object.isFrozen(ZERO_GREY_TOKENS.theme.dark)).toBe(true);
    expect(Object.isFrozen(COMPONENT_CONTRACTS.components.Button)).toBe(true);
    expect(Object.isFrozen(STATE_TAXONOMY)).toBe(true);
    expect(Object.isFrozen(STANDARD_SHORTCUTS)).toBe(true);
    expect(Object.isFrozen(MENU_TAXONOMY)).toBe(true);
    expect(Object.isFrozen(STANDARD_DIALOGS.dialogs.destructive)).toBe(true);
    expect(Object.isFrozen(SCREEN_SPEC_SCHEMA.properties.states)).toBe(true);
    expect(Object.isFrozen(SVG_REFERENCE_MANIFEST.plates)).toBe(true);
  });

  it('validates every implemented top-level surface against the normative schema', () => {
    const validate = new Ajv2020({strict: false}).compile(SCREEN_SPEC_SCHEMA);
    for(const specification of [
      workbenchScreen, projectExplorerScreen, ddnViewerScreen, ddnDesignerScreen,
    ]) {
      expect({id: specification.screen_id, errors: validate(specification) ?
        [] : validate.errors}).toEqual({id: specification.screen_id, errors: []});
      expect(specification.spec_gaps).toEqual([]);
    }
  });

  it('binds every DDN screen command to an executable module contribution', () => {
    const commandIds = new Set(ddnModuleDefinition().contributions.commands
      .map((command) => command.id));
    for(const specification of [ddnViewerScreen, ddnDesignerScreen]) {
      const declared = [...specification.entry_commands,
        ...specification.toolbar_commands];
      expect({screen: specification.screen_id, missing: declared
        .filter((command) => !commandIds.has(command))})
        .toEqual({screen: specification.screen_id, missing: []});
    }
  });

  it('carries the complete standard dialog and 26-plate SVG authorities', () => {
    expect(Object.keys(STANDARD_DIALOGS.dialogs)).toEqual([
      'about', 'unsaved_changes', 'destructive', 'credentials', 'simple_input', 'wizard',
    ]);
    expect(SVG_REFERENCE_MANIFEST.normative).toBe(true);
    expect(SVG_REFERENCE_MANIFEST.plates).toHaveLength(26);
    expect(SVG_REFERENCE_MANIFEST.plates.map((plate) => plate.order))
      .toEqual(Array.from({length: 26}, (_value, index) => index + 1));
  });

  it.each([
    ['cdeadmin_standard', 'standard'],
    ['comfortable', 'comfortable'],
    ['compact_expert', 'compact_expert'],
    ['low_vision', 'low_vision'],
    ['motor_assistance', 'motor_assistance'],
  ])('maps %s to every exact %s geometry token', (profileId, tokenId) => {
    const value = resolvePresentation({accessibility_profile: profileId}, theme('light'));
    const exact = ZERO_GREY_TOKENS.profiles[tokenId];
    expect(value).toEqual(expect.objectContaining({
      controlHeight: exact.control_height,
      targetSize: exact.target_size,
      treeRowHeight: exact.tree_row,
      gridRowHeight: exact.grid_row,
      tabHeight: exact.tab_height,
      toolbarHeight: exact.toolbar_height,
      menuRowHeight: exact.menu_row,
      statusHeight: exact.status_height,
      scrollbarSize: exact.scrollbar,
      resizeHandleSize: exact.splitter_hit,
      focusWidth: exact.focus_width,
      focusOffset: exact.focus_offset,
    }));
  });

  it.each(['light', 'dark'])('maps all semantic %s theme values', (mode) => {
    const value = resolvePresentation({accessibility_profile: 'cdeadmin_standard'},
      theme(mode));
    const source = ZERO_GREY_TOKENS.theme[mode];
    expect(value.colors).toEqual(expect.objectContaining({
      canvas: source.surface_canvas,
      panel: source.surface_panel,
      root: source.surface_root,
      navigation: source.surface_navigation,
      workspace: source.surface_workspace,
      elevated: source.surface_elevated,
      editor: source.surface_editor,
      grid: source.surface_grid,
      text: source.content_primary,
      secondaryText: source.content_secondary,
      mutedText: source.content_muted,
      disabledText: source.content_disabled,
      primary: source.action_primary,
      primaryText: source.action_primary_text,
      hover: source.action_hover,
      selection: source.action_selected,
      pressed: source.action_pressed,
      focus: source.focus,
      subtleBorder: source.border_subtle,
      border: source.border_default,
      strongBorder: source.border_strong,
      success: source.success,
      warning: source.warning,
      error: source.error,
      info: source.info,
    }));
  });

  it('exports exact layers, square geometry, motion, font, and semantic CSS', () => {
    const value = resolvePresentation({accessibility_profile: 'cdeadmin_standard'},
      theme('dark'));
    const css = presentationCssVariables(value);
    expect(css).toEqual(expect.objectContaining({
      '--cde-radius-control': '0px',
      '--cde-radius-panel': '0px',
      '--cde-radius-dialog': '0px',
      '--cde-elevation-raised': 'none',
      '--cde-elevation-overlay': 'none',
      '--cde-motion-fast': '150ms',
      '--cde-motion-normal': '250ms',
      '--cde-layer-menu': 1000,
      '--cde-layer-context-menu': 1100,
      '--cde-layer-popover': 1200,
      '--cde-layer-modal-backdrop': 2000,
      '--cde-layer-dialog': 2100,
      '--cde-layer-tooltip': 3000,
      '--cde-layer-drag': 4000,
      '--cde-toolbar-height': '34px',
      '--cde-tab-height': '32px',
      '--cde-status-height': '24px',
      '--cde-color-root': ZERO_GREY_TOKENS.theme.dark.surface_root,
      '--cde-color-info': ZERO_GREY_TOKENS.theme.dark.info,
    }));
    expect(value.fontFamily).toBe(ZERO_GREY_TOKENS.fonts.ui);
  });
});
