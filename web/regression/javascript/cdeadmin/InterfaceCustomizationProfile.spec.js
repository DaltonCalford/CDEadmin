/////////////////////////////////////////////////////////////
// ScratchRobin interface profile validation and persistence tests.
/////////////////////////////////////////////////////////////

import {
  exportCustomizationProfile,
  importCustomizationProfile,
  normalizeAppearanceCustomizations,
  normalizeCommandCustomizations,
  readCustomizationDraft,
  saveCustomizationDraft,
} from 'sources/cdeadmin_ui/customization/profile';

function preference(name, id) {
  return {module: 'browser', name, id, cid: 7, mid: 3, value: '{}'};
}

describe('CDEadmin interface customization profiles', () => {
  it('normalizes command presentation without accepting unsafe identities', () => {
    expect(normalizeCommandCustomizations({
      'tools.query.open': {label: 'Query', iconKey: 'action.search',
        surface: 'data', visible: false, enabled: false, priority: 42,
        presentation: {fontFamily: 'Atkinson Hyperlegible', fontSize: 48,
          color: '#123456', backgroundColor: 'javascript:bad',
          iconPosition: 'after'}},
      '<script>': {label: 'Unsafe'},
      'tools.bad': {iconKey: 'javascript:bad', surface: '../bad'},
    })).toEqual({
      'tools.query.open': {label: 'Query', iconKey: 'action.search',
        surface: 'data', visible: false, enabled: false, priority: 42,
        presentation: {fontFamily: 'Atkinson Hyperlegible', fontSize: 32,
          color: '#123456', iconPosition: 'after'}},
    });
  });

  it('round trips a portable validated interface profile', () => {
    const exported = exportCustomizationProfile({
      icons: {'tool.query': 'action.search'},
      menus: {tools: {label: 'Utilities', visible: true, index: 2}},
      commands: {'tools.query.open': {surface: 'tools'}},
    }, {accessibility_profile: 'low_vision'});
    const imported = importCustomizationProfile(JSON.stringify(exported));
    expect(imported.appearance.accessibility_profile).toBe('low_vision');
    expect(imported.draft.icons['tool.query']).toBe('action.search');
    expect(imported.draft.menus.tools.label).toBe('Utilities');
    expect(imported.draft.commands['tools.query.open'].surface).toBe('tools');
    expect(()=>importCustomizationProfile('{}')).toThrow('not a CDEadmin');
  });

  it('exports only bounded presentation preferences without unrelated data', () => {
    expect(normalizeAppearanceCustomizations({
      accessibility_profile: 'low_vision', accessibility_ui_scale: 999,
      accessibility_color_text: '#abcdef', accessibility_ui_font_family:
        'Atkinson Hyperlegible', unrelated_secret: 'must-not-export',
    })).toEqual({
      accessibility_profile: 'low_vision', accessibility_ui_scale: 300,
      accessibility_color_text: '#ABCDEF', accessibility_ui_font_family:
        'Atkinson Hyperlegible',
    });
  });

  it('persists all customization documents as one user-owned preference set', async () => {
    const values = new Map([
      ['icon_assignments', preference('icon_assignments', 1)],
      ['menu_customizations', preference('menu_customizations', 2)],
      ['command_customizations', preference('command_customizations', 3)],
    ]);
    const store = {
      getPreferences: (_module, name)=>values.get(name),
      setPreferences: jest.fn(async (updates)=>{
        updates.forEach((update)=>values.set(update.name, {
          ...values.get(update.name), value: update.value,
        }));
      }),
    };
    await saveCustomizationDraft(store, {
      icons: {'tool.query': 'action.search'}, menus: {}, commands: {},
    });
    expect(store.setPreferences).toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({name: 'icon_assignments'}),
      expect.objectContaining({name: 'menu_customizations'}),
      expect.objectContaining({name: 'command_customizations'}),
    ]));
    expect(readCustomizationDraft(store).icons['tool.query'])
      .toBe('action.search');
  });
});
