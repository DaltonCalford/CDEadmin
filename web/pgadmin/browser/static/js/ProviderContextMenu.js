/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import _ from 'lodash';
import gettext from 'sources/gettext';
import pgAdmin from 'sources/pgadmin';

const ACTION_SCHEMA = 'cdeadmin.context-action.v1';
const SAFE_HANDLERS = new Set([
  'clear_credentials', 'connector_info', 'edit_endpoint',
  'forget_endpoint', 'open_workspace', 'refresh_node',
  'register_endpoint', 'verify_endpoint',
]);

export const PROVIDER_MENU_CATEGORIES = Object.freeze({
  register: {label: gettext('Registration'), priority: 10},
  connection: {label: gettext('Connection'), priority: 20},
  database: {label: gettext('Database workspace'), priority: 30},
  'database-lifecycle': {
    label: gettext('Definition and lifecycle'), priority: 40,
  },
  backup: {label: gettext('Backup'), priority: 50},
  restore: {label: gettext('Restore'), priority: 60},
  diagnostics: {
    label: gettext('Diagnostics and verification'), priority: 70,
  },
  maintenance: {label: gettext('Maintenance'), priority: 80},
  availability: {label: gettext('Availability'), priority: 90},
  security: {label: gettext('Security and authorization'), priority: 100},
  object: {label: gettext('Object workspace'), priority: 110},
  operations: {label: gettext('Object operations'), priority: 120},
  endpoint: {label: gettext('Endpoint registration'), priority: 130},
  information: {label: gettext('Information'), priority: 140},
});

export function registerProviderMenuCategories(browser, actions) {
  if(!browser) return;
  browser.menu_categories ||= {};
  new Set((actions || []).map((item) => item.category).filter(Boolean))
    .forEach((categoryId) => {
      const definition = PROVIDER_MENU_CATEGORIES[categoryId];
      if(!definition || browser.menu_categories[categoryId]) return;
      browser.menu_categories[categoryId] = {
        name: categoryId,
        label: definition.label,
        priority: definition.priority,
        single: true,
      };
    });
}

function selectedAction(commandId, itemData) {
  return (itemData?.cde_context_actions || []).find(
    (candidate) => candidate.command_id === commandId
  );
}

function serverSelection(item) {
  const tree = pgAdmin.Browser.tree;
  let current = item;
  while(current) {
    const data = tree.itemData(current);
    if(data?._type === 'server') return {item: current, data};
    current = tree.hasParent(current) ? tree.parent(current) : null;
  }
  return {item: null, data: null};
}

function showConnectorInformation(args, context) {
  const title = context.itemData?.label || context.itemData?._label ||
    gettext('Connector information');
  const body = _.escape(JSON.stringify(args, null, 2));
  pgAdmin.Browser.notifier.alert(
    gettext('%s information', title), `<pre>${body}</pre>`
  );
  return false;
}

function dispatch(action, args, context) {
  const tree = pgAdmin.Browser.tree;
  const item = context.item || tree.selected();
  const serverNode = pgAdmin.Browser.Nodes.server;
  const endpoint = serverSelection(item);
  switch(action.handler) {
  case 'register_endpoint':
    return serverNode.callbacks.register_engine_server.call(serverNode, {
      item,
      registrationContext: args,
    });
  case 'connector_info':
    return showConnectorInformation(args, context);
  case 'refresh_node': {
    const target = args.reload_parent && tree.hasParent(item) ?
      tree.parent(item) : item;
    const targetData = tree.itemData(target);
    const node = pgAdmin.Browser.Nodes[targetData?._type];
    return node?.callbacks?.refresh?.call(node, null, target);
  }
  case 'verify_endpoint':
    return serverNode.callbacks.verify_cde_endpoint.call(serverNode, {
      item: endpoint.item,
    });
  case 'open_workspace':
    return serverNode.callbacks.open_cde_workspace.call(
      serverNode, {item: endpoint.item}, args.tab || 'resources', {
        ...args, task_title: action.label.replace(/\.\.\.$/, ''),
      }
    );
  case 'edit_endpoint':
    return pgAdmin.Browser.Node.callbacks.show_obj_properties.call(
      serverNode, {action: 'edit', item: endpoint.item}
    );
  case 'clear_credentials':
    return serverNode.callbacks.clear_saved_password.call(serverNode, {
      item: endpoint.item,
    });
  case 'forget_endpoint':
    return pgAdmin.Browser.Node.callbacks.delete_obj.call(serverNode, {
      item: endpoint.item, url: 'drop',
    });
  default:
    throw new Error(gettext('The provider context action is not supported.'));
  }
}

export function isProviderContextNode(itemData) {
  return Array.isArray(itemData?.cde_context_actions);
}

export function providerContextMenuItems(itemData, item=null) {
  if(!isProviderContextNode(itemData)) return null;
  return itemData.cde_context_actions.filter((action) =>
    action?.schema === ACTION_SCHEMA &&
    typeof action.command_id === 'string' &&
    typeof action.label === 'string' &&
    SAFE_HANDLERS.has(action.handler)
  ).map((action) => ({
    name: action.command_id.replace(/[^a-zA-Z0-9_]+/g, '_'),
    commandId: action.command_id,
    commandVersion: action.command_version || 1,
    label: action.label,
    description: action.description || '',
    disabledReason: action.disabled_reason || '',
    iconKey: action.icon_key || 'command.default',
    category: action.menu_group || 'common',
    priority: action.priority ?? 100,
    intent: action.mutation_class || 'read',
    applies: ['object', 'context'],
    enable: () => selectedAction(action.command_id, itemData)?.enabled !==
      false,
    commandArguments: () => ({
      ...(selectedAction(action.command_id, itemData)?.arguments || {}),
    }),
    commandHandler: (args, context) => dispatch(action, args, {
      ...context, item: item || context.item, itemData,
    }),
    requiresConfirmation: Boolean(action.requires_confirmation),
    macroCallable: action.macro_callable !== false,
  }));
}

function executeProviderAction(menu, itemData, item) {
  const invoke = ()=>menu.commandHandler(
    menu.commandArguments({itemData, item}), {itemData, item}
  );
  if(!menu.requiresConfirmation) return invoke();
  return pgAdmin.Browser.notifier.confirm(
    gettext('Confirm provider action'),
    gettext('Are you sure you want to continue with “%s”?', menu.label),
    invoke,
    ()=>true,
    gettext('Continue'),
    gettext('Cancel')
  );
}

export function openProviderObject(itemData, item) {
  const menu = providerContextMenuItems(itemData, item)?.find(
    (candidate) => candidate.commandId.endsWith('.browse')
  );
  if (menu?.enable()) return executeProviderAction(menu, itemData, item);
  pgAdmin.Browser.notifier.error(
    gettext('The provider has not made an object browser available for this object.')
  );
  return false;
}

export function providerTreeContextActions(itemData, item=null) {
  const menus = providerContextMenuItems(itemData, item);
  if(menus === null) return null;
  const grouped = new Map();
  menus.forEach((menu)=>{
    const category = menu.category || 'object';
    if(!grouped.has(category)) grouped.set(category, []);
    grouped.get(category).push({
      id: menu.commandId,
      label: menu.label,
      description: menu.description,
      iconKey: menu.iconKey,
      intent: menu.intent === 'destructive' ? 'destructive' : 'default',
      enabled: menu.enable(itemData, item),
      disabledReason: menu.disabledReason,
      requiresConfirmation: menu.requiresConfirmation,
      execute: ()=>executeProviderAction(menu, itemData, item),
      providerId: itemData?.cde_provider_id || '',
      objectTypes: [itemData?._type || 'object'],
    });
  });
  return [...grouped.entries()].sort(([left], [right])=>{
    if (left === 'common') return -1;
    if (right === 'common') return 1;
    const a = PROVIDER_MENU_CATEGORIES[left]?.priority ?? 1000;
    const b = PROVIDER_MENU_CATEGORIES[right]?.priority ?? 1000;
    return a - b || left.localeCompare(right);
  }).flatMap(([category, children])=>category === 'common' ? children : [{
    id: `provider-category-${category}`,
    label: PROVIDER_MENU_CATEGORIES[category]?.label || category,
    iconKey: '',
    enabled: true,
    children,
  }]);
}

export default providerContextMenuItems;
