/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import usePreferences from '../../../../../preferences/static/js/store';

define('pgadmin.node.engine_type', [
  'sources/gettext', 'sources/pgadmin', 'pgadmin.browser',
  'pgadmin.cdeadmin.endpoint_profiles',
], function(gettext, pgAdmin, pgBrowser, endpointProfiles) {
  const navigatorEngineId = (engineId)=>engineId === 'opensearch_sql_ppl' ?
    'opensearch' : engineId;
  const connectorProfiles = ()=>{
    const profiles = new Map();
    endpointProfiles.profiles.forEach((profile)=>{
      const engineId = navigatorEngineId(profile.engine_id);
      if(!profiles.has(engineId)) profiles.set(engineId, profile);
    });
    return [...profiles.entries()].sort((left, right)=>
      left[1].engine_display_name.localeCompare(right[1].engine_display_name)
    );
  };
  const preferenceName = (engineId)=>`show_connector_${engineId}`;
  const connectorPreference = (engineId)=>usePreferences.getState()
    .getPreferences('NODE-engine_type', preferenceName(engineId));
  const updateConnectorVisibility = async (engineId, visible)=>{
    const preference = connectorPreference(engineId);
    if(!preference) {
      throw new Error(gettext('Connector visibility preference is unavailable.'));
    }
    await usePreferences.getState().setPreferences([{
      category_id: preference.cid,
      id: preference.id,
      mid: preference.mid,
      name: preference.name,
      value: visible,
    }]);
    await pgBrowser.tree.destroy();
    pgBrowser.Events.trigger(
      'pgadmin-browser:tree:destroyed', undefined, undefined
    );
  };

  if (!pgBrowser.Nodes.engine_type) {
    pgBrowser.Nodes.engine_type = pgBrowser.Node.extend({
      parent_type: 'server_group',
      type: 'engine_type',
      label: gettext('Connector'),
      canEdit: false,
      canDrop: false,
      hasProperties: false,
      hasSQL: false,
      hasStatistics: false,
      hasDependencies: false,
      hasDependents: false,
      hasScriptTypes: [],
      Init: function() {
        if (this.initialized) return;
        this.initialized = true;
        const menus = connectorProfiles().map(([engineId, profile], index)=>({
          name: `show_connector_${engineId}`,
          commandId: `connector.visibility.${engineId}.set`,
          commandVersion: 1,
          label: profile.engine_display_name,
          description: gettext('Show or hide this connector in the Object Explorer.'),
          iconKey: `engine.${engineId}`,
          applies: ['connectors'],
          priority: index + 1,
          is_checkbox: true,
          checked: ()=>connectorPreference(engineId)?.value === true,
          commandArguments: ()=>({
            visible: connectorPreference(engineId)?.value !== true,
          }),
          validateArguments: (args)=>typeof args.visible === 'boolean' ||
            gettext('Connector visibility must be true or false.'),
          commandHandler: (args)=>updateConnectorVisibility(
            engineId, args.visible
          ),
          macroCallable: true,
        }));
        menus.push({
          name: 'refresh_engine_connector',
          node: this.type,
          module: this,
          applies: ['object', 'context'],
          callback: 'refresh',
          priority: 2,
          label: gettext('Refresh connector availability...'),
          enable: function(data) {
            return !data?.localhost_placeholder;
          },
        });
        pgBrowser.add_menus(menus);
      },
      can_expand: function(data) {
        return !data?.localhost_placeholder;
      },
    });
  }

  if (!pgBrowser.Nodes.localhost_placeholder) {
    pgBrowser.Nodes.localhost_placeholder = pgBrowser.Node.extend({
      parent_type: 'engine_type',
      type: 'localhost_placeholder',
      label: gettext('Local server discovery'),
      hasId: false,
      canEdit: false,
      canDrop: false,
      hasProperties: false,
      hasSQL: false,
      hasScriptTypes: [],
      can_expand: false,
    });
  }

  return pgBrowser.Nodes.engine_type;
});
