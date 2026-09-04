##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-specific navigator and object-editor experience descriptors."""

from __future__ import annotations

import copy

from .requirements import concept_coverage_for_engine


EXPERIENCE_SCHEMA = 'cdeadmin.provider-experience.v1'

GROUPS = (
    ('topology', 'Servers & topology'),
    ('namespaces', 'Databases & namespaces'),
    ('relations', 'Tables & relations'),
    ('programmable', 'Programmability'),
    ('documents', 'Documents'),
    ('graph-data', 'Graph data'),
    ('graph-schema', 'Graph schema'),
    ('keys', 'Keys & data structures'),
    ('streams', 'Streams & messaging'),
    ('search-schema', 'Search schema'),
    ('ingest', 'Ingest & processing'),
    ('analytics', 'Analytical structures'),
    ('temporal', 'Bitemporal history'),
    ('security', 'Security'),
    ('storage', 'Storage'),
    ('replication', 'Replication & distribution'),
    ('scheduling', 'Jobs & events'),
    ('operations', 'Operations & diagnostics'),
)

GROUP_KINDS = {
    'topology': {
        'server', 'deployment', 'dbms', 'cluster', 'node', 'replica',
        'replica-set', 'shard', 'router', 'member', 'universe', 'region',
        'zone', 'datacenter', 'locality', 'cell', 'tablet', 'tserver',
        'master', 'coordinator', 'process', 'store', 'peer', 'vtgate',
        'vttablet', 'baseline-topology', 'cluster-slot', 'sentinel',
    },
    'namespaces': {
        'database', 'attached-database', 'schema', 'sql-schema', 'keyspace',
        'tenant', 'directory', 'subspace', 'catalog', 'data-source',
        'composite-database', 'branch', 'tag', 'remote',
    },
    'relations': {
        'table', 'view', 'materialized-view', 'column', 'field', 'domain',
        'type', 'user-defined-type', 'index', 'constraint', 'sequence',
        'partition', 'virtual-table', 'fts-table', 'foreign-table',
        'collation', 'conversion', 'rule', 'row-policy', 'operator',
        'operator-class', 'operator-family', 'cast', 'language',
    },
    'programmable': {
        'function', 'procedure', 'package', 'macro', 'trigger',
        'event-trigger', 'aggregate', 'external-function', 'script',
        'function-library', 'exception',
    },
    'documents': {
        'collection', 'document', 'entity', 'validator', 'validation-rule',
        'revision', 'change-stream',
    },
    'graph-data': {
        'graph', 'node', 'relationship', 'graph-node', 'graph-edge',
        'graph-projection',
    },
    'graph-schema': {
        'label', 'node-label', 'relationship-type', 'edge-type', 'property',
    },
    'keys': {
        'key', 'raw-key', 'key-range', 'string', 'hash', 'list', 'set',
        'sorted-set', 'bitmap', 'hyperloglog', 'geospatial', 'vector-set',
        'cache', 'cache-template', 'lock', 'transaction', 'pipeline',
    },
    'streams': {
        'stream', 'consumer-group', 'consumer', 'pubsub-channel',
    },
    'search-schema': {
        'index', 'mapping', 'analyzer', 'normalizer', 'tokenizer',
        'index-template', 'component-template', 'data-stream', 'alias',
        'search-index',
    },
    'ingest': {
        'ingest-pipeline', 'ingest-processor', 'aggregation-pipeline',
        'processing-engine', 'plugin', 'connector', 'import-job',
        'coprocessor',
    },
    'analytics': {
        'cube', 'dimension', 'hierarchy', 'level', 'metric',
        'materialization', 'projection', 'dictionary', 'time-series',
        'measurement', 'tag', 'last-cache', 'distinct-cache',
        'vector-collection', 'vector-index', 'load-state', 'compaction',
        'data-skipping-index', 'query-plan', 'profiling', 'saved-query',
        'prepared-query', 'query',
    },
    'temporal': {
        'entity', 'valid-time', 'system-time', 'transaction',
        'transaction-log',
    },
    'security': {
        'user', 'acl-user', 'principal', 'role', 'privilege', 'permission',
        'grant', 'credential', 'token', 'role-mapping', 'security-policy',
        'audit-policy', 'quota', 'settings-profile', 'tenant', 'secret',
        'user-mapping',
    },
    'storage': {
        'tablespace', 'filespace', 'data-region', 'configuration',
        'persistence', 'repository', 'snapshot', 'filespace-snapshot',
        'backup', 'restore',
    },
    'replication': {
        'replication-channel', 'publication', 'subscription',
        'changefeed', 'xcluster-replication', 'vreplication-stream',
        'placement-policy', 'placement-rule', 'zone-config', 'range',
        'routing-rule', 'vindex', 'vschema', 'shard',
    },
    'scheduling': {
        'job', 'event', 'schedule', 'scheduler', 'workflow', 'online-ddl',
        'materialize',
    },
    'operations': {
        'health', 'statistics', 'current-operation', 'server-log',
        'slow-log', 'latency', 'client', 'compute-task', 'service',
        'service-operation', 'merge', 'rebase', 'conflict', 'working-set',
        'commit', 'watch', 'proof', 'transaction-log', 'system-time',
        'valid-time', 'pragma', 'module', 'extension', 'shell', 'import',
        'export', 'consistency-check', 'language-settings',
    },
}

PARENT_KINDS = {
    'database': ('server', 'deployment', 'dbms', 'cluster', 'universe'),
    'schema': ('database',),
    'sql-schema': ('database',),
    'table': ('schema', 'sql-schema', 'database', 'keyspace'),
    'view': ('schema', 'database'),
    'materialized-view': ('schema', 'database', 'keyspace'),
    'column': ('table', 'view', 'materialized-view'),
    'field': ('collection', 'table', 'index'),
    'index': ('table', 'collection', 'database'),
    'constraint': ('table', 'database'),
    'trigger': ('table', 'database'),
    'partition': ('table', 'collection'),
    'document': ('collection',),
    'validator': ('collection',),
    'mapping': ('index',),
    'consumer-group': ('stream',),
    'consumer': ('consumer-group',),
    'node': ('graph', 'database', 'cluster'),
    'relationship': ('graph', 'database'),
    'label': ('graph', 'database'),
    'graph-projection': ('database', 'graph'),
    'query-plan': ('database', 'query'),
    'shard': ('index', 'keyspace', 'cluster'),
    'replica': ('shard', 'cluster', 'server'),
}

DATA_PRESENTATIONS = {
    'document': 'document-json', 'entity': 'document-json',
    'revision': 'document-history', 'collection': 'document-grid',
    'node': 'graph-node', 'relationship': 'graph-relationship',
    'graph-node': 'graph-node', 'graph-edge': 'graph-relationship',
    'key': 'key-value', 'raw-key': 'key-value', 'key-range': 'key-value',
    'string': 'redis-string', 'hash': 'redis-hash', 'list': 'redis-list',
    'set': 'redis-set', 'sorted-set': 'redis-sorted-set',
    'stream': 'redis-stream', 'pubsub-channel': 'redis-pubsub',
    'mapping': 'search-mapping', 'query-plan': 'query-plan',
    'profiling': 'query-profile', 'time-series': 'time-series',
    'measurement': 'time-series', 'vector-collection': 'vector',
    'vector-index': 'vector-index', 'cube': 'semantic-cube',
    'valid-time': 'bitemporal-history',
    'system-time': 'bitemporal-history',
    'transaction': 'transaction-state',
    'transaction-log': 'transaction-history',
}


DOCUMENT_KINDS = {
    'collection', 'view', 'document', 'field', 'index', 'validator',
    'validation-rule', 'revision', 'change-stream',
    'aggregation-pipeline',
}

GRAPH_DATA_KINDS = {
    'graph', 'node', 'relationship', 'graph-node', 'graph-edge',
}

GRAPH_SCHEMA_KINDS = {
    'label', 'node-label', 'relationship-type', 'edge-type', 'property',
    'index', 'constraint', 'procedure', 'function', 'graph-projection',
    'query', 'query-plan', 'transaction',
}

SEARCH_KINDS = {
    'index', 'mapping', 'settings', 'alias', 'index-template',
    'component-template', 'data-stream', 'document', 'field', 'analyzer',
    'normalizer', 'tokenizer', 'repository', 'snapshot', 'policy',
    'reindex-operation', 'query-profile',
}

COLUMNAR_KINDS = {
    'table', 'column', 'view', 'materialized-view', 'dictionary',
    'function', 'projection', 'data-skipping-index', 'partition',
}

TIME_SERIES_KINDS = {
    'table', 'column', 'measurement', 'time-series', 'tag', 'field',
    'retention-policy', 'last-cache', 'distinct-cache', 'trigger',
    'processing-engine', 'compaction',
}

VECTOR_KINDS = {
    'collection', 'field', 'partition', 'vector-index', 'alias',
    'load-state', 'compaction', 'resource-group',
}


def _group_for(kind, model_family):
    if 'bitemporal' in model_family and kind in GROUP_KINDS['temporal']:
        return 'temporal'
    if 'document' in model_family and kind in DOCUMENT_KINDS:
        return 'documents'
    if 'graph' in model_family and kind in GRAPH_DATA_KINDS:
        return 'graph-data'
    if 'graph' in model_family and kind in GRAPH_SCHEMA_KINDS:
        return 'graph-schema'
    if 'search' in model_family and kind in SEARCH_KINDS:
        return 'search-schema'
    if kind == 'tenant' and 'search' in model_family:
        return 'security'
    if 'columnar' in model_family and kind in COLUMNAR_KINDS:
        return 'analytics'
    if 'time-series' in model_family and kind in TIME_SERIES_KINDS:
        return 'analytics'
    if 'vector' in model_family and kind in VECTOR_KINDS:
        return 'analytics'
    for group_id, kinds in GROUP_KINDS.items():
        if kind in kinds:
            return group_id
    return 'operations'


def _editor_kind(kind, model_family, group_id):
    if 'bitemporal' in model_family and group_id == 'temporal':
        return 'bitemporal-object'
    if 'document' in model_family and group_id == 'documents':
        return 'document-object'
    if 'graph' in model_family and group_id in {
            'graph-data', 'graph-schema'}:
        return 'graph-object'
    if ('key-value' in model_family or 'data-structure' in model_family) \
            and group_id in {'keys', 'streams'}:
        return 'key-value-object'
    if group_id in {'search-schema', 'ingest'} and 'search' in model_family:
        return 'search-object'
    if group_id == 'analytics':
        if 'vector' in model_family:
            return 'vector-object'
        if 'time-series' in model_family:
            return 'time-series-object'
        if 'columnar' in model_family:
            return 'columnar-object'
        return 'analytical-object'
    if group_id == 'topology':
        return 'topology-object'
    if group_id == 'security':
        return 'security-object'
    if group_id in {
            'storage', 'replication', 'scheduling', 'operations', 'temporal'}:
        return 'operational-object'
    return 'relational-object'


def _sections(group_id, operations):
    sections = ['properties']
    if group_id in {
        'relations', 'programmable', 'graph-schema', 'search-schema',
        'ingest', 'analytics',
    }:
        sections.extend(['definition', 'dependencies'])
    if group_id in {
        'relations', 'documents', 'graph-data', 'keys', 'streams',
        'search-schema', 'analytics',
    }:
        sections.append('data')
    if group_id in {
            'topology', 'replication', 'storage', 'operations', 'temporal'}:
        sections.extend(['state', 'statistics'])
    if group_id == 'security' or any(
            item in {'grant', 'revoke'} for item in operations):
        sections.append('security')
    sections.append('operations')
    return list(dict.fromkeys(sections))


def enrich_engine_experience(engine):
    """Add complete navigator/editor metadata to one engine descriptor."""
    value = copy.deepcopy(engine)
    engine_id = value['engine_id']
    model_family = value.get('model_family', 'provider-native')
    kinds = {item['resource_kind'] for item in value['objects']}
    used_groups = []
    for order, resource in enumerate(value['objects']):
        kind = resource['resource_kind']
        group_id = resource.get(
            'navigator_group', _group_for(kind, model_family))
        used_groups.append(group_id)
        parent_kinds = [
            item for item in resource.get(
                'parent_kinds', PARENT_KINDS.get(kind, ()))
            if item in kinds and item != kind
        ]
        operation_ids = [
            item['operation_id'] for item in resource['operations']
        ]
        navigator = {
            'navigator_id': f'cdeadmin.{engine_id}.{kind}.navigator',
            'icon_id': f'cdeadmin.{engine_id}.{kind}',
            'group_id': group_id,
            'order': order,
            'parent_kinds': parent_kinds,
            'lazy_children': True,
        }
        navigator.update(resource.get('navigator', {}))
        resource['navigator'] = navigator
        editor = {
            'editor_id': f'cdeadmin.{engine_id}.{kind}.editor',
            'editor_kind': _editor_kind(kind, model_family, group_id),
            'sections': _sections(group_id, operation_ids),
            'data_presentation': DATA_PRESENTATIONS.get(
                kind,
                'columnar-grid' if 'columnar' in model_family else
                'structured-grid' if group_id == 'relations' else None,
            ),
            'native_definition': group_id in {
                'relations', 'programmable', 'graph-schema',
                'search-schema', 'ingest', 'analytics',
            },
            'provider_planned_operations': operation_ids,
        }
        editor.update(resource.get('editor', {}))
        editor['provider_planned_operations'] = operation_ids
        resource['editor'] = editor
    group_titles = dict(GROUPS)
    ordered_groups = list(dict.fromkeys(used_groups))
    navigator = {
        **value.get('navigator', {}),
        'schema': EXPERIENCE_SCHEMA,
        'navigator_id': f'cdeadmin.{engine_id}.navigator',
        'hierarchical': True,
        'authority_path_owned_by_provider': True,
        'groups': [{
            'group_id': group_id,
            'title': group_titles[group_id],
            'order': index,
        } for index, group_id in enumerate(ordered_groups)],
    }
    value['navigator'] = navigator
    value['object_editor'] = {
        **value.get('object_editor', {}),
        'schema': EXPERIENCE_SCHEMA,
        'editor_suite_id': f'cdeadmin.{engine_id}.editors',
        'provider_validates_drafts': True,
        'provider_plans_native_commands': True,
        'provider_owns_finality': True,
    }
    value['concept_coverage'] = concept_coverage_for_engine(value)
    return value


__all__ = ('EXPERIENCE_SCHEMA', 'enrich_engine_experience')
