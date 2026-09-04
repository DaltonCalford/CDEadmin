"""Typed YugabyteDB control-plane actions using trusted ``yb-admin``."""

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


_ID_PATTERN = r'[A-Za-z0-9_.:@+-]+'
_UUID_PATTERN = r'[A-Fa-f0-9-]+'
_HOST_PORT_PATTERN = r'[A-Za-z0-9_.-]+:[0-9]{1,5}'


def _choice(field_id, label, values, **options):
    return cp_field(
        field_id, label, 'select', options=[
            {'value': value, 'label': title} for value, title in values
        ], **options
    )


OPERATIONS = (
    ControlPlaneOperation(
        'placement-policy', 'configure', 'Configure live placement', 'admin',
        'topology_admin', (
            cp_field('placements', 'Cloud/region/zone placements', 'json',
                     True, json_type='array'),
            cp_field('replication_factor', 'Replication factor', 'number',
                     True, minimum=1, maximum=64),
            cp_field('placement_uuid', 'Placement UUID', 'text', False,
                     max_length=256, pattern=_ID_PATTERN),
        ), target_required=False, impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'placement-policy', 'clear', 'Clear live placement', 'destructive',
        'topology_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'table', 'configure_placement', 'Configure table placement', 'admin',
        'topology_admin', (
            cp_field('tablespace', 'Placement tablespace', 'text', True,
                     max_length=256, pattern=_ID_PATTERN),
        ), impact_scope='cluster', long_running=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'node', 'add_blacklist', 'Drain node replicas', 'admin',
        'maintenance_admin', impact_scope='node', long_running=True
    ),
    ControlPlaneOperation(
        'node', 'remove_blacklist', 'Return node replicas', 'admin',
        'maintenance_admin', impact_scope='node', long_running=True
    ),
    ControlPlaneOperation(
        'node', 'add_leader_blacklist', 'Drain node leaders', 'admin',
        'maintenance_admin', impact_scope='node', long_running=True
    ),
    ControlPlaneOperation(
        'node', 'remove_leader_blacklist', 'Return node leaders', 'admin',
        'maintenance_admin', impact_scope='node', long_running=True
    ),
    ControlPlaneOperation(
        'cluster', 'set_load_balancer', 'Set load balancer state', 'admin',
        'topology_admin', (
            cp_field('enabled', 'Enabled', 'boolean', True, default=True),
        ), impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'master', 'leader_stepdown', 'Step down master leader', 'admin',
        'topology_admin', (
            cp_field('destination_uuid', 'Destination master UUID', 'text',
                     False, max_length=256, pattern=_UUID_PATTERN),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'tablet', 'leader_stepdown', 'Step down tablet leader', 'admin',
        'topology_admin', (
            cp_field('destination_uuid', 'Destination TServer UUID', 'text',
                     False, max_length=256, pattern=_UUID_PATTERN),
        ), impact_scope='resource', long_running=True
    ),
    ControlPlaneOperation(
        'tablet', 'split', 'Split tablet', 'admin', 'topology_admin',
        impact_scope='resource', long_running=True
    ),
    ControlPlaneOperation(
        'snapshot', 'create_database', 'Create database snapshot', 'admin',
        'backup_admin', (
            cp_field('database', 'YSQL database', 'text', True,
                     max_length=256, pattern=_ID_PATTERN),
            cp_field('retention_hours', 'Retention hours', 'number', False,
                     minimum=0, maximum=87600),
        ), target_required=False, impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'snapshot', 'restore', 'Restore snapshot', 'destructive',
        'restore_admin', (
            cp_field('restore_timestamp', 'Restore timestamp', 'text', False,
                     max_length=128, pattern=r'[0-9TZ: .+-]+'),
        ), impact_scope='cluster', long_running=True, cancellable=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'snapshot', 'drop', 'Delete snapshot', 'destructive', 'backup_admin',
        impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'schedule', 'create', 'Create snapshot schedule', 'admin',
        'backup_admin', (
            cp_field('interval_minutes', 'Snapshot interval (minutes)',
                     'number', True, minimum=1, maximum=525600),
            cp_field('retention_minutes', 'Snapshot retention (minutes)',
                     'number', True, minimum=1, maximum=5256000),
            cp_field('namespace', 'YSQL or YCQL namespace', 'text', True,
                     max_length=256, pattern=_ID_PATTERN),
        ), target_required=False, impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'schedule', 'alter', 'Alter snapshot schedule', 'admin',
        'backup_admin', (
            cp_field('interval_minutes', 'New interval (minutes)', 'number',
                     False, minimum=1, maximum=525600),
            cp_field('retention_minutes', 'New retention (minutes)',
                     'number', False, minimum=1, maximum=5256000),
        ), impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'schedule', 'drop', 'Delete snapshot schedule', 'destructive',
        'backup_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'schedule', 'restore', 'Restore snapshot schedule', 'destructive',
        'restore_admin', (
            cp_field('restore_timestamp', 'Restore timestamp', 'text', True,
                     max_length=128, pattern=r'[0-9TZ: .+-]+'),
        ), impact_scope='cluster', long_running=True, cancellable=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'changefeed', 'create', 'Create CDCSDK stream', 'admin',
        'replication_admin', (
            cp_field('namespace', 'YSQL or YCQL namespace', 'text', True,
                     max_length=256, pattern=_ID_PATTERN),
            _choice('checkpoint_type', 'Checkpoint type', (
                ('EXPLICIT', 'Explicit'), ('IMPLICIT', 'Implicit'),
            ), default='EXPLICIT'),
            _choice('record_type', 'Record type', (
                ('CHANGE', 'Change'), ('ALL', 'All'),
            ), default='CHANGE'),
            _choice('snapshot_mode', 'Consistent snapshot mode', (
                ('EXPORT_SNAPSHOT', 'Export snapshot'),
                ('USE_SNAPSHOT', 'Use snapshot'),
                ('NOEXPORT_SNAPSHOT', 'No exported snapshot'),
            ), default='EXPORT_SNAPSHOT'),
            cp_field('dynamic_tables', 'Include new tables', 'boolean', False,
                     default=True),
        ), target_required=False, impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'changefeed', 'drop', 'Delete CDCSDK stream', 'destructive',
        'replication_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'xcluster-replication', 'create', 'Create xCluster replication',
        'admin', 'replication_admin', (
            cp_field('replication_group_id', 'Replication group ID', 'text',
                     True, max_length=256, pattern=_ID_PATTERN),
            cp_field('producer_master_addresses',
                     'Producer master addresses', 'json', True,
                     json_type='array'),
            cp_field('table_ids', 'Producer table IDs', 'json', True,
                     json_type='array'),
            cp_field('bootstrap_ids', 'Producer bootstrap IDs', 'json',
                     False, default=[], json_type='array'),
            cp_field('transactional', 'Transactional', 'boolean', False,
                     default=False),
        ), target_required=False, impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'xcluster-replication', 'set_enabled',
        'Set xCluster replication state', 'admin', 'replication_admin', (
            cp_field('enabled', 'Enabled', 'boolean', True, default=True),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'xcluster-replication', 'add_tables',
        'Add xCluster replicated tables', 'admin', 'replication_admin', (
            cp_field('table_ids', 'Producer table IDs', 'json', True,
                     json_type='array'),
            cp_field('bootstrap_ids', 'Producer bootstrap IDs', 'json',
                     False, default=[], json_type='array'),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'xcluster-replication', 'remove_tables',
        'Remove xCluster replicated tables', 'destructive',
        'replication_admin', (
            cp_field('table_ids', 'Producer table IDs', 'json', True,
                     json_type='array'),
            cp_field('ignore_errors', 'Ignore missing tables', 'boolean',
                     False, default=False),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'xcluster-replication', 'drop', 'Delete xCluster replication',
        'destructive', 'replication_admin', (
            cp_field('ignore_errors', 'Ignore cleanup errors', 'boolean',
                     False, default=False),
        ), impact_scope='cluster', long_running=True
    ),
)


def _safe(value, label, pattern=_ID_PATTERN):
    if not isinstance(value, str) or not value or len(value) > 256 or (
            re.fullmatch(pattern, value) is None):
        raise RelationalClientError(f'YugabyteDB {label} is invalid')
    return value


def _list(value, label, pattern=_ID_PATTERN, maximum=1024):
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise RelationalClientError(f'YugabyteDB {label} list is invalid')
    result = [_safe(item, label, pattern) for item in value]
    if len(result) != len(set(result)):
        raise RelationalClientError(
            f'YugabyteDB {label} values must be unique')
    return result


def _target(request, pattern=_ID_PATTERN):
    target = request.get('target_resource')
    if not isinstance(target, dict):
        raise RelationalClientError('YugabyteDB control target is required')
    path = target.get('display_path') or [target.get('display_name')]
    if not isinstance(path, list) or not path:
        raise RelationalClientError('YugabyteDB target path is invalid')
    return _safe(path[-1], 'target', pattern)


def _quoted_identifier(value, label):
    return '"' + _safe(value, label, _ID_PATTERN) + '"'


def _qualified_table(request):
    target = request.get('target_resource')
    path = target.get('display_path') if isinstance(target, dict) else None
    if not isinstance(path, list) or len(path) < 2:
        raise RelationalClientError(
            'YugabyteDB YSQL table path is invalid')
    return '.'.join((
        _quoted_identifier(path[-2], 'schema'),
        _quoted_identifier(path[-1], 'table'),
    ))


def _placements(value):
    placements = _list(value, 'placement', r'[A-Za-z0-9_.:-]+', 256)
    for placement in placements:
        parts = placement.split(':', 1)[0].split('.')
        if len(parts) != 3 or any(not part for part in parts):
            raise RelationalClientError(
                'YugabyteDB placement must be cloud.region.zone[:count]')
        if ':' in placement:
            count = placement.rsplit(':', 1)[1]
            if not count.isdigit() or not 1 <= int(count) <= 64:
                raise RelationalClientError(
                    'YugabyteDB placement replica count is invalid')
    return ','.join(placements)


def _positive_integer(value, label, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not (
            1 <= value <= maximum):
        raise RelationalClientError(f'YugabyteDB {label} is invalid')
    return str(value)


def compile_action(request):
    kind = request['resource_kind']
    operation = request['operation_id']
    draft = request.get('draft') or {}
    arguments = []
    if kind == 'table':
        # YugabyteDB rejects modify_table_placement_info for YSQL tables and
        # explicitly requires placement through YSQL tablespaces.  Compile
        # the provider-owned form to the supported YSQL operation instead of
        # presenting the YCQL-only yb-admin command as usable for YSQL.
        tablespace = _quoted_identifier(
            draft.get('tablespace'), 'tablespace')
        return {
            'statements': [{
                'source': (
                    f'ALTER TABLE {_qualified_table(request)} '
                    f'SET TABLESPACE {tablespace}'
                ),
                'parameters': (),
            }],
            'impact': {
                'scope': 'resource',
                'target_resource_id': (
                    request.get('target_resource') or {}
                ).get('resource_id'),
                'availability_risk': 'medium',
                'data_movement_possible': True,
            },
        }
    if kind == 'placement-policy':
        if operation == 'clear':
            arguments = ['clear_placement_info']
        else:
            arguments = ['modify_placement_info']
            arguments.extend([
                _placements(draft.get('placements')),
                _positive_integer(
                    draft.get('replication_factor'),
                    'replication factor', 64,
                ),
            ])
            if draft.get('placement_uuid'):
                arguments.append(_safe(
                    draft['placement_uuid'], 'placement UUID'))
    elif kind == 'node':
        command = (
            'change_leader_blacklist'
            if 'leader' in operation else 'change_blacklist'
        )
        change = 'ADD' if operation.startswith('add_') else 'REMOVE'
        arguments = [command, change, _target(
            request, _HOST_PORT_PATTERN)]
    elif kind == 'cluster' and operation == 'set_load_balancer':
        enabled = draft.get('enabled')
        if not isinstance(enabled, bool):
            raise RelationalClientError(
                'YugabyteDB load balancer state is invalid')
        arguments = ['set_load_balancer_enabled', '1' if enabled else '0']
    elif kind == 'master' and operation == 'leader_stepdown':
        arguments = ['master_leader_stepdown']
        if draft.get('destination_uuid'):
            arguments.append(_safe(
                draft['destination_uuid'], 'master UUID', _UUID_PATTERN))
    elif kind == 'tablet':
        tablet_id = _target(request, _UUID_PATTERN)
        arguments = [
            'split_tablet' if operation == 'split' else 'leader_stepdown',
            tablet_id,
        ]
        if operation == 'leader_stepdown' and draft.get(
                'destination_uuid'):
            arguments.append(_safe(
                draft['destination_uuid'], 'TServer UUID', _UUID_PATTERN))
    elif kind == 'snapshot':
        if operation == 'create_database':
            arguments = [
                'create_database_snapshot',
                _safe(draft.get('database'), 'database'),
            ]
            if draft.get('retention_hours') is not None:
                value = draft['retention_hours']
                if isinstance(value, bool) or not isinstance(value, int) or (
                        not 0 <= value <= 87600):
                    raise RelationalClientError(
                        'YugabyteDB snapshot retention is invalid')
                arguments.append(str(value))
        elif operation == 'restore':
            arguments = ['restore_snapshot', _target(
                request, _UUID_PATTERN)]
            if draft.get('restore_timestamp'):
                arguments.append(_safe(
                    draft['restore_timestamp'], 'restore timestamp',
                    r'[0-9TZ: .+-]+'))
        else:
            arguments = ['delete_snapshot', _target(
                request, _UUID_PATTERN)]
    elif kind == 'schedule':
        if operation == 'create':
            arguments = [
                'create_snapshot_schedule',
                _positive_integer(
                    draft.get('interval_minutes'), 'snapshot interval',
                    525600,
                ),
                _positive_integer(
                    draft.get('retention_minutes'), 'snapshot retention',
                    5256000,
                ),
                _safe(draft.get('namespace'), 'namespace'),
            ]
        elif operation == 'alter':
            arguments = ['edit_snapshot_schedule', _target(
                request, _UUID_PATTERN)]
            for name, token, maximum in (
                    ('interval_minutes', 'interval', 525600),
                    ('retention_minutes', 'retention', 5256000)):
                if draft.get(name) is not None:
                    arguments.extend([
                        token, _positive_integer(
                            draft[name], token, maximum),
                    ])
            if len(arguments) == 2:
                raise RelationalClientError(
                    'YugabyteDB schedule change is empty')
        elif operation == 'restore':
            arguments = [
                'restore_snapshot_schedule', _target(
                    request, _UUID_PATTERN),
                _safe(draft.get('restore_timestamp'), 'restore timestamp',
                      r'[0-9TZ: .+-]+'),
            ]
        else:
            arguments = ['delete_snapshot_schedule', _target(
                request, _UUID_PATTERN)]
    elif kind == 'changefeed':
        if operation == 'create':
            checkpoint = draft.get('checkpoint_type', 'EXPLICIT')
            record = draft.get('record_type', 'CHANGE')
            snapshot = draft.get('snapshot_mode', 'EXPORT_SNAPSHOT')
            dynamic = draft.get('dynamic_tables', True)
            if checkpoint not in {'EXPLICIT', 'IMPLICIT'} or record not in {
                    'CHANGE', 'ALL'} or snapshot not in {
                    'EXPORT_SNAPSHOT', 'USE_SNAPSHOT',
                    'NOEXPORT_SNAPSHOT'} or not isinstance(dynamic, bool):
                raise RelationalClientError(
                    'YugabyteDB changefeed options are invalid')
            arguments = [
                'create_change_data_stream',
                _safe(draft.get('namespace'), 'namespace'), checkpoint,
                record, snapshot,
                'DYNAMIC_TABLES_ENABLED' if dynamic
                else 'DYNAMIC_TABLES_DISABLED',
            ]
        else:
            arguments = ['delete_change_data_stream', _target(
                request, _UUID_PATTERN)]
    elif kind == 'xcluster-replication':
        group_id = (
            _safe(draft.get('replication_group_id'), 'replication group')
            if operation == 'create' else _target(request)
        )
        if operation == 'create':
            addresses = _list(
                draft.get('producer_master_addresses'), 'master address',
                _HOST_PORT_PATTERN,
            )
            table_ids = _list(
                draft.get('table_ids'), 'table ID', _UUID_PATTERN)
            arguments = [
                'setup_universe_replication', group_id,
                ','.join(addresses), ','.join(table_ids),
            ]
            bootstrap = draft.get('bootstrap_ids') or []
            if bootstrap:
                arguments.append(','.join(_list(
                    bootstrap, 'bootstrap ID', _UUID_PATTERN)))
            if draft.get('transactional'):
                if not bootstrap:
                    arguments.append('transactional')
                else:
                    arguments.append('transactional')
        elif operation == 'set_enabled':
            enabled = draft.get('enabled')
            if not isinstance(enabled, bool):
                raise RelationalClientError(
                    'YugabyteDB replication state is invalid')
            arguments = [
                'set_universe_replication_enabled', group_id,
                '1' if enabled else '0',
            ]
        elif operation in {'add_tables', 'remove_tables'}:
            table_ids = _list(
                draft.get('table_ids'), 'table ID', _UUID_PATTERN)
            action = 'add_table' if operation == 'add_tables' else (
                'remove_table')
            arguments = [
                'alter_universe_replication', group_id, action,
                ','.join(table_ids),
            ]
            bootstrap = draft.get('bootstrap_ids') or []
            if operation == 'add_tables' and bootstrap:
                arguments.append(','.join(_list(
                    bootstrap, 'bootstrap ID', _UUID_PATTERN)))
            if operation == 'remove_tables' and draft.get('ignore_errors'):
                arguments.append('ignore-errors')
        else:
            arguments = ['delete_universe_replication', group_id]
            if draft.get('ignore_errors'):
                arguments.append('ignore-errors')
    if not arguments:
        raise RelationalClientError(
            'YugabyteDB control-plane operation is unavailable')
    return {
        'provider_action': {'arguments': arguments},
        'action_preview': {
            'tool': 'yb-admin', 'verb': arguments[0],
            'argument_count': len(arguments) - 1,
            'provider_constructed': True,
        },
        'impact': {
            'scope': 'resource' if kind == 'tablet' else (
                'node' if kind == 'node' else 'cluster'),
            'target_resource_id': (
                request.get('target_resource') or {}
            ).get('resource_id'),
            'availability_risk': 'high' if operation in {
                'clear', 'restore', 'leader_stepdown', 'drop'
            } else 'medium',
            'data_movement_possible': kind in {
                'placement-policy', 'table', 'node', 'tablet', 'snapshot',
                'schedule', 'xcluster-replication',
            },
        },
    }


def _route(request):
    payload = request.get('provider_payload')
    route = payload.get('route') if isinstance(payload, dict) else None
    if not isinstance(route, dict):
        raise RelationalClientError(
            'YugabyteDB control route is unavailable')
    return route


def _trusted_file(route, key, label, executable=False):
    value = route.get(key)
    if not isinstance(value, str) or not value:
        raise RelationalClientError(f'YugabyteDB {label} is required')
    path = Path(value).expanduser().resolve()
    if not path.is_file() or (executable and not os.access(path, os.X_OK)):
        raise RelationalClientError(f'YugabyteDB {label} is unavailable')
    return str(path)


def _master_addresses(route):
    value = route.get('master_addresses')
    if isinstance(value, str):
        values = value.split(',')
    elif isinstance(value, list):
        values = value
    else:
        raise RelationalClientError(
            'YugabyteDB master addresses are required')
    return ','.join(_list(
        values, 'master address', _HOST_PORT_PATTERN, 64))


def _run(route, arguments, timeout=120):
    if not isinstance(arguments, list) or not arguments or len(
            arguments) > 2048 or any(
                not isinstance(value, str) or not value or len(value) > 8192
                or '\x00' in value for value in arguments):
        raise RelationalClientError('YugabyteDB tool arguments are invalid')
    timeout_ms = route.get('yb_admin_timeout_ms', 120000)
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or (
            not 1000 <= timeout_ms <= 3600000):
        raise RelationalClientError('YugabyteDB tool timeout is invalid')
    command = [
        _trusted_file(
            route, 'yb_admin_path', 'yb-admin executable', True),
        '--master_addresses', _master_addresses(route),
        '--timeout_ms', str(timeout_ms),
    ]
    if route.get('certs_dir_name'):
        certs = Path(str(route['certs_dir_name'])).expanduser().resolve()
        if not certs.is_dir():
            raise RelationalClientError(
                'YugabyteDB certificate directory is unavailable')
        command.extend(['--certs_dir_name', str(certs)])
    command.extend(arguments)
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RelationalClientError(
            'YugabyteDB control request failed to return') from exc
    output = (result.stdout or '') + (result.stderr or '')
    if len(output.encode('utf-8')) > 8 * 1024 * 1024:
        raise RelationalClientError(
            'YugabyteDB control response exceeds size limit')
    if result.returncode != 0:
        raise RelationalClientError(
            'YugabyteDB control request was rejected')
    return {
        'exit_code': 0, 'stdout': result.stdout or '',
        'stderr': result.stderr or '',
        'provider_response_observed': True,
        'provider_finality_authority': True,
        'automatic_mutation_retry': False,
    }


def _json_document(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _result_stdout(request):
    result = request.get('provider_result') or {}
    response = result.get('provider_response') if isinstance(
        result, dict
    ) else None
    return response.get('stdout', '') if isinstance(response, dict) else ''


def _created_identifier(request, keys, pattern):
    text = _result_stdout(request)
    document = _json_document(text)
    if isinstance(document, dict):
        for key in keys:
            value = document.get(key)
            if isinstance(value, str) and re.fullmatch(pattern, value):
                return value
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1) if match else None


def _schedule_rows(document):
    rows = document.get('schedules') if isinstance(document, dict) else None
    return rows if isinstance(rows, list) else []


def _schedule_ids(document):
    return {
        row.get('id') for row in _schedule_rows(document)
        if isinstance(row, dict) and isinstance(row.get('id'), str)
    }


def _changefeed_ids(text):
    return set(re.findall(
        r'(?m)^\s*stream_id:\s*"([A-Fa-f0-9-]+)"\s*$', text or ''
    ))


def _xcluster_ids(text):
    match = re.search(r'\[([^\]]*)\]', text or '')
    if match is None:
        return set()
    return {
        value.strip() for value in match.group(1).split(',')
        if value.strip()
    }


def _resource(kind, name, generation, native, path=None):
    path = list(path or ())
    return {
        'resource_id': ':'.join([kind, *path, name]),
        'resource_kind': kind, 'display_name': name,
        'display_path': [*path, name],
        'authority_path': [*path, kind, name], 'generation': generation,
        'native': copy.deepcopy(native),
    }


def catalog_resources(route, generation):
    """Enumerate provider-owned YB control-plane objects for navigation."""
    if not isinstance(route, dict) or not route.get(
            'yb_admin_path') or not route.get('master_addresses'):
        return []
    resources = []
    probes = (
        ('schedule', ['list_snapshot_schedules']),
        ('changefeed', ['list_change_data_streams']),
        ('xcluster-replication', ['list_universe_replications']),
        ('snapshot', ['list_snapshots', 'JSON', 'SHOW_DETAILS']),
        ('placement-policy', ['get_universe_config']),
        ('table', [
            'list_tables', 'include_db_type', 'include_table_id',
            'include_table_type',
        ]),
    )
    for kind, arguments in probes:
        try:
            response = _run(route, arguments, timeout=30)
        except RelationalClientError:
            continue
        text = response.get('stdout', '')
        document = _json_document(text)
        if kind == 'schedule':
            for row in _schedule_rows(document):
                if isinstance(row, dict) and isinstance(row.get('id'), str):
                    resources.append(_resource(
                        kind, row['id'], generation, row))
        elif kind == 'changefeed':
            for identifier in sorted(_changefeed_ids(text)):
                resources.append(_resource(
                    kind, identifier, generation, {'raw': text}))
        elif kind == 'xcluster-replication':
            for identifier in sorted(_xcluster_ids(text)):
                resources.append(_resource(
                    kind, identifier, generation, {'raw': text}))
        elif kind == 'snapshot':
            rows = document.get('snapshots') if isinstance(
                document, dict
            ) else document
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                identifier = row.get('id') or row.get('snapshot_id')
                if isinstance(identifier, str):
                    resources.append(_resource(
                        kind, identifier, generation, row))
        elif kind == 'table':
            database = str(route.get('database') or '')
            pattern = re.compile(
                r'^ysql\.([^\.\s]+)\.([^\s]+) '
                r'\[ysql_schema=([^\]]+)\] '
                r'\[([A-Fa-f0-9-]+)\] [A-Fa-f0-9-]+ table$',
                re.MULTILINE,
            )
            for db_name, table, schema, table_id in pattern.findall(text):
                if database and db_name != database:
                    continue
                resources.append(_resource(
                    kind, table, generation, {
                        'table_id': table_id, 'database': db_name,
                        'schema': schema,
                    }, path=[schema]))
        elif isinstance(document, dict):
            resources.append(_resource(
                kind, 'live-placement', generation,
                document.get('replicationInfo') or document,
            ))
    return resources


def execute_action(_client, request):
    payload = request.get('provider_payload') or {}
    action = payload.get('compiled', {}).get('provider_action', {})
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
        raise RelationalClientError(
            'YugabyteDB operation handle is invalid')
    return plan, payload


def inspect_action(_client, request):
    plan, _payload = _parts(request)
    kind = plan['resource_kind']
    operation = plan['operation_id']
    if kind in {'placement-policy', 'node'}:
        arguments = ['get_universe_config']
    elif kind == 'cluster':
        arguments = ['get_load_balancer_state']
    elif kind == 'master':
        arguments = ['list_all_masters']
    elif kind == 'tablet':
        arguments = ['list_tablet_servers', _target({
            'target_resource': plan.get('target_resource')
        }, _UUID_PATTERN)]
    elif kind == 'snapshot':
        arguments = ['list_snapshots', 'JSON', 'SHOW_DETAILS']
    elif kind == 'schedule':
        arguments = ['list_snapshot_schedules']
        if operation not in {'create', 'drop'}:
            arguments.append(_target({
                'target_resource': plan.get('target_resource')
            }, _UUID_PATTERN))
    elif kind == 'changefeed':
        arguments = ['list_change_data_streams']
    elif kind == 'xcluster-replication':
        if operation in {'create', 'drop'}:
            arguments = ['list_universe_replications']
        else:
            arguments = [
                'get_universe_replication_info', _target({
                    'target_resource': plan.get('target_resource')
                }),
            ]
    else:
        arguments = ['get_universe_config']
    return {
        'provider_observation': _run(
            _route(request), arguments, timeout=30),
        'provider_observation_only': True,
        'provider_finality_authority': True,
    }


def _restoration_id(value):
    text = value.get('stdout', '') if isinstance(value, dict) else ''
    try:
        document = json.loads(text)
    except (TypeError, ValueError):
        document = None
    if isinstance(document, dict):
        for key in ('restoration_id', 'restorationId'):
            candidate = document.get(key)
            if isinstance(candidate, str) and re.fullmatch(
                    _UUID_PATTERN, candidate):
                return candidate
    match = re.search(
        r'(?i)restoration(?:_|\s+)id[^A-Fa-f0-9-]*([A-Fa-f0-9-]{16,})',
        text,
    )
    return match.group(1) if match else None


def cancel_action(_client, request):
    plan, _payload = _parts(request)
    if plan.get('operation_id') != 'restore' or plan.get(
            'resource_kind') not in {'snapshot', 'schedule'}:
        raise RelationalClientError(
            'YugabyteDB operation is not declared cancellable')
    result = request.get('provider_result') or {}
    response = result.get('provider_response') if isinstance(
        result, dict
    ) else None
    restoration_id = _restoration_id(response)
    if restoration_id is None:
        raise RelationalClientError(
            'YugabyteDB restoration ID is not yet observable')
    return _run(
        _route(request), ['abort_snapshot_restore', restoration_id])


def post_validate_action(client, request):
    observation = inspect_action(client, request)
    text = observation['provider_observation'].get('stdout', '')
    document = _json_document(text)
    plan = request.get('plan') or {}
    kind = plan.get('resource_kind')
    operation = plan.get('operation_id')
    draft = plan.get('draft') or {}
    target = plan.get('target_resource') or {}
    path = target.get('display_path') or [target.get('display_name')]
    expected = path[-1] if path and isinstance(path[-1], str) else None
    present = None
    if kind == 'schedule':
        if operation == 'create':
            expected = _created_identifier(
                request, ('schedule_id', 'scheduleId'),
                r'"schedule_id"\s*:\s*"([A-Fa-f0-9-]+)"',
            )
        present = expected in _schedule_ids(document)
        if present and operation == 'drop':
            row = next(item for item in _schedule_rows(document)
                       if item.get('id') == expected)
            present = not bool((row.get('options') or {}).get('delete_time'))
        if present and operation == 'alter':
            row = next(item for item in _schedule_rows(document)
                       if item.get('id') == expected)
            options = row.get('options') or {}
            checks = []
            for key, field in (
                    ('interval_minutes', 'interval'),
                    ('retention_minutes', 'retention')):
                if draft.get(key) is not None:
                    checks.append(str(options.get(field, '')).startswith(
                        str(draft[key]) + ' '))
            present = bool(checks) and all(checks)
    elif kind == 'changefeed':
        if operation == 'create':
            expected = _created_identifier(
                request, (),
                r'CDC\s+Stream\s+ID:\s*([A-Fa-f0-9-]+)',
            )
        present = expected in _changefeed_ids(text)
    elif kind == 'xcluster-replication':
        if operation == 'create':
            expected = draft.get('replication_group_id')
            present = expected in _xcluster_ids(text)
        else:
            present = bool(expected and (
                f'Replication Group Id: {expected}' in text or
                expected in _xcluster_ids(text)
            ))
            if present and operation in {'add_tables', 'remove_tables'}:
                observed_ids = set(re.findall(
                    r'(?m)^\s*[A-Fa-f0-9-]+\s+([A-Fa-f0-9-]+)\s+'
                    r'[A-Fa-f0-9-]+\s*$', text,
                ))
                requested = set(draft.get('table_ids') or [])
                present = (
                    requested.issubset(observed_ids)
                    if operation == 'add_tables' else
                    requested.isdisjoint(observed_ids)
                )
    confirmed = bool(expected and present is not None and (
        (operation == 'drop' and not present) or
        (operation != 'drop' and present)
    ))
    return {
        'confirmed': confirmed,
        'reason': (
            'yugabytedb_resource_state_matches_requested_state'
            if confirmed else
            'yugabytedb_provider_state_requires_semantic_review'
        ),
        'provider_output': text,
        'observation': observation,
        'provider_finality_authority': True,
    }
