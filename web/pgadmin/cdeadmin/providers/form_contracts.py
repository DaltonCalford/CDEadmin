##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Exact provider-owned server and database form contracts.

The UI renderer and individual controls are reusable.  Form identity,
labels, fields, validation metadata, supported operations, and execution
routes are not shared between providers.  There is intentionally no generic
or default database form.
"""

from __future__ import annotations

import copy


class ProviderFormContractError(ValueError):
    """An active provider does not own a complete form contract."""


def _field(field_id, label, control='text', *, required=False, **values):
    options = values.get('options')
    if options is not None:
        values['options'] = [
            option if isinstance(option, dict) else {
                'value': option,
                'label': str(option),
            }
            for option in options
        ]
    return {
        'field_id': field_id,
        'label': label,
        'control': control,
        'required': required,
        **values,
    }


_DISPLAY_NAME = _field(
    'display_name', 'Navigator display name',
    help='Optional label; the native identifier remains authoritative.',
)
_DEFINITION = _field(
    'definition', 'Provider-native definition', 'code',
    help='Native definition accepted by this provider.',
)
_OPTIONS = _field(
    'options', 'Execution options', 'json', default={},
    help='Provider-specific creation or alteration options.',
)
_CHANGES = _field(
    'changes', 'Provider-native changes', 'json', required=True,
)
_ONLINE = _field(
    'online', 'Request online operation', 'boolean', default=False,
)
_CASCADE = _field(
    'cascade', 'Cascade to dependent objects', 'boolean', default=False,
)
_CONFIRM = _field(
    'confirmation', 'Type the native name to confirm', required=True,
)


# Every entry is deliberately explicit.  Adding an active profile without an
# entry is an activation error rather than an invitation to use a generic UI.
_DATABASE_SPECS = {
    'postgresql-native': {
        'engine_id': 'postgresql', 'noun': 'database',
        'identity_label': 'Database name',
        'connection_fields': (
            _field('maintenance_database', 'Maintenance database'),
            _field('role', 'Connection role'),
            _field('service', 'libpq service name'),
        ),
    },
    'mysql-native': {
        'engine_id': 'mysql', 'noun': 'database',
        'identity_label': 'MySQL database name',
        'create_fields': (
            _field('name', 'MySQL database name', required=True),
            _field('if_not_exists', 'Do not fail if it already exists',
                   'boolean', default=False),
            _field('character_set', 'Default character set'),
            _field('collation', 'Default collation'),
            _field('encryption', 'Default encryption', 'select',
                   options=('Y', 'N')),
        ),
        'alter_fields': (
            _field('character_set', 'Default character set'),
            _field('collation', 'Default collation'),
            _field('encryption', 'Default encryption', 'select',
                   options=('Y', 'N')),
            _field('read_only', 'Database read-only state', 'select',
                   options=('ON', 'OFF')),
        ),
        'drop_fields': (
            _field('confirmation',
                   'Type the exact MySQL database name to confirm',
                   required=True),
        ),
        'connection_fields': (
            _field('charset', 'Default character set'),
            _field('collation', 'Default collation'),
            _field('read_only', 'Open read-only', 'boolean', default=False),
        ),
    },
    'mariadb-native': {
        'engine_id': 'mariadb', 'noun': 'database',
        'identity_label': 'MariaDB database name',
        'create_fields': (
            _field('name', 'MariaDB database name', required=True),
            _field('or_replace', 'Replace an existing database',
                   'boolean', default=False),
            _field('if_not_exists', 'Do not fail if it already exists',
                   'boolean', default=False),
            _field('character_set', 'Default character set'),
            _field('collation', 'Default collation'),
            _field('comment', 'Database comment', 'multiline'),
        ),
        'alter_fields': (
            _field('character_set', 'Default character set'),
            _field('collation', 'Default collation'),
            _field('comment', 'Database comment', 'multiline'),
        ),
        'drop_fields': (
            _field(
                'confirmation',
                'Type the exact MariaDB database name to confirm',
                required=True,
            ),
        ),
        'connection_fields': (
            _field('charset', 'Default character set'),
            _field('collation', 'Default collation'),
            _field('sql_mode', 'Session SQL mode'),
        ),
    },
    'duckdb-native': {
        'engine_id': 'duckdb', 'noun': 'database file',
        'identity_label': 'DuckDB database file', 'identity_control': 'file',
        'create_fields': (
            _field(
                'name', 'Database filename', required=True,
                placeholder='analytics.duckdb',
                help=(
                    'Created below the endpoint-approved database root; the '
                    '.duckdb suffix is added when omitted.'
                ),
            ),
            _field(
                'config', 'Creation configuration', 'json', default={},
                help=(
                    'Named scalar options passed directly to DuckDB 1.5.2 '
                    'when the new database file is opened.'
                ),
            ),
        ),
        'native_alter': False,
        'unsupported_reason': (
            'DuckDB 1.5.2 has no ALTER DATABASE statement. Connection and '
            'session settings are edited through their provider-owned forms.'
        ),
        'drop_fields': (
            _field(
                'confirmation',
                'Type the exact database path to confirm deletion',
                required=True,
            ),
        ),
        'connection_fields': (
            _field('alias', 'Attachment alias'),
            _field('read_only', 'Open read-only', 'boolean', default=False),
            _field('config', 'DuckDB configuration', 'json', default={}),
        ),
    },
    'firebird-native': {
        'engine_id': 'firebird', 'noun': 'database',
        'identity_label': 'Firebird database filename or alias',
        'create_identity': 'database_path',
        'create_fields': (
            _field(
                'database_path',
                'Absolute database filename on the Firebird server',
                required=True,
                placeholder='/var/lib/firebird/data/example.fdb',
                help=(
                    'The path is evaluated on the Firebird server and must '
                    'remain below the server profile creation root.'
                ),
            ),
            _field(
                'page_size', 'Page size', 'select', default='8192',
                options=('4096', '8192', '16384', '32768'),
            ),
            _field(
                'default_charset', 'Default character set',
                default='UTF8',
            ),
            _field(
                'sql_dialect', 'Database SQL dialect', 'select', default='3',
                options=('1', '3'),
            ),
            _field(
                'forced_writes', 'Force synchronous writes', 'boolean',
                default=True,
            ),
            _field(
                'reserve_space', 'Reserve page space for record versions',
                'boolean', default=True,
            ),
        ),
        'drop_fields': (
            _field(
                'confirmation',
                'Type the exact Firebird database filename or alias to '
                'confirm',
                required=True,
            ),
        ),
        'connection_fields': (
            _field('role', 'Initial role'),
            _field('charset', 'Connection character set', default='UTF8'),
            _field('session_time_zone', 'Session time zone'),
            _field('no_gc', 'Disable cooperative garbage collection',
                   'boolean', default=False),
            _field('no_db_triggers', 'Disable database triggers', 'boolean',
                   default=False),
            _field('dbkey_scope', 'DBKEY scope', 'select',
                   default='TRANSACTION',
                   options=('TRANSACTION', 'ATTACHMENT')),
            _field('transaction_isolation', 'Default transaction isolation',
                   'select', default='SNAPSHOT', options=(
                       'READ_COMMITTED',
                       'READ_COMMITTED_NO_RECORD_VERSION',
                       'READ_COMMITTED_RECORD_VERSION',
                       'READ_COMMITTED_READ_CONSISTENCY',
                       'SNAPSHOT', 'SERIALIZABLE',
                   )),
            _field('transaction_access', 'Default transaction access',
                   'select', default='WRITE', options=('WRITE', 'READ')),
            _field('transaction_lock_timeout',
                   'Transaction lock timeout (seconds)', 'number',
                   default=-1, minimum=-1, maximum=32767),
            _field('transaction_auto_commit',
                   'Native autocommit (writes cannot be rolled back later)',
                   'boolean', default=False),
            _field('transaction_no_auto_undo',
                   'No auto undo (native rollback still applies)',
                   'boolean', default=False),
            _field('transaction_ignore_limbo',
                   'Ignore records from limbo transactions',
                   'boolean', default=False),
            _field('statement_timeout_ms',
                   'Statement timeout (milliseconds; 0 uses database policy)',
                   'number', minimum=0, maximum=2147483647),
            _field('session_idle_timeout_seconds',
                   'Session idle timeout (seconds; 0 uses database policy)',
                   'number', minimum=0, maximum=2147483647),
        ),
    },
    'mongodb-native': {
        'engine_id': 'mongodb', 'noun': 'database',
        'identity_label': 'MongoDB database name',
        'connection_fields': (
            _field('auth_source', 'Authentication database'),
            _field('read_preference', 'Read preference', 'select',
                   default='primary', options=('primary', 'primaryPreferred',
                                               'secondary',
                                               'secondaryPreferred',
                                               'nearest')),
            _field('read_concern_level', 'Read concern'),
            _field('write_concern', 'Write concern'),
        ),
    },
    'neo4j-native': {
        'engine_id': 'neo4j', 'noun': 'graph database',
        'identity_label': 'Neo4j database name',
        'connection_fields': (
            _field('access_mode', 'Access mode', 'select', default='write',
                   options=('read', 'write')),
            _field('fetch_size', 'Record fetch size', 'number', default=1000,
                   minimum=1),
            _field('impersonated_user', 'Impersonated user'),
        ),
    },
    'cassandra-native': {
        'engine_id': 'cassandra', 'noun': 'keyspace',
        'identity_label': 'Cassandra keyspace name',
        'connection_fields': (
            _field('consistency', 'Consistency level'),
            _field('serial_consistency', 'Serial consistency level'),
            _field('execution_profile', 'Execution profile'),
        ),
    },
    'redis-native': {
        'engine_id': 'redis', 'noun': 'logical database',
        'identity_label': 'Redis database number',
        'identity_control': 'number',
        'connection_fields': (
            _field('key_prefix', 'Navigator key prefix'),
            _field('scan_count', 'SCAN page size', 'number', default=250,
                   minimum=1, maximum=10000),
        ),
        'native_create': False, 'native_alter': False, 'native_drop': False,
        'unsupported_reason': (
            'Redis logical databases are configured by the server; CDEadmin '
            'can connect to or forget a database number but cannot create, '
            'alter, or drop it independently.'
        ),
    },
    'xtdb-native': {
        'engine_id': 'xtdb', 'noun': 'database',
        'identity_label': 'XTDB database',
        'connection_fields': (
            _field('default_schema', 'Default SQL schema'),
            _field('transaction_mode', 'Transaction mode'),
            _field('read_only', 'Open read-only', 'boolean', default=False),
        ),
        'native_create': False, 'native_alter': False, 'native_drop': False,
        'unsupported_reason': (
            'The XTDB server owns its database lifecycle; this interface '
            'connects to the configured XTDB database.'
        ),
    },
    'clickhouse-native': {
        'engine_id': 'clickhouse', 'noun': 'database',
        'identity_label': 'ClickHouse database name',
        'connection_fields': (
            _field('engine', 'Database engine'),
            _field('settings', 'Database settings', 'json', default={}),
            _field('read_only', 'Open read-only', 'boolean', default=False),
        ),
    },
    'influxdb-native': {
        'engine_id': 'influxdb', 'noun': 'database',
        'identity_label': 'InfluxDB database name',
        'connection_fields': (
            _field('retention_period', 'Retention period'),
            _field('query_language', 'Default query language', 'select',
                   default='sql', options=('sql', 'influxql')),
        ),
    },
    'milvus-native': {
        'engine_id': 'milvus', 'noun': 'database',
        'identity_label': 'Milvus database name',
        'connection_fields': (
            _field('consistency_level', 'Default consistency level'),
            _field('properties', 'Database properties', 'json', default={}),
        ),
    },
    'opensearch-native': {
        'engine_id': 'opensearch', 'noun': 'index namespace',
        'identity_label': 'OpenSearch index or alias',
        'lifecycle_kind': 'index',
        'connection_fields': (
            _field('routing', 'Default routing value'),
            _field('preference', 'Shard preference'),
            _field('allow_no_indices', 'Allow no indices', 'boolean',
                   default=False),
        ),
    },
    'opensearch-sql-ppl': {
        'engine_id': 'opensearch_sql_ppl', 'noun': 'index namespace',
        'identity_label': 'OpenSearch index or alias',
        'lifecycle_kind': 'index',
        'connection_fields': (
            _field('query_language', 'Query language', 'select', default='sql',
                   options=('sql', 'ppl')),
            _field('fetch_size', 'Fetch size', 'number', default=1000,
                   minimum=1),
        ),
    },
    'sqlite-native': {
        'engine_id': 'sqlite', 'noun': 'database file',
        'identity_label': 'SQLite database file', 'identity_control': 'file',
        'create_fields': (
            _field('name', 'Database filename', required=True,
                   max_length=255),
            _field(
                'page_size', 'Page size', 'select', default='4096',
                options=(
                    '512', '1024', '2048', '4096', '8192', '16384',
                    '32768', '65536',
                ),
            ),
            _field(
                'encoding', 'Text encoding', 'select', default='UTF-8',
                options=('UTF-8', 'UTF-16', 'UTF-16le', 'UTF-16be'),
            ),
            _field(
                'auto_vacuum', 'Auto-vacuum mode', 'select', default='NONE',
                options=('NONE', 'FULL', 'INCREMENTAL'),
            ),
            _field('application_id', 'Application ID', 'number', default=0,
                   minimum=-2147483648, maximum=2147483647),
            _field('user_version', 'User version', 'number', default=0,
                   minimum=-2147483648, maximum=2147483647),
        ),
        'alter_fields': (
            _field(
                'journal_mode', 'Journal mode', 'select',
                options=(
                    'DELETE', 'TRUNCATE', 'PERSIST', 'MEMORY', 'WAL', 'OFF',
                ),
            ),
            _field(
                'synchronous', 'Synchronous mode', 'select',
                options=('OFF', 'NORMAL', 'FULL', 'EXTRA'),
            ),
            _field(
                'auto_vacuum', 'Auto-vacuum mode', 'select',
                options=('NONE', 'FULL', 'INCREMENTAL'),
            ),
            _field(
                'page_size', 'Page size', 'select', options=(
                    '512', '1024', '2048', '4096', '8192', '16384',
                    '32768', '65536',
                ),
            ),
            _field('application_id', 'Application ID', 'number',
                   minimum=-2147483648, maximum=2147483647),
            _field('user_version', 'User version', 'number',
                   minimum=-2147483648, maximum=2147483647),
            _field(
                'run_vacuum', 'Rebuild the database with VACUUM',
                'boolean', default=False,
            ),
        ),
        'drop_fields': (
            _field(
                'confirmation',
                'Type the exact database path to confirm deletion',
                required=True, max_length=4096,
            ),
        ),
        'connection_fields': (
            _field('read_only', 'Open read-only', 'boolean', default=False),
            _field('uri_mode', 'URI open mode'),
            _field('journal_mode', 'Journal mode'),
            _field('busy_timeout', 'Busy timeout (milliseconds)', 'number',
                   default=5000, minimum=0),
        ),
    },
    'apache-ignite-native': {
        'engine_id': 'apache_ignite', 'noun': 'SQL catalog',
        'identity_label': 'Ignite catalog or schema',
        'connection_fields': (
            _field('schema', 'Default SQL schema'),
            _field('partition_aware', 'Partition-aware routing', 'boolean',
                   default=True),
        ),
        'native_create': False, 'native_alter': False, 'native_drop': False,
        'unsupported_reason': (
            'Ignite lifecycle administration is performed through caches '
            'and SQL schemas rather than independent server databases.'
        ),
    },
    'cockroachdb-native': {
        'engine_id': 'cockroachdb', 'noun': 'database',
        'identity_label': 'CockroachDB database name',
        'connection_fields': (
            _field('owner', 'Database owner'),
            _field('primary_region', 'Primary region'),
            _field('survival_goal', 'Survival goal'),
            _field('placement', 'Data placement'),
        ),
    },
    'dolt-native': {
        'engine_id': 'dolt', 'noun': 'versioned database',
        'identity_label': 'Dolt database/repository name',
        'connection_fields': (
            _field('branch', 'Initial branch'),
            _field('remote', 'Remote repository URL'),
            _field('read_only', 'Open read-only', 'boolean', default=False),
        ),
    },
    'foundationdb-native': {
        'engine_id': 'foundationdb', 'noun': 'cluster database',
        'identity_label': 'FoundationDB cluster database',
        'connection_fields': (
            _field('tenant', 'Tenant name'),
            _field('subspace_prefix', 'Subspace prefix'),
            _field('api_version', 'API version', 'number'),
        ),
        'native_create': False, 'native_alter': False, 'native_drop': False,
        'unsupported_reason': (
            'A FoundationDB cluster exposes one database; tenants and '
            'subspaces have their own provider administration forms.'
        ),
    },
    'immudb-native': {
        'engine_id': 'immudb', 'noun': 'immutable database',
        'identity_label': 'immudb database name',
        'connection_fields': (
            _field('replica', 'Open as replica', 'boolean', default=False),
            _field('settings', 'Database settings', 'json', default={}),
        ),
    },
    'tidb-native': {
        'engine_id': 'tidb', 'noun': 'database',
        'identity_label': 'TiDB database name',
        'connection_fields': (
            _field('charset', 'Default character set'),
            _field('collation', 'Default collation'),
            _field('placement_policy', 'Placement policy'),
        ),
    },
    'tikv-native': {
        'engine_id': 'tikv', 'noun': 'keyspace',
        'identity_label': 'TiKV keyspace name or ID',
        'lifecycle_kind': 'keyspace',
        'connection_fields': (
            _field('api_version', 'API version', 'number', default=1),
            _field('transaction_mode', 'Transaction mode'),
            _field('enable_ttl', 'Enable TTL operations', 'boolean',
                   default=False),
        ),
    },
    'vitess-native': {
        'engine_id': 'vitess', 'noun': 'keyspace',
        'identity_label': 'Vitess keyspace name',
        'lifecycle_kind': 'keyspace',
        'connection_fields': (
            _field('keyspace_type', 'Keyspace type', 'select',
                   default='NORMAL', options=('NORMAL', 'SNAPSHOT')),
            _field('durability_policy', 'Durability policy'),
            _field('sidecar_database', 'Sidecar database name'),
        ),
    },
    'yugabytedb-native': {
        'engine_id': 'yugabytedb', 'noun': 'YSQL database',
        'identity_label': 'YSQL database name',
        'connection_fields': (
            _field('owner', 'Database owner'),
            _field('template', 'Template database'),
            _field('encoding', 'Character encoding'),
            _field('connection_limit', 'Connection limit', 'number'),
        ),
    },
    'yugabytedb-ycql': {
        'engine_id': 'yugabytedb', 'noun': 'YCQL keyspace',
        'identity_label': 'YCQL keyspace name',
        'lifecycle_kind': 'keyspace',
        'connection_fields': (
            _field('replication', 'Replication strategy', 'json', default={}),
            _field(
                'durable_writes', 'Durable writes', 'boolean', default=True
            ),
            _field('consistency', 'Default consistency level'),
        ),
    },
}


def _operation(profile_id, operation_id, noun, fields, execution, *,
               supported=True, reason=None, destructive=False):
    return {
        'form_id': f'cdeadmin.{profile_id}.database.{operation_id}.v1',
        'operation_id': operation_id,
        'title': f'{operation_id.capitalize()} {noun}',
        'execution': execution,
        'supported': supported,
        'disabled_reason': None if supported else reason,
        'destructive': destructive,
        'fields': copy.deepcopy(list(fields)),
    }


def _database_contract(profile_id, spec):
    noun = spec['noun']
    identity_id = spec.get('create_identity', 'name')
    identity = _field(
        'database', spec['identity_label'],
        spec.get('identity_control', 'text'), required=True,
    )
    create_identity = _field(
        identity_id, spec['identity_label'],
        spec.get('identity_control', 'text'), required=True,
    )
    connection = tuple(spec.get('connection_fields', ()))
    native_create = spec.get('native_create', True)
    native_alter = spec.get('native_alter', True)
    native_drop = spec.get('native_drop', True)
    reason = spec.get('unsupported_reason')
    create_fields = spec.get(
        'create_fields', (create_identity, _DEFINITION, _OPTIONS)
    )
    return {
        'form_set_id': f'cdeadmin.{profile_id}.database.forms.v1',
        'profile_id': profile_id,
        'engine_id': spec['engine_id'],
        'noun': noun,
        'lifecycle_resource_kind': spec.get('lifecycle_kind', 'database'),
        'forms': {
            'define': _operation(
                profile_id, 'define', noun,
                (identity, _DISPLAY_NAME, *connection),
                'database_target_attach',
            ),
            'connect': _operation(
                profile_id, 'connect', noun, connection,
                'database_target_activate',
            ),
            'create': _operation(
                profile_id, 'create', noun,
                create_fields, 'visual_admin',
                supported=native_create, reason=reason,
            ),
            'edit': _operation(
                profile_id, 'edit', noun,
                (_DISPLAY_NAME, *connection), 'database_target_update',
            ),
            'alter': _operation(
                profile_id, 'alter', noun,
                spec.get(
                    'alter_fields', (_CHANGES, _DEFINITION, _ONLINE)
                ), 'visual_admin',
                supported=native_alter, reason=reason,
            ),
            'drop': _operation(
                profile_id, 'drop', noun,
                spec.get('drop_fields', (_CASCADE, _CONFIRM)),
                'visual_admin', supported=native_drop, reason=reason,
                destructive=True,
            ),
            'remove': _operation(
                profile_id, 'remove', noun, (_CONFIRM,),
                'database_target_delete', destructive=True,
            ),
        },
    }


def provider_form_contract(profile):
    """Build the unique form set for one exact active provider profile."""
    profile_id = profile.get('profile_id')
    spec = _DATABASE_SPECS.get(profile_id)
    if spec is None:
        raise ProviderFormContractError(
            f'provider {profile_id!r} has no database form contract'
        )
    if spec['engine_id'] != profile.get('engine_id'):
        raise ProviderFormContractError(
            f'provider {profile_id!r} database form engine is invalid'
        )
    route_kind = profile.get('route_kind')
    server_fields = [
        _field('name', 'Connection profile name', required=True),
    ]
    if route_kind == 'network':
        server_fields.extend((
            _field('host', 'Server host or address', required=True),
            _field('port', 'Server port', 'number', required=True,
                   minimum=1, maximum=65535),
            _field('username', 'User or principal'),
        ))
    declared_fields = copy.deepcopy(profile.get('connection_fields', []))
    declared_fields.extend({
        **copy.deepcopy(field),
        'control': 'password',
    } for field in profile.get('secret_fields', []))
    if profile.get('secret_fields'):
        declared_fields.append(_field(
            'save_password', 'Save default connection credentials',
            'boolean', default=False,
            help=(
                'Store the credentials for the default user in CDEadmin. '
                'Credentials entered while connecting as another user are '
                'never stored as the default credentials.'
            ),
        ))
    positions = {
        field['field_id']: index for index, field in enumerate(server_fields)
    }
    for field in declared_fields:
        position = positions.get(field['field_id'])
        if position is None:
            positions[field['field_id']] = len(server_fields)
            server_fields.append(field)
        else:
            # A provider declaration owns its label and validation metadata
            # when it refines a common host/port/principal field.
            server_fields[position] = field
    server_forms = {
        operation_id: {
            'form_id': (
                f'cdeadmin.{profile_id}.server.{operation_id}.v1'
            ),
            'operation_id': operation_id,
            'title': f'{operation_id.capitalize()} {profile["display_name"]} '
                     'server',
            'fields': copy.deepcopy(server_fields) if operation_id !=
            'remove' else [_field(
                'confirmation',
                'Type the connection profile name to confirm',
                required=True,
            )],
            'destructive': operation_id == 'remove',
        }
        for operation_id in ('define', 'edit', 'remove')
    }
    value = {
        'contract_version': '1.0',
        'profile_id': profile_id,
        'server': {
            'form_set_id': f'cdeadmin.{profile_id}.server.forms.v1',
            'forms': server_forms,
        },
        'database': _database_contract(profile_id, spec),
    }
    _validate_contract(value)
    return value


def assert_form_contract_coverage(profiles):
    """Reject active profiles lacking a unique exact form contract."""
    profile_ids = {profile['profile_id'] for profile in profiles}
    configured = set(_DATABASE_SPECS)
    if profile_ids != configured:
        missing = sorted(profile_ids - configured)
        stale = sorted(configured - profile_ids)
        raise ProviderFormContractError(
            'provider form coverage mismatch; '
            f'missing={missing}, stale={stale}'
        )
    contracts = [provider_form_contract(profile) for profile in profiles]
    form_ids = [
        form['form_id']
        for contract in contracts
        for scope in ('server', 'database')
        for form in contract[scope]['forms'].values()
    ]
    if len(form_ids) != len(set(form_ids)):
        raise ProviderFormContractError(
            'provider form IDs must be globally unique'
        )
    return True


def _validate_contract(contract):
    required = {
        'server': {'define', 'edit', 'remove'},
        'database': {
            'define', 'connect', 'create', 'edit', 'alter', 'drop', 'remove',
        },
    }
    for scope, operations in required.items():
        forms = contract[scope].get('forms')
        if not isinstance(forms, dict) or set(forms) != operations:
            raise ProviderFormContractError(
                f'provider {scope} form operations are incomplete'
            )
        for operation_id, form in forms.items():
            if form.get('operation_id') != operation_id:
                raise ProviderFormContractError(
                    f'provider {scope} form operation identity is invalid'
                )
            fields = form.get('fields')
            field_ids = [field.get('field_id') for field in fields or ()]
            if not isinstance(fields, list) or any(not value for value in
                                                   field_ids):
                raise ProviderFormContractError(
                    f'provider {scope} form fields are invalid'
                )
            if len(field_ids) != len(set(field_ids)):
                raise ProviderFormContractError(
                    f'provider {contract["profile_id"]} {scope} form '
                    f'fields are duplicated: {field_ids}'
                )


__all__ = (
    'ProviderFormContractError', 'assert_form_contract_coverage',
    'provider_form_contract',
)
