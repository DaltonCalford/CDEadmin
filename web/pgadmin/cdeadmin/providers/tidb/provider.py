"""TiDB 8.5.6 distributed relational provider."""

import json
import os
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from pgadmin.cdeadmin.sdk import (
    ActualEnginePilotProvider,
    PilotProfile,
    RelationalClientError,
)
from ..distributed_sql import (
    create_sql_client,
    mysql_catalog,
    optional_rows,
    resource,
    sql_administration,
)
from ..distributed_control_plane import DistributedSQLControlPlane
from pgadmin.cdeadmin.visual_admin import (
    ControlPlaneOperation,
    control_plane_field as cp_field,
)


PROFILE = PilotProfile(
    'org.cdeadmin.tidb', 'tidb-native', 'tidb', 'TiDB', '8.5.6',
    'mysql_wire', 'distributed-relational', 'tidb-sql', 'TiDB SQL',
    'tidb-distributed-transaction-native', 'tabular',
    (
        'cluster', 'node', 'database', 'table', 'column', 'index',
        'constraint', 'view', 'sequence', 'partition', 'placement-policy',
        'resource-group', 'tiflash-replica', 'job', 'changefeed', 'backup',
        'user', 'role', 'privilege',
    ),
    ('mysql-client', 'tiup', 'br', 'ticdc'),
    semantic_sql_dialect={
        'language_profile': 'tidb-sql', 'quote_open': '`',
        'quote_close': '`', 'supports_rollup': True,
    },
)


_BASE_ADMINISTRATION = sql_administration('tidb', 'mysql', (
    'node', 'partition', 'placement-policy', 'resource-group',
    'tiflash-replica', 'job', 'changefeed', 'backup',
))


def _choice(field_id, label, values, **options):
    return cp_field(
        field_id, label, 'select', options=[
            {'value': value, 'label': title} for value, title in values
        ], **options
    )


_PLACEMENT_FIELDS = (
    cp_field('primary_region', 'Primary region', 'text', False,
             max_length=256),
    cp_field('regions', 'Regions (comma separated)', 'text', False,
             max_length=2048),
    cp_field('followers', 'Follower replicas', 'number', False,
             minimum=0, maximum=15),
    cp_field('voters', 'Voting replicas', 'number', False,
             minimum=0, maximum=15),
    cp_field('learners', 'Learner replicas', 'number', False,
             minimum=0, maximum=15),
    cp_field('constraints', 'Replica constraints', 'text', False,
             max_length=4096),
    cp_field('leader_constraints', 'Leader constraints', 'text', False,
             max_length=4096),
    cp_field('follower_constraints', 'Follower constraints', 'text', False,
             max_length=4096),
    cp_field('voter_constraints', 'Voter constraints', 'text', False,
             max_length=4096),
    cp_field('learner_constraints', 'Learner constraints', 'text', False,
             max_length=4096),
    _choice('schedule', 'Placement schedule', (
        ('even', 'Even'), ('majority_in_primary', 'Majority in primary'),
    ), required=False),
)

_RESOURCE_GROUP_FIELDS = (
    _choice('ru_mode', 'Request-unit limit', (
        ('limited', 'Fixed limit'), ('unlimited', 'Unlimited'),
    ), required=True, default='limited'),
    cp_field('ru_per_sec', 'Request units per second', 'number', False,
             minimum=1, visible_when={
                 'field_id': 'ru_mode', 'equals': 'limited'
             }),
    _choice('priority', 'Priority', (
        ('LOW', 'Low'), ('MEDIUM', 'Medium'), ('HIGH', 'High'),
    ), required=True, default='MEDIUM'),
    cp_field('burstable', 'Burstable', 'boolean', False, default=False),
)

_BR_STORAGE_FIELD = (
    cp_field('storage_uri', 'Approved BR storage URI', 'text', True,
             max_length=8192, sensitive=True),
)

CONTROL_OPERATIONS = (
    ControlPlaneOperation(
        'cluster', 'backup_full', 'Back up cluster with BR', 'admin',
        'backup_admin', _BR_STORAGE_FIELD, impact_scope='cluster',
        long_running=True
    ),
    ControlPlaneOperation(
        'database', 'backup_br', 'Back up database with BR', 'admin',
        'backup_admin', _BR_STORAGE_FIELD, impact_scope='cluster',
        long_running=True
    ),
    ControlPlaneOperation(
        'table', 'backup_br', 'Back up table with BR', 'admin',
        'backup_admin', _BR_STORAGE_FIELD, impact_scope='cluster',
        long_running=True
    ),
    ControlPlaneOperation(
        'cluster', 'restore_full', 'Restore cluster with BR', 'destructive',
        'restore_admin', _BR_STORAGE_FIELD, impact_scope='cluster',
        long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'database', 'restore_br', 'Restore database with BR', 'destructive',
        'restore_admin', _BR_STORAGE_FIELD, impact_scope='cluster',
        long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'table', 'restore_br', 'Restore table with BR', 'destructive',
        'restore_admin', _BR_STORAGE_FIELD, impact_scope='cluster',
        long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'cluster', 'restore_point', 'Point-in-time restore with BR',
        'destructive', 'restore_admin', _BR_STORAGE_FIELD + (
            cp_field('restore_timestamp', 'Restore timestamp or TSO',
                     'text', True, max_length=128,
                     pattern=r'[0-9T: .+\-Z]+'),
            cp_field('full_backup_storage_uri',
                     'Approved full-backup storage URI', 'text', False,
                     max_length=8192, sensitive=True),
        ), impact_scope='cluster', long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'placement-policy', 'create', 'Create placement policy', 'admin',
        'topology_admin', (
            cp_field('name', 'Policy name', 'text', True, max_length=256,
                     pattern=r'[A-Za-z_][A-Za-z0-9_$-]*'),
        ) + _PLACEMENT_FIELDS, target_required=False,
        impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'placement-policy', 'alter', 'Alter placement policy', 'admin',
        'topology_admin', _PLACEMENT_FIELDS, impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'placement-policy', 'drop', 'Drop placement policy', 'destructive',
        'topology_admin', impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'database', 'configure_placement', 'Set database placement policy',
        'admin', 'topology_admin', (
            cp_field('policy_name', 'Placement policy', 'text', True,
                     max_length=256,
                     pattern=r'[A-Za-z_][A-Za-z0-9_$-]*'),
        ), impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'table', 'configure_placement', 'Set table placement policy',
        'admin', 'topology_admin', (
            cp_field('policy_name', 'Placement policy', 'text', True,
                     max_length=256,
                     pattern=r'[A-Za-z_][A-Za-z0-9_$-]*'),
        ), impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'partition', 'configure_placement', 'Set partition placement policy',
        'admin', 'topology_admin', (
            cp_field('policy_name', 'Placement policy', 'text', True,
                     max_length=256,
                     pattern=r'[A-Za-z_][A-Za-z0-9_$-]*'),
        ), impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'resource-group', 'create', 'Create resource group', 'admin',
        'topology_admin', (
            cp_field('name', 'Resource group name', 'text', True,
                     max_length=256,
                     pattern=r'[A-Za-z_][A-Za-z0-9_$-]*'),
        ) + _RESOURCE_GROUP_FIELDS, target_required=False,
        impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'resource-group', 'alter', 'Alter resource group', 'admin',
        'topology_admin', _RESOURCE_GROUP_FIELDS, impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'resource-group', 'drop', 'Drop resource group', 'destructive',
        'topology_admin', impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'table', 'set_tiflash_replica', 'Configure TiFlash replicas',
        'admin', 'replication_admin', (
            cp_field('replica_count', 'Replica count', 'number', True,
                     minimum=0, maximum=15),
            cp_field('location_labels', 'Location labels', 'json', False,
                     default=[], json_type='array'),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'database', 'set_tiflash_replica', 'Configure TiFlash replicas',
        'admin', 'replication_admin', (
            cp_field('replica_count', 'Replica count', 'number', True,
                     minimum=0, maximum=15),
            cp_field('location_labels', 'Location labels', 'json', False,
                     default=[], json_type='array'),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'job', 'cancel', 'Cancel DDL job', 'destructive',
        'maintenance_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'changefeed', 'create', 'Create TiCDC changefeed', 'admin',
        'replication_admin', (
            cp_field('changefeed_id', 'Changefeed ID', 'text', True,
                     max_length=128,
                     pattern=r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*'),
            cp_field('sink_uri', 'Approved sink URI', 'text', True,
                     max_length=8192, sensitive=True),
            cp_field('start_ts', 'Start TSO', 'text', False,
                     max_length=32, pattern=r'[0-9]+'),
            cp_field('target_ts', 'Target TSO', 'text', False,
                     max_length=32, pattern=r'[0-9]+'),
        ), target_required=False, impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'changefeed', 'pause', 'Pause TiCDC changefeed', 'admin',
        'replication_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'changefeed', 'resume', 'Resume TiCDC changefeed', 'admin',
        'replication_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'changefeed', 'remove', 'Remove TiCDC changefeed', 'destructive',
        'replication_admin', impact_scope='cluster', long_running=True
    ),
)


def _quote(value):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise RelationalClientError('TiDB identifier is invalid')
    return '`' + value.replace('`', '``') + '`'


def _path(request):
    target = request.get('target_resource')
    if not isinstance(target, dict):
        raise RelationalClientError('TiDB target is required')
    values = target.get('display_path') or [target.get('display_name')]
    if not isinstance(values, list) or not values or any(
            value is None for value in values):
        raise RelationalClientError('TiDB target path is invalid')
    return [str(value) for value in values]


def _target_id(request):
    try:
        return int(_path(request)[-1])
    except (TypeError, ValueError):
        raise RelationalClientError(
            'TiDB operation requires a numeric native identifier'
        ) from None


def _placement_assignments(draft):
    names = (
        'primary_region', 'regions', 'followers', 'voters', 'learners',
        'constraints', 'leader_constraints', 'follower_constraints',
        'voter_constraints', 'learner_constraints', 'schedule',
    )
    parts = []
    parameters = []
    for name in names:
        if draft.get(name) in (None, ''):
            continue
        parts.append(f'{name.upper()} = %s')
        parameters.append(draft[name])
    if not parts:
        raise RelationalClientError(
            'TiDB placement policy has no configured properties'
        )
    return ' '.join(parts), tuple(parameters)


def _resource_group_assignments(draft):
    ru_mode = draft.get('ru_mode', 'limited')
    if ru_mode not in {'limited', 'unlimited'}:
        raise RelationalClientError('TiDB request-unit mode is invalid')
    priority = draft.get('priority', 'MEDIUM')
    if priority not in {'LOW', 'MEDIUM', 'HIGH'}:
        raise RelationalClientError('TiDB resource priority is invalid')
    parts = ['RU_PER_SEC = UNLIMITED']
    parameters = []
    if ru_mode == 'limited':
        ru = draft.get('ru_per_sec')
        if ru is None:
            raise RelationalClientError(
                'TiDB request-unit limit is required'
            )
        parts[0] = 'RU_PER_SEC = %s'
        parameters.append(ru)
    parts.append('PRIORITY = ' + priority)
    parts.append('BURSTABLE = ' + (
        'TRUE' if draft.get('burstable') else 'FALSE'
    ))
    return ', '.join(parts), tuple(parameters)


def _impact(request, **extra):
    return {
        'scope': 'cluster',
        'target_resource_id': (request.get('target_resource') or {}).get(
            'resource_id'
        ),
        'availability_risk': extra.pop('availability_risk', 'medium'),
        'data_movement_possible': extra.pop(
            'data_movement_possible', True
        ),
        **extra,
    }


def _allowlisted_br_uri(request, field_id='storage_uri'):
    value = (request.get('draft') or {}).get(field_id)
    if not isinstance(value, str) or not value.strip():
        raise RelationalClientError('TiDB BR storage URI is required')
    value = value.strip()
    if urlsplit(value).scheme.lower() not in {
            'azure', 'gcs', 'gs', 'hdfs', 'local', 's3'}:
        raise RelationalClientError('TiDB BR storage scheme is not admitted')
    prefixes = (request.get('_provider_route') or {}).get(
        'br_storage_allowlist')
    if isinstance(prefixes, str):
        prefixes = [item.strip() for item in prefixes.split(',')]
    if not isinstance(prefixes, list) or not prefixes or not any(
            isinstance(prefix, str) and prefix and value.startswith(prefix)
            for prefix in prefixes):
        raise RelationalClientError(
            'TiDB BR storage URI is outside the endpoint allowlist')
    return value


def _changefeed_id(value):
    if not isinstance(value, str) or re.fullmatch(
            r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*', value) is None or len(
                value) > 128:
        raise RelationalClientError('TiDB changefeed ID is invalid')
    return value


def _allowlisted_cdc_sink(request):
    value = (request.get('draft') or {}).get('sink_uri')
    if not isinstance(value, str) or not value.strip():
        raise RelationalClientError('TiCDC sink URI is required')
    value = value.strip()
    scheme = urlsplit(value).scheme.lower()
    if scheme not in {
            'blackhole', 'file', 'kafka', 'mysql', 'pulsar', 's3',
            'storage'}:
        raise RelationalClientError('TiCDC sink scheme is not admitted')
    prefixes = (request.get('_provider_route') or {}).get(
        'ticdc_sink_allowlist')
    if isinstance(prefixes, str):
        prefixes = [item.strip() for item in prefixes.split(',')]
    if not isinstance(prefixes, list) or not prefixes or not any(
            isinstance(prefix, str) and prefix and value.startswith(prefix)
            for prefix in prefixes):
        raise RelationalClientError(
            'TiCDC sink URI is outside the endpoint allowlist')
    return value


def _compile_cdc_action(request):
    operation = request['operation_id']
    draft = request.get('draft') or {}
    if operation == 'create':
        changefeed = _changefeed_id(draft.get('changefeed_id'))
        arguments = [
            'cli', 'changefeed', 'create',
            '--changefeed-id', changefeed,
            '--sink-uri', _allowlisted_cdc_sink(request),
        ]
        for field_id, flag in (
                ('start_ts', '--start-ts'), ('target_ts', '--target-ts')):
            value = draft.get(field_id)
            if value:
                if not isinstance(value, str) or re.fullmatch(
                        r'[0-9]{1,32}', value) is None:
                    raise RelationalClientError(
                        f'TiCDC {field_id.replace("_", " ")} is invalid')
                arguments.extend([flag, value])
    else:
        changefeed = _changefeed_id(_path(request)[-1])
        arguments = [
            'cli', 'changefeed', operation,
            '--changefeed-id', changefeed,
        ]
    return {
        'provider_action': {
            'tool': 'cdc', 'arguments': arguments,
            'changefeed_id': changefeed,
        },
        'action_preview': {
            'tool': 'cdc', 'verb': f'changefeed {operation}',
            'argument_count': len(arguments) - 3,
            'provider_constructed': True,
            'sensitive_values_redacted': operation == 'create',
        },
        'impact': _impact(request, data_movement_possible=True),
        'provider_operation_observation': {
            'provider_finality_authority': True,
            'automatic_mutation_retry': False,
        },
    }


def _compile_br_action(request):
    kind = request['resource_kind']
    operation = request['operation_id']
    mode = 'backup' if operation in {'backup_full', 'backup_br'} else (
        'restore')
    if operation in {'backup_full', 'restore_full'}:
        scope = 'full'
    elif operation == 'restore_point':
        scope = 'point'
    else:
        scope = 'db' if kind == 'database' else 'table'
    arguments = [mode, scope, f'--storage={_allowlisted_br_uri(request)}']
    if scope in {'db', 'table'}:
        path = _path(request)
        database = path[-2] if scope == 'table' else path[-1]
        arguments.append(f'--db={database}')
        if scope == 'table':
            arguments.append(f'--table={path[-1]}')
    if scope == 'point':
        draft = request.get('draft') or {}
        timestamp = draft.get('restore_timestamp')
        if not isinstance(timestamp, str) or not re.fullmatch(
                r'[0-9T: .+\-Z]{1,128}', timestamp):
            raise RelationalClientError(
                'TiDB BR restore timestamp is invalid')
        arguments.append(f'--restored-ts={timestamp}')
        if draft.get('full_backup_storage_uri'):
            arguments.append(
                '--full-backup-storage=' + _allowlisted_br_uri(
                    request, 'full_backup_storage_uri'))
    arguments.append('--redact-info-log=true')
    cancel_arguments = None
    if mode == 'restore':
        cancel_arguments = [
            'abort', 'restore', scope, *arguments[2:],
        ]
    return {
        'provider_action': {
            'arguments': arguments,
            'cancel_arguments': cancel_arguments,
        },
        'action_preview': {
            'tool': 'br', 'verb': f'{mode} {scope}',
            'argument_count': len(arguments) - 2,
            'provider_constructed': True,
        },
        'impact': _impact(
            request,
            availability_risk='high' if mode == 'restore' else 'medium',
            data_movement_possible=True,
        ),
        'provider_operation_observation': {
            'provider_finality_authority': True,
            'automatic_mutation_retry': False,
        },
    }


def _compile_control_plane(request):
    kind = request['resource_kind']
    operation = request['operation_id']
    draft = request.get('draft') or {}
    source = None
    parameters = ()
    if operation in {
            'backup_full', 'backup_br', 'restore_full', 'restore_br',
            'restore_point'}:
        return _compile_br_action(request)
    if kind == 'changefeed' and operation in {
            'create', 'pause', 'resume', 'remove'}:
        return _compile_cdc_action(request)
    if kind == 'placement-policy':
        name = draft['name'] if operation == 'create' else _path(request)[-1]
        if operation in {'create', 'alter'}:
            assignments, parameters = _placement_assignments(draft)
            source = (
                f'{operation.upper()} PLACEMENT POLICY {_quote(name)} '
                f'{assignments}'
            )
        elif operation == 'drop':
            source = f'DROP PLACEMENT POLICY {_quote(name)}'
    elif operation == 'configure_placement':
        policy = _quote(draft['policy_name'])
        path = _path(request)
        if kind == 'database':
            source = f'ALTER DATABASE {_quote(path[-1])} '
            source += f'PLACEMENT POLICY = {policy}'
        elif kind == 'table':
            source = f'ALTER TABLE {".".join(_quote(v) for v in path[-2:])} '
            source += f'PLACEMENT POLICY = {policy}'
        elif kind == 'partition' and len(path) >= 3:
            table = '.'.join(_quote(v) for v in path[-3:-1])
            source = f'ALTER TABLE {table} PARTITION {_quote(path[-1])} '
            source += f'PLACEMENT POLICY = {policy}'
    elif kind == 'resource-group':
        name = draft['name'] if operation == 'create' else _path(request)[-1]
        if operation in {'create', 'alter'}:
            assignments, parameters = _resource_group_assignments(draft)
            source = (
                f'{operation.upper()} RESOURCE GROUP {_quote(name)} '
                f'{assignments}'
            )
        elif operation == 'drop':
            source = f'DROP RESOURCE GROUP {_quote(name)}'
    elif operation == 'set_tiflash_replica' and kind in {
            'database', 'table'}:
        path = _path(request)
        target = _quote(path[-1]) if kind == 'database' else '.'.join(
            _quote(value) for value in path[-2:]
        )
        source = f'ALTER {kind.upper()} {target} SET TIFLASH REPLICA %s'
        parameters = (draft['replica_count'],)
        labels = draft.get('location_labels') or []
        if labels:
            if len(labels) > 64 or not all(
                    isinstance(value, str) and value and len(value) <= 256
                    for value in labels):
                raise RelationalClientError(
                    'TiDB TiFlash location labels must be non-empty text'
                )
            source += ' LOCATION LABELS ' + ', '.join(
                '%s' for _value in labels
            )
            parameters += tuple(labels)
    elif kind == 'job' and operation == 'cancel':
        source = 'ADMIN CANCEL DDL JOBS %s'
        parameters = (_target_id(request),)
    if source is None:
        raise RelationalClientError(
            'TiDB control-plane operation is unavailable'
        )
    return {
        'statements': [{'source': source, 'parameters': parameters}],
        'impact': _impact(
            request,
            availability_risk=('high' if operation == 'drop' else 'medium'),
            data_movement_possible=(
                kind in {'placement-policy', 'database', 'table',
                         'partition'}
            ),
        ),
        'provider_operation_observation': {
            'provider_finality_authority': True,
            'automatic_mutation_retry': False,
        },
    }


def _private_parts(request):
    plan = request.get('plan')
    payload = request.get('provider_payload')
    if not isinstance(plan, dict) or not isinstance(payload, dict) or not (
            isinstance(payload.get('route'), dict)):
        raise RelationalClientError('TiDB provider operation is invalid')
    return plan, payload['route']


def _trusted_file(route, key, label, executable=False):
    value = route.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RelationalClientError(f'TiDB {label} is required')
    path = Path(value).expanduser().resolve()
    if not path.is_file() or (executable and not os.access(path, os.X_OK)):
        raise RelationalClientError(f'TiDB {label} is unavailable')
    return str(path)


def _br_pd_addresses(route):
    value = route.get('br_pd_addresses')
    values = value.split(',') if isinstance(value, str) else value
    if not isinstance(values, list) or not 1 <= len(values) <= 16:
        raise RelationalClientError('TiDB BR PD addresses are required')
    result = []
    for item in values:
        item = str(item).strip()
        parsed = urlsplit('//' + item)
        try:
            port = parsed.port
        except ValueError:
            port = None
        if not parsed.hostname or port is None or parsed.username or (
                parsed.path or parsed.query or parsed.fragment or
                any(character in item for character in '\x00\r\n\t')):
            raise RelationalClientError('TiDB BR PD address is invalid')
        result.append(item)
    return ','.join(result)


def _run_br(route, arguments, timeout=None):
    if not isinstance(arguments, list) or not arguments or len(
            arguments) > 64 or any(
                not isinstance(value, str) or not value or '\x00' in value or
                len(value) > 16384 for value in arguments):
        raise RelationalClientError('TiDB BR arguments are invalid')
    timeout_value = route.get('br_timeout_seconds', 3600)
    if timeout is not None:
        timeout_value = timeout
    if isinstance(timeout_value, bool) or not isinstance(
            timeout_value, int) or not 1 <= timeout_value <= 86400:
        raise RelationalClientError('TiDB BR timeout is invalid')
    command = [
        _trusted_file(route, 'br_path', 'BR executable', True),
        *arguments,
        f'--pd={_br_pd_addresses(route)}',
    ]
    tls = [route.get(name) for name in ('br_ca', 'br_cert', 'br_key')]
    if any(tls) and not all(tls):
        raise RelationalClientError(
            'TiDB BR TLS requires CA, certificate, and key')
    if all(tls):
        command.extend([
            '--ca=' + _trusted_file(route, 'br_ca', 'BR CA file'),
            '--cert=' + _trusted_file(
                route, 'br_cert', 'BR certificate file'),
            '--key=' + _trusted_file(route, 'br_key', 'BR key file'),
        ])
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True,
            timeout=timeout_value,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RelationalClientError(
            'TiDB BR request failed to return') from exc
    output = (result.stdout or '') + (result.stderr or '')
    if len(output.encode('utf-8')) > 16 * 1024 * 1024:
        raise RelationalClientError('TiDB BR response exceeds size limit')
    if result.returncode != 0:
        raise RelationalClientError('TiDB BR request was rejected')
    return {
        'exit_code': 0,
        'stdout': result.stdout or '',
        'stderr': result.stderr or '',
        'provider_response_observed': True,
        'provider_finality_authority': True,
        'automatic_mutation_retry': False,
    }


def _cdc_server(route):
    value = route.get('ticdc_server')
    if not isinstance(value, str) or len(value) > 2048:
        raise RelationalClientError('TiCDC server endpoint is required')
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        port = None
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or (
            port is None or parsed.username or parsed.password or
            parsed.path not in {'', '/'} or parsed.query or parsed.fragment):
        raise RelationalClientError('TiCDC server endpoint is invalid')
    return value.rstrip('/')


def _run_cdc(route, arguments, timeout=None):
    if not isinstance(arguments, list) or not arguments or len(
            arguments) > 32 or any(
                not isinstance(value, str) or not value or '\x00' in value or
                len(value) > 16384 for value in arguments):
        raise RelationalClientError('TiCDC arguments are invalid')
    timeout_value = route.get('ticdc_timeout_seconds', 120)
    if timeout is not None:
        timeout_value = timeout
    if isinstance(timeout_value, bool) or not isinstance(
            timeout_value, int) or not 1 <= timeout_value <= 86400:
        raise RelationalClientError('TiCDC timeout is invalid')
    command = [
        _trusted_file(route, 'ticdc_path', 'TiCDC executable', True),
        *arguments, '--server', _cdc_server(route),
    ]
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True,
            timeout=timeout_value,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RelationalClientError(
            'TiCDC request failed to return') from exc
    output = (result.stdout or '') + (result.stderr or '')
    if len(output.encode('utf-8')) > 16 * 1024 * 1024:
        raise RelationalClientError('TiCDC response exceeds size limit')
    if result.returncode != 0:
        raise RelationalClientError('TiCDC request was rejected')
    return {
        'exit_code': 0,
        'stdout': result.stdout or '',
        'stderr': result.stderr or '',
        'provider_response_observed': True,
        'provider_finality_authority': True,
        'automatic_mutation_retry': False,
    }


def _execute_control_action(_client, request):
    payload = request.get('provider_payload') or {}
    action = payload.get('compiled', {}).get('provider_action') or {}
    runner = _run_cdc if action.get('tool') == 'cdc' else _run_br
    return {
        'accepted': True,
        'provider_response': runner(
            payload.get('route') or {}, action.get('arguments')),
        'provider_finality_authority': True,
        'automatic_mutation_retry': False,
    }


def _query_once(client, route, source, parameters=()):
    connection = client._connect({'route': route})
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(source, parameters)
        description = getattr(cursor, 'description', None)
        return {
            'columns': [str(item[0]) for item in description or []],
            'rows': list(cursor.fetchall()) if description else [],
            'provider_observation_only': True,
            'finality_interpreted_by_common_code': False,
        }
    finally:
        if cursor is not None:
            client._safe_close(cursor)
        client._forget_and_close(connection)


def _inspect_control_plane(client, request):
    plan, route = _private_parts(request)
    kind = plan['resource_kind']
    operation = plan['operation_id']
    draft = plan.get('draft') or {}
    target = plan.get('target_resource') or {}
    path = target.get('display_path') or [target.get('display_name')]
    name = draft.get('name') if operation == 'create' else path[-1]
    if operation in {'backup_full', 'backup_br'}:
        compiled = (request.get('provider_payload') or {}).get(
            'compiled', {})
        arguments = compiled.get('provider_action', {}).get(
            'arguments', [])
        storage = next(
            (value for value in arguments
             if value.startswith('--storage=')), None)
        if storage is None:
            raise RelationalClientError(
                'TiDB BR backup storage observation is unavailable')
        return {
            'provider_observation': _run_br(
                route, ['debug', 'backupmeta', 'validate', storage,
                        '--redact-info-log=true'], timeout=300),
            'provider_observation_only': True,
            'provider_finality_authority': True,
        }
    if operation in {'restore_full', 'restore_point'}:
        return {
            'provider_observation_only': True,
            'provider_finality_authority': True,
            'manual_scope_validation_required': True,
        }
    if kind == 'changefeed':
        compiled = (request.get('provider_payload') or {}).get(
            'compiled', {})
        action = compiled.get('provider_action', {})
        changefeed = action.get('changefeed_id')
        return {
            'provider_observation': _run_cdc(route, [
                'cli', 'changefeed', 'query', '--simple',
                '--changefeed-id', _changefeed_id(changefeed),
            ], timeout=30),
            'provider_observation_only': True,
            'provider_finality_authority': True,
        }
    if operation == 'restore_br':
        if kind == 'database':
            return _query_once(
                client, route,
                'SELECT SCHEMA_NAME FROM information_schema.SCHEMATA '
                'WHERE SCHEMA_NAME = %s', (path[-1],),
            )
        return _query_once(
            client, route,
            'SELECT TABLE_SCHEMA, TABLE_NAME FROM information_schema.TABLES '
            'WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s', tuple(path[-2:]),
        )
    if kind == 'placement-policy':
        return _query_once(
            client, route,
            'SELECT POLICY_NAME, PRIMARY_REGION, REGIONS, FOLLOWERS, '
            'VOTERS, LEARNERS, CONSTRAINTS FROM '
            'information_schema.PLACEMENT_POLICIES WHERE POLICY_NAME = %s',
            (name,),
        )
    if kind == 'resource-group':
        return _query_once(
            client, route,
            'SELECT NAME, RU_PER_SEC, PRIORITY, BURSTABLE FROM '
            'information_schema.RESOURCE_GROUPS WHERE NAME = %s', (name,)
        )
    if operation == 'set_tiflash_replica':
        database = path[-2] if kind == 'table' and len(path) >= 2 else path[-1]
        table = path[-1] if kind == 'table' else None
        source = ('SELECT TABLE_SCHEMA, TABLE_NAME, REPLICA_COUNT, '
                  'AVAILABLE, PROGRESS FROM information_schema.'
                  'TIFLASH_REPLICA WHERE TABLE_SCHEMA = %s')
        parameters = (database,)
        if table is not None:
            source += ' AND TABLE_NAME = %s'
            parameters += (table,)
        return _query_once(client, route, source, parameters)
    if kind == 'job':
        return _query_once(client, route, 'ADMIN SHOW DDL JOBS 100')
    if operation == 'configure_placement':
        if kind == 'database':
            return _query_once(
                client, route,
                'SELECT SCHEMA_NAME, TIDB_PLACEMENT_POLICY_NAME FROM '
                'information_schema.SCHEMATA WHERE SCHEMA_NAME = %s',
                (path[-1],),
            )
        if kind == 'table':
            return _query_once(
                client, route,
                'SELECT TABLE_SCHEMA, TABLE_NAME, TIDB_PLACEMENT_POLICY_NAME '
                'FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s '
                'AND TABLE_NAME = %s', tuple(path[-2:]),
            )
        return _query_once(
            client, route,
            'SELECT TABLE_SCHEMA, TABLE_NAME, PARTITION_NAME, '
            'TIDB_PLACEMENT_POLICY_NAME FROM information_schema.PARTITIONS '
            'WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND '
            'PARTITION_NAME = %s', tuple(path[-3:]),
        )
    raise RelationalClientError('TiDB operation observation is unavailable')


def _cancel_control_plane(_client, request):
    plan, route = _private_parts(request)
    if not plan.get('cancellable'):
        raise RelationalClientError(
            'TiDB operation is not declared cancellable')
    compiled = (request.get('provider_payload') or {}).get('compiled', {})
    arguments = compiled.get('provider_action', {}).get('cancel_arguments')
    if not arguments:
        raise RelationalClientError(
            'TiDB operation has no provider cancellation')
    return _run_br(route, arguments)


def _post_validate_control_plane(client, request):
    plan, _route = _private_parts(request)
    observation = _inspect_control_plane(client, request)
    rows = observation.get('rows') or []
    operation = plan['operation_id']
    confirmed = bool(rows)
    if operation in {'backup_full', 'backup_br'}:
        confirmed = bool(observation.get('provider_observation'))
    elif operation in {'restore_full', 'restore_point'}:
        confirmed = False
    elif operation == 'restore_br':
        confirmed = bool(rows)
    elif plan['resource_kind'] == 'changefeed':
        output = observation.get('provider_observation', {}).get(
            'stdout', '')
        try:
            state = json.loads(output).get('state')
        except (AttributeError, TypeError, ValueError):
            state = None
        admitted = {
            'create': {'normal', 'stopped', 'warning'},
            'pause': {'stopped'},
            'resume': {'normal', 'warning'},
            'remove': {'removed'},
        }
        confirmed = state in admitted.get(operation, set())
    elif operation == 'drop':
        confirmed = not rows
    elif operation == 'cancel':
        job_id = str(_target_id({'target_resource': plan['target_resource']}))
        matching = [row for row in rows if row and str(row[0]) == job_id]
        confirmed = bool(matching) and any(
            str(value).lower() in {'cancelled', 'canceled', 'rollback done'}
            for value in matching[0]
        )
    elif operation == 'configure_placement' and rows:
        confirmed = str(rows[0][-1]) == str(plan['draft']['policy_name'])
    elif operation == 'set_tiflash_replica':
        expected = int(plan['draft']['replica_count'])
        confirmed = bool(rows) and all(int(row[2]) == expected for row in rows)
    return {
        'confirmed': confirmed,
        'operation_id': operation,
        'observation': observation,
        'provider_finality_authority': True,
    }


ADMINISTRATION = DistributedSQLControlPlane(
    replace(_BASE_ADMINISTRATION.dialect), CONTROL_OPERATIONS,
    _compile_control_plane, inspector=_inspect_control_plane,
    canceller=_cancel_control_plane,
    post_validator=_post_validate_control_plane,
    action_executor=_execute_control_action,
)


def _version(row):
    value = str(row[0] if row else '')
    match = re.search(r'Release Version:\s*v?(\d+\.\d+\.\d+)', value, re.I)
    if match is None:
        match = re.search(r'TiDB[^\n]*?v?(\d+\.\d+\.\d+)', value, re.I)
    if match is None:
        raise RelationalClientError('TiDB version is unavailable')
    return match.group(1)


def _extras(cursor, _request, generation):
    values = []
    for component, instance, status_address, version in optional_rows(
        cursor,
        'SELECT TYPE, INSTANCE, STATUS_ADDRESS, VERSION '
        'FROM information_schema.CLUSTER_INFO ORDER BY TYPE, INSTANCE',
    ):
        values.append(resource('node', [component], instance, generation, {
            'status_address': str(status_address), 'version': str(version),
        }))
    for name, followers, learners, constraints in optional_rows(
        cursor,
        'SELECT POLICY_NAME, FOLLOWERS, LEARNERS, CONSTRAINTS '
        'FROM information_schema.PLACEMENT_POLICIES ORDER BY POLICY_NAME',
    ):
        values.append(resource('placement-policy', [], name, generation, {
            'followers': followers, 'learners': learners,
            'constraints': None if constraints is None else str(constraints),
        }))
    for name, ru_per_sec, priority in optional_rows(
        cursor,
        'SELECT NAME, RU_PER_SEC, PRIORITY '
        'FROM information_schema.RESOURCE_GROUPS ORDER BY NAME',
    ):
        values.append(resource('resource-group', [], name, generation, {
            'ru_per_sec': ru_per_sec, 'priority': str(priority),
        }))
    return values


class TiDBProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    return TiDBProvider(
        context,
        permissions,
        client or create_sql_client(
            PROFILE,
            permissions,
            wire='mysql',
            version_query='SELECT TIDB_VERSION()',
            version_parser=_version,
            metadata_reader=lambda connection, request: mysql_catalog(
                connection, request, 'TiDB', _extras
            ),
            administration=ADMINISTRATION,
        ),
    )
