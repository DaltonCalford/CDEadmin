/////////////////////////////////////////////////////////////
// ScratchRobin user-facing interface, icon and menu designer.
/////////////////////////////////////////////////////////////

import {useEffect, useMemo, useState} from 'react';
import {Box} from '@mui/material';
import {styled} from '@mui/material/styles';
import PropTypes from 'prop-types';
import gettext from 'sources/gettext';
import usePreferences from '../../../../preferences/static/js/store';
import {Button} from '../primitives/Button';
import {Checkbox, Select} from '../primitives/Choice';
import {NumberField, TextField} from '../primitives/Field';
import {Icon} from '../icons';
import {listIconDefinitions} from '../icons/registry';
import {commandRegistry} from '../commands/CommandRegistry';
import packagedStandardProfile from
  '../../../assets/cdeadmin/profiles/cdeadmin-standard.json';
import {
  CDEADMIN_MENU_STRUCTURE,
  menuStructureRegistry,
} from '../commands/MenuStructure';
import {
  exportCustomizationProfile,
  importCustomizationProfile,
  normalizeAppearanceCustomizations,
  readCustomizationDraft,
  saveCustomizationDraft,
} from './profile';

const Root = styled(Box)(({theme}) => ({
  height: '100%',
  minHeight: 0,
  display: 'grid',
  gridTemplateRows: 'auto 1fr auto',
  background: theme.palette.background.default,
  '& .InterfaceDesigner-header, & .InterfaceDesigner-footer': {
    display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8,
    padding: 8, borderBottom: `1px solid ${theme.otherVars.borderColor}`,
  },
  '& .InterfaceDesigner-footer': {
    borderBottom: 0, borderTop: `1px solid ${theme.otherVars.borderColor}`,
  },
  '& .InterfaceDesigner-body': {
    minHeight: 0, display: 'grid', gridTemplateColumns: '190px minmax(0, 1fr)',
  },
  '& .InterfaceDesigner-nav': {
    padding: 8, borderRight: `1px solid ${theme.otherVars.borderColor}`,
    display: 'flex', flexDirection: 'column', gap: 6,
  },
  '& .InterfaceDesigner-page': {minHeight: 0, overflow: 'auto', padding: 12},
  '& .InterfaceDesigner-grid': {
    display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
    gap: 12,
  },
  '& .InterfaceDesigner-masterDetail': {
    height: '100%', minHeight: 420, display: 'grid',
    gridTemplateColumns: 'minmax(220px, 32%) minmax(320px, 1fr)', gap: 12,
  },
  '& .InterfaceDesigner-list': {
    minHeight: 0, overflow: 'auto', border: `1px solid ${theme.otherVars.borderColor}`,
  },
  '& .InterfaceDesigner-listButton': {
    width: '100%', border: 0, borderBottom: `1px solid ${theme.otherVars.borderColor}`,
    background: 'transparent', color: 'inherit', textAlign: 'left', padding: 8,
    cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8,
    '&[aria-selected="true"]': {background: theme.palette.primary.light},
  },
  '& .InterfaceDesigner-detail': {
    minWidth: 0, overflow: 'auto', border: `1px solid ${theme.otherVars.borderColor}`,
    padding: 12,
  },
  '& .InterfaceDesigner-preview': {
    display: 'flex', alignItems: 'center', gap: 16, minHeight: 80,
    fontSize: 44,
  },
  '& h2, & h3': {marginTop: 0},
  '@media (max-width: 720px)': {
    '& .InterfaceDesigner-body': {gridTemplateColumns: '1fr'},
    '& .InterfaceDesigner-nav': {
      borderRight: 0, borderBottom: `1px solid ${theme.otherVars.borderColor}`,
      flexDirection: 'row', overflowX: 'auto',
    },
    '& .InterfaceDesigner-masterDetail': {gridTemplateColumns: '1fr'},
  },
}));

const APPEARANCE_FIELDS = Object.freeze([
  ['accessibility_ui_scale', gettext('Interface scale (%)'), 75, 300],
  ['accessibility_icon_scale', gettext('Icon scale (%)'), 75, 300],
  ['accessibility_line_height', gettext('Text line height'), 1, 2.5],
  ['accessibility_letter_spacing', gettext('Letter spacing (em)'), 0, 0.2],
  ['accessibility_control_height', gettext('Control height (pixels)'), 24, 72],
  ['accessibility_target_size', gettext('Activation target (pixels)'), 24, 72],
  ['accessibility_target_spacing', gettext('Target spacing (pixels)'), 0, 24],
  ['accessibility_scrollbar_size', gettext('Scrollbar size (pixels)'), 8, 48],
  ['accessibility_resize_handle_size', gettext('Resize handle (pixels)'), 5, 32],
  ['accessibility_panel_gap', gettext('Panel spacing (pixels)'), 0, 32],
  ['accessibility_tree_row_height', gettext('Tree row height (pixels)'), 24, 72],
  ['accessibility_tree_indent', gettext('Tree branch indent (pixels)'), 12, 48],
  ['accessibility_tree_expander_size', gettext('Tree expander size (pixels)'), 12, 40],
  ['accessibility_tree_guide_width', gettext('Tree branch line width'), 1, 4],
  ['accessibility_grid_row_height', gettext('Grid row height (pixels)'), 24, 96],
  ['accessibility_grid_header_height', gettext('Grid header height (pixels)'), 24, 120],
  ['accessibility_grid_cell_padding', gettext('Grid cell padding (pixels)'), 2, 32],
  ['accessibility_tab_height', gettext('Workspace tab height (pixels)'), 24, 96],
  ['accessibility_toolbar_height', gettext('Toolbar height (pixels)'), 24, 96],
  ['accessibility_menu_row_height', gettext('Menu row height (pixels)'), 24, 96],
  ['accessibility_status_height', gettext('Status bar height (pixels)'), 20, 72],
  ['accessibility_corner_radius', gettext('Interface corner radius (pixels)'), 0, 24],
  ['accessibility_active_tab_scale', gettext('Selected activity scale (%)'), 100, 150],
  ['accessibility_inactive_brightness', gettext('Inactive activity brightness (%)'), 30, 100],
  ['accessibility_focus_width', gettext('Focus indicator width'), 2, 8],
  ['accessibility_focus_offset', gettext('Focus indicator offset'), 0, 6],
]);

const COLOR_FIELDS = Object.freeze([
  ['accessibility_color_canvas', gettext('Canvas')],
  ['accessibility_color_panel', gettext('Panels')],
  ['accessibility_color_text', gettext('Primary text')],
  ['accessibility_color_muted_text', gettext('Secondary text')],
  ['accessibility_color_primary', gettext('Primary action')],
  ['accessibility_color_focus', gettext('Keyboard focus')],
  ['accessibility_color_border', gettext('Borders')],
  ['accessibility_color_selection', gettext('Selection')],
  ['accessibility_color_success', gettext('Success')],
  ['accessibility_color_warning', gettext('Warning')],
  ['accessibility_color_error', gettext('Error')],
]);

const PROFILE_OPTIONS = [
  ['cdeadmin_standard', gettext('CDEadmin standard')],
  ['classic', gettext('Classic compatibility')],
  ['comfortable', gettext('Comfortable')],
  ['system_adaptive', gettext('System adaptive')],
  ['high_contrast_light', gettext('High contrast light')],
  ['high_contrast_dark', gettext('High contrast dark')],
  ['low_vision', gettext('Low vision')],
  ['motor_assistance', gettext('Motor assistance')],
  ['reduced_motion', gettext('Reduced motion')],
  ['compact_expert', gettext('Compact expert')],
].map(([value, label])=>({value, label}));

const DENSITY_OPTIONS = [
  ['profile', gettext('Use profile')], ['compact', gettext('Compact')],
  ['standard', gettext('Standard')], ['comfortable', gettext('Comfortable')],
  ['touch', gettext('Touch')],
].map(([value, label])=>({value, label}));
const MOTION_OPTIONS = [
  ['profile', gettext('Use profile')], ['system', gettext('Use system setting')],
  ['reduce', gettext('Reduce motion')], ['full', gettext('Full motion')],
].map(([value, label])=>({value, label}));

function currentAppearance(store) {
  return store.getPreferencesForModule('misc');
}

function preferenceUpdate(store, name, value) {
  const preference = store.getPreferences('misc', name);
  if(!preference) throw new Error(`Appearance preference is unavailable: ${name}`);
  return {category_id: preference.cid, id: preference.id, mid: preference.mid,
    name: preference.name, value};
}

function AppearancePage({value, onChange}) {
  const update = (name, next) => onChange({...value, [name]: next});
  return <Box component="section" aria-labelledby="appearance-heading">
    <h2 id="appearance-heading">{gettext('Appearance and accessibility')}</h2>
    <p>{gettext('Values are validated by the presentation authority. Use -1 or leave a colour blank to inherit the selected profile.')}</p>
    <div className="InterfaceDesigner-grid">
      <Select label={gettext('Presentation profile')} value={value.accessibility_profile ||
        'cdeadmin_standard'} options={PROFILE_OPTIONS}
      onChange={(next)=>update('accessibility_profile', next)} />
      <Select label={gettext('Information density')} value={value.accessibility_density ||
        'profile'} options={DENSITY_OPTIONS}
      onChange={(next)=>update('accessibility_density', next)} />
      <Select label={gettext('Motion')} value={value.accessibility_motion || 'profile'}
        options={MOTION_OPTIONS}
        onChange={(next)=>update('accessibility_motion', next)} />
      <TextField label={gettext('Interface font family')}
        value={value.accessibility_ui_font_family || ''}
        onChange={(event)=>update('accessibility_ui_font_family', event.target.value)} />
      <TextField label={gettext('Editor and data font family')}
        value={value.accessibility_monospace_font_family || ''}
        onChange={(event)=>update('accessibility_monospace_font_family', event.target.value)} />
      {APPEARANCE_FIELDS.map(([name, label, min, max]) => <NumberField key={name}
        label={label} value={value[name] ?? -1} inputProps={{min: -1, max, step:
          Number.isInteger(min) && Number.isInteger(max) ? 1 : 0.01}}
        helperText={gettext('-1 inherits; supported range %s–%s', min, max)}
        onChange={(event)=>update(name, Number(event.target.value))} />)}
    </div>
    <h3>{gettext('Colours')}</h3>
    <div className="InterfaceDesigner-grid">
      {COLOR_FIELDS.map(([name, label]) => <TextField key={name} label={label}
        value={value[name] || ''} placeholder="#RRGGBB"
        onChange={(event)=>update(name, event.target.value)} />)}
    </div>
  </Box>;
}

AppearancePage.propTypes = {value: PropTypes.object, onChange: PropTypes.func};

function IconPage({value, onChange, definitions}) {
  const [query, setQuery] = useState('');
  const filtered = definitions.filter((item)=>`${item.key} ${item.label}`
    .toLowerCase().includes(query.toLowerCase()));
  const [selected, setSelected] = useState(definitions[0]?.key || '');
  const source = definitions.find((item)=>item.key === selected);
  const target = value[selected] || '';
  const options = [{value: '', label: gettext('Use packaged default')}, ...definitions.map(
    (item)=>({value: item.key, label: `${item.label} — ${item.key}`}))];
  const assign = (next) => {
    const updated = {...value};
    if(next && next !== selected) updated[selected] = next;
    else delete updated[selected];
    onChange(updated);
  };
  return <Box component="section" aria-labelledby="icons-heading" sx={{height: '100%'}}>
    <h2 id="icons-heading">{gettext('Artwork and icon assignments')}</h2>
    <div className="InterfaceDesigner-masterDetail">
      <div className="InterfaceDesigner-list">
        <TextField fullWidth label={gettext('Find semantic icon')} value={query}
          onChange={(event)=>setQuery(event.target.value)} />
        {filtered.map((item)=><button type="button" key={item.key}
          className="InterfaceDesigner-listButton" aria-selected={item.key === selected}
          onClick={()=>setSelected(item.key)}>
          <Icon iconKey={item.key} decorative /><span>{item.label}<br />
            <small>{item.key}</small></span>
        </button>)}
      </div>
      <div className="InterfaceDesigner-detail">
        {source ? <>
          <h3>{source.label}</h3>
          <p><code>{source.key}</code></p>
          <div className="InterfaceDesigner-preview"
            aria-label={gettext('Icon preview')}>
            <Icon iconKey={source.key}
              label={gettext('%s packaged default', source.label)} />
            <span aria-hidden="true">→</span>
            <Icon iconKey={target || source.key}
              label={gettext('%s assigned icon', source.label)} />
          </div>
          <Select fullWidth label={gettext('Assigned catalog artwork')} value={target}
            options={options} onChange={assign} />
          <p>{gettext('Assignments use catalog identities, not paths or SVG markup. Resetting this assignment restores the packaged and attributed default.')}</p>
        </> : <p>{gettext('No matching icon.')}</p>}
      </div>
    </div>
  </Box>;
}

IconPage.propTypes = {
  value: PropTypes.object, onChange: PropTypes.func, definitions: PropTypes.array,
};

function MenuPage({menus, commands, onMenusChange, onCommandsChange,
  definitions}) {
  const resolvedMenus = menuStructureRegistry.resolve(menus);
  const allNames = [...new Set([
    ...CDEADMIN_MENU_STRUCTURE.map((item)=>item.name), ...Object.keys(menus),
  ])];
  const [selectedMenu, setSelectedMenu] = useState(allNames[0] || 'file');
  const [selectedCommand, setSelectedCommand] = useState('');
  const [newMenu, setNewMenu] = useState({name: '', label: ''});
  const base = CDEADMIN_MENU_STRUCTURE.find((item)=>item.name === selectedMenu);
  const menu = menus[selectedMenu] ?? {name: selectedMenu,
    label: base?.label ?? selectedMenu, visible: true, index: base?.index ?? 100,
    iconKey: '', presentation: {}};
  const updateMenu = (patch) => onMenusChange({...menus,
    [selectedMenu]: {...menu, ...patch}});
  const commandList = commandRegistry.list().sort((left, right)=>
    left.label.localeCompare(right.label));
  const command = commandList.find((item)=>item.id === selectedCommand) ?? commandList[0];
  const commandValue = commands[command?.id] ?? {};
  const updateCommand = (patch) => onCommandsChange({...commands,
    [command.id]: {...commandValue, ...patch}});
  const iconOptions = [{value: '', label: gettext('Use packaged default')}, ...definitions.map(
    (item)=>({value: item.key, label: `${item.label} — ${item.key}`}))];
  const surfaceOptions = [{value: '', label: gettext('Use command default')},
    ...resolvedMenus.map((item)=>({value: item.name, label: item.label}))];
  const addMenu = () => {
    const name = newMenu.name.trim().toLowerCase();
    if(!/^[a-z][a-z0-9_-]*$/.test(name)) return;
    onMenusChange({...menus, [name]: {name, label: newMenu.label.trim() || name,
      visible: true, index: 100, iconKey: '', presentation: {}}});
    setSelectedMenu(name); setNewMenu({name: '', label: ''});
  };
  const removeMenu = () => {
    if(base) return;
    const updated = {...menus};
    delete updated[selectedMenu];
    onMenusChange(updated);
    setSelectedMenu(CDEADMIN_MENU_STRUCTURE[0].name);
  };
  return <Box component="section" aria-labelledby="menus-heading">
    <h2 id="menus-heading">{gettext('Menus and commands')}</h2>
    <h3>{gettext('Top-level menus')}</h3>
    <div className="InterfaceDesigner-masterDetail">
      <div className="InterfaceDesigner-list">
        {allNames.map((name)=><button type="button" key={name}
          className="InterfaceDesigner-listButton" aria-selected={name === selectedMenu}
          onClick={()=>setSelectedMenu(name)}>{menus[name]?.label ??
            CDEADMIN_MENU_STRUCTURE.find((item)=>item.name === name)?.label ?? name}</button>)}
      </div>
      <div className="InterfaceDesigner-detail InterfaceDesigner-grid">
        <TextField label={gettext('Menu label')} value={menu.label}
          onChange={(event)=>updateMenu({label: event.target.value})} />
        <NumberField label={gettext('Order')} value={menu.index}
          onChange={(event)=>updateMenu({index: Number(event.target.value)})} />
        <Select label={gettext('Menu icon')} value={menu.iconKey || ''}
          options={iconOptions}
          onChange={(iconKey)=>updateMenu({iconKey})} />
        <Checkbox label={gettext('Show menu')} checked={menu.visible !== false}
          onChange={(visible)=>updateMenu({visible})} />
        {!base && <Button intent="destructive" onClick={removeMenu}>
          {gettext('Remove custom menu')}
        </Button>}
        <TextField label={gettext('Menu font family')}
          value={menu.presentation?.fontFamily || ''}
          onChange={(event)=>updateMenu({presentation: {...menu.presentation,
            fontFamily: event.target.value}})} />
        <NumberField label={gettext('Menu font size')}
          value={menu.presentation?.fontSize ?? -1}
          helperText={gettext('-1 inherits the active profile')}
          inputProps={{min: -1, max: 32}}
          onChange={(event)=>updateMenu({presentation: {...menu.presentation,
            fontSize: Number(event.target.value)}})} />
        <NumberField label={gettext('Menu font weight')}
          value={menu.presentation?.fontWeight ?? -1}
          helperText={gettext('-1 inherits the active profile')}
          inputProps={{min: -1, max: 900, step: 100}}
          onChange={(event)=>updateMenu({presentation: {...menu.presentation,
            fontWeight: Number(event.target.value)}})} />
        <Select label={gettext('Menu icon position')}
          value={menu.presentation?.iconPosition || 'before'}
          options={[{value: 'before', label: gettext('Before label')},
            {value: 'after', label: gettext('After label')},
            {value: 'hidden', label: gettext('Hide icon')}]}
          onChange={(iconPosition)=>updateMenu({presentation: {
            ...menu.presentation, iconPosition}})} />
        <TextField label={gettext('Text colour')}
          value={menu.presentation?.color || ''}
          placeholder="#RRGGBB" onChange={(event)=>updateMenu({presentation: {
            ...menu.presentation, color: event.target.value}})} />
        <TextField label={gettext('Background colour')}
          value={menu.presentation?.backgroundColor || ''} placeholder="#RRGGBB"
          onChange={(event)=>updateMenu({presentation: {...menu.presentation,
            backgroundColor: event.target.value}})} />
      </div>
    </div>
    <Box sx={{display: 'flex', gap: 1, my: 1, flexWrap: 'wrap'}}>
      <TextField label={gettext('New menu ID')} value={newMenu.name}
        onChange={(event)=>setNewMenu({...newMenu, name: event.target.value})} />
      <TextField label={gettext('New menu label')} value={newMenu.label}
        onChange={(event)=>setNewMenu({...newMenu, label: event.target.value})} />
      <Button onClick={addMenu}>{gettext('Add menu')}</Button>
    </Box>
    <h3>{gettext('Command placement and presentation')}</h3>
    {command && <div className="InterfaceDesigner-masterDetail">
      <div className="InterfaceDesigner-list">
        {commandList.map((item)=><button type="button" key={item.id}
          className="InterfaceDesigner-listButton"
          aria-selected={item.id === (selectedCommand || command.id)}
          onClick={()=>setSelectedCommand(item.id)}>{item.label}<br />
          <small>{item.id}</small></button>)}
      </div>
      <div className="InterfaceDesigner-detail InterfaceDesigner-grid">
        <TextField label={gettext('Command label')}
          value={commandValue.label ?? command.label}
          onChange={(event)=>updateCommand({label: event.target.value})} />
        <Select label={gettext('Top-level menu')}
          value={commandValue.surface || ''}
          options={surfaceOptions} onChange={(surface)=>updateCommand({surface})} />
        <Select label={gettext('Command icon')}
          value={commandValue.iconKey || ''}
          options={iconOptions} onChange={(iconKey)=>updateCommand({iconKey})} />
        <NumberField label={gettext('Order within menu')}
          value={commandValue.priority ?? 1000}
          inputProps={{min: 0, max: 10000}}
          onChange={(event)=>updateCommand({priority: Number(event.target.value)})} />
        <TextField label={gettext('Keyboard shortcut')}
          value={commandValue.shortcut || ''}
          onChange={(event)=>updateCommand({shortcut: event.target.value})} />
        <TextField label={gettext('Command font family')}
          value={commandValue.presentation?.fontFamily || ''}
          onChange={(event)=>updateCommand({presentation: {
            ...commandValue.presentation, fontFamily: event.target.value}})} />
        <NumberField label={gettext('Command font size')}
          value={commandValue.presentation?.fontSize ?? -1}
          helperText={gettext('-1 inherits the active profile')}
          inputProps={{min: -1, max: 32}}
          onChange={(event)=>updateCommand({presentation: {
            ...commandValue.presentation, fontSize: Number(event.target.value)}})} />
        <NumberField label={gettext('Command font weight')}
          value={commandValue.presentation?.fontWeight ?? -1}
          helperText={gettext('-1 inherits the active profile')}
          inputProps={{min: -1, max: 900, step: 100}}
          onChange={(event)=>updateCommand({presentation: {
            ...commandValue.presentation, fontWeight: Number(event.target.value)}})} />
        <TextField label={gettext('Command text colour')}
          value={commandValue.presentation?.color || ''} placeholder="#RRGGBB"
          onChange={(event)=>updateCommand({presentation: {
            ...commandValue.presentation, color: event.target.value}})} />
        <TextField label={gettext('Command background colour')}
          value={commandValue.presentation?.backgroundColor || ''}
          placeholder="#RRGGBB"
          onChange={(event)=>updateCommand({presentation: {
            ...commandValue.presentation,
            backgroundColor: event.target.value}})} />
        <Select label={gettext('Command icon position')}
          value={commandValue.presentation?.iconPosition || 'before'}
          options={[{value: 'before', label: gettext('Before label')},
            {value: 'after', label: gettext('After label')},
            {value: 'hidden', label: gettext('Hide icon')}]}
          onChange={(iconPosition)=>updateCommand({presentation: {
            ...commandValue.presentation, iconPosition}})} />
        <Checkbox label={gettext('Show command')}
          checked={commandValue.visible !== false}
          onChange={(visible)=>updateCommand({visible})} />
        <Checkbox label={gettext('Enable command')}
          checked={commandValue.enabled !== false}
          onChange={(enabled)=>updateCommand({enabled})} />
        <p>{gettext('Customization can further restrict visibility or availability. It cannot grant a permission or bypass command and backend authorization.')}</p>
      </div>
    </div>}
  </Box>;
}

MenuPage.propTypes = {
  menus: PropTypes.object, commands: PropTypes.object,
  onMenusChange: PropTypes.func, onCommandsChange: PropTypes.func,
  definitions: PropTypes.array,
};

export default function InterfaceDesigner({panelId: _panelId}) {
  const preferenceStore = usePreferences();
  const [page, setPage] = useState('appearance');
  const [appearance, setAppearance] = useState(()=>currentAppearance(
    usePreferences.getState()));
  const [draft, setDraft] = useState(()=>readCustomizationDraft(
    usePreferences.getState()));
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const definitions = useMemo(()=>[...listIconDefinitions()].sort((left, right)=>
    left.key.localeCompare(right.key)), []);
  useEffect(() => {
    setAppearance(currentAppearance(usePreferences.getState()));
    setDraft(readCustomizationDraft(usePreferences.getState()));
  }, [preferenceStore.version]);
  const save = async () => {
    setBusy(true); setMessage('');
    try {
      const appearanceNames = new Set([
        'accessibility_profile', 'accessibility_density', 'accessibility_motion',
        'accessibility_ui_font_family', 'accessibility_monospace_font_family',
        ...APPEARANCE_FIELDS.map(([name])=>name),
        ...COLOR_FIELDS.map(([name])=>name),
      ]);
      const normalizedAppearance = normalizeAppearanceCustomizations(
        appearance
      );
      await usePreferences.getState().setPreferences([...appearanceNames].map(
        (name)=>preferenceUpdate(usePreferences.getState(), name,
          normalizedAppearance[name] ?? '')
      ));
      await saveCustomizationDraft(usePreferences.getState(), draft);
      setMessage(gettext(
        'Interface profile saved. Open menus and icons update immediately.'
      ));
    } catch(error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  };
  const reset = () => {
    const packaged = importCustomizationProfile(packagedStandardProfile);
    setAppearance({...currentAppearance(usePreferences.getState()),
      ...Object.fromEntries(APPEARANCE_FIELDS.map(([name])=>[name, -1])),
      ...Object.fromEntries(COLOR_FIELDS.map(([name])=>[name, ''])),
      ...packaged.appearance,
    });
    setDraft(packaged.draft);
    setMessage(gettext(
      'Packaged defaults are staged. Choose Save profile to apply them.'
    ));
  };
  const exportProfile = () => {
    const data = JSON.stringify(exportCustomizationProfile(draft, appearance), null, 2);
    const url = URL.createObjectURL(new Blob([data], {type: 'application/json'}));
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = 'scratchrobin-interface-profile.json';
    anchor.click(); URL.revokeObjectURL(url);
  };
  const importProfile = async (event) => {
    try {
      const imported = importCustomizationProfile(await event.target.files?.[0]?.text());
      setDraft(imported.draft); setAppearance({...appearance, ...imported.appearance});
      setMessage(gettext(
        'Profile imported and validated. Choose Save profile to apply it.'
      ));
    } catch(error) {
      setMessage(error.message);
    } finally {
      event.target.value = '';
    }
  };
  const pages = [
    ['appearance', gettext('Appearance')],
    ['icons', gettext('Artwork & icons')],
    ['menus', gettext('Menus & commands')],
  ];
  return <Root data-cdeadmin-qa-key="interface-designer.workspace">
    <header className="InterfaceDesigner-header">
      <Icon iconKey="action.settings" decorative size="1.5rem" />
      <strong>{gettext('Interface Designer')}</strong>
      <span>{gettext('Personalize accessible presentation without changing command authority.')}</span>
    </header>
    <div className="InterfaceDesigner-body">
      <nav className="InterfaceDesigner-nav"
        aria-label={gettext('Interface designer sections')}>
        {pages.map(([id, label])=><Button key={id} intent={page === id ?
          'primary' : 'neutral'} aria-current={page === id ? 'page' : undefined}
        onClick={()=>setPage(id)}>{label}</Button>)}
      </nav>
      <main className="InterfaceDesigner-page">
        {page === 'appearance' && <AppearancePage value={appearance}
          onChange={setAppearance} />}
        {page === 'icons' && <IconPage value={draft.icons}
          onChange={(icons)=>setDraft({...draft, icons})} definitions={definitions} />}
        {page === 'menus' && <MenuPage menus={draft.menus} commands={draft.commands}
          onMenusChange={(menus)=>setDraft({...draft, menus})}
          onCommandsChange={(commands)=>setDraft({...draft, commands})}
          definitions={definitions} />}
      </main>
    </div>
    <footer className="InterfaceDesigner-footer">
      <Button onClick={reset}>{gettext('Restore packaged defaults')}</Button>
      <Button onClick={exportProfile}>{gettext('Export profile')}</Button>
      <Button component="label">{gettext('Import profile')}<input type="file" hidden
        accept="application/json,.json" onChange={importProfile} /></Button>
      <Box role={message && /unavailable|not |invalid|failed/i.test(message) ?
        'alert' : 'status'} sx={{flex: 1}}>{message}</Box>
      <Button intent="primary" loading={busy} onClick={save}>
        {gettext('Save profile')}
      </Button>
    </footer>
  </Root>;
}

InterfaceDesigner.propTypes = {panelId: PropTypes.string};
