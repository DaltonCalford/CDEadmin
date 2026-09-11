"""SQLite 3.53.0 exact-version native provider."""

import json
import os
import re
import sqlite3
import stat
from collections.abc import Mapping
from importlib import resources as package_resources

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
from ..embedded_route import contained_database, sqlite_arguments


PROFILE = PilotProfile(
    'org.cdeadmin.sqlite', 'sqlite-native', 'sqlite', 'SQLite', '3.53.0',
    'embedded_sqlite', 'relational', 'sqlite-sql', 'SQLite SQL',
    'sqlite-native-transaction', 'tabular',
    ('database', 'attached-database', 'table', 'column', 'view', 'index',
     'constraint', 'trigger', 'virtual-table', 'fts-table', 'pragma',
     'extension', 'metric'),
    ('sqlite-shell', 'backup', 'integrity-check', 'vacuum'),
    ('embedded_runtime', 'filesystem'),
    semantic_sql_dialect={
        'contract_complete': True,
        'language_profile': 'sqlite-sql', 'quote_open': '"',
        'quote_close': '"',
        'supports_rollup': False,
        'limit_style': 'limit',
        'true_literal': '1', 'false_literal': '0',
        'percent_change_numerator_cast': 'REAL',
        'time_operations': (
            'as_of', 'range', 'period_to_date', 'period_comparison',
        ),
        'window_operations': (
            'running_sum', 'moving_sum', 'moving_average', 'lag', 'delta',
            'percent_change', 'rank', 'dense_rank',
        ),
    },
    dialect_contract_id='sqlite.dialect.3.53.0.v1',
    dialect_evidence=(
        'sqlite-3.53.0-source-grammar',
        'sqlite-3.53.0-task-live-execution',
    ),
    dialect_contract_file='sqlite_dialect_3_53_0.json',
    metrics_contract_file='sqlite_metrics_3_53_0.json',
    query_plan_templates=(
        ('SQLite query plan', 'EXPLAIN QUERY PLAN {source}'),
        ('SQLite virtual-machine bytecode', 'EXPLAIN {source}'),
    ),
)


ADMINISTRATION = RelationalAdministration(RelationalAdminDialect(
    engine_id='sqlite',
    supports_cascade=False,
    embedded_database=True,
    database_create_mode='embedded-file',
    database_extension='.sqlite',
    not_applicable_concepts=frozenset({
        'servers', 'materialized_views', 'domains', 'types', 'sequences',
        'functions', 'procedures', 'roles_and_grants', 'partitions',
        'tablespaces_and_filespaces', 'replication_objects',
        'jobs_and_events',
    }),
    concept_resource_kinds={'schemas': ('database', 'attached-database')},
    supported={
        'database': frozenset({
            'inspect', 'create', 'alter', 'drop', 'backup', 'restore',
            'integrity_check', 'quick_check', 'foreign_key_check', 'vacuum',
            'incremental_vacuum', 'optimize', 'analyze', 'reindex',
            'wal_checkpoint',
        }),
        'attached-database': frozenset({'inspect'}),
        'table': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
            'insert', 'update', 'delete',
        }),
        'view': frozenset({'inspect', 'create', 'drop'}),
        'column': frozenset({'inspect', 'create', 'rename', 'drop'}),
        'index': frozenset({'inspect', 'create', 'drop'}),
        'trigger': frozenset({'inspect', 'create', 'drop'}),
        'constraint': frozenset({'inspect'}),
        'virtual-table': frozenset({
            'inspect', 'create', 'rename', 'drop',
        }),
        'fts-table': frozenset({
            'inspect', 'create', 'rename', 'drop',
        }),
        'pragma': frozenset({'inspect'}),
        'extension': frozenset({'inspect'}),
    },
))


class SQLiteProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def _route_arguments(route):
    return sqlite_arguments(route)


def _metric_records():
    """Load only scalar metrics admitted by the exact contract."""
    artifact = package_resources.files(__package__).joinpath(
        PROFILE.metrics_contract_file
    )
    document = json.loads(artifact.read_text(encoding='utf-8'))
    observations = {
        item['observation_id']: item
        for item in document['native_observations']
    }
    return [{
        **observations[item['observation_id']], **item,
    } for item in document['metrics']]


def _resources(connection, request):
    generation = str(request.get('capability_generation') or 'current')
    cursor = connection.cursor()
    try:
        cursor.execute('PRAGMA database_list')
        resources = {}
        databases = cursor.fetchall()

        def quote(value):
            return '"' + str(value).replace('"', '""') + '"'

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

        def pragma_value(schema_name, pragma_name):
            cursor.execute(
                f'PRAGMA {quote(schema_name)}.{pragma_name}'
            )
            row = cursor.fetchone()
            return row[0] if row else None

        def database_properties(sequence, schema_name, database_path):
            database_path = str(database_path or '')
            page_size = pragma_value(schema_name, 'page_size')
            page_count = pragma_value(schema_name, 'page_count')
            physical_bytes = None
            if database_path and os.path.isfile(database_path):
                physical_bytes = os.path.getsize(database_path)
            cursor.execute('SELECT sqlite_version(), sqlite_source_id()')
            runtime = cursor.fetchone()
            return {
                'database_name': database_path,
                'schema_name': schema_name,
                'attachment_sequence': int(sequence),
                'encoding': pragma_value(schema_name, 'encoding'),
                'page_size_bytes': page_size,
                'page_count': page_count,
                'logical_size_bytes': (
                    int(page_size) * int(page_count)
                    if page_size is not None and page_count is not None
                    else None
                ),
                'physical_file_bytes': physical_bytes,
                'free_list_pages': pragma_value(
                    schema_name, 'freelist_count'
                ),
                'maximum_page_count': pragma_value(
                    schema_name, 'max_page_count'
                ),
                'auto_vacuum_code': pragma_value(
                    schema_name, 'auto_vacuum'
                ),
                'journal_mode': pragma_value(
                    schema_name, 'journal_mode'
                ),
                'synchronous_code': pragma_value(
                    schema_name, 'synchronous'
                ),
                'locking_mode': pragma_value(
                    schema_name, 'locking_mode'
                ),
                'secure_delete_code': pragma_value(
                    schema_name, 'secure_delete'
                ),
                'application_id': pragma_value(
                    schema_name, 'application_id'
                ),
                'user_version': pragma_value(schema_name, 'user_version'),
                'schema_version': pragma_value(
                    schema_name, 'schema_version'
                ),
                'sqlite_runtime_version': runtime[0],
                'sqlite_source_id': runtime[1],
            }

        for sequence, name, path in databases:
            name = str(name)
            kind = 'database' if name == 'main' else 'attached-database'
            add(kind, [], name, database_properties(sequence, name, path))
        for _sequence, schema_name, _path in databases:
            schema_name = str(schema_name)
            cursor.execute(
                f'SELECT type, name, tbl_name, sql FROM '
                f'{quote(schema_name)}.sqlite_schema '
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
            schema_rows = cursor.fetchall()
            relation_names = []
            for native_kind, name, table_name, source in schema_rows:
                native_kind = str(native_kind)
                name = str(name)
                source_value = str(source or '')
                kind = native_kind
                if native_kind == 'table' and re.match(
                    r'^\s*CREATE\s+VIRTUAL\s+TABLE', source_value, re.I
                ):
                    kind = (
                        'fts-table' if re.search(
                            r'\bUSING\s+fts\d?\b', source_value, re.I
                        ) else 'virtual-table'
                    )
                if kind not in {
                    'table', 'view', 'index', 'trigger', 'virtual-table',
                    'fts-table',
                }:
                    continue
                native = {
                    'catalog_metadata': {
                        'schema_object_type': native_kind,
                        'table_name': str(table_name),
                    },
                    'ddl': source_value,
                }
                if kind in {'table', 'virtual-table', 'fts-table'}:
                    native.update({
                        'columns': [], 'constraints': [], 'indexes': [],
                        'triggers': [],
                    })
                elif kind == 'view':
                    native.update({'columns': [], 'triggers': []})
                resource = add(kind, [schema_name], name, native)
                if native_kind in {'table', 'view'}:
                    relation_names.append((name, resource['resource_id']))
            for table_name, relation_id in relation_names:
                cursor.execute(
                    f'PRAGMA {quote(schema_name)}.table_xinfo('
                    f'{quote(table_name)})'
                )
                columns = cursor.fetchall()
                primary = []
                for _cid, name, data_type, not_null, default, pk, hidden in (
                    columns
                ):
                    native = {'catalog_metadata': {
                        'ordinal_position': int(_cid),
                        'data_type': str(data_type or ''),
                        'nullable': not bool(not_null),
                        'default': default,
                        'primary_key_position': int(pk or 0),
                        'hidden': int(hidden or 0),
                    }}
                    column = add(
                        'column', [schema_name, table_name], name, native
                    )
                    resources[relation_id]['native']['columns'].append({
                        'resource_id': column['resource_id'],
                        'name': str(name),
                        **native['catalog_metadata'],
                    })
                    if int(pk or 0):
                        primary.append((int(pk), str(name)))
                if primary:
                    constraint = add(
                        'constraint', [schema_name, table_name],
                        f'pk_{table_name}', {
                            'catalog_metadata': {
                                'constraint_type': 'PRIMARY KEY',
                                'columns': [
                                    name for _position, name in sorted(
                                        primary
                                    )
                                ],
                            },
                        })
                    resources[relation_id]['native'][
                        'constraints'
                    ].append({
                        'resource_id': constraint['resource_id'],
                        'name': f'pk_{table_name}',
                        **constraint['native']['catalog_metadata'],
                    })
                # Views expose columns but do not admit table-only PRAGMAs.
                if resources[relation_id]['resource_kind'] == 'view':
                    continue
                cursor.execute(
                    f'PRAGMA {quote(schema_name)}.foreign_key_list('
                    f'{quote(table_name)})'
                )
                foreign_keys = {}
                for row in cursor.fetchall():
                    foreign_keys.setdefault(int(row[0]), []).append(row)
                for foreign_id, rows in foreign_keys.items():
                    constraint_name = f'fk_{table_name}_{foreign_id}'
                    dependency = {
                        'relationship': 'foreign-key',
                        'referenced_table': str(rows[0][2]),
                        'referenced_columns': [str(row[4]) for row in rows],
                    }
                    constraint = add(
                        'constraint', [schema_name, table_name],
                        constraint_name, {
                            'catalog_metadata': {
                                'constraint_type': 'FOREIGN KEY',
                                'columns': [str(row[3]) for row in rows],
                                'referenced_table': str(rows[0][2]),
                                'referenced_columns': [
                                    str(row[4]) for row in rows
                                ],
                                'on_update': str(rows[0][5]),
                                'on_delete': str(rows[0][6]),
                                'match': str(rows[0][7]),
                            },
                            'dependencies': [dependency],
                        })
                    resources[relation_id]['native'][
                        'constraints'
                    ].append({
                        'resource_id': constraint['resource_id'],
                        'name': constraint_name,
                        **constraint['native']['catalog_metadata'],
                    })
                    resources[relation_id]['native'].setdefault(
                        'dependencies', []
                    ).append({
                        **dependency, 'constraint_name': constraint_name,
                    })

                cursor.execute(
                    f'PRAGMA {quote(schema_name)}.index_list('
                    f'{quote(table_name)})'
                )
                for index_row in cursor.fetchall():
                    index_name = str(index_row[1])
                    index_id = ':'.join(['index', schema_name, index_name])
                    index = resources.get(index_id)
                    if index is not None:
                        resources[relation_id]['native']['indexes'].append({
                            'resource_id': index_id,
                            'name': index_name,
                            'unique': bool(index_row[2]),
                            'origin': str(index_row[3]),
                            'partial': bool(index_row[4]),
                            'ddl': index['native'].get('ddl'),
                        })
                for item in resources.values():
                    if (
                        item['resource_kind'] == 'trigger' and
                        item['native']['catalog_metadata']['table_name'] ==
                        table_name
                    ):
                        resources[relation_id]['native']['triggers'].append({
                            'resource_id': item['resource_id'],
                            'name': item['display_name'],
                            'ddl': item['native'].get('ddl'),
                        })
        for name in (
            'application_id', 'auto_vacuum', 'cache_size', 'foreign_keys',
            'journal_mode', 'page_size', 'query_only', 'synchronous',
            'user_version',
        ):
            add('pragma', [], name)
        cursor.execute('SELECT name FROM pragma_module_list ORDER BY name')
        for (name,) in cursor.fetchall():
            add('extension', [], name, {
                'extension_kind': 'virtual-table-module',
                'loaded_in_connection': True,
            })
        for metric in _metric_records():
            cursor.execute(metric['source'])
            row = cursor.fetchone()
            add('metric', ['Metrics'], metric['native_name'], {
                **metric,
                'value': row[0] if row else None,
                'observation_error': None if row else (
                    'scalar PRAGMA returned no row'
                ),
            })
        return list(resources.values())
    finally:
        cursor.close()


def _security(connection, request):
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT 'embedded-process'")
        current_user = str(cursor.fetchone()[0])
        return {
            'resource_id': f'authorization:{current_user}',
            'display_name': current_user,
            'authority_path': ['authorization', current_user],
            'generation': str(
                request.get('capability_generation') or 'current'
            ),
            'native': {'authorization_model': current_user},
        }
    finally:
        cursor.close()


def _initialize_connection(connection, route):
    attachments = route.get('attached_databases', [])
    if not isinstance(attachments, list):
        raise RelationalClientError(
            'SQLite attached databases must be an array'
        )
    cursor = connection.cursor()
    try:
        for attachment in attachments:
            if not isinstance(attachment, Mapping):
                raise RelationalClientError(
                    'SQLite attachment must be an object'
                )
            name = attachment.get('name')
            database = attachment.get('database')
            if not isinstance(name, str) or not re.fullmatch(
                    r'[A-Za-z_][A-Za-z0-9_]{0,127}', name) or name.lower() in {
                        'main', 'temp',
                    }:
                raise RelationalClientError(
                    'SQLite attachment name is invalid'
                )
            attached_route = dict(route)
            attached_route['database'] = database
            path = contained_database(attached_route)
            cursor.execute(
                'ATTACH DATABASE ? AS "' + name.replace('"', '""') + '"',
                (path,),
            )
    finally:
        cursor.close()


def _initialize_database(connection, options):
    """Apply settings that SQLite admits before the first schema object."""
    admitted = {
        'page_size', 'encoding', 'auto_vacuum',
        'application_id', 'user_version',
    }
    if set(options).difference(admitted):
        raise RelationalClientError(
            'SQLite database creation options are unsupported'
        )
    page_size = str(options.get('page_size', '4096'))
    if page_size not in {
        '512', '1024', '2048', '4096', '8192', '16384', '32768',
        '65536',
    }:
        raise RelationalClientError('SQLite page size is invalid')
    encoding = options.get('encoding', 'UTF-8')
    if encoding not in {'UTF-8', 'UTF-16', 'UTF-16le', 'UTF-16be'}:
        raise RelationalClientError('SQLite encoding is invalid')
    auto_vacuum = options.get('auto_vacuum', 'NONE')
    if auto_vacuum not in {'NONE', 'FULL', 'INCREMENTAL'}:
        raise RelationalClientError('SQLite auto-vacuum mode is invalid')
    integers = {}
    for field_id in ('application_id', 'user_version'):
        value = options.get(field_id, 0)
        if isinstance(value, bool) or not isinstance(value, int) or not (
                -2147483648 <= value <= 2147483647):
            raise RelationalClientError(
                f'SQLite {field_id.replace("_", " ")} is invalid'
            )
        integers[field_id] = value
    cursor = connection.cursor()
    observed = {}
    try:
        cursor.execute(f'PRAGMA page_size = {page_size}')
        cursor.execute(f"PRAGMA encoding = '{encoding}'")
        cursor.execute(f'PRAGMA auto_vacuum = {auto_vacuum}')
        for field_id, value in integers.items():
            cursor.execute(f'PRAGMA {field_id} = {value}')
        for field_id in (
            'page_size', 'encoding', 'auto_vacuum',
            'application_id', 'user_version',
        ):
            cursor.execute(f'PRAGMA {field_id}')
            row = cursor.fetchone()
            observed[field_id] = row[0] if row else None
        connection.commit()
    finally:
        cursor.close()
    return {
        'settings_applied': True,
        'observed': observed,
        'transaction_finality_interpreted_by_common_code': False,
    }


def _drop_database_file(route, database):
    """Delete one contained, offline SQLite file without inventing SQL."""
    path = contained_database(route)
    if path != os.path.realpath(database):
        raise RelationalClientError(
            'SQLite deletion target does not match the trusted route'
        )
    if route.get('read_only') is True or route.get('uri_immutable') is True:
        raise RelationalClientError(
            'SQLite read-only routes cannot delete database files'
        )
    sidecars = [path + suffix for suffix in ('-journal', '-wal', '-shm')]
    if any(os.path.lexists(item) for item in sidecars):
        raise RelationalClientError(
            'SQLite database has journal or WAL sidecars; close external '
            'sessions and checkpoint it before deletion'
        )
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RelationalClientError(
            f'SQLite database file cannot be opened ({type(exc).__name__})'
        ) from None
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise RelationalClientError(
                'SQLite deletion target is not a regular file'
            )
        header = os.read(descriptor, 16)
        if details.st_size and header != b'SQLite format 3\x00':
            raise RelationalClientError(
                'SQLite deletion target has no SQLite database header'
            )
        identity = (details.st_dev, details.st_ino)
    finally:
        os.close(descriptor)
    current = os.lstat(path)
    if stat.S_ISLNK(current.st_mode) or (
            current.st_dev, current.st_ino) != identity:
        raise RelationalClientError(
            'SQLite deletion target changed during verification'
        )
    os.unlink(path)
    return {
        'driver_operation': 'embedded-drop-database',
        'driver_returned': True,
        'database': path,
        'file_deleted': True,
        'transaction_finality_interpreted_by_common_code': False,
    }


def _sqlite_database_operation(connection, route, operation, options):
    """Run SQLite's native online backup/restore API within route bounds."""
    if operation not in {'backup', 'restore'}:
        raise RelationalClientError('SQLite database operation is unknown')
    if getattr(connection, 'in_transaction', False):
        raise RelationalClientError(
            'commit or roll back the SQLite transaction before this operation'
        )
    active_path = contained_database(route)
    backup_route = dict(route)
    backup_route['database'] = options.get('backup_path')
    backup_path = contained_database(backup_route)
    if backup_path in {':memory:', active_path}:
        raise RelationalClientError(
            'SQLite backup and active database paths must differ'
        )
    if os.path.lexists(backup_path):
        details = os.lstat(backup_path)
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise RelationalClientError(
                'SQLite backup path must be a regular file'
            )
    if operation == 'backup':
        overwrite = options.get('overwrite', False)
        if os.path.exists(backup_path) and not overwrite:
            raise RelationalClientError(
                'SQLite backup file exists and overwrite is disabled'
            )
        if os.path.exists(backup_path):
            os.unlink(backup_path)
        destination = sqlite3.connect(backup_path)
        try:
            connection.backup(destination)
            destination.commit()
            integrity = destination.execute(
                'PRAGMA quick_check(1)'
            ).fetchone()
        except Exception:
            destination.close()
            try:
                os.unlink(backup_path)
            except OSError:
                pass
            raise
        else:
            destination.close()
        return {
            'operation': 'sqlite-online-backup',
            'backup_path': backup_path,
            'bytes': os.path.getsize(backup_path),
            'quick_check': integrity[0] if integrity else None,
            'driver_returned': True,
        }
    if not os.path.isfile(backup_path):
        raise RelationalClientError('SQLite restore source does not exist')
    with open(backup_path, 'rb') as source_file:
        if source_file.read(16) != b'SQLite format 3\x00':
            raise RelationalClientError(
                'SQLite restore source has no SQLite database header'
            )
    source = sqlite3.connect(
        'file:' + backup_path + '?mode=ro', uri=True
    )
    try:
        integrity = source.execute('PRAGMA quick_check(1)').fetchone()
        if not integrity or integrity[0] != 'ok':
            raise RelationalClientError(
                'SQLite restore source did not pass quick_check'
            )
        source.backup(connection)
    finally:
        source.close()
    observed = connection.execute('PRAGMA quick_check(1)').fetchone()
    return {
        'operation': 'sqlite-online-restore',
        'backup_path': backup_path,
        'quick_check': observed[0] if observed else None,
        'driver_returned': True,
    }


def _create_client():
    return RelationalDBAPIClient(RelationalClientConfig(
        profile=PROFILE,
        module_name='sqlite3',
        version_query='SELECT sqlite_version()',
        connect_arguments=_route_arguments,
        metadata_reader=_resources,
        security_reader=_security,
        administration=ADMINISTRATION,
        connection_initializer=_initialize_connection,
        database_initializer=_initialize_database,
        database_dropper=_drop_database_file,
        database_operation_runner=_sqlite_database_operation,
    ))


def create_provider(context, permissions, client=None):
    return SQLiteProvider(context, permissions, client or _create_client())
