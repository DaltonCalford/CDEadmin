"""Typed Vitess 23 control-plane actions using trusted vtctldclient."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import subprocess

from pgadmin.cdeadmin.sdk import RelationalClientError
from pgadmin.cdeadmin.visual_admin import (
    ControlPlaneOperation,
    control_plane_field as cp_field,
)


def _choice(field_id, label, values, **options):
    return cp_field(
        field_id, label, 'select', options=[
            {'value': value, 'label': title} for value, title in values
        ], **options
    )


_NAME_PATTERN = r'[A-Za-z0-9_.:@+-]+'
OPERATIONS = (
    ControlPlaneOperation(
        'keyspace', 'create', 'Create keyspace', 'admin',
        'topology_admin', (
            cp_field('name', 'Keyspace name', 'text', True,
                     max_length=256, pattern=_NAME_PATTERN),
            cp_field('durability_policy', 'Durability policy', 'text', False,
                     max_length=256, pattern=_NAME_PATTERN),
            cp_field('allow_empty_vschema', 'Allow empty VSchema',
                     'boolean', False, default=False),
        ), target_required=False, impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'keyspace', 'drop', 'Delete keyspace', 'destructive',
        'topology_admin', (
            cp_field('recursive', 'Delete shards and tablets', 'boolean',
                     False, default=False),
            cp_field('force', 'Force lock bypass', 'boolean', False,
                     default=False),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'keyspace', 'rebuild_graph', 'Rebuild keyspace serving graph',
        'admin', 'topology_admin', (
            cp_field('cells', 'Cells', 'json', False, default=[],
                     json_type='array'),
            cp_field('allow_partial', 'Allow partial snapshot keyspace',
                     'boolean', False, default=False),
        ), impact_scope='cluster', long_running=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'shard', 'create', 'Create shard', 'admin', 'topology_admin', (
            cp_field('keyspace', 'Keyspace', 'text', True,
                     max_length=256, pattern=_NAME_PATTERN),
            cp_field('shard', 'Shard', 'text', True,
                     max_length=256, pattern=r'[0-9A-Fa-f-]+'),
        ), target_required=False, impact_scope='shard'
    ),
    ControlPlaneOperation(
        'shard', 'drop', 'Delete shard', 'destructive',
        'topology_admin', (
            cp_field('recursive', 'Delete tablets', 'boolean', False,
                     default=False),
            cp_field('even_if_serving', 'Delete serving shard', 'boolean',
                     False, default=False),
        ), impact_scope='shard', long_running=True
    ),
    ControlPlaneOperation(
        'shard', 'planned_reparent', 'Planned reparent shard', 'admin',
        'replication_admin', (
            cp_field('new_primary', 'New primary tablet alias', 'text', True,
                     max_length=256, pattern=_NAME_PATTERN),
            cp_field('expected_primary', 'Expected current primary', 'text',
                     False, max_length=256, pattern=_NAME_PATTERN),
            cp_field('allow_cross_cell', 'Allow cross-cell promotion',
                     'boolean', False, default=False),
        ), impact_scope='shard', long_running=True
    ),
    ControlPlaneOperation(
        'shard', 'emergency_reparent', 'Emergency reparent shard',
        'destructive', 'replication_admin', (
            cp_field('new_primary', 'New primary tablet alias', 'text', True,
                     max_length=256, pattern=_NAME_PATTERN),
            cp_field('expected_primary', 'Expected prior primary', 'text',
                     False, max_length=256, pattern=_NAME_PATTERN),
            cp_field('prevent_cross_cell', 'Prevent cross-cell promotion',
                     'boolean', False, default=True),
            cp_field('wait_for_all_tablets', 'Wait for all tablets',
                     'boolean', False, default=False),
        ), impact_scope='shard', long_running=True
    ),
    ControlPlaneOperation(
        'shard', 'set_primary_serving', 'Set primary serving state',
        'destructive', 'topology_admin', (
            cp_field('is_serving', 'Primary is serving', 'boolean', True),
        ), impact_scope='shard'
    ),
    ControlPlaneOperation(
        'workflow', 'create_move_tables', 'Create MoveTables workflow',
        'admin', 'replication_admin', (
            cp_field('workflow_name', 'Workflow name', 'text', True,
                     max_length=256, pattern=_NAME_PATTERN),
            cp_field('target_keyspace', 'Target keyspace', 'text', True,
                     max_length=256, pattern=_NAME_PATTERN),
            cp_field('source_keyspace', 'Source keyspace', 'text', True,
                     max_length=256, pattern=_NAME_PATTERN),
            cp_field('tables', 'Tables', 'json', True, json_type='array'),
            _choice('tablet_types', 'Source tablet types', (
                ('primary', 'Primary'), ('replica', 'Replica'),
                ('rdonly', 'Read-only'),
                ('replica,rdonly', 'Replica and read-only'),
            ), required=True, default='primary'),
        ), target_required=False, impact_scope='cluster', long_running=True,
        cancellable=True
    ),
    ControlPlaneOperation(
        'workflow', 'create_reshard', 'Create Reshard workflow', 'admin',
        'replication_admin', (
            cp_field('workflow_name', 'Workflow name', 'text', True,
                     max_length=256, pattern=_NAME_PATTERN),
            cp_field('target_keyspace', 'Keyspace', 'text', True,
                     max_length=256, pattern=_NAME_PATTERN),
            cp_field('source_shards', 'Source shards', 'json', True,
                     json_type='array'),
            cp_field('target_shards', 'Target shards', 'json', True,
                     json_type='array'),
            _choice('tablet_types', 'Source tablet types', (
                ('primary', 'Primary'), ('replica', 'Replica'),
                ('rdonly', 'Read-only'),
                ('replica,rdonly', 'Replica and read-only'),
            ), required=True, default='primary'),
        ), target_required=False, impact_scope='cluster', long_running=True,
        cancellable=True
    ),
    ControlPlaneOperation(
        'workflow', 'start', 'Start workflow', 'admin',
        'replication_admin', impact_scope='cluster', long_running=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'workflow', 'stop', 'Stop workflow', 'admin',
        'replication_admin', impact_scope='cluster', long_running=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'workflow', 'switch_traffic', 'Switch workflow traffic',
        'destructive', 'replication_admin', (
            _choice('workflow_family', 'Workflow family', (
                ('movetables', 'MoveTables'), ('reshard', 'Reshard'),
            ), required=True),
            _choice('tablet_types', 'Tablet traffic', (
                ('all', 'Primary, replica, and read-only'),
                ('primary', 'Primary only'),
                ('replica', 'Replica only'),
                ('rdonly', 'Read-only only'),
                ('replica,rdonly', 'Replica and read-only'),
            ), required=True, default='all'),
            cp_field('cells', 'Cells', 'json', False, default=[],
                     json_type='array'),
            cp_field('timeout_seconds', 'Catch-up timeout (seconds)',
                     'number', False, default=30, minimum=1, maximum=86400),
            cp_field('max_replication_lag_seconds',
                     'Maximum replication lag (seconds)', 'number', False,
                     default=30, minimum=1, maximum=86400),
            cp_field('enable_reverse_replication',
                     'Enable reverse replication', 'boolean', False,
                     default=True),
            cp_field('initialize_target_sequences',
                     'Initialize target sequences', 'boolean', False,
                     default=False, visible_when={
                         'field_id': 'workflow_family',
                         'equals': 'movetables',
                     }),
            cp_field('force', 'Force past non-critical failures',
                     'boolean', False, default=False),
        ), impact_scope='cluster', long_running=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'workflow', 'reverse_traffic', 'Reverse workflow traffic',
        'destructive', 'replication_admin', (
            _choice('workflow_family', 'Workflow family', (
                ('movetables', 'MoveTables'), ('reshard', 'Reshard'),
            ), required=True),
            _choice('tablet_types', 'Tablet traffic', (
                ('all', 'Primary, replica, and read-only'),
                ('primary', 'Primary only'),
                ('replica', 'Replica only'),
                ('rdonly', 'Read-only only'),
                ('replica,rdonly', 'Replica and read-only'),
            ), required=True, default='all'),
            cp_field('cells', 'Cells', 'json', False, default=[],
                     json_type='array'),
            cp_field('timeout_seconds', 'Catch-up timeout (seconds)',
                     'number', False, default=30, minimum=1, maximum=86400),
            cp_field('max_replication_lag_seconds',
                     'Maximum replication lag (seconds)', 'number', False,
                     default=30, minimum=1, maximum=86400),
            cp_field('enable_reverse_replication',
                     'Enable reverse replication', 'boolean', False,
                     default=True),
            cp_field('force', 'Force past non-critical failures',
                     'boolean', False, default=False),
        ), impact_scope='cluster', long_running=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'workflow', 'complete', 'Complete workflow migration',
        'destructive', 'replication_admin', (
            _choice('workflow_family', 'Workflow family', (
                ('movetables', 'MoveTables'), ('reshard', 'Reshard'),
            ), required=True),
            cp_field('keep_data', 'Keep source data', 'boolean', False,
                     default=False, visible_when={
                         'field_id': 'workflow_family',
                         'equals': 'movetables',
                     }),
            cp_field('keep_routing_rules', 'Keep routing rules', 'boolean',
                     False, default=False, visible_when={
                         'field_id': 'workflow_family',
                         'equals': 'movetables',
                     }),
            cp_field('rename_tables', 'Rename retained source tables',
                     'boolean', False, default=False, visible_when={
                         'field_id': 'workflow_family',
                         'equals': 'movetables',
                     }),
            cp_field('ignore_source_keyspace', 'Ignore source keyspace',
                     'boolean', False, default=False, visible_when={
                         'field_id': 'workflow_family',
                         'equals': 'movetables',
                     }),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'workflow', 'drop', 'Delete workflow', 'destructive',
        'replication_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'materialize', 'create', 'Create materialization workflow', 'admin',
        'replication_admin', (
            cp_field('workflow_name', 'Workflow name', 'text', True,
                     max_length=256, pattern=_NAME_PATTERN),
            cp_field('target_keyspace', 'Target keyspace', 'text', True,
                     max_length=256, pattern=_NAME_PATTERN),
            cp_field('source_keyspace', 'Source keyspace', 'text', True,
                     max_length=256, pattern=_NAME_PATTERN),
            cp_field('table_settings', 'Materialized tables', 'json', True,
                     json_type='array'),
            cp_field('reference_tables', 'Reference tables', 'json', False,
                     default=[], json_type='array'),
            _choice('tablet_types', 'Source tablet types', (
                ('primary', 'Primary'), ('replica', 'Replica'),
                ('rdonly', 'Read-only'),
                ('replica,rdonly', 'Replica and read-only'),
            ), required=True, default='primary'),
            cp_field('cells', 'Source cells', 'json', False, default=[],
                     json_type='array'),
            cp_field('stop_after_copy', 'Stop after initial copy',
                     'boolean', False, default=False),
        ), target_required=False, impact_scope='cluster', long_running=True,
        cancellable=True
    ),
    ControlPlaneOperation(
        'materialize', 'start', 'Start materialization', 'admin',
        'replication_admin', impact_scope='cluster', long_running=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'materialize', 'stop', 'Stop materialization', 'admin',
        'replication_admin', impact_scope='cluster', long_running=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'materialize', 'drop', 'Delete materialization', 'destructive',
        'replication_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'routing-rule', 'apply', 'Replace routing rules', 'destructive',
        'topology_admin', (
            cp_field('rules', 'Routing rules', 'json', True,
                     json_type='object'),
            cp_field('cells', 'Rebuild cells', 'json', False, default=[],
                     json_type='array'),
            cp_field('skip_rebuild', 'Skip serving VSchema rebuild',
                     'boolean', False, default=False),
        ), target_required=False, impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'online-ddl', 'launch', 'Launch postponed migration', 'admin',
        'maintenance_admin', impact_scope='cluster', long_running=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'online-ddl', 'retry', 'Retry failed migration', 'admin',
        'maintenance_admin', impact_scope='cluster', long_running=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'online-ddl', 'complete', 'Complete postponed migration', 'admin',
        'maintenance_admin', impact_scope='cluster', long_running=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'online-ddl', 'cancel', 'Cancel migration', 'destructive',
        'maintenance_admin', impact_scope='cluster', long_running=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'online-ddl', 'cleanup', 'Clean up migration artifacts',
        'destructive', 'maintenance_admin', impact_scope='cluster',
        long_running=True, post_state_required=False
    ),
    ControlPlaneOperation(
        'online-ddl', 'throttle', 'Throttle migration', 'admin',
        'maintenance_admin', impact_scope='cluster',
        post_state_required=False
    ),
    ControlPlaneOperation(
        'online-ddl', 'unthrottle', 'Unthrottle migration', 'admin',
        'maintenance_admin', impact_scope='cluster',
        post_state_required=False
    ),
    ControlPlaneOperation(
        'online-ddl', 'force_cutover', 'Force migration cutover',
        'destructive', 'maintenance_admin', impact_scope='cluster',
        long_running=True, post_state_required=False
    ),
    ControlPlaneOperation(
        'vschema', 'rebuild', 'Rebuild serving VSchema', 'admin',
        'topology_admin', (
            cp_field('cells', 'Cells', 'json', False, default=[],
                     json_type='array'),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'tablet', 'ping', 'Ping tablet', 'admin', 'maintenance_admin',
        impact_scope='shard', post_state_required=False
    ),
    ControlPlaneOperation(
        'tablet', 'refresh_state', 'Refresh tablet state', 'admin',
        'maintenance_admin', impact_scope='shard',
        post_state_required=False
    ),
    ControlPlaneOperation(
        'tablet', 'run_health_check', 'Run tablet health check', 'admin',
        'maintenance_admin', impact_scope='shard',
        post_state_required=False
    ),
    ControlPlaneOperation(
        'tablet', 'change_type', 'Change tablet type', 'admin',
        'topology_admin', (
            _choice('tablet_type', 'Tablet type', (
                ('replica', 'Replica'), ('rdonly', 'Read-only'),
                ('spare', 'Spare'),
            ), required=True),
        ), impact_scope='shard'
    ),
    ControlPlaneOperation(
        'tablet', 'set_writable', 'Set tablet writability', 'destructive',
        'topology_admin', (
            cp_field('writable', 'Writable', 'boolean', True),
        ), impact_scope='shard', post_state_required=False
    ),
    ControlPlaneOperation(
        'tablet', 'drop', 'Delete tablet from topology', 'destructive',
        'topology_admin', (
            cp_field('allow_primary', 'Allow primary deletion', 'boolean',
                     False, default=False),
        ), impact_scope='shard', long_running=True
    ),
    ControlPlaneOperation(
        'tablet', 'backup', 'Back up tablet', 'admin', 'backup_admin',
        impact_scope='shard', long_running=True
    ),
    ControlPlaneOperation(
        'tablet', 'restore', 'Restore tablet from backup', 'destructive',
        'restore_admin', (
            cp_field('backup_timestamp', 'Backup timestamp', 'text', False,
                     max_length=128, pattern=r'[0-9TZ:.-]+'),
        ), impact_scope='shard', long_running=True
    ),
)


def _safe_name(value, label, pattern=_NAME_PATTERN):
    if not isinstance(value, str) or not value or len(value) > 256 or (
            re.fullmatch(pattern, value) is None):
        raise RelationalClientError(f'Vitess {label} is invalid')
    return value


def _target(request):
    target = request.get('target_resource')
    if not isinstance(target, dict):
        raise RelationalClientError('Vitess control target is required')
    path = target.get('display_path') or [target.get('display_name')]
    if not isinstance(path, list) or not path:
        raise RelationalClientError('Vitess control target path is invalid')
    return [str(value) for value in path]


def _shard_target(request):
    path = _target(request)
    value = path[-1]
    if '/' in value:
        keyspace, shard = value.split('/', 1)
    elif len(path) >= 2:
        keyspace, shard = path[-2:]
    else:
        raise RelationalClientError('Vitess shard keyspace is unavailable')
    return _safe_name(keyspace, 'keyspace'), _safe_name(
        shard, 'shard', r'[0-9A-Fa-f-]+'
    )


def _workflow_target(request):
    path = _target(request)
    if len(path) < 2:
        raise RelationalClientError(
            'Vitess workflow keyspace is unavailable')
    return _safe_name(path[-2], 'keyspace'), _safe_name(
        path[-1], 'workflow'
    )


def _online_ddl_target(request):
    path = _target(request)
    if len(path) < 2:
        raise RelationalClientError(
            'Vitess online DDL keyspace is unavailable')
    keyspace = _safe_name(path[-2], 'keyspace')
    migration = _safe_name(
        path[-1], 'online DDL migration',
        r'[0-9a-f]{8}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{4}_'
        r'[0-9a-f]{12}',
    )
    return keyspace, migration


def _string_list(value, label, pattern=_NAME_PATTERN, maximum=256):
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise RelationalClientError(f'Vitess {label} list is invalid')
    result = [_safe_name(item, label, pattern) for item in value]
    if len(result) != len(set(result)):
        raise RelationalClientError(f'Vitess {label} values must be unique')
    return result


def _duration_seconds(value, label, default):
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RelationalClientError(f'Vitess {label} is invalid')
    if value < 1 or value > 86400 or int(value) != value:
        raise RelationalClientError(f'Vitess {label} is invalid')
    return f'{int(value)}s'


def _workflow_family(value):
    if value not in {'movetables', 'reshard'}:
        raise RelationalClientError('Vitess workflow family is invalid')
    return value


def _routing_rules(value):
    if not isinstance(value, dict) or set(value) != {'rules'}:
        raise RelationalClientError('Vitess routing rules are invalid')
    rules = value.get('rules')
    if not isinstance(rules, list) or len(rules) > 4096:
        raise RelationalClientError('Vitess routing rules are invalid')
    normalized = []
    origins = set()
    for item in rules:
        if not isinstance(item, dict) or set(item) != {
                'fromTable', 'toTables'}:
            raise RelationalClientError('Vitess routing rule is invalid')
        origin = _safe_name(item.get('fromTable'), 'routing source')
        if origin in origins:
            raise RelationalClientError(
                'Vitess routing rule sources must be unique')
        origins.add(origin)
        targets = _string_list(item.get('toTables'), 'routing target')
        normalized.append({'fromTable': origin, 'toTables': targets})
    return {'rules': normalized}


def _materialize_settings(value):
    if not isinstance(value, list) or not value or len(value) > 256:
        raise RelationalClientError(
            'Vitess materialization table settings are invalid')
    normalized = []
    targets = set()
    for item in value:
        if not isinstance(item, dict) or not set(item).issubset({
                'target_table', 'source_expression', 'create_ddl'}):
            raise RelationalClientError(
                'Vitess materialization table setting is invalid')
        target = _safe_name(item.get('target_table'), 'target table')
        if target in targets:
            raise RelationalClientError(
                'Vitess materialization target tables must be unique')
        targets.add(target)
        expression = item.get('source_expression')
        if not isinstance(expression, str) or not 0 < len(
                expression) <= 65536 or '\x00' in expression or re.match(
                    r'^\s*(?:select|with)\b', expression,
                    flags=re.IGNORECASE) is None:
            raise RelationalClientError(
                'Vitess materialization source expression is invalid')
        setting = {
            'target_table': target,
            'source_expression': expression,
        }
        create_ddl = item.get('create_ddl', 'copy')
        if create_ddl != 'copy':
            raise RelationalClientError(
                'Vitess materialization create DDL must use copy mode')
        copied_table = re.fullmatch(
            r'\s*select\s+\*\s+from\s+(?:`?[A-Za-z0-9_]+`?\.)?'
            r'`?([A-Za-z0-9_]+)`?\s*;?\s*',
            expression, flags=re.IGNORECASE,
        )
        if copied_table is None or copied_table.group(1) != target:
            raise RelationalClientError(
                'Vitess copy materialization must select all columns from '
                'the identically named source table')
        setting['create_ddl'] = create_ddl
        normalized.append(setting)
    return normalized


def compile_action(request):
    kind = request['resource_kind']
    operation = request['operation_id']
    draft = request.get('draft') or {}
    arguments = []
    cancel_arguments = None
    if kind == 'keyspace':
        name = draft.get('name') if operation == 'create' else _target(
            request)[-1]
        name = _safe_name(name, 'keyspace')
        if operation == 'create':
            arguments = ['CreateKeyspace']
            if draft.get('allow_empty_vschema'):
                arguments.append('--allow-empty-vschema')
            if draft.get('durability_policy'):
                arguments.extend([
                    '--durability-policy', _safe_name(
                        draft['durability_policy'], 'durability policy'
                    ),
                ])
            arguments.append(name)
        elif operation == 'drop':
            arguments = ['DeleteKeyspace']
            if draft.get('recursive'):
                arguments.append('--recursive')
            if draft.get('force'):
                arguments.append('--force')
            arguments.append(name)
        elif operation == 'rebuild_graph':
            arguments = ['RebuildKeyspaceGraph']
            cells = draft.get('cells') or []
            if cells:
                arguments.extend([
                    '--cells', ','.join(_string_list(cells, 'cell')),
                ])
            if draft.get('allow_partial'):
                arguments.append('--allow-partial')
            arguments.append(name)
    elif kind == 'shard':
        if operation == 'create':
            keyspace = _safe_name(draft.get('keyspace'), 'keyspace')
            shard = _safe_name(draft.get('shard'), 'shard', r'[0-9A-Fa-f-]+')
        else:
            keyspace, shard = _shard_target(request)
        target = f'{keyspace}/{shard}'
        if operation == 'create':
            arguments = ['CreateShard', target]
        elif operation == 'drop':
            arguments = ['DeleteShards']
            if draft.get('recursive'):
                arguments.append('--recursive')
            if draft.get('even_if_serving'):
                arguments.append('--even-if-serving')
            arguments.append(target)
        elif operation in {'planned_reparent', 'emergency_reparent'}:
            arguments = [
                'PlannedReparentShard' if operation == 'planned_reparent'
                else 'EmergencyReparentShard',
                '--new-primary', _safe_name(
                    draft.get('new_primary'), 'tablet alias'
                ),
            ]
            if draft.get('expected_primary'):
                arguments.extend([
                    '--expected-primary', _safe_name(
                        draft['expected_primary'], 'expected tablet alias'
                    ),
                ])
            if operation == 'planned_reparent' and draft.get(
                    'allow_cross_cell'):
                arguments.append('--allow-cross-cell-promotion')
            if operation == 'emergency_reparent':
                if draft.get('prevent_cross_cell', True):
                    arguments.append('--prevent-cross-cell-promotion')
                if draft.get('wait_for_all_tablets'):
                    arguments.append('--wait-for-all-tablets')
            arguments.append(target)
        elif operation == 'set_primary_serving':
            serving = draft.get('is_serving')
            if not isinstance(serving, bool):
                raise RelationalClientError(
                    'Vitess shard serving state is invalid')
            arguments = [
                'SetShardIsPrimaryServing', target,
                str(serving).lower(),
            ]
    elif kind == 'workflow' and operation.startswith('create_'):
        workflow = _safe_name(draft.get('workflow_name'), 'workflow')
        target = _safe_name(draft.get('target_keyspace'), 'keyspace')
        family = 'movetables' if operation == 'create_move_tables' else (
            'reshard'
        )
        arguments = [
            family, '--workflow', workflow, '--target-keyspace', target,
            'create',
        ]
        if family == 'movetables':
            arguments.extend([
                '--source-keyspace', _safe_name(
                    draft.get('source_keyspace'), 'source keyspace'
                ),
                '--tables', ','.join(_string_list(
                    draft.get('tables'), 'table'
                )),
            ])
        else:
            arguments.extend([
                '--source-shards', ','.join(_string_list(
                    draft.get('source_shards'), 'source shard',
                    r'[0-9A-Fa-f-]+'
                )),
                '--target-shards', ','.join(_string_list(
                    draft.get('target_shards'), 'target shard',
                    r'[0-9A-Fa-f-]+'
                )),
            ])
        tablet_types = draft.get('tablet_types', 'primary')
        if tablet_types not in {
                'primary', 'replica', 'rdonly', 'replica,rdonly'}:
            raise RelationalClientError('Vitess tablet types are invalid')
        arguments.extend(['--tablet-types', tablet_types])
        cancel_arguments = [
            family, '--workflow', workflow, '--target-keyspace', target,
            'cancel',
        ]
    elif kind == 'materialize' and operation == 'create':
        workflow = _safe_name(draft.get('workflow_name'), 'workflow')
        target = _safe_name(draft.get('target_keyspace'), 'keyspace')
        source = _safe_name(draft.get('source_keyspace'), 'source keyspace')
        settings = _materialize_settings(draft.get('table_settings'))
        arguments = [
            'Materialize', '--workflow', workflow,
            '--target-keyspace', target, 'create',
            '--source-keyspace', source, '--table-settings',
            json.dumps(settings, sort_keys=True, separators=(',', ':')),
        ]
        references = draft.get('reference_tables') or []
        if references:
            arguments.extend([
                '--reference-tables', ','.join(_string_list(
                    references, 'reference table')),
            ])
        tablet_types = draft.get('tablet_types', 'primary')
        if tablet_types not in {
                'primary', 'replica', 'rdonly', 'replica,rdonly'}:
            raise RelationalClientError('Vitess tablet types are invalid')
        arguments.extend(['--tablet-types', tablet_types])
        cells = draft.get('cells') or []
        if cells:
            arguments.extend([
                '--cells', ','.join(_string_list(cells, 'cell')),
            ])
        if draft.get('stop_after_copy'):
            arguments.append('--stop-after-copy')
        cancel_arguments = [
            'Workflow', '--keyspace', target, 'delete',
            '--workflow', workflow,
        ]
    elif kind == 'materialize':
        keyspace, workflow = _workflow_target(request)
        if operation == 'drop':
            arguments = [
                'Workflow', '--keyspace', keyspace, 'delete',
                '--workflow', workflow,
            ]
        else:
            arguments = [
                'Materialize', '--workflow', workflow,
                '--target-keyspace', keyspace, operation,
            ]
    elif kind == 'workflow' and operation in {
            'switch_traffic', 'reverse_traffic'}:
        keyspace, workflow = _workflow_target(request)
        family = _workflow_family(draft.get('workflow_family'))
        arguments = [
            family, '--workflow', workflow, '--target-keyspace', keyspace,
            'switchtraffic' if operation == 'switch_traffic'
            else 'reversetraffic',
        ]
        tablet_types = draft.get('tablet_types', 'all')
        admitted_tablet_types = {
            'all', 'primary', 'replica', 'rdonly', 'replica,rdonly',
        }
        if tablet_types not in admitted_tablet_types:
            raise RelationalClientError('Vitess tablet types are invalid')
        if tablet_types != 'all':
            arguments.extend(['--tablet-types', tablet_types])
        cells = draft.get('cells') or []
        if cells:
            arguments.extend(['--cells', ','.join(_string_list(
                cells, 'cell'
            ))])
        arguments.extend([
            '--timeout', _duration_seconds(
                draft.get('timeout_seconds'), 'traffic timeout', 30
            ),
            '--max-replication-lag-allowed', _duration_seconds(
                draft.get('max_replication_lag_seconds'),
                'maximum replication lag', 30
            ),
        ])
        if not draft.get('enable_reverse_replication', True):
            arguments.append('--enable-reverse-replication=false')
        if operation == 'switch_traffic' and family == 'movetables' and (
                draft.get('initialize_target_sequences')):
            arguments.append('--initialize-target-sequences')
        if draft.get('force'):
            arguments.append('--force')
    elif kind == 'workflow' and operation == 'complete':
        keyspace, workflow = _workflow_target(request)
        family = _workflow_family(draft.get('workflow_family'))
        arguments = [
            family, '--workflow', workflow, '--target-keyspace', keyspace,
            'complete',
        ]
        if family == 'movetables':
            for field_id, flag in (
                    ('keep_data', '--keep-data'),
                    ('keep_routing_rules', '--keep-routing-rules'),
                    ('rename_tables', '--rename-tables'),
                    ('ignore_source_keyspace', '--ignore-source-keyspace')):
                if draft.get(field_id):
                    arguments.append(flag)
    elif kind == 'workflow':
        keyspace, workflow = _workflow_target(request)
        command = 'delete' if operation == 'drop' else operation
        arguments = [
            'workflow', '--keyspace', keyspace, command,
            '--workflow', workflow,
        ]
    elif kind == 'routing-rule' and operation == 'apply':
        rules = _routing_rules(draft.get('rules'))
        arguments = [
            'ApplyRoutingRules', '--rules', json.dumps(
                rules, sort_keys=True, separators=(',', ':')
            ),
        ]
        cells = draft.get('cells') or []
        if cells:
            arguments.extend(['--cells', ','.join(_string_list(
                cells, 'cell'
            ))])
        if draft.get('skip_rebuild'):
            arguments.append('--skip-rebuild')
    elif kind == 'online-ddl' and operation in {
            'launch', 'retry', 'complete', 'cancel', 'cleanup', 'throttle',
            'unthrottle', 'force_cutover'}:
        keyspace, migration = _online_ddl_target(request)
        native_operation = (
            'force-cutover' if operation == 'force_cutover' else operation
        )
        arguments = ['OnlineDDL', native_operation, keyspace, migration]
    elif kind == 'vschema' and operation == 'rebuild':
        arguments = ['RebuildVSchemaGraph']
        cells = draft.get('cells') or []
        if cells:
            arguments.extend(['--cells', ','.join(_string_list(
                cells, 'cell'
            ))])
    elif kind == 'tablet':
        tablet = _safe_name(_target(request)[-1], 'tablet alias')
        if operation == 'ping':
            arguments = ['PingTablet', tablet]
        elif operation == 'refresh_state':
            arguments = ['RefreshState', tablet]
        elif operation == 'run_health_check':
            arguments = ['RunHealthCheck', tablet]
        elif operation == 'change_type':
            tablet_type = draft.get('tablet_type')
            if tablet_type not in {'replica', 'rdonly', 'spare'}:
                raise RelationalClientError('Vitess tablet type is invalid')
            arguments = ['ChangeTabletType', tablet, tablet_type]
        elif operation == 'set_writable':
            writable = draft.get('writable')
            if not isinstance(writable, bool):
                raise RelationalClientError(
                    'Vitess tablet writability is invalid')
            arguments = ['SetWritable', tablet, str(writable).lower()]
        elif operation == 'drop':
            arguments = ['DeleteTablets']
            if draft.get('allow_primary'):
                arguments.append('--allow-primary')
            arguments.append(tablet)
        elif operation in {'backup', 'restore'}:
            arguments = ['Backup' if operation == 'backup' else (
                'RestoreFromBackup'
            )]
        if operation == 'restore' and draft.get('backup_timestamp'):
            timestamp = _safe_name(
                draft['backup_timestamp'], 'backup timestamp', r'[0-9TZ:.-]+'
            )
            arguments.extend(['--backup-timestamp', timestamp])
        if operation in {'backup', 'restore'}:
            arguments.append(tablet)
    if not arguments:
        raise RelationalClientError(
            'Vitess control-plane operation is unavailable')
    return {
        'provider_action': {
            'arguments': arguments,
            'cancel_arguments': cancel_arguments,
        },
        'action_preview': {
            'tool': 'vtctldclient',
            'verb': arguments[0],
            'argument_count': len(arguments) - 1,
            'provider_constructed': True,
        },
        'impact': {
            'scope': 'shard' if kind in {'shard', 'tablet'} else 'cluster',
            'target_resource_id': (
                request.get('target_resource') or {}
            ).get('resource_id'),
            'availability_risk': 'high' if operation in {
                'drop', 'emergency_reparent', 'restore', 'set_writable'
            } else 'medium',
            'data_movement_possible': kind in {
                'shard', 'workflow', 'tablet'
            },
        },
    }


def _route(request):
    payload = request.get('provider_payload')
    route = payload.get('route') if isinstance(payload, dict) else None
    if not isinstance(route, dict):
        raise RelationalClientError('Vitess control route is unavailable')
    return route


def _trusted_executable(route):
    value = route.get('vtctldclient_path')
    if not isinstance(value, str) or not value:
        raise RelationalClientError(
            'Vitess vtctldclient executable is required')
    path = Path(value).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise RelationalClientError(
            'Vitess vtctldclient executable is unavailable')
    return str(path)


def _server(route):
    value = route.get('vtctld_server')
    if not isinstance(value, str) or not re.fullmatch(
            r'[A-Za-z0-9_.:\-\[\]]{3,512}', value):
        raise RelationalClientError('Vitess vtctld server is invalid')
    return value


def _trusted_optional_file(route, key, label):
    value = route.get(key)
    if value in {None, ''}:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RelationalClientError(f'Vitess {label} is invalid')
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise RelationalClientError(f'Vitess {label} is unavailable')
    return str(path)


def _bounded_integer(route, key, label, default, minimum, maximum):
    value = route.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not (
            minimum <= value <= maximum):
        raise RelationalClientError(f'Vitess {label} is invalid')
    return value


def _connection_arguments(route):
    arguments = []
    for cert, key, label in (
        ('vtctld_grpc_cert', 'vtctld_grpc_key', 'VTctld client TLS'),
        ('tablet_manager_grpc_cert', 'tablet_manager_grpc_key',
         'tablet-manager client TLS'),
    ):
        if bool(route.get(cert)) != bool(route.get(key)):
            raise RelationalClientError(
                f'Vitess {label} requires both certificate and key'
            )
    for key, flag, label in (
        ('vtctld_grpc_ca', '--vtctld-grpc-ca', 'VTctld CA file'),
        ('vtctld_grpc_cert', '--vtctld-grpc-cert',
         'VTctld client certificate file'),
        ('vtctld_grpc_key', '--vtctld-grpc-key',
         'VTctld client key file'),
        ('vtctld_grpc_crl', '--vtctld-grpc-crl', 'VTctld CRL file'),
        ('grpc_auth_static_client_creds',
         '--grpc-auth-static-client-creds',
         'static gRPC client credentials file'),
        ('tablet_manager_grpc_ca', '--tablet-manager-grpc-ca',
         'tablet-manager CA file'),
        ('tablet_manager_grpc_cert', '--tablet-manager-grpc-cert',
         'tablet-manager client certificate file'),
        ('tablet_manager_grpc_key', '--tablet-manager-grpc-key',
         'tablet-manager client key file'),
        ('tablet_manager_grpc_crl', '--tablet-manager-grpc-crl',
         'tablet-manager CRL file'),
    ):
        value = _trusted_optional_file(route, key, label)
        if value:
            arguments.extend([flag, value])
    for key, flag, label in (
        ('vtctld_grpc_server_name', '--vtctld-grpc-server-name',
         'VTctld certificate server name'),
        ('tablet_manager_grpc_server_name',
         '--tablet-manager-grpc-server-name',
         'tablet-manager certificate server name'),
    ):
        value = route.get(key)
        if value not in {None, ''}:
            if not isinstance(value, str) or re.fullmatch(
                    r'[A-Za-z0-9_.:-]{1,253}', value) is None:
                raise RelationalClientError(f'Vitess {label} is invalid')
            arguments.extend([flag, value])
    compression = route.get('grpc_compression', 'none')
    if compression not in {'none', 'snappy'}:
        raise RelationalClientError('Vitess gRPC compression is invalid')
    if compression != 'none':
        arguments.extend(['--grpc-compression', compression])
    for key, flag, label, default in (
        ('grpc_keepalive_time_seconds', '--grpc-keepalive-time',
         'gRPC keepalive time', 10),
        ('grpc_keepalive_timeout_seconds', '--grpc-keepalive-timeout',
         'gRPC keepalive timeout', 10),
    ):
        if key in route:
            value = _bounded_integer(
                route, key, label, default, 1, 86400)
            arguments.extend([flag, f'{value}s'])
    if 'grpc_max_message_size' in route:
        value = _bounded_integer(
            route, 'grpc_max_message_size', 'gRPC maximum message size',
            16777216, 1024, 1073741824,
        )
        arguments.extend(['--grpc-max-message-size', str(value)])
    if 'tablet_manager_grpc_concurrency' in route:
        value = _bounded_integer(
            route, 'tablet_manager_grpc_concurrency',
            'tablet-manager gRPC concurrency', 8, 1, 1024,
        )
        arguments.extend(['--tablet-manager-grpc-concurrency', str(value)])
    action_timeout = _bounded_integer(
        route, 'vtctld_action_timeout_seconds',
        'VTctld action timeout', 3600, 1, 86400,
    )
    arguments.extend(['--action-timeout', f'{action_timeout}s'])
    return arguments, action_timeout


def _run(route, arguments, timeout=None):
    if not isinstance(arguments, list) or not arguments or len(
            arguments) > 128 or any(
                not isinstance(value, str) or not value or len(value) > 8192
                or '\x00' in value for value in arguments):
        raise RelationalClientError('Vitess tool arguments are invalid')
    connection_arguments, action_timeout = _connection_arguments(route)
    command = [
        _trusted_executable(route), '--server', _server(route),
        *connection_arguments, *arguments,
    ]
    timeout_value = action_timeout + 15 if timeout is None else timeout
    if isinstance(timeout_value, bool) or not isinstance(
            timeout_value, int) or not 1 <= timeout_value <= 86415:
        raise RelationalClientError('Vitess client timeout is invalid')
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True,
            timeout=timeout_value,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RelationalClientError(
            'Vitess control request failed to return') from exc
    output = (result.stdout or '') + (result.stderr or '')
    if len(output.encode('utf-8')) > 8 * 1024 * 1024:
        raise RelationalClientError(
            'Vitess control response exceeds size limit')
    if result.returncode != 0:
        raise RelationalClientError('Vitess control request was rejected')
    return {
        'exit_code': result.returncode,
        'stdout': result.stdout or '',
        'stderr': result.stderr or '',
        'provider_response_observed': True,
        'provider_finality_authority': True,
        'automatic_mutation_retry': False,
    }


def execute_action(_client, request):
    payload = request.get('provider_payload') or {}
    compiled = payload.get('compiled') or {}
    action = compiled.get('provider_action') or {}
    return {
        'accepted': True,
        'provider_response': _run(_route(request), action.get('arguments')),
        'provider_finality_authority': True,
        'automatic_mutation_retry': False,
    }


def _parts(request):
    plan = request.get('plan')
    payload = request.get('provider_payload')
    if not isinstance(plan, dict) or not isinstance(payload, dict):
        raise RelationalClientError('Vitess operation handle is invalid')
    return plan, payload


def inspect_action(_client, request):
    plan, payload = _parts(request)
    original = payload.get('compiled', {}).get('provider_action', {})
    arguments = original.get('arguments') or []
    kind = plan['resource_kind']
    if kind == 'keyspace':
        inspect_arguments = ['GetKeyspaces']
    elif kind == 'shard':
        keyspace, shard = _shard_target({
            'target_resource': plan.get('target_resource')
        })
        inspect_arguments = (
            ['FindAllShardsInKeyspace', keyspace]
            if plan['operation_id'] == 'drop'
            else ['GetShard', f'{keyspace}/{shard}']
        )
    elif kind == 'workflow':
        if plan['operation_id'].startswith('create_'):
            draft = payload.get('compiled_request_draft') or plan.get(
                'draft', {})
            keyspace = draft.get('target_keyspace')
            workflow = draft.get('workflow_name')
        else:
            keyspace, workflow = _workflow_target({
                'target_resource': plan.get('target_resource')
            })
        inspect_arguments = [
            'workflow', '--keyspace', keyspace, 'show',
            '--workflow', workflow,
        ]
    elif kind == 'materialize':
        if plan['operation_id'] == 'create':
            draft = payload.get('compiled_request_draft') or plan.get(
                'draft', {})
            keyspace = draft.get('target_keyspace')
            workflow = draft.get('workflow_name')
        else:
            keyspace, workflow = _workflow_target({
                'target_resource': plan.get('target_resource')
            })
        inspect_arguments = (
            ['Materialize', '--workflow', workflow,
             '--target-keyspace', keyspace, 'show']
        )
    elif kind == 'routing-rule':
        inspect_arguments = ['GetRoutingRules']
    elif kind == 'online-ddl':
        keyspace, migration = _online_ddl_target({
            'target_resource': plan.get('target_resource')
        })
        inspect_arguments = [
            'OnlineDDL', 'show', '--json', keyspace, migration,
        ]
    elif kind == 'tablet':
        tablet = _target({
            'target_resource': plan.get('target_resource')
        })[-1]
        inspect_arguments = (
            ['GetTablets', '--format', 'json', '--tablet-alias', tablet]
            if plan['operation_id'] == 'drop'
            else ['GetTablet', tablet]
        )
    elif kind == 'vschema':
        inspect_arguments = ['GetSrvVSchemas']
    else:
        inspect_arguments = arguments
    return {
        'provider_observation': _run(
            _route(request), inspect_arguments, timeout=30
        ),
        'provider_observation_only': True,
        'provider_finality_authority': True,
    }


def cancel_action(_client, request):
    _plan, payload = _parts(request)
    action = payload.get('compiled', {}).get('provider_action', {})
    arguments = action.get('cancel_arguments')
    if not arguments:
        raise RelationalClientError(
            'Vitess operation is not declared cancellable')
    return _run(_route(request), arguments)


def post_validate_action(client, request):
    observation = inspect_action(client, request)
    text = observation['provider_observation'].get('stdout', '')
    try:
        document = json.loads(text)
    except (TypeError, ValueError):
        document = None
    plan = request.get('plan') or {}
    kind = plan.get('resource_kind')
    operation = plan.get('operation_id')
    target = plan.get('target_resource') or {}
    path = target.get('display_path') or [target.get('display_name')]
    draft = plan.get('draft') or {}
    expected = None
    present = None
    if kind == 'keyspace':
        expected = (
            draft.get('name') if operation == 'create' else path[-1]
        )
        present = _document_contains_named_resource(document, expected)
    elif kind == 'shard':
        if operation == 'create':
            expected = draft.get('shard')
        elif operation == 'set_primary_serving':
            expected = str(draft.get('is_serving')).upper()
            present = expected in _document_values_for_key(
                document, 'is_primary_serving')
        else:
            expected = path[-1].split('/', 1)[-1]
        if operation != 'set_primary_serving':
            present = _document_contains_named_resource(document, expected)
    elif kind in {'workflow', 'materialize'}:
        if operation.startswith('create_') or operation == 'create':
            expected = draft.get('workflow_name')
        else:
            expected = path[-1]
        present = _document_contains_named_resource(document, expected)
    elif kind == 'routing-rule' and operation == 'apply':
        expected = _routing_rules(draft.get('rules'))
        present = document == expected
    elif kind == 'tablet' and operation == 'change_type':
        tablet_type = str(draft.get('tablet_type') or '').lower()
        expected = {
            'replica': {'REPLICA', '2'},
            'rdonly': {'RDONLY', '3'},
            'spare': {'SPARE', '4'},
        }.get(tablet_type, set())
        present = bool(expected.intersection(
            _document_values_for_key(document, 'type')
        ))
    elif kind == 'tablet' and operation == 'drop':
        expected = path[-1]
        present = _document_contains_tablet(document)
    confirmed = bool(
        expected and present is not None and (
            (operation in {'drop', 'complete'} and not present) or
            (operation in {'create', 'create_move_tables',
                           'create_reshard', 'apply', 'change_type',
                           'set_primary_serving'} and
             present)
        )
    )
    return {
        'confirmed': confirmed,
        'reason': (
            'vitess_resource_presence_matches_requested_state'
            if confirmed else 'vitess_provider_state_requires_semantic_review'
        ),
        'provider_document': document,
        'observation': observation,
        'provider_finality_authority': True,
    }


def _workflow_streams(document):
    if isinstance(document, dict):
        workflows = document.get('workflows')
    else:
        workflows = document
    return workflows if isinstance(workflows, list) else []


def catalog_resources(route, generation, keyspaces):
    """Read exact native workflow and VReplication objects for navigation."""
    if not isinstance(route, dict) or not route.get(
            'vtctldclient_path') or not route.get('vtctld_server'):
        return []
    resources = []
    for keyspace in sorted(set(keyspaces or ())):
        try:
            response = _run(
                route, ['GetWorkflows', '--show-all', keyspace], timeout=30,
            )
            document = json.loads(response.get('stdout') or 'null')
        except (RelationalClientError, TypeError, ValueError):
            continue
        for workflow in _workflow_streams(document):
            if not isinstance(workflow, dict):
                continue
            name = workflow.get('name')
            try:
                name = _safe_name(name, 'workflow')
            except RelationalClientError:
                continue
            workflow_type = str(
                workflow.get('workflow_type') or
                workflow.get('workflowType') or 'unknown'
            )
            native = copy.deepcopy(workflow)
            resources.append({
                'resource_id': f'workflow:{keyspace}:{name}',
                'resource_kind': 'workflow', 'display_name': name,
                'display_path': [keyspace, name],
                'authority_path': [keyspace, 'workflow', name],
                'generation': generation, 'native': native,
            })
            if workflow_type.lower() == 'materialize':
                resources.append({
                    'resource_id': f'materialize:{keyspace}:{name}',
                    'resource_kind': 'materialize', 'display_name': name,
                    'display_path': [keyspace, name],
                    'authority_path': [keyspace, 'materialize', name],
                    'generation': generation, 'native': native,
                })
            shard_streams = workflow.get(
                'shard_streams', workflow.get('shardStreams', {}))
            if not isinstance(shard_streams, dict):
                continue
            for shard, group in shard_streams.items():
                streams = group.get('streams', []) if isinstance(
                    group, dict) else []
                for stream in streams:
                    if not isinstance(stream, dict):
                        continue
                    stream_id = stream.get('id')
                    if not isinstance(stream_id, (str, int)):
                        continue
                    display = f'{name}:{shard}:{stream_id}'
                    resources.append({
                        'resource_id': (
                            f'vreplication-stream:{keyspace}:{display}'
                        ),
                        'resource_kind': 'vreplication-stream',
                        'display_name': display,
                        'display_path': [keyspace, name, str(shard),
                                         str(stream_id)],
                        'authority_path': [keyspace, 'workflow', name,
                                           str(shard), str(stream_id)],
                        'generation': generation,
                        'native': copy.deepcopy(stream),
                    })
    return resources


def _document_contains_named_resource(document, expected):
    """Return an exact identifier observation, never an outcome inference."""
    if not isinstance(expected, str) or not expected or document is None:
        return None
    candidates = set()

    def visit(value, key=None):
        if isinstance(value, dict):
            for item_key, item_value in value.items():
                if isinstance(item_key, str):
                    candidates.add(item_key)
                visit(item_value, item_key)
        elif isinstance(value, list):
            for item in value:
                visit(item, key)
        elif isinstance(value, str) and key in {
                'name', 'keyspace', 'shard', 'workflow'}:
            candidates.add(value)

    visit(document)
    return expected in candidates


def _document_values_for_key(document, expected_key):
    values = set()

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == expected_key and isinstance(item, (str, int)):
                    values.add(str(item).upper())
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(document)
    return values


def _document_contains_tablet(document):
    if isinstance(document, list):
        return bool(document)
    if isinstance(document, dict):
        tablets = document.get('tablets')
        if isinstance(tablets, (list, dict)):
            return bool(tablets)
        return bool(document)
    return None
