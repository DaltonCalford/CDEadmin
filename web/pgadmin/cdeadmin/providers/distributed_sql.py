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

from typing import Any, Callable, Mapping

from pgadmin.cdeadmin.sdk import (
    RelationalClientConfig,
    RelationalDBAPIClient,
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
        return []


def postgresql_route(route):
    allowed = {
        'host', 'port', 'user', 'dbname', 'connect_timeout', 'sslmode',
        'sslrootcert', 'sslcert', 'sslkey', 'application_name',
    }
    result = {key: value for key, value in route.items() if key in allowed}
    if 'database' in route and 'dbname' not in result:
        result['dbname'] = route['database']
    return result


def mysql_route(route):
    allowed = {
        'host', 'port', 'user', 'database', 'unix_socket',
        'connection_timeout', 'ssl_ca', 'ssl_cert', 'ssl_key',
    }
    return {key: value for key, value in route.items() if key in allowed}


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
        relations = optional_rows(
            cursor,
            'SELECT table_schema, table_name, table_type '
            'FROM information_schema.tables ORDER BY 1, 2',
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
            'is_nullable FROM information_schema.columns ORDER BY 1, 2, 3',
        )
        for schema, table, name, data_type, nullable in columns:
            item = resource('column', [schema, table], name, generation, {
                'data_type': str(data_type), 'is_nullable': str(nullable),
            })
            values[item['resource_id']] = item
        indexes = optional_rows(
            cursor,
            'SELECT schemaname, tablename, indexname, indexdef '
            'FROM pg_catalog.pg_indexes ORDER BY 1, 2, 3',
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
):
    """Create a DB-API adapter from one provider-owned distributed spec."""
    if wire == 'postgresql':
        module_name = 'psycopg'
        route_builder = postgresql_route
        security_reader = postgresql_security
    elif wire == 'mysql':
        module_name = 'mysql.connector'
        route_builder = mysql_route
        security_reader = mysql_security
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
        credential_argument='password',
        secret_acquirer=permissions.acquire_secret,
        administration=administration,
    )
    return client_class(config, **dict(client_options or {}))


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
