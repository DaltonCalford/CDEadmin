"""Firebird 5.0.4 semantic provider."""

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


PROFILE = PilotProfile(
    'org.cdeadmin.firebird', 'firebird-native', 'firebird', 'Firebird',
    '5.0.4', 'firebird_wire', 'relational', 'firebird-sql', 'Firebird SQL',
    'firebird-native-transaction', 'tabular',
    ('server', 'database', 'schema', 'table', 'column', 'view', 'index',
     'constraint', 'domain', 'sequence', 'routine', 'trigger', 'procedure',
     'function', 'package', 'exception', 'user', 'role', 'privilege',
     'character-set', 'collation', 'external-function',
     'service-operation'),
    ('isql', 'gbak', 'gfix', 'gstat', 'nbackup', 'user-administration'),
    semantic_sql_dialect={
        'language_profile': 'firebird-sql', 'quote_open': '"',
        'supports_rollup': False, 'limit_style': 'rows',
    },
)


ADMINISTRATION = RelationalAdministration(RelationalAdminDialect(
    engine_id='firebird',
    database_create_mode='firebird-driver',
    database_extension='.fdb',
    supported={
        'server': frozenset({'inspect'}),
        'database': frozenset({'inspect', 'create'}),
        'table': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
            'insert', 'update', 'delete',
        }),
        'view': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'column': frozenset({'inspect', 'create', 'rename', 'drop'}),
        'constraint': frozenset({'inspect', 'create', 'drop'}),
        'index': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'sequence': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
        }),
        'domain': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
        }),
        'trigger': frozenset({
            'inspect', 'create', 'alter', 'drop',
        }),
        'procedure': frozenset({
            'inspect', 'create', 'alter', 'drop',
        }),
        'function': frozenset({
            'inspect', 'create', 'alter', 'drop',
        }),
        'package': frozenset({
            'inspect', 'create', 'alter', 'drop',
        }),
        'exception': frozenset({
            'inspect', 'create', 'alter', 'drop',
        }),
        'role': frozenset({
            'inspect', 'create', 'alter', 'drop',
        }),
        'user': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'privilege': frozenset({'inspect', 'grant', 'revoke'}),
        'character-set': frozenset({'inspect'}),
        'collation': frozenset({'inspect'}),
        'external-function': frozenset({'inspect'}),
        'service-operation': frozenset({'inspect'}),
    },
))


class FirebirdProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def _route_arguments(route):
    allowed = {'database', 'user', 'role', 'charset', 'timeout'}
    result = {key: value for key, value in route.items() if key in allowed}
    database = result.get('database')
    host = route.get('host')
    port = route.get('port')
    if isinstance(database, str) and isinstance(host, str) and host and (
        ':' not in database
    ):
        host_spec = f'{host}/{port}' if isinstance(port, int) else host
        result['database'] = f'{host_spec}:{database}'
    return result


def _version(row):
    value = str(row[0]).strip() if row else ''
    match = re.search(r'(\d+\.\d+\.\d+)', value)
    if match is None:
        raise RelationalClientError('Firebird profile version is unavailable')
    return match.group(1)


def _resources(connection, request):
    cursor = connection.cursor()
    try:
        generation = str(request.get('capability_generation') or 'current')
        resources = {}

        def add(kind, path, name, native=None):
            path = [str(item).strip() for item in path]
            name = str(name).strip()
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

        add('server', [], 'Firebird')
        add('database', [], 'current')
        cursor.execute(
            'SELECT TRIM(RDB$RELATION_NAME), RDB$VIEW_BLR '
            'FROM RDB$RELATIONS WHERE COALESCE(RDB$SYSTEM_FLAG, 0) = 0 '
            'ORDER BY RDB$RELATION_NAME'
        )
        for name, view_blr in cursor.fetchall():
            name = str(name).strip()
            kind = 'view' if view_blr is not None else 'table'
            add(kind, [], name)
        queries = (
            ('column', 'SELECT TRIM(RF.RDB$RELATION_NAME), '
             'TRIM(RF.RDB$FIELD_NAME), TRIM(RF.RDB$FIELD_SOURCE), '
             'RF.RDB$NULL_FLAG, RF.RDB$DEFAULT_SOURCE '
             'FROM RDB$RELATION_FIELDS RF JOIN RDB$RELATIONS R ON '
             'R.RDB$RELATION_NAME = RF.RDB$RELATION_NAME WHERE '
             'COALESCE(R.RDB$SYSTEM_FLAG, 0) = 0 ORDER BY 1, '
             'RF.RDB$FIELD_POSITION'),
            ('index', 'SELECT TRIM(RDB$RELATION_NAME), '
             'TRIM(RDB$INDEX_NAME), RDB$UNIQUE_FLAG, RDB$INDEX_INACTIVE '
             'FROM RDB$INDICES WHERE COALESCE(RDB$SYSTEM_FLAG, 0) = 0 '
             'ORDER BY 1, 2'),
            ('constraint', 'SELECT TRIM(RDB$RELATION_NAME), '
             'TRIM(RDB$CONSTRAINT_NAME), TRIM(RDB$CONSTRAINT_TYPE) '
             'FROM RDB$RELATION_CONSTRAINTS WHERE '
             'COALESCE(RDB$SYSTEM_FLAG, 0) = 0 ORDER BY 1, 2'),
        )
        for kind, source in queries:
            for row in optional(source):
                parent, name, *details = row
                add(kind, [parent], name, {
                    'details': [
                        None if item is None else str(item).strip()
                        for item in details
                    ],
                })
        simple_queries = (
            ('domain', 'SELECT TRIM(RDB$FIELD_NAME), RDB$FIELD_TYPE '
             'FROM RDB$FIELDS WHERE COALESCE(RDB$SYSTEM_FLAG, 0) = 0 '
             "AND RDB$FIELD_NAME NOT STARTING WITH 'RDB$' ORDER BY 1"),
            ('sequence', 'SELECT TRIM(RDB$GENERATOR_NAME), '
             'RDB$INITIAL_VALUE FROM RDB$GENERATORS WHERE '
             'COALESCE(RDB$SYSTEM_FLAG, 0) = 0 ORDER BY 1'),
            ('trigger', 'SELECT TRIM(RDB$TRIGGER_NAME), '
             'TRIM(RDB$RELATION_NAME), RDB$TRIGGER_TYPE, '
             'RDB$TRIGGER_INACTIVE FROM RDB$TRIGGERS WHERE '
             'COALESCE(RDB$SYSTEM_FLAG, 0) = 0 ORDER BY 1'),
            ('procedure', 'SELECT TRIM(RDB$PROCEDURE_NAME), '
             'TRIM(RDB$PACKAGE_NAME) FROM RDB$PROCEDURES WHERE '
             'COALESCE(RDB$SYSTEM_FLAG, 0) = 0 ORDER BY 1'),
            ('function', 'SELECT TRIM(RDB$FUNCTION_NAME), '
             'TRIM(RDB$PACKAGE_NAME) FROM RDB$FUNCTIONS WHERE '
             'COALESCE(RDB$SYSTEM_FLAG, 0) = 0 AND '
             'RDB$MODULE_NAME IS NULL ORDER BY 1'),
            ('external-function', 'SELECT TRIM(RDB$FUNCTION_NAME), '
             'TRIM(RDB$MODULE_NAME) FROM RDB$FUNCTIONS WHERE '
             'COALESCE(RDB$SYSTEM_FLAG, 0) = 0 AND '
             'RDB$MODULE_NAME IS NOT NULL ORDER BY 1'),
            ('package', 'SELECT TRIM(RDB$PACKAGE_NAME), '
             'RDB$PACKAGE_HEADER_SOURCE FROM RDB$PACKAGES WHERE '
             'COALESCE(RDB$SYSTEM_FLAG, 0) = 0 ORDER BY 1'),
            ('exception', 'SELECT TRIM(RDB$EXCEPTION_NAME), '
             'RDB$MESSAGE FROM RDB$EXCEPTIONS WHERE '
             'COALESCE(RDB$SYSTEM_FLAG, 0) = 0 ORDER BY 1'),
            ('role', 'SELECT TRIM(RDB$ROLE_NAME), RDB$SYSTEM_PRIVILEGES '
             'FROM RDB$ROLES WHERE COALESCE(RDB$SYSTEM_FLAG, 0) = 0 '
             'ORDER BY 1'),
            ('character-set', 'SELECT TRIM(RDB$CHARACTER_SET_NAME), '
             'RDB$BYTES_PER_CHARACTER FROM RDB$CHARACTER_SETS ORDER BY 1'),
            ('collation', 'SELECT TRIM(RDB$COLLATION_NAME), '
             'RDB$CHARACTER_SET_ID FROM RDB$COLLATIONS ORDER BY 1'),
            ('user', 'SELECT TRIM(SEC$USER_NAME), TRIM(SEC$PLUGIN) '
             'FROM SEC$USERS ORDER BY 1'),
        )
        for kind, source in simple_queries:
            for name, detail in optional(source):
                add(kind, [], name, {
                    'detail': None if detail is None else str(detail).strip(),
                })
        for grantee, relation, privilege, grantor in optional(
            'SELECT TRIM(RDB$USER), TRIM(RDB$RELATION_NAME), '
            'TRIM(RDB$PRIVILEGE), TRIM(RDB$GRANTOR) '
            'FROM RDB$USER_PRIVILEGES ORDER BY 1, 2, 3'
        ):
            relation = str(relation or '').strip() or 'database'
            name = f'{str(grantee).strip()}:{str(privilege).strip()}'
            add('privilege', [relation], name, {
                'grantor': str(grantor or '').strip(),
            })
        for name in PROFILE.admin_tools:
            add('service-operation', [], name)
        return list(resources.values())
    finally:
        cursor.close()


def _security(connection, request):
    cursor = connection.cursor()
    try:
        cursor.execute(
            'SELECT CURRENT_USER, CURRENT_ROLE FROM RDB$DATABASE'
        )
        current_user, current_role = cursor.fetchone()
        current_user = str(current_user).strip()
        return {
            'resource_id': f'authorization:{current_user}',
            'display_name': current_user,
            'authority_path': ['authorization', current_user],
            'generation': str(
                request.get('capability_generation') or 'current'
            ),
            'native': {
                'current_user': current_user,
                'current_role': str(current_role or '').strip(),
            },
        }
    finally:
        cursor.close()


def _create_client(permissions):
    return RelationalDBAPIClient(RelationalClientConfig(
        profile=PROFILE,
        module_name='firebird.driver',
        version_query=(
            "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
            'FROM RDB$DATABASE'
        ),
        version_parser=_version,
        connect_arguments=_route_arguments,
        metadata_reader=_resources,
        security_reader=_security,
        credential_argument='password',
        secret_acquirer=permissions.acquire_secret,
        administration=ADMINISTRATION,
    ))


def create_provider(context, permissions, client=None):
    return FirebirdProvider(
        context, permissions, client or _create_client(permissions)
    )
