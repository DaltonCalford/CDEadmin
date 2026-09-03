"""Shared MySQL wire, distinct MySQL and MariaDB semantic profiles."""

import re

from pgadmin.cdeadmin.sdk import (
    ActualEnginePilotProvider,
    PilotProfile,
    RelationalClientConfig,
    RelationalDBAPIClient,
    RelationalClientError,
)
from ..relational_admin import (
    RelationalAdministration,
    RelationalAdminDialect,
)


MYSQL_PROFILE = PilotProfile(
    'org.cdeadmin.mysql', 'mysql-native', 'mysql', 'MySQL', '9.7.0',
    'mysql_wire', 'relational', 'mysql-sql', 'MySQL SQL',
    'mysql-session-autocommit', 'tabular',
    ('server', 'database', 'table', 'column', 'view', 'index', 'constraint',
     'trigger', 'event', 'procedure', 'function', 'partition', 'tablespace',
     'user', 'role', 'privilege', 'replication-channel', 'resource-group'),
    ('mysql-shell', 'backup', 'replication', 'account-administration'),
    semantic_sql_dialect={
        'language_profile': 'mysql-sql', 'quote_open': '`',
        'quote_close': '`', 'supports_rollup': False,
    },
)
MARIADB_PROFILE = PilotProfile(
    'org.cdeadmin.mariadb', 'mariadb-native', 'mariadb', 'MariaDB', '12.2.2',
    'mysql_wire', 'relational', 'mariadb-sql', 'MariaDB SQL',
    'mariadb-session-transaction', 'tabular',
    ('server', 'database', 'table', 'column', 'view', 'index', 'constraint',
     'sequence', 'trigger', 'event', 'procedure', 'function', 'package',
     'partition', 'tablespace', 'user', 'role', 'privilege',
     'replication-channel', 'server-link'),
    ('mariadb-client', 'mariadb-backup', 'replication', 'user-administration'),
    semantic_sql_dialect={
        'language_profile': 'mariadb-sql', 'quote_open': '`',
        'quote_close': '`', 'supports_rollup': False,
    },
)


def _administration(profile):
    common = {
        'server': frozenset({'inspect'}),
        'partition': frozenset({'inspect'}),
        'tablespace': frozenset({'inspect'}),
        'replication-channel': frozenset({'inspect'}),
        'resource-group': frozenset({'inspect'}),
        'server-link': frozenset({'inspect'}),
        'database': frozenset({
            'inspect', 'create', 'alter', 'drop',
        }),
        'table': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
            'insert', 'update', 'delete',
        }),
        'view': frozenset({'inspect', 'create', 'drop'}),
        'column': frozenset({'inspect', 'create', 'rename', 'drop'}),
        'constraint': frozenset({'inspect', 'create', 'drop'}),
        'index': frozenset({'inspect', 'create', 'drop'}),
        'trigger': frozenset({'inspect', 'create', 'drop'}),
        'procedure': frozenset({'inspect', 'create', 'drop'}),
        'function': frozenset({'inspect', 'create', 'drop'}),
        'event': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'sequence': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
        }),
        'role': frozenset({
            'inspect', 'create', 'drop',
        }),
        'user': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
        }),
        'privilege': frozenset({'inspect', 'grant', 'revoke'}),
    }
    if profile is MYSQL_PROFILE:
        common.pop('sequence')
    else:
        common['package'] = frozenset({
            'inspect', 'create', 'alter', 'drop',
        })
    return RelationalAdministration(RelationalAdminDialect(
        engine_id=profile.engine_id,
        quote_open='`',
        quote_close='`',
        parameter='%s',
        supported=common,
    ))


MYSQL_ADMINISTRATION = _administration(MYSQL_PROFILE)
MARIADB_ADMINISTRATION = _administration(MARIADB_PROFILE)


class MySQLPilotProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, MYSQL_PROFILE)


class MariaDBPilotProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, MARIADB_PROFILE)


def _route_arguments(route, profile):
    allowed = {
        'host', 'port', 'user', 'database', 'unix_socket',
        'connection_timeout', 'ssl_ca', 'ssl_cert', 'ssl_key',
    }
    result = {key: value for key, value in route.items() if key in allowed}
    if profile is MARIADB_PROFILE and 'connection_timeout' in result:
        result['connect_timeout'] = result.pop('connection_timeout')
    return result


def _version(row):
    value = str(row[0]).strip() if row else ''
    match = re.match(r'^(\d+\.\d+\.\d+)', value)
    if match is None:
        raise RelationalClientError(
            'MySQL-family profile version is unavailable'
        )
    return match.group(1)


def _resources(connection, request, profile=MYSQL_PROFILE):
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

        def optional(source):
            try:
                cursor.execute(source)
                return cursor.fetchall()
            except Exception:
                return []

        add('server', [], profile.engine_name)
        cursor.execute(
            'SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE '
            'FROM information_schema.TABLES '
            'ORDER BY TABLE_SCHEMA, TABLE_NAME'
        )
        for schema_name, object_name, object_type in cursor.fetchall():
            schema_name = str(schema_name)
            add('database', [], schema_name)
            object_type = str(object_type).upper()
            if object_type == 'SEQUENCE' and profile is MARIADB_PROFILE:
                kind = 'sequence'
            else:
                kind = 'view' if 'VIEW' in object_type else 'table'
            add(kind, [schema_name], object_name, {
                'table_type': object_type,
            })
        queries = (
            ('column', 'SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, '
             'COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT '
             'FROM information_schema.COLUMNS ORDER BY 1, 2, '
             'ORDINAL_POSITION'),
            ('index', 'SELECT DISTINCT TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, '
             'NON_UNIQUE, INDEX_TYPE FROM information_schema.STATISTICS '
             'ORDER BY 1, 2, 3'),
            ('constraint', 'SELECT TABLE_SCHEMA, TABLE_NAME, '
             'CONSTRAINT_NAME, CONSTRAINT_TYPE FROM information_schema.'
             'TABLE_CONSTRAINTS ORDER BY 1, 2, 3'),
            ('trigger', 'SELECT TRIGGER_SCHEMA, EVENT_OBJECT_TABLE, '
             'TRIGGER_NAME, ACTION_TIMING, EVENT_MANIPULATION '
             'FROM information_schema.TRIGGERS ORDER BY 1, 2, 3'),
            ('partition', 'SELECT TABLE_SCHEMA, TABLE_NAME, PARTITION_NAME, '
             'PARTITION_METHOD, PARTITION_EXPRESSION FROM information_schema.'
             'PARTITIONS WHERE PARTITION_NAME IS NOT NULL ORDER BY 1, 2, 3'),
        )
        for kind, source in queries:
            for row in optional(source):
                schema_name, parent, name, *detail = row
                add(kind, [schema_name, parent], name, {
                    'details': [
                        None if item is None else str(item)
                        for item in detail
                    ],
                })
        for schema_name, name, routine_type, data_type in optional(
            'SELECT ROUTINE_SCHEMA, ROUTINE_NAME, ROUTINE_TYPE, DATA_TYPE '
            'FROM information_schema.ROUTINES ORDER BY 1, 2'
        ):
            kind = str(routine_type).lower()
            if kind in {'package', 'package body'} and (
                profile is MARIADB_PROFILE
            ):
                add('package', [schema_name], name, {
                    'routine_type': str(routine_type).upper(),
                    'data_type': data_type,
                })
                continue
            if kind not in {'procedure', 'function'}:
                continue
            add(kind, [schema_name], name, {'data_type': data_type})
        for schema_name, name, status, event_type in optional(
            'SELECT EVENT_SCHEMA, EVENT_NAME, STATUS, EVENT_TYPE '
            'FROM information_schema.EVENTS ORDER BY 1, 2'
        ):
            add('event', [schema_name], name, {
                'status': status, 'event_type': event_type,
            })
        roles = set()
        for user, host in optional(
            'SELECT DISTINCT TO_USER, TO_HOST FROM mysql.role_edges '
            'ORDER BY 1, 2'
        ):
            roles.add((str(user), str(host)))
            add('role', [], f'{user}@{host}')
        for user, host, locked in optional(
            'SELECT User, Host, account_locked FROM mysql.user ORDER BY 1, 2'
        ):
            if (str(user), str(host)) not in roles:
                add('user', [], f'{user}@{host}', {
                    'account_locked': str(locked),
                })
        for grantee, privilege in optional(
            'SELECT GRANTEE, PRIVILEGE_TYPE FROM information_schema.'
            'USER_PRIVILEGES ORDER BY 1, 2'
        ):
            add('privilege', [grantee], privilege)
        for name, engine in optional(
            'SELECT DISTINCT TABLESPACE_NAME, ENGINE FROM information_schema.'
            'FILES WHERE TABLESPACE_NAME IS NOT NULL ORDER BY 1'
        ):
            add('tablespace', [], name, {'engine': engine})
        for row in optional(
            'SELECT CHANNEL_NAME, HOST, PORT FROM performance_schema.'
            'replication_connection_configuration ORDER BY CHANNEL_NAME'
        ):
            name, host, port = row
            add('replication-channel', [], name, {
                'host': host, 'port': port,
            })
        if profile is MYSQL_PROFILE:
            for name, resource_type, enabled in optional(
                'SELECT RESOURCE_GROUP_NAME, RESOURCE_GROUP_TYPE, ENABLED '
                'FROM information_schema.RESOURCE_GROUPS '
                'ORDER BY RESOURCE_GROUP_NAME'
            ):
                add('resource-group', [], name, {
                    'resource_type': resource_type, 'enabled': enabled,
                })
        else:
            for name, host, database in optional(
                'SELECT Server_name, Host, Db FROM mysql.servers '
                'ORDER BY Server_name'
            ):
                add('server-link', [], name, {
                    'host': host, 'database': database,
                })
        return list(resources.values())
    finally:
        cursor.close()


def _security(connection, request):
    cursor = connection.cursor()
    try:
        cursor.execute('SELECT CURRENT_USER(), USER()')
        current_user, session_user = cursor.fetchone()
        generation = str(request.get('capability_generation') or 'current')
        return {
            'resource_id': f'authorization:{current_user}',
            'display_name': str(current_user),
            'authority_path': ['authorization', str(current_user)],
            'generation': generation,
            'native': {
                'current_user': str(current_user),
                'session_user': str(session_user),
            },
        }
    finally:
        cursor.close()


def _create_client(profile, permissions):
    module_name = (
        'mysql.connector' if profile is MYSQL_PROFILE else 'mariadb'
    )
    return RelationalDBAPIClient(RelationalClientConfig(
        profile=profile,
        module_name=module_name,
        version_query='SELECT VERSION()',
        version_parser=_version,
        connect_arguments=lambda route: _route_arguments(route, profile),
        metadata_reader=lambda connection, request: _resources(
            connection, request, profile
        ),
        security_reader=_security,
        credential_argument='password',
        secret_acquirer=permissions.acquire_secret,
        administration=(
            MYSQL_ADMINISTRATION if profile is MYSQL_PROFILE
            else MARIADB_ADMINISTRATION
        ),
    ))


def create_provider(context, permissions, client=None):
    providers = {
        MYSQL_PROFILE.profile_id: MySQLPilotProvider,
        MARIADB_PROFILE.profile_id: MariaDBPilotProvider,
    }
    provider_type = providers[context.profile_id]
    return provider_type(
        context, permissions, client or _create_client(
            MYSQL_PROFILE if provider_type is MySQLPilotProvider
            else MARIADB_PROFILE,
            permissions,
        )
    )
