/////////////////////////////////////////////////////////////
// Application-wide visual QA identity regression tests.
/////////////////////////////////////////////////////////////

import {fireEvent} from '@testing-library/react';
import {QA_VISUAL_ID_ATTRIBUTE, QA_VISUAL_MODE_STORAGE_KEY,
  QAVisualIdentityController, readQAVisualMode, requestQAVisualMode,
  writeQAVisualMode} from 'sources/cdeadmin_ui/qa';

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('QA visual identity authority', () => {
  let controller;

  beforeEach(() => {
    window.localStorage.clear(); document.body.innerHTML = '';
  });

  afterEach(() => {
    controller?.stop(); controller = null;
    document.body.innerHTML = ''; window.localStorage.clear();
  });

  test('persists mode without letting unavailable storage block login', () => {
    expect(readQAVisualMode(window.localStorage)).toBe(false);
    expect(writeQAVisualMode(window.localStorage, true)).toBe(true);
    expect(window.localStorage.getItem(QA_VISUAL_MODE_STORAGE_KEY)).toBe('true');
    expect(readQAVisualMode(window.localStorage)).toBe(true);
    const blocked = {getItem: () => { throw new Error('blocked'); },
      setItem: () => { throw new Error('blocked'); }};
    expect(readQAVisualMode(blocked)).toBe(false);
    expect(writeQAVisualMode(blocked, true)).toBe(true);
  });

  test('assigns unique deterministic IDs to every DOM and SVG visual element',
    () => {
      document.body.innerHTML = '<main data-cdeadmin-qa-key="workspace">' +
        '<button name="run">Run</button><button name="run">Run again</button>' +
        '<svg><path d="M0 0"></path></svg><script></script></main>';
      window.localStorage.setItem(QA_VISUAL_MODE_STORAGE_KEY, 'true');
      controller = new QAVisualIdentityController().start();
      const visuals = [...document.body.querySelectorAll(
        'body, body *, svg, path')].filter((element) =>
        element.tagName !== 'SCRIPT');
      const identities = visuals.map((element) =>
        element.getAttribute(QA_VISUAL_ID_ATTRIBUTE));
      expect(identities.every(Boolean)).toBe(true);
      expect(new Set(identities).size).toBe(identities.length);
      expect(document.querySelector('main').getAttribute(
        QA_VISUAL_ID_ATTRIBUTE)).toContain('main.workspace');
      expect(document.querySelector('script')).not.toHaveAttribute(
        QA_VISUAL_ID_ATTRIBUTE);
    });

  test('covers dynamic content and reuses its structural identity after removal',
    async () => {
      document.body.innerHTML = '<main data-cdeadmin-qa-key="workspace"></main>';
      window.localStorage.setItem(QA_VISUAL_MODE_STORAGE_KEY, 'true');
      controller = new QAVisualIdentityController().start();
      const main = document.querySelector('main');
      const first = document.createElement('button'); first.name = 'refresh';
      main.appendChild(first); await tick();
      const identity = first.getAttribute(QA_VISUAL_ID_ATTRIBUTE);
      expect(identity).toBeTruthy();
      first.remove(); await tick();
      const replacement = document.createElement('button');
      replacement.name = 'refresh'; main.appendChild(replacement); await tick();
      expect(replacement.getAttribute(QA_VISUAL_ID_ATTRIBUTE)).toBe(identity);
    });

  test('covers open shadow roots and same-origin iframe documents', async () => {
    const host = document.createElement('section');
    host.setAttribute('data-cdeadmin-qa-key', 'designer');
    const shadow = host.attachShadow({mode: 'open'});
    const shadowButton = document.createElement('button');
    shadowButton.textContent = 'Draw'; shadow.appendChild(shadowButton);
    const frame = document.createElement('iframe'); document.body.append(host, frame);
    frame.contentDocument.body.innerHTML = '<button>Frame action</button>';
    window.localStorage.setItem(QA_VISUAL_MODE_STORAGE_KEY, 'true');
    controller = new QAVisualIdentityController().start(); await tick();
    expect(shadowButton).toHaveAttribute(QA_VISUAL_ID_ATTRIBUTE);
    expect(frame).toHaveAttribute(QA_VISUAL_ID_ATTRIBUTE);
    expect(frame.contentDocument.querySelector('button')).toHaveAttribute(
      QA_VISUAL_ID_ATTRIBUTE);
    expect(shadowButton.getAttribute(QA_VISUAL_ID_ATTRIBUTE)).not.toBe(
      frame.contentDocument.querySelector('button').getAttribute(
        QA_VISUAL_ID_ATTRIBUTE));
  });

  test('shows the identifier on pointer hover and keyboard focus without changing names',
    () => {
      document.body.innerHTML = '<button aria-label="Private object name">Open</button>';
      window.localStorage.setItem(QA_VISUAL_MODE_STORAGE_KEY, 'true');
      controller = new QAVisualIdentityController().start();
      const button = document.querySelector('button');
      fireEvent.pointerOver(button, {clientX: 20, clientY: 30});
      const overlay = document.querySelector('[data-cdeadmin-qa-overlay]');
      expect(overlay).toHaveTextContent(button.getAttribute(
        QA_VISUAL_ID_ATTRIBUTE));
      expect(overlay.style.display).toBe('block');
      expect(overlay).toHaveAttribute('aria-hidden', 'true');
      expect(button.getAttribute(QA_VISUAL_ID_ATTRIBUTE)).not.toContain(
        'private-object-name');
      fireEvent.pointerOut(button); expect(overlay.style.display).toBe('none');
      fireEvent.focusIn(button); expect(overlay.style.display).toBe('block');
    });

  test('reacts to the global switch and removes all instrumentation when disabled',
    () => {
      document.body.innerHTML = '<main><button>Run</button></main>';
      controller = new QAVisualIdentityController().start();
      expect(document.querySelector('button')).not.toHaveAttribute(
        QA_VISUAL_ID_ATTRIBUTE);
      requestQAVisualMode(true);
      expect(document.querySelector('button')).toHaveAttribute(
        QA_VISUAL_ID_ATTRIBUTE);
      expect(document.documentElement.dataset.cdeadminQaVisualIds).toBe('true');
      requestQAVisualMode(false);
      expect(document.querySelector('button')).not.toHaveAttribute(
        QA_VISUAL_ID_ATTRIBUTE);
      expect(document.querySelector('[data-cdeadmin-qa-overlay]')).toBeNull();
      expect(document.documentElement.dataset.cdeadminQaVisualIds).toBeUndefined();
    });
});
