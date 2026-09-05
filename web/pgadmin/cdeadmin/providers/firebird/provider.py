"""Firebird 5.0.4 semantic provider."""

import hashlib
import json
import re
import threading

from pgadmin.cdeadmin.sdk import (
    ActualEnginePilotProvider,
    PilotProfile,
    RelationalClientConfig,
    RelationalClientError,
    RelationalDBAPIClient,
    load_optional_module,
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
     'character-set', 'collation', 'external-function', 'plugin',
     'publication',
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
    not_applicable_concepts=frozenset({
        'schemas', 'materialized_views', 'types', 'partitions',
        'tablespaces_and_filespaces', 'jobs_and_events',
    }),
    supported={
        'server': frozenset({'inspect'}),
        'database': frozenset({'inspect', 'create'}),
        'table': frozenset({
            'inspect', 'create', 'alter', 'drop',
            'insert', 'update', 'delete',
        }),
        'view': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'column': frozenset({'inspect', 'create', 'rename', 'drop'}),
        'constraint': frozenset({'inspect', 'create', 'drop'}),
        'index': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'sequence': frozenset({
            'inspect', 'create', 'alter', 'drop',
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
        'plugin': frozenset({'inspect'}),
        'publication': frozenset({'inspect', 'alter'}),
        'service-operation': frozenset({'inspect'}),
    },
))


class FirebirdProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


_CONFIG_LOCK = threading.RLock()


def _route_arguments(route, module=None):
    allowed = {
        'database', 'user', 'role', 'charset', 'auth_plugin_list',
        'session_time_zone', 'no_gc', 'no_db_triggers',
    }
    result = {key: value for key, value in route.items() if key in allowed}
    database = result.get('database')
    host = route.get('host')
    port = route.get('port')
    if isinstance(database, str) and isinstance(host, str) and host and (
        ':' not in database
    ):
        host_spec = f'{host}/{port}' if isinstance(port, int) else host
        result['database'] = f'{host_spec}:{database}'
    configured = any(
        name in route for name in (
            'trusted_auth', 'timeout', 'protocol',
            'dummy_packet_interval', 'wire_config', 'wire_crypt',
            'wire_compression', 'dbkey_scope',
        )
    )
    if not configured or module is None:
        return result
    database = result.pop('database', None)
    if not database:
        return result
    material = {
        name: route.get(name) for name in (
            'host', 'port', 'database', 'trusted_auth', 'timeout',
            'protocol', 'dummy_packet_interval', 'wire_config',
            'wire_crypt', 'wire_compression',
        )
    }
    digest = hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()[:24]
    server_name = f'cde_server_{digest}'
    database_name = f'cde_database_{digest}'
    with _CONFIG_LOCK:
        server = module.driver_config.get_server(server_name)
        if server is None:
            server = module.driver_config.register_server(server_name)
        server.host.value = route.get('host')
        server.port.value = (
            str(route['port']) if route.get('port') is not None else None
        )
        server.user.value = route.get('user')
        server.trusted_auth.value = bool(route.get('trusted_auth'))
        server.auth_plugin_list.value = route.get('auth_plugin_list')
        config = module.driver_config.get_database(database_name)
        if config is None:
            config = module.driver_config.register_database(database_name)
            config.database.value = route.get('database') or database
            config.server.value = server_name
            if route.get('protocol'):
                config.protocol.value = module.NetProtocol[
                    route['protocol']
                ]
            config.trusted_auth.value = bool(route.get('trusted_auth'))
            config.timeout.value = route.get('timeout')
            config.dummy_packet_interval.value = route.get(
                'dummy_packet_interval'
            )
            wire_options = []
            if route.get('wire_crypt'):
                if route['wire_crypt'] not in {
                    'Disabled', 'Enabled', 'Required'
                }:
                    raise RelationalClientError(
                        'Firebird wire encryption policy is invalid'
                    )
                wire_options.append(f'WireCrypt={route["wire_crypt"]}')
            if route.get('wire_compression'):
                wire_options.append('WireCompression=true')
            if route.get('wire_config'):
                wire_options.append(str(route['wire_config']))
            config.config.value = '\n'.join(wire_options) or None
    result['database'] = database_name
    if route.get('trusted_auth'):
        result.pop('user', None)
    if route.get('dbkey_scope'):
        result['dbkey_scope'] = module.DBKeyScope[route['dbkey_scope']]
    return result


def _server_route(route):
    return not isinstance(route.get('database'), str) or not (
        route['database'].strip()
    )


def _server_arguments(route, module):
    """Build a Firebird service-manager attachment without a database."""
    material = {
        name: route.get(name) for name in (
            'host', 'port', 'trusted_auth', 'auth_plugin_list',
            'wire_config', 'wire_crypt', 'wire_compression',
        )
    }
    digest = hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()[:24]
    server_name = f'cde_service_{digest}'
    with _CONFIG_LOCK:
        server = module.driver_config.get_server(server_name)
        if server is None:
            server = module.driver_config.register_server(server_name)
        server.host.value = route.get('host')
        server.port.value = (
            str(route['port']) if route.get('port') is not None else None
        )
        server.user.value = route.get('user')
        server.trusted_auth.value = bool(route.get('trusted_auth'))
        server.auth_plugin_list.value = route.get('auth_plugin_list')
        wire_options = []
        if route.get('wire_crypt'):
            if route['wire_crypt'] not in {
                'Disabled', 'Enabled', 'Required'
            }:
                raise RelationalClientError(
                    'Firebird wire encryption policy is invalid'
                )
            wire_options.append(f'WireCrypt={route["wire_crypt"]}')
        if route.get('wire_compression'):
            wire_options.append('WireCompression=true')
        if route.get('wire_config'):
            wire_options.append(str(route['wire_config']))
        server.config.value = '\n'.join(wire_options) or None
    result = {'server': server_name}
    if not route.get('trusted_auth') and route.get('user'):
        result['user'] = route['user']
    if route.get('role'):
        result['role'] = route['role']
    return result


def _server_identity(server, _request):
    version = _version((server.info.version,))
    return {
        'engine_id': PROFILE.engine_id,
        'version': version,
        'build_id': f'{PROFILE.engine_id}:{version}:service-manager',
        'protocol_id': PROFILE.protocol_id,
    }


def _server_resources(server, request):
    generation = str(request.get('capability_generation') or 'current')
    info = server.info
    resources = [{
        'resource_id': 'server:Firebird',
        'resource_kind': 'server',
        'display_name': 'Firebird',
        'display_path': ['Firebird'],
        'authority_path': ['server', 'Firebird'],
        'generation': generation,
        'native': {
            'version': str(info.version),
            'architecture': str(info.architecture),
            'home_directory': str(info.home_directory),
            'connection_count': int(info.connection_count),
            'scope': 'server',
        },
    }]
    resources.extend({
        'resource_id': f'service-operation:{name}',
        'resource_kind': 'service-operation',
        'display_name': name,
        'display_path': ['Firebird', 'Services', name],
        'authority_path': ['server', 'service-operation', name],
        'generation': generation,
    } for name in PROFILE.admin_tools)
    return resources


def _version(row):
    value = str(row[0]).strip() if row else ''
    match = re.search(r'(\d+\.\d+\.\d+)', value)
    if match is None:
        raise RelationalClientError('Firebird profile version is unavailable')
    return match.group(1)


def _initialize_connection(connection, route, module):
    isolation_name = route.get('transaction_isolation', 'SNAPSHOT')
    access_name = route.get('transaction_access', 'WRITE')
    lock_timeout = route.get('transaction_lock_timeout', -1)
    try:
        isolation = module.Isolation[isolation_name]
        access = module.TraAccessMode[access_name]
    except (KeyError, TypeError) as exc:
        raise RelationalClientError(
            'Firebird transaction defaults are invalid'
        ) from exc
    if isinstance(lock_timeout, bool) or not isinstance(lock_timeout, int) or (
        not -1 <= lock_timeout <= 86400
    ):
        raise RelationalClientError(
            'Firebird transaction lock timeout is invalid'
        )
    value = module.tpb(
        isolation=isolation, lock_timeout=lock_timeout,
        access_mode=access,
    )
    connection.default_tpb = value
    connection.main_transaction.default_tpb = value


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
            ('constraint', 'SELECT TRIM(C.RDB$RELATION_NAME), '
             'TRIM(C.RDB$CONSTRAINT_NAME), TRIM(C.RDB$CONSTRAINT_TYPE) '
             'FROM RDB$RELATION_CONSTRAINTS C JOIN RDB$RELATIONS R ON '
             'R.RDB$RELATION_NAME = C.RDB$RELATION_NAME WHERE '
             'COALESCE(R.RDB$SYSTEM_FLAG, 0) = 0 ORDER BY 1, 2'),
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
            ('plugin', 'SELECT TRIM(RDB$CONFIG_NAME), '
             'RDB$CONFIG_VALUE FROM RDB$CONFIG WHERE '
             "UPPER(RDB$CONFIG_NAME) LIKE '%PLUGIN%' ORDER BY 1"),
            ('publication', 'SELECT TRIM(RDB$PUBLICATION_NAME), '
             'RDB$ACTIVE_FLAG FROM RDB$PUBLICATIONS ORDER BY 1'),
        )
        for kind, source in simple_queries:
            for row in optional(source):
                name, detail = row[0], row[1]
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
    module = load_optional_module('firebird.driver')
    return RelationalDBAPIClient(RelationalClientConfig(
        profile=PROFILE,
        module_name='firebird.driver',
        version_query=(
            "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
            'FROM RDB$DATABASE'
        ),
        version_parser=_version,
        connect_arguments=lambda route: _route_arguments(route, module),
        metadata_reader=_resources,
        security_reader=_security,
        credential_argument='password',
        secret_acquirer=permissions.acquire_secret,
        connection_initializer=lambda connection, route: (
            _initialize_connection(connection, route, module)
        ),
        administration=ADMINISTRATION,
        server_route=_server_route,
        server_connector_name='connect_server',
        server_connect_arguments=lambda route: _server_arguments(
            route, module
        ),
        server_identity_reader=_server_identity,
        server_metadata_reader=_server_resources,
    ), module)


def create_provider(context, permissions, client=None):
    return FirebirdProvider(
        context, permissions, client or _create_client(permissions)
    )
