/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import { act, render } from '@testing-library/react';

import {
  availableDialogViewport,
  AlertContent,
  modalAccessibilityAttributes,
  modalTitleId,
  viewportDialogGeometry,
} from '../../pgadmin/static/js/helpers/ModalProvider';
import { withTheme } from './fake_theme';

const ThemedAlertContent = withTheme(AlertContent);


// MUI <Button> triggers an async TouchRipple update on mount that
// otherwise leaks past the test boundary and trips
// `console.error` (setup-jest treats any console.error as a failure).
// Wrap render in act() and flush microtasks so the ripple effect settles.
async function renderAlert(props) {
  let ctrl;
  await act(async () => {
    ctrl = render(<ThemedAlertContent {...props} />);
  });
  return ctrl;
}


describe('ModalProvider AlertContent', () => {
  it('renders plain-text body when plainText is true (no HTML parsing)',
    async () => {
      const xss = '<iframe srcdoc="&lt;script&gt;alert(1)&lt;/script&gt;">';
      const ctrl = await renderAlert({
        text: 'Failed:\n' + xss, plainText: true, onOkClick: () => {}});
      expect(ctrl.container.querySelector('iframe')).toBeNull();
      expect(ctrl.container.querySelector('script')).toBeNull();
      expect(ctrl.container.textContent).toContain('iframe');
    });

  it('preserves newlines in plainText mode (no <br/> substitution needed)',
    async () => {
      const body = 'line one\nline two\nline three';
      const ctrl = await renderAlert({
        text: body, plainText: true, onOkClick: () => {}});
      const safeSpan = ctrl.container.querySelector('span');
      expect(safeSpan).not.toBeNull();
      expect(safeSpan.textContent).toBe(body);
      expect(safeSpan.style.whiteSpace).toBe('pre-wrap');
    });

  it('renders HTML body when plainText is false (default, sanitized)',
    async () => {
      const ctrl = await renderAlert({
        text: 'Welcome <b>admin</b>', onOkClick: () => {}});
      expect(ctrl.container.querySelector('b')).not.toBeNull();
      expect(ctrl.container.querySelector('script')).toBeNull();
    });

  it('strips script tag even in default HTML mode', async () => {
    const ctrl = await renderAlert({
      text: '<script>window.__xss=true</script>x', onOkClick: () => {}});
    expect(ctrl.container.querySelector('script')).toBeNull();
    expect(window.__xss).toBeUndefined();
  });
});

describe('viewportDialogGeometry', () => {
  it('subtracts application chrome from an embedded modal viewport', () => {
    const viewport = availableDialogViewport({
      windowWidth: 800, windowHeight: 757, applicationTop: 40,
      containerBounds: {left: 0, width: 800, height: 757},
    });
    const geometry = viewportDialogGeometry({
      viewportWidth: viewport.width, viewportHeight: viewport.height,
      width: 1100, height: 720, minWidth: 760, minHeight: 480,
    });
    expect(viewport).toEqual({width: 800, height: 717});
    expect(40 + geometry.y + geometry.height).toBeLessThanOrEqual(757);
  });

  it('retains the requested provider workspace size on a wide viewport', () => {
    expect(viewportDialogGeometry({
      viewportWidth: 1600, viewportHeight: 1000,
      width: 1100, height: 720, minWidth: 760, minHeight: 480,
    })).toEqual(expect.objectContaining({
      width: 1100, height: 720, minWidth: 760, minHeight: 480, x: 250,
    }));
  });

  it('bounds width and minimum width inside a narrow viewport', () => {
    const geometry = viewportDialogGeometry({
      viewportWidth: 800, viewportHeight: 900,
      width: 1100, height: 720, minWidth: 760, minHeight: 480,
    });
    expect(geometry.width).toBe(768);
    expect(geometry.minWidth).toBe(760);
    expect(geometry.x).toBe(16);
    expect(geometry.x + geometry.width).toBeLessThanOrEqual(800);
  });

  it('bounds height and minimum height inside a short viewport', () => {
    const geometry = viewportDialogGeometry({
      viewportWidth: 1000, viewportHeight: 420,
      width: 800, height: 720, minWidth: 500, minHeight: 480,
    });
    expect(geometry.height).toBeCloseTo(403.2);
    expect(geometry.minHeight).toBeCloseTo(403.2);
    expect(geometry.y + geometry.height).toBeLessThanOrEqual(420);
  });
});

describe('modalTitleId', () => {
  it('creates a stable safe target for the dialog accessible name', () => {
    expect(modalTitleId('provider,dialog 42')).toBe(
      'cdeadmin-modal-title-provider-dialog-42');
  });


  it('uses an explicit accessible name for a textual dialog title', () => {
    expect(modalAccessibilityAttributes(
      'provider-workspace', 'Logical backup', true,
    )).toEqual({
      'aria-label': 'Logical backup',
      'aria-labelledby': undefined,
    });
  });


  it('links a non-text title to its visible title element', () => {
    expect(modalAccessibilityAttributes(
      'provider-workspace', <span>Provider task</span>, true,
    )).toEqual({
      'aria-label': undefined,
      'aria-labelledby': 'cdeadmin-modal-title-provider-workspace',
    });
  });
});
