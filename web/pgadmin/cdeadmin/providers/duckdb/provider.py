"""DuckDB embedded-helper semantic provider pilot."""

import os
import re
import stat
from collections.abc import Mapping

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
from ..embedded_route import contained_database


PROFILE = PilotProfile(
    'org.cdeadmin.duckdb', 'duckdb-native', 'duckdb', 'DuckDB', '1.5.2',
    'embedded_duckdb', 'relational', 'duckdb-sql', 'DuckDB SQL',
    'duckdb-native-transaction', 'columnar',
    ('database', 'attached-database', 'schema', 'table', 'column', 'view',
     'index', 'constraint', 'sequence', 'type', 'macro', 'function',
     'secret', 'extension', 'materialization'),
    ('duckdb-shell', 'export', 'import', 'extension-manager'),
    ('embedded_runtime', 'filesystem'),
    result_renderer_kind='columnar',
    result_renderer_id='cdeadmin.result.columnar.grid',
    result_component_reference='cdeadmin/results/ColumnarView',
    result_export_formats=('csv', 'json', 'jsonl'),
    result_worker_required=True,
    semantic_sql_dialect={
        'contract_complete': True,
        'language_profile': 'duckdb-sql', 'quote_open': '"',
        'quote_close': '"', 'supports_rollup': True,
        'rollup_style': 'function', 'limit_style': 'limit',
        'true_literal': 'TRUE', 'false_literal': 'FALSE',
        'time_operations': (
            'as_of', 'range', 'period_to_date', 'period_comparison',
        ),
        'window_operations': (
            'running_sum', 'moving_sum', 'moving_average', 'lag', 'delta',
            'percent_change', 'rank', 'dense_rank',
        ),
    },
    semantic_materialization_kind='materialization',
    dialect_contract_id='duckdb.dialect.1.5.2.v1',
    dialect_evidence=(
        'duckdb-1.5.2-source-and-runtime-inventory',
        'duckdb-1.5.2-task-live-execution',
    ),
    dialect_contract_file='duckdb_dialect_1_5_2.json',
    metrics_contract_file='duckdb_metrics_1_5_2.json',
    query_plan_templates=(
        ('DuckDB physical query plan', 'EXPLAIN {source}'),
        ('DuckDB profiled query plan', 'EXPLAIN ANALYZE {source}'),
    ),
)


ADMINISTRATION = RelationalAdministration(RelationalAdminDialect(
    engine_id='duckdb',
    embedded_database=True,
    database_create_mode='embedded-file',
    database_extension='.duckdb',
    not_applicable_concepts=frozenset({
        'servers', 'materialized_views', 'domains', 'procedures',
        'triggers', 'roles_and_grants', 'partitions',
        'tablespaces_and_filespaces', 'replication_objects',
        'jobs_and_events',
    }),
    additional_concept_declarations={
        'columnar': {
            'native_relations': {
                'status': 'supported',
                'resource_kinds': ['table', 'view'],
                'reason': (
                    'DuckDB relations use the provider columnar object '
                    'editor and structured relational administration.'
                ),
                'evidence': ['provider-dialect:duckdb'],
            },
            'projections': 'not_applicable',
            'dictionaries': 'not_applicable',
            'data_skipping_indexes': 'not_applicable',
            'partitions': 'not_applicable',
        },
        'semantic': {
            concept_id: {
                'status': 'supported',
                'resource_kinds': (
                    ['materialization']
                    if concept_id == 'materializations' else []
                ),
                'external_surface': 'cdeadmin.semantic-model-workspace.v1',
                'reason': (
                    'The provider compiles this semantic-model concept to '
                    'DuckDB SQL through the semantic workspace.'
                ),
                'evidence': ['provider-semantic-compiler:duckdb'],
            }
            for concept_id in (
                'cubes', 'dimensions', 'hierarchies', 'levels', 'measures',
                'materializations',
            )
        },
    },
    supported={
        'database': frozenset({
            'inspect', 'create', 'drop', 'checkpoint',
            'force_checkpoint', 'vacuum', 'analyze',
            'export_database', 'import_database',
        }),
        'attached-database': frozenset({'inspect'}),
        'schema': frozenset({
            'inspect', 'create', 'drop',
        }),
        'table': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
            'insert', 'update', 'delete',
        }),
        'view': frozenset({'inspect', 'create', 'drop'}),
        'column': frozenset({'inspect', 'create', 'rename', 'drop'}),
        'constraint': frozenset({'inspect'}),
        'index': frozenset({'inspect', 'create', 'drop'}),
        'sequence': frozenset({'inspect', 'create', 'drop'}),
        'type': frozenset({'inspect', 'create', 'drop'}),
        'macro': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'function': frozenset({'inspect'}),
        'secret': frozenset({'inspect', 'create', 'drop'}),
        'extension': frozenset({'inspect', 'execute'}),
        'materialization': frozenset({'create'}),
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
            return resources[resource_id]

        cursor.execute(
            'SELECT database_name, database_size, block_size, total_blocks, '
            'used_blocks, free_blocks, wal_size, memory_usage, memory_limit '
            'FROM pragma_database_size()'
        )
        sizes = {
            row[0]: {
                'database_size': row[1], 'block_size': row[2],
                'total_blocks': row[3], 'used_blocks': row[4],
                'free_blocks': row[5], 'wal_size': row[6],
                'memory_usage': row[7], 'memory_limit': row[8],
            }
            for row in cursor.fetchall()
        }
        cursor.execute('PRAGMA platform')
        platform = cursor.fetchone()[0]
        cursor.execute('SELECT version()')
        runtime_version = cursor.fetchone()[0]
        cursor.execute('SELECT current_database()')
        current_database = cursor.fetchone()[0]
        cursor.execute(
            'SELECT database_name, database_oid, path, comment, tags, type, '
            'readonly, encrypted, cipher, options FROM duckdb_databases() '
            'WHERE NOT internal ORDER BY database_name'
        )
        databases = cursor.fetchall()
        for row in databases:
            (
                database, database_oid, path, comment, tags, database_type,
                read_only, encrypted, cipher, options,
            ) = row
            kind = (
                'database' if database == current_database
                else 'attached-database'
            )
            native = {
                'database_name': database,
                'path': path, 'database_oid': database_oid,
                'comment': comment, 'tags': tags,
                'database_type': database_type,
                'read_only': bool(read_only), 'encrypted': bool(encrypted),
                'cipher': cipher, 'options': options,
                'runtime_version': runtime_version, 'platform': platform,
                **sizes.get(database, {}),
            }
            if isinstance(path, str) and os.path.isfile(path):
                native['file_bytes'] = os.path.getsize(path)
            add(kind, [], database, native)
        cursor.execute(
            'SELECT s.database_name, s.schema_name, s.oid, s.comment, '
            's.tags, s.sql FROM duckdb_schemas() s '
            'JOIN duckdb_databases() d USING (database_name) '
            'WHERE NOT d.internal ORDER BY 1, 2'
        )
        for database, schema, oid, comment, tags, sql in cursor.fetchall():
            native = {
                'catalog_metadata': {
                    'oid': oid, 'comment': comment, 'tags': tags,
                },
            }
            if sql:
                native['ddl'] = sql
            add('schema', [database], schema, native)

        cursor.execute(
            'SELECT database_name, schema_name, table_name, table_oid, '
            'comment, tags, temporary, has_primary_key, estimated_size, '
            'column_count, index_count, check_constraint_count, sql '
            'FROM duckdb_tables() WHERE NOT internal ORDER BY 1, 2, 3'
        )
        for row in cursor.fetchall():
            (
                database, schema, name, oid, comment, tags, temporary,
                primary_key, estimated_size, column_count, index_count,
                check_count, sql,
            ) = row
            add('table', [database, schema], name, {
                'catalog_metadata': {
                    'oid': oid, 'comment': comment, 'tags': tags,
                    'temporary': bool(temporary),
                    'has_primary_key': bool(primary_key),
                },
                'statistics': {
                    'estimated_rows': estimated_size,
                    'column_count': column_count,
                    'index_count': index_count,
                    'check_constraint_count': check_count,
                },
                'ddl': sql,
                'columns': [],
                'constraints': [],
                'indexes': [],
            })

        cursor.execute(
            'SELECT database_name, schema_name, view_name, view_oid, '
            'comment, tags, temporary, column_count, sql, is_bound '
            'FROM duckdb_views() WHERE NOT internal ORDER BY 1, 2, 3'
        )
        for row in cursor.fetchall():
            (
                database, schema, name, oid, comment, tags, temporary,
                column_count, sql, is_bound,
            ) = row
            add('view', [database, schema], name, {
                'catalog_metadata': {
                    'oid': oid, 'comment': comment, 'tags': tags,
                    'temporary': bool(temporary),
                },
                'state': {'bound': bool(is_bound)},
                'statistics': {'column_count': column_count},
                'ddl': sql,
                'columns': [],
            })

        cursor.execute(
            'SELECT database_name, schema_name, table_name, column_name, '
            'column_index, comment, column_default, is_nullable, data_type, '
            'data_type_id, character_maximum_length, numeric_precision, '
            'numeric_precision_radix, numeric_scale '
            'FROM duckdb_columns() WHERE NOT internal '
            'ORDER BY 1, 2, 3, column_index'
        )
        for row in cursor.fetchall():
            database, schema, parent, name, *values = row
            native = {
                'catalog_metadata': dict(zip((
                    'ordinal_position', 'comment', 'default', 'nullable',
                    'data_type', 'data_type_id',
                    'character_maximum_length', 'numeric_precision',
                    'numeric_precision_radix', 'numeric_scale',
                ), values)),
            }
            column = add('column', [database, schema, parent], name, native)
            parent_id = ':'.join([
                'table', str(database), str(schema), str(parent),
            ])
            if parent_id not in resources:
                parent_id = ':'.join([
                    'view', str(database), str(schema), str(parent),
                ])
            if parent_id in resources:
                resources[parent_id]['native']['columns'].append({
                    'resource_id': column['resource_id'],
                    'name': str(name),
                    **native['catalog_metadata'],
                })

        cursor.execute(
            'SELECT database_name, schema_name, table_name, index_name, '
            'index_oid, comment, tags, is_unique, is_primary, expressions, '
            'sql FROM duckdb_indexes() ORDER BY 1, 2, 3, 4'
        )
        for row in cursor.fetchall():
            database, schema, parent, name, *values = row
            oid, comment, tags, unique, primary, expressions, sql = values
            native = {
                'catalog_metadata': {
                    'oid': oid, 'comment': comment, 'tags': tags,
                    'unique': bool(unique), 'primary': bool(primary),
                    'expressions': expressions,
                },
                'ddl': sql,
            }
            index = add('index', [database, schema, parent], name, native)
            parent_id = ':'.join([
                'table', str(database), str(schema), str(parent),
            ])
            if parent_id in resources:
                resources[parent_id]['native']['indexes'].append({
                    'resource_id': index['resource_id'],
                    'name': str(name),
                    **native['catalog_metadata'],
                    'ddl': sql,
                })

        cursor.execute(
            'SELECT database_name, schema_name, table_name, '
            'constraint_name, constraint_index, constraint_type, '
            'constraint_text, expression, constraint_column_indexes, '
            'constraint_column_names, referenced_table, '
            'referenced_column_names FROM duckdb_constraints() '
            'ORDER BY 1, 2, 3, constraint_index'
        )
        for row in cursor.fetchall():
            database, schema, parent, name, *values = row
            native = {
                'catalog_metadata': dict(zip((
                    'constraint_index', 'constraint_type',
                    'constraint_text', 'expression', 'column_indexes',
                    'column_names', 'referenced_table',
                    'referenced_column_names',
                ), values)),
            }
            if values[6]:
                native['dependencies'] = [{
                    'relationship': 'foreign-key',
                    'referenced_table': values[6],
                    'referenced_columns': values[7],
                }]
            constraint = add(
                'constraint', [database, schema, parent], name, native
            )
            parent_id = ':'.join([
                'table', str(database), str(schema), str(parent),
            ])
            if parent_id in resources:
                summary = {
                    'resource_id': constraint['resource_id'],
                    'name': str(name),
                    **native['catalog_metadata'],
                }
                resources[parent_id]['native']['constraints'].append(summary)
                if values[6]:
                    resources[parent_id]['native'].setdefault(
                        'dependencies', []
                    ).append({
                        'relationship': 'foreign-key',
                        'constraint_name': str(name),
                        'referenced_table': values[6],
                        'referenced_columns': values[7],
                    })

        cursor.execute(
            'SELECT database_name, schema_name, sequence_name, sequence_oid, '
            'comment, tags, temporary, start_value, min_value, max_value, '
            'increment_by, cycle, last_value, sql '
            'FROM duckdb_sequences() ORDER BY 1, 2, 3'
        )
        for row in cursor.fetchall():
            database, schema, name, *values = row
            sql = values[-1]
            add('sequence', [database, schema], name, {
                'catalog_metadata': dict(zip((
                    'oid', 'comment', 'tags', 'temporary', 'start_value',
                    'min_value', 'max_value', 'increment_by', 'cycle',
                    'last_value',
                ), values[:-1])),
                'state': {'last_value': values[-2]},
                'ddl': sql,
            })

        cursor.execute(
            'SELECT database_name, schema_name, type_name, type_oid, '
            'type_size, logical_type, type_category, comment, tags, labels '
            'FROM duckdb_types() WHERE NOT internal ORDER BY 1, 2, 3'
        )
        for row in cursor.fetchall():
            database, schema, name, *values = row
            add('type', [database, schema], name, {
                'catalog_metadata': dict(zip((
                    'oid', 'size_bytes', 'logical_type', 'category',
                    'comment', 'tags', 'labels',
                ), values)),
            })
        cursor.execute(
            'SELECT database_name, schema_name, function_name, '
            'function_type, alias_of, description, comment, tags, '
            'return_type, parameters, parameter_types, varargs, '
            'macro_definition, has_side_effects, internal, function_oid, '
            'examples, stability, categories '
            'FROM duckdb_functions() ORDER BY 1, 2, 3'
        )
        for row in cursor.fetchall():
            database, schema, name, function_type, *values = row
            kind = 'macro' if 'macro' in str(function_type) else 'function'
            (
                alias_of, description, comment, tags, return_type,
                parameters, parameter_types, varargs, definition,
                has_side_effects, internal, oid, examples, stability,
                categories,
            ) = values
            add(kind, [database, schema], name, {
                'catalog_metadata': {
                    'function_type': function_type,
                    'alias_of': alias_of, 'description': description,
                    'comment': comment, 'tags': tags,
                    'return_type': return_type, 'varargs': varargs,
                    'has_side_effects': bool(has_side_effects),
                    'internal': bool(internal), 'oid': oid,
                    'examples': examples, 'stability': stability,
                    'categories': categories,
                },
                'parameters': [
                    {'name': parameter, 'data_type': parameter_type}
                    for parameter, parameter_type in zip(
                        parameters or [], parameter_types or []
                    )
                ],
                **({'definition': definition} if definition else {}),
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


def _initialize_connection(connection, route):
    attachments = route.get('attached_databases', [])
    if not isinstance(attachments, list):
        raise RelationalClientError(
            'DuckDB attached databases must be an array'
        )
    existing = {
        str(row[0]).lower(): str(row[1] or '')
        for row in connection.execute(
            'SELECT database_name, path FROM duckdb_databases()'
        ).fetchall()
    }
    for attachment in attachments:
        if not isinstance(attachment, Mapping):
            raise RelationalClientError(
                'DuckDB attachment must be an object'
            )
        name = attachment.get('name')
        if not isinstance(name, str) or not re.fullmatch(
                r'[A-Za-z_][A-Za-z0-9_]{0,127}', name) or name.lower() in {
                    'memory', 'system', 'temp',
        }:
            raise RelationalClientError('DuckDB attachment name is invalid')
        read_only = attachment.get('read_only', False)
        if not isinstance(read_only, bool):
            raise RelationalClientError(
                'DuckDB attachment read_only must be true or false'
            )
        attached_route = dict(route)
        attached_route['database'] = attachment.get('database')
        path = contained_database(attached_route)
        if name.lower() in existing:
            if existing[name.lower()] and existing[name.lower()] != path:
                raise RelationalClientError(
                    'DuckDB attachment name is already bound to another file'
                )
            continue
        escaped_path = path.replace("'", "''")
        quoted_name = '"' + name.replace('"', '""') + '"'
        mode = ' (READ_ONLY)' if read_only else ''
        connection.execute(
            f"ATTACH '{escaped_path}' AS {quoted_name}{mode}"
        )
        existing[name.lower()] = path


def _initialize_studio_session(connection, _route):
    """Begin the explicit transaction required by the editable Data Studio."""
    connection.execute('BEGIN TRANSACTION')


def _control_studio_transaction(connection, action):
    """Apply exact DuckDB finality and prepare the retained next unit."""
    if action not in {'commit', 'rollback'}:
        raise RelationalClientError(
            'DuckDB transaction action is unavailable'
        )
    connection.execute(action.upper())
    connection.execute('BEGIN TRANSACTION')


def _database_create_arguments(route, database, options):
    """Build exact DuckDB creation arguments from a provider-owned form."""
    expected = contained_database({**dict(route), 'database': database})
    if expected != database:
        raise RelationalClientError(
            'DuckDB creation target does not match the trusted route root'
        )
    if route.get('read_only') is True:
        raise RelationalClientError(
            'DuckDB read-only routes cannot create database files'
        )
    unknown = set(options).difference({'config'})
    if unknown:
        raise RelationalClientError(
            'DuckDB database creation options are unsupported'
        )
    config = options.get('config', {})
    if not isinstance(config, Mapping) or not all(
        isinstance(key, str) and key.strip() and
        isinstance(value, (str, int, float, bool)) and value is not None
        for key, value in config.items()
    ):
        raise RelationalClientError(
            'DuckDB creation configuration must contain named scalar values'
        )
    arguments = {'database': database}
    if config:
        arguments['config'] = dict(config)
    return arguments


def _drop_database_file(route, database):
    """Delete one contained offline DuckDB file after native identification."""
    path = contained_database(route)
    if path != os.path.realpath(database):
        raise RelationalClientError(
            'DuckDB deletion target does not match the trusted route'
        )
    if route.get('read_only') is True:
        raise RelationalClientError(
            'DuckDB read-only routes cannot delete database files'
        )
    if os.path.lexists(path + '.wal'):
        raise RelationalClientError(
            'DuckDB database has a WAL sidecar; close external sessions and '
            'checkpoint it before deletion'
        )
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RelationalClientError(
            f'DuckDB database file cannot be opened ({type(exc).__name__})'
        ) from None
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise RelationalClientError(
                'DuckDB deletion target is not a regular file'
            )
        header = os.read(descriptor, 12)
        if details.st_size and header[8:12] != b'DUCK':
            raise RelationalClientError(
                'DuckDB deletion target has no DuckDB database header'
            )
        identity = (details.st_dev, details.st_ino)
    finally:
        os.close(descriptor)
    current = os.lstat(path)
    if stat.S_ISLNK(current.st_mode) or (
            current.st_dev, current.st_ino) != identity:
        raise RelationalClientError(
            'DuckDB deletion target changed during verification'
        )
    os.unlink(path)
    return {
        'driver_operation': 'embedded-drop-database',
        'driver_returned': True,
        'database': path,
        'file_deleted': True,
        'transaction_finality_interpreted_by_common_code': False,
    }


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
        execute_on_connection=True,
        metadata_reader=_resources,
        security_reader=_security,
        administration=ADMINISTRATION,
        connection_initializer=_initialize_connection,
        session_initializer=_initialize_studio_session,
        transaction_controller=_control_studio_transaction,
        database_create_arguments=_database_create_arguments,
        database_dropper=_drop_database_file,
    ))


def create_provider(context, permissions, client=None):
    return DuckDBPilotProvider(
        context, permissions, client or _create_client()
    )
