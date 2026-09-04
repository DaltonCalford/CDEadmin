"""CockroachDB 26.1.3 distributed SQL provider."""

import json
import os
import re
import subprocess
import time
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
    optional_rows,
    postgresql_catalog,
    resource,
    sql_administration,
)
from ..distributed_control_plane import DistributedSQLControlPlane
from pgadmin.cdeadmin.visual_admin import (
    ControlPlaneOperation,
    control_plane_field as cp_field,
)


PROFILE = PilotProfile(
    'org.cdeadmin.cockroachdb', 'cockroachdb-native', 'cockroachdb',
    'CockroachDB', '26.1.3', 'postgresql_wire',
    'distributed-relational', 'cockroachdb-sql', 'CockroachDB SQL',
    'cockroachdb-serializable-retry-native', 'tabular',
    (
        'cluster', 'node', 'locality', 'database', 'schema', 'table',
        'column', 'index', 'constraint', 'sequence', 'view',
        'materialized-view', 'type', 'function', 'procedure', 'trigger',
        'partition', 'user', 'role', 'privilege', 'range',
        'zone-config', 'job', 'schedule', 'changefeed',
    ),
    ('cockroach-sql', 'node-status', 'debug-zip', 'backup-restore'),
    semantic_sql_dialect={
        'language_profile': 'cockroachdb-sql', 'quote_open': '"',
        'supports_rollup': True,
    },
)


_BASE_ADMINISTRATION = sql_administration('cockroachdb', 'postgresql', (
    'node', 'locality', 'range', 'zone-config', 'job', 'schedule',
    'changefeed', 'materialized-view', 'type', 'partition',
))
_COCKROACH_SUPPORTED = dict(_BASE_ADMINISTRATION.dialect.supported)
for _kind in ('function', 'procedure', 'trigger'):
    _COCKROACH_SUPPORTED[_kind] = frozenset({
        'inspect', 'create', 'drop',
    })
_COCKROACH_SUPPORTED['materialized-view'] = frozenset({
    'inspect', 'create', 'rename', 'drop',
})
_COCKROACH_DIALECT = replace(
    _BASE_ADMINISTRATION.dialect,
    supported=_COCKROACH_SUPPORTED,
    not_applicable_concepts=frozenset({
        'domains', 'extensions_and_plugins',
    }),
    concept_resource_kinds={
        'servers': ('cluster',),
        'tablespaces_and_filespaces': ('zone-config',),
    },
)


def _choice(field_id, label, values, **options):
    return cp_field(
        field_id, label, 'select', options=[
            {'value': value, 'label': title} for value, title in values
        ], **options
    )


_ZONE_FIELDS = (
    cp_field('num_replicas', 'Replica count', 'number', False,
             minimum=1, maximum=15),
    cp_field('num_voters', 'Voting replica count', 'number', False,
             minimum=1, maximum=15),
    cp_field('range_min_bytes', 'Minimum range bytes', 'number', False,
             minimum=0),
    cp_field('range_max_bytes', 'Maximum range bytes', 'number', False,
             minimum=1),
    cp_field('gc_ttl_seconds', 'GC TTL seconds', 'number', False,
             minimum=1),
    cp_field('constraints', 'Replica constraints', 'json', False,
             default=[], json_type='array'),
    cp_field('voter_constraints', 'Voter constraints', 'json', False,
             default=[], json_type='array'),
    cp_field('lease_preferences', 'Lease preferences', 'json', False,
             default=[], json_type='array'),
)

CONTROL_OPERATIONS = (
    ControlPlaneOperation(
        'node', 'decommission', 'Decommission node', 'destructive',
        'topology_admin', (
            _choice('checks', 'Readiness checks', (
                ('enabled', 'Enabled'), ('strict', 'Strict'),
                ('skip', 'Skip'),
            ), required=False, default='enabled'),
            _choice('wait', 'Wait for', (
                ('all', 'All replicas relocated'),
                ('none', 'Request acceptance only'),
            ), required=False, default='all'),
        ), impact_scope='node', long_running=True
    ),
    ControlPlaneOperation(
        'node', 'recommission', 'Recommission node', 'admin',
        'topology_admin', impact_scope='node', long_running=True
    ),
    ControlPlaneOperation(
        'database', 'configure_zone', 'Configure database placement',
        'admin', 'topology_admin', _ZONE_FIELDS, impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'database', 'reset_zone', 'Reset database placement',
        'admin', 'topology_admin', impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'database', 'set_primary_region', 'Set primary region', 'admin',
        'topology_admin', (
            cp_field('region', 'Cluster region', 'text', True,
                     max_length=256, pattern=r'[A-Za-z0-9_.-]+'),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'database', 'add_region', 'Add database region', 'admin',
        'topology_admin', (
            cp_field('region', 'Cluster region', 'text', True,
                     max_length=256, pattern=r'[A-Za-z0-9_.-]+'),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'database', 'drop_region', 'Drop database region', 'destructive',
        'topology_admin', (
            cp_field('region', 'Database region', 'text', True,
                     max_length=256, pattern=r'[A-Za-z0-9_.-]+'),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'database', 'set_secondary_region', 'Set secondary region', 'admin',
        'topology_admin', (
            cp_field('region', 'Database region', 'text', True,
                     max_length=256, pattern=r'[A-Za-z0-9_.-]+'),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'database', 'drop_secondary_region', 'Drop secondary region',
        'destructive', 'topology_admin', impact_scope='cluster',
        long_running=True
    ),
    ControlPlaneOperation(
        'database', 'set_survival_goal', 'Set survival goal', 'admin',
        'topology_admin', (
            _choice('goal', 'Failure survival goal', (
                ('zone', 'Zone failure'), ('region', 'Region failure'),
            ), required=True, default='zone'),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'database', 'set_placement', 'Set replica placement policy',
        'admin', 'topology_admin', (
            _choice('policy', 'Placement policy', (
                ('default', 'Default'), ('restricted', 'Restricted'),
            ), required=True, default='default'),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'table', 'configure_zone', 'Configure table placement',
        'admin', 'topology_admin', _ZONE_FIELDS, impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'table', 'reset_zone', 'Reset table placement',
        'admin', 'topology_admin', impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'table', 'set_locality', 'Set table locality', 'admin',
        'topology_admin', (
            _choice('locality', 'Table locality', (
                ('global', 'Global'),
                ('regional_by_table', 'Regional by table'),
                ('regional_by_row', 'Regional by row'),
            ), required=True),
            cp_field('region', 'Table home region', 'text', False,
                     max_length=256, pattern=r'[A-Za-z0-9_.-]+',
                     visible_when={
                         'field_id': 'locality',
                         'equals': 'regional_by_table',
                     }),
            cp_field('region_column', 'Region column', 'text', False,
                     max_length=256,
                     pattern=r'[A-Za-z_][A-Za-z0-9_$-]*',
                     visible_when={
                         'field_id': 'locality',
                         'equals': 'regional_by_row',
                     }),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'materialized-view', 'refresh', 'Refresh materialized view', 'admin',
        'maintenance_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'table', 'scatter', 'Scatter table ranges', 'admin',
        'topology_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'index', 'scatter', 'Scatter index ranges', 'admin',
        'topology_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'range', 'relocate_lease', 'Relocate range lease', 'admin',
        'topology_admin', (
            cp_field('destination_store_id', 'Destination store ID',
                     'number', True, minimum=1),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'range', 'relocate_voter', 'Relocate voting replica', 'admin',
        'topology_admin', (
            cp_field('source_store_id', 'Source store ID', 'number', True,
                     minimum=1),
            cp_field('destination_store_id', 'Destination store ID',
                     'number', True, minimum=1),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'range', 'relocate_nonvoter', 'Relocate non-voting replica', 'admin',
        'topology_admin', (
            cp_field('source_store_id', 'Source store ID', 'number', True,
                     minimum=1),
            cp_field('destination_store_id', 'Destination store ID',
                     'number', True, minimum=1),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'job', 'pause', 'Pause job', 'admin', 'maintenance_admin',
        (cp_field('reason', 'Reason', 'text', False, max_length=1024),),
        impact_scope='cluster', long_running=True, cancellable=False
    ),
    ControlPlaneOperation(
        'job', 'resume', 'Resume job', 'admin', 'maintenance_admin',
        impact_scope='cluster', long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'job', 'cancel', 'Cancel job', 'destructive', 'maintenance_admin',
        impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'schedule', 'pause', 'Pause schedule', 'admin',
        'maintenance_admin', impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'schedule', 'resume', 'Resume schedule', 'admin',
        'maintenance_admin', impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'schedule', 'drop', 'Drop schedule', 'destructive',
        'maintenance_admin', impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'database', 'backup', 'Back up database', 'admin', 'backup_admin',
        (
            cp_field('destination_uri', 'Approved destination URI', 'text',
                     True, max_length=4096, sensitive=True),
            _choice('revision_history', 'Revision history', (
                ('default', 'Default'),
                ('with_revision_history', 'Include revision history'),
            ), required=False, default='default'),
        ),
        impact_scope='cluster', long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'cluster', 'restore_database', 'Restore database', 'destructive',
        'restore_admin', (
            cp_field('source_uri', 'Approved backup URI', 'text', True,
                     max_length=4096, sensitive=True),
            cp_field('database_name', 'Database name', 'text', True,
                     max_length=256,
                     pattern=r'[A-Za-z_][A-Za-z0-9_$-]*'),
            cp_field('new_database_name', 'Restore as database', 'text',
                     False, max_length=256,
                     pattern=r'[A-Za-z_][A-Za-z0-9_$-]*'),
        ), impact_scope='cluster', long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'table', 'create_changefeed', 'Create table changefeed', 'admin',
        'replication_admin', (
            cp_field('sink_uri', 'Approved sink URI', 'text', True,
                     max_length=8192, sensitive=True),
            _choice('initial_scan', 'Initial scan', (
                ('yes', 'Yes'), ('no', 'No'), ('only', 'Only'),
            ), required=False, default='yes'),
            _choice('format', 'Message format', (
                ('json', 'JSON'), ('csv', 'CSV'),
            ), required=False, default='json'),
            _choice('envelope', 'Envelope', (
                ('wrapped', 'Wrapped'), ('row', 'Row'),
                ('key_only', 'Key only'),
            ), required=False, default='wrapped'),
            cp_field('resolved_interval', 'Resolved timestamp interval',
                     'text', False, max_length=64,
                     pattern=r'[0-9]+(?:ms|s|m|h)'),
        ), impact_scope='cluster', long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'changefeed', 'pause', 'Pause changefeed', 'admin',
        'replication_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'changefeed', 'resume', 'Resume changefeed', 'admin',
        'replication_admin', impact_scope='cluster', long_running=True,
        cancellable=True
    ),
    ControlPlaneOperation(
        'changefeed', 'cancel', 'Cancel changefeed', 'destructive',
        'replication_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'changefeed', 'add_table', 'Add table to changefeed', 'admin',
        'replication_admin', (
            cp_field('table_name', 'Qualified table name', 'text', True,
                     max_length=768),
            _choice('initial_scan', 'Initial scan', (
                ('yes', 'Yes'), ('no', 'No'), ('only', 'Only'),
            ), required=False, default='yes'),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'changefeed', 'drop_table', 'Remove table from changefeed',
        'destructive', 'replication_admin', (
            cp_field('table_name', 'Qualified table name', 'text', True,
                     max_length=768),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'changefeed', 'set_sink', 'Change changefeed sink', 'admin',
        'replication_admin', (
            cp_field('sink_uri', 'Approved sink URI', 'text', True,
                     max_length=8192, sensitive=True),
        ), impact_scope='cluster', long_running=True
    ),
)


def _path(request):
    target = request.get('target_resource')
    if not isinstance(target, dict):
        raise RelationalClientError('CockroachDB target is required')
    parts = target.get('display_path') or [target.get('display_name')]
    if not isinstance(parts, list) or not parts:
        raise RelationalClientError('CockroachDB target path is invalid')
    return [str(item) for item in parts]


def _quote(value):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise RelationalClientError('CockroachDB identifier is invalid')
    return '"' + value.replace('"', '""') + '"'


def _target_id(request):
    value = _path(request)[-1]
    try:
        result = int(value)
        if result <= 0:
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise RelationalClientError(
            'CockroachDB operation requires a numeric native identifier'
        ) from None


def _positive_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RelationalClientError(
            f'CockroachDB {label} must be a positive integer')
    return value


def _impact(request, **extra):
    return {
        'scope': 'cluster',
        'target_resource_id': request.get('target_resource', {}).get(
            'resource_id'
        ),
        'availability_risk': extra.pop('availability_risk', 'low'),
        'data_movement_possible': extra.pop(
            'data_movement_possible', False
        ),
        **extra,
    }


def _allowlisted_uri(request, field_id, allowlist_key=None):
    value = request['draft'].get(field_id)
    if not isinstance(value, str) or not value.strip():
        raise RelationalClientError('CockroachDB backup URI is required')
    value = value.strip()
    scheme = urlsplit(value).scheme.lower()
    backup_schemes = {
        'nodelocal', 'userfile', 'external', 's3', 'gs', 'azure', 'http',
        'https',
    }
    changefeed_schemes = {
        'azure', 'azure-kafka', 'confluent-cloud', 'external', 'gcpubsub',
        'gs', 'http', 'https', 'kafka', 'nodelocal', 'null', 'pulsar', 's3',
        'webhook-http', 'webhook-https',
    }
    admitted = changefeed_schemes if field_id == 'sink_uri' else (
        backup_schemes)
    if scheme not in admitted:
        raise RelationalClientError(
            'CockroachDB provider URI scheme is not admitted'
        )
    route = request.get('_provider_route') or {}
    prefixes = route.get(
        allowlist_key or 'backup_destination_allowlist')
    if isinstance(prefixes, str):
        prefixes = [item.strip() for item in prefixes.split(',')]
    if not isinstance(prefixes, list) or not prefixes or not any(
        isinstance(prefix, str) and value.startswith(prefix)
        for prefix in prefixes
    ):
        raise RelationalClientError(
            'CockroachDB backup URI is outside the endpoint allowlist'
        )
    return value


def _qualified_identifier(value, label='qualified identifier'):
    if not isinstance(value, str):
        raise RelationalClientError(f'CockroachDB {label} is invalid')
    parts = value.split('.')
    if not 1 <= len(parts) <= 3 or any(
            not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$-]{0,255}', part)
            for part in parts):
        raise RelationalClientError(f'CockroachDB {label} is invalid')
    return '.'.join(_quote(part) for part in parts)


def _region(draft):
    value = draft.get('region')
    if not isinstance(value, str) or not re.fullmatch(
            r'[A-Za-z0-9_.-]{1,256}', value):
        raise RelationalClientError('CockroachDB region is invalid')
    return _quote(value)


def _changefeed_options(draft):
    options = []
    parameters = []
    for name, admitted, default in (
        ('initial_scan', {'yes', 'no', 'only'}, 'yes'),
        ('format', {'json', 'csv'}, 'json'),
        ('envelope', {'wrapped', 'row', 'key_only'}, 'wrapped'),
    ):
        value = draft.get(name, default)
        if value not in admitted:
            raise RelationalClientError(
                f'CockroachDB changefeed {name} is invalid')
        options.append(f'{name} = %s')
        parameters.append(value)
    resolved = draft.get('resolved_interval')
    if resolved:
        if not isinstance(resolved, str) or not re.fullmatch(
                r'[0-9]+(?:ms|s|m|h)', resolved):
            raise RelationalClientError(
                'CockroachDB resolved timestamp interval is invalid')
        options.append('resolved = %s')
        parameters.append(resolved)
    return options, parameters


def _node_action(request):
    operation = request['operation_id']
    node_id = str(_target_id(request))
    arguments = ['node', operation]
    if operation == 'decommission':
        draft = request.get('draft') or {}
        checks = draft.get('checks', 'enabled')
        wait = draft.get('wait', 'all')
        if checks not in {'enabled', 'strict', 'skip'} or wait not in {
                'all', 'none'}:
            raise RelationalClientError(
                'CockroachDB decommission options are invalid')
        arguments.extend([f'--checks={checks}', f'--wait={wait}'])
    arguments.append(node_id)
    return {
        'provider_action': {'arguments': arguments},
        'action_preview': {
            'tool': 'cockroach', 'verb': f'node {operation}',
            'argument_count': len(arguments) - 2,
            'provider_constructed': True,
        },
        'impact': _impact(
            request, availability_risk='high',
            data_movement_possible=operation == 'decommission',
        ),
        'provider_operation_observation': {
            'job_backed': False,
            'finality_interpreted_by_common_code': False,
        },
    }


def _compile_control_plane(request):
    kind = request['resource_kind']
    operation = request['operation_id']
    draft = request.get('draft') or {}
    source = None
    parameters = ()
    statements = []
    impact = _impact(request)
    if kind == 'node' and operation in {'decommission', 'recommission'}:
        return _node_action(request)
    if operation in {'configure_zone', 'reset_zone'}:
        target = '.'.join(_quote(part) for part in _path(request))
        prefix = f'ALTER {kind.upper()} {target} CONFIGURE ZONE'
        if operation == 'reset_zone':
            source = prefix + ' DISCARD'
        else:
            assignments = []
            values = []
            names = (
                ('num_replicas', 'num_replicas'),
                ('num_voters', 'num_voters'),
                ('range_min_bytes', 'range_min_bytes'),
                ('range_max_bytes', 'range_max_bytes'),
                ('gc_ttl_seconds', 'gc.ttlseconds'),
                ('constraints', 'constraints'),
                ('voter_constraints', 'voter_constraints'),
                ('lease_preferences', 'lease_preferences'),
            )
            for field_id, sql_name in names:
                value = draft.get(field_id)
                if value in (None, [], ''):
                    continue
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, separators=(',', ':'))
                assignments.append(f'{sql_name} = %s')
                values.append(value)
            if not assignments:
                raise RelationalClientError(
                    'CockroachDB zone configuration has no changes'
                )
            source = prefix + ' USING ' + ', '.join(assignments)
            parameters = tuple(values)
            impact = _impact(
                request, availability_risk='medium',
                data_movement_possible=True
            )
    elif kind == 'database' and operation in {
            'set_primary_region', 'add_region', 'drop_region',
            'set_secondary_region', 'drop_secondary_region',
            'set_survival_goal', 'set_placement'}:
        database = _quote(_path(request)[-1])
        source = f'ALTER DATABASE {database} '
        if operation == 'set_primary_region':
            source += 'SET PRIMARY REGION ' + _region(draft)
        elif operation == 'add_region':
            source += 'ADD REGION ' + _region(draft)
        elif operation == 'drop_region':
            source += 'DROP REGION ' + _region(draft)
        elif operation == 'set_secondary_region':
            source += 'SET SECONDARY REGION ' + _region(draft)
        elif operation == 'drop_secondary_region':
            source += 'DROP SECONDARY REGION'
        elif operation == 'set_survival_goal':
            goal = draft.get('goal')
            if goal not in {'zone', 'region'}:
                raise RelationalClientError(
                    'CockroachDB survival goal is invalid')
            source += f'SURVIVE {goal.upper()} FAILURE'
        else:
            policy = draft.get('policy')
            if policy not in {'default', 'restricted'}:
                raise RelationalClientError(
                    'CockroachDB placement policy is invalid')
            statements.append({
                'source': (
                    'SET enable_multiregion_placement_policy = true'
                ),
                'parameters': (),
            })
            source += f'PLACEMENT {policy.upper()}'
        impact = _impact(
            request, availability_risk='medium',
            data_movement_possible=True,
        )
    elif kind == 'table' and operation == 'set_locality':
        table = '.'.join(_quote(part) for part in _path(request))
        locality = draft.get('locality')
        source = f'ALTER TABLE {table} SET LOCALITY '
        if locality == 'global':
            source += 'GLOBAL'
        elif locality == 'regional_by_table':
            source += 'REGIONAL BY TABLE'
            if draft.get('region'):
                source += ' IN ' + _region(draft)
        elif locality == 'regional_by_row':
            source += 'REGIONAL BY ROW'
            column = draft.get('region_column')
            if column:
                if not re.fullmatch(
                        r'[A-Za-z_][A-Za-z0-9_$-]{0,255}', column):
                    raise RelationalClientError(
                        'CockroachDB region column is invalid')
                source += ' AS ' + _quote(column)
        else:
            raise RelationalClientError(
                'CockroachDB table locality is invalid')
        impact = _impact(
            request, availability_risk='medium',
            data_movement_possible=True,
        )
    elif kind == 'materialized-view' and operation == 'refresh':
        target = '.'.join(_quote(part) for part in _path(request))
        source = f'REFRESH MATERIALIZED VIEW {target}'
        impact = _impact(request, availability_risk='low')
    elif kind in {'table', 'index'} and operation == 'scatter':
        path = _path(request)
        if kind == 'index':
            if len(path) < 2:
                raise RelationalClientError(
                    'CockroachDB index path is invalid')
            target = (
                '.'.join(_quote(part) for part in path[:-1]) + '@' +
                _quote(path[-1])
            )
        else:
            target = '.'.join(_quote(part) for part in path)
        source = f'ALTER {kind.upper()} {target} SCATTER'
        impact = _impact(
            request, availability_risk='low', data_movement_possible=True)
    elif kind == 'range' and operation in {
            'relocate_lease', 'relocate_voter', 'relocate_nonvoter'}:
        range_id = _target_id(request)
        destination = _positive_integer(
            draft.get('destination_store_id'), 'destination store ID')
        if operation == 'relocate_lease':
            source = (
                f'ALTER RANGE {range_id} RELOCATE LEASE TO {destination}'
            )
        else:
            source_store = _positive_integer(
                draft.get('source_store_id'), 'source store ID')
            subject = (
                'VOTERS' if operation == 'relocate_voter'
                else 'NONVOTERS'
            )
            source = (
                f'ALTER RANGE {range_id} RELOCATE {subject} '
                f'FROM {source_store} TO {destination}'
            )
        impact = _impact(
            request, availability_risk='medium',
            data_movement_possible=True)
    elif kind == 'job' and operation in {'pause', 'resume', 'cancel'}:
        source = f'{operation.upper()} JOB %s'
        parameters = (_target_id(request),)
        reason = draft.get('reason')
        if reason and operation == 'pause':
            source += ' WITH REASON = %s'
            parameters += (reason,)
    elif kind == 'schedule' and operation in {'pause', 'resume', 'drop'}:
        source = f'{operation.upper()} SCHEDULE %s'
        parameters = (_target_id(request),)
    elif kind == 'database' and operation == 'backup':
        database = _quote(_path(request)[-1])
        source = f'BACKUP DATABASE {database} INTO %s'
        parameters = (_allowlisted_uri(request, 'destination_uri'),)
        if draft.get('revision_history') == 'with_revision_history':
            source += ' WITH revision_history'
        impact = _impact(request, availability_risk='low')
    elif kind == 'cluster' and operation == 'restore_database':
        database = _quote(draft['database_name'])
        source = f'RESTORE DATABASE {database} FROM LATEST IN %s'
        parameters = (_allowlisted_uri(request, 'source_uri'),)
        if draft.get('new_database_name'):
            source += ' WITH new_db_name = %s'
            parameters += (draft['new_database_name'],)
        impact = _impact(
            request, availability_risk='high',
            data_movement_possible=True
        )
    elif kind == 'table' and operation == 'create_changefeed':
        noun = 'TABLE'
        target = '.'.join(_quote(part) for part in _path(request))
        source = f'CREATE CHANGEFEED FOR {noun} {target} INTO %s'
        parameters = (
            _allowlisted_uri(
                request, 'sink_uri', 'changefeed_sink_allowlist'),
        )
        options, values = _changefeed_options(draft)
        source += ' WITH ' + ', '.join(options)
        parameters += tuple(values)
        impact = _impact(request, availability_risk='medium')
    elif kind == 'changefeed' and operation in {
            'pause', 'resume', 'cancel'}:
        source = f'{operation.upper()} JOB %s'
        parameters = (_target_id(request),)
    elif kind == 'changefeed' and operation in {
            'add_table', 'drop_table'}:
        verb = 'ADD' if operation == 'add_table' else 'DROP'
        table = _qualified_identifier(draft.get('table_name'), 'table name')
        source = f'ALTER CHANGEFEED %s {verb} TABLE {table}'
        parameters = (_target_id(request),)
        if operation == 'add_table':
            initial_scan = draft.get('initial_scan', 'yes')
            if initial_scan not in {'yes', 'no', 'only'}:
                raise RelationalClientError(
                    'CockroachDB changefeed initial scan is invalid')
            # CockroachDB 26.1 cannot infer the placeholder type in this
            # ALTER CHANGEFEED option. The value is drawn from the strict
            # provider allowlist above, so emitting the literal is safe.
            source += f" WITH initial_scan = '{initial_scan}'"
    elif kind == 'changefeed' and operation == 'set_sink':
        source = 'ALTER CHANGEFEED %s SET sink = %s'
        parameters = (
            _target_id(request),
            _allowlisted_uri(
                request, 'sink_uri', 'changefeed_sink_allowlist'),
        )
    if source is None:
        raise RelationalClientError(
            'CockroachDB control-plane operation is unavailable'
        )
    statements.append({'source': source, 'parameters': parameters})
    return {
        'statements': statements,
        'impact': impact,
        'provider_operation_observation': {
            'job_backed': kind in {
                'job', 'schedule', 'changefeed'
            } or operation in {
                'backup', 'restore_database', 'create_changefeed',
            },
            'finality_interpreted_by_common_code': False,
        },
    }


def _private_parts(request):
    plan = request.get('plan')
    payload = request.get('provider_payload')
    if not isinstance(plan, dict) or not isinstance(payload, dict):
        raise RelationalClientError(
            'CockroachDB provider operation handle is invalid'
        )
    route = payload.get('route')
    if not isinstance(route, dict):
        raise RelationalClientError(
            'CockroachDB provider operation route is unavailable'
        )
    return plan, route


def _trusted_file(route, key, label, executable=False):
    value = route.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RelationalClientError(f'CockroachDB {label} is required')
    path = Path(value).expanduser().resolve()
    if not path.is_file() or (executable and not os.access(path, os.X_OK)):
        raise RelationalClientError(f'CockroachDB {label} is unavailable')
    return str(path)


def _cli_address(route):
    host = route.get('host')
    port = route.get('port', 26257)
    if not isinstance(host, str) or not re.fullmatch(
            r'[A-Za-z0-9_.:\-\[\]]{1,512}', host):
        raise RelationalClientError('CockroachDB CLI host is invalid')
    if isinstance(port, bool) or not isinstance(port, int) or not (
            1 <= port <= 65535):
        raise RelationalClientError('CockroachDB CLI port is invalid')
    if ':' in host and not host.startswith('['):
        host = f'[{host}]'
    return f'{host}:{port}'


def _run_node(route, arguments, timeout=None):
    if not isinstance(arguments, list) or len(arguments) < 3 or len(
            arguments) > 32 or any(
                not isinstance(value, str) or not value or '\x00' in value or
                len(value) > 4096 for value in arguments):
        raise RelationalClientError(
            'CockroachDB control arguments are invalid')
    timeout_value = route.get('cockroach_cli_timeout_seconds', 120)
    if timeout is not None:
        timeout_value = timeout
    if isinstance(timeout_value, bool) or not isinstance(
            timeout_value, int) or not 1 <= timeout_value <= 3600:
        raise RelationalClientError('CockroachDB CLI timeout is invalid')
    flags = [f'--host={_cli_address(route)}', '--format=json']
    insecure = route.get('cockroach_insecure', False)
    if not isinstance(insecure, bool):
        raise RelationalClientError(
            'CockroachDB insecure transport option is invalid')
    if insecure:
        flags.append('--insecure')
    else:
        certs = route.get('cockroach_certs_dir')
        if not isinstance(certs, str) or not certs.strip():
            raise RelationalClientError(
                'CockroachDB certificate directory is required')
        cert_path = Path(certs).expanduser().resolve()
        if not cert_path.is_dir():
            raise RelationalClientError(
                'CockroachDB certificate directory is unavailable')
        flags.append(f'--certs-dir={cert_path}')
    cluster_name = route.get('cockroach_cluster_name')
    if cluster_name:
        if not isinstance(cluster_name, str) or not re.fullmatch(
                r'[A-Za-z0-9_.-]{1,256}', cluster_name):
            raise RelationalClientError(
                'CockroachDB cluster name is invalid')
        flags.append(f'--cluster-name={cluster_name}')
    command = [
        _trusted_file(
            route, 'cockroach_path', 'cockroach executable', True),
        *arguments[:-1], *flags, arguments[-1],
    ]
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True,
            timeout=timeout_value,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RelationalClientError(
            'CockroachDB control request failed to return') from exc
    output = (result.stdout or '') + (result.stderr or '')
    if len(output.encode('utf-8')) > 8 * 1024 * 1024:
        raise RelationalClientError(
            'CockroachDB control response exceeds size limit')
    if result.returncode != 0:
        raise RelationalClientError(
            'CockroachDB control request was rejected')
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
    action = payload.get('compiled', {}).get('provider_action', {})
    return {
        'accepted': True,
        'provider_response': _run_node(
            payload.get('route') or {}, action.get('arguments')),
        'provider_finality_authority': True,
        'automatic_mutation_retry': False,
    }


def _node_observation(route, node_id):
    response = _run_node(
        route, ['node', 'status', '--decommission', str(node_id)],
        timeout=30,
    )
    document = None
    try:
        document = json.loads(response.get('stdout') or '')
    except (TypeError, ValueError) as exc:
        raise RelationalClientError(
            'CockroachDB node status response is invalid') from exc
    return {
        'provider_observation': response,
        'document': document,
        'provider_observation_only': True,
        'provider_finality_authority': True,
    }


def _node_membership(document, node_id):
    rows = document if isinstance(document, list) else [document]
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get('id')
        try:
            matches = int(value) == int(node_id)
        except (TypeError, ValueError):
            matches = False
        if matches and isinstance(row.get('membership'), str):
            return row['membership'].lower()
    return None


def _result_job_id(request):
    result = request.get('provider_result')
    if isinstance(result, dict):
        for statement in result.get('statement_results') or []:
            for row in statement.get('rows') or []:
                if isinstance(row, (list, tuple)) and row:
                    try:
                        return int(row[0])
                    except (TypeError, ValueError):
                        continue
    plan = request.get('plan') or {}
    target = plan.get('target_resource') or {}
    path = target.get('display_path') or [target.get('display_name')]
    try:
        return int(path[-1])
    except (IndexError, TypeError, ValueError):
        raise RelationalClientError(
            'CockroachDB provider job identifier is unavailable'
        ) from None


def _query_once(client, route, source, parameters=()):
    connection = client._connect({'route': route})
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(source, parameters)
        description = getattr(cursor, 'description', None)
        columns = [item[0] for item in description or []]
        rows = list(cursor.fetchall()) if description else []
        return {
            'columns': columns,
            'rows': rows,
            'provider_observation_only': True,
            'finality_interpreted_by_common_code': False,
        }
    finally:
        if cursor is not None:
            client._safe_close(cursor)
        client._forget_and_close(connection)


def _mutate_once(client, route, source, parameters=()):
    connection = client._connect({'route': route})
    cursor = None
    commit_requested = False
    try:
        cursor = connection.cursor()
        cursor.execute(source, parameters)
        commit = getattr(connection, 'commit', None)
        if callable(commit):
            commit_requested = True
            commit()
        return {
            'request_accepted_by_driver': True,
            'commit_requested': commit_requested,
            'provider_observation_only': True,
            'finality_interpreted_by_common_code': False,
        }
    except Exception as exc:
        rollback = getattr(connection, 'rollback', None)
        if callable(rollback):
            try:
                rollback()
            except Exception:
                pass
        if isinstance(exc, RelationalClientError):
            raise
        raise RelationalClientError(
            'CockroachDB control request failed '
            f'({type(exc).__name__})'
        ) from None
    finally:
        if cursor is not None:
            client._safe_close(cursor)
        client._forget_and_close(connection)


def _inspect_control_plane(client, request):
    plan, route = _private_parts(request)
    kind = plan['resource_kind']
    operation = plan['operation_id']
    if kind == 'node':
        return _node_observation(route, _result_job_id(request))
    if kind == 'changefeed' or operation == 'create_changefeed':
        job_id = _result_job_id(request)
        return _query_once(
            client, route,
            'SELECT job_id, status, error, sink_uri, full_table_names '
            'FROM [SHOW CHANGEFEED JOBS] WHERE job_id = %s',
            (job_id,),
        )
    if kind == 'job' or operation in {'backup', 'restore_database'}:
        job_id = _result_job_id(request)
        return _query_once(
            client, route,
            'SELECT job_id, status, fraction_completed, error, '
            'created, finished FROM [SHOW JOBS] WHERE job_id = %s',
            (job_id,),
        )
    if kind == 'schedule':
        return _query_once(
            client, route,
            'SELECT id, label, schedule_status, next_run '
            'FROM [SHOW SCHEDULES] WHERE id = %s',
            (_result_job_id(request),),
        )
    if operation in {'configure_zone', 'reset_zone'}:
        target = plan.get('target_resource') or {}
        path = target.get('display_path') or [target.get('display_name')]
        if operation == 'reset_zone':
            return _query_once(
                client, route,
                'SELECT target, raw_config_sql FROM '
                '[SHOW ALL ZONE CONFIGURATIONS] WHERE target = %s',
                (kind.upper() + ' ' + '.'.join(str(item) for item in path),),
            )
        name = '.'.join(_quote(str(item)) for item in path)
        return _query_once(
            client, route,
            f'SHOW ZONE CONFIGURATION FOR {kind.upper()} {name}',
        )
    if kind == 'database' and operation in {
            'set_primary_region', 'add_region', 'drop_region',
            'set_secondary_region', 'drop_secondary_region',
            'set_survival_goal', 'set_placement'}:
        name = _quote(_path(plan)[-1])
        observation = _query_once(
            client, route,
            'SELECT database_name, primary_region, secondary_region, '
            'regions, survival_goal FROM [SHOW DATABASES] '
            'WHERE database_name = %s', (_path(plan)[-1],),
        )
        created = _query_once(
            client, route, f'SHOW CREATE DATABASE {name}')
        observation['create_rows'] = created['rows']
        return observation
    if kind in {'table', 'index'} and operation == 'scatter':
        return _query_once(client, route, 'SELECT 1')
    if kind == 'table' and operation == 'set_locality':
        name = '.'.join(_quote(part) for part in _path(plan))
        return _query_once(client, route, f'SHOW CREATE TABLE {name}')
    if kind == 'materialized-view' and operation == 'refresh':
        name = '.'.join(_quote(part) for part in _path(plan))
        return _query_once(
            client, route, f'SHOW CREATE VIEW {name}')
    if kind == 'range':
        database = route.get('database')
        if not isinstance(database, str) or not re.fullmatch(
                r'[A-Za-z_][A-Za-z0-9_$-]{0,255}', database):
            raise RelationalClientError(
                'CockroachDB range observation requires a database')
        return _query_once(
            client, route,
            'SELECT range_id, lease_holder, voting_replicas, '
            'non_voting_replicas FROM [SHOW RANGES FROM DATABASE ' +
            _quote(database) + ' WITH DETAILS] WHERE range_id = %s',
            (_target_id(plan),),
        )
    raise RelationalClientError(
        'CockroachDB operation observation is unavailable'
    )


def _cancel_control_plane(client, request):
    plan, route = _private_parts(request)
    if not plan.get('cancellable'):
        raise RelationalClientError(
            'CockroachDB operation is not declared cancellable'
        )
    return _mutate_once(
        client, route, 'CANCEL JOB %s', (_result_job_id(request),)
    )


def _wait_for_status(client, request, admitted, timeout=90):
    deadline = time.monotonic() + timeout
    observation = None
    while time.monotonic() < deadline:
        observation = _inspect_control_plane(client, request)
        rows = observation.get('rows') or []
        if rows and str(rows[0][1]).lower() in admitted:
            return observation
        time.sleep(0.25)
    return observation or _inspect_control_plane(client, request)


def _post_validate_control_plane(client, request):
    plan, _route = _private_parts(request)
    operation = plan['operation_id']
    kind = plan['resource_kind']
    if kind in {'job', 'changefeed'} or operation in {
            'backup', 'restore_database', 'create_changefeed'}:
        if operation == 'pause':
            statuses = {'paused'}
        elif operation == 'resume' or operation == 'create_changefeed':
            statuses = {'running', 'pending'}
        elif operation == 'cancel':
            statuses = {'canceled'}
        elif operation in {'backup', 'restore_database'}:
            statuses = {'succeeded'}
        else:
            statuses = {'paused'}
        observation = _wait_for_status(client, request, statuses)
    else:
        observation = _inspect_control_plane(client, request)
    rows = observation.get('rows') or []
    draft = plan.get('draft') or {}
    confirmed = False
    if kind == 'node':
        membership = _node_membership(
            observation.get('document'), _result_job_id(request))
        expected = (
            {'decommissioning', 'decommissioned'}
            if operation == 'decommission' else {'active'}
        )
        confirmed = membership in expected
    elif operation == 'pause':
        confirmed = bool(rows) and str(rows[0][2 if (
            kind == 'schedule'
        ) else 1]).lower() == 'paused'
    elif operation == 'resume':
        confirmed = bool(rows) and str(rows[0][2 if (
            kind == 'schedule'
        ) else 1]).lower() in {'active', 'running', 'pending'}
    elif operation in {'cancel', 'backup', 'restore_database'}:
        expected = 'canceled' if operation == 'cancel' else 'succeeded'
        confirmed = bool(rows) and str(rows[0][1]).lower() == expected
    elif operation == 'create_changefeed':
        confirmed = bool(rows) and str(rows[0][1]).lower() in {
            'running', 'pending'
        }
    elif operation == 'drop' and kind == 'schedule':
        confirmed = not rows
    elif operation == 'configure_zone':
        raw = str(rows[0][1]) if rows else ''
        configured = [
            ('num_replicas', 'num_replicas'),
            ('num_voters', 'num_voters'),
            ('range_min_bytes', 'range_min_bytes'),
            ('range_max_bytes', 'range_max_bytes'),
            ('gc_ttl_seconds', 'gc.ttlseconds'),
            ('constraints', 'constraints'),
            ('voter_constraints', 'voter_constraints'),
            ('lease_preferences', 'lease_preferences'),
        ]
        confirmed = bool(rows) and all(
            value in (None, [], '') or re.search(
                rf'(?m)^\s*{re.escape(sql_name)}\s*=', raw)
            for value, sql_name in (
                (draft.get(field_id), sql_name)
                for field_id, sql_name in configured
            )
        )
    elif operation == 'reset_zone':
        confirmed = not rows
    elif kind == 'database' and operation in {
            'set_primary_region', 'add_region', 'drop_region',
            'set_secondary_region', 'drop_secondary_region',
            'set_survival_goal', 'set_placement'}:
        row = rows[0] if rows else None
        regions = set(row[3] or []) if row else set()
        region = draft.get('region')
        if operation == 'set_primary_region':
            confirmed = bool(row) and row[1] == region
        elif operation == 'add_region':
            confirmed = bool(row) and region in regions
        elif operation == 'drop_region':
            confirmed = bool(row) and region not in regions
        elif operation == 'set_secondary_region':
            confirmed = bool(row) and row[2] == region
        elif operation == 'drop_secondary_region':
            confirmed = bool(row) and row[2] in {None, ''}
        elif operation == 'set_survival_goal':
            confirmed = bool(row) and str(row[4]).lower() == draft.get('goal')
        else:
            create_rows = observation.get('create_rows') or []
            statement = str(create_rows[0][1]).upper() if create_rows else ''
            policy = str(draft.get('policy')).upper()
            confirmed = (
                bool(statement) and (
                    f'PLACEMENT {policy}' in statement
                    if policy == 'RESTRICTED'
                    else 'PLACEMENT RESTRICTED' not in statement
                )
            )
    elif kind == 'table' and operation == 'set_locality':
        statement = str(rows[0][1]).upper() if rows else ''
        locality = draft.get('locality')
        expected = {
            'global': 'LOCALITY GLOBAL',
            'regional_by_table': 'LOCALITY REGIONAL BY TABLE',
            'regional_by_row': 'LOCALITY REGIONAL BY ROW',
        }.get(locality, '')
        confirmed = bool(expected) and expected in statement
        if confirmed and locality == 'regional_by_table' and draft.get(
                'region'):
            confirmed = f'IN "{str(draft["region"]).upper()}"' in statement
        if confirmed and locality == 'regional_by_row' and draft.get(
                'region_column'):
            region_column = str(draft['region_column']).upper()
            confirmed = f'AS {region_column}' in statement
    elif kind == 'materialized-view' and operation == 'refresh':
        confirmed = bool(rows) and 'CREATE MATERIALIZED VIEW' in str(
            rows[0][1]).upper()
    elif operation == 'scatter' and kind in {'table', 'index'}:
        confirmed = rows == [(1,)] or rows == [[1]]
    elif kind == 'range' and rows:
        destination = draft.get('destination_store_id')
        if operation == 'relocate_lease':
            confirmed = rows[0][1] == destination
        elif operation == 'relocate_voter':
            confirmed = destination in set(rows[0][2] or [])
        elif operation == 'relocate_nonvoter':
            confirmed = destination in set(rows[0][3] or [])
    elif kind == 'changefeed' and operation in {
            'add_table', 'drop_table', 'set_sink'} and rows:
        if operation == 'set_sink':
            payload = request.get('provider_payload') or {}
            statements = (payload.get('compiled') or {}).get(
                'statements') or []
            parameters = (
                statements[0].get('parameters') or ()
                if statements else ()
            )
            expected_sink = parameters[1] if len(parameters) > 1 else None
            confirmed = str(rows[0][3]) == expected_sink
        else:
            tables = set(rows[0][4] or [])
            table = draft.get('table_name')
            confirmed = (
                table in tables if operation == 'add_table'
                else table not in tables
            )
    return {
        'confirmed': confirmed,
        'operation_id': operation,
        'observation': observation,
        'provider_finality_authority': True,
    }


class CockroachDBAdministration(DistributedSQLControlPlane):
    """CockroachDB-specific relational forms plus native controls."""

    def _form(self, kind, operation):
        if kind == 'materialized-view' and operation == 'create':
            return {
                'form_id': 'materialized-view.create',
                'title': 'Create materialized view',
                'fields': [
                    self._field('name', 'Name', 'text', True),
                    self._field('parent', 'Parent schema', 'text', False),
                    self._field(
                        'query', 'Materialized query', 'code', True,
                        'Enter one SELECT or WITH query; complete DDL is not '
                        'accepted.',
                    ),
                    self._field(
                        'with_data', 'Populate now', 'boolean',
                        default=True),
                ],
            }
        return super()._form(kind, operation)

    def _compile_create(self, request):
        if request['resource_kind'] != 'materialized-view':
            return super()._compile_create(request)
        draft = request['draft']
        options = draft.get('options') or {}
        name = self._identifier(draft['name'])
        qualified = self._new_object_name(name, options)
        query = self._query_body(draft.get('definition'))
        suffix = '' if options.get('with_data', True) else ' WITH NO DATA'
        return [{
            'source': (
                f'CREATE MATERIALIZED VIEW {qualified} AS {query}{suffix}'
            ),
            'parameters': (),
        }]


ADMINISTRATION = CockroachDBAdministration(
    _COCKROACH_DIALECT, CONTROL_OPERATIONS,
    _compile_control_plane, inspector=_inspect_control_plane,
    canceller=_cancel_control_plane,
    post_validator=_post_validate_control_plane,
    action_executor=_execute_control_action,
)


def _version(row):
    value = str(row[0] if row else '')
    match = re.search(r'CockroachDB[^\n]*?v?(\d+\.\d+\.\d+)', value, re.I)
    if match is None:
        raise RelationalClientError('CockroachDB version is unavailable')
    return match.group(1)


def _extras(cursor, request, generation):
    values = []
    for schema, table, name, constraint_type in optional_rows(
        cursor,
        'SELECT constraint_schema, table_name, constraint_name, '
        'constraint_type FROM information_schema.table_constraints '
        'WHERE constraint_schema NOT IN '
        "('pg_catalog', 'information_schema', 'crdb_internal') "
        'ORDER BY 1, 2, 3',
    ):
        values.append(resource(
            'constraint', [schema, table], name, generation,
            {'constraint_type': str(constraint_type)},
        ))
    for schema, name in optional_rows(
        cursor,
        'SELECT sequence_schema, sequence_name '
        'FROM information_schema.sequences ORDER BY 1, 2',
    ):
        values.append(resource('sequence', [schema], name, generation))
    for schema, name in optional_rows(
        cursor,
        'SELECT schemaname, matviewname FROM pg_catalog.pg_matviews '
        'ORDER BY 1, 2',
    ):
        values.append(resource(
            'materialized-view', [schema], name, generation
        ))
    for schema, name, type_kind in optional_rows(
        cursor,
        'SELECT n.nspname, t.typname, t.typtype '
        'FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n '
        'ON n.oid = t.typnamespace WHERE t.typisdefined '
        "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND t.typtype IN ('c', 'd', 'e') ORDER BY 1, 2",
    ):
        values.append(resource(
            'type', [schema], name, generation,
            {'type_kind': str(type_kind)},
        ))
    for schema, name, routine_kind in optional_rows(
        cursor,
        'SELECT n.nspname, p.proname, p.prokind '
        'FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n '
        'ON n.oid = p.pronamespace '
        "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND p.prokind IN ('f', 'p') ORDER BY 1, 2",
    ):
        kind = 'procedure' if str(routine_kind) == 'p' else 'function'
        values.append(resource(kind, [schema], name, generation))
    for schema, table, name in optional_rows(
        cursor,
        'SELECT event_object_schema, event_object_table, trigger_name '
        'FROM information_schema.triggers WHERE event_object_schema NOT IN '
        "('pg_catalog', 'information_schema', 'crdb_internal') "
        'ORDER BY 1, 2, 3',
    ):
        values.append(resource(
            'trigger', [schema, table], name, generation
        ))
    route = request.get('route') or {}
    database = route.get('database')
    partition_rows = []
    if isinstance(database, str) and re.fullmatch(
            r'[A-Za-z_][A-Za-z0-9_$-]{0,255}', database):
        partition_rows = optional_rows(
            cursor,
            f'SHOW PARTITIONS FROM DATABASE {_quote(database)}',
        )
    for database_name, table, name, parent, columns, index, value, *_ in (
            partition_rows):
        values.append(resource(
            'partition', [database_name, table, index], name, generation, {
                'parent_name': None if parent is None else str(parent),
                'column_names': None if columns is None else str(columns),
                'partition_value': None if value is None else str(value),
            },
        ))
    for name, can_login in optional_rows(
        cursor,
        'SELECT rolname, rolcanlogin FROM pg_catalog.pg_roles '
        'ORDER BY rolname',
    ):
        values.append(resource('role', [], name, generation, {
            'can_login': bool(can_login),
        }))
        if can_login:
            values.append(resource('user', [], name, generation, {
                'can_login': True,
            }))
    for grantee, schema, table, privilege in optional_rows(
        cursor,
        'SELECT grantee, table_schema, table_name, privilege_type '
        'FROM information_schema.table_privileges '
        'WHERE table_schema NOT IN '
        "('pg_catalog', 'information_schema', 'crdb_internal') "
        'ORDER BY 1, 2, 3, 4',
    ):
        display_name = f'{grantee}:{privilege}:{schema}.{table}'
        values.append(resource(
            'privilege', [schema, table, str(grantee)], display_name,
            generation, {'privilege_type': str(privilege)},
        ))
    route = request.get('route') or {}
    if route.get('cockroach_path'):
        response = _run_node(
            route, ['node', 'status', '--format=json'], timeout=30)
        try:
            node_rows = json.loads(response.get('stdout') or '')
        except (TypeError, ValueError) as exc:
            raise RelationalClientError(
                'CockroachDB node inventory is invalid') from exc
        if not isinstance(node_rows, list):
            raise RelationalClientError(
                'CockroachDB node inventory is invalid')
        for row in node_rows:
            if not isinstance(row, dict) or not str(row.get('id') or ''):
                raise RelationalClientError(
                    'CockroachDB node inventory entry is invalid')
            node_id = str(row['id'])
            values.append(resource(
                'node', [], node_id, generation, dict(row)))
            locality = str(row.get('locality') or '')
            if locality:
                values.append(resource(
                    'locality', [node_id], locality, generation, {
                        'node_id': node_id,
                        'is_live': str(row.get('is_live')).lower() == 'true',
                        'is_available': str(
                            row.get('is_available')).lower() == 'true',
                    }
                ))
    for region, zones in optional_rows(
        cursor, 'SHOW REGIONS FROM CLUSTER',
    ):
        values.append(resource(
            'locality', ['cluster'], region, generation, {
                'region': str(region),
                'zones': list(zones or []),
            }
        ))
    if isinstance(database, str) and re.fullmatch(
            r'[A-Za-z_][A-Za-z0-9_$-]{0,255}', database):
        range_columns = (
            'start_key', 'end_key', 'range_id', 'range_size_mb',
            'lease_holder', 'lease_holder_locality', 'replicas',
            'replica_localities', 'voting_replicas',
            'non_voting_replicas', 'learner_replicas',
            'split_enforced_until', 'range_size', 'span_stats',
        )
        range_rows = optional_rows(
            cursor,
            'SELECT * FROM [SHOW RANGES FROM DATABASE ' +
            _quote(database) + ' WITH DETAILS] ORDER BY range_id LIMIT 2000',
        )
        for row in range_rows:
            native = dict(zip(range_columns, row))
            values.append(resource(
                'range', [database], native['range_id'], generation, native
            ))
    for job_id, status, description in optional_rows(
        cursor,
        'SELECT job_id, status, description FROM [SHOW JOBS] '
        'ORDER BY created DESC LIMIT 200',
    ):
        values.append(resource('job', [], job_id, generation, {
            'status': str(status), 'description': str(description),
        }))
    for row in optional_rows(
        cursor,
        'SELECT id, label, schedule_status, next_run '
        'FROM [SHOW SCHEDULES] ORDER BY id',
    ):
        values.append(resource('schedule', [], row[0], generation, {
            'name': str(row[1]), 'status': str(row[2]),
            'next_run': None if row[3] is None else str(row[3]),
        }))
    for row in optional_rows(
        cursor,
        'SELECT job_id, status, description '
        'FROM [SHOW CHANGEFEED JOBS] ORDER BY job_id',
    ):
        values.append(resource('changefeed', [], row[0], generation, {
            'status': str(row[1]), 'description': str(row[2]),
        }))
    for row in optional_rows(
        cursor,
        'SELECT target, raw_config_sql '
        'FROM [SHOW ALL ZONE CONFIGURATIONS] ORDER BY target',
    ):
        values.append(resource('zone-config', [], row[0], generation, {
            'raw_config_sql': str(row[1]),
        }))
    return values


class CockroachDBProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    return CockroachDBProvider(
        context,
        permissions,
        client or create_sql_client(
            PROFILE,
            permissions,
            wire='postgresql',
            version_query='SELECT version()',
            version_parser=_version,
            metadata_reader=lambda connection, request: postgresql_catalog(
                connection, request, 'CockroachDB', _extras
            ),
            administration=ADMINISTRATION,
            pool_namespace=context.pool_namespace,
        ),
    )
