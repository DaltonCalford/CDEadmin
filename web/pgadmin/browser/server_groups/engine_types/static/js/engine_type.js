/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

define('pgadmin.node.engine_type', [
  'sources/gettext', 'sources/pgadmin', 'pgadmin.browser',
], function(gettext, pgAdmin, pgBrowser) {
  if (!pgBrowser.Nodes.engine_type) {
    pgBrowser.Nodes.engine_type = pgBrowser.Node.extend({
      parent_type: 'server_group',
      type: 'engine_type',
      label: gettext('Connector'),
      canEdit: false,
      canDrop: false,
      hasScriptTypes: [],
      Init: function() {
        if (this.initialized) return;
        this.initialized = true;
        pgBrowser.add_menus([{
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
        }]);
      },
      can_expand: function(data) {
        return !data?.localhost_placeholder;
      },
    });
  }

  return pgBrowser.Nodes.engine_type;
});
