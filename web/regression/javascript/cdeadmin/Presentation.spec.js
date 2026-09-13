/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {
  contrastRatio,
  presentationCssVariables,
  presentationThemeOverrides,
  readAccessibilitySafeMode,
  resolvePresentation,
  safeModePreferences,
  writeAccessibilitySafeMode,
} from 'sources/cdeadmin_ui/foundations/presentation';

const baseTheme = {
  palette: {
    background: {default: '#FFFFFF', paper: '#FFFFFF'},
    text: {primary: '#222222', muted: '#646B82'},
    primary: {main: '#326690', light: '#D6EFFC'},
    success: {main: '#26852B'},
    warning: {main: '#EEA236'},
    error: {main: '#CC0000'},
  },
  otherVars: {borderColor: '#BAC1CD'},
  typography: {
    fontFamily: 'Interface Sans',
    fontFamilySourceCode: 'Interface Mono',
  },
};

describe('CDEadmin presentation profiles', () => {
  it('preserves the classic profile by default', () => {
    const value = resolvePresentation({}, baseTheme);

    expect(value.profileId).toBe('classic');
    expect(value.scale).toBe(100);
    expect(value.controlHeight).toBe(28);
    expect(value.fontFamily).toBe('Interface Sans');
    expect(value.warnings).toEqual([]);
  });

  it('normalizes abbreviated colors from existing themes', () => {
    const value = resolvePresentation({}, {
      ...baseTheme,
      palette: {
        ...baseTheme.palette,
        background: {default: '#111', paper: '#111'},
        text: {primary: '#fff', muted: '#ddd'},
      },
    });

    expect(value.colors.canvas).toBe('#111111');
    expect(value.colors.text).toBe('#FFFFFF');
  });

  it('applies the low-vision profile and bounded overrides', () => {
    const value = resolvePresentation({
      accessibility_profile: 'low_vision',
      accessibility_ui_scale: 999,
      accessibility_focus_width: 1,
      accessibility_ui_font_family: 'Atkinson Hyperlegible',
    }, baseTheme);

    expect(value.scale).toBe(300);
    expect(value.focusWidth).toBe(2);
    expect(value.targetSize).toBe(44);
    expect(value.reduceMotion).toBe(true);
    expect(value.fontFamily).toBe('Atkinson Hyperlegible');
  });

  it('recognizes the inherited named dark theme used by the application', () => {
    const value = resolvePresentation({
      accessibility_profile: 'cdeadmin_standard',
    }, {...baseTheme, name: 'dark'});

    expect(value.colors.canvas).toBe('#04054F');
    expect(value.colors.navigation).toBe('#02196E');
    expect(value.colors.text).toBe('#EDF2FB');
  });

  it('uses system reduced-motion state when requested', () => {
    const value = resolvePresentation({
      accessibility_profile: 'system_adaptive',
      accessibility_motion: 'system',
    }, baseTheme, {systemReduceMotion: true});

    expect(value.reduceMotion).toBe(true);
  });

  it('provides complete light and dark high-contrast profiles', () => {
    const light = resolvePresentation({
      accessibility_profile: 'high_contrast_light',
    }, baseTheme);
    const dark = resolvePresentation({
      accessibility_profile: 'high_contrast_dark',
    }, baseTheme);

    expect(contrastRatio(light.colors.text, light.colors.canvas))
      .toBeGreaterThanOrEqual(7);
    expect(contrastRatio(dark.colors.text, dark.colors.canvas))
      .toBeGreaterThanOrEqual(7);
    expect(light.focusWidth).toBe(4);
    expect(dark.colors.focus).toBe('#FFD740');
  });

  it('rejects unsafe text and focus color combinations', () => {
    const value = resolvePresentation({
      accessibility_color_text: '#FFFFFF',
      accessibility_color_focus: '#FFFFFF',
    }, baseTheme);

    expect(value.colors.text).toBe('#222222');
    expect(value.colors.focus).toBe('#222222');
    expect(value.warnings).toEqual(expect.arrayContaining([
      'accessibility_color_text',
      'accessibility_color_focus',
    ]));
  });

  it('repairs an unsafe inherited foreground on a custom background', () => {
    const value = resolvePresentation({
      accessibility_color_canvas: '#222222',
      accessibility_color_panel: '#222222',
    }, baseTheme);

    expect(value.colors.canvas).toBe('#222222');
    expect(value.colors.panel).toBe('#222222');
    expect(value.colors.text).toBe('#FFFFFF');
    expect(value.warnings).toContain('accessibility_color_text');
  });

  it('accepts safe custom colors and maps them to theme tokens', () => {
    const value = resolvePresentation({
      accessibility_color_canvas: '#101820',
      accessibility_color_panel: '#101820',
      accessibility_color_text: '#FFFFFF',
      accessibility_color_primary: '#FFB000',
      accessibility_color_focus: '#FFB000',
      accessibility_color_border: '#AAB7C4',
    }, baseTheme);
    const overrides = presentationThemeOverrides(value);

    expect(value.colors.canvas).toBe('#101820');
    expect(value.colors.text).toBe('#FFFFFF');
    expect(overrides.palette.primary.main).toBe('#FFB000');
    expect(overrides.palette.primary.contrastText).toBe('#000000');
    expect(overrides.otherVars.borderColor).toBe('#AAB7C4');
  });

  it('exports stable CSS custom properties', () => {
    const value = resolvePresentation({
      accessibility_profile: 'motor_assistance',
    }, baseTheme);
    const variables = presentationCssVariables(value);

    expect(variables['--cde-font-scale']).toBe('115%');
    expect(variables['--cde-target-size']).toBe('48px');
    expect(variables['--cde-scrollbar-size']).toBe('18px');
    expect(variables['--cde-resize-handle-size']).toBe('12');
    expect(variables['--cde-tree-indent']).toBe('29px');
    expect(variables['--cde-tree-expander-size']).toBe('26px');
    expect(variables['--cde-tree-guide-width']).toBe('1px');
    expect(variables['--cde-tree-guide-color']).toBe('#66ADD3');
    expect(variables['--cde-grid-header-height']).toBe('52px');
    expect(variables['--cde-grid-cell-padding']).toBe('12px');
    expect(variables['--cde-motion-normal']).toBe('0.01ms');
    expect(variables['--cde-layer-dialog']).toBe(2100);
    expect(variables['--cde-color-text']).toBe('#03045E');
  });

  it('bounds user-configurable branching-tree geometry', () => {
    const value = resolvePresentation({
      accessibility_tree_indent: 999,
      accessibility_tree_expander_size: 4,
      accessibility_tree_guide_width: 9,
    }, baseTheme);

    expect(value.treeIndent).toBe(48);
    expect(value.treeExpanderSize).toBe(12);
    expect(value.treeGuideWidth).toBe(4);
  });

  it('bounds user-configurable grid geometry', () => {
    const value = resolvePresentation({
      accessibility_grid_row_height: 999,
      accessibility_grid_header_height: 2,
      accessibility_grid_cell_padding: 99,
    }, baseTheme);

    expect(value.gridRowHeight).toBe(96);
    expect(value.gridHeaderHeight).toBe(24);
    expect(value.gridCellPadding).toBe(32);
  });

  it('controls workspace chrome geometry and activity emphasis', () => {
    const value = resolvePresentation({
      accessibility_tab_height: 200,
      accessibility_toolbar_height: 10,
      accessibility_menu_row_height: 44,
      accessibility_status_height: 36,
      accessibility_corner_radius: 12,
      accessibility_active_tab_scale: 135,
      accessibility_inactive_brightness: 60,
    }, baseTheme);
    const variables = presentationCssVariables(value);
    expect(value).toMatchObject({tabHeight: 96, toolbarHeight: 24,
      menuRowHeight: 44, statusHeight: 36, cornerRadius: 12,
      activeTabScale: 1.35, inactiveBrightness: 0.6});
    expect(variables['--cde-corner-radius']).toBe('12px');
    expect(variables['--cde-active-tab-scale']).toBe('1.35');
    expect(variables['--cde-inactive-brightness']).toBe('0.6');
    expect(presentationThemeOverrides(value).shape.borderRadius).toBe(12);
  });

  it('calculates standard contrast ratios', () => {
    expect(contrastRatio('#000000', '#FFFFFF')).toBeCloseTo(21, 4);
    expect(contrastRatio('invalid', '#FFFFFF')).toBe(0);
  });

  it('provides a recoverable high-legibility safe mode', () => {
    const preferences = safeModePreferences({
      accessibility_color_canvas: '#000000',
      accessibility_profile: 'compact_expert',
    });

    expect(preferences.accessibility_profile).toBe('low_vision');
    expect(preferences.accessibility_motion).toBe('reduce');
    expect(preferences.accessibility_color_canvas).toBe('');
  });

  it('reads and writes safe mode without trusting storage availability', () => {
    const data = new Map();
    const storage = {
      getItem: (key) => data.get(key),
      setItem: (key, value) => data.set(key, value),
    };

    expect(readAccessibilitySafeMode(storage)).toBe(false);
    expect(writeAccessibilitySafeMode(storage, true)).toBe(true);
    expect(readAccessibilitySafeMode(storage)).toBe(true);
    expect(readAccessibilitySafeMode({getItem: () => { throw Error(); }}))
      .toBe(false);
  });
});
