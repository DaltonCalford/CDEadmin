/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {commandRegistry} from './CommandRegistry';

function normalizedCombo(value) {
  const parts = String(value ?? '').toLowerCase().replaceAll('cmd', 'meta')
    .replaceAll('control', 'ctrl').split('+').map((item) => item.trim())
    .filter(Boolean);
  const key = parts.pop();
  if(!key) throw new TypeError('Shortcut key combination is required.');
  const modifiers = new Set(parts);
  return {
    key: key === 'escape' ? 'esc' : key,
    ctrl: modifiers.has('ctrl'), meta: modifiers.has('meta'),
    alt: modifiers.has('alt'), shift: modifiers.has('shift'),
  };
}

function matches(shortcut, event) {
  const key = String(event.key).toLowerCase() === 'escape' ? 'esc' :
    String(event.key).toLowerCase();
  const primaryMatches = shortcut.ctrl && shortcut.meta ?
    (event.ctrlKey || event.metaKey) :
    event.ctrlKey === shortcut.ctrl && event.metaKey === shortcut.meta;
  return primaryMatches && event.altKey === shortcut.alt &&
    event.shiftKey === shortcut.shift && key === shortcut.key;
}

export class ShortcutRegistry {
  constructor(commands=commandRegistry) {
    this.commands = commands;
    this.bindings = new Map();
  }

  register(input) {
    const id = String(input?.id ?? '').trim();
    const commandId = String(input?.commandId ?? '').trim();
    const scope = String(input?.scope ?? 'global');
    if(!id || !commandId) throw new TypeError('Shortcut ID and command ID are required.');
    if(this.bindings.has(id)) throw new Error(`Shortcut already registered: ${id}`);
    const binding = Object.freeze({
      id, commandId, scope, shortcut: normalizedCombo(input.shortcut),
      priority: Number.isFinite(input.priority) ? input.priority : 100,
      arguments: input.arguments ?? (() => ({})),
      when: input.when ?? (() => true),
    });
    const conflict = [...this.bindings.values()].find((candidate) =>
      candidate.scope === scope && JSON.stringify(candidate.shortcut) ===
      JSON.stringify(binding.shortcut));
    if(conflict) throw new Error(
      `Shortcut conflict in ${scope}: ${conflict.id} and ${id}`
    );
    this.bindings.set(id, binding);
    return () => this.bindings.delete(id);
  }

  async handle(event, context={}) {
    if(event.defaultPrevented) return false;
    const scopes = [context.activeSurfaceId, context.activeScope, 'global']
      .filter(Boolean);
    const candidates = [...this.bindings.values()].filter((binding) =>
      scopes.includes(binding.scope) && matches(binding.shortcut, event) &&
      binding.when(context)
    ).sort((left, right) => scopes.indexOf(left.scope) - scopes.indexOf(right.scope) ||
      left.priority - right.priority);
    const binding = candidates[0];
    if(!binding) return false;
    event.preventDefault();
    await this.commands.execute(
      binding.commandId, binding.arguments(context), context
    );
    return true;
  }

  attach(target, contextProvider=() => ({})) {
    if(!target?.addEventListener) throw new TypeError('Shortcut event target is invalid.');
    const listener = (event) => this.handle(event, contextProvider())
      .catch((error) => contextProvider()?.onShortcutError?.(error));
    target.addEventListener('keydown', listener);
    return () => target.removeEventListener('keydown', listener);
  }
}

export const shortcutRegistry = new ShortcutRegistry();
