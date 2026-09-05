/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

const DEFAULT_FONT = [
  'Roboto',
  '"Helvetica Neue"',
  '-apple-system',
  'BlinkMacSystemFont',
  '"Segoe UI"',
  'Arial',
  'sans-serif',
].join(',');

const DEFAULT_MONOSPACE_FONT = [
  '"Source Code Pro"',
  'SFMono-Regular',
  'Menlo',
  'Monaco',
  'Consolas',
  '"Liberation Mono"',
  '"Courier New"',
  'monospace',
].join(',');

export const PRESENTATION_PROFILE_IDS = Object.freeze({
  CLASSIC: 'classic',
  CDEADMIN_STANDARD: 'cdeadmin_standard',
  SYSTEM_ADAPTIVE: 'system_adaptive',
  HIGH_CONTRAST_LIGHT: 'high_contrast_light',
  HIGH_CONTRAST_DARK: 'high_contrast_dark',
  LOW_VISION: 'low_vision',
  MOTOR_ASSISTANCE: 'motor_assistance',
  REDUCED_MOTION: 'reduced_motion',
  COMPACT_EXPERT: 'compact_expert',
});

export const PRESENTATION_PROFILES = Object.freeze({
  [PRESENTATION_PROFILE_IDS.CLASSIC]: Object.freeze({
    scale: 100,
    iconScale: 100,
    lineHeight: 1.43,
    letterSpacing: 0.01071,
    density: 'classic',
    controlHeight: 28,
    targetSize: 24,
    treeRowHeight: 28,
    gridRowHeight: 28,
    focusWidth: 2,
    focusOffset: 1,
    reduceMotion: false,
  }),
  [PRESENTATION_PROFILE_IDS.CDEADMIN_STANDARD]: Object.freeze({
    scale: 100,
    iconScale: 100,
    lineHeight: 1.5,
    letterSpacing: 0.01,
    density: 'standard',
    controlHeight: 32,
    targetSize: 28,
    treeRowHeight: 30,
    gridRowHeight: 30,
    focusWidth: 2,
    focusOffset: 2,
    reduceMotion: 'system',
  }),
  [PRESENTATION_PROFILE_IDS.SYSTEM_ADAPTIVE]: Object.freeze({
    scale: 100,
    iconScale: 100,
    lineHeight: 1.43,
    letterSpacing: 0.01071,
    density: 'standard',
    controlHeight: 32,
    targetSize: 28,
    treeRowHeight: 30,
    gridRowHeight: 30,
    focusWidth: 2,
    focusOffset: 1,
    reduceMotion: 'system',
  }),
  [PRESENTATION_PROFILE_IDS.HIGH_CONTRAST_LIGHT]: Object.freeze({
    scale: 110,
    iconScale: 115,
    lineHeight: 1.5,
    letterSpacing: 0.015,
    density: 'comfortable',
    controlHeight: 36,
    targetSize: 36,
    treeRowHeight: 36,
    gridRowHeight: 36,
    focusWidth: 4,
    focusOffset: 2,
    reduceMotion: 'system',
    colors: Object.freeze({
      canvas: '#FFFFFF',
      panel: '#FFFFFF',
      text: '#000000',
      mutedText: '#333333',
      primary: '#003E73',
      focus: '#003E73',
      border: '#000000',
      selection: '#BDE3FF',
      success: '#006B2D',
      warning: '#8A4B00',
      error: '#B00020',
    }),
  }),
  [PRESENTATION_PROFILE_IDS.HIGH_CONTRAST_DARK]: Object.freeze({
    scale: 110,
    iconScale: 115,
    lineHeight: 1.5,
    letterSpacing: 0.015,
    density: 'comfortable',
    controlHeight: 36,
    targetSize: 36,
    treeRowHeight: 36,
    gridRowHeight: 36,
    focusWidth: 4,
    focusOffset: 2,
    reduceMotion: 'system',
    colors: Object.freeze({
      canvas: '#000000',
      panel: '#111111',
      text: '#FFFFFF',
      mutedText: '#E0E0E0',
      primary: '#FFD740',
      focus: '#FFD740',
      border: '#FFFFFF',
      selection: '#164E78',
      success: '#6DFF8B',
      warning: '#FFD166',
      error: '#FF808F',
    }),
  }),
  [PRESENTATION_PROFILE_IDS.LOW_VISION]: Object.freeze({
    scale: 150,
    iconScale: 150,
    lineHeight: 1.6,
    letterSpacing: 0.03,
    density: 'comfortable',
    controlHeight: 44,
    targetSize: 44,
    treeRowHeight: 44,
    gridRowHeight: 44,
    focusWidth: 4,
    focusOffset: 2,
    reduceMotion: true,
  }),
  [PRESENTATION_PROFILE_IDS.MOTOR_ASSISTANCE]: Object.freeze({
    scale: 115,
    iconScale: 135,
    lineHeight: 1.5,
    letterSpacing: 0.015,
    density: 'touch',
    controlHeight: 48,
    targetSize: 48,
    treeRowHeight: 48,
    gridRowHeight: 48,
    focusWidth: 3,
    focusOffset: 2,
    reduceMotion: true,
  }),
  [PRESENTATION_PROFILE_IDS.REDUCED_MOTION]: Object.freeze({
    scale: 100,
    iconScale: 100,
    lineHeight: 1.43,
    letterSpacing: 0.01071,
    density: 'standard',
    controlHeight: 32,
    targetSize: 28,
    treeRowHeight: 30,
    gridRowHeight: 30,
    focusWidth: 2,
    focusOffset: 1,
    reduceMotion: true,
  }),
  [PRESENTATION_PROFILE_IDS.COMPACT_EXPERT]: Object.freeze({
    scale: 90,
    iconScale: 90,
    lineHeight: 1.35,
    letterSpacing: 0,
    density: 'compact',
    controlHeight: 26,
    targetSize: 24,
    treeRowHeight: 24,
    gridRowHeight: 24,
    focusWidth: 2,
    focusOffset: 1,
    reduceMotion: false,
  }),
});

const OVERRIDE_RANGES = Object.freeze({
  accessibility_ui_scale: [75, 300],
  accessibility_icon_scale: [75, 300],
  accessibility_line_height: [1, 2.5],
  accessibility_letter_spacing: [0, 0.2],
  accessibility_control_height: [24, 72],
  accessibility_target_size: [24, 72],
  accessibility_target_spacing: [0, 24],
  accessibility_scrollbar_size: [8, 48],
  accessibility_resize_handle_size: [5, 32],
  accessibility_panel_gap: [0, 32],
  accessibility_tree_row_height: [24, 72],
  accessibility_grid_row_height: [24, 96],
  accessibility_focus_width: [2, 8],
  accessibility_focus_offset: [0, 6],
});

const COLOR_PREFERENCES = Object.freeze({
  accessibility_color_canvas: 'canvas',
  accessibility_color_panel: 'panel',
  accessibility_color_text: 'text',
  accessibility_color_muted_text: 'mutedText',
  accessibility_color_primary: 'primary',
  accessibility_color_focus: 'focus',
  accessibility_color_border: 'border',
  accessibility_color_selection: 'selection',
  accessibility_color_success: 'success',
  accessibility_color_warning: 'warning',
  accessibility_color_error: 'error',
});

export const ACCESSIBILITY_SAFE_MODE_STORAGE_KEY =
  'cdeadmin.accessibility.safe_mode';

const HEX_COLOR = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;

function asNumber(value) {
  if(value === '' || value === null || value === undefined) {
    return null;
  }
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function boundedOverride(preferences, name, fallback) {
  const number = asNumber(preferences?.[name]);
  if(number === null || number === -1) {
    return fallback;
  }
  const [minimum, maximum] = OVERRIDE_RANGES[name];
  return Math.min(maximum, Math.max(minimum, number));
}

function normalizeHex(value) {
  if(typeof value !== 'string') {
    return null;
  }
  const normalized = value.trim();
  if(!HEX_COLOR.test(normalized)) {
    return null;
  }
  if(normalized.length === 4) {
    return (`#${normalized[1]}${normalized[1]}${normalized[2]}`+
      `${normalized[2]}${normalized[3]}${normalized[3]}`).toUpperCase();
  }
  return normalized.toUpperCase();
}

function rgb(color) {
  return [1, 3, 5].map((offset) => parseInt(color.slice(offset, offset + 2), 16));
}

function luminance(color) {
  const channels = rgb(color).map((channel) => {
    const normalized = channel / 255;
    return normalized <= 0.04045 ?
      normalized / 12.92 :
      Math.pow((normalized + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] +
    0.0722 * channels[2];
}

export function contrastRatio(foreground, background) {
  const first = normalizeHex(foreground);
  const second = normalizeHex(background);
  if(!first || !second) {
    return 0;
  }
  const lighter = Math.max(luminance(first), luminance(second));
  const darker = Math.min(luminance(first), luminance(second));
  return (lighter + 0.05) / (darker + 0.05);
}

function contrastText(background) {
  return contrastRatio('#000000', background) >=
    contrastRatio('#FFFFFF', background) ? '#000000' : '#FFFFFF';
}

function baseColors(theme) {
  return {
    canvas: normalizeHex(theme?.palette?.background?.default) || '#FFFFFF',
    panel: normalizeHex(theme?.palette?.background?.paper) || '#FFFFFF',
    text: normalizeHex(theme?.palette?.text?.primary) || '#222222',
    mutedText: normalizeHex(theme?.palette?.text?.muted) || '#646B82',
    primary: normalizeHex(theme?.palette?.primary?.main) || '#326690',
    focus: normalizeHex(theme?.palette?.primary?.main) || '#326690',
    border: normalizeHex(theme?.otherVars?.borderColor) || '#BAC1CD',
    selection: normalizeHex(theme?.palette?.primary?.light) || '#D6EFFC',
    success: normalizeHex(theme?.palette?.success?.main) || '#26852B',
    warning: normalizeHex(theme?.palette?.warning?.main) || '#EEA236',
    error: normalizeHex(theme?.palette?.error?.main) || '#CC0000',
  };
}

function readCustomColors(preferences) {
  return Object.entries(COLOR_PREFERENCES).reduce((result, [preference, token]) => {
    const value = normalizeHex(preferences?.[preference]);
    if(value) {
      result[token] = value;
    }
    return result;
  }, {});
}

function applySafeColors(defaults, requested) {
  const colors = {...defaults, ...requested};
  const warnings = [];
  const revertRequested = (...tokens) => {
    tokens.forEach((token) => {
      if(requested[token]) {
        colors[token] = defaults[token];
      }
    });
  };
  const changed = (...tokens) => tokens.some(
    (token) => colors[token] !== defaults[token]
  );
  const foregroundFor = (minimum) => [
    colors.text, defaults.text, '#000000', '#FFFFFF',
  ].find((candidate) =>
    contrastRatio(candidate, colors.canvas) >= minimum &&
    contrastRatio(candidate, colors.panel) >= minimum
  );

  if(changed('text', 'canvas', 'panel') &&
      (contrastRatio(colors.text, colors.canvas) < 4.5 ||
      contrastRatio(colors.text, colors.panel) < 4.5)) {
    const replacement = foregroundFor(4.5);
    if(replacement) {
      colors.text = replacement;
    } else {
      revertRequested('text', 'canvas', 'panel');
    }
    warnings.push('accessibility_color_text');
  }
  if(changed('mutedText', 'canvas', 'panel') &&
      (contrastRatio(colors.mutedText, colors.canvas) < 4.5 ||
      contrastRatio(colors.mutedText, colors.panel) < 4.5)) {
    colors.mutedText = colors.text;
    warnings.push('accessibility_color_muted_text');
  }
  if(changed('border', 'canvas', 'panel') &&
      contrastRatio(colors.border, colors.canvas) < 3 &&
      contrastRatio(colors.border, colors.panel) < 3) {
    colors.border = colors.text;
    warnings.push('accessibility_color_border');
  }
  if(changed('focus', 'canvas', 'panel') &&
      (contrastRatio(colors.focus, colors.canvas) < 3 ||
      contrastRatio(colors.focus, colors.panel) < 3)) {
    colors.focus = colors.text;
    warnings.push('accessibility_color_focus');
  }

  return {colors, warnings};
}

function resolveMotion(value, profileValue, systemReduceMotion) {
  if(value === 'reduce') {
    return true;
  }
  if(value === 'full') {
    return false;
  }
  if(value === 'system' || profileValue === 'system') {
    return Boolean(systemReduceMotion);
  }
  return Boolean(profileValue);
}

function densitySpacing(density) {
  return {
    compact: 0.75,
    classic: 1,
    standard: 1,
    comfortable: 1.25,
    touch: 1.5,
  }[density] ?? 1;
}

export function resolvePresentation(preferences={}, theme={}, environment={}) {
  const requestedProfile = preferences.accessibility_profile;
  const profileId = PRESENTATION_PROFILES[requestedProfile] ?
    requestedProfile : PRESENTATION_PROFILE_IDS.CLASSIC;
  const profile = PRESENTATION_PROFILES[profileId];
  const defaultColorValues = baseColors(theme);
  const {colors, warnings} = applySafeColors(
    defaultColorValues,
    {...profile.colors, ...readCustomColors(preferences)}
  );
  const density = preferences.accessibility_density &&
    preferences.accessibility_density !== 'profile' ?
    preferences.accessibility_density : profile.density;
  const targetSize = boundedOverride(
    preferences, 'accessibility_target_size', profile.targetSize);
  const largeTarget = targetSize >= 44;

  return Object.freeze({
    schemaVersion: 1,
    profileId,
    scale: boundedOverride(
      preferences, 'accessibility_ui_scale', profile.scale),
    iconScale: boundedOverride(
      preferences, 'accessibility_icon_scale', profile.iconScale),
    lineHeight: boundedOverride(
      preferences, 'accessibility_line_height', profile.lineHeight),
    letterSpacing: boundedOverride(
      preferences, 'accessibility_letter_spacing', profile.letterSpacing),
    density,
    controlHeight: boundedOverride(
      preferences, 'accessibility_control_height', profile.controlHeight),
    targetSize,
    targetSpacing: boundedOverride(
      preferences, 'accessibility_target_spacing', largeTarget ? 8 : 4),
    scrollbarSize: boundedOverride(
      preferences, 'accessibility_scrollbar_size', largeTarget ? 24 : 16),
    resizeHandleSize: boundedOverride(
      preferences, 'accessibility_resize_handle_size', largeTarget ? 12 : 8),
    panelGap: boundedOverride(
      preferences, 'accessibility_panel_gap',
      density === 'compact' ? 4 : largeTarget ? 12 : 8),
    spacingMultiplier: densitySpacing(density),
    treeRowHeight: boundedOverride(
      preferences, 'accessibility_tree_row_height', profile.treeRowHeight),
    gridRowHeight: boundedOverride(
      preferences, 'accessibility_grid_row_height', profile.gridRowHeight),
    focusWidth: boundedOverride(
      preferences, 'accessibility_focus_width', profile.focusWidth),
    focusOffset: boundedOverride(
      preferences, 'accessibility_focus_offset', profile.focusOffset),
    reduceMotion: resolveMotion(
      preferences.accessibility_motion,
      profile.reduceMotion,
      environment.systemReduceMotion
    ),
    fontFamily: preferences.accessibility_ui_font_family?.trim() ||
      theme?.typography?.fontFamily || DEFAULT_FONT,
    monospaceFontFamily:
      preferences.accessibility_monospace_font_family?.trim() ||
      theme?.typography?.fontFamilySourceCode || DEFAULT_MONOSPACE_FONT,
    colors: Object.freeze(colors),
    warnings: Object.freeze(warnings),
  });
}

export function safeModePreferences(preferences={}) {
  const safe = {
    ...preferences,
    accessibility_profile: PRESENTATION_PROFILE_IDS.LOW_VISION,
    accessibility_density: 'comfortable',
    accessibility_motion: 'reduce',
  };
  Object.keys(COLOR_PREFERENCES).forEach((name) => {
    safe[name] = '';
  });
  Object.keys(OVERRIDE_RANGES).forEach((name) => {
    safe[name] = -1;
  });
  safe.accessibility_ui_font_family = '';
  safe.accessibility_monospace_font_family = '';
  return safe;
}

export function readAccessibilitySafeMode(storage) {
  try {
    return storage?.getItem(ACCESSIBILITY_SAFE_MODE_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

export function writeAccessibilitySafeMode(storage, enabled) {
  try {
    storage?.setItem(
      ACCESSIBILITY_SAFE_MODE_STORAGE_KEY,
      enabled ? 'true' : 'false'
    );
    return true;
  } catch {
    return false;
  }
}

export function presentationThemeOverrides(presentation) {
  const {colors} = presentation;
  return {
    typography: {
      fontFamily: presentation.fontFamily,
      fontFamilySourceCode: presentation.monospaceFontFamily,
    },
    palette: {
      background: {default: colors.canvas, paper: colors.panel},
      text: {primary: colors.text, muted: colors.mutedText},
      primary: {
        main: colors.primary,
        contrastText: contrastText(colors.primary),
        light: colors.selection,
      },
      success: {main: colors.success},
      warning: {main: colors.warning},
      error: {main: colors.error},
      default: {
        main: colors.panel,
        contrastText: colors.text,
        borderColor: colors.border,
        disabledBorderColor: colors.border,
      },
    },
    custom: {
      checkbox: {borderColor: colors.border},
      icon: {
        main: colors.panel,
        contrastText: colors.text,
        borderColor: colors.border,
        disabledMain: colors.panel,
        disabledContrastText: colors.mutedText,
        disabledBorderColor: colors.border,
        hoverContrastText: colors.text,
      },
    },
    otherVars: {
      borderColor: colors.border,
      inputBorderColor: colors.border,
      activeColor: colors.primary,
      activeBorder: colors.primary,
      headerBg: colors.panel,
      tableBg: colors.panel,
      qtDatagridBg: colors.panel,
      qtDatagridSelectFg: colors.text,
      cardHeaderBg: colors.panel,
      textMuted: colors.mutedText,
      editor: {
        fg: colors.text,
        bg: colors.canvas,
        selectionBg: colors.selection,
      },
      tree: {
        textFg: colors.text,
        inputBg: colors.canvas,
        fgHover: colors.text,
        textHoverFg: colors.text,
        bgSelected: colors.selection,
      },
    },
    cdeadminPresentation: presentation,
  };
}

export function presentationCssVariables(presentation) {
  const {colors} = presentation;
  return {
    '--cde-font-scale': `${presentation.scale}%`,
    '--cde-icon-scale': `${presentation.iconScale / 100}em`,
    '--cde-line-height': String(presentation.lineHeight),
    '--cde-letter-spacing': `${presentation.letterSpacing}em`,
    '--cde-control-height': `${presentation.controlHeight}px`,
    '--cde-target-size': `${presentation.targetSize}px`,
    '--cde-target-spacing': `${presentation.targetSpacing}px`,
    '--cde-scrollbar-size': `${presentation.scrollbarSize}px`,
    '--cde-resize-handle-size': `${presentation.resizeHandleSize}`,
    '--cde-panel-gap': `${presentation.panelGap}px`,
    '--cde-spacing-multiplier': String(presentation.spacingMultiplier),
    '--cde-tree-row-height': `${presentation.treeRowHeight}px`,
    '--cde-grid-row-height': `${presentation.gridRowHeight}px`,
    '--cde-focus-width': `${presentation.focusWidth}px`,
    '--cde-focus-offset': `${presentation.focusOffset}px`,
    '--cde-radius-control': '4px',
    '--cde-radius-panel': '4px',
    '--cde-radius-dialog': '6px',
    '--cde-elevation-raised': '0 1px 3px rgba(0, 0, 0, 0.24)',
    '--cde-elevation-overlay': '0 8px 24px rgba(0, 0, 0, 0.32)',
    '--cde-motion-fast': presentation.reduceMotion ? '0.01ms' : '100ms',
    '--cde-motion-normal': presentation.reduceMotion ? '0.01ms' : '180ms',
    '--cde-motion-slow': presentation.reduceMotion ? '0.01ms' : '300ms',
    '--cde-layer-content': 0,
    '--cde-layer-sticky': 10,
    '--cde-layer-toolbar': 100,
    '--cde-layer-menu': 1200,
    '--cde-layer-popover': 1300,
    '--cde-layer-dialog': 3001,
    '--cde-layer-toast': 4000,
    '--cde-layer-drag': 5000,
    '--cde-color-canvas': colors.canvas,
    '--cde-color-panel': colors.panel,
    '--cde-color-text': colors.text,
    '--cde-color-text-muted': colors.mutedText,
    '--cde-color-primary': colors.primary,
    '--cde-color-focus': colors.focus,
    '--cde-color-border': colors.border,
    '--cde-color-selection': colors.selection,
    '--cde-color-success': colors.success,
    '--cde-color-warning': colors.warning,
    '--cde-color-error': colors.error,
  };
}
