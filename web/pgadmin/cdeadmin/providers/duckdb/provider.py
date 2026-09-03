"""DuckDB embedded-helper semantic provider pilot."""

import re

from pgadmin.cdeadmin.sdk import (
    ActualEnginePilotProvider,
    PilotProfile,
    RelationalClientConfig,
    RelationalClientError,
    RelationalDBAPIClient,
)
from ..relational_admin import (
    RelationalAdministration,
    RelationalAdminDialect,
)
from ..embedded_route import duckdb_arguments


PROFILE = PilotProfile(
    'org.cdeadmin.duckdb', 'duckdb-native', 'duckdb', 'DuckDB', '1.5.2',
    'embedded_duckdb', 'relational', 'duckdb-sql', 'DuckDB SQL',
    'duckdb-native-transaction', 'columnar',
    ('database', 'attached-database', 'schema', 'table', 'column', 'view',
     'index', 'constraint', 'sequence', 'type', 'macro', 'function',
     'secret', 'extension'),
    ('duckdb-shell', 'export', 'import', 'extension-manager'),
    ('embedded_runtime', 'filesystem'),
    semantic_sql_dialect={
        'language_profile': 'duckdb-sql', 'quote_open': '"',
        'supports_rollup': True,
    },
)


ADMINISTRATION = RelationalAdministration(RelationalAdminDialect(
    engine_id='duckdb',
    embedded_database=True,
    database_create_mode='embedded-file',
    database_extension='.duckdb',
    supported={
        'database': frozenset({'inspect', 'create'}),
        'attached-database': frozenset({'inspect'}),
        'schema': frozenset({
            'inspect', 'create', 'rename', 'drop',
        }),
        'table': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
            'insert', 'update', 'delete',
        }),
        'view': frozenset({'inspect', 'create', 'drop'}),
        'column': frozenset({'inspect', 'create', 'rename', 'drop'}),
        'constraint': frozenset({'inspect', 'create', 'drop'}),
        'index': frozenset({'inspect', 'create', 'drop'}),
        'sequence': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
        }),
        'type': frozenset({'inspect', 'create', 'drop'}),
        'macro': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'function': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'secret': frozenset({'inspect', 'create', 'drop'}),
        'extension': frozenset({'inspect', 'execute'}),
    },
))


class DuckDBPilotProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def _route_arguments(route):
    return duckdb_arguments(route)


def _resources(connection, request):
    cursor = connection.cursor()
    try:
        generation = str(request.get('capability_generation') or 'current')
        resources = {}

        def add(kind, path, name, native=None):
            path = [str(item) for item in path]
            name = str(name)
            resource_id = ':'.join([kind, *path, name])
            resources[resource_id] = {
                'resource_id': resource_id,
                'resource_kind': kind,
                'display_name': name,
                'display_path': [*path, name],
                'authority_path': [*path, kind, name],
                'generation': generation,
            }
            if native:
                resources[resource_id]['native'] = native

        cursor.execute(
            'SELECT database_name, path FROM duckdb_databases() '
            'WHERE NOT internal ORDER BY database_name'
        )
        databases = cursor.fetchall()
        for index, (database, path) in enumerate(databases):
            kind = 'database' if index == 0 else 'attached-database'
            add(kind, [], database, {'path': path})
        cursor.execute(
            'SELECT s.database_name, s.schema_name FROM duckdb_schemas() s '
            'JOIN duckdb_databases() d USING (database_name) '
            'WHERE NOT d.internal ORDER BY 1, 2'
        )
        for database, schema in cursor.fetchall():
            add('schema', [database], schema)
        queries = (
            ('table', 'SELECT database_name, schema_name, table_name, '
             'estimated_size FROM duckdb_tables() WHERE NOT internal '
             'ORDER BY 1, 2, 3'),
            ('view', 'SELECT database_name, schema_name, view_name, sql '
             'FROM duckdb_views() WHERE NOT internal ORDER BY 1, 2, 3'),
            ('column', 'SELECT database_name, schema_name, table_name, '
             'column_name, data_type FROM duckdb_columns() '
             'WHERE NOT internal ORDER BY 1, 2, 3, column_index'),
            ('index', 'SELECT database_name, schema_name, table_name, '
             'index_name, sql FROM duckdb_indexes() ORDER BY 1, 2, 3, 4'),
            ('constraint', 'SELECT database_name, schema_name, table_name, '
             'constraint_name, constraint_type FROM duckdb_constraints() '
             'ORDER BY 1, 2, 3, constraint_index'),
            ('sequence', 'SELECT database_name, schema_name, sequence_name, '
             'sql FROM duckdb_sequences() ORDER BY 1, 2, 3'),
            ('type', 'SELECT database_name, schema_name, type_name, '
             'logical_type FROM duckdb_types() WHERE NOT internal '
             'ORDER BY 1, 2, 3'),
        )
        for kind, source in queries:
            cursor.execute(source)
            for row in cursor.fetchall():
                database, schema, *values = row
                if kind in {'column', 'index', 'constraint'}:
                    parent, name, detail = values
                    path = [database, schema, parent]
                else:
                    name, detail = values
                    path = [database, schema]
                add(kind, path, name, {'definition': detail})
        cursor.execute(
            'SELECT database_name, schema_name, function_name, '
            'function_type, macro_definition FROM duckdb_functions() '
            'WHERE NOT internal ORDER BY 1, 2, 3'
        )
        for database, schema, name, function_type, definition in (
            cursor.fetchall()
        ):
            kind = 'macro' if 'macro' in str(function_type) else 'function'
            add(kind, [database, schema], name, {
                'function_type': function_type, 'definition': definition,
            })
        cursor.execute(
            'SELECT extension_name, loaded, installed, extension_version '
            'FROM duckdb_extensions() ORDER BY extension_name'
        )
        for name, loaded, installed, version in cursor.fetchall():
            add('extension', [], name, {
                'loaded': loaded, 'installed': installed, 'version': version,
            })
        cursor.execute(
            'SELECT name, type, provider, persistent, storage, scope '
            'FROM duckdb_secrets() ORDER BY name'
        )
        for name, kind, provider, persistent, storage, scope in (
            cursor.fetchall()
        ):
            add('secret', [], name, {
                'type': kind, 'provider': provider,
                'persistent': persistent, 'storage': storage,
                'scope': scope,
            })
        return list(resources.values())
    finally:
        cursor.close()


def _security(connection, request):
    cursor = connection.cursor()
    try:
        cursor.execute('SELECT current_user')
        current_user = str(cursor.fetchone()[0])
        return {
            'resource_id': f'authorization:{current_user}',
            'display_name': current_user,
            'authority_path': ['authorization', current_user],
            'generation': str(
                request.get('capability_generation') or 'current'
            ),
            'native': {'current_user': current_user},
        }
    finally:
        cursor.close()


def _version(row):
    value = str(row[0]).strip() if row else ''
    match = re.search(r'(\d+\.\d+\.\d+)', value)
    if match is None:
        raise RelationalClientError('DuckDB profile version is unavailable')
    return match.group(1)


def _create_client():
    return RelationalDBAPIClient(RelationalClientConfig(
        profile=PROFILE,
        module_name='duckdb',
        version_query='SELECT version()',
        version_parser=_version,
        connect_arguments=_route_arguments,
        metadata_reader=_resources,
        security_reader=_security,
        administration=ADMINISTRATION,
    ))


def create_provider(context, permissions, client=None):
    return DuckDBPilotProvider(
        context, permissions, client or _create_client()
    )
