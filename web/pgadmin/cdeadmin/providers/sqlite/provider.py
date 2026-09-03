"""SQLite 3.53.0 semantic provider."""

import re

from pgadmin.cdeadmin.sdk import (
    ActualEnginePilotProvider,
    PilotProfile,
    RelationalClientConfig,
    RelationalDBAPIClient,
)
from ..relational_admin import (
    RelationalAdministration,
    RelationalAdminDialect,
)
from ..embedded_route import sqlite_arguments


PROFILE = PilotProfile(
    'org.cdeadmin.sqlite', 'sqlite-native', 'sqlite', 'SQLite', '3.53.0',
    'embedded_sqlite', 'relational', 'sqlite-sql', 'SQLite SQL',
    'sqlite-native-transaction', 'tabular',
    ('database', 'attached-database', 'table', 'column', 'view', 'index',
     'constraint', 'trigger', 'virtual-table', 'fts-table', 'pragma',
     'extension'),
    ('sqlite-shell', 'backup', 'integrity-check', 'vacuum'),
    ('embedded_runtime', 'filesystem'),
    semantic_sql_dialect={
        'language_profile': 'sqlite-sql', 'quote_open': '"',
        'supports_rollup': False,
        'true_literal': '1', 'false_literal': '0',
    },
)


ADMINISTRATION = RelationalAdministration(RelationalAdminDialect(
    engine_id='sqlite',
    supports_cascade=False,
    embedded_database=True,
    database_create_mode='embedded-file',
    database_extension='.sqlite',
    supported={
        'database': frozenset({'inspect', 'create'}),
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

        for _sequence, name, path in databases:
            name = str(name)
            kind = 'database' if name == 'main' else 'attached-database'
            add(kind, [], name, {'path': str(path or '')})
        for _sequence, schema_name, _path in databases:
            schema_name = str(schema_name)
            cursor.execute(
                f'SELECT type, name, tbl_name, sql FROM '
                f'{quote(schema_name)}.sqlite_schema '
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
            schema_rows = cursor.fetchall()
            table_names = []
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
                add(kind, [schema_name], name, {
                    'table_name': str(table_name),
                    'definition': source_value,
                })
                if native_kind == 'table':
                    table_names.append(name)
            for table_name in table_names:
                cursor.execute(
                    f'PRAGMA {quote(schema_name)}.table_xinfo('
                    f'{quote(table_name)})'
                )
                columns = cursor.fetchall()
                primary = []
                for _cid, name, data_type, not_null, default, pk, hidden in (
                    columns
                ):
                    add('column', [schema_name, table_name], name, {
                        'data_type': str(data_type or ''),
                        'nullable': not bool(not_null),
                        'default': default,
                        'primary_key_position': int(pk or 0),
                        'hidden': int(hidden or 0),
                    })
                    if int(pk or 0):
                        primary.append((int(pk), str(name)))
                if primary:
                    add('constraint', [schema_name, table_name],
                        f'pk_{table_name}', {
                            'constraint_type': 'PRIMARY KEY',
                            'columns': [
                                name for _position, name in sorted(primary)
                            ],
                    })
                cursor.execute(
                    f'PRAGMA {quote(schema_name)}.foreign_key_list('
                    f'{quote(table_name)})'
                )
                foreign_keys = {}
                for row in cursor.fetchall():
                    foreign_keys.setdefault(int(row[0]), []).append(row)
                for foreign_id, rows in foreign_keys.items():
                    add('constraint', [schema_name, table_name],
                        f'fk_{table_name}_{foreign_id}', {
                            'constraint_type': 'FOREIGN KEY',
                            'columns': [str(row[3]) for row in rows],
                            'referenced_table': str(rows[0][2]),
                            'referenced_columns': [
                                str(row[4]) for row in rows
                            ],
                    })
        for name in (
            'application_id', 'auto_vacuum', 'cache_size', 'foreign_keys',
            'journal_mode', 'page_size', 'query_only', 'synchronous',
            'user_version',
        ):
            add('pragma', [], name)
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


def _create_client():
    return RelationalDBAPIClient(RelationalClientConfig(
        profile=PROFILE,
        module_name='sqlite3',
        version_query='SELECT sqlite_version()',
        connect_arguments=_route_arguments,
        metadata_reader=_resources,
        security_reader=_security,
        administration=ADMINISTRATION,
    ))


def create_provider(context, permissions, client=None):
    return SQLiteProvider(context, permissions, client or _create_client())
