/////////////////////////////////////////////////////////////
// ScratchRobin CDE Administrator — safe browser gate targeting
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
/////////////////////////////////////////////////////////////
import {readFileSync} from 'fs';
import {resolve} from 'path';

const source = readFileSync(resolve(__dirname,
  '../../../../tools/cdeadmin_ui_evidence.py'), 'utf8');
const script = source.match(/(const supplied = arguments\[0\];[\s\S]*?)\n {12}"""/)[1];
// Execute the actual Selenium JavaScript against DOM nodes. A queued click
// deliberately does not update aria-selected before the contextmenu event.
const openContext = new Function(script);

describe('browser evidence context target selection', () => {
  afterEach(() => { document.body.replaceChildren(); });

  function row(selected) {
    const node = document.createElement('div');
    node.className = 'file-entry';
    node.setAttribute('aria-selected', String(selected));
    const label = document.createElement('span');
    node.appendChild(label);
    document.body.appendChild(node);
    const context = jest.fn();
    node.addEventListener('contextmenu', context);
    return {node, label, context};
  }

  it.each(['row', 'label'])('uses the requested %s before stale selection', (kind) => {
    const previous = row(true), requested = row(false);
    const supplied = kind === 'row' ? requested.node : requested.label;
    const click = jest.fn();
    supplied.addEventListener('click', click);
    openContext(supplied);
    expect(click).toHaveBeenCalledTimes(1);
    expect(requested.context).toHaveBeenCalledTimes(1);
    expect(requested.context.mock.calls[0][0].target).toBe(requested.node);
    expect(previous.context).not.toHaveBeenCalled();
    expect(previous.node.getAttribute('aria-selected')).toBe('true');
  });

  it('uses current selection only when no explicit row was supplied', () => {
    const selected = row(true);
    openContext(null);
    expect(selected.context).toHaveBeenCalledTimes(1);
  });

  it('rejects an absent target without dispatching a document event', () => {
    expect(() => openContext(null)).toThrow('selected tree row is unavailable');
  });
});
