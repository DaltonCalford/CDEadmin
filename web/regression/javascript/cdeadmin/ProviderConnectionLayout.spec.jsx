/////////////////////////////////////////////////////////////
// ScratchRobin CDE Administrator — readable connection controls
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
/////////////////////////////////////////////////////////////

import {render, screen} from '@testing-library/react';
import {Box} from '@mui/material';
import {VisualAdminField} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import {providerConnectionFieldGridSx} from
  'sources/cdeadmin_ui/foundations/providerConnectionLayout';

jest.mock('../../../pgadmin/static/js/api_instance');

describe('font-relative provider connection fields', () => {
  it('uses root-font units and caps minimum width to the available task width', () => {
    expect(providerConnectionFieldGridSx.gridTemplateColumns).toBe(
      'repeat(auto-fit, minmax(min(100%, 17.5rem), 1fr))');
    expect(providerConnectionFieldGridSx.minWidth).toBe(0);
  });

  it.each(['Use server preference', 'READ_COMMITTED_READ_CONSISTENCY',
    'Very long provider-native option with explanatory text'])(
    'emits a scoped wrapping rule matching the selected value %s', (label) => {
      render(<Box sx={providerConnectionFieldGridSx}>
        <VisualAdminField field={{field_id: 'mode', label: 'Native preference',
          control: 'select', options: [{value: 'native', label}]}}
        value="native" onChange={jest.fn()} />
      </Box>);
      const control = screen.getByRole('combobox', {name: 'Native preference'});
      expect(control).toHaveTextContent(label);
      // jsdom's cascade does not implement selector specificity. Assert the
      // actual emitted, matching rule here; the live-browser gate asserts
      // computed values and scroll dimensions at 100%, 200%, and 300%.
      const rules = [...document.styleSheets].flatMap(sheet => [...sheet.cssRules]);
      const rule = rules.find(item => item.selectorText &&
        control.matches(item.selectorText) &&
        item.style.getPropertyValue('white-space') === 'normal');
      expect(rule).toBeDefined();
      expect(rule.selectorText).toMatch(/\.[\w-]+\.[\w-]+ \.MuiSelect-select$/);
      expect(rule.style.getPropertyValue('overflow-wrap')).toBe('anywhere');
      expect(rule.style.getPropertyValue('text-overflow')).toBe('clip');
      expect(rule.style.getPropertyValue('height')).toBe('auto');
    });
});
