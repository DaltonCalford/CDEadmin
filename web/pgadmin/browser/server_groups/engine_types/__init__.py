##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Engine-family roots and local endpoint discovery for the navigator."""

import importlib.util
import socket

from flask import render_template
from flask_babel import gettext

from pgadmin.browser.server_groups import ServerGroupPluginModule
from pgadmin.browser.utils import NodeView
from pgadmin.cdeadmin.context_menu import connector_context_actions
from pgadmin.cdeadmin.endpoints import registration_profiles
from pgadmin.cdeadmin.navigator import is_loopback_server
from pgadmin.user_login_check import pga_login_required
from pgadmin.utils.ajax import bad_request, make_json_response


ENGINE_LABELS = {
    'apache_ignite': 'Apache Ignite',
    'cassandra': 'Apache Cassandra',
    'clickhouse': 'ClickHouse',
    'cockroachdb': 'CockroachDB',
    'dolt': 'Dolt',
    'duckdb': 'DuckDB',
    'firebird': 'Firebird',
    'foundationdb': 'FoundationDB',
    'immudb': 'immudb',
    'influxdb': 'InfluxDB',
    'mariadb': 'MariaDB',
    'milvus': 'Milvus',
    'mongodb': 'MongoDB',
    'mysql': 'MySQL',
    'neo4j': 'Neo4j',
    'opensearch': 'OpenSearch',
    'postgresql': 'PostgreSQL',
    'redis': 'Redis',
    'scratchbird': 'ScratchBird',
    'sqlite': 'SQLite',
    'tidb': 'TiDB',
    'tikv': 'TiKV',
    'vitess': 'Vitess',
    'xtdb': 'XTDB',
    'yugabytedb': 'YugabyteDB',
}

_INTERFACE_LABELS = {
    'opensearch': 'Native',
    'opensearch_sql_ppl': 'SQL/PPL',
    'ysql': 'YSQL',
    'ycql': 'YCQL',
}


def navigator_engine_id(engine_id):
    """Collapse protocol/interface profiles under their logical engine."""
    if engine_id == 'opensearch_sql_ppl':
        return 'opensearch'
    return engine_id


def supported_engine_types(profiles=None):
    """Return every active logical engine, including empty navigator roots."""
    profiles = registration_profiles() if profiles is None else profiles
    active = {
        navigator_engine_id(profile['engine_id'])
        for profile in profiles
    }
    return tuple(
        (engine_id, ENGINE_LABELS[engine_id])
        for engine_id in sorted(active, key=lambda item: ENGINE_LABELS[item])
    )


_EMBEDDED_MODULES = {
    'duckdb': 'duckdb',
    'sqlite': 'sqlite3',
}


def localhost_engine_status(engine_id, active_profiles=None):
    """Return a passive, credential-free local availability observation."""
    active_profiles = (
        registration_profiles() if active_profiles is None
        else active_profiles
    )
    profiles = [
        profile for profile in active_profiles
        if navigator_engine_id(profile['engine_id']) == engine_id
    ]
    embedded = [
        profile for profile in profiles
        if profile['route_kind'] == 'embedded_file'
    ]
    if embedded:
        module_name = _EMBEDDED_MODULES.get(engine_id)
        available = bool(
            module_name and importlib.util.find_spec(module_name) is not None
        )
        return {
            'available': available,
            'observation': 'embedded-driver-available' if available else
            'embedded-driver-unavailable',
            'ports': [],
            'profile_ids': [item['profile_id'] for item in profiles],
        }

    listening = []
    ports = sorted({
        profile['default_port'] for profile in profiles
        if isinstance(profile.get('default_port'), int)
    })
    for port in ports:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.15):
                listening.append(port)
        except OSError:
            continue
    return {
        'available': bool(listening),
        'observation': 'tcp-listener-detected' if listening else
        'no-default-port-listener',
        'ports': ports,
        'listening_ports': listening,
        'profile_ids': [item['profile_id'] for item in profiles],
    }


def engine_registration_profiles(engine_id, active_profiles=None):
    """Return registration interfaces owned by one navigator connector."""
    active_profiles = (
        registration_profiles() if active_profiles is None
        else active_profiles
    )
    return tuple(
        profile for profile in active_profiles
        if navigator_engine_id(profile['engine_id']) == engine_id
    )


def disambiguate_server_interfaces(server_nodes, profiles):
    """Keep same-host interface registrations distinct and readable."""
    by_label = {}
    for node in server_nodes:
        by_label.setdefault(node.get('label'), []).append(node)
    profile_by_id = {
        profile['profile_id']: profile for profile in profiles
    }
    for label, duplicates in by_label.items():
        if not label or len(duplicates) < 2:
            continue
        for node in duplicates:
            profile = profile_by_id.get(node.get('cde_profile_id'), {})
            interface_id = profile.get('interface_id')
            interface_label = _INTERFACE_LABELS.get(
                interface_id, str(interface_id or '').replace('_', ' ')
            )
            if interface_label:
                node['server_host_label'] = label
                node['cde_interface_id'] = interface_id
                node['label'] = f'{label} ({interface_label})'
    return server_nodes


class EngineTypeModule(ServerGroupPluginModule):
    _NODE_TYPE = 'engine_type'

    @property
    def node_type(self):
        return self._NODE_TYPE

    @property
    def script_load(self):
        return 'server_group'

    @property
    def csssnippets(self):
        return [render_template('css/engine_types.css')]

    def register_preferences(self):
        """Register per-user visibility for every release connector."""
        self.connector_visibility = {}
        for engine_id, label in supported_engine_types():
            self.connector_visibility[engine_id] = self.preference.register(
                'visibility', f'show_connector_{engine_id}',
                gettext('Show %(engine)s connector', engine=label),
                'boolean', False,
                category_label=gettext('Connector visibility'),
                hidden=True,
                help_str=gettext(
                    'Controls whether this release connector is displayed '
                    'in the Object Explorer.'
                ),
            )

    def get_nodes(self, gid, **_kwargs):
        active_profiles = registration_profiles()
        for engine_id, label in supported_engine_types(active_profiles):
            preference = self.connector_visibility.get(engine_id)
            if preference is None or not preference.get():
                continue
            profiles = engine_registration_profiles(
                engine_id, active_profiles
            )
            status = localhost_engine_status(engine_id, active_profiles)
            yield self.generate_browser_node(
                engine_id, gid, label,
                f'icon-engine-type-{engine_id}', True, self.node_type,
                engine_id=engine_id,
                icon_key=f'engine.{engine_id}',
                cde_context_actions=connector_context_actions(
                    engine_id, profiles, status=status,
                ),
            )


blueprint = EngineTypeModule(__name__)


class EngineTypeNode(NodeView):
    node_type = EngineTypeModule._NODE_TYPE
    node_label = 'Connector'
    parent_ids = [{'type': 'int', 'id': 'gid'}]
    ids = [{'type': 'string', 'id': 'eid'}]
    operations = {
        'nodes': [{'get': 'node'}, {'get': 'nodes'}],
        'children': [{'get': 'children'}],
    }

    def _connector_children(self, gid, eid):
        """Build the endpoints and servers below an engine connector."""
        engines = dict(supported_engine_types())
        if eid not in engines:
            return bad_request(errormsg=gettext(
                'The selected engine type is unavailable.'
            ))
        from pgadmin.browser.server_groups.servers import (
            blueprint as server_blueprint,
        )
        status = localhost_engine_status(eid)
        profiles = engine_registration_profiles(eid)
        server_nodes = list(server_blueprint.engine_nodes(
            gid, eid, parent_id=f'engine_type_{eid}'
        ))
        disambiguate_server_interfaces(server_nodes, profiles)
        nodes = []
        if not any(node.get('cde_local_server') or
                   is_loopback_server(node.get('host'))
                   for node in server_nodes):
            local_label = gettext('localhost')
            if not status['available']:
                local_label = gettext('localhost (not detected)')
            nodes.append(self.blueprint.generate_browser_node(
                f'{eid}__localhost', f'engine_type_{eid}', local_label,
                f'icon-engine-type-{eid}' + (
                    '' if status['available'] else '-unavailable'
                ),
                False, 'localhost_placeholder',
                engine_id=eid,
                localhost_placeholder=True,
                localhost_available=status['available'],
                localhost_observation=status['observation'],
                localhost_ports=status.get('ports', []),
                localhost_listening_ports=status.get(
                    'listening_ports', []
                ),
                cde_profile_ids=status['profile_ids'],
                cde_context_actions=connector_context_actions(
                    eid, profiles, status=status,
                    localhost_placeholder=True,
                ),
            ))
        nodes.extend(server_nodes)
        return make_json_response(data=nodes)

    @pga_login_required
    def nodes(self, gid, eid=None):
        return self._connector_children(gid, eid)

    @pga_login_required
    def children(self, gid, eid):
        """Return the endpoints and servers below an engine connector."""
        return self._connector_children(gid, eid)

    @pga_login_required
    def node(self, gid, eid):
        engines = dict(supported_engine_types())
        if eid not in engines:
            return bad_request(errormsg=gettext(
                'The selected engine type is unavailable.'
            ))
        status = localhost_engine_status(eid)
        return make_json_response(data=self.blueprint.generate_browser_node(
            eid, gid, engines[eid], f'icon-engine-type-{eid}', True,
            self.node_type, engine_id=eid, icon_key=f'engine.{eid}',
            cde_context_actions=connector_context_actions(
                eid, engine_registration_profiles(eid), status=status,
            ),
        ))


EngineTypeNode.register_node_view(blueprint)
