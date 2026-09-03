##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Wire-compatible foundations for distributed SQL providers.

This module shares transport mechanics, not engine identity or semantics.
Every provider supplies its own version parser, profile, metadata extensions,
and administration policy. A ScratchBird-compatible listener follows exactly
the same path as the corresponding reference engine.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Mapping

from pgadmin.cdeadmin.sdk import (
    RelationalClientConfig,
    RelationalClientError,
    RelationalDBAPIClient,
    load_optional_module,
)
from .relational_admin import (
    RelationalAdministration,
    RelationalAdminDialect,
)


def resource(kind, path, name, generation, native=None):
    """Build one provider-native resource before common normalization."""
    parts = [str(item) for item in path]
    item = {
        'resource_id': ':'.join([kind, *parts, str(name)]),
        'resource_kind': kind,
        'display_name': str(name),
        'display_path': [*parts, str(name)],
        'authority_path': [*parts, kind, str(name)],
        'generation': generation,
    }
    if native:
        item['native'] = dict(native)
    return item


def optional_rows(cursor, source, parameters=()):
    """Read an optional catalog surface without widening its permissions."""
    try:
        cursor.execute(source, parameters)
        return list(cursor.fetchall())
    except Exception:
        try:
            cursor.connection.rollback()
        except Exception:
            pass
        return []


def postgresql_route(route):
    allowed = {
        'host', 'port', 'user', 'dbname', 'connect_timeout', 'sslmode',
        'hostaddr', 'client_encoding', 'options', 'application_name',
        'fallback_application_name', 'keepalives', 'keepalives_idle',
        'keepalives_interval', 'keepalives_count', 'tcp_user_timeout',
        'replication', 'channel_binding', 'gssencmode', 'sslcompression',
        'sslcert', 'sslkey',
        'sslrootcert', 'sslcrl', 'sslcrldir', 'sslsni', 'requirepeer',
        'ssl_min_protocol_version', 'ssl_max_protocol_version',
        'krbsrvname', 'gsslib', 'target_session_attrs',
        'load_balance_hosts', 'gssdelegation', 'require_auth',
        'sslnegotiation', 'sslkeylogfile', 'min_protocol_version',
        'max_protocol_version', 'oauth_issuer', 'oauth_client_id',
        'oauth_scope', 'pool_enabled', 'pool_min_size', 'pool_max_size',
        'pool_acquisition_timeout', 'pool_max_waiting',
        'pool_max_lifetime', 'pool_max_idle', 'pool_reconnect_timeout',
        'pool_workers', 'pool_check_connection',
    }
    result = {key: value for key, value in route.items() if key in allowed}
    if 'database' in route and 'dbname' not in result:
        result['dbname'] = route['database']
    if route.get('route_id'):
        result['cde_route_id'] = str(route['route_id'])
    return result


class _PooledConnectionLease:
    """Return a checked-out psycopg connection exactly once."""

    def __init__(self, pool, connection):
        object.__setattr__(self, '_pool', pool)
        object.__setattr__(self, '_connection', connection)
        object.__setattr__(self, '_closed', False)

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def __setattr__(self, name, value):
        if name.startswith('_'):
            object.__setattr__(self, name, value)
        else:
            setattr(self._connection, name, value)

    def close(self):
        if self._closed:
            return
        self._pool.putconn(self._connection)
        object.__setattr__(self, '_closed', True)


class _PsycopgPoolConnector:
    """Adapt psycopg_pool to the synchronous DB-API connector boundary."""

    _POOL_ARGUMENTS = frozenset({
        'pool_enabled', 'pool_min_size', 'pool_max_size',
        'pool_acquisition_timeout', 'pool_max_waiting',
        'pool_max_lifetime', 'pool_max_idle', 'pool_reconnect_timeout',
        'pool_workers', 'pool_check_connection',
        'cde_route_id',
    })

    def __init__(self, module, pool_namespace, pool_module=None):
        self.module = module
        self.pool_namespace = str(pool_namespace)
        self.pool_module = pool_module
        self._pools = {}

    def __call__(self, *args, **kwargs):
        if not kwargs.get('pool_enabled'):
            options = dict(kwargs)
            for key in self._POOL_ARGUMENTS:
                options.pop(key, None)
            return self.module.connect(*args, **options)
        if args:
            raise RelationalClientError(
                'pooled PostgreSQL-wire connections require named arguments'
            )
        options = dict(kwargs)
        pool_options = {
            key: options.pop(key)
            for key in tuple(options)
            if key in self._POOL_ARGUMENTS
        }
        pool_options.pop('pool_enabled', None)
        route_id = pool_options.pop('cde_route_id', None)
        key = str(route_id or (
            str(options.get('host') or '') + ':' +
            str(options.get('port') or '') + '/' +
            str(options.get('dbname') or '') + ':' +
            str(options.get('user') or '')
        ))
        pool_key = self.pool_namespace + ':' + key
        pool = self._pools.get(pool_key)
        if pool is None:
            namespace = self.pool_module or load_optional_module(
                'psycopg_pool'
            )
            pool_type = getattr(namespace, 'ConnectionPool', None)
            if not callable(pool_type):
                raise RelationalClientError(
                    'psycopg_pool has no synchronous ConnectionPool'
                )
            check = None
            if pool_options.pop('pool_check_connection', True):
                check = getattr(pool_type, 'check_connection', None)
            pool = pool_type(
                kwargs=options,
                min_size=pool_options.pop('pool_min_size', 1),
                max_size=pool_options.pop('pool_max_size', 10),
                timeout=pool_options.pop('pool_acquisition_timeout', 30),
                max_waiting=pool_options.pop('pool_max_waiting', 0),
                max_lifetime=pool_options.pop('pool_max_lifetime', 3600),
                max_idle=pool_options.pop('pool_max_idle', 600),
                reconnect_timeout=pool_options.pop(
                    'pool_reconnect_timeout', 300
                ),
                num_workers=pool_options.pop('pool_workers', 3),
                check=check,
                open=True,
            )
            self._pools[pool_key] = pool
        return _PooledConnectionLease(pool, pool.getconn())

    def close(self):
        for pool in tuple(self._pools.values()):
            close = getattr(pool, 'close', None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self._pools.clear()


class PsycopgPoolDBAPIClient(RelationalDBAPIClient):
    """PostgreSQL-wire client with optional native psycopg pooling."""

    def __init__(self, config, pool_namespace, module=None, pool_module=None):
        super().__init__(config, module)
        self._pool_connector = _PsycopgPoolConnector(
            self.module, pool_namespace, pool_module
        )
        self._connector = self._pool_connector

    def close(self):
        super().close()
        self._pool_connector.close()


def _initialize_postgresql_connection(connection, route):
    connection.set_autocommit(route.get('autocommit', False) is True)
    isolation = route.get('transaction_isolation', 'server')
    if isolation != 'server':
        module = load_optional_module('psycopg')
        try:
            value = module.IsolationLevel[isolation]
        except KeyError as exc:
            raise RelationalClientError(
                'PostgreSQL-wire transaction isolation is invalid'
            ) from exc
        connection.set_isolation_level(value)
    read_mode = route.get('transaction_read_only', 'server')
    if read_mode not in {'server', 'read-only', 'read-write'}:
        raise RelationalClientError(
            'PostgreSQL-wire transaction read mode is invalid'
        )
    if read_mode != 'server':
        connection.set_read_only(read_mode == 'read-only')
    deferrable = route.get('transaction_deferrable', 'server')
    if deferrable not in {'server', 'deferrable', 'not-deferrable'}:
        raise RelationalClientError(
            'PostgreSQL-wire transaction deferrability is invalid'
        )
    if deferrable != 'server':
        connection.set_deferrable(deferrable == 'deferrable')


def _initialize_mysql_connection(connection, route):
    """Apply session-only MySQL-wire defaults after authentication.

    Transaction isolation is deliberately not interpolated from arbitrary
    text.  The finite allowlist is shared by Dolt, TiDB and Vitess because
    they expose the same admitted MySQL wire statement.
    """
    isolation = route.get('transaction_isolation')
    if isolation is None:
        return
    allowed = {
        'READ UNCOMMITTED', 'READ COMMITTED', 'REPEATABLE READ',
        'SERIALIZABLE',
    }
    if isolation not in allowed:
        raise RelationalClientError(
            'MySQL-wire transaction isolation is invalid'
        )
    cursor = connection.cursor()
    try:
        cursor.execute(
            f'SET SESSION TRANSACTION ISOLATION LEVEL {isolation}'
        )
    finally:
        cursor.close()


def mysql_route(route):
    allowed = {
        'host', 'port', 'user', 'database', 'unix_socket',
        'connection_timeout', 'read_timeout', 'write_timeout',
        'ssl_ca', 'ssl_cert', 'ssl_key', 'ssl_verify_cert',
        'ssl_verify_identity', 'ssl_cipher', 'tls_ciphersuites',
        'ssl_disabled', 'tls_versions', 'compress', 'force_ipv6',
        'auth_plugin', 'krb_service_principal', 'kerberos_auth_mode',
        'oci_config_file', 'oci_config_profile', 'openid_token_file',
        'dns_srv', 'charset', 'collation', 'autocommit', 'time_zone',
        'sql_mode', 'init_command', 'conn_attrs', 'pool_name', 'pool_size',
        'pool_reset_session', 'failover',
    }
    result = {key: value for key, value in route.items() if key in allowed}
    if result.get('auth_plugin') == 'auto':
        result.pop('auth_plugin')
    if result.get('pool_size') and not result.get('pool_name'):
        route_id = str(route.get('route_id') or 'unscoped')
        result['pool_name'] = 'cde_' + hashlib.sha256(
            route_id.encode('utf-8')
        ).hexdigest()[:24]
    return result


def postgresql_catalog(connection, request, engine_name, extras=None):
    """Read portable SQL catalogs plus provider-selected topology extras."""
    generation = str(request.get('capability_generation') or 'current')
    values = {resource('cluster', [], engine_name, generation)['resource_id']:
              resource('cluster', [], engine_name, generation)}
    cursor = connection.cursor()
    try:
        databases = optional_rows(
            cursor,
            'SELECT datname FROM pg_catalog.pg_database '
            'WHERE datallowconn ORDER BY datname',
        )
        for row in databases:
            item = resource('database', [], row[0], generation)
            values[item['resource_id']] = item
        schemas = optional_rows(
            cursor,
            'SELECT schema_name FROM information_schema.schemata '
            'WHERE schema_name NOT IN '
            "('pg_catalog', 'information_schema', 'crdb_internal') "
            'ORDER BY 1',
        )
        for row in schemas:
            item = resource('schema', [], row[0], generation)
            values[item['resource_id']] = item
        relations = optional_rows(
            cursor,
            'SELECT table_schema, table_name, table_type '
            'FROM information_schema.tables WHERE table_schema NOT IN '
            "('pg_catalog', 'information_schema', 'crdb_internal') "
            'ORDER BY 1, 2',
        )
        for schema, name, table_type in relations:
            schema_item = resource('schema', [], schema, generation)
            values[schema_item['resource_id']] = schema_item
            kind = 'view' if 'VIEW' in str(table_type).upper() else 'table'
            item = resource(
                kind, [schema], name, generation,
                {'table_type': str(table_type)},
            )
            values[item['resource_id']] = item
        columns = optional_rows(
            cursor,
            'SELECT table_schema, table_name, column_name, data_type, '
            'is_nullable FROM information_schema.columns '
            'WHERE table_schema NOT IN '
            "('pg_catalog', 'information_schema', 'crdb_internal') "
            'ORDER BY 1, 2, 3',
        )
        for schema, table, name, data_type, nullable in columns:
            item = resource('column', [schema, table], name, generation, {
                'data_type': str(data_type), 'is_nullable': str(nullable),
            })
            values[item['resource_id']] = item
        indexes = optional_rows(
            cursor,
            'SELECT schemaname, tablename, indexname, indexdef '
            'FROM pg_catalog.pg_indexes WHERE schemaname NOT IN '
            "('pg_catalog', 'information_schema', 'crdb_internal') "
            'ORDER BY 1, 2, 3',
        )
        for schema, table, name, definition in indexes:
            item = resource('index', [schema, table], name, generation, {
                'definition': str(definition),
            })
            values[item['resource_id']] = item
        if extras:
            for item in extras(cursor, request, generation):
                values[item['resource_id']] = item
        return list(values.values())
    finally:
        cursor.close()


def mysql_catalog(connection, request, engine_name, extras=None):
    """Read MySQL-wire catalogs plus provider-selected topology extras."""
    generation = str(request.get('capability_generation') or 'current')
    cluster = resource('cluster', [], engine_name, generation)
    values = {cluster['resource_id']: cluster}
    cursor = connection.cursor()
    try:
        rows = optional_rows(
            cursor,
            'SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE '
            'FROM information_schema.TABLES ORDER BY 1, 2',
        )
        for database, name, table_type in rows:
            database_item = resource('database', [], database, generation)
            values[database_item['resource_id']] = database_item
            kind = 'view' if 'VIEW' in str(table_type).upper() else 'table'
            item = resource(kind, [database], name, generation, {
                'table_type': str(table_type),
            })
            values[item['resource_id']] = item
        rows = optional_rows(
            cursor,
            'SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, '
            'IS_NULLABLE FROM information_schema.COLUMNS ORDER BY 1, 2, 3',
        )
        for database, table, name, data_type, nullable in rows:
            item = resource('column', [database, table], name, generation, {
                'data_type': str(data_type), 'is_nullable': str(nullable),
            })
            values[item['resource_id']] = item
        rows = optional_rows(
            cursor,
            'SELECT DISTINCT TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, '
            'NON_UNIQUE, INDEX_TYPE FROM information_schema.STATISTICS '
            'ORDER BY 1, 2, 3',
        )
        for database, table, name, non_unique, index_type in rows:
            item = resource('index', [database, table], name, generation, {
                'unique': not bool(non_unique),
                'index_type': str(index_type),
            })
            values[item['resource_id']] = item
        if extras:
            for item in extras(cursor, request, generation):
                values[item['resource_id']] = item
        return list(values.values())
    finally:
        cursor.close()


def postgresql_security(connection, request):
    cursor = connection.cursor()
    try:
        cursor.execute('SELECT current_user, session_user')
        current, session = cursor.fetchone()
        generation = str(request.get('capability_generation') or 'current')
        return resource('role', [], current, generation, {
            'current_user': str(current), 'session_user': str(session),
            'authorization_model': 'postgresql-wire-native',
        })
    finally:
        cursor.close()


def mysql_security(connection, request):
    cursor = connection.cursor()
    try:
        cursor.execute('SELECT CURRENT_USER(), USER()')
        current, session = cursor.fetchone()
        generation = str(request.get('capability_generation') or 'current')
        return resource('user', [], current, generation, {
            'current_user': str(current), 'session_user': str(session),
            'authorization_model': 'mysql-wire-native',
        })
    finally:
        cursor.close()


def create_sql_client(
    profile,
    permissions,
    *,
    wire,
    version_query,
    version_parser,
    metadata_reader: Callable[[object, Mapping[str, Any]], list[dict]],
    administration,
    client_class=RelationalDBAPIClient,
    client_options=None,
    security_reader_override=None,
    pool_namespace=None,
):
    """Create a DB-API adapter from one provider-owned distributed spec."""
    if wire == 'postgresql':
        module_name = 'psycopg'
        route_builder = postgresql_route
        security_reader = postgresql_security
        credential_arguments = {
            'database_password': 'password',
            'tls_private_key_password': 'sslpassword',
            'oauth_client_secret': 'oauth_client_secret',
        }
    elif wire == 'mysql':
        module_name = 'mysql.connector'
        route_builder = mysql_route
        security_reader = mysql_security
        credential_arguments = {
            'database_password': 'password',
            'database_password_2': 'password2',
            'database_password_3': 'password3',
        }
    else:
        raise ValueError('distributed SQL wire must be provider-selected')
    config = RelationalClientConfig(
        profile=profile,
        module_name=module_name,
        version_query=version_query,
        version_parser=version_parser,
        connect_arguments=route_builder,
        metadata_reader=metadata_reader,
        security_reader=security_reader_override or security_reader,
        credential_arguments=credential_arguments,
        secret_acquirer=permissions.acquire_secret,
        connection_initializer=(
            _initialize_postgresql_connection
            if wire == 'postgresql' else _initialize_mysql_connection
        ),
        administration=administration,
    )
    options = dict(client_options or {})
    if wire == 'postgresql' and client_class is RelationalDBAPIClient:
        if pool_namespace is None:
            raise ValueError(
                'PostgreSQL-wire client requires a pool namespace'
            )
        return PsycopgPoolDBAPIClient(
            config, pool_namespace, **options
        )
    return client_class(config, **options)


def sql_administration(
    engine_id, wire, native_read_only=(), provider_read_only=(),
):
    """Construct the safe common SQL admin subset for one provider."""
    namespace = frozenset({'inspect', 'create', 'alter', 'drop'})
    definition = frozenset({'inspect', 'create', 'drop'})
    supported = {
        'cluster': frozenset({'inspect'}),
        'database': namespace,
        'schema': namespace,
        'table': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
            'insert', 'update', 'delete',
        }),
        'column': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
        }),
        'view': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'index': definition,
        'constraint': definition,
        'sequence': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
        }),
        'user': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'role': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'privilege': frozenset({'inspect', 'grant', 'revoke'}),
    }
    for kind in native_read_only:
        supported[kind] = frozenset({'inspect'})
    for kind in provider_read_only:
        supported[kind] = frozenset({'inspect'})
    mysql = wire == 'mysql'
    return RelationalAdministration(RelationalAdminDialect(
        engine_id=engine_id,
        quote_open='`' if mysql else '"',
        quote_close='`' if mysql else '"',
        parameter='%s',
        supported=supported,
        syntax_family=wire,
    ))
