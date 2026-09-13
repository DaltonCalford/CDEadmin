/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import currentUser from 'pgadmin.user_management.current_user';
import getApiInstance from '../../../static/js/api_instance';
import pgAdmin from 'sources/pgadmin';
import url_for from 'sources/url_for';
import usePreferences from '../../../preferences/static/js/store';
import {
  commandRegistry,
} from '../../../static/js/cdeadmin_ui/commands/CommandRegistry';
import {
  menuBindingRegistry,
} from '../../../static/js/cdeadmin_ui/commands/MenuStructure';
import {inferActionIconKey} from '../../../static/js/cdeadmin_ui/icons/registry';
import {normalizeCommandCustomizations} from
  '../../../static/js/cdeadmin_ui/customization/profile';

function safeSegment(value, fallback) {
  const normalized = String(value ?? '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  return normalized || fallback;
}

export function legacyCommandId(options={}) {
  if(options.commandId || options.command_id) {
    return String(options.commandId ?? options.command_id);
  }
  const owner = safeSegment(
    options.module?.type ?? options.node ?? 'application', 'application'
  );
  const name = safeSegment(options.name, 'unnamed');
  return `legacy.${owner}.${name}`;
}

function legacyPredicate(value, options, context, defaultValue) {
  if(value === undefined || value === '') return defaultValue;
  if(typeof value === 'boolean') return value;
  if(typeof value === 'function') {
    if(context.itemData === undefined && options.node) return false;
    return Boolean(value.apply(options.module, [
      context.itemData, context.item, options.data,
    ]));
  }
  if(typeof value === 'string') {
    if(value.toLowerCase() === 'false') return false;
    const member = options.module?.[value];
    if(typeof member === 'boolean') return member;
    if(typeof member === 'function') {
      if(context.itemData === undefined && options.node) return false;
      return Boolean(member.call(
        options.module, context.itemData, context.item, options.data
      ));
    }
  }
  return defaultValue;
}

async function executeLegacy(options, args, context) {
  const selected = context.item ?? pgAdmin.Browser.tree?.selected();
  const callback = options.callback;
  if(typeof options.commandHandler === 'function') {
    return options.commandHandler(args, context);
  }
  if(options.module?.callbacks?.[callback]) {
    return options.module.callbacks[callback].apply(
      options.module, [options.data, selected, args]
    );
  }
  if(typeof options.module?.[callback] === 'function') {
    return options.module[callback](options.data, selected, args);
  }
  if(typeof callback === 'function') return callback(options, args, context);
  if(options.url && options.url !== '#') {
    await getApiInstance()(url_for('tools.initialize'));
    return window.open(options.url, options.target ?? '_self');
  }
  return undefined;
}

function isActionable(options) {
  return typeof options.commandHandler === 'function' ||
    typeof options.callback === 'function' ||
    typeof options.module?.callbacks?.[options.callback] === 'function' ||
    typeof options.module?.[options.callback] === 'function' ||
    Boolean(options.url && options.url !== '#');
}

export function registerMenuCommand(options, surface=null) {
  const id = legacyCommandId(options);
  if(commandRegistry.has(id)) return id;
  if(!isActionable(options)) return null;
  commandRegistry.ensure({
    id,
    version: options.commandVersion ?? 1,
    label: options.label,
    description: options.description,
    iconKey: options.iconKey ?? inferActionIconKey(options) ??
      options.icon ?? 'command.default',
    permission: options.permission,
    allowedSecurityGroups: options.allowedSecurityGroups,
    deniedSecurityGroups: options.deniedSecurityGroups,
    defaultEnabled: options.defaultEnabled !== false,
    defaultVisible: options.defaultVisible !== false,
    macroCallable: options.macroCallable !== false,
    requiresConfirmation: options.requiresConfirmation,
    intent: options.intent,
    surfaces: options.applies,
    enabledWhen: (context)=>legacyPredicate(
      options.enable, options, context, true
    ),
    visibleWhen: (context)=>legacyPredicate(
      options.visible, options, context, true
    ),
    checkedWhen: (context)=>legacyPredicate(
      options.checked, options, context, false
    ),
    validateArguments: options.validateArguments,
    execute: (args, context)=>executeLegacy(options, args, context),
  });
  if(surface) {
    menuBindingRegistry.register({
      surface,
      commandId: id,
      name: options.name,
      category: options.category,
      node: options.node,
      priority: options.priority,
      commandArguments: options.commandArguments,
    });
  }
  return id;
}

export function commandCustomizations() {
  const value = usePreferences.getState().getPreferences(
    'browser', 'command_customizations'
  )?.value;
  return normalizeCommandCustomizations(value);
}

export function commandContext(item=null, itemData=undefined, shortcut=null) {
  const selected = item ?? pgAdmin.Browser.tree?.selected();
  return {
    ...(pgAdmin.Browser.CDEadminCommandContext?.() ?? {}),
    currentUser,
    item: selected,
    itemData: itemData ?? pgAdmin.Browser.tree?.itemData(selected),
    shortcut,
    commandCustomizations: commandCustomizations(),
  };
}

export function resolveMenuCommand(options, item=null, itemData=undefined) {
  const id = registerMenuCommand(options);
  if(!id) return null;
  return commandRegistry.resolve(
    id, commandContext(item, itemData, options.shortcut)
  );
}

export async function executeMenuCommand(
  options, item=null, itemData=undefined
) {
  const id = registerMenuCommand(options);
  if(!id) return undefined;
  const context = commandContext(item, itemData, options.shortcut);
  const rawArguments = typeof options.commandArguments === 'function' ?
    options.commandArguments(context) : (options.commandArguments ?? {});
  return commandRegistry.execute(id, rawArguments, context);
}

function authoritativeContext(context={}) {
  return {
    ...context,
    ...commandContext(
      context.item ?? null, context.itemData, context.shortcut ?? null
    ),
  };
}

export const commandService = Object.freeze({
  has: (id)=>commandRegistry.has(id),
  get: (id)=>commandRegistry.get(id),
  list: ()=>commandRegistry.list(),
  register: (command)=>commandRegistry.register(command),
  resolve: (id, context={})=>commandRegistry.resolve(
    id, authoritativeContext(context)
  ),
  execute: (id, args={}, context={})=>commandRegistry.execute(
    id, args, authoritativeContext(context)
  ),
  executeMacro: (macro, context={})=>commandRegistry.executeMacro(
    macro, authoritativeContext(context)
  ),
  subscribe: (listener)=>commandRegistry.subscribe(listener),
});

export {commandRegistry, menuBindingRegistry};
