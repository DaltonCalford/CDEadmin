##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-declared operational workspace projection.

This module classifies provider-owned resources and operations for navigation
and presentation only.  It never compiles commands, interprets provider state,
infers finality, or retries a mutation.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Iterable, Mapping


OPERATIONAL_WORKSPACE_SCHEMA = 'cdeadmin.operational-workspace.v1'


class OperationalWorkspaceError(RuntimeError):
    """A provider catalog cannot be projected safely."""


def _words(value):
    return frozenset(filter(None, re.split(r'[^a-z0-9]+', str(value).lower())))


@dataclass(frozen=True)
class WorkspaceFacet:
    facet_id: str
    title: str
    category: str
    summary: str
    resource_terms: frozenset[str]
    operation_terms: frozenset[str] = frozenset()
    distributed: bool = False


def _facet(facet_id, title, category, summary, resources, operations=(),
           distributed=False):
    return WorkspaceFacet(
        facet_id, title, category, summary, frozenset(resources),
        frozenset(operations), distributed,
    )


FACETS = (
    _facet('health', 'Server and cluster health', 'runtime',
           'Provider-reported availability and health state.',
           ('server', 'cluster', 'deployment', 'universe', 'health'),
           ('health', 'status', 'ping', 'check')),
    _facet('runtime-status', 'Process and runtime status', 'runtime',
           'Provider runtime, process, service, and daemon state.',
           ('process', 'runtime', 'service', 'server', 'node', 'tserver',
            'master', 'vtgate', 'vttablet'),
           ('status', 'inspect', 'ping', 'health')),
    _facet('nodes-members', 'Nodes and members', 'runtime',
           'Provider-declared members, nodes, servers, and replicas.',
           ('node', 'member', 'server', 'replica', 'store', 'tablet',
            'process', 'router', 'sentinel', 'coordinator', 'peer',
            'tserver', 'master', 'vtgate', 'vttablet')),
    _facet('replication', 'Replication', 'continuity',
           'Replication channels, replicas, streams, and replica sets.',
           ('replication', 'replica', 'publication', 'subscription',
            'changefeed', 'stream', 'workflow', 'peer'),
           ('replicate', 'replication', 'sync', 'promote', 'reparent')),
    _facet('failover', 'Failover', 'continuity',
           'Provider-planned failover, promotion, and election workflows.',
           ('replica', 'replication', 'sentinel', 'cluster', 'tablet',
            'master', 'peer'),
           ('failover', 'switchover', 'promote', 'reparent', 'elect',
            'stepdown', 'primary')),
    _facet('backups', 'Backups', 'continuity',
           'Backup and snapshot creation, inspection, and retention.',
           ('backup', 'snapshot', 'repository', 'persistence'),
           ('backup', 'snapshot', 'archive')),
    _facet('restore', 'Restore', 'continuity',
           'Provider restore and recovery workflows.',
           ('restore', 'backup', 'snapshot', 'repository'),
           ('restore', 'recover', 'recovery')),
    _facet('point-in-time-recovery', 'Point-in-time recovery', 'continuity',
           'Timestamp, log, snapshot, or provider-native recovery points.',
           ('restore', 'backup', 'snapshot', 'transaction-log',
            'persistence'),
           ('pitr', 'point', 'restore', 'recover')),
    _facet('compaction', 'Compaction', 'maintenance',
           'Provider compaction, merge, and storage reclamation controls.',
           ('compaction', 'partition', 'storage', 'table', 'index'),
           ('compact', 'compaction', 'merge', 'optimize')),
    _facet('vacuum-analyze', 'Vacuum and analyze equivalents', 'maintenance',
           'Provider-native cleanup, analyze, verification, and optimization.',
           ('statistics', 'table', 'index', 'database', 'partition'),
           ('vacuum', 'analyze', 'optimize', 'verify', 'cleanup')),
    _facet('reindexing', 'Reindexing', 'maintenance',
           'Index rebuild, validation, and reindex operations.',
           ('index', 'vector-index', 'data-skipping-index', 'projection'),
           ('reindex', 'rebuild', 'validate')),
    _facet('statistics', 'Statistics', 'observability',
           'Provider statistics, cardinality, and optimizer observations.',
           ('statistics', 'table', 'index', 'query-profile'),
           ('statistics', 'stats', 'analyze', 'profile')),
    _facet('storage-usage', 'Storage usage', 'observability',
           'Provider storage, disk, range, shard, and capacity observations.',
           ('storage', 'tablespace', 'filespace', 'partition', 'shard',
            'tablet', 'range', 'region', 'key-range', 'subspace',
            'data-region'),
           ('size', 'usage', 'capacity', 'status')),
    _facet('partition-management', 'Partition management', 'maintenance',
           'Partitions, shards, tablets, ranges, and placement operations.',
           ('partition', 'shard', 'tablet', 'range', 'region', 'peer',
            'cluster-slot', 'key-range'),
           ('split', 'merge', 'scatter', 'relocate', 'placement')),
    _facet('configuration', 'Configuration', 'runtime',
           'Provider settings, configuration, policies, and runtime '
           'defaults.',
           ('configuration', 'setting', 'settings-profile', 'pragma',
            'zone-config', 'placement-policy', 'policy', 'quota',
            'resource-group', 'data-region'),
           ('configure', 'config', 'setting', 'alter', 'set', 'reset')),
    _facet('logs', 'Logs', 'observability',
           'Provider server, audit, transaction, and slow-operation logs.',
           ('log', 'server-log', 'slow-log', 'transaction-log', 'event'),
           ('log', 'tail', 'inspect')),
    _facet('metrics', 'Metrics', 'observability',
           'Provider metrics, latency, profiling, and health measurements.',
           ('metric', 'latency', 'statistics', 'profiling', 'health',
            'query-profile'),
           ('metrics', 'profile', 'statistics', 'health')),
    _facet('slow-queries', 'Slow queries', 'workload',
           'Provider slow-query logs, profiling, and active query details.',
           ('slow-log', 'query', 'query-profile', 'profiling',
            'current-operation'),
           ('profile', 'explain', 'inspect', 'kill', 'cancel')),
    _facet('locks', 'Locks', 'workload',
           'Provider lock and contention visibility and controls.',
           ('lock', 'transaction', 'session', 'client'),
           ('lock', 'block', 'cancel', 'kill')),
    _facet('sessions', 'Sessions', 'workload',
           'Provider sessions, clients, transactions, and connection state.',
           ('session', 'client', 'transaction', 'connection', 'query'),
           ('cancel', 'kill', 'terminate', 'inspect')),
    _facet('long-running-operations', 'Long-running operations', 'workload',
           'Provider-owned progress, terminal state, and cancellation.',
           ('operation', 'current-operation', 'job', 'task', 'workflow',
            'reindex-operation', 'compaction', 'import-job'),
           ('cancel', 'pause', 'resume', 'status', 'progress')),
    _facet('jobs-schedules', 'Jobs and schedules', 'automation',
           'Provider jobs, schedules, events, tasks, and processing engines.',
           ('job', 'schedule', 'event', 'trigger', 'compute-task',
            'processing-engine'),
           ('schedule', 'pause', 'resume', 'cancel', 'run')),
    _facet('change-feeds', 'Change feeds', 'data-movement',
           'Provider change streams, feeds, publications, and subscriptions.',
           ('changefeed', 'change-stream', 'publication', 'subscription',
            'stream', 'vreplication-stream'),
           ('create', 'pause', 'resume', 'drop', 'sync')),
    _facet('cdc', 'Change data capture', 'data-movement',
           'Provider CDC configuration, streams, consumers, and progress.',
           ('changefeed', 'change-stream', 'publication', 'subscription',
            'vreplication-stream', 'consumer-group', 'stream'),
           ('create', 'configure', 'pause', 'resume', 'drop')),
    _facet('security-audit', 'Security audit', 'security',
           'Provider users, roles, privileges, ACLs, and audit evidence.',
           ('audit', 'user', 'role', 'privilege', 'permission', 'acl-user',
            'role-mapping'),
           ('audit', 'grant', 'revoke', 'inspect')),
    _facet('certificates', 'Certificates', 'security',
           'Provider certificate and transport-security lifecycle.',
           ('certificate', 'credential', 'configuration', 'server'),
           ('certificate', 'tls', 'rotate', 'reload')),
    _facet('upgrade-compatibility', 'Upgrade and compatibility checks',
           'lifecycle',
           'Provider version, compatibility, extension, and plugin checks.',
           ('extension', 'plugin', 'module', 'server', 'cluster', 'node'),
           ('upgrade', 'version', 'compatibility', 'check', 'validate')),
    _facet('topology', 'Topology visualization', 'distributed',
           'Provider-declared hierarchy and placement topology.',
           ('cluster', 'universe', 'deployment', 'node', 'member', 'server',
            'shard', 'tablet', 'region', 'zone', 'datacenter', 'cell',
            'store', 'peer', 'replica', 'router', 'process'),
           distributed=True),
    _facet('node-roles', 'Node roles', 'distributed',
           'Provider node, process, tablet, and member roles.',
           ('node', 'member', 'server', 'process', 'store', 'tablet',
            'tserver', 'master', 'vtgate', 'vttablet', 'replica'),
           ('class', 'role', 'type'), True),
    _facet('placement', 'Shard, tablet, and partition placement',
           'distributed', 'Provider placement units, constraints, and rules.',
           ('shard', 'tablet', 'partition', 'range', 'region', 'peer',
            'placement-rule', 'placement-policy', 'zone-config',
            'cluster-slot', 'key-range'),
           ('placement', 'relocate', 'scatter', 'split'), True),
    _facet('leadership', 'Leadership', 'distributed',
           'Provider leaders, primaries, coordinators, and leaseholders.',
           ('master', 'replica', 'peer', 'tablet', 'shard', 'coordinator',
            'node'),
           ('leader', 'primary', 'lease', 'reparent', 'stepdown'), True),
    _facet('replication-lag', 'Replication lag', 'distributed',
           'Provider-reported replication progress and lag.',
           ('replica', 'replication', 'peer', 'vreplication-stream',
            'changefeed'),
           ('lag', 'status', 'progress'), True),
    _facet('region-zone-placement', 'Region and zone placement', 'distributed',
           'Provider locality, region, zone, cell, and data-center placement.',
           ('region', 'zone', 'locality', 'datacenter', 'cell',
            'placement-policy', 'zone-config', 'data-region'),
           ('placement', 'locality', 'region', 'zone'), True),
    _facet('membership-changes', 'Membership changes', 'distributed',
           'Provider-admitted member addition, removal, and role changes.',
           ('node', 'member', 'server', 'store', 'peer', 'coordinator',
            'tablet'),
           ('add', 'remove', 'decommission', 'recommission', 'exclude',
            'include', 'offline'), True),
    _facet('rebalancing', 'Rebalancing', 'distributed',
           'Provider data movement, balancing, scatter, and relocation.',
           ('balancer', 'scheduler', 'shard', 'tablet', 'range', 'region',
            'peer', 'placement-rule'),
           ('rebalance', 'balance', 'scatter', 'relocate', 'transfer',
            'move'), True),
    _facet('failure-maintenance', 'Failure injection and maintenance modes',
           'distributed',
           'Provider maintenance, drain, exclusion, and failure controls.',
           ('node', 'server', 'store', 'process', 'cluster', 'tablet'),
           ('maintenance', 'failure', 'exclude', 'offline', 'drain',
            'blacklist', 'throttle'), True),
    _facet('rolling-operations', 'Safe rolling operations', 'distributed',
           'Provider-planned rolling maintenance and upgrade sequences.',
           ('cluster', 'node', 'server', 'process', 'tablet', 'store'),
           ('rolling', 'upgrade', 'restart', 'maintenance', 'drain'), True),
    _facet('operation-progress', 'Operation progress and cancellation',
           'distributed',
           'Provider-reported progress and provider-authorized cancellation.',
           ('operation', 'job', 'task', 'workflow', 'reindex-operation',
            'import-job', 'compaction'),
           ('cancel', 'pause', 'resume', 'status', 'progress'), True),
)


# These workspaces present a specific operational signal or workflow. Merely
# having a broad node/server/table resource is not enough to advertise every
# mutation on that resource inside the facet.
STRICT_OPERATION_FACETS = frozenset({
    'health', 'runtime-status', 'failover', 'point-in-time-recovery',
    'vacuum-analyze', 'statistics', 'storage-usage', 'logs', 'metrics',
    'slow-queries', 'locks', 'sessions', 'long-running-operations',
    'certificates', 'upgrade-compatibility', 'topology', 'node-roles',
    'leadership', 'replication-lag', 'membership-changes', 'rebalancing',
    'failure-maintenance', 'rolling-operations', 'operation-progress',
})


# Only distinctive verbs may route an operation into a facet whose resource
# kind did not match. Generic verbs such as create, alter, inspect, or status
# are meaningful only in the context of the provider resource that owns them.
GLOBAL_OPERATION_TERMS = frozenset({
    'backup', 'snapshot', 'archive', 'restore', 'recover', 'recovery',
    'pitr', 'failover', 'switchover', 'promote', 'reparent', 'stepdown',
    'compact', 'compaction', 'vacuum', 'analyze', 'optimize', 'reindex',
    'rebuild', 'relocate', 'scatter', 'split', 'replicate', 'replication',
    'decommission', 'recommission', 'rebalance', 'balance', 'upgrade',
    'compatibility', 'rolling', 'maintenance', 'blacklist', 'throttle',
})


def _validate_catalog(catalog):
    if not isinstance(catalog, Mapping):
        raise OperationalWorkspaceError(
            'visual administration catalog required'
        )
    engine_id = catalog.get('engine_id')
    if not isinstance(engine_id, str) or not engine_id:
        raise OperationalWorkspaceError('provider engine_id is required')
    objects = catalog.get('objects')
    if not isinstance(objects, list):
        raise OperationalWorkspaceError('provider objects must be a list')
    return engine_id, objects


def _resource_matches(facet, resource_kind):
    return bool(_words(resource_kind).intersection(facet.resource_terms))


def _operation_matches(facet, resource_kind, operation):
    resource_match = _resource_matches(facet, resource_kind)
    operation_words = _words(operation.get('operation_id', '')) | _words(
        operation.get('title', '')
    )
    term_match = bool(operation_words.intersection(facet.operation_terms))
    if resource_match:
        return (
            term_match if facet.facet_id in STRICT_OPERATION_FACETS else True
        )
    return bool(
        operation_words.intersection(
            facet.operation_terms.intersection(GLOBAL_OPERATION_TERMS)
        )
    )


def build_operational_workspace(catalog, resources: Iterable[Mapping] = ()):
    """Return a deterministic UI projection of one provider's declarations."""
    engine_id, objects = _validate_catalog(catalog)
    discovered = {}
    for resource in resources or ():
        if not isinstance(resource, Mapping):
            continue
        kind = resource.get('resource_kind')
        if isinstance(kind, str):
            discovered[kind] = discovered.get(kind, 0) + 1
    declared_kinds = {
        item.get('resource_kind') for item in objects
        if isinstance(item, Mapping)
    }
    distributed = bool(
        catalog.get('distributed_control_plane_contract') or
        str(catalog.get('model_family', '')).startswith('distributed') or
        declared_kinds.intersection({
            'cluster', 'deployment', 'universe', 'replica-set',
            'cluster-slot',
        }) or
        {'dbms', 'server'}.issubset(declared_kinds) or
        any(object_item.get('resource_kind') in {
            'shard', 'tablet', 'peer', 'coordinator', 'placement-rule',
            'placement-policy', 'zone-config', 'region', 'zone', 'cell',
        } for object_item in objects if isinstance(object_item, Mapping))
    )
    projected = []
    for facet in FACETS:
        if facet.distributed and not distributed:
            continue
        kinds = []
        operations = []
        for object_item in objects:
            if not isinstance(object_item, Mapping):
                continue
            kind = object_item.get('resource_kind')
            if not isinstance(kind, str) or not kind:
                continue
            if _resource_matches(facet, kind) and kind not in kinds:
                kinds.append(kind)
            for operation in object_item.get('operations', ()):
                if not isinstance(operation, Mapping):
                    continue
                if not _operation_matches(facet, kind, operation):
                    continue
                item = copy.deepcopy(dict(operation))
                item['resource_kind'] = kind
                operations.append(item)
                if kind not in kinds:
                    kinds.append(kind)
        operations.sort(key=lambda item: (
            item['resource_kind'], item.get('title', ''),
            item.get('operation_id', ''),
        ))
        declared = bool(kinds or operations)
        projected.append({
            'facet_id': facet.facet_id,
            'title': facet.title,
            'category': facet.category,
            'summary': facet.summary,
            'distributed': facet.distributed,
            'catalog_state': (
                'operational' if operations else
                'observable' if kinds else 'unavailable'
            ),
            'unavailable_reason': None if declared else (
                'The provider does not declare a resource or operation for '
                'this workspace facet.'
            ),
            'resource_kinds': kinds,
            'discovered_resource_count': sum(
                discovered.get(kind, 0) for kind in kinds
            ),
            'operations': operations,
            'execution_available': any(
                operation.get('execution_available', True)
                for operation in operations
            ),
            'long_running': any(
                operation.get('long_running', False)
                for operation in operations
            ),
            'cancellable': any(
                operation.get('cancellable', False)
                for operation in operations
            ),
        })
    categories = []
    for facet in projected:
        if facet['category'] not in categories:
            categories.append(facet['category'])
    return {
        'schema': OPERATIONAL_WORKSPACE_SCHEMA,
        'engine_id': engine_id,
        'model_family': catalog.get('model_family'),
        'provider_declared': True,
        'distributed': distributed,
        'categories': categories,
        'facets': projected,
        'topology': {
            'available': any(
                item['facet_id'] == 'topology' and
                item['catalog_state'] != 'unavailable'
                for item in projected
            ),
            'authority': 'provider-resource-authority-path',
            'resource_kinds': next((
                item['resource_kinds'] for item in projected
                if item['facet_id'] == 'topology'
            ), []),
            'infer_edges': False,
        },
        'execution_contract': {
            'provider_compilation_required': True,
            'provider_finality_authority': True,
            'provider_cancellation_authority': True,
            'automatic_mutation_retry': False,
            'plan_confirmation_required_when_declared': True,
        },
    }


__all__ = (
    'FACETS', 'OPERATIONAL_WORKSPACE_SCHEMA', 'OperationalWorkspaceError',
    'WorkspaceFacet', 'build_operational_workspace',
)
