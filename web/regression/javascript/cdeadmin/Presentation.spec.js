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
    expect(variables['--cde-scrollbar-size']).toBe('24px');
    expect(variables['--cde-resize-handle-size']).toBe('12');
    expect(variables['--cde-motion-normal']).toBe('0.01ms');
    expect(variables['--cde-layer-dialog']).toBe(3001);
    expect(variables['--cde-color-text']).toBe('#222222');
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
