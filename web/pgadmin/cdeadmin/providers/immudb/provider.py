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
        'collection', 'document', 'revision', 'proof', 'transaction', 'user',
        'permission',
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
    else:
        raise RelationalClientError('immudb control operation is unavailable')
    return {
        'provider_action': {
            'method': method, 'path': path, 'body': body,
            'database': database,
            **({
                'source': f'DROP USER {username}',
            } if kind == 'user' and operation == 'drop' else {}),
        },
        'action_preview': {
            'transport': (
                'postgresql-wire' if kind == 'user' and operation == 'drop'
                else 'immudb-rest'
            ),
            'operation': f'{kind}.{operation}',
            'provider_constructed': True,
            'sensitive_values_redacted': kind == 'user' or bool(
                (body or {}).get('settings', {}).get(
                    'replicationSettings', {}).get('primaryPassword')
            ),
        },
        'impact': {
            'scope': 'cluster',
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
    action = compiled.get('provider_action') or {}
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
        response = client.rest_admin(route, **action)
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
    plan, _route, _compiled = _parts(request)
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
_SUPPORTED.pop('view', None)
_SUPPORTED.pop('sequence', None)
_SUPPORTED.pop('constraint', None)
_SUPPORTED.pop('role', None)
_SUPPORTED.pop('privilege', None)
_SUPPORTED['database'] = frozenset({'inspect'})
_SUPPORTED['user'] = frozenset({'inspect', 'drop'})
_DIALECT = replace(_BASE_ADMIN.dialect, supported=_SUPPORTED)
ADMINISTRATION = DistributedSQLControlPlane(
    _DIALECT, CONTROL_OPERATIONS, _compile_control,
    inspector=_inspect_control, post_validator=_post_validate,
    action_executor=_execute_control,
)


class ImmudbDBAPIClient(RelationalDBAPIClient):
    """Join PostgreSQL-wire SQL with exact native REST identity/admin."""

    MAX_REST_BYTES = 16 * 1024 * 1024

    def __init__(self, config, module=None, opener=None):
        super().__init__(config, module)
        self._opener = opener or urllib.request.urlopen

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

    def _url(self, route, path):
        host, port, timeout, mode = self._rest_route(route)
        address = f'[{host}]' if ':' in host and not host.startswith('[') \
            else host
        scheme = 'http' if mode == 'disable' else 'https'
        return f'{scheme}://{address}:{port}/api{path}', timeout, mode

    def _request(self, route, method, path, body=None, token=None):
        url, timeout, mode = self._url(route, path)
        data = None if body is None else json.dumps(
            body, separators=(',', ':')).encode('utf-8')
        headers = {'Accept': 'application/json'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        if token is not None:
            headers['Authorization'] = 'Bearer ' + token
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
    return [
        item for item in postgresql_catalog(
            connection, request, 'immudb', _extras
        )
        if item['resource_kind'] in PROFILE.resource_kinds
    ]


def _security(connection, request):
    cursor = connection.cursor()
    try:
        cursor.execute('SELECT current_user')
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
            security_reader_override=_security,
        ),
    )
