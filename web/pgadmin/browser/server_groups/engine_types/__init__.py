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
from pgadmin.cdeadmin.endpoints import registration_profiles
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


def navigator_engine_id(engine_id):
    """Collapse protocol/interface profiles under their logical engine."""
    if engine_id == 'opensearch_sql_ppl':
        return 'opensearch'
    return engine_id


def supported_engine_types():
    """Return every active logical engine, including empty navigator roots."""
    active = {
        navigator_engine_id(profile['engine_id'])
        for profile in registration_profiles()
    }
    return tuple(
        (engine_id, ENGINE_LABELS[engine_id])
        for engine_id in sorted(active, key=lambda item: ENGINE_LABELS[item])
    )


_EMBEDDED_MODULES = {
    'duckdb': 'duckdb',
    'sqlite': 'sqlite3',
}


def localhost_engine_status(engine_id):
    """Return a passive, credential-free local availability observation."""
    profiles = [
        profile for profile in registration_profiles()
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
        """Engine roots do not expose the generic show-node preference."""
        pass

    def get_nodes(self, gid, **_kwargs):
        for engine_id, label in supported_engine_types():
            yield self.generate_browser_node(
                engine_id, gid, label,
                f'icon-engine-type-{engine_id}', True, self.node_type,
                engine_id=engine_id,
                icon_key=f'engine.{engine_id}',
            )


blueprint = EngineTypeModule(__name__)


class EngineTypeNode(NodeView):
    node_type = EngineTypeModule._NODE_TYPE
    node_label = 'Engine type'
    parent_ids = [{'type': 'int', 'id': 'gid'}]
    ids = [{'type': 'string', 'id': 'eid'}]
    operations = {
        'nodes': [{'get': 'node'}, {'get': 'nodes'}],
    }

    @pga_login_required
    def nodes(self, gid, eid=None):
        engines = dict(supported_engine_types())
        if eid not in engines:
            return bad_request(errormsg=gettext(
                'The selected engine type is unavailable.'
            ))
        from pgadmin.browser.server_groups.servers import (
            blueprint as server_blueprint,
        )
        status = localhost_engine_status(eid)
        local_label = gettext('localhost')
        if not status['available']:
            local_label = gettext('localhost (not detected)')
        nodes = [self.blueprint.generate_browser_node(
            f'{eid}__localhost', f'engine_type_{eid}', local_label,
            f'icon-engine-type-{eid}' + (
                '' if status['available'] else '-unavailable'
            ),
            False, self.node_type,
            engine_id=eid,
            localhost_placeholder=True,
            localhost_available=status['available'],
            localhost_observation=status['observation'],
            localhost_ports=status.get('ports', []),
            localhost_listening_ports=status.get('listening_ports', []),
            cde_profile_ids=status['profile_ids'],
        )]
        nodes.extend(server_blueprint.engine_nodes(
            gid, eid, parent_id=f'engine_type_{eid}'
        ))
        return make_json_response(data=nodes)

    @pga_login_required
    def node(self, gid, eid):
        engines = dict(supported_engine_types())
        if eid not in engines:
            return bad_request(errormsg=gettext(
                'The selected engine type is unavailable.'
            ))
        return make_json_response(data=self.blueprint.generate_browser_node(
            eid, gid, engines[eid], f'icon-engine-type-{eid}', True,
            self.node_type, engine_id=eid, icon_key=f'engine.{eid}',
        ))


EngineTypeNode.register_node_view(blueprint)
