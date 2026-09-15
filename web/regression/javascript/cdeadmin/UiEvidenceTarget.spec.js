/////////////////////////////////////////////////////////////
// ScratchRobin CDE Administrator — safe browser gate targeting
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
/////////////////////////////////////////////////////////////
import {readFileSync} from 'fs';
import {resolve} from 'path';

const source = readFileSync(resolve(__dirname,
  '../../../../tools/cdeadmin_ui_evidence.py'), 'utf8');
const pointerSource = source.split('def _context_pointer(')[1]
  .split('\ndef invoke_context_action(')[0];
const script = pointerSource.match(/execute_script\('''([\s\S]*?)''', supplied/)[1];
// Execute the actual hit-test script. It must not synthesize clicks or menu
// events: the Python caller uses the returned coordinates for a real pointer.
const pointerLocation = new Function(script);

describe('browser evidence context target selection', () => {
  const originalHitTest = document.elementFromPoint;
  beforeEach(() => { document.elementFromPoint = jest.fn(); });
  afterEach(() => {
    document.body.replaceChildren();
    document.elementFromPoint = originalHitTest;
  });

  function row(selected) {
    const node = document.createElement('div');
    node.className = 'file-entry';
    node.setAttribute('aria-selected', String(selected));
    const label = document.createElement('span');
    node.appendChild(label);
    document.body.appendChild(node);
    node.getBoundingClientRect = () => ({left: 10, top: 20,
      right: 300, bottom: 80});
    const context = jest.fn();
    node.addEventListener('contextmenu', context);
    return {node, label, context};
  }

  it.each(['row', 'label'])('uses the requested %s before stale selection', (kind) => {
    const previous = row(true), requested = row(false);
    const supplied = kind === 'row' ? requested.node : requested.label;
    const click = jest.fn();
    supplied.addEventListener('click', click);
    document.elementFromPoint.mockReturnValue(requested.label);
    expect(pointerLocation(supplied)).toEqual({x: 155, y: 50});
    expect(click).not.toHaveBeenCalled();
    expect(requested.context).not.toHaveBeenCalled();
    expect(previous.context).not.toHaveBeenCalled();
    expect(previous.node.getAttribute('aria-selected')).toBe('true');
  });

  it('uses current selection only when no explicit row was supplied', () => {
    const selected = row(true);
    document.elementFromPoint.mockReturnValue(selected.label);
    expect(pointerLocation(null)).toEqual({x: 155, y: 50});
    expect(selected.context).not.toHaveBeenCalled();
  });

  it('rejects an absent target without dispatching a document event', () => {
    expect(pointerLocation(null)).toBeNull();
    expect(document.elementFromPoint).not.toHaveBeenCalled();
  });

  it('does not fall back from a detached supplied row to stale selection', () => {
    const selected = row(true), detached = row(false);
    detached.node.remove();
    document.elementFromPoint.mockReturnValue(selected.label);
    expect(pointerLocation(detached.label)).toBeNull();
    expect(document.elementFromPoint).not.toHaveBeenCalled();
  });

  it.each([null, 'overlay'])('waits when hit testing finds %s', (kind) => {
    const selected = row(true);
    document.elementFromPoint.mockReturnValue(kind && document.createElement('div'));
    expect(pointerLocation(selected.node)).toBeNull();
    expect(selected.context).not.toHaveBeenCalled();
  });

  it('intersects the row with the viewport', () => {
    const selected = row(true);
    selected.node.getBoundingClientRect = () => ({left: -100, top: -50,
      right: 300, bottom: 60});
    document.elementFromPoint.mockReturnValue(selected.label);
    expect(pointerLocation(selected.node)).toEqual({x: 150, y: 30});
  });

  it.each([
    {left: -100, right: -5, top: 20, bottom: 80},
    {left: 10000, right: 11000, top: 20, bottom: 80},
    {left: 10, right: 300, top: -80, bottom: -1},
    {left: 10, right: 300, top: 10000, bottom: 11000},
    {left: 10, right: 11, top: 20, bottom: 80},
  ])('rejects an offscreen or empty visible intersection %#', (bounds) => {
    const selected = row(true);
    selected.node.getBoundingClientRect = () => bounds;
    expect(pointerLocation(selected.node)).toBeNull();
    expect(document.elementFromPoint).not.toHaveBeenCalled();
  });

  it.each(['hidden', 'auto', 'scroll', 'clip'])(
    'respects %s clipping ancestors and their borders without scrolling', (overflow) => {
      const selected = row(true);
      const parent = document.createElement('div');
      parent.style.overflowX = overflow;
      parent.style.overflowY = overflow;
      parent.getBoundingClientRect = () => ({left: 70, top: 35, right: 200, bottom: 75});
      for (const [name, value] of Object.entries({
        clientLeft: 2, clientTop: 3, clientWidth: 100, clientHeight: 30,
      })) Object.defineProperty(parent, name, {value});
      document.body.appendChild(parent);
      parent.appendChild(selected.node);
      parent.scrollLeft = 60;
      parent.scrollTop = 7;
      document.elementFromPoint.mockReturnValue(selected.label);
      expect(pointerLocation(selected.node)).toEqual({x: 122, y: 53});
      expect(parent.scrollLeft).toBe(60);
      expect(parent.scrollTop).toBe(7);
    });

  it('does not clip an axis declared visible', () => {
    const selected = row(true), parent = document.createElement('div');
    parent.style.overflowX = 'hidden';
    parent.style.overflowY = 'visible';
    parent.getBoundingClientRect = () => ({left: 70, top: 200, right: 200, bottom: 210});
    Object.defineProperty(parent, 'clientWidth', {value: 100});
    document.body.appendChild(parent);
    parent.appendChild(selected.node);
    document.elementFromPoint.mockReturnValue(selected.label);
    expect(pointerLocation(selected.node)).toEqual({x: 120, y: 50});
  });
});
