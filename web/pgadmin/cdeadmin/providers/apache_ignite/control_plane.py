"""Typed Apache Ignite 2.17 control-utility administration."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

from pgadmin.cdeadmin.visual_admin import (
    ControlPlaneCatalog,
    ControlPlaneOperation,
    control_plane_field as cp_field,
)

from ..native_distributed import NativeDistributedError


_NAME_PATTERN = r'[A-Za-z0-9_.:@+-]+'


def _choice(field_id, label, values, **options):
    return cp_field(
        field_id, label, 'select', options=[
            {'value': value, 'label': title} for value, title in values
        ], **options
    )


OPERATIONS = (
    ControlPlaneOperation(
        'cluster', 'set_state', 'Set cluster state', 'destructive',
        'topology_admin', (
            _choice('state', 'Cluster state', (
                ('ACTIVE', 'Active'),
                ('ACTIVE_READ_ONLY', 'Active read-only'),
                ('INACTIVE', 'Inactive'),
            ), required=True, default='ACTIVE'),
            cp_field('force', 'Force unsafe deactivation', 'boolean', False,
                     default=False),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'baseline-topology', 'add_nodes', 'Add baseline nodes', 'admin',
        'topology_admin', (
            cp_field('consistent_ids', 'Node consistent IDs', 'json', True,
                     json_type='array'),
        ), target_required=False, impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'baseline-topology', 'remove_nodes', 'Remove baseline nodes',
        'destructive', 'topology_admin', (
            cp_field('consistent_ids', 'Node consistent IDs', 'json', True,
                     json_type='array'),
        ), target_required=False, impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'baseline-topology', 'set_nodes', 'Replace baseline nodes',
        'destructive', 'topology_admin', (
            cp_field('consistent_ids', 'Node consistent IDs', 'json', True,
                     json_type='array'),
        ), target_required=False, impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'baseline-topology', 'set_version', 'Set baseline topology version',
        'destructive', 'topology_admin', (
            cp_field('topology_version', 'Topology version', 'number', True,
                     minimum=1, maximum=9223372036854775807),
        ), target_required=False, impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'baseline-topology', 'configure_auto_adjust',
        'Configure baseline auto-adjust', 'admin', 'topology_admin', (
            cp_field('enabled', 'Enabled', 'boolean', True, default=True),
            cp_field('timeout_ms', 'Adjustment timeout (milliseconds)',
                     'number', False, minimum=0, maximum=86400000,
                     visible_when={'field_id': 'enabled', 'equals': True}),
        ), target_required=False, impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'cache', 'validate_indexes', 'Validate cache indexes', 'read',
        'maintenance_admin', (
            cp_field('check_crc', 'Check page CRC', 'boolean', False,
                     default=False),
            cp_field('check_sizes', 'Check index sizes', 'boolean', False,
                     default=False),
        ), confirmation_required=False, impact_scope='resource',
        long_running=True, post_state_required=False
    ),
    ControlPlaneOperation(
        'cache', 'idle_verify', 'Verify cache partition consistency', 'read',
        'maintenance_admin', (
            cp_field('dump', 'Write detailed dump', 'boolean', False,
                     default=False),
            cp_field('skip_zeros', 'Skip zero counter conflicts', 'boolean',
                     False, default=False),
        ), confirmation_required=False, impact_scope='resource',
        long_running=True, post_state_required=False
    ),
    ControlPlaneOperation(
        'cache', 'reset_lost_partitions', 'Reset lost partitions',
        'destructive', 'maintenance_admin', impact_scope='resource',
        long_running=True
    ),
    ControlPlaneOperation(
        'cache', 'clear', 'Clear all cache entries', 'destructive',
        'maintenance_admin', impact_scope='resource', long_running=True
    ),
    ControlPlaneOperation(
        'cache', 'rebuild_indexes', 'Rebuild cache indexes', 'admin',
        'maintenance_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'service', 'cancel', 'Cancel deployed service', 'destructive',
        'maintenance_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'compute-task', 'cancel', 'Cancel compute task', 'destructive',
        'maintenance_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'snapshot', 'create', 'Create cluster snapshot', 'admin',
        'backup_admin', (
            cp_field('name', 'Snapshot name', 'text', True, max_length=256,
                     pattern=_NAME_PATTERN),
            cp_field('synchronous', 'Wait for completion', 'boolean', False,
                     default=False),
        ), target_required=False, impact_scope='cluster', long_running=True,
        cancellable=True
    ),
    ControlPlaneOperation(
        'snapshot', 'check', 'Check cluster snapshot', 'read',
        'backup_admin', confirmation_required=False, impact_scope='cluster',
        long_running=True, post_state_required=False
    ),
    ControlPlaneOperation(
        'snapshot', 'restore', 'Restore cluster snapshot', 'destructive',
        'restore_admin', (
            cp_field('groups', 'Cache groups', 'json', False, default=[],
                     json_type='array'),
            cp_field('synchronous', 'Wait for completion', 'boolean', False,
                     default=False),
        ), impact_scope='cluster', long_running=True, cancellable=True
    ),
)


CATALOG = ControlPlaneCatalog('apache_ignite', OPERATIONS)


def apply_catalog(catalog):
    """Augment full or capability-filtered catalogs without inventing kinds."""
    resources = {
        item.get('resource_kind') for item in catalog.get('objects', [])
        if isinstance(item, dict)
    }
    operations = tuple(
        operation for operation in OPERATIONS
        if operation.resource_kind in resources
    )
    return ControlPlaneCatalog('apache_ignite', operations).apply(catalog)


def _safe(value, label):
    if not isinstance(value, str) or not value or len(value) > 256 or (
            re.fullmatch(_NAME_PATTERN, value) is None):
        raise NativeDistributedError(f'Ignite {label} is invalid')
    return value


def _list(value, label, maximum=1024):
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise NativeDistributedError(f'Ignite {label} list is invalid')
    result = [_safe(item, label) for item in value]
    if len(result) != len(set(result)):
        raise NativeDistributedError(f'Ignite {label} values must be unique')
    return result


def _target(request):
    target = request.get('target_resource')
    if not isinstance(target, dict):
        raise NativeDistributedError('Ignite control target is required')
    return _safe(target.get('display_name'), 'target')


def compile_action(request):
    kind = request['resource_kind']
    operation = request['operation_id']
    draft = request.get('draft') or {}
    arguments = []
    cancel_arguments = None
    if kind == 'cluster':
        state = draft.get('state')
        if state not in {'ACTIVE', 'ACTIVE_READ_ONLY', 'INACTIVE'}:
            raise NativeDistributedError('Ignite cluster state is invalid')
        arguments = ['--set-state', state]
        if draft.get('force'):
            arguments.append('--force')
        arguments.append('--yes')
    elif kind == 'baseline-topology':
        if operation in {'add_nodes', 'remove_nodes', 'set_nodes'}:
            action = {
                'add_nodes': 'add', 'remove_nodes': 'remove',
                'set_nodes': 'set',
            }[operation]
            arguments = [
                '--baseline', action,
                ','.join(_list(draft.get('consistent_ids'), 'node ID')),
                '--yes',
            ]
        elif operation == 'set_version':
            version = draft.get('topology_version')
            if isinstance(version, bool) or not isinstance(version, int) or (
                    not 1 <= version <= 9223372036854775807):
                raise NativeDistributedError(
                    'Ignite topology version is invalid')
            arguments = [
                '--baseline', 'version', str(version), '--yes',
            ]
        else:
            enabled = draft.get('enabled')
            if not isinstance(enabled, bool):
                raise NativeDistributedError(
                    'Ignite baseline auto-adjust state is invalid')
            arguments = [
                '--baseline', 'auto_adjust',
                'enable' if enabled else 'disable',
            ]
            if enabled and draft.get('timeout_ms') is not None:
                timeout = draft['timeout_ms']
                if isinstance(timeout, bool) or not isinstance(
                        timeout, int) or not 0 <= timeout <= 86400000:
                    raise NativeDistributedError(
                        'Ignite baseline timeout is invalid')
                arguments.extend(['timeout', str(timeout)])
            arguments.append('--yes')
    elif kind == 'cache':
        name = _target(request)
        if operation == 'validate_indexes':
            arguments = ['--cache', 'validate_indexes', name]
            if draft.get('check_crc'):
                arguments.append('--check-crc')
            if draft.get('check_sizes'):
                arguments.append('--check-sizes')
        elif operation == 'idle_verify':
            arguments = ['--cache', 'idle_verify', name]
            if draft.get('dump'):
                arguments.append('--dump')
            if draft.get('skip_zeros'):
                arguments.append('--skip-zeros')
        elif operation == 'reset_lost_partitions':
            arguments = ['--cache', 'reset_lost_partitions', name, '--yes']
        elif operation == 'rebuild_indexes':
            arguments = [
                '--cache', 'indexes_force_rebuild', '--all-nodes',
                '--cache-names', name,
            ]
        else:
            arguments = [
                '--cache', 'clear', '--caches', name, '--yes',
            ]
    elif kind == 'service' and operation == 'cancel':
        arguments = ['--kill', 'service', _target(request)]
    elif kind == 'compute-task' and operation == 'cancel':
        arguments = ['--kill', 'compute', _target(request)]
    elif kind == 'snapshot':
        if operation == 'create':
            name = _safe(draft.get('name'), 'snapshot name')
            arguments = ['--snapshot', 'create', name]
            if draft.get('synchronous'):
                arguments.append('--sync')
            cancel_arguments = ['--snapshot', 'cancel', '--name', name]
        else:
            name = _target(request)
            if operation == 'check':
                arguments = ['--snapshot', 'check', name]
            else:
                arguments = ['--snapshot', 'restore', name]
                groups = draft.get('groups') or []
                if groups:
                    arguments.extend([
                        '--groups', ','.join(_list(groups, 'cache group')),
                    ])
                if draft.get('synchronous'):
                    arguments.append('--sync')
                arguments.append('--yes')
                cancel_arguments = [
                    '--snapshot', 'restore', name, '--cancel',
                ]
    if not arguments:
        raise NativeDistributedError(
            'Ignite control-plane operation is unavailable')
    return {
        'provider_action': {
            'arguments': arguments,
            'cancel_arguments': cancel_arguments,
        },
        'action_preview': {
            'tool': 'control.sh', 'verb': arguments[0],
            'argument_count': len(arguments) - 1,
            'provider_constructed': True,
        },
        'impact': {
            'scope': 'resource' if kind == 'cache' else 'cluster',
            'target_resource_id': (
                request.get('target_resource') or {}
            ).get('resource_id'),
            'availability_risk': 'high' if operation in {
                'set_state', 'remove_nodes', 'set_nodes', 'set_version',
                'reset_lost_partitions', 'clear', 'restore',
            } else 'medium',
            'data_movement_possible': kind in {
                'cluster', 'baseline-topology', 'cache', 'snapshot',
            },
        },
    }


def _route(request):
    payload = request.get('provider_payload')
    route = payload.get('_provider_route') if isinstance(
        payload, dict
    ) else None
    if not isinstance(route, dict):
        raise NativeDistributedError('Ignite control route is unavailable')
    return route


def _trusted_executable(route):
    value = route.get('control_sh_path')
    if not isinstance(value, str) or not value:
        raise NativeDistributedError('Ignite control.sh path is required')
    path = Path(value).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise NativeDistributedError('Ignite control.sh is unavailable')
    return str(path)


def _connection_arguments(route, secrets):
    values = []
    if route.get('auth_mode') == 'username-password':
        values.extend([
            '--user', route['username'], '--password',
            secrets['database_password'],
        ])
    mappings = (
        ('control_ssl_protocols', '--ssl-protocol'),
        ('control_ssl_ciphers', '--ssl-cipher-suites'),
        ('control_ssl_key_algorithm', '--ssl-key-algorithm'),
        ('control_ssl_factory', '--ssl-factory'),
        ('control_keystore_type', '--keystore-type'),
        ('control_keystore_path', '--keystore'),
        ('control_truststore_type', '--truststore-type'),
        ('control_truststore_path', '--truststore'),
    )
    for field, argument in mappings:
        value = route.get(field)
        if value:
            values.extend([argument, str(value)])
    for secret, argument in (
            ('control_keystore_password', '--keystore-password'),
            ('control_truststore_password', '--truststore-password')):
        if secret in secrets:
            values.extend([argument, secrets[secret]])
    return values


def _run(route, arguments, timeout=120, secrets=None):
    if not isinstance(arguments, list) or not arguments or len(
            arguments) > 128 or any(
                not isinstance(value, str) or not value or len(value) > 8192
                or '\x00' in value for value in arguments):
        raise NativeDistributedError('Ignite control arguments are invalid')
    host = route.get('control_host') or route.get('host', '127.0.0.1')
    if not isinstance(host, str) or not re.fullmatch(
            r'[A-Za-z0-9_.:-]+', host):
        raise NativeDistributedError('Ignite control host is invalid')
    port = route.get('control_port', 10800)
    if isinstance(port, bool) or not isinstance(port, int) or not (
            1 <= port <= 65535):
        raise NativeDistributedError('Ignite control port is invalid')
    command = [
        _trusted_executable(route), '--host', host, '--port', str(port),
        *_connection_arguments(route, secrets or {}),
        *arguments,
    ]
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NativeDistributedError(
            'Ignite control request failed to return') from exc
    output = (result.stdout or '') + (result.stderr or '')
    if len(output.encode('utf-8')) > 8 * 1024 * 1024:
        raise NativeDistributedError(
            'Ignite control response exceeds size limit')
    if result.returncode != 0:
        raise NativeDistributedError('Ignite control request was rejected')
    return {
        'exit_code': 0, 'stdout': result.stdout or '',
        'stderr': result.stderr or '',
        'provider_response_observed': True,
        'provider_finality_authority': True,
        'automatic_mutation_retry': False,
    }


def execute_action(request, secrets=None):
    payload = request.get('provider_payload') or {}
    action = payload.get('compiled', {}).get('provider_action', {})
    return {
        'accepted': True,
        'provider_response': _run(
            _route(request), action.get('arguments'), secrets=secrets),
        'provider_finality_authority': True,
        'automatic_mutation_retry': False,
    }


def inspect_action(request, secrets=None):
    plan = request.get('plan') or {}
    kind = plan.get('resource_kind')
    if kind == 'cluster':
        arguments = ['--state']
    elif kind == 'baseline-topology':
        arguments = ['--baseline']
    elif kind == 'snapshot':
        operation = plan.get('operation_id')
        payload = request.get('provider_payload') or {}
        draft = payload.get('draft') or {}
        target = payload.get('target_resource') or plan.get(
            'target_resource') or {}
        name = draft.get('name') or target.get('display_name')
        if operation in {'create', 'check'} and name:
            arguments = ['--snapshot', 'check', _safe(
                name, 'snapshot name')]
        else:
            arguments = ['--snapshot', 'status']
    elif kind == 'cache':
        arguments = ['--cache', 'list', '^' + re.escape(
            _target({'target_resource': plan.get('target_resource')})
        ) + '$']
    else:
        arguments = ['--state']
    return {
        'provider_observation': _run(
            _route(request), arguments, timeout=30, secrets=secrets),
        'provider_observation_only': True,
        'provider_finality_authority': True,
    }


def cancel_action(request, secrets=None):
    payload = request.get('provider_payload') or {}
    arguments = payload.get('compiled', {}).get(
        'provider_action', {}
    ).get('cancel_arguments')
    if not arguments:
        raise NativeDistributedError(
            'Ignite operation is not declared cancellable')
    return _run(_route(request), arguments, secrets=secrets)
