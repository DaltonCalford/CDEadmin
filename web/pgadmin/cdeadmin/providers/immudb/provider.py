"""immudb 1.11.0 immutable multi-model provider."""

from __future__ import annotations

import base64
import copy
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from collections.abc import Mapping

from pgadmin.cdeadmin.sdk import (
    ActualEnginePilotProvider,
    PilotProfile,
    RelationalClientError,
    RelationalDBAPIClient,
)
from pgadmin.cdeadmin.visual_admin import (
    ControlPlaneOperation,
    control_plane_field as cp_field,
)
from ..distributed_control_plane import DistributedSQLControlPlane
from ..distributed_sql import (
    PsycopgPoolDBAPIClient,
    create_sql_client,
    optional_rows,
    postgresql_catalog,
    resource,
    sql_administration,
)


PROFILE = PilotProfile(
    'org.cdeadmin.immudb', 'immudb-native', 'immudb', 'immudb', '1.11.0',
    'postgresql_wire', 'immutable-multimodel', 'immudb-sql', 'immudb SQL',
    'immudb-transaction-native', 'tabular',
    (
        'server', 'replica', 'database', 'table', 'column', 'index', 'key',
        'view', 'sequence', 'constraint', 'ttl', 'collection',
        'collection-index', 'document', 'revision', 'proof', 'transaction',
        'user', 'permission',
    ),
    ('immuadmin', 'immuclient', 'proof-verifier', 'hot-backup'),
    semantic_sql_dialect={
        'language_profile': 'immudb-sql', 'quote_open': '"',
        'quote_close': '"', 'supports_rollup': False,
    },
)


def _choice(field_id, label, values, **options):
    return cp_field(
        field_id, label, 'select', options=[
            {'value': value, 'label': title} for value, title in values
        ], **options
    )


_NAME_PATTERN = r'[A-Za-z_][A-Za-z0-9_]{0,127}'
_UINT32_MAX = 4294967295


def _uint_field(field_id, label, **options):
    return cp_field(
        field_id, label, 'number', False, minimum=0,
        maximum=_UINT32_MAX, **options
    )


_DATABASE_SETTINGS = (
    cp_field('autoload', 'Load automatically', 'boolean', False,
             default=True),
    cp_field('exclude_commit_time', 'Exclude commit time from checksums',
             'boolean', False, default=False),
    cp_field('max_concurrency', 'Maximum commit concurrency', 'number',
             False, minimum=1, maximum=65536),
    cp_field('read_tx_pool_size', 'Read transaction pool size', 'number',
             False, minimum=1, maximum=65536),
    cp_field('sync_frequency_ms', 'Fsync frequency (milliseconds)', 'number',
             False, minimum=0, maximum=86400000),
    _uint_field('file_size', 'Maximum data file size'),
    _uint_field('max_key_length', 'Maximum key length'),
    _uint_field('max_value_length', 'Maximum value length'),
    _uint_field('max_transaction_entries',
                'Maximum entries per transaction'),
    _uint_field('max_io_concurrency', 'Maximum I/O concurrency'),
    _uint_field('tx_log_cache_size', 'Transaction-log cache size'),
    _uint_field('value_log_cache_size', 'Value-log cache size'),
    _uint_field('value_log_open_files', 'Open value-log file limit'),
    _uint_field('tx_log_open_files', 'Open transaction-log file limit'),
    _uint_field('commit_log_open_files', 'Open commit-log file limit'),
    _uint_field('write_tx_header_version',
                'Transaction-header write version'),
    _uint_field('write_buffer_size', 'Write buffer size'),
    _uint_field('max_active_transactions',
                'Maximum active transactions'),
    _uint_field('mvcc_read_set_limit', 'MVCC read-set limit'),
    cp_field('embedded_values', 'Embed values in transaction headers',
             'boolean', False),
    cp_field('preallocate_files', 'Preallocate database files', 'boolean',
             False),
    _uint_field('index_flush_threshold', 'Index flush threshold'),
    _uint_field('index_sync_threshold', 'Index sync threshold'),
    _uint_field('index_cache_size', 'Index node-cache size'),
    _uint_field('index_max_node_size', 'Index maximum node size'),
    _uint_field('index_max_active_snapshots',
                'Index active-snapshot limit'),
    cp_field('index_renew_snapshot_root_after_ms',
             'Index snapshot-root renewal (milliseconds)', 'number', False,
             minimum=0, maximum=9007199254740991),
    _uint_field('index_compaction_threshold',
                'Index compaction threshold'),
    _uint_field('index_compaction_delay_ms',
                'Index compaction delay (milliseconds)'),
    _uint_field('index_nodes_log_open_files',
                'Open index node-log file limit'),
    _uint_field('index_history_log_open_files',
                'Open index history-log file limit'),
    _uint_field('index_commit_log_open_files',
                'Open index commit-log file limit'),
    _uint_field('index_flush_buffer_size', 'Index flush buffer size'),
    cp_field('index_cleanup_percentage', 'Index cleanup percentage',
             'number', False, minimum=0, maximum=100),
    _uint_field('index_max_bulk_size', 'Index maximum bulk size'),
    cp_field('index_bulk_preparation_timeout_ms',
             'Index bulk preparation timeout (milliseconds)', 'number',
             False, minimum=0, maximum=315360000000),
    _uint_field('aht_sync_threshold', 'AHT sync threshold'),
    _uint_field('aht_write_buffer_size', 'AHT write buffer size'),
    cp_field('history_retention_period_ms',
             'Automatic history retention (milliseconds)', 'number', False,
             minimum=0, maximum=315360000000),
    cp_field('history_truncation_frequency_ms',
             'Automatic history truncation frequency (milliseconds)',
             'number', False, minimum=0, maximum=315360000000),
    cp_field('is_replica', 'Configure as replica', 'boolean', False,
             default=False),
    cp_field('primary_database', 'Primary database', 'text', False,
             max_length=128, pattern=_NAME_PATTERN, visible_when={
                 'field_id': 'is_replica', 'equals': True,
             }),
    cp_field('primary_host', 'Primary host', 'text', False,
             max_length=253, pattern=r'[A-Za-z0-9_.:-]+', visible_when={
                 'field_id': 'is_replica', 'equals': True,
             }),
    cp_field('primary_port', 'Primary port', 'number', False,
             minimum=1, maximum=65535, visible_when={
                 'field_id': 'is_replica', 'equals': True,
             }),
    cp_field('primary_username', 'Replication username', 'text', False,
             max_length=128, pattern=_NAME_PATTERN, visible_when={
                 'field_id': 'is_replica', 'equals': True,
             }),
    cp_field('primary_password', 'Replication password', 'password', False,
             max_length=1024, sensitive=True, visible_when={
                 'field_id': 'is_replica', 'equals': True,
             }),
    cp_field('synchronous_replication', 'Synchronous replication',
             'boolean', False, default=False),
    cp_field('synchronous_acknowledgements',
             'Required synchronous acknowledgements', 'number', False,
             minimum=0, maximum=65535),
    _uint_field('replication_prefetch_transactions',
                'Replication prefetch transaction limit'),
    _uint_field('replication_commit_concurrency',
                'Replication commit concurrency'),
    cp_field('replication_allow_tx_discarding',
             'Allow divergent transaction discard', 'boolean', False),
    cp_field('replication_skip_integrity_check',
             'Skip replication integrity checks', 'boolean', False),
    cp_field('replication_wait_for_indexing',
             'Wait for replication indexing', 'boolean', False),
)

CONTROL_OPERATIONS = (
    ControlPlaneOperation(
        'server', 'health', 'Inspect server health', 'read',
        'maintenance_admin', confirmation_required=False,
        impact_scope='cluster', post_state_required=False
    ),
    ControlPlaneOperation(
        'database', 'create', 'Create immutable database', 'admin',
        'topology_admin', (
            cp_field('name', 'Database name', 'text', True,
                     max_length=128, pattern=_NAME_PATTERN),
            cp_field('if_not_exists', 'Allow existing database', 'boolean',
                     False, default=False),
        ) + _DATABASE_SETTINGS, target_required=False, impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'database', 'update_settings', 'Update database settings', 'admin',
        'topology_admin', _DATABASE_SETTINGS, impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'database', 'load', 'Load database', 'admin', 'topology_admin',
        impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'database', 'unload', 'Unload database', 'destructive',
        'topology_admin', impact_scope='cluster', long_running=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'database', 'drop', 'Delete database permanently', 'destructive',
        'topology_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'database', 'flush_index', 'Flush database index', 'admin',
        'maintenance_admin', (
            cp_field('cleanup_percentage', 'Cleanup percentage', 'number',
                     False, default=0, minimum=0, maximum=100),
            cp_field('synced', 'Synchronize to storage', 'boolean', False,
                     default=True),
        ), impact_scope='cluster', long_running=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'database', 'compact_index', 'Compact database index', 'admin',
        'maintenance_admin', impact_scope='cluster', long_running=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'database', 'truncate_history', 'Truncate retained history',
        'destructive', 'maintenance_admin', (
            cp_field('retention_period_ms', 'Retention period (milliseconds)',
                     'number', True, minimum=1,
                     maximum=315360000000),
        ), impact_scope='cluster', long_running=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'user', 'create', 'Create user', 'admin', 'security_admin', (
            cp_field('name', 'Username', 'text', True, max_length=128,
                     pattern=_NAME_PATTERN),
            cp_field('password', 'Password', 'password', True,
                     max_length=1024, sensitive=True),
            _choice('permission', 'Database permission', (
                ('read', 'Read'), ('readwrite', 'Read and write'),
                ('admin', 'Administrator'),
            ), required=True, default='read'),
            cp_field('database', 'Database', 'text', True, max_length=128,
                     pattern=_NAME_PATTERN),
        ), target_required=False, impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'user', 'change_password', 'Change user password', 'admin',
        'security_admin', (
            cp_field('old_password', 'Current password', 'password', False,
                     max_length=1024, sensitive=True),
            cp_field('new_password', 'New password', 'password', True,
                     max_length=1024, sensitive=True),
        ), impact_scope='cluster', post_state_required=False
    ),
    ControlPlaneOperation(
        'user', 'set_active', 'Set user activation', 'admin',
        'security_admin', (
            cp_field('active', 'Active', 'boolean', True, default=True),
        ), impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'user', 'drop', 'Drop user (deactivate)', 'destructive',
        'security_admin',
        impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'permission', 'grant', 'Grant database permission', 'admin',
        'security_admin', (
            cp_field('username', 'Username', 'text', True, max_length=128,
                     pattern=_NAME_PATTERN),
            cp_field('database', 'Database', 'text', True, max_length=128,
                     pattern=_NAME_PATTERN),
            _choice('permission', 'Permission', (
                ('read', 'Read'), ('readwrite', 'Read and write'),
                ('admin', 'Administrator'),
            ), required=True),
        ), target_required=False, impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'permission', 'revoke', 'Revoke database permission',
        'destructive', 'security_admin', (
            cp_field('username', 'Username', 'text', True, max_length=128,
                     pattern=_NAME_PATTERN),
            cp_field('database', 'Database', 'text', True, max_length=128,
                     pattern=_NAME_PATTERN),
            _choice('permission', 'Permission', (
                ('read', 'Read'), ('readwrite', 'Read and write'),
                ('admin', 'Administrator'),
            ), required=True),
        ), target_required=False, impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'permission', 'grant_sql', 'Grant SQL privileges', 'admin',
        'security_admin', (
            cp_field('username', 'Username', 'text', True, max_length=128,
                     pattern=_NAME_PATTERN),
            cp_field('database', 'Database', 'text', True, max_length=128,
                     pattern=_NAME_PATTERN),
            cp_field('privileges', 'SQL privileges', 'multiselect', True,
                     options=[
                         {'value': value, 'label': value.title()}
                         for value in (
                             'SELECT', 'CREATE', 'INSERT', 'UPDATE',
                             'DELETE', 'DROP', 'ALTER'
                         )
                     ]),
        ), target_required=False, impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'permission', 'revoke_sql', 'Revoke SQL privileges', 'destructive',
        'security_admin', (
            cp_field('username', 'Username', 'text', True, max_length=128,
                     pattern=_NAME_PATTERN),
            cp_field('database', 'Database', 'text', True, max_length=128,
                     pattern=_NAME_PATTERN),
            cp_field('privileges', 'SQL privileges', 'multiselect', True,
                     options=[
                         {'value': value, 'label': value.title()}
                         for value in (
                             'SELECT', 'CREATE', 'INSERT', 'UPDATE',
                             'DELETE', 'DROP', 'ALTER'
                         )
                     ]),
        ), target_required=False, impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'index', 'create', 'Create native SQL index', 'admin', 'administer', (
            cp_field('table', 'Table', 'text', True, max_length=128,
                     pattern=_NAME_PATTERN),
            cp_field('columns', 'Indexed columns', 'json', True,
                     json_type='array'),
            cp_field('unique', 'Unique index', 'boolean', False,
                     default=False),
        ), target_required=False, confirmation_required=False,
        impact_scope='database'
    ),
    ControlPlaneOperation(
        'index', 'drop', 'Drop native SQL index', 'destructive',
        'administer', impact_scope='database'
    ),
    ControlPlaneOperation(
        'key', 'insert', 'Set immutable key value', 'write', 'data_write', (
            cp_field('key', 'Key', 'text', True, max_length=4096),
            _choice('key_encoding', 'Key encoding', (
                ('utf8', 'UTF-8 text'), ('base64', 'Base64 bytes'),
            ), required=True, default='utf8'),
            cp_field('value', 'Value', 'multiline', True,
                     max_length=1048576),
            _choice('encoding', 'Input encoding', (
                ('utf8', 'UTF-8 text'), ('base64', 'Base64 bytes'),
            ), required=True, default='utf8'),
            cp_field('expires_at', 'Expires at (Unix seconds)', 'number',
                     False, minimum=1, maximum=9007199254740991),
        ), target_required=False, confirmation_required=False,
        impact_scope='database'
    ),
    ControlPlaneOperation(
        'key', 'update', 'Replace immutable key value', 'write',
        'data_write', (
            cp_field('value', 'Value', 'multiline', True,
                     max_length=1048576),
            _choice('encoding', 'Input encoding', (
                ('utf8', 'UTF-8 text'), ('base64', 'Base64 bytes'),
            ), required=True, default='utf8'),
            cp_field('expires_at', 'Expires at (Unix seconds)', 'number',
                     False, minimum=1, maximum=9007199254740991),
        ), confirmation_required=False, impact_scope='database'
    ),
    ControlPlaneOperation(
        'key', 'delete', 'Logically delete key', 'destructive', 'data_write',
        impact_scope='database'
    ),
    ControlPlaneOperation(
        'collection', 'create', 'Create document collection', 'write',
        'data_write', (
            cp_field('name', 'Collection name', 'text', True,
                     max_length=128, pattern=_NAME_PATTERN),
            cp_field('document_id_field', 'Document ID field', 'text', True,
                     max_length=128, pattern=_NAME_PATTERN,
                     default='_id'),
            cp_field('fields', 'Typed fields', 'json', False,
                     json_type='array', default=[]),
            cp_field('indexes', 'Initial indexes', 'json', False,
                     json_type='array', default=[]),
        ), target_required=False, confirmation_required=False,
        impact_scope='database'
    ),
    ControlPlaneOperation(
        'collection', 'alter', 'Change document ID field', 'write',
        'data_write', (
            cp_field('document_id_field', 'Document ID field', 'text', True,
                     max_length=128, pattern=_NAME_PATTERN),
        ), confirmation_required=False, impact_scope='database'
    ),
    ControlPlaneOperation(
        'collection', 'drop', 'Delete document collection', 'destructive',
        'data_write', impact_scope='database'
    ),
    ControlPlaneOperation(
        'collection-index', 'create', 'Create collection index', 'write',
        'data_write', (
            cp_field('collection', 'Collection', 'text', True,
                     max_length=128, pattern=_NAME_PATTERN),
            cp_field('fields', 'Indexed fields', 'json', True,
                     json_type='array'),
            cp_field('unique', 'Unique index', 'boolean', False,
                     default=False),
        ), target_required=False, confirmation_required=False,
        impact_scope='database'
    ),
    ControlPlaneOperation(
        'collection-index', 'drop', 'Drop collection index', 'destructive',
        'data_write', impact_scope='database'
    ),
    ControlPlaneOperation(
        'document', 'insert', 'Insert document', 'write', 'data_write', (
            cp_field('collection', 'Collection', 'text', True,
                     max_length=128, pattern=_NAME_PATTERN),
            cp_field('document', 'Document', 'json', True,
                     json_type='object'),
        ), target_required=False, confirmation_required=False,
        impact_scope='database'
    ),
    ControlPlaneOperation(
        'document', 'update', 'Replace document', 'write', 'data_write', (
            cp_field('document', 'Replacement document', 'json', True,
                     json_type='object'),
        ), confirmation_required=False, impact_scope='database'
    ),
    ControlPlaneOperation(
        'document', 'delete', 'Delete document', 'destructive', 'data_write',
        impact_scope='database'
    ),
)


def _target_name(request, label):
    target = request.get('target_resource')
    if not isinstance(target, Mapping):
        raise RelationalClientError(f'immudb {label} target is required')
    path = target.get('display_path') or [target.get('display_name')]
    value = path[-1] if isinstance(path, list) and path else None
    return _name(value, label)


def _name(value, label):
    if not isinstance(value, str) or re.fullmatch(
            _NAME_PATTERN, value) is None:
        raise RelationalClientError(f'immudb {label} is invalid')
    return value


def _nullable(value):
    return {'value': value}


def _database_settings(draft, include_defaults=False):
    settings = {}
    boolean_fields = {
        'autoload': 'autoload',
        'exclude_commit_time': 'excludeCommitTime',
        'embedded_values': 'embeddedValues',
        'preallocate_files': 'preallocFiles',
    }
    defaults = {'autoload': True, 'exclude_commit_time': False}
    for field_id, native in boolean_fields.items():
        if field_id in draft or (include_defaults and field_id in defaults):
            settings[native] = _nullable(bool(
                draft.get(field_id, defaults.get(field_id, False))
            ))

    direct = {
        'max_concurrency': 'maxConcurrency',
        'read_tx_pool_size': 'readTxPoolSize',
        'sync_frequency_ms': 'syncFrequency',
        'file_size': 'fileSize',
        'max_key_length': 'maxKeyLen',
        'max_value_length': 'maxValueLen',
        'max_transaction_entries': 'maxTxEntries',
        'max_io_concurrency': 'maxIOConcurrency',
        'tx_log_cache_size': 'txLogCacheSize',
        'value_log_cache_size': 'vLogCacheSize',
        'value_log_open_files': 'vLogMaxOpenedFiles',
        'tx_log_open_files': 'txLogMaxOpenedFiles',
        'commit_log_open_files': 'commitLogMaxOpenedFiles',
        'write_tx_header_version': 'writeTxHeaderVersion',
        'write_buffer_size': 'writeBufferSize',
        'max_active_transactions': 'maxActiveTransactions',
        'mvcc_read_set_limit': 'mvccReadSetLimit',
    }
    for field_id, native in direct.items():
        if field_id in draft and draft[field_id] not in (None, ''):
            settings[native] = _nullable(int(draft[field_id]))

    replication_fields = {
        'is_replica': 'replica',
        'primary_database': 'primaryDatabase',
        'primary_host': 'primaryHost',
        'primary_port': 'primaryPort',
        'primary_username': 'primaryUsername',
        'primary_password': 'primaryPassword',
        'synchronous_replication': 'syncReplication',
        'synchronous_acknowledgements': 'syncAcks',
        'replication_prefetch_transactions': 'prefetchTxBufferSize',
        'replication_commit_concurrency': 'replicationCommitConcurrency',
        'replication_allow_tx_discarding': 'allowTxDiscarding',
        'replication_skip_integrity_check': 'skipIntegrityCheck',
        'replication_wait_for_indexing': 'waitForIndexing',
    }
    replication = {}
    for field_id, native in replication_fields.items():
        if field_id in draft and draft[field_id] not in (None, ''):
            value = draft[field_id]
            if field_id in {
                'is_replica', 'synchronous_replication',
                'replication_allow_tx_discarding',
                'replication_skip_integrity_check',
                'replication_wait_for_indexing',
            }:
                value = bool(value)
            elif field_id in {
                'primary_port', 'synchronous_acknowledgements',
                'replication_prefetch_transactions',
                'replication_commit_concurrency',
            }:
                value = int(value)
            replication[native] = _nullable(value)
    if draft.get('is_replica') and not all(
            draft.get(key) not in (None, '') for key in (
                'primary_database', 'primary_host', 'primary_port',
                'primary_username', 'primary_password')):
        raise RelationalClientError(
            'immudb replica settings require primary connection fields')
    if replication or include_defaults:
        replication.setdefault(
            'replica', _nullable(bool(draft.get('is_replica', False))))
        replication.setdefault('syncReplication', _nullable(bool(
            draft.get('synchronous_replication', False)
        )))
        settings['replicationSettings'] = replication

    nested = {
        'indexSettings': {
            'index_flush_threshold': 'flushThreshold',
            'index_sync_threshold': 'syncThreshold',
            'index_cache_size': 'cacheSize',
            'index_max_node_size': 'maxNodeSize',
            'index_max_active_snapshots': 'maxActiveSnapshots',
            'index_renew_snapshot_root_after_ms': 'renewSnapRootAfter',
            'index_compaction_threshold': 'compactionThld',
            'index_compaction_delay_ms': 'delayDuringCompaction',
            'index_nodes_log_open_files': 'nodesLogMaxOpenedFiles',
            'index_history_log_open_files': 'historyLogMaxOpenedFiles',
            'index_commit_log_open_files': 'commitLogMaxOpenedFiles',
            'index_flush_buffer_size': 'flushBufferSize',
            'index_cleanup_percentage': 'cleanupPercentage',
            'index_max_bulk_size': 'maxBulkSize',
            'index_bulk_preparation_timeout_ms': 'bulkPreparationTimeout',
        },
        'ahtSettings': {
            'aht_sync_threshold': 'syncThreshold',
            'aht_write_buffer_size': 'writeBufferSize',
        },
        'truncationSettings': {
            'history_retention_period_ms': 'retentionPeriod',
            'history_truncation_frequency_ms': 'truncationFrequency',
        },
    }
    for group_name, fields in nested.items():
        group = {
            native: _nullable(draft[field_id])
            for field_id, native in fields.items()
            if field_id in draft and draft[field_id] not in (None, '')
        }
        if group:
            settings[group_name] = group
    return settings


def _compile_control(request):
    kind = request['resource_kind']
    operation = request['operation_id']
    draft = request.get('draft') or {}
    method = 'POST'
    database = None
    api_version = 1
    if kind == 'server' and operation == 'health':
        method, path, body = 'GET', '/health', None
    elif kind == 'database':
        name = _name(draft.get('name'), 'database') if (
            operation == 'create'
        ) else _target_name(request, 'database')
        if operation == 'create':
            path = '/db/create/v2'
            body = {
                'name': name,
                'settings': _database_settings(draft, include_defaults=True),
                'ifNotExists': bool(draft.get('if_not_exists', False)),
            }
        elif operation == 'update_settings':
            path = '/db/update/v2'
            body = {'database': name, 'settings': _database_settings(draft)}
        elif operation in {'load', 'unload', 'drop'}:
            path = {
                'load': '/db/load', 'unload': '/db/unload',
                'drop': '/db/delete',
            }[operation]
            body = {'database': name}
        elif operation == 'flush_index':
            method, path, database = 'GET', '/db/flushindex', name
            body = None
            query = urllib.parse.urlencode({
                'cleanupPercentage': draft.get('cleanup_percentage', 0),
                'synced': str(bool(draft.get('synced', True))).lower(),
            })
            path += '?' + query
        elif operation == 'compact_index':
            method, path, body, database = (
                'GET', '/db/compactindex', None, name
            )
        elif operation == 'truncate_history':
            path = '/db/truncate'
            body = {
                'database': name,
                'retentionPeriod': str(int(draft['retention_period_ms'])),
            }
        else:
            raise RelationalClientError(
                'immudb database operation is unavailable')
    elif kind == 'user':
        username = _name(draft.get('name'), 'username') if (
            operation == 'create'
        ) else _target_name(request, 'username')
        if operation == 'create':
            permission = {'read': 1, 'readwrite': 2, 'admin': 254}.get(
                draft.get('permission'))
            if permission is None:
                raise RelationalClientError(
                    'immudb user permission is invalid')
            path = '/user'
            body = {
                'user': _bytes(username), 'password': _bytes(
                    draft.get('password')), 'permission': permission,
                'database': _name(draft.get('database'), 'database'),
            }
        elif operation == 'change_password':
            path = '/user/password/change'
            body = {
                'user': _bytes(username),
                'oldPassword': _bytes(draft.get('old_password') or ''),
                'newPassword': _bytes(draft.get('new_password')),
            }
        elif operation == 'set_active':
            path = '/user/setactiveUser'
            body = {'username': username, 'active': bool(draft['active'])}
        elif operation == 'drop':
            path, body = None, None
            method = None
        else:
            raise RelationalClientError('immudb user operation is unavailable')
    elif kind == 'permission' and operation in {'grant', 'revoke'}:
        permission = {'read': 1, 'readwrite': 2, 'admin': 254}.get(
            draft.get('permission'))
        if permission is None:
            raise RelationalClientError('immudb permission is invalid')
        path = '/user/changepermission'
        body = {
            'action': 'GRANT' if operation == 'grant' else 'REVOKE',
            'username': _name(draft.get('username'), 'username'),
            'database': _name(draft.get('database'), 'database'),
            'permission': permission,
        }
    elif kind == 'permission' and operation in {'grant_sql', 'revoke_sql'}:
        privileges = draft.get('privileges')
        allowed = {
            'SELECT', 'CREATE', 'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER',
        }
        if not isinstance(privileges, list) or not privileges or any(
                value not in allowed for value in privileges):
            raise RelationalClientError('immudb SQL privileges are invalid')
        path = '/user/changesqlprivileges'
        body = {
            'action': 'GRANT' if operation == 'grant_sql' else 'REVOKE',
            'username': _name(draft.get('username'), 'username'),
            'database': _name(draft.get('database'), 'database'),
            'privileges': privileges,
        }
    elif kind == 'index' and operation in {'create', 'drop'}:
        database = str(
            (request.get('_provider_route') or {}).get('database') or
            (request.get('_provider_route') or {}).get('dbname') or
            'defaultdb'
        )
        if operation == 'create':
            table = _name(draft.get('table'), 'index table')
            columns = _sql_identifier_list(
                draft.get('columns'), 'index column'
            )
            unique = 'UNIQUE ' if draft.get('unique', False) else ''
        else:
            _target, native = _target_native(request, 'index')
            table = _name(native.get('table'), 'index table')
            columns = _sql_identifier_list(
                native.get('columns'), 'index column'
            )
            unique = ''
        verb = f'CREATE {unique}INDEX' if operation == 'create' else (
            'DROP INDEX'
        )
        source = f'{verb} ON {table} ({", ".join(columns)})'
        path = '/db/sqlexec'
        body = {'sql': source, 'params': [], 'noWait': False}
    elif kind == 'key' and operation in {'insert', 'update', 'delete'}:
        database = str(
            (request.get('_provider_route') or {}).get('database') or
            (request.get('_provider_route') or {}).get('dbname') or
            'defaultdb'
        )
        if operation == 'insert':
            key = _encoded_bytes(
                draft.get('key'), draft.get('key_encoding'), 'key'
            )
        else:
            _target, native = _target_native(request, 'key')
            key = native.get('key_base64')
            if not isinstance(key, str) or not key:
                raise RelationalClientError(
                    'immudb key target lacks native byte identity'
                )
        if operation == 'delete':
            path = '/db/deletekey'
            body = {'keys': [key], 'sinceTx': '0', 'noWait': False}
        else:
            value = _encoded_bytes(
                draft.get('value'), draft.get('encoding'), 'value'
            )
            metadata = {}
            if draft.get('expires_at') is not None:
                metadata['expiration'] = {
                    'expiresAt': str(int(draft['expires_at']))
                }
            path = '/db/set'
            body = {
                'KVs': [{
                    'key': key, 'value': value,
                    **({'metadata': metadata} if metadata else {}),
                }],
                'noWait': False, 'preconditions': [],
            }
    elif kind == 'collection':
        api_version = 2
        if operation == 'create':
            name = _name(draft.get('name'), 'collection')
            fields = _collection_fields(draft.get('fields', []))
            body = {
                'name': name,
                'documentIdFieldName': _name(
                    draft.get('document_id_field'), 'document ID field'
                ),
                'fields': fields,
                'indexes': _collection_indexes(
                    draft.get('indexes', []),
                    {item['name'] for item in fields},
                ),
            }
            path = '/collection/' + urllib.parse.quote(name, safe='')
        else:
            target, _native = _target_native(request, 'collection')
            name = _name(target.get('display_name'), 'collection')
            path = '/collection/' + urllib.parse.quote(name, safe='')
            if operation == 'alter':
                method = 'PUT'
                body = {
                    'name': name,
                    'documentIdFieldName': _name(
                        draft.get('document_id_field'),
                        'document ID field',
                    ),
                }
            elif operation == 'drop':
                method, body = 'DELETE', None
            else:
                raise RelationalClientError(
                    'immudb collection operation is unavailable'
                )
    elif kind == 'collection-index' and operation in {'create', 'drop'}:
        api_version = 2
        if operation == 'create':
            collection = _name(draft.get('collection'), 'collection')
            indexes = _collection_indexes([{
                'fields': draft.get('fields'),
                'unique': draft.get('unique', False),
            }])
            body = {
                'collectionName': collection, **indexes[0],
            }
            path = (
                '/collection/' + urllib.parse.quote(collection, safe='') +
                '/index'
            )
        else:
            target, native = _target_native(request, 'collection-index')
            target_path = target.get('display_path')
            if not isinstance(target_path, list) or len(target_path) < 3:
                raise RelationalClientError(
                    'immudb collection index path is invalid'
                )
            collection = _name(target_path[-2], 'collection')
            fields = native.get('fields')
            indexes = _collection_indexes([{'fields': fields}])
            method = 'DELETE'
            body = None
            path = (
                '/collection/' + urllib.parse.quote(collection, safe='') +
                '/index?' + urllib.parse.urlencode([
                    ('fields', field) for field in indexes[0]['fields']
                ])
            )
    elif kind == 'document' and operation in {'insert', 'update', 'delete'}:
        api_version = 2
        if operation == 'insert':
            collection = _name(draft.get('collection'), 'collection')
            document = draft.get('document')
            if not isinstance(document, Mapping):
                raise RelationalClientError('immudb document is invalid')
            body = {
                'collectionName': collection,
                'documents': [copy.deepcopy(dict(document))],
            }
            path = (
                '/collection/' + urllib.parse.quote(collection, safe='') +
                '/documents'
            )
        else:
            collection, query = _document_target_query(request)
            path = (
                '/collection/' + urllib.parse.quote(collection, safe='') +
                '/documents/' + (
                    'replace' if operation == 'update' else 'delete'
                )
            )
            if operation == 'update':
                method = 'PUT'
                document = draft.get('document')
                if not isinstance(document, Mapping):
                    raise RelationalClientError(
                        'immudb replacement document is invalid'
                    )
                body = {
                    'query': query,
                    'document': copy.deepcopy(dict(document)),
                }
            else:
                body = {'query': query}
    else:
        raise RelationalClientError('immudb control operation is unavailable')
    return {
        'provider_action': {
            'method': method, 'path': path, 'body': body,
            'database': database,
            'api_version': api_version,
            **({
                'pre_actions': [{
                    'method': 'POST', 'path': '/db/unload',
                    'body': {'database': name}, 'database': None,
                }],
            } if kind == 'database' and operation == 'drop' else {}),
            **({
                'source': f'DROP USER {username}',
            } if kind == 'user' and operation == 'drop' else {}),
        },
        'action_preview': {
            'transport': (
                'postgresql-wire' if kind == 'user' and operation == 'drop'
                else 'immudb-document-rest-v2' if api_version == 2
                else 'immudb-rest-v1'
            ),
            'operation': f'{kind}.{operation}',
            'provider_constructed': True,
            'sensitive_values_redacted': kind in {
                'user', 'key', 'document',
            } or bool(
                (body or {}).get('settings', {}).get(
                    'replicationSettings', {}).get('primaryPassword')
            ),
        },
        'impact': {
            'scope': (
                'database' if kind in {
                    'key', 'collection', 'collection-index', 'document',
                } else 'cluster'
            ),
            'target_resource_id': (
                request.get('target_resource') or {}).get('resource_id'),
            'availability_risk': 'high' if operation in {
                'drop', 'unload', 'truncate_history'
            } else 'medium',
            'data_movement_possible': kind in {'database', 'replica'},
        },
        'provider_operation_observation': {
            'provider_finality_authority': True,
            'automatic_mutation_retry': False,
        },
    }


def _bytes(value):
    if not isinstance(value, str) or '\x00' in value:
        raise RelationalClientError('immudb byte field is invalid')
    return base64.b64encode(value.encode('utf-8')).decode('ascii')


def _encoded_bytes(value, encoding, label):
    if not isinstance(value, str) or '\x00' in value:
        raise RelationalClientError(f'immudb {label} is invalid')
    if encoding == 'utf8':
        payload = value.encode('utf-8')
    elif encoding == 'base64':
        try:
            payload = base64.b64decode(value, validate=True)
        except ValueError:
            raise RelationalClientError(
                f'immudb {label} is not valid base64'
            ) from None
    else:
        raise RelationalClientError('immudb value encoding is invalid')
    return base64.b64encode(payload).decode('ascii')


def _target_native(request, kind):
    target = request.get('target_resource')
    if not isinstance(target, Mapping) or target.get(
            'resource_kind') != kind:
        raise RelationalClientError(f'immudb {kind} target is required')
    native = target.get('native')
    if not isinstance(native, Mapping):
        native = target.get('extensions', {}).get(
            PROFILE.engine_id, {}
        ).get('native')
    return target, dict(native) if isinstance(native, Mapping) else {}


def _collection_fields(value):
    if not isinstance(value, list):
        raise RelationalClientError('immudb collection fields are invalid')
    admitted = {'STRING', 'BOOLEAN', 'INTEGER', 'DOUBLE', 'UUID'}
    fields = []
    names = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise RelationalClientError(
                'immudb collection field is invalid'
            )
        name = _name(item.get('name'), 'collection field')
        field_type = str(item.get('type', '')).upper()
        if field_type not in admitted or name in names:
            raise RelationalClientError(
                'immudb collection field is invalid'
            )
        names.add(name)
        fields.append({'name': name, 'type': field_type})
    return fields


def _sql_identifier_list(value, label):
    if not isinstance(value, list) or not value:
        raise RelationalClientError(f'immudb {label}s are invalid')
    values = [_name(item, label) for item in value]
    if len(values) != len(set(values)):
        raise RelationalClientError(f'immudb {label}s are invalid')
    return values


def _collection_indexes(value, field_names=None):
    if not isinstance(value, list):
        raise RelationalClientError('immudb collection indexes are invalid')
    indexes = []
    seen = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise RelationalClientError(
                'immudb collection index is invalid'
            )
        raw_fields = item.get('fields')
        if not isinstance(raw_fields, list) or not raw_fields:
            raise RelationalClientError(
                'immudb collection index fields are invalid'
            )
        fields = tuple(
            _name(field, 'collection index field') for field in raw_fields
        )
        if len(fields) != len(set(fields)) or fields in seen or (
            field_names is not None and
            not set(fields).issubset(field_names)
        ):
            raise RelationalClientError(
                'immudb collection index fields are invalid'
            )
        seen.add(fields)
        indexes.append({
            'fields': list(fields),
            'isUnique': bool(item.get('unique', False)),
        })
    return indexes


def _document_target_query(request):
    target, native = _target_native(request, 'document')
    path = target.get('display_path')
    if not isinstance(path, list) or len(path) < 3:
        raise RelationalClientError('immudb document path is invalid')
    collection = _name(path[-2], 'collection')
    document_id = path[-1]
    id_field = _name(
        native.get('document_id_field') or '_id', 'document ID field'
    )
    if not isinstance(document_id, str) or not document_id:
        raise RelationalClientError('immudb document ID is invalid')
    return collection, {
        'collectionName': collection,
        'expressions': [{
            'fieldComparisons': [{
                'field': id_field, 'operator': 'EQ', 'value': document_id,
            }],
        }],
        'orderBy': [], 'limit': 1,
    }


def _parts(request):
    plan = request.get('plan')
    payload = request.get('provider_payload')
    if not isinstance(plan, Mapping) or not isinstance(payload, Mapping):
        raise RelationalClientError('immudb operation handle is invalid')
    route = payload.get('route')
    compiled = payload.get('compiled')
    if not isinstance(route, Mapping) or not isinstance(compiled, Mapping):
        raise RelationalClientError('immudb operation route is unavailable')
    return dict(plan), dict(route), dict(compiled)


def _execute_control(client, request):
    _plan, route, compiled = _parts(request)
    action = dict(compiled.get('provider_action') or {})
    source = action.get('source')
    if isinstance(source, str):
        connection = client._connect({'route': route})
        cursor = None
        try:
            connection.autocommit = True
            cursor = connection.cursor()
            cursor.execute(source)
        except Exception as exc:
            try:
                connection.rollback()
            except Exception:
                pass
            raise RelationalClientError(
                'immudb SQL administration failed '
                f'({type(exc).__name__})'
            ) from None
        finally:
            if cursor is not None:
                client._safe_close(cursor)
            client._forget_and_close(connection)
        response = {
            'provider_response_observed': True,
            'autocommit_requested': True,
            'commit_requested': False,
        }
    else:
        api_version = action.pop('api_version', 1)
        if api_version == 2:
            action.pop('database', None)
            session_id = client._document_session(route)
            try:
                document = client._document_request(
                    route, session_id, **action
                )
            finally:
                try:
                    client._document_request(
                        route, session_id, 'POST',
                        '/authorization/session/close', {},
                    )
                    session_closed = True
                except RelationalClientError:
                    # A session cleanup failure must not erase an already
                    # observed mutation response or trigger a replay.
                    session_closed = False
            response = {
                'document': document,
                'document_session_closed': session_closed,
                'provider_response_observed': True,
                'provider_finality_authority': True,
                'automatic_mutation_retry': False,
            }
        else:
            action.pop('api_version', None)
            pre_actions = action.pop('pre_actions', [])
            prior = [
                client.rest_admin(route, **dict(item))
                for item in pre_actions
            ]
            response = client.rest_admin(route, **action)
            if prior:
                response['provider_pre_action_responses'] = prior
    return {
        'accepted': True,
        'provider_response': response,
        'provider_finality_authority': True,
        'automatic_mutation_retry': False,
    }


def _inspect_control(client, request):
    plan, route, compiled = _parts(request)
    kind = plan['resource_kind']
    if kind == 'server':
        response = client.rest_admin(route, 'GET', '/serverinfo', None)
    elif kind == 'database':
        response = client.rest_admin(route, 'POST', '/db/list/v2', {})
    elif kind in {'user', 'permission'}:
        response = client.rest_admin(route, 'GET', '/user/list', None)
    elif kind in {
        'index', 'key', 'collection', 'collection-index', 'document',
    }:
        resources = client.list_resources({'route': route})
        target_id = (plan.get('target_resource') or {}).get('resource_id')
        response = {
            'resources': [
                item for item in resources
                if item.get('resource_kind') == kind and (
                    target_id is None or item.get('resource_id') == target_id
                )
            ],
            'bounded_provider_discovery': True,
        }
    else:
        action = compiled.get('provider_action') or {}
        response = client.rest_admin(route, **action)
    return {
        'provider_observation': response,
        'provider_observation_only': True,
        'provider_finality_authority': True,
    }


def _contains_name(value, expected):
    if not isinstance(expected, str):
        return None
    names = set()

    def visit(item, key=None):
        if isinstance(item, Mapping):
            for child_key, child in item.items():
                visit(child, child_key)
        elif isinstance(item, list):
            for child in item:
                visit(child, key)
        elif isinstance(item, str) and key in {
                'name', 'database', 'username', 'user'}:
            if key == 'user':
                try:
                    names.add(base64.b64decode(
                        item, validate=True).decode('utf-8'))
                except (ValueError, UnicodeDecodeError):
                    names.add(item)
            else:
                names.add(item)

    visit(value)
    return expected in names


def _named_item(document, collection, expected, name_field):
    if not isinstance(document, Mapping):
        return None
    for item in document.get(collection, []):
        if not isinstance(item, Mapping):
            continue
        name = item.get(name_field)
        if name_field == 'user' and isinstance(name, str):
            try:
                name = base64.b64decode(
                    name, validate=True).decode('utf-8')
            except (ValueError, UnicodeDecodeError):
                pass
        if name == expected:
            return item
    return None


def _mapping_contains(actual, expected):
    if not isinstance(expected, Mapping):
        return actual == expected
    if not isinstance(actual, Mapping):
        return False
    return all(
        key in actual and _mapping_contains(actual[key], value)
        for key, value in expected.items()
    )


def _post_validate(client, request):
    plan, route, _compiled = _parts(request)
    observation = _inspect_control(client, request)
    document = observation['provider_observation'].get('document')
    operation = plan['operation_id']
    draft = plan.get('draft', {})
    if plan['resource_kind'] == 'server':
        confirmed = (
            isinstance(document, Mapping) and
            document.get('version') == PROFILE.exact_version
        )
    elif plan['resource_kind'] == 'database':
        expected = (
            draft.get('name') if operation == 'create'
            else (plan.get('target_resource', {}).get('display_path') or [
                None
            ])[-1]
        )
        item = _named_item(document, 'databases', expected, 'name')
        if operation == 'drop':
            confirmed = item is None
        elif operation == 'load':
            confirmed = (
                isinstance(item, Mapping) and item.get('loaded') is True
            )
        elif operation == 'unload':
            confirmed = (
                isinstance(item, Mapping) and item.get('loaded') is False
            )
        elif operation == 'update_settings':
            confirmed = (
                isinstance(item, Mapping) and _mapping_contains(
                    item.get('settings'), _database_settings(draft)
                )
            )
        else:
            confirmed = item is not None
    elif plan['resource_kind'] == 'user':
        expected = (
            draft.get('name') if operation == 'create'
            else (plan.get('target_resource', {}).get('display_path') or [
                None
            ])[-1]
        )
        item = _named_item(document, 'users', expected, 'user')
        confirmed = item is not None
        if operation == 'drop' and isinstance(item, Mapping):
            confirmed = bool(item.get('active', False)) is False
        if operation == 'set_active' and isinstance(item, Mapping):
            confirmed = bool(item.get('active', False)) is bool(
                draft.get('active')
            )
    elif plan['resource_kind'] == 'permission':
        username = draft.get('username')
        item = _named_item(document, 'users', username, 'user')
        if not isinstance(item, Mapping):
            confirmed = False
        elif operation in {'grant', 'revoke'}:
            permission = {'read': 1, 'readwrite': 2, 'admin': 254}.get(
                draft.get('permission'))
            present = any(
                value.get('database') == draft.get('database') and
                value.get('permission') == permission
                for value in item.get('permissions', [])
                if isinstance(value, Mapping)
            )
            confirmed = present if operation == 'grant' else not present
        else:
            current = {
                value.get('privilege')
                for value in item.get('sqlPrivileges', [])
                if isinstance(value, Mapping) and
                value.get('database') == draft.get('database')
            }
            requested = set(draft.get('privileges', []))
            confirmed = (
                requested <= current if operation == 'grant_sql'
                else requested.isdisjoint(current)
            )
    elif plan['resource_kind'] in {
        'index', 'key', 'collection', 'collection-index', 'document',
    }:
        resources = client.list_resources({'route': route})
        kind = plan['resource_kind']
        target = plan.get('target_resource') or {}
        if kind == 'index' and operation == 'create':
            expected_table = draft.get('table')
            expected_columns = draft.get('columns')
            matches = [
                item for item in resources
                if item.get('resource_kind') == kind and
                item.get('native', {}).get('table') == expected_table and
                item.get('native', {}).get('columns') == expected_columns and
                item.get('native', {}).get('unique') is bool(
                    draft.get('unique', False)
                )
            ]
        elif kind == 'key' and operation == 'insert':
            encoded = _encoded_bytes(
                draft.get('key'), draft.get('key_encoding'), 'key'
            )
            expected_name = client._byte_label(encoded)
            matches = [
                item for item in resources
                if item.get('resource_kind') == kind and
                item.get('display_name') == expected_name
            ]
        elif kind == 'collection' and operation == 'create':
            matches = [
                item for item in resources
                if item.get('resource_kind') == kind and
                item.get('display_name') == draft.get('name')
            ]
        elif kind == 'collection-index' and operation == 'create':
            expected_name = ', '.join(str(item) for item in draft.get(
                'fields', []
            ))
            matches = [
                item for item in resources
                if item.get('resource_kind') == kind and
                item.get('display_name') == expected_name and
                draft.get('collection') in item.get('display_path', [])
            ]
        elif kind == 'document' and operation == 'insert':
            response = request.get('provider_result') or {}
            document = (response.get('provider_response') or {}).get(
                'document', {}
            )
            identifiers = set(document.get('documentIds', []))
            matches = [
                item for item in resources
                if item.get('resource_kind') == kind and
                item.get('display_name') in identifiers
            ]
        else:
            target_id = target.get('resource_id')
            matches = [
                item for item in resources
                if item.get('resource_id') == target_id
            ]
        confirmed = not matches if operation in {'delete', 'drop'} or (
            kind in {'collection', 'collection-index'} and
            operation == 'drop'
        ) else bool(matches)
        if (
            confirmed and kind == 'collection' and operation == 'alter'
        ):
            confirmed = matches[0].get('native', {}).get(
                'document_id_field'
            ) == draft.get('document_id_field')
    else:
        confirmed = False
    return {
        'confirmed': confirmed,
        'observation': observation,
        'provider_finality_authority': True,
    }


_BASE_ADMIN = sql_administration(
    'immudb', 'postgresql', (
        'server', 'replica', 'key', 'collection', 'document', 'revision',
        'proof', 'transaction', 'permission',
    )
)
_SUPPORTED = dict(_BASE_ADMIN.dialect.supported)
_SUPPORTED.pop('schema', None)
_SUPPORTED.pop('role', None)
_SUPPORTED.pop('privilege', None)
_SUPPORTED['database'] = frozenset({'inspect'})
_SUPPORTED['view'] = frozenset({'inspect', 'create', 'drop'})
_SUPPORTED['sequence'] = frozenset({'inspect', 'create', 'drop'})
_SUPPORTED['constraint'] = frozenset({'inspect', 'drop'})
_SUPPORTED['user'] = frozenset({'inspect', 'drop'})
_SUPPORTED['ttl'] = frozenset({'inspect'})
_SUPPORTED['collection-index'] = frozenset({'inspect'})
_DIALECT = replace(
    _BASE_ADMIN.dialect,
    supported=_SUPPORTED,
    not_applicable_concepts=frozenset({
        'schemas', 'materialized_views', 'domains', 'types', 'functions',
        'procedures', 'triggers', 'extensions_and_plugins', 'partitions',
        'tablespaces_and_filespaces', 'jobs_and_events',
    }),
    concept_resource_kinds={
        'roles_and_grants': ('user', 'permission'),
    },
    additional_concept_declarations={
        'key_value': {
            'key_browsing': {
                'status': 'read_only', 'resource_kinds': ['key'],
                'reason': (
                    'The native immutable key scan is exposed through the '
                    'provider navigator.'
                ),
                'evidence': ['immudb-native-kv-api'],
            },
            'data_type_editing': {
                'status': 'read_only', 'resource_kinds': ['key'],
                'reason': (
                    'Native byte keys and values are displayed without '
                    'inventing Redis data structures.'
                ),
                'evidence': ['immudb-native-kv-api'],
            },
            'ttl_inspection': {
                'status': 'read_only', 'resource_kinds': ['ttl'],
                'reason': 'Native KV expiration metadata is inspectable.',
                'evidence': ['immudb-native-kv-metadata'],
            },
            'expiration_management': {
                'status': 'read_only', 'resource_kinds': ['ttl'],
                'reason': (
                    'Expiration metadata is visible; mutation remains '
                    'disabled until its native overwrite contract passes.'
                ),
                'evidence': ['immudb-native-kv-metadata'],
            },
            'streams': 'not_applicable',
            'pubsub': 'not_applicable',
            'consumer_groups': 'not_applicable',
            'modules': 'not_applicable',
            'acls': {
                'status': 'supported',
                'resource_kinds': ['user', 'permission'],
                'reason': (
                    'immudb users, database permissions and SQL privileges '
                    'are provider-owned native controls.'
                ),
                'evidence': ['immudb-native-authorization-api'],
            },
            'replication': {
                'status': 'read_only', 'resource_kinds': ['replica'],
                'reason': (
                    'Primary/replica state belongs to native database '
                    'settings rather than a Redis-style cluster.'
                ),
                'evidence': ['immudb-native-database-settings'],
            },
            'sentinel_or_cluster_state': 'not_applicable',
        },
        'document': {
            'databases': {
                'status': 'supported', 'resource_kinds': ['database'],
                'reason': 'Document collections live in native databases.',
                'evidence': ['immudb-native-document-api-v2'],
            },
            'collections': {
                'status': 'read_only', 'resource_kinds': ['collection'],
                'reason': 'Native document collections are discoverable.',
                'evidence': ['immudb-native-document-api-v2'],
            },
            'documents': {
                'status': 'read_only', 'resource_kinds': ['document'],
                'reason': (
                    'Bounded native document search supplies navigator '
                    'documents and revision metadata.'
                ),
                'evidence': ['immudb-native-document-api-v2'],
            },
            'validation_rules': {
                'status': 'read_only', 'resource_kinds': ['collection'],
                'reason': (
                    'Collection field names and scalar types are the native '
                    'document schema contract.'
                ),
                'evidence': ['immudb-native-collection-fields'],
            },
            'indexes': {
                'status': 'read_only',
                'resource_kinds': ['collection-index'],
                'reason': 'Native collection indexes are discoverable.',
                'evidence': ['immudb-native-collection-indexes'],
            },
            'views': 'not_applicable',
            'aggregation_pipelines': 'not_applicable',
            'users_and_roles': {
                'status': 'supported',
                'resource_kinds': ['user', 'permission'],
                'reason': (
                    'Document access uses shared immudb users and database '
                    'permissions.'
                ),
                'evidence': ['immudb-native-authorization-api'],
            },
            'replica_sets_and_sharding': 'not_applicable',
        },
    },
)


class ImmudbAdministration(DistributedSQLControlPlane):
    """Account for exact immudb PostgreSQL-wire DML result semantics."""

    def _compile_identity_dml(self, request):
        statement = super()._compile_identity_dml(request)
        # immudb reports zero in the PG command tag for UPDATE/DELETE. Its
        # native RETURNING path reports the actual cardinality, preserving
        # the common row-identity concurrency check without inference.
        statement['source'] += ' RETURNING 1'
        return statement


ADMINISTRATION = ImmudbAdministration(
    _DIALECT, CONTROL_OPERATIONS, _compile_control,
    inspector=_inspect_control, post_validator=_post_validate,
    action_executor=_execute_control,
)


class ImmudbDBAPIClient(PsycopgPoolDBAPIClient):
    """Join PostgreSQL-wire SQL with exact native REST identity/admin."""

    MAX_REST_BYTES = 16 * 1024 * 1024

    def __init__(self, config, module=None, opener=None,
                 pool_namespace='immudb-unscoped', pool_module=None):
        super().__init__(
            config, pool_namespace, module=module, pool_module=pool_module
        )
        self._opener = opener or urllib.request.urlopen

    def _connect(self, request, overrides=None):
        """Use psycopg's safe text binding for immudb's PG adapter.

        immudb 1.11.0 exposes binary-bound scalar parameters as unknown-type
        byte strings. ``ClientCursor`` keeps parameter quoting in psycopg but
        sends the resulting statement through the compatible text path.
        """
        options = dict(overrides or {})
        cursor_factory = getattr(self.module, 'ClientCursor', None)
        if cursor_factory is not None:
            options.setdefault('cursor_factory', cursor_factory)
        route = self._route(request)
        connection = self._invoke_connector(
            request, self._connector, options
        )
        self._connections.append(connection)
        initializer = self.config.connection_initializer
        if initializer is not None:
            try:
                initializer(connection, route)
            except RelationalClientError:
                self._safe_close(connection)
                raise
            except Exception as exc:
                self._safe_close(connection)
                raise RelationalClientError(
                    'immudb session initialization failed '
                    f'({type(exc).__name__})'
                ) from None
        return connection

    @staticmethod
    def _rest_route(route):
        host = str(route.get('web_host') or route.get('host') or '')
        if not host or any(character in host for character in '/?#@'):
            raise RelationalClientError('immudb REST host is invalid')
        try:
            port = int(route.get('web_port', 8080))
            timeout = float(route.get('web_timeout', 30))
        except (TypeError, ValueError):
            raise RelationalClientError(
                'immudb REST route values are invalid') from None
        if not 1 <= port <= 65535 or not 0 < timeout <= 300:
            raise RelationalClientError(
                'immudb REST route values are outside approved bounds')
        mode = str(route.get('web_tls_mode', 'disable'))
        if mode not in {'disable', 'require', 'verify-ca', 'verify-full'}:
            raise RelationalClientError('immudb REST TLS mode is invalid')
        return host, port, timeout, mode

    @staticmethod
    def _ssl_context(route, mode):
        if mode == 'disable':
            return None
        if mode == 'require':
            return ssl._create_unverified_context()
        context = ssl.create_default_context(cafile=route.get('web_ca_file'))
        context.check_hostname = mode == 'verify-full'
        return context

    def _url(self, route, path, api_root='/api'):
        host, port, timeout, mode = self._rest_route(route)
        address = f'[{host}]' if ':' in host and not host.startswith('[') \
            else host
        scheme = 'http' if mode == 'disable' else 'https'
        return f'{scheme}://{address}:{port}{api_root}{path}', timeout, mode

    def _request(
        self, route, method, path, body=None, token=None, *,
        api_root='/api', extra_headers=None,
    ):
        url, timeout, mode = self._url(route, path, api_root)
        data = None if body is None else json.dumps(
            body, separators=(',', ':')).encode('utf-8')
        headers = {'Accept': 'application/json'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        if token is not None:
            headers['Authorization'] = 'Bearer ' + token
        if extra_headers:
            headers.update(extra_headers)
        try:
            with self._opener(
                urllib.request.Request(
                    url, data=data, headers=headers, method=method),
                timeout=timeout,
                context=self._ssl_context(route, mode),
            ) as response:
                payload = response.read(self.MAX_REST_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = None
            try:
                payload = exc.read(self.MAX_REST_BYTES + 1)
                document = json.loads(payload)
                if isinstance(document, Mapping):
                    code = document.get('code')
                    message = document.get('message') or document.get('error')
                    detail = f' code={code!r} message={message!r}'
            except (OSError, ValueError):
                pass
            raise RelationalClientError(
                f'immudb REST request failed (HTTP {exc.code})'
                f'{detail or ""}'
            ) from None
        except (OSError, urllib.error.URLError) as exc:
            raise RelationalClientError(
                f'immudb REST request failed ({type(exc).__name__})'
            ) from None
        if len(payload) > self.MAX_REST_BYTES:
            raise RelationalClientError('immudb REST response exceeds limit')
        if not payload:
            return {}
        try:
            value = json.loads(payload)
        except ValueError:
            raise RelationalClientError(
                'immudb REST response is not JSON') from None
        if not isinstance(value, Mapping):
            raise RelationalClientError('immudb REST response is invalid')
        return dict(value)

    def _document_session(self, route):
        database = str(
            route.get('database') or route.get('dbname') or 'defaultdb'
        )
        response = self._request(
            route, 'POST', '/authorization/session/open', {
                'username': route.get('user'),
                'password': self._password(route),
                'database': database,
            }, api_root='/api/v2',
        )
        session_id = response.get('sessionID')
        if not isinstance(session_id, str) or not session_id:
            raise RelationalClientError(
                'immudb document session identifier is unavailable'
            )
        return session_id

    def _document_request(
        self, route, session_id, method, path, body=None,
    ):
        return self._request(
            route, method, path, body, api_root='/api/v2',
            extra_headers={'grpc-metadata-sessionid': session_id},
        )

    @staticmethod
    def _decoded_bytes(value):
        if not isinstance(value, str):
            return b''
        try:
            return base64.b64decode(value, validate=True)
        except ValueError:
            return value.encode('utf-8', errors='replace')

    @classmethod
    def _byte_label(cls, value):
        decoded = cls._decoded_bytes(value)
        try:
            label = decoded.decode('utf-8')
        except UnicodeDecodeError:
            label = 'base64:' + base64.b64encode(decoded).decode('ascii')
        return label if label and '\x00' not in label else (
            'base64:' + base64.b64encode(decoded).decode('ascii')
        )

    def _password(self, route):
        reference = route.get('credential_reference_id')
        principal = route.get('principal_reference')
        if not isinstance(reference, str) or not isinstance(principal, str):
            raise RelationalClientError(
                'immudb REST authentication requires endpoint credentials')
        acquirer = self.config.secret_acquirer
        if not callable(acquirer):
            raise RelationalClientError(
                'immudb REST credential binding is unavailable')
        lease = acquirer(
            reference, principal, 'connect', 'database_password'
        )
        with lease:
            return lease.use(lambda value: bytes(value).decode('utf-8'))

    def _login(self, route):
        username = route.get('user')
        if not isinstance(username, str) or not username:
            raise RelationalClientError('immudb username is required')
        response = self._request(route, 'POST', '/login', {
            'user': _bytes(username), 'password': _bytes(
                self._password(route)),
        })
        token = response.get('token')
        if not isinstance(token, str) or not token:
            raise RelationalClientError('immudb login token is unavailable')
        return token

    def rest_admin(self, route, method, path, body=None, database=None):
        route = copy.deepcopy(dict(route))
        token = self._login(route)
        if database is not None:
            selected = self._request(
                route, 'GET', '/db/use/' + urllib.parse.quote(
                    _name(database, 'database'), safe=''),
                token=token,
            )
            token = selected.get('token')
            if not isinstance(token, str) or not token:
                raise RelationalClientError(
                    'immudb database token is unavailable')
        document = self._request(route, method, path, body, token)
        return {
            'document': document,
            'provider_response_observed': True,
            'provider_finality_authority': True,
            'automatic_mutation_retry': False,
        }

    def list_resources(self, request):
        """Join SQL catalogs with bounded native KV/document discovery."""
        values = {
            item['resource_id']: item for item in super().list_resources(
                request
            )
        }
        route = self._route(request)
        generation = str(request.get('capability_generation') or 'current')
        database = str(
            route.get('database') or route.get('dbname') or 'defaultdb'
        )
        databases = self.rest_admin(
            route, 'POST', '/db/list/v2', {}
        )['document']
        for database_item in databases.get('databases', []):
            if not isinstance(database_item, Mapping):
                continue
            name = database_item.get('name')
            if not isinstance(name, str) or not name:
                continue
            item = resource(
                'database', [], name, generation,
                copy.deepcopy(dict(database_item)),
            )
            values[item['resource_id']] = item
            replication = (
                database_item.get('settings', {}).get(
                    'replicationSettings', {}
                )
            )
            if isinstance(replication, Mapping) and replication.get(
                    'replica', {}).get('value') is True:
                item = resource(
                    'replica', [name], 'replication', generation,
                    copy.deepcopy(dict(replication)),
                )
                values[item['resource_id']] = item
        scan = self.rest_admin(
            route, 'POST', '/db/scan', {
                'seekKey': '', 'endKey': '', 'prefix': '', 'desc': False,
                'limit': 100, 'sinceTx': 0, 'noWait': False,
                'inclusiveSeek': True, 'inclusiveEnd': False, 'offset': 0,
            }, database=database,
        )['document']
        for entry in scan.get('entries', []):
            if not isinstance(entry, Mapping):
                continue
            name = self._byte_label(entry.get('key'))
            native = {
                'database': database,
                'key_base64': entry.get('key'),
                'value_base64': entry.get('value'),
                'transaction_id': entry.get('tx'),
                'revision': entry.get('revision'),
                'expired': bool(entry.get('expired', False)),
                'metadata': copy.deepcopy(entry.get('metadata') or {}),
            }
            item = resource('key', [database], name, generation, native)
            values[item['resource_id']] = item
            revision = entry.get('revision')
            if revision is not None:
                item = resource(
                    'revision', [database, name], revision, generation,
                    native,
                )
                values[item['resource_id']] = item
            transaction = entry.get('tx')
            if transaction is not None:
                item = resource(
                    'transaction', [database], transaction, generation,
                    {'transaction_id': transaction},
                )
                values[item['resource_id']] = item
            expiration = (entry.get('metadata') or {}).get('expiration')
            if isinstance(expiration, Mapping):
                item = resource(
                    'ttl', [database, name], 'expiration', generation, {
                        'expires_at': expiration.get('expiresAt'),
                        'expired': bool(entry.get('expired', False)),
                    },
                )
                values[item['resource_id']] = item

        session_id = self._document_session(route)
        try:
            collections = self._document_request(
                route, session_id, 'GET', '/collections'
            )
            for collection in collections.get('collections', []):
                if not isinstance(collection, Mapping):
                    continue
                name = collection.get('name')
                if not isinstance(name, str) or not name:
                    continue
                item = resource(
                    'collection', [database], name, generation, {
                        'document_id_field': collection.get(
                            'documentIdFieldName'
                        ),
                        'fields': copy.deepcopy(
                            collection.get('fields') or []
                        ),
                        'indexes': copy.deepcopy(
                            collection.get('indexes') or []
                        ),
                    },
                )
                values[item['resource_id']] = item
                for index in collection.get('indexes', []):
                    if not isinstance(index, Mapping):
                        continue
                    fields = index.get('fields')
                    if not isinstance(fields, list) or not fields:
                        continue
                    index_name = ', '.join(str(field) for field in fields)
                    item = resource(
                        'collection-index', [database, name], index_name,
                        generation, {
                            'fields': copy.deepcopy(fields),
                            'unique': bool(index.get('isUnique', False)),
                        },
                    )
                    values[item['resource_id']] = item
                result = self._document_request(
                    route, session_id, 'POST',
                    f'/collection/{urllib.parse.quote(name, safe="")}'
                    '/documents/search', {
                        'searchId': '',
                        'query': {
                            'collectionName': name, 'expressions': [],
                            'orderBy': [], 'limit': 50,
                        },
                        'page': 1, 'pageSize': 50, 'keepOpen': False,
                    },
                )
                for revision in result.get('revisions', []):
                    if not isinstance(revision, Mapping):
                        continue
                    document_id = revision.get('documentId')
                    if not isinstance(document_id, str) or not document_id:
                        continue
                    native = copy.deepcopy(dict(revision))
                    native['document_id_field'] = collection.get(
                        'documentIdFieldName'
                    ) or '_id'
                    item = resource(
                        'document', [database, name], document_id,
                        generation, native,
                    )
                    values[item['resource_id']] = item
                    revision_id = revision.get('revision')
                    if revision_id is not None:
                        item = resource(
                            'revision', [database, name, document_id],
                            revision_id, generation, native,
                        )
                        values[item['resource_id']] = item
        finally:
            self._document_request(
                route, session_id, 'POST',
                '/authorization/session/close', {},
            )
        return list(values.values())

    def runtime_identity(self, request, handle=None):
        temporary = handle is None
        connection = handle or self._connect(request)
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute('SELECT version()')
            value = str((cursor.fetchone() or [''])[0])
            if 'immudb' not in value.casefold():
                raise RelationalClientError(
                    'PostgreSQL wire endpoint is not immudb')
            route = self._route(request)
            info = self._request(route, 'GET', '/serverinfo')
            version = str(info.get('version') or '')
            if re.fullmatch(r'\d+\.\d+\.\d+', version) is None:
                raise RelationalClientError('immudb version is unavailable')
            return {
                'engine_id': PROFILE.engine_id,
                'version': version,
                'build_id': f'immudb:{version}:{info.get("startedAt")}',
                'protocol_id': PROFILE.protocol_id,
            }
        finally:
            if cursor is not None:
                self._safe_close(cursor)
            if temporary:
                self._forget_and_close(connection)


def _extras(cursor, _request, generation):
    values = [resource('server', [], 'immudb', generation)]
    for row in optional_rows(
            cursor, 'SELECT rolname FROM pg_catalog.pg_roles'):
        values.append(resource('user', [], row[0], generation))
    return values


def _catalog(connection, request):
    values = []
    for item in postgresql_catalog(
            connection, request, 'immudb', _extras):
        if item['resource_kind'] not in PROFILE.resource_kinds:
            continue
        if item['resource_kind'] == 'index':
            definition = item.get('native', {}).get('definition', '')
            match = re.fullmatch(
                r'CREATE (UNIQUE )?INDEX ON '
                r'([A-Za-z_][A-Za-z0-9_]*) \(([^)]+)\)',
                definition,
            )
            if match is not None:
                item['native'].update({
                    'table': match.group(2),
                    'columns': [
                        value.strip() for value in match.group(3).split(',')
                    ],
                    'unique': match.group(1) is not None,
                })
        values.append(item)
    return values


def _security(connection, request):
    cursor = connection.cursor()
    try:
        cursor.execute('SELECT current_user()')
        current = cursor.fetchone()[0]
        generation = str(
            request.get('capability_generation') or 'current'
        )
        return resource('user', [], current, generation, {
            'current_user': str(current),
            'authorization_model': 'immudb-native',
        })
    finally:
        cursor.close()


class ImmudbProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    return ImmudbProvider(
        context,
        permissions,
        client or create_sql_client(
            PROFILE,
            permissions,
            wire='postgresql',
            version_query='SELECT version()',
            version_parser=lambda _row: PROFILE.exact_version,
            metadata_reader=_catalog,
            administration=ADMINISTRATION,
            client_class=ImmudbDBAPIClient,
            client_options={'pool_namespace': context.pool_namespace},
            security_reader_override=_security,
        ),
    )
