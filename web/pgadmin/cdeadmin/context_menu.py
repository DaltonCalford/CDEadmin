##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-owned Object Explorer context-action descriptors.

The explorer must not infer database operations from a generic node type.
These small, JSON-safe descriptors are resolved on the server from the
registered provider profile and the provider's admitted visual-admin catalog.
The browser only dispatches the finite handler names declared here.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


CONTEXT_ACTION_SCHEMA = 'cdeadmin.context-action.v1'
_COMMAND_ID = re.compile(r'^[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*$')
_HANDLERS = frozenset({
    'clear_credentials',
    'connector_info',
    'edit_endpoint',
    'forget_endpoint',
    'open_workspace',
    'refresh_node',
    'register_endpoint',
    'verify_endpoint',
})
_MUTATION_CLASSES = frozenset({
    'none', 'read', 'write', 'admin', 'destructive',
})


class ContextActionError(ValueError):
    """A provider context-action descriptor is invalid."""


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContextActionError(f'{name} must not be empty')
    return value.strip()


@dataclass(frozen=True)
class ContextAction:
    """One admitted explorer command with a finite browser dispatch target."""

    command_id: str
    label: str
    handler: str
    icon_key: str = 'command.default'
    description: str = ''
    menu_group: str = 'common'
    priority: int = 100
    mutation_class: str = 'read'
    arguments: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True
    disabled_reason: str = ''
    requires_confirmation: bool = False
    macro_callable: bool = True

    def __post_init__(self):
        command_id = _required(self.command_id, 'command_id')
        if not _COMMAND_ID.fullmatch(command_id):
            raise ContextActionError('command_id is invalid')
        object.__setattr__(self, 'command_id', command_id)
        object.__setattr__(self, 'label', _required(self.label, 'label'))
        handler = _required(self.handler, 'handler')
        if handler not in _HANDLERS:
            raise ContextActionError('handler is not admitted')
        object.__setattr__(self, 'handler', handler)
        object.__setattr__(
            self, 'icon_key', _required(self.icon_key, 'icon_key')
        )
        object.__setattr__(
            self, 'menu_group', _required(self.menu_group, 'menu_group')
        )
        if isinstance(self.priority, bool) or not isinstance(
            self.priority, int
        ):
            raise ContextActionError('priority must be an integer')
        if self.mutation_class not in _MUTATION_CLASSES:
            raise ContextActionError('mutation_class is invalid')
        if not isinstance(self.arguments, Mapping):
            raise ContextActionError('arguments must be an object')

    def to_dict(self) -> dict[str, Any]:
        return {
            'schema': CONTEXT_ACTION_SCHEMA,
            'command_id': self.command_id,
            'command_version': 1,
            'label': self.label,
            'description': self.description,
            'handler': self.handler,
            'icon_key': self.icon_key,
            'menu_group': self.menu_group,
            'priority': self.priority,
            'mutation_class': self.mutation_class,
            'arguments': copy.deepcopy(dict(self.arguments)),
            'enabled': self.enabled,
            'disabled_reason': self.disabled_reason,
            'requires_confirmation': self.requires_confirmation,
            'macro_callable': self.macro_callable,
        }


def _action(command_id, label, handler, *, icon='command.default',
            description='', group='common', priority=100,
            mutation='read', arguments=None, enabled=True,
            disabled_reason='', confirmation=False, macro=True):
    return ContextAction(
        command_id=command_id,
        label=label,
        handler=handler,
        icon_key=icon,
        description=description,
        menu_group=group,
        priority=priority,
        mutation_class=mutation,
        arguments=arguments or {},
        enabled=enabled,
        disabled_reason=disabled_reason,
        requires_confirmation=confirmation,
        macro_callable=macro,
    ).to_dict()


def _engine_profiles(profiles: Sequence[Mapping[str, Any]]):
    return sorted(
        profiles,
        key=lambda item: (
            str(item.get('interface_display_name', '')),
            str(item.get('profile_id', '')),
        ),
    )


def connector_context_actions(
        engine_id: str, profiles: Sequence[Mapping[str, Any]], *,
        status: Mapping[str, Any] | None = None,
        localhost_placeholder: bool = False) -> list[dict[str, Any]]:
    """Resolve connector-root or passive localhost actions."""
    engine_id = _required(engine_id, 'engine_id')
    ordered = _engine_profiles(profiles)
    available = not localhost_placeholder or bool(
        (status or {}).get('available')
    )
    unavailable_reason = (
        '' if available else 'No local driver or default-port listener '
        'was detected.'
    )
    actions = []
    for index, profile in enumerate(ordered):
        profile_id = profile['profile_id']
        display = profile.get('interface_display_name') or profile[
            'display_name'
        ]
        base_arguments = {
            'profile_id': profile_id,
            'host': 'localhost' if profile['route_kind'] == 'network' else '',
            'form_id': profile['form_contract']['server']['forms'][
                'define'
            ]['form_id'],
        }
        if profile['route_kind'] == 'embedded_file':
            actions.extend([
                _action(
                    f'connector.{engine_id}.create_database',
                    f'Create new {display} database file...',
                    'register_endpoint', icon='action.create_database',
                    group='register', priority=10 + index * 10,
                    mutation='write', arguments={
                        **base_arguments,
                        'registration_intent': 'create_database',
                    },
                ),
                _action(
                    f'connector.{engine_id}.register_database',
                    f'Open existing {display} database file...',
                    'register_endpoint', icon='action.attach',
                    group='register', priority=11 + index * 10,
                    arguments={
                        **base_arguments,
                        'registration_intent': 'register_existing',
                    },
                ),
            ])
        else:
            action_label = f'Register {display} server / instance...'
            if localhost_placeholder:
                action_label = f'Register detected local {display} endpoint...'
            actions.append(_action(
                f'connector.{engine_id}.register_endpoint.{profile_id}',
                action_label, 'register_endpoint', icon='action.connect',
                group='register', priority=10 + index,
                mutation='admin', arguments={
                    **base_arguments,
                    'registration_intent': 'endpoint',
                }, enabled=available,
                disabled_reason=unavailable_reason,
            ))
            if engine_id == 'firebird':
                actions.extend([
                    _action(
                        'connector.firebird.create_database',
                        'Create a Firebird database through this listener...',
                        'register_endpoint', icon='action.create_database',
                        group='register', priority=20,
                        mutation='write', arguments={
                            **base_arguments,
                            'registration_intent': 'create_database',
                        }, enabled=available,
                        disabled_reason=unavailable_reason,
                    ),
                    _action(
                        'connector.firebird.register_database',
                        'Register an existing Firebird database...',
                        'register_endpoint', icon='action.attach',
                        group='register', priority=21,
                        mutation='admin', arguments={
                            **base_arguments,
                            'registration_intent': 'register_existing',
                        }, enabled=available,
                        disabled_reason=unavailable_reason,
                    ),
                ])
    info = {
        'engine_id': engine_id,
        'profile_ids': [item['profile_id'] for item in ordered],
        'localhost': copy.deepcopy(dict(status or {})),
    }
    actions.extend([
        _action(
            f'connector.{engine_id}.information',
            'Connector and local component information...',
            'connector_info', icon='action.about', group='information',
            priority=80, arguments=info,
        ),
        _action(
            f'connector.{engine_id}.refresh', 'Refresh connector...',
            'refresh_node', icon='action.refresh', group='information',
            priority=90, arguments={'reload_parent': localhost_placeholder},
        ),
    ])
    return actions


_ENDPOINT_WORKSPACES = {
    'firebird': (
        ('resources', 'Browse Firebird objects...', 'action.view'),
        ('studio', 'Open Firebird SQL Studio...', 'action.script'),
        ('data', 'Browse and edit Firebird data...', 'action.edit'),
        ('administration', 'Firebird object administration...',
         'action.settings'),
        ('operations', 'Firebird service, health, and maintenance...',
         'tool.dashboard'),
        ('movement', 'Firebird import, export, and bulk data...',
         'action.import'),
    ),
    'mysql': (
        ('resources', 'Browse MySQL objects...', 'action.view'),
        ('studio', 'Open MySQL SQL Studio...', 'action.script'),
        ('data', 'Browse and edit MySQL data...', 'action.edit'),
        ('administration', 'MySQL users, roles, grants, and objects...',
         'action.security'),
        ('operations', 'MySQL server status, replication, and diagnostics...',
         'tool.dashboard'),
        ('movement', 'MySQL import, export, and bulk data...',
         'action.import'),
    ),
    'sqlite': (
        ('resources', 'Browse SQLite schema...', 'action.view'),
        ('studio', 'Open SQLite SQL Studio...', 'action.script'),
        ('data', 'Browse and edit SQLite rows...', 'action.edit'),
        ('administration', 'SQLite schema administration...',
         'action.settings'),
        ('operations', 'SQLite PRAGMA, integrity, and maintenance...',
         'tool.dashboard'),
        ('movement', 'SQLite import, export, and bulk data...',
         'action.import'),
    ),
    'duckdb': (
        ('resources', 'Browse DuckDB catalogs and objects...', 'action.view'),
        ('studio', 'Open DuckDB SQL Studio...', 'action.script'),
        ('data', 'Browse and edit DuckDB data...', 'action.edit'),
        ('administration', 'DuckDB objects and extensions...',
         'action.settings'),
        ('operations', 'DuckDB storage, settings, and diagnostics...',
         'tool.dashboard'),
        ('semantic', 'Open DuckDB cubes and semantic models...',
         'tool.report'),
        ('movement', 'DuckDB import, export, and bulk data...',
         'action.import'),
    ),
    'mariadb': (
        ('resources', 'Browse MariaDB objects...', 'action.view'),
        ('studio', 'Open MariaDB SQL Studio...', 'action.script'),
        ('data', 'Browse and edit MariaDB data...', 'action.edit'),
        ('administration',
         'MariaDB users, roles, grants, plugins, and objects...',
         'action.security'),
        ('operations',
         'MariaDB status, replication, events, and diagnostics...',
         'tool.dashboard'),
        ('movement', 'MariaDB import, export, and bulk data...',
         'action.import'),
    ),
    'cockroachdb': (
        ('resources', 'Browse CockroachDB databases and objects...',
         'action.view'),
        ('studio', 'Open CockroachDB SQL Studio...', 'action.script'),
        ('data', 'Browse and edit CockroachDB data...', 'action.edit'),
        ('administration',
         'CockroachDB schemas, grants, zones, and changefeeds...',
         'action.settings'),
        ('operations',
         'CockroachDB nodes, ranges, jobs, and diagnostics...',
         'tool.dashboard'),
        ('movement', 'CockroachDB import, export, and backup data...',
         'action.import'),
    ),
    'tidb': (
        ('resources', 'Browse TiDB databases and objects...', 'action.view'),
        ('studio', 'Open TiDB SQL Studio...', 'action.script'),
        ('data', 'Browse and edit TiDB data...', 'action.edit'),
        ('administration',
         'TiDB users, grants, placement, and CDC objects...',
         'action.settings'),
        ('operations',
         'TiDB topology, regions, DDL jobs, and diagnostics...',
         'tool.dashboard'),
        ('movement', 'TiDB import, export, backup, and restore...',
         'action.import'),
    ),
    'dolt': (
        ('resources', 'Browse Dolt repositories and relational objects...',
         'action.view'),
        ('studio', 'Open Dolt SQL and version-control Studio...',
         'action.script'),
        ('data', 'Browse and edit Dolt working-set data...', 'action.edit'),
        ('administration',
         'Dolt branches, tags, commits, remotes, and objects...',
         'action.settings'),
        ('operations',
         'Dolt status, history, diffs, conflicts, and diagnostics...',
         'tool.dashboard'),
        ('movement', 'Dolt clone, fetch, push, import, and export...',
         'action.import'),
    ),
    'vitess': (
        ('resources',
         'Browse Vitess cells, keyspaces, shards, and tablets...',
         'action.view'),
        ('studio', 'Open Vitess SQL Studio...', 'action.script'),
        ('data', 'Browse and edit Vitess-routed data...', 'action.edit'),
        ('administration',
         'Vitess VSchema, routing, workflows, and online DDL...',
         'action.settings'),
        ('operations',
         'Vitess topology, reparenting, tablets, and health...',
         'tool.dashboard'),
        ('movement', 'Vitess reshard, MoveTables, backup, and restore...',
         'action.import'),
    ),
    'yugabytedb': (
        ('resources',
         'Browse YugabyteDB YSQL databases and objects...', 'action.view'),
        ('studio', 'Open YugabyteDB YSQL Studio...', 'action.script'),
        ('data', 'Browse and edit YugabyteDB YSQL data...', 'action.edit'),
        ('administration',
         'YSQL schemas, grants, placement, and CDC objects...',
         'action.settings'),
        ('operations',
         'YugabyteDB nodes, tablets, placement, and replication...',
         'tool.dashboard'),
        ('movement',
         'YugabyteDB YSQL import, export, backup, and restore...',
         'action.import'),
    ),
    'yugabytedb-ycql': (
        ('resources',
         'Browse YugabyteDB YCQL keyspaces, tables, and indexes...',
         'action.view'),
        ('studio', 'Open YugabyteDB YCQL Studio...', 'action.script'),
        ('data', 'Browse and edit YugabyteDB YCQL partition data...',
         'action.edit'),
        ('administration',
         'YCQL keyspaces, types, roles, grants, and placement...',
         'action.settings'),
        ('operations',
         'YugabyteDB nodes, tablets, schema agreement, and health...',
         'tool.dashboard'),
        ('movement',
         'YugabyteDB YCQL import, export, backup, and restore...',
         'action.import'),
    ),
    'immudb': (
        ('resources',
         'Browse immudb SQL, document, and key/value resources...',
         'action.view'),
        ('studio', 'Open immudb SQL and native command Studio...',
         'action.script'),
        ('data', 'Browse immudb values and immutable history...',
         'action.edit'),
        ('administration',
         'immudb databases, users, permissions, and objects...',
         'action.security'),
        ('operations',
         'immudb health, replication, proofs, and transactions...',
         'tool.dashboard'),
        ('movement', 'immudb verified export and backup data...',
         'action.export'),
    ),
    'mongodb': (
        ('resources',
         'Browse MongoDB topology, databases, and collections...',
         'action.view'),
        ('studio',
         'Open MongoDB query and aggregation pipeline Studio...',
         'action.script'),
        ('data', 'Browse and edit MongoDB documents...', 'action.edit'),
        ('administration',
         'MongoDB collections, validation, indexes, users, and roles...',
         'action.settings'),
        ('operations',
         'MongoDB replica sets, sharding, profiler, and operations...',
         'tool.dashboard'),
        ('movement', 'MongoDB import, export, dump, and restore...',
         'action.import'),
    ),
    'cassandra': (
        ('resources',
         'Browse Cassandra datacenters, keyspaces, and objects...',
         'action.view'),
        ('studio', 'Open Cassandra CQL Studio...', 'action.script'),
        ('data', 'Browse and edit Cassandra partition data...',
         'action.edit'),
        ('administration',
         'Cassandra keyspaces, types, functions, roles, and grants...',
         'action.settings'),
        ('operations',
         'Cassandra ring, repair, compaction, snapshots, and health...',
         'tool.dashboard'),
        ('movement', 'Cassandra import, export, and snapshot data...',
         'action.import'),
    ),
    'xtdb': (
        ('resources', 'Browse XTDB nodes and temporal resources...',
         'action.view'),
        ('studio', 'Open XTDB SQL and XTQL Studio...', 'action.script'),
        ('data',
         'Browse XTDB documents, entities, and temporal history...',
         'action.edit'),
        ('administration',
         'XTDB transactions and native data administration...',
         'action.settings'),
        ('operations',
         'XTDB health, transaction log, storage, and checkpoints...',
         'tool.dashboard'),
        ('movement', 'XTDB temporal export and reproducible results...',
         'action.export'),
    ),
    'neo4j': (
        ('resources',
         'Browse Neo4j databases, graph schema, and cluster members...',
         'action.view'),
        ('studio', 'Open Neo4j Cypher Studio...', 'action.script'),
        ('data', 'Browse and edit graph nodes and relationships...',
         'action.edit'),
        ('administration',
         'Neo4j graph schema, projections, users, and privileges...',
         'action.settings'),
        ('operations',
         'Neo4j topology, queries, transactions, CDC, and health...',
         'tool.dashboard'),
        ('movement', 'Neo4j import, export, dump, and restore...',
         'action.import'),
    ),
    'redis': (
        ('resources',
         'Browse Redis deployments, logical databases, and keys...',
         'action.view'),
        ('studio', 'Open Redis native command Studio...',
         'action.terminal'),
        ('data', 'Browse and edit Redis values by native type...',
         'action.edit'),
        ('administration',
         'Redis TTL, streams, Pub/Sub, consumer groups, modules, and ACLs...',
         'action.settings'),
        ('operations',
         'Redis replication, Sentinel, Cluster, persistence, and health...',
         'tool.dashboard'),
        ('movement', 'Redis import, export, dump, and restore...',
         'action.import'),
    ),
    'foundationdb': (
        ('resources',
         'Browse FoundationDB tenants, directories, ranges, and keys...',
         'action.view'),
        ('studio', 'Open FoundationDB transaction workspace...',
         'action.terminal'),
        ('data', 'Browse and edit FoundationDB binary keys and values...',
         'action.edit'),
        ('administration',
         'FoundationDB tenants, directories, and coordinators...',
         'action.settings'),
        ('operations',
         'FoundationDB processes, regions, data distribution, and health...',
         'tool.dashboard'),
        ('movement', 'FoundationDB backup, restore, DR, and export...',
         'action.backup'),
    ),
    'tikv': (
        ('resources',
         'Browse TiKV stores, regions, peers, keyspaces, and ranges...',
         'action.view'),
        ('studio', 'Open TiKV raw and transactional key workspace...',
         'action.terminal'),
        ('data', 'Browse and edit TiKV byte-safe key/value data...',
         'action.edit'),
        ('administration',
         'TiKV placement rules, schedulers, operators, and keyspaces...',
         'action.settings'),
        ('operations',
         'TiKV PD leadership, stores, regions, hot spots, and health...',
         'tool.dashboard'),
        ('movement', 'TiKV backup, restore, import, and export...',
         'action.backup'),
    ),
    'apache_ignite': (
        ('resources',
         'Browse Ignite clusters, caches, schemas, and services...',
         'action.view'),
        ('studio', 'Open Ignite SQL and cache query Studio...',
         'action.script'),
        ('data', 'Browse Ignite cache entries and relational data...',
         'action.edit'),
        ('administration',
         'Ignite caches, baseline topology, services, and tasks...',
         'action.settings'),
        ('operations',
         'Ignite nodes, partitions, rebalance, snapshots, and health...',
         'tool.dashboard'),
        ('movement', 'Ignite snapshot, restore, import, and export...',
         'action.backup'),
    ),
    'opensearch': (
        ('resources',
         'Browse OpenSearch indices, mappings, aliases, and shards...',
         'action.view'),
        ('studio', 'Open OpenSearch query Studio...', 'action.search'),
        ('data', 'Search and edit indexed documents...', 'action.edit'),
        ('administration',
         'OpenSearch templates, pipelines, security, and snapshots...',
         'action.settings'),
        ('operations',
         'OpenSearch cluster health, allocation, tasks, and profiling...',
         'tool.dashboard'),
        ('movement', 'OpenSearch reindex, bulk, snapshot, and restore...',
         'action.import'),
    ),
    'opensearch_sql_ppl': (
        ('resources', 'Browse OpenSearch SQL/PPL catalogues...',
         'action.view'),
        ('studio', 'Open OpenSearch SQL and PPL Studio...',
         'action.script'),
        ('data', 'Search SQL/PPL result data...', 'action.search'),
        ('operations',
         'OpenSearch SQL/PPL capabilities, explain, and profiling...',
         'tool.dashboard'),
        ('movement', 'Export and compare SQL/PPL results...',
         'action.export'),
    ),
    'clickhouse': (
        ('resources',
         'Browse ClickHouse clusters, tables, parts, and projections...',
         'action.view'),
        ('studio', 'Open ClickHouse SQL Studio...', 'action.script'),
        ('data', 'Browse ClickHouse columnar data...', 'action.edit'),
        ('administration',
         'ClickHouse engines, dictionaries, policies, and objects...',
         'action.settings'),
        ('operations',
         'ClickHouse replicas, mutations, merges, queues, and health...',
         'tool.dashboard'),
        ('semantic', 'Open ClickHouse cubes and semantic models...',
         'tool.report'),
        ('movement',
         'ClickHouse partitions, backup, restore, import, and export...',
         'action.import'),
    ),
    'influxdb': (
        ('resources',
         'Browse InfluxDB databases, measurements, tags, and fields...',
         'action.view'),
        ('studio', 'Open InfluxDB SQL and InfluxQL Studio...',
         'action.script'),
        ('data', 'Explore time-series measurements and retention...',
         'action.edit'),
        ('administration',
         'InfluxDB caches, processing triggers, plugins, and tokens...',
         'action.settings'),
        ('operations',
         'InfluxDB nodes, storage, compaction, metrics, and health...',
         'tool.dashboard'),
        ('semantic', 'Open time-series models, charts, and dashboards...',
         'tool.report'),
        ('movement', 'InfluxDB line protocol import and export...',
         'action.import'),
    ),
    'milvus': (
        ('resources',
         'Browse Milvus databases, collections, partitions, and indexes...',
         'action.view'),
        ('studio', 'Open Milvus query and vector-search Studio...',
         'action.search'),
        ('data', 'Browse vectors, scalar fields, and metadata...',
         'action.edit'),
        ('administration',
         'Milvus collections, aliases, indexes, users, and privileges...',
         'action.settings'),
        ('operations',
         'Milvus load state, replicas, compaction, jobs, and health...',
         'tool.dashboard'),
        ('semantic', 'Open vector analytical models and comparisons...',
         'tool.report'),
        ('movement', 'Milvus bulk import, backup, and export...',
         'action.import'),
    ),
}


def endpoint_context_actions(
        profile: Mapping[str, Any], verification_state: str, *,
        is_password_saved: bool = False,
        can_manage: bool = True) -> list[dict[str, Any]]:
    """Resolve an endpoint's menus without any PostgreSQL inheritance."""
    engine_id = _required(profile.get('engine_id'), 'engine_id')
    ready = verification_state == 'verified'
    blocked = '' if ready else 'Verify this endpoint before using it.'
    display = profile.get('engine_display_name') or profile['display_name']
    database_forms = profile['form_contract']['database']
    database_noun = database_forms['noun']
    create_form = database_forms['forms']['create']
    actions = [_action(
        f'endpoint.{engine_id}.verify',
        f'Verify {display} endpoint...', 'verify_endpoint',
        icon='action.connect', group='connection', priority=10,
        arguments={'profile_id': profile['profile_id']},
    )]
    actions.extend([
        _action(
            f'endpoint.{engine_id}.create_database',
            f'Create {display} {database_noun}...', 'open_workspace',
            icon='action.create_database', group='database', priority=20,
            mutation='write', arguments={
                'tab': 'connections', 'database_mode': 'create',
                'form_id': create_form['form_id'],
            }, enabled=ready and create_form['supported'], disabled_reason=(
                blocked if not ready else create_form['disabled_reason'] or ''
            ),
        ),
        _action(
            f'endpoint.{engine_id}.register_database',
            f'Register existing {display} {database_noun}...',
            'open_workspace', icon='action.attach', group='database',
            priority=21, mutation='admin', arguments={
                'tab': 'connections', 'database_mode': 'define',
                'form_id': database_forms['forms']['define']['form_id'],
            }, enabled=ready, disabled_reason=blocked,
        ),
    ])
    if profile.get('form_contract') and profile.get('route_kind') == 'network':
        actions.append(_action(
            f'endpoint.{engine_id}.routes',
            f'{display} databases and connection routes...',
            'open_workspace', icon='action.settings', group='connection',
            priority=70, arguments={'tab': 'connections'}, enabled=ready,
            disabled_reason=blocked,
        ))
    if engine_id == 'mariadb':
        actions.append(_action(
            'endpoint.mariadb.check_upgrade_required',
            'Check MariaDB upgrade requirement...', 'open_workspace',
            icon='action.validate', group='maintenance', priority=71,
            arguments={
                'tab': 'administration', 'resource_kind': 'server',
                'operation_id': 'check_upgrade_required',
            }, enabled=ready, disabled_reason=blocked,
        ))
    if can_manage:
        server_forms = profile['form_contract']['server']['forms']
        actions.append(_action(
            f'endpoint.{engine_id}.properties',
            'Edit endpoint properties...', 'open_workspace',
            icon='action.properties', group='endpoint', priority=80,
            mutation='admin', arguments={
                'tab': 'connections', 'server_mode': 'edit',
                'form_id': server_forms['edit']['form_id'],
            }, macro=False,
        ))
        if is_password_saved:
            actions.append(_action(
                f'endpoint.{engine_id}.clear_credentials',
                'Clear saved credentials...', 'clear_credentials',
                icon='action.security', group='endpoint', priority=81,
                mutation='admin', confirmation=True, macro=False,
            ))
        actions.append(_action(
            f'endpoint.{engine_id}.forget', 'Remove endpoint registration...',
            'open_workspace', icon='action.delete', group='endpoint',
            priority=90, mutation='destructive', arguments={
                'tab': 'connections', 'server_mode': 'remove',
                'form_id': server_forms['remove']['form_id'],
            }, confirmation=False,
            macro=False,
        ))
    return actions


def database_target_context_actions(
        profile: Mapping[str, Any], target: Mapping[str, Any]) -> list[
            dict[str, Any]]:
    """Resolve actions for one provider-qualified database target."""
    engine_id = _required(profile.get('engine_id'), 'engine_id')
    target_id = _required(target.get('target_id'), 'target_id')
    base = {
        'resource_id': target_id,
        'database_target_id': target_id,
    }
    contract = profile['form_contract']['database']
    noun = contract['noun']
    exact_labels = {
        'firebird': (
            ('properties', 'Firebird database properties...',
             'action.properties'),
            ('resources', 'Browse Firebird database objects...',
             'action.view'),
            ('studio', 'Open Firebird SQL editor...', 'action.script'),
            ('data', 'Browse and edit Firebird table data...', 'action.edit'),
        ),
        'sqlite': (
            ('properties', 'SQLite database file properties...',
             'action.properties'),
            ('resources', 'Browse SQLite database objects...', 'action.view'),
            ('studio', 'Open SQLite SQL editor...', 'action.script'),
            ('data', 'Browse and edit SQLite rows...', 'action.edit'),
        ),
        'duckdb': (
            ('properties', 'DuckDB database file properties...',
             'action.properties'),
            ('resources', 'Browse DuckDB database objects...',
             'action.view'),
            ('studio', 'Open DuckDB SQL editor...', 'action.script'),
            ('data', 'Browse and edit DuckDB rows...', 'action.edit'),
        ),
        'mysql': (
            ('properties', 'MySQL database properties...',
             'action.properties'),
            ('resources', 'Browse MySQL database objects...',
             'action.view'),
            ('studio', 'Open MySQL SQL editor...', 'action.script'),
            ('data', 'Browse and edit MySQL table data...', 'action.edit'),
        ),
        'mariadb': (
            ('properties', 'MariaDB database properties...',
             'action.properties'),
            ('resources', 'Browse MariaDB database objects...',
             'action.view'),
            ('studio', 'Open MariaDB SQL editor...', 'action.script'),
            ('data', 'Browse and edit MariaDB table data...', 'action.edit'),
        ),
    }
    rows = exact_labels.get(engine_id, (
        ('properties', f'{noun.capitalize()} properties...',
         'action.properties'),
        ('resources', f'Browse {noun} objects...', 'action.view'),
        ('studio', f'Open {noun} query workspace...', 'action.script'),
        ('data', f'Browse and edit {noun} data...', 'action.edit'),
    ))
    actions = [_action(
        f'database.{engine_id}.{tab}', label, 'open_workspace',
        icon=icon, group='database', priority=10 + index,
        arguments={**base, 'tab': tab},
    ) for index, (tab, label, icon) in enumerate(rows)]
    action_specs = (
        ('connect', f'Connect to {noun}...', 'action.connect', 'admin'),
        ('edit', f'Edit {noun} connection...', 'action.properties', 'admin'),
        ('alter', f'Alter {noun}...', 'action.edit', 'admin'),
        ('drop', f'Drop {noun}...', 'action.delete', 'destructive'),
        ('remove', f'Remove {noun} registration...', 'action.delete',
         'destructive'),
    )
    if engine_id == 'sqlite':
        action_specs = (
            ('connect', 'Open SQLite database file...', 'action.connect',
             'admin'),
            ('edit', 'Edit SQLite file connection...', 'action.properties',
             'admin'),
            ('alter', 'Configure SQLite database file...', 'action.edit',
             'admin'),
            ('drop', 'Delete SQLite database file...', 'action.delete',
             'destructive'),
            ('remove', 'Remove SQLite file registration...',
             'action.delete', 'destructive'),
        )
    elif engine_id == 'duckdb':
        action_specs = (
            ('connect', 'Open DuckDB database file...', 'action.connect',
             'admin'),
            ('edit', 'Edit DuckDB file connection...', 'action.properties',
             'admin'),
            ('alter', 'Configure DuckDB database file...', 'action.edit',
             'admin'),
            ('drop', 'Delete DuckDB database file...', 'action.delete',
             'destructive'),
            ('remove', 'Remove DuckDB file registration...',
             'action.delete', 'destructive'),
        )
    for offset, (operation_id, label, icon, mutation) in enumerate(
        action_specs
    ):
        form = contract['forms'][operation_id]
        actions.append(_action(
            f'database.{engine_id}.{operation_id}', label, 'open_workspace',
            icon=icon, group='database-lifecycle', priority=30 + offset,
            mutation=mutation, arguments={
                **base, 'tab': 'connections',
                'database_mode': operation_id,
                'form_id': form['form_id'],
            }, enabled=form['supported'],
            disabled_reason=form['disabled_reason'] or '',
            confirmation=False,
        ))
    if engine_id == 'firebird':
        security_tasks = (
            ('user', 'Manage Firebird users...', 'action.security'),
            ('role', 'Manage Firebird roles...', 'action.security'),
            ('privilege', 'Manage Firebird grants and privileges...',
             'action.security'),
        )
        for offset, (resource_kind, label, icon) in enumerate(
                security_tasks):
            actions.append(_action(
                f'database.firebird.security.{resource_kind}', label,
                'open_workspace', icon=icon, group='security',
                priority=45 + offset, mutation='admin', arguments={
                    **base, 'tab': 'administration',
                    'resource_kind': resource_kind,
                },
            ))
        tasks = (
            ('backup_logical', 'Logical backup (gbak)...',
             'action.backup', 'admin', 'backup'),
            ('restore_logical', 'Logical restore (gbak)...',
             'action.restore', 'destructive', 'restore'),
            ('backup_physical', 'Physical backup (nbackup)...',
             'action.backup', 'admin', 'backup'),
            ('restore_physical', 'Physical restore (nbackup)...',
             'action.restore', 'destructive', 'restore'),
            ('database_statistics', 'Database statistics (gstat)...',
             'tool.dashboard', 'read', 'diagnostics'),
            ('validate_database', 'Validate database...',
             'action.check', 'admin', 'maintenance'),
            ('repair_database', 'Repair database (gfix)...',
             'action.settings', 'destructive', 'maintenance'),
            ('sweep_database', 'Sweep database...',
             'action.refresh', 'admin', 'maintenance'),
            ('shutdown_database', 'Shut down database...',
             'action.disconnect', 'destructive', 'availability'),
            ('bring_online', 'Bring database online...',
             'action.connect', 'admin', 'availability'),
            ('set_page_cache_size', 'Set page cache size...',
             'action.settings', 'admin', 'maintenance'),
            ('set_sweep_interval', 'Set automatic sweep interval...',
             'action.settings', 'admin', 'maintenance'),
            ('set_space_reservation', 'Set page space reservation...',
             'action.settings', 'admin', 'maintenance'),
            ('set_write_mode', 'Set database write mode...',
             'action.settings', 'admin', 'maintenance'),
            ('set_access_mode', 'Set database access mode...',
             'action.settings', 'admin', 'availability'),
            ('set_sql_dialect', 'Set database SQL dialect...',
             'action.settings', 'admin', 'maintenance'),
            ('activate_shadow', 'Activate database shadow...',
             'action.settings', 'destructive', 'availability'),
            ('remove_linger', 'Remove linger for next attachment...',
             'action.settings', 'admin', 'maintenance'),
            ('fixup_database', 'Fix up after filesystem copy...',
             'action.settings', 'destructive', 'maintenance'),
            ('set_replica_mode', 'Set replica mode...',
             'action.settings', 'admin', 'availability'),
            ('upgrade_database', 'Upgrade database ODS...',
             'action.settings', 'destructive', 'maintenance'),
        )
        for offset, (operation_id, label, icon, mutation, group) in enumerate(
                tasks):
            actions.append(_action(
                f'database.firebird.{operation_id}', label,
                'open_workspace', icon=icon, group=group,
                priority=50 + offset, mutation=mutation,
                arguments={
                    **base, 'tab': 'administration',
                    'resource_kind': 'database',
                    'operation_id': operation_id,
                }, confirmation=mutation == 'destructive',
                macro=mutation != 'destructive',
            ))
    if engine_id == 'sqlite':
        tasks = (
            ('backup', 'Online backup...', 'action.backup', 'admin',
             'backup'),
            ('restore', 'Restore from backup...', 'action.restore',
             'destructive', 'restore'),
            ('integrity_check', 'Integrity check...', 'action.check', 'read',
             'diagnostics'),
            ('quick_check', 'Quick integrity check...', 'action.check',
             'read', 'diagnostics'),
            ('foreign_key_check', 'Foreign-key check...', 'action.check',
             'read', 'diagnostics'),
            ('vacuum', 'Vacuum database...', 'action.refresh', 'admin',
             'maintenance'),
            ('incremental_vacuum', 'Incremental vacuum...',
             'action.refresh', 'admin', 'maintenance'),
            ('optimize', 'Optimize query planner...', 'action.settings',
             'admin', 'maintenance'),
            ('analyze', 'Analyze statistics...', 'tool.dashboard', 'admin',
             'maintenance'),
            ('reindex', 'Rebuild indexes...', 'action.refresh', 'admin',
             'maintenance'),
            ('wal_checkpoint', 'WAL checkpoint...', 'action.settings',
             'admin', 'maintenance'),
        )
        for offset, (operation_id, label, icon, mutation, group) in enumerate(
                tasks):
            actions.append(_action(
                f'database.sqlite.{operation_id}', label,
                'open_workspace', icon=icon, group=group,
                priority=50 + offset, mutation=mutation,
                arguments={
                    **base, 'tab': 'administration',
                    'resource_kind': 'database',
                    'operation_id': operation_id,
                }, confirmation=mutation == 'destructive',
                macro=mutation != 'destructive',
            ))
    if engine_id == 'duckdb':
        tasks = (
            ('checkpoint', 'Checkpoint database...', 'action.settings',
             'admin', 'maintenance'),
            ('force_checkpoint', 'Force checkpoint...', 'action.refresh',
             'admin', 'maintenance'),
            ('vacuum', 'Vacuum database...', 'action.refresh', 'admin',
             'maintenance'),
            ('analyze', 'Analyze statistics...', 'tool.dashboard', 'admin',
             'maintenance'),
            ('export_database', 'Export database...', 'action.backup',
             'admin', 'backup'),
            ('import_database', 'Import database...', 'action.restore',
             'admin', 'restore'),
        )
        for offset, (operation_id, label, icon, mutation, group) in enumerate(
                tasks):
            actions.append(_action(
                f'database.duckdb.{operation_id}', label,
                'open_workspace', icon=icon, group=group,
                priority=50 + offset, mutation=mutation,
                arguments={
                    **base, 'tab': 'administration',
                    'resource_kind': 'database',
                    'operation_id': operation_id,
                }, confirmation=False, macro=True,
            ))
    if engine_id == 'mysql':
        tasks = (
            ('backup_logical', 'Logical backup (MySQL Shell)...',
             'action.backup', 'admin', 'backup'),
            ('restore_logical', 'Logical restore (MySQL Shell)...',
             'action.restore', 'destructive', 'restore'),
            ('analyze_tables', 'Analyze tables and histograms...',
             'tool.dashboard', 'admin', 'maintenance'),
            ('check_tables', 'Check tables...', 'action.check', 'read',
             'maintenance'),
            ('optimize_tables', 'Optimize tables...', 'action.settings',
             'admin', 'maintenance'),
            ('repair_tables', 'Repair supported tables...',
             'action.settings', 'admin', 'maintenance'),
            ('checksum_tables', 'Checksum tables...', 'action.check',
             'read', 'maintenance'),
        )
        for offset, (operation_id, label, icon, mutation, group) in enumerate(
                tasks):
            actions.append(_action(
                f'database.mysql.{operation_id}', label,
                'open_workspace', icon=icon, group=group,
                priority=50 + offset, mutation=mutation,
                arguments={
                    **base, 'tab': 'administration',
                    'resource_kind': 'database',
                    'operation_id': operation_id,
                }, confirmation=mutation == 'destructive',
                macro=mutation != 'destructive',
            ))
    if engine_id == 'mariadb':
        tasks = (
            ('backup_logical', 'Logical backup (mariadb-dump)...',
             'action.backup', 'admin', 'backup'),
            ('restore_logical', 'Logical restore (mariadb client)...',
             'action.restore', 'destructive', 'restore'),
            ('analyze_tables', 'Analyze tables and persistent statistics...',
             'tool.dashboard', 'admin', 'maintenance'),
            ('check_objects', 'Check tables or views...', 'action.check',
             'read', 'maintenance'),
            ('optimize_tables', 'Optimize tables...', 'action.settings',
             'admin', 'maintenance'),
            ('repair_objects', 'Repair supported tables or views...',
             'action.settings', 'admin', 'maintenance'),
            ('checksum_tables', 'Checksum tables...', 'action.check',
             'read', 'maintenance'),
        )
        for offset, (operation_id, label, icon, mutation, group) in enumerate(
                tasks):
            actions.append(_action(
                f'database.mariadb.{operation_id}', label,
                'open_workspace', icon=icon, group=group,
                priority=50 + offset, mutation=mutation,
                arguments={
                    **base, 'tab': 'administration',
                    'resource_kind': 'database',
                    'operation_id': operation_id,
                }, confirmation=mutation == 'destructive',
                macro=mutation != 'destructive',
            ))
    return actions


def resource_group_context_actions(profile, resource_kind, visual_catalog,
                                   *, database_target_id=None,
                                   parent_resource=None, system=False):
    """Creation belongs to a presentation group, never a fabricated object."""
    if system:
        return []
    descriptor = next((item for item in
                       (visual_catalog or {}).get('objects', [])
                       if item.get('resource_kind') == resource_kind), None)
    if not descriptor:
        return []
    create = next((item for item in descriptor.get('operations', [])
                   if item.get('operation_id') == 'create' and
                   item.get('native_supported') is not False), None)
    if not create:
        return []
    arguments = {'tab': 'administration', 'operation_id': 'create',
                 'resource_kind': resource_kind}
    if database_target_id:
        arguments['database_target_id'] = database_target_id
    if parent_resource:
        arguments['parent_resource_id'] = parent_resource['resource_id']
    enabled = create.get('execution_available') is True
    noun = descriptor.get('title') or resource_kind.replace('-', ' ').title()
    return [_action(
        f"resource.{profile['engine_id']}.{resource_kind}.create",
        f'New {noun}', 'open_workspace', icon='action.create',
        group='common', priority=10, arguments=arguments,
        mutation=create.get('mutation_class', 'admin'), enabled=enabled,
        disabled_reason=', '.join(create.get('blockers') or []) or (
            '' if enabled else 'The provider has not admitted creation.'),
    )]


def resource_context_actions(
        profile: Mapping[str, Any], resource: Mapping[str, Any],
        visual_catalog: Mapping[str, Any] | None, *,
        database_target_id: str | None = None) -> list[dict[str, Any]]:
    """Resolve provider catalog operations for a discovered resource."""
    from pgadmin.cdeadmin.navigator import resource_native
    engine_id = _required(profile.get('engine_id'), 'engine_id')
    resource_id = _required(resource.get('resource_id'), 'resource_id')
    resource_kind = _required(
        resource.get('resource_kind'), 'resource_kind'
    )
    base = {'resource_id': resource_id}
    if database_target_id is not None:
        base['database_target_id'] = _required(
            database_target_id, 'database_target_id'
        )
    actions = []
    if database_target_id is not None:
        query_labels = {
            'firebird': 'Open Firebird SQL editor...',
            'mysql': 'Open MySQL SQL editor...',
            'mariadb': 'Open MariaDB SQL editor...',
            'sqlite': 'Open SQLite SQL editor...',
            'duckdb': 'Open DuckDB SQL editor...',
            'cassandra': 'Open Cassandra CQL editor...',
            'neo4j': 'Open Neo4j Cypher editor...',
            'opensearch_sql_ppl': 'Open OpenSearch SQL/PPL editor...',
            'influxdb': 'Open InfluxDB query editor...',
        }
        actions.append(_action(
            f'resource.{engine_id}.{resource_kind}.query',
            query_labels.get(
                engine_id,
                f"Open {profile.get('engine_display_name') or engine_id} "
                'query workspace...',
            ),
            'open_workspace', icon='action.script', group='object',
            priority=5, arguments={
                **base, 'tab': 'studio',
                'resource_kind': resource_kind,
            },
        ))
    descriptor = next((item for item in (
        (visual_catalog or {}).get('objects') or []
    ) if item.get('resource_kind') == resource_kind), None)
    if descriptor is None:
        # Query access is a database-scoped language capability, not a
        # visual-administration operation. Provider resources such as
        # Firebird MON$ metrics remain queryable without an object editor.
        return actions
    operations = [
        operation for operation in descriptor.get('operations', [])
        if operation.get('native_supported') is not False and
        (not resource_native(resource).get('system_object') or
         operation.get('operation_id') == 'inspect')
    ]
    inspect = next((
        operation for operation in operations
        if operation.get('operation_id') == 'inspect'
    ), None)
    if inspect is not None:
        execution_available = inspect.get('execution_available') is True
        blockers = ', '.join(inspect.get('blockers') or [])
        actions.append(_action(
            f'resource.{engine_id}.{resource_kind}.browse',
            f"Open {resource.get('display_name') or resource_kind}",
            'open_workspace', icon='action.view', group='object', priority=1,
            arguments={**base, 'tab': 'object', 'operation_id': 'inspect',
                       'resource_kind': resource_kind},
            enabled=execution_available,
            disabled_reason=blockers or (
                '' if execution_available else 'Object inspection unavailable.'
            ),
        ))
        actions.append(_action(
            f'resource.{engine_id}.{resource_kind}.inspect',
            inspect.get('title') or 'Inspect object...',
            'open_workspace', icon='action.view', group='object', priority=10,
            arguments={
                **base, 'tab': 'administration',
                'operation_id': 'inspect',
                'resource_kind': resource_kind,
            }, enabled=execution_available,
            disabled_reason=blockers or (
                '' if execution_available else
                'The provider has not admitted execution for this operation.'
            ),
        ))
    sections = descriptor.get('editor', {}).get('sections', [])
    if 'data' in sections:
        actions.append(_action(
            f'resource.{engine_id}.{resource_kind}.data',
            'Open object data...', 'open_workspace', icon='action.edit',
            group='object', priority=11,
            arguments={**base, 'tab': 'data'},
        ))
    for index, operation in enumerate(operations):
        operation_id = operation.get('operation_id')
        if (
            not isinstance(operation_id, str) or not operation_id or
            operation_id in {'inspect', 'create'}
        ):
            continue
        mutation = operation.get('mutation_class', 'admin')
        if mutation not in _MUTATION_CLASSES:
            mutation = 'admin'
        execution_available = operation.get('execution_available') is True
        blockers = ', '.join(operation.get('blockers') or [])
        label = f"{operation.get('title') or operation_id}..."
        if operation_id in {'alter', 'drop'}:
            noun = descriptor.get('title') or resource_kind.replace('-', ' ')
            label = (f'{operation_id.title()} {noun} '
                     f"{resource.get('display_name') or resource_id}")
        actions.append(_action(
            f'resource.{engine_id}.{resource_kind}.{operation_id}',
            label,
            'open_workspace', icon=(
                'action.delete' if mutation == 'destructive' else
                'action.create' if operation_id == 'create' else
                'action.edit'
            ), group=('common' if operation_id in {'alter', 'drop'}
                      else 'operations'), priority=30 + index,
            mutation=mutation, arguments={
                **base, 'tab': 'administration',
                'operation_id': operation_id,
                'resource_kind': resource_kind,
            }, enabled=execution_available,
            disabled_reason=blockers or (
                '' if execution_available else
                'The provider has not admitted execution for this operation.'
            ), confirmation=operation.get('confirmation_required') is True,
            macro=mutation != 'destructive',
        ))
    if resource_kind == 'database':
        existing = {action['command_id'] for action in actions}
        for child_index, child in enumerate(
                (visual_catalog or {}).get('objects') or []):
            child_kind = child.get('resource_kind')
            if child_kind == 'database' or not isinstance(child_kind, str):
                continue
            create = next((
                operation for operation in child.get('operations', [])
                if operation.get('operation_id') == 'create' and
                operation.get('target_required') is False and
                operation.get('native_supported') is not False
            ), None)
            if create is None:
                continue
            command_id = f'resource.{engine_id}.{child_kind}.create'
            if command_id in existing:
                continue
            mutation = create.get('mutation_class', 'admin')
            execution_available = create.get('execution_available') is True
            blockers = ', '.join(create.get('blockers') or [])
            actions.append(_action(
                command_id,
                f"{create.get('title') or 'Create'}...",
                'open_workspace', icon='action.create',
                group='create', priority=100 + child_index,
                mutation=(
                    mutation if mutation in _MUTATION_CLASSES else 'admin'
                ), arguments={
                    **base, 'tab': 'administration',
                    'operation_id': 'create',
                    'resource_kind': child_kind,
                    'target_resource': None,
                }, enabled=execution_available,
                disabled_reason=blockers or (
                    '' if execution_available else
                    'The provider has not admitted execution for this '
                    'operation.'
                ), macro=mutation != 'destructive',
            ))
    return actions
