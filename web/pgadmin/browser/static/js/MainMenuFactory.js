/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////
import gettext from 'sources/gettext';
import pgAdmin from 'sources/pgadmin';
import Menu, { MenuItem } from '../../../static/js/helpers/Menu';
import usePreferences from '../../../preferences/static/js/store';
import {menuStructureRegistry} from
  '../../../static/js/cdeadmin_ui/commands/MenuStructure';
import {
  commandCustomizations, executeMenuCommand, resolveMenuCommand,
} from './CommandMenuAdapter';
import {commandRegistry} from
  '../../../static/js/cdeadmin_ui/commands/CommandRegistry';
import {
  isProviderContextNode, providerContextMenuItems,
  registerProviderMenuCategories,
} from './ProviderContextMenu';

export default class MainMenuFactory {
  static electronCallbacks = {};

  static toElectron() {
    // we support 2 levels of submenu
    return pgAdmin.Browser.MainMenus.map((m)=>{
      return {
        ...m.serialize(),
        submenu: m.menuItems.map((sm)=>{
          const smName = `${m.name}_${sm.name}`;
          MainMenuFactory.electronCallbacks[smName] = sm.callback;
          return {
            ...sm.serialize(),
            submenu: sm.getMenuItems()?.map((smsm)=>{
              MainMenuFactory.electronCallbacks[`${smName}_${smsm.name}`] = smsm.callback;
              return {
                ...smsm.serialize(),
              };
            })
          };
        })
      };
    });
  }

  static listenToElectronMenuClick() {
    window.electronUI?.onMenuClick((menuName)=>{
      MainMenuFactory.electronCallbacks[menuName]?.();
    });
  }

  static createMainMenus() {
    pgAdmin.Browser.MainMenus = [];
    const menuPreference = usePreferences.getState().getPreferences(
      'browser', 'menu_customizations'
    )?.value;
    menuStructureRegistry.resolve(menuPreference).forEach((_menu) => {
      let menuObj = Menu.create(_menu.name, gettext(_menu.label), _menu.id, _menu.index, _menu.addSeprator, _menu.hasDynamicMenuItems);
      menuObj.iconKey = _menu.iconKey;
      menuObj.presentation = _menu.presentation;
      pgAdmin.Browser.MainMenus.push(menuObj);
      // Don't add menuItems for hasDynamicMenuItems true as it's menuItems get changed on tree selection.
      if(!_menu.hasDynamicMenuItems) {
        menuObj.clearMenuItems();
        menuObj.addMenuItems(MainMenuFactory.createMenuItems(
          MainMenuFactory.menuContributions(_menu.name)
        ));
      }
    });

    // enable disable will take care of dynamic menus.
    MainMenuFactory.enableDisableMenus();

    window.electronUI?.setMenus(MainMenuFactory.toElectron());
  }

  static menuContributions(name) {
    const aliases = name === 'data' ? ['data', 'connectors', 'management'] :
      [name];
    const cache = Object.assign({}, ...aliases.map((surface) =>
      pgAdmin.Browser.all_menus_cache?.[surface] ?? {}
    ));
    const customizations = commandCustomizations();
    const registered = commandRegistry.list().filter((command) => {
      const requestedSurface = customizations[command.id]?.surface;
      return requestedSurface ? requestedSurface === name :
        command.surfaces.includes(name);
    }
    ).reduce((result, command, index) => ({...result, [command.id]: {
      name: command.id.replaceAll('.', '_'),
      commandId: command.id,
      label: customizations[command.id]?.label ?? command.label,
      iconKey: customizations[command.id]?.iconKey ?? command.iconKey,
      presentation: customizations[command.id]?.presentation ?? {},
      priority: Number.isFinite(customizations[command.id]?.priority) ?
        customizations[command.id].priority : 1000 + index,
      category: 'common',
    }}), {});
    return {...cache, ...registered};
  }

  static getSeparator(label, priority) {
    return new MenuItem({type: 'separator', label, priority});
  }

  static createMenuItem(options) {
    const resolved = resolveMenuCommand(options);
    if(resolved && !resolved.visible) return null;
    const callback = ()=>executeMenuCommand(options).catch((error)=>{
      if(error?.code === 'permission_denied') {
        pgAdmin.Browser.notifier.alert(
          gettext('Permission Denied'),
          gettext('You don\'t have the necessary permissions to access this feature. Please contact your administrator for assistance.')
        );
      } else {
        pgAdmin.Browser.notifier.error(
          error?.message ?? gettext('The command could not be completed.')
        );
      }
    });
    const materialized = resolved ? {
      ...options,
      commandId: resolved.id,
      commandVersion: resolved.version,
      label: resolved.label,
      iconKey: resolved.iconKey,
      checked: resolved.checked,
      enable: resolved.enabled,
      disabledReason: resolved.disabledReason,
      callback,
    } : options;
    return new MenuItem(materialized, (menu, item)=> {
      pgAdmin.Browser.Events.trigger('pgadmin:enable-disable-menu-items', menu, item);
      window.electronUI?.enableDisableMenuItems(menu?.serialize(), item?.serialize());
    });
  }

  static updateShortcutsFromPreferences(prefStore)  {
    const updateShortcuts = (item) => {
      if (!item || typeof item !== 'object') return;

      Object.values(item).forEach((menuItem) => {
        if (!menuItem || typeof menuItem !== 'object') return;

        if (menuItem?.shortcut_preference) {
          const [module, key] = menuItem.shortcut_preference;
          menuItem.shortcut = prefStore.getPreferences(module, key)?.value || null;
        }
        // Recurse only if it's a nested object.
        if (!menuItem.name) {
          updateShortcuts(menuItem);
        }
      });
    };
    let allMenus = pgAdmin.Browser?.all_menus_cache || {};
    Object.values(allMenus).forEach(updateShortcuts);
    MainMenuFactory.createMainMenus();
  };

  // Assign and Update menu shortcuts using preference.
  static subscribeShortcutChanges() {
    MainMenuFactory.updateShortcutsFromPreferences(usePreferences.getState());
    usePreferences.subscribe(MainMenuFactory.updateShortcutsFromPreferences);
  }

  static enableDisableMenus(item) {
    item = item || pgAdmin.Browser.tree?.selected();
    let itemData = pgAdmin.Browser.tree?.itemData(item);

    const checkForItems = (items)=>{
      items.forEach((mitem) => {
        const subItems = mitem.getMenuItems() ?? [];
        if(subItems.length > 0) {
          checkForItems(subItems);
        } else {
          mitem.checkAndSetDisabled(itemData, item);
        }
      });
    };

    // Non dynamic menus will be required to check whether enabled/disabled.
    pgAdmin.Browser.MainMenus.filter((m)=>(!m.hasDynamicMenuItems)).forEach((menu) => {
      checkForItems(menu.getMenuItems());
    });

    pgAdmin.Browser.MainMenus.filter((m)=>(m.hasDynamicMenuItems)).forEach((menu) => {
      let menuItemList = MainMenuFactory.getDynamicMenu(menu.name, item, itemData);
      menu.setMenuItems(menuItemList);
    });

    // set the context menu as well
    pgAdmin.Browser.BrowserContextMenu = MainMenuFactory.getDynamicMenu('context', item, itemData, true);

    window.electronUI?.setMenus(MainMenuFactory.toElectron());

    pgAdmin.Browser.Events.trigger('pgadmin:refresh-app-menu');
  }

  static checkNoMenuOptionForNode(itemData){
    if(!itemData) {
      return true;
    }
    let selectedNodeFromNodes=pgAdmin.Browser.Nodes[itemData._type];
    let selectedNode=pgAdmin.Browser.tree.selected();
    return selectedNodeFromNodes.showMenu?.(itemData, selectedNode) ?? true;
  }

  static createMenuItems(items, skipDisabled=false, checkAndSetDisabled=()=>true) {
    let retVal = [];
    let categories = {};

    const getNewMenuItem = (i)=>{
      const mi = MainMenuFactory.createMenuItem({...i});
      if(!mi) return null;
      checkAndSetDisabled?.(mi);
      if(skipDisabled && mi.isDisabled) {
        return null;
      }
      return mi;
    };

    const getMenuCategory = (catName)=>{
      let category = pgAdmin.Browser.menu_categories[catName];

      if(!category) {
        // generate category on the fly.
        category = {
          name: catName,
          label: catName,
          priority: 10,
        };
      }

      let cmi = categories[category.name];
      if(!cmi) {
        cmi = getNewMenuItem({...category});
        // for easily finding again, note down.
        categories[category.name] = cmi;
      }
      return cmi;
    };

    const applySeparators = (mi)=>{
      const newItems = [];
      if(mi.above) {
        newItems.push(MainMenuFactory.getSeparator(mi.label, mi.priority));
      }
      newItems.push(mi);
      if(mi.below) {
        newItems.push(MainMenuFactory.getSeparator(mi.label, mi.priority));
      }
      return newItems;
    };

    Object.entries(items ?? {}).forEach(([k, i])=>{
      if('name' in i) {
        const mi = getNewMenuItem(i);
        if(!mi) return;

        if((i.category??'common') != 'common') {
          const cmi = getMenuCategory(i.category);
          if(cmi) {
            cmi.addMenuItems([...applySeparators(mi)]);
          } else {
            retVal.push(...applySeparators(mi));
          }
        } else {
          retVal.push(...applySeparators(mi));
        }
      } else {
        // Can be a category
        const cmi = getMenuCategory(k);
        if(cmi) {
          cmi.addMenuItems(MainMenuFactory.createMenuItems(i, skipDisabled, checkAndSetDisabled));
        }
      }
    });

    // Push the category menus
    Object.values(categories).forEach((cmi)=>{
      if (!cmi) return;
      const items = cmi.getMenuItems();

      // if there is only one menu in the category, then no need of the category.
      if(items.length <= 1 && !cmi.single) {
        retVal = retVal.concat(items);
        return;
      }
      retVal.push(...applySeparators(cmi));
    });

    Menu.sortMenus(retVal ?? []);
    return retVal;
  }

  static getDynamicMenu(name, item, itemData, skipDisabled=false) {
    if(!item) {
      return [MainMenuFactory.createMenuItem({
        name: '',
        label: gettext('No object selected'),
        category: 'create',
        priority: 1,
        enable: false,
      })];
    }
    const showMenu = MainMenuFactory.checkNoMenuOptionForNode(itemData);
    if(!showMenu){
      return [MainMenuFactory.createMenuItem({
        enable : false,
        label: gettext('No menu available for this object.'),
        name:'',
        priority: 1,
        category: 'create',
      })];
    } else {
      // Provider nodes carry server-resolved actions. They are authoritative:
      // never merge PostgreSQL's legacy server/object menus into them.
      const providerNode = isProviderContextNode(itemData) &&
        ['context', 'object'].includes(name);
      const nodeTypeMenus = providerNode ? providerContextMenuItems(
        itemData, item
      ) :
        (pgAdmin.Browser.all_menus_cache[name]?.[itemData._type] ?? []);
      if(providerNode) {
        registerProviderMenuCategories(pgAdmin.Browser, nodeTypeMenus);
      }
      const menuItemList = MainMenuFactory.createMenuItems(nodeTypeMenus, skipDisabled, (mi)=>{
        return mi.checkAndSetDisabled(itemData, item);
      });
      if(menuItemList.length == 0) {
        return [MainMenuFactory.createMenuItem({
          enable : false,
          label: gettext('No menu available for this object.'),
          name:'',
          priority: 1,
          category: 'create',
        })];
      }
      return menuItemList;
    }
  }
}
