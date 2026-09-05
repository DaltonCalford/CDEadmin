/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {inferActionIconKey} from '../icons/registry';

export const TREE_ACTION_INTENTS = Object.freeze({
  DEFAULT: 'default',
  PRIMARY: 'primary',
  DESTRUCTIVE: 'destructive',
});

const INTENTS = new Set(Object.values(TREE_ACTION_INTENTS));

function text(value, fallback='') {
  return value === null || value === undefined ? fallback : String(value);
}

function actionId(value, fallback) {
  const normalized = text(value, fallback).trim().toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return normalized || fallback;
}

export function createTreeActionDescriptor(input={}) {
  if(input.type === 'separator') {
    return Object.freeze({schemaVersion: 1, type: 'separator'});
  }

  const id = actionId(input.id ?? input.name, 'action');
  const label = text(input.label).trim();
  if(!label) throw new TypeError('Tree action label is required.');
  const execute = input.execute ?? input.callback;
  if(typeof execute !== 'function' && !input.children?.length) {
    throw new TypeError(`Tree action ${id} requires an executable handler.`);
  }

  const enabled = input.enabled !== false && input.isDisabled !== true;
  const children = Object.freeze((input.children ?? []).map(
    (child)=>createTreeActionDescriptor(child)
  ));
  return Object.freeze({
    schemaVersion: 1,
    type: input.type || 'action',
    id,
    label,
    intent: INTENTS.has(input.intent) ? input.intent :
      /delete|drop|remove|revoke|terminate/.test(id) ?
        TREE_ACTION_INTENTS.DESTRUCTIVE : TREE_ACTION_INTENTS.DEFAULT,
    iconKey: text(input.iconKey, inferActionIconKey({...input, id})),
    enabled,
    disabledReason: enabled ? '' : text(
      input.disabledReason, 'This action is not available for the selected object.'
    ),
    checked: typeof input.checked === 'boolean' ? input.checked : undefined,
    shortcut: input.shortcut,
    requiresConfirmation: Boolean(input.requiresConfirmation ||
      /delete|drop|remove|revoke|terminate/.test(id)),
    execute,
    children,
    providerId: text(input.providerId),
    objectTypes: Object.freeze([...(input.objectTypes ?? [])]),
  });
}

function legacyChildren(item) {
  const children = item.children ?? item.getMenuItems?.() ?? [];
  return Array.isArray(children) ? children : [];
}

export function normalizeTreeActions(items=[], context={}) {
  return Object.freeze(items.map((item, index)=>{
    if(item?.schemaVersion === 1 &&
        (item.type === 'separator' || typeof item.execute === 'function' ||
          item.children?.length)) {
      return item;
    }
    if(item?.type === 'separator') return createTreeActionDescriptor(item);
    const children = legacyChildren(item);
    return createTreeActionDescriptor({
      id: item.id ?? item.name ?? `${context.node?.objectType ?? 'object'}-${index}`,
      label: item.label,
      type: item.type,
      intent: item.intent,
      iconKey: item.iconKey ?? item.icon_key,
      enabled: item.enabled,
      isDisabled: item.isDisabled,
      disabledReason: item.disabledReason,
      checked: item.checked,
      shortcut: item.shortcut,
      requiresConfirmation: item.requiresConfirmation,
      execute: item.execute ?? item.callback,
      children: children.map((child, childIndex)=>({
        ...child,
        id: child.id ?? child.name ?? `${item.name ?? index}-${childIndex}`,
        children: legacyChildren(child),
      })),
      providerId: context.node?.providerId,
      objectTypes: context.node?.objectType ? [context.node.objectType] : [],
    });
  }));
}

export class TreeActionRegistry {
  constructor() {
    this.registrations = [];
  }

  register({
    id,
    providerId='*',
    engineId='*',
    objectTypes=['*'],
    priority=100,
    actionFactory,
  }) {
    if(typeof actionFactory !== 'function') {
      throw new TypeError('Tree action registrations require an actionFactory.');
    }
    const registrationId = actionId(id, 'provider-actions');
    if(this.registrations.some((item)=>item.id === registrationId)) {
      throw new Error(`Tree action registration already exists: ${registrationId}`);
    }
    const registration = Object.freeze({
      id: registrationId,
      providerId,
      engineId,
      objectTypes: Object.freeze([...objectTypes]),
      priority: Number(priority),
      actionFactory,
    });
    this.registrations.push(registration);
    this.registrations.sort((a, b)=>a.priority - b.priority);
    return ()=>{
      this.registrations = this.registrations.filter(
        (item)=>item !== registration
      );
    };
  }

  actionsFor(context={}) {
    const node = context.node ?? {};
    const actions = [];
    this.registrations.forEach((registration)=>{
      const providerMatches = registration.providerId === '*' ||
        registration.providerId === node.providerId;
      const engineMatches = registration.engineId === '*' ||
        registration.engineId === node.engineId;
      const objectMatches = registration.objectTypes.includes('*') ||
        registration.objectTypes.includes(node.objectType);
      if(providerMatches && engineMatches && objectMatches) {
        const supplied = registration.actionFactory(context) ?? [];
        actions.push(...normalizeTreeActions(supplied, context));
      }
    });
    return Object.freeze(actions);
  }

  resolve(context={}, legacyActions=[]) {
    const combined = [
      ...normalizeTreeActions(legacyActions, context),
      ...this.actionsFor(context),
    ];
    const seen = new Set();
    return Object.freeze(combined.filter((action)=>{
      if(action.type === 'separator') return true;
      const identity = `${action.providerId}:${action.id}`;
      if(seen.has(identity)) return false;
      seen.add(identity);
      return true;
    }));
  }
}

export const treeActionRegistry = new TreeActionRegistry();
