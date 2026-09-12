/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {CommandRegistry} from 'sources/cdeadmin_ui/commands/CommandRegistry';
import {ShortcutRegistry} from 'sources/cdeadmin_ui/commands/ShortcutRegistry';

function event(key, values={}) {
  return {key, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
    defaultPrevented: false, preventDefault: jest.fn(), ...values};
}

describe('command-owned shortcut registry', () => {
  it('gives the active surface precedence over global bindings', async () => {
    const commands = new CommandRegistry();
    const calls = [];
    commands.register({id: 'global.save', execute: () => calls.push('global')});
    commands.register({id: 'editor.save', execute: () => calls.push('editor')});
    const shortcuts = new ShortcutRegistry(commands);
    shortcuts.register({id: 'global-save', commandId: 'global.save',
      scope: 'global', shortcut: 'Ctrl+S'});
    shortcuts.register({id: 'editor-save', commandId: 'editor.save',
      scope: 'query.editor', shortcut: 'Ctrl+S'});
    const input = event('s', {ctrlKey: true});
    await shortcuts.handle(input, {activeSurfaceId: 'query.editor'});
    expect(calls).toEqual(['editor']);
    expect(input.preventDefault).toHaveBeenCalled();
  });

  it('supports Ctrl/Cmd portability, predicates and typed arguments', async () => {
    const commands = new CommandRegistry();
    const execute = jest.fn();
    commands.register({id: 'surface.run', execute});
    const shortcuts = new ShortcutRegistry(commands);
    shortcuts.register({id: 'run', commandId: 'surface.run', scope: 'global',
      shortcut: 'Ctrl+Cmd+Enter', when: (context) => context.runnable,
      arguments: (context) => ({targetId: context.targetId})});
    expect(await shortcuts.handle(event('Enter', {metaKey: true}), {
      runnable: false,
    })).toBe(false);
    await shortcuts.handle(event('Enter', {metaKey: true}), {
      runnable: true, targetId: 'query-one',
    });
    expect(execute).toHaveBeenCalledWith({targetId: 'query-one'},
      expect.objectContaining({runnable: true}));
  });

  it('rejects conflicts and detaches DOM event ownership cleanly', async () => {
    const commands = new CommandRegistry();
    commands.register({id: 'view.help', execute: jest.fn()});
    const shortcuts = new ShortcutRegistry(commands);
    shortcuts.register({id: 'help', commandId: 'view.help', shortcut: 'F1'});
    expect(() => shortcuts.register({id: 'other-help', commandId: 'view.help',
      shortcut: 'F1'})).toThrow('Shortcut conflict');
    const target = new EventTarget();
    const remove = shortcuts.attach(target);
    const input = new KeyboardEvent('keydown', {key: 'F1', bubbles: true});
    target.dispatchEvent(input);
    await Promise.resolve();
    expect(commands.get('view.help').execute).toHaveBeenCalledTimes(1);
    remove();
    target.dispatchEvent(new KeyboardEvent('keydown', {key: 'F1'}));
    await Promise.resolve();
    expect(commands.get('view.help').execute).toHaveBeenCalledTimes(1);
  });
});
