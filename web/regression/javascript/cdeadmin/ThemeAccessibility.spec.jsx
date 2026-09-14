/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {useTheme} from '@mui/material/styles';
import {MenuItem, TextField} from '@mui/material';
import Theme from 'sources/Theme';
import DataGrid from 'sources/cdeadmin_ui/data/DataGrid';
import usePreferences from '../../../pgadmin/preferences/static/js/store';

function ThemeProbe() {
  const theme = useTheme();
  const presentation = theme.cdeadminPresentation;
  return <output data-testid="theme-probe">
    {JSON.stringify({
      profileId: presentation.profileId,
      scale: presentation.scale,
      targetSize: presentation.targetSize,
      reduceMotion: presentation.reduceMotion,
      canvas: presentation.colors.canvas,
    })}
  </output>;
}

function setMiscPreferences(values) {
  usePreferences.setState({
    data: Object.entries(values).map(([name, value], id) => ({
      id, module: 'misc', name, value,
    })),
    version: Date.now(),
    isLoading: false,
    failed: false,
  });
}

describe('CDEadmin accessibility Theme integration', () => {
  beforeEach(() => {
    window.localStorage.clear();
    setMiscPreferences({theme: 'light', accessibility_profile: 'classic'});
  });

  it.each(['', 'populated'])('keeps compact labels above fields for %s values', (value) => {
    render(<Theme><>
      <TextField label="Role name" defaultValue={value} />
      <TextField label="Comment" multiline defaultValue={value} />
      <TextField label="Privileges" select defaultValue={value}>
        <MenuItem value="">None</MenuItem>
        <MenuItem value="populated">Monitor</MenuItem>
      </TextField>
      <TextField label="Limit" type="number" />
      <TextField label="Secret" type="password" />
    </></Theme>);
    for (const label of document.querySelectorAll('.MuiInputLabel-root')) {
      expect(label).toHaveAttribute('data-shrink', 'true');
      expect(label).toHaveStyle({fontSize: '1rem'});
      expect(label).toHaveStyle({position: 'relative', transform: 'none', marginBottom: '0.25rem'});
    }
    expect(document.querySelectorAll('.MuiInputLabel-root')).toHaveLength(5);
    expect(screen.getByLabelText('Role name')).toHaveValue(value);
  });

  it('preserves an explicit field label presentation override', () => {
    render(<Theme><TextField label="Explicit" InputLabelProps={{shrink: false}} /></Theme>);
    expect(document.querySelector('.MuiInputLabel-root')).toHaveAttribute('data-shrink', 'false');
  });

  it('applies account preferences to the shared theme', () => {
    setMiscPreferences({
      theme: 'light',
      accessibility_profile: 'motor_assistance',
      accessibility_ui_scale: 125,
      accessibility_color_canvas: '#101820',
      accessibility_color_panel: '#101820',
      accessibility_color_text: '#FFFFFF',
    });
    render(<Theme><ThemeProbe /></Theme>);
    const value = JSON.parse(screen.getByTestId('theme-probe').textContent);

    expect(value).toEqual({
      profileId: 'motor_assistance',
      scale: 125,
      targetSize: 48,
      reduceMotion: true,
      canvas: '#101820',
    });
    expect(document.documentElement.dataset.cdeadminProfile)
      .toBe('motor_assistance');
  });

  it('provides a persistent emergency safe-mode shortcut', async () => {
    setMiscPreferences({
      theme: 'light',
      accessibility_profile: 'compact_expert',
      accessibility_ui_scale: 75,
    });
    render(<Theme><ThemeProbe /></Theme>);

    fireEvent.keyDown(window, {key: '0', code: 'Digit0', ctrlKey: true,
      shiftKey: true});

    await waitFor(() => {
      expect(document.documentElement.dataset.cdeadminSafeMode).toBe('true');
    });
    expect(window.localStorage.getItem('cdeadmin.accessibility.safe_mode'))
      .toBe('true');
    const value = JSON.parse(screen.getByTestId('theme-probe').textContent);
    expect(value.profileId).toBe('low_vision');
    expect(value.scale).toBe(150);
  });

  it('keeps virtualized grid metrics synchronized with the profile', () => {
    setMiscPreferences({
      theme: 'light',
      accessibility_profile: 'low_vision',
    });
    render(<Theme>
      <DataGrid id="profile-grid" columns={[]} rows={[]} headerRowHeight={40} />
    </Theme>);

    const grid = document.querySelector('[data-test="react-data-grid"]');
    expect(grid).toHaveAttribute('data-row-height', '44');
    expect(grid).toHaveAttribute('data-header-row-height', '48');
  });
});
