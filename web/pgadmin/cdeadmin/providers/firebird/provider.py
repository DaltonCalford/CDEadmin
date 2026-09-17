"""Firebird 5.0.4 semantic provider."""

import ctypes
import hashlib
import json
import os
import re
import threading
from contextlib import ExitStack, nullcontext
from enum import Enum
from importlib import resources as package_resources

from pgadmin.cdeadmin.sdk import (
    ActualEnginePilotProvider,
    PilotProfile,
    RelationalClientConfig,
    RelationalClientError,
    load_optional_module,
)
from pgadmin.cdeadmin.sdk.actual_engine import _mapping
from ..relational_admin import (
    FIREBIRD_SYSTEM_PRIVILEGES,
    RelationalAdministration,
    RelationalAdminDialect,
)
from . import columns, mappings, character_metadata, external_functions
from . import (
    blob_filters, object_privileges, shadows, database_storage,
    shadow_activation, limbo, views,
)
from .backup_guid import normalize_backup_guid
from .backup_level import normalize_backup_level
from .backup_volumes import logical_backup_volumes, start_logical_backup
from .restore_files import logical_restore_files, start_logical_restore
from .encryption_info import read_encryption_text
from .attachment_cache import requested_pages, stored_page_buffers
from .decfloat_traps import requested_traps
from .parallel_workers import requested_workers
from .failed_session import discard_failed_session
from .error_diagnostics import status_codes
from .restore_policy import physical_restore_policy
from .physical_io import normalize_physical_io
from .column_type_metadata import type_editor_values
from .catalog_reader import CatalogReader
from .connection_strings import (
    database_dsn, server_host, service_dsn, target_path, validate_transport,
)
from .identity import catalog_resource_id as _catalog_resource_id
from .query_parameters import normalize_parameters
from .query_values import normalize_value
from .query_columns import describe_columns
from .query_client import FirebirdQueryClient
from .service_connection import connect_service, notify_attached
from .database_creation import create_database as create_owned_database
from .session_settings import initialize_timeouts
from .transaction_state import observe_transaction, release_session
from . import availability
from . import repair


PROFILE = PilotProfile(
    'org.cdeadmin.firebird', 'firebird-native', 'firebird', 'Firebird',
    '5.0.4', 'firebird_wire', 'relational', 'firebird-sql', 'Firebird SQL',
    'firebird-native-transaction', 'tabular',
    ('server', 'database', 'schema', 'table', 'column', 'view', 'index',
     'constraint', 'domain', 'sequence', 'routine', 'trigger', 'procedure',
     'function', 'package', 'exception', 'user', 'role', 'privilege',
     'character-set', 'collation', 'external-function', 'blob-filter',
     'plugin', 'shadow', 'storage-file',
     'publication', 'authentication-mapping', 'global-authentication-mapping',
     'service-operation', 'metric'),
    ('isql', 'gbak', 'gfix', 'gstat', 'nbackup', 'user-administration'),
    semantic_sql_dialect={
        'contract_complete': True,
        'language_profile': 'firebird-sql', 'quote_open': '"',
        'quote_close': '"', 'supports_rollup': False,
        'limit_style': 'rows',
        'true_literal': 'TRUE', 'false_literal': 'FALSE',
        'window_input_cast': 'INTEGER',
        'percent_change_result_cast': 'DECIMAL(18,6)',
        'time_operations': (
            'as_of', 'range', 'period_to_date', 'period_comparison',
        ),
        'window_operations': (
            'running_sum', 'moving_sum', 'moving_average', 'lag', 'delta',
            'percent_change', 'rank', 'dense_rank',
        ),
    },
    starter_source=(
        'SELECT CAST(42 AS INTEGER) AS QUALIFICATION_VALUE '
        'FROM RDB$DATABASE'
    ),
    dialect_contract_id='firebird.dialect.5.0.4.v2',
    dialect_evidence=(
        'firebird-5.0.4-grammar',
        'firebird-5.0.4-task-live-execution',
    ),
    dialect_contract_file='firebird_dialect_5_0_4.json',
    metrics_contract_file='firebird_metrics_5_0_4.json',
    parameter_shape='array',
    parameter_hint=(
        'Use a JSON array in ? placeholder order, '
        'for example [42, "text", null].'),
)


ADMINISTRATION = RelationalAdministration(RelationalAdminDialect(
    engine_id='firebird',
    database_create_mode='firebird-driver',
    supports_cascade=False,
    database_extension='.fdb',
    not_applicable_concepts=frozenset({
        'schemas', 'materialized_views', 'types', 'partitions',
        'tablespaces_and_filespaces', 'jobs_and_events',
    }),
    supported={
        'server': frozenset({'inspect'}),
        'shadow': shadows.OPERATIONS,
        'storage-file': frozenset({'inspect'}),
        'database': frozenset({
            'inspect', 'create', 'alter', 'drop',
            'backup_logical', 'restore_logical', 'backup_physical',
            'restore_physical', 'validate_database', 'repair_database',
            'sweep_database', 'database_statistics', 'shutdown_database',
            'bring_online', 'set_page_cache_size', 'set_sweep_interval',
            'set_space_reservation', 'set_write_mode', 'set_access_mode',
            'set_sql_dialect', 'activate_shadow', 'remove_linger',
            'fixup_database', 'set_replica_mode', 'upgrade_database',
        }) | database_storage.OPERATIONS | limbo.ATTACHMENT_OPERATIONS,
        'table': frozenset({
            'inspect', 'create', 'alter', 'drop', 'recreate',
            'insert', 'update', 'delete', 'grant', 'revoke',
        }),
        'view': frozenset({'inspect', 'create', 'alter', 'drop',
                           'grant', 'revoke', 'create_or_alter', 'recreate'}),
        'column': frozenset({'inspect', 'create', 'alter', 'comment',
                             'rename', 'drop', 'grant', 'revoke'}),
        'constraint': frozenset({'inspect', 'create', 'drop'}),
        'index': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'sequence': frozenset({
            'inspect', 'create', 'alter', 'drop', 'grant', 'revoke',
            'create_or_alter', 'recreate', 'set_current', 'comment',
        }),
        'domain': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
        }),
        'trigger': frozenset({
            'inspect', 'create', 'alter', 'drop',
            'create_or_alter', 'recreate',
        }),
        'procedure': frozenset({
            'inspect', 'create', 'alter', 'drop', 'grant', 'revoke',
            'create_or_alter', 'recreate',
        }),
        'function': frozenset({
            'inspect', 'create', 'alter', 'drop', 'grant', 'revoke',
            'create_or_alter', 'recreate',
        }),
        'package': frozenset({
            'inspect', 'create', 'alter', 'drop', 'grant', 'revoke',
            'comment', 'create_or_alter', 'recreate', 'create_body',
            'replace_body', 'drop_body',
        }),
        'exception': frozenset({
            'inspect', 'create', 'alter', 'drop', 'grant', 'revoke',
            'create_or_alter', 'recreate',
        }),
        'role': frozenset({
            'inspect', 'create', 'alter', 'drop', 'grant', 'revoke',
            'configure_admin_mapping',
        }),
        'user': frozenset({'inspect', 'create', 'alter', 'drop',
                          'create_or_alter', 'recreate'}),
        'authentication-mapping': mappings.OPERATIONS,
        'global-authentication-mapping': mappings.OPERATIONS,
        'privilege': frozenset({'inspect', 'grant', 'revoke'}),
        **character_metadata.OPERATIONS,
        'external-function': (external_functions.OPERATIONS |
                              object_privileges.OPERATIONS),
        'blob-filter': blob_filters.OPERATIONS,
        'plugin': frozenset({'inspect'}),
        'publication': frozenset({'inspect', 'alter'}),
        'service-operation': frozenset({'inspect'}),
    },
))


class FirebirdProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)

    def open_session(self, request):
        if isinstance(self.client, FirebirdQueryClient):
            # Initialization alone is not admission: identity verification,
            # failed-open cleanup and publication must precede shutdown too.
            with self.client._connecting():
                return super().open_session(request)
        return super().open_session(request)

    def _grid_session_guard(self, request, *, closing=False):
        context = self._visual_admin_session_context(_mapping(request))
        if context and isinstance(self.client, FirebirdQueryClient):
            return self.client._exclusive(
                context['session_handle'], closing=closing)
        return nullcontext()

    def read_visual_admin_rows(self, request):
        with self._grid_session_guard(request):
            return super().read_visual_admin_rows(request)

    def plan_visual_admin(self, request):
        with self._grid_session_guard(request):
            return super().plan_visual_admin(request)

    def apply_visual_admin(self, request):
        request = _mapping(request)
        with self._grid_session_guard(request):
            if request.get('session_id') is not None:
                self._invalidate_grid_session(
                    request['session_id'], keep_plan_id=request.get('plan_id'))
            return super().apply_visual_admin(request)

    def _invalidate_grid_session(self, session_id, *, keep_plan_id=None):
        ADMINISTRATION.invalidate_row_session(session_id)
        self._visual_admin.invalidate_session_plans(
            session_id, keep_plan_id=keep_plan_id)

    def close_session(self, request):
        request = _mapping(request)
        with self._grid_session_guard(request, closing=True):
            session_id = request.get('session_id')
            if session_id in self._sessions:
                self._invalidate_grid_session(session_id)
            return super().close_session(request)

    def _execute_query_token(self, handle, payload):
        if isinstance(self.client, FirebirdQueryClient):
            # Arbitrary native source may change data, metadata or transaction
            # state. Do not classify it with a client-side SQL prefix parser.
            # Claim query ownership before releasing the grid boundary lock.
            with self.client._exclusive(handle):
                for session_id, session in tuple(self._sessions.items()):
                    if session.handle is handle:
                        self._invalidate_grid_session(session_id)
                return self.client.submit_query(handle, payload)
        return super()._execute_query_token(handle, payload)

    def _discard_unverified_session(self, handle):
        if isinstance(self.client, FirebirdQueryClient):
            return self.client.discard_unverified_session(handle)
        return super()._discard_unverified_session(handle)

    def release_for_profile_change(self):
        """Do not replace credentials or routing beneath an owned session."""
        if not isinstance(self.client, FirebirdQueryClient):
            if self._sessions:
                raise RelationalClientError(
                    'Close Firebird sessions before changing this connection')
            self.close()
            return
        with self.client._admission:
            if self._sessions or self.client._connections:
                raise RelationalClientError(
                    'Close Firebird sessions before changing this '
                    'connection; commit or roll back pending work '
                    'explicitly first')
            # Admission stays closed across the check and native teardown.
            # The client also rejects in-flight connects or temporary work.
            self.close()

    def control_transaction(self, request):
        request = _mapping(request)
        session = self._sessions.get(request.get('session_id'))
        if (isinstance(self.client, FirebirdQueryClient) and
                session is not None):
            # Keep the explicit action and following native observation in
            # one ownership interval; a new query must not slip between them.
            with self.client._exclusive(session.handle):
                if request.get('action') in self.client.transaction_actions:
                    self._invalidate_grid_session(request['session_id'])
                return super().control_transaction(request)
        return super().control_transaction(request)


_CONFIG_LOCK = threading.RLock()


def _wire_configuration(route):
    options = []
    if route.get('wire_config') is not None and not isinstance(
            route['wire_config'], str):
        raise RelationalClientError('Firebird wire configuration is invalid')
    if route.get('wire_crypt'):
        if route['wire_crypt'] not in {'Disabled', 'Enabled', 'Required'}:
            raise RelationalClientError(
                'Firebird wire encryption policy is invalid')
        options.append(f'WireCrypt={route["wire_crypt"]}')
    if route.get('wire_compression') is not None:
        if type(route['wire_compression']) is not bool:
            raise RelationalClientError(
                'Firebird wire compression policy is invalid')
        options.append('WireCompression=' + (
            'true' if route['wire_compression'] else 'false'))
    if route.get('wire_config'):
        options.append(route['wire_config'])
    return '\n'.join(options) or None


def _route_arguments(route, module=None, *, creation=None):
    validate_transport(route.get('protocol'), route.get('host'),
                       route.get('port'))
    cache_pages = requested_pages(route)
    traps = requested_traps(route)
    workers = requested_workers(route)
    linger_policy = route.get('no_linger')
    if linger_policy is not None and (
        not isinstance(linger_policy, str) or
        linger_policy not in {'NATIVE_DEFAULT', 'SUPPRESS'}
    ):
        raise RelationalClientError(
            'Firebird attachment linger policy is invalid')
    rounding = route.get('decfloat_round')
    if rounding == 'NATIVE_DEFAULT':
        rounding = None
    if rounding is not None and (
        not isinstance(rounding, str) or rounding not in {
            'CEILING', 'UP', 'HALF_UP', 'HALF_EVEN', 'HALF_DOWN',
            'DOWN', 'FLOOR', 'REROUND',
        }
    ):
        raise RelationalClientError('Firebird DECFLOAT rounding is invalid')
    allowed = {
        'database', 'user', 'role', 'charset', 'auth_plugin_list',
        'session_time_zone', 'no_gc', 'no_db_triggers',
    }
    result = {key: value for key, value in route.items() if key in allowed}
    # An unspecified driver charset can silently replace non-ASCII metadata
    # when DDL literals are converted into Firebird's metadata character set.
    # Default to Unicode while preserving an explicitly selected charset.
    result.setdefault('charset', 'UTF8')
    database = creation['database'] if creation is not None else (
        result.get('database'))
    host = server_host(route.get('host'))
    port = route.get('port')
    path = (target_path(database, host, port)
            if database and creation is None else None)
    if creation is not None:
        result['database'] = database
    elif database:
        protocol = route.get('protocol')
        result['database'] = database_dsn(
            path, host, port,
            protocol)
    # Even a minimal route needs a private configuration. Passing its DSN
    # directly to connect() activates process-wide driver defaults (or an
    # unrelated named configuration), which can change target/session options.
    if module is None:
        return result
    database = result.pop('database', None)
    if not database:
        return result
    wire_configuration = _wire_configuration(route)
    material = {
        name: route.get(name) for name in (
            'host', 'port', 'database', 'user', 'auth_plugin_list',
            'trusted_auth', 'timeout',
            'protocol', 'dummy_packet_interval', 'wire_config',
            'wire_crypt', 'wire_compression', 'decfloat_round', 'no_linger',
        )
    }
    if creation is not None:
        material['creation'] = creation
    material['attachment_cache_pages'] = cache_pages
    material['decfloat_traps'] = traps
    material['parallel_workers'] = workers
    digest = hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()[:24]
    server_name = f'cde_server_{digest}'
    database_name = f'cde_database_{digest}'
    with _CONFIG_LOCK:
        server = module.driver_config.get_server(server_name)
        if server is None:
            server = module.driver_config.register_server(server_name)
        # Driver 1.10.11 has no INET6 enum, although Firebird supports it.
        # Use its documented DSN configuration with a hostless private server
        # configuration so driver defaults cannot add a second address.
        inet6 = route.get('protocol') == 'INET6'
        explicit_dsn = inet6 or route.get('protocol') == 'XNET' or (
            creation is not None)
        server.host.value = None if explicit_dsn else host
        server.port.value = (
            str(route['port'])
            if route.get('port') is not None and not explicit_dsn else None
        )
        server.user.value = (
            None if route.get('trusted_auth') else route.get('user'))
        server.password.value = None
        server.trusted_auth.value = bool(route.get('trusted_auth'))
        server.auth_plugin_list.value = route.get('auth_plugin_list')
        config = module.driver_config.get_database(database_name)
        if config is None:
            config = module.driver_config.register_database(database_name)
            config.user.value = None
            config.password.value = None
            config.database.value = None if explicit_dsn else path
            config.server.value = server_name
            if creation is not None:
                config.dsn.value = database
                options = creation['options']
                config.page_size.value = options.get('page_size', 8192)
                config.db_charset.value = options.get(
                    'default_charset', 'UTF8')
                config.db_sql_dialect.value = options.get('sql_dialect', 3)
                config.forced_writes.value = options.get('forced_writes', True)
                config.reserve_space.value = options.get('reserve_space', True)
                config.db_cache_size.value = options.get('stored_page_buffers')
                config.sweep_interval.value = options.get('sweep_interval')
            elif explicit_dsn:
                config.dsn.value = database
            elif route.get('protocol'):
                config.protocol.value = module.NetProtocol[
                    route['protocol']
                ]
            config.trusted_auth.value = bool(route.get('trusted_auth'))
            config.timeout.value = route.get('timeout')
            config.dummy_packet_interval.value = route.get(
                'dummy_packet_interval'
            )
            config.config.value = wire_configuration
            config.decfloat_round.value = (
                module.DecfloatRound[rounding] if rounding is not None
                else None)
            config.decfloat_traps.value = (
                [module.DecfloatTraps[name] for name in traps]
                if traps is not None else None)
            config.cache_size.value = cache_pages
            config.parallel_workers.value = workers
            # jrd.cpp applies this DPB only when attaching to an existing
            # database, not to the initial creation attachment. Suppression
            # affects a shared live cache, never the stored LINGER setting.
            config.no_linger.value = (
                True if creation is None and linger_policy == 'SUPPRESS'
                else None)
    result['database'] = database_name
    if route.get('trusted_auth'):
        result.pop('user', None)
    if route.get('dbkey_scope'):
        result['dbkey_scope'] = module.DBKeyScope[route['dbkey_scope']]
    if creation is not None:
        result['overwrite'] = False
    return result


def _database_create_arguments(route, database, options, module):
    """Use the selected connection DPB, never shared driver defaults."""
    supported = {'page_size', 'default_charset', 'sql_dialect',
                 'forced_writes', 'reserve_space', 'stored_page_buffers',
                 'sweep_interval'}
    if not isinstance(options, dict) or set(options).difference(supported):
        raise RelationalClientError(
            'Firebird database creation options are unsupported')
    from .attachment_cache import creation_stored_pages
    from .creation_options import creation_sweep_interval
    creation_stored_pages(options)
    creation_sweep_interval(options)
    return _route_arguments(route, module, creation={
        'database': database, 'options': dict(options)})


def _server_route(route):
    return not isinstance(route.get('database'), str) or not (
        route['database'].strip()
    )


def _server_arguments(route, module):
    """Build a Firebird service-manager attachment without a database."""
    validate_transport(route.get('protocol'), route.get('host'),
                       route.get('port'))
    expected_db = route.get('service_expected_database')
    if expected_db is not None:
        if not isinstance(expected_db, str) or '\x00' in expected_db:
            raise RelationalClientError(
                'Firebird service authentication database must be text')
        expected_db = expected_db.strip() or None
    _configure_client_library(module)
    wire_configuration = _wire_configuration(route)
    material = {
        name: route.get(name) for name in (
            'host', 'port', 'user', 'protocol', 'trusted_auth',
            'auth_plugin_list',
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
        protocol = route.get('protocol')
        server.host.value = service_dsn(
            route.get('host'), route.get('port'),
            protocol)
        server.port.value = None
        server.user.value = (
            None if route.get('trusted_auth') else route.get('user'))
        server.password.value = None
        server.trusted_auth.value = bool(route.get('trusted_auth'))
        server.auth_plugin_list.value = route.get('auth_plugin_list')
        server.config.value = wire_configuration
    result = {'server': server_name}
    if not route.get('trusted_auth') and route.get('user'):
        result['user'] = route['user']
    if route.get('role'):
        result['role'] = route['role']
    if expected_db is not None:
        result['expected_db'] = expected_db
    return result


def _client_library_identity(module):
    """Return and enforce the Firebird client generation used by the driver."""
    api = module.get_api()
    function = api.client_library.isc_get_client_version
    function.argtypes = [ctypes.c_char_p]
    function.restype = None
    buffer = ctypes.create_string_buffer(256)
    function(buffer)
    value = buffer.value.decode('utf-8', errors='replace').strip()
    match = re.search(r'Firebird\s+(\d+)\.(\d+)', value)
    if match is None:
        raise RelationalClientError(
            'Firebird client library version could not be determined'
        )
    if int(match.group(1)) < 5:
        raise RelationalClientError(
            'Firebird 5 client library is required for the Firebird 5.0.4 '
            'provider'
        )
    return {
        'client_library_version': '.'.join(match.groups()),
        'client_library_name': os.path.basename(api.client_library_name),
    }


def _configure_client_library(module):
    """Select the configured Firebird client before the API is initialized."""
    client_library = os.environ.get(
        'CDEADMIN_FIREBIRD_CLIENT_LIBRARY', ''
    ).strip()
    if not client_library:
        return
    if not os.path.isfile(client_library):
        raise RelationalClientError(
            'Configured Firebird client library was not found'
        )
    loaded = module.fbapi.has_api()
    if loaded and os.path.realpath(
            module.get_api().client_library_name) != os.path.realpath(
                client_library):
        raise RelationalClientError(
            'Firebird client API was initialized from a different library '
            'before the provider loaded'
        )
    if not loaded:
        module.driver_config.fb_client_library.value = client_library


def _server_identity(server, _request, module):
    version = _version((server.info.version,))
    return {
        'engine_id': PROFILE.engine_id,
        'version': version,
        'build_id': f'{PROFILE.engine_id}:{version}:service-manager',
        'protocol_id': PROFILE.protocol_id,
        **_client_library_identity(module),
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
    for metric in _metric_records('firebird.driver.ServerInfoProvider3'):
        native_name = metric['native_name']
        try:
            value = getattr(info, native_name)
        except Exception as exc:
            value = None
            observation_error = str(exc)
        else:
            observation_error = None
        resources.append({
            'resource_id': f"metric:{metric['metric_id']}",
            'resource_kind': 'metric',
            'display_name': native_name,
            'display_path': ['Firebird', 'Metrics', native_name],
            'authority_path': [
                'server', 'metric', metric['metric_id'],
            ],
            'generation': generation,
            'native': {
                **metric,
                'value': None if value is None else str(value),
                'observation_error': observation_error,
            },
        })
    return resources


def _metric_records(source=None):
    """Read the generated exact-version inventory without adding defaults."""
    artifact = package_resources.files(__package__).joinpath(
        PROFILE.metrics_contract_file
    )
    document = json.loads(artifact.read_text(encoding='utf-8'))
    observations = {
        item['observation_id']: item
        for item in document['native_observations']
    }
    metrics = [{
        **observations[item['observation_id']], **item,
    } for item in document['metrics']]
    if source is None:
        return metrics
    return [item for item in metrics if item['source'] == source]


def _flag_value(module, enum_name, names, default=0, allowed=None):
    """Resolve only exact firebird-driver enum members declared by the form."""
    if names is None:
        return getattr(module, enum_name)(default)
    if not isinstance(names, list):
        raise RelationalClientError(
            f'Firebird {enum_name} selection must be an array'
        )
    enum = getattr(module, enum_name)
    value = enum(default)
    for name in names:
        if not isinstance(name, str) or name not in enum.__members__:
            raise RelationalClientError(
                f'Firebird {enum_name} selection is invalid'
            )
        if allowed is not None and name not in allowed:
            raise RelationalClientError(
                f'Firebird {enum_name} selection is unavailable for this '
                'operation'
            )
        value |= enum[name]
    return value


def _service_lines(callback):
    lines = []
    state = {'truncated': False}

    def collect(line):
        if len(lines) < 2000:
            lines.append(str(line))
        else:
            state['truncated'] = True

    callback(collect)
    return lines, state['truncated']


def _service_parallel_sweep(
        service, database, parallel_workers, role, module):
    """Issue sweep before its worker count as required by Firebird 5.0.4."""
    _service_parallel_repair(service, database, parallel_workers, role, module,
                             module.SrvRepairFlag.SWEEP_DB)


def _service_parallel_repair(
        service, database, parallel_workers, role, module, flags):
    """Send the repair action before the action-dependent worker option."""
    server = service._srv()
    core = module.core
    server._reset_output()
    with module.get_api().util.get_xpb_builder(
            core.XpbKind.SPB_START) as spb:
        spb.insert_tag(core.ServerAction.REPAIR)
        spb.insert_string(
            core.SPBItem.DBNAME, str(database), encoding=server.encoding
        )
        if role is not None:
            spb.insert_string(
                core.SPBItem.SQL_ROLE_NAME, role, encoding=server.encoding
            )
        spb.insert_int(core.SPBItem.OPTIONS, flags)
        spb.insert_int(
            core.SrvRepairOption.PARALLEL_WORKERS, parallel_workers
        )
        server._svc.start(spb.get_buffer())
    server.wait()


def _service_nfix_database(service, database, flags, role, module):
    """Build the Firebird 5 NFIX SPB in native action-first order.

    firebird-driver 1.10.11 inserts DBNAME before the NFIX action tag. The
    Firebird clumplet API rejects that dataless prefix before the request can
    reach the engine. Keep this narrow provider correction until the driver
    supplies the action-first ordering used by fbsvcmgr 5.0.4.
    """
    server = service._srv()
    core = module.core
    server._reset_output()
    with module.get_api().util.get_xpb_builder(
            core.XpbKind.SPB_START) as spb:
        spb.insert_tag(core.ServerAction.NFIX)
        spb.insert_string(
            core.SPBItem.DBNAME, str(database), encoding=server.encoding
        )
        if role is not None:
            spb.insert_string(
                core.SPBItem.SQL_ROLE_NAME, role, encoding=server.encoding
            )
        spb.insert_int(core.SPBItem.OPTIONS, flags)
        server._svc.start(spb.get_buffer())
    server.wait()


def _service_backup_with_history(server, database, options, module):
    """Firebird 5 NBAK history retention, absent from driver 1.10.11 API.

    Native svc.cpp requires CLEAN_HISTORY plus exactly one KEEP parameter.
    nbackup.cpp cleans RDB$BACKUP_HISTORY after recording the new backup;
    this does not delete backup files or prove a recoverable backup chain.
    """
    unit = options.get('history_keep_unit')
    count = options.get('history_keep_value')
    if (options.get('clean_history') is not True or
            not isinstance(unit, str) or unit not in {'DAYS', 'ROWS'} or
            isinstance(count, bool) or not isinstance(count, int) or
            not 1 <= count <= 2147483647):
        raise RelationalClientError(
            'Firebird backup history retention is invalid')
    options = normalize_physical_io('backup_physical', options)
    options['backup_level'] = normalize_backup_level(
        options.get('backup_level'))
    guid = normalize_backup_guid(options.get('database_guid'))
    flags = _flag_value(module, 'SrvNBackupFlag', options.get('backup_flags'),
                        allowed={'NO_TRIGGERS'})
    core = module.core
    server._reset_output()
    with module.get_api().util.get_xpb_builder(
            core.XpbKind.SPB_START) as spb:
        spb.insert_tag(core.ServerAction.NBAK)
        spb.insert_string(core.SPBItem.DBNAME, str(database),
                          encoding=server.encoding)
        spb.insert_string(core.SrvNBackupOption.FILE, options['backup_file'],
                          encoding=server.encoding)
        if guid is not None:
            spb.insert_string(core.SrvNBackupOption.GUID,
                              guid)
        else:
            spb.insert_int(core.SrvNBackupOption.LEVEL,
                           options.get('backup_level', 0))
        if options.get('direct_io') is not None:
            spb.insert_string(core.SrvNBackupOption.DIRECT,
                              'ON' if options['direct_io'] else 'OFF')
        if options.get('role'):
            spb.insert_string(core.SPBItem.SQL_ROLE_NAME, options['role'],
                              encoding=server.encoding)
        spb.insert_int(core.SPBItem.OPTIONS, flags)
        spb.insert_tag(core.SrvNBackupOption.CLEAN_HISTORY)
        spb.insert_int(core.SrvNBackupOption['KEEP_' + unit], count)
        server._svc.start(spb.get_buffer())
    server.wait()


def _firebird_service_operation(
        server, operation_id, database, options, module):
    """Dispatch one exact Firebird 5 database service-manager task."""
    if operation_id in availability.OPERATIONS:
        availability.validate(operation_id, options)
    if operation_id == 'repair_database':
        repair_selection = repair.selection(database, options)
    if operation_id == 'backup_logical':
        logical_backup_volumes(options)
    if operation_id == 'restore_logical':
        database_files, database_file_pages = logical_restore_files(options)
    if operation_id in {'backup_physical', 'restore_physical'}:
        options = normalize_physical_io(operation_id, options)
    if operation_id == 'backup_physical':
        options = {**options, 'database_guid': normalize_backup_guid(
            options.get('database_guid')), 'backup_level':
            normalize_backup_level(options.get('backup_level'))}
    restore_policy = (physical_restore_policy(operation_id, options)
                      if operation_id in {'restore_physical', 'fixup_database'}
                      else None)
    service = server.database
    result = {
        'schema': 'cdeadmin.firebird-service-result.v1',
        'operation_id': operation_id,
        'database': database,
        'server_completed': False,
        'output': [],
        'output_truncated': False,
    }
    role = options.get('role') or None
    if restore_policy is not None:
        result['restore_policy_requested'] = restore_policy
    if operation_id == 'backup_physical':
        result['backup_io_requested'] = (
            'NATIVE' if options['direct_io'] is None else
            'ON' if options['direct_io'] else 'OFF')
        result['backup_selection_requested'] = ({
            'mode': 'guid', 'guid': options['database_guid'],
        } if options['database_guid'] is not None else {
            'mode': 'level', 'level': options.get('backup_level', 0),
        })
    if operation_id == 'backup_logical' and (
        options.get('split_backup') or any(
            isinstance(options.get(field), str) and
            not options[field].isascii() for field in (
                'skip_data', 'include_data', 'key_holder', 'key_name',
                'crypt_plugin'))
    ):
        flags = _flag_value(module, 'SrvBackupFlag',
                            options.get('backup_flags'))
        lines, truncated = _service_lines(lambda output: start_logical_backup(
            server, database, options, flags, module, output))
        result.update(output=lines, output_truncated=truncated)
    elif operation_id == 'backup_logical':
        lines, truncated = _service_lines(lambda output: service.backup(
            database=database, backup=options['backup_file'], role=role,
            flags=_flag_value(
                module, 'SrvBackupFlag', options.get('backup_flags')
            ),
            verbose=bool(options.get('verbose', True)),
            stats=options.get('statistics') or None,
            verbint=options.get('verbose_interval'),
            skip_data=options.get('skip_data') or None,
            include_data=options.get('include_data') or None,
            keyhoder=options.get('key_holder') or None,
            keyname=options.get('key_name') or None,
            crypt=options.get('crypt_plugin') or None,
            parallel_workers=options.get('parallel_workers'),
            callback=output,
        ))
        result.update(output=lines, output_truncated=truncated)
    elif operation_id == 'restore_logical':
        restore_flags = list(options.get('restore_flags') or [])
        lifecycle = (
            'REPLACE' if options.get('replace_existing') else 'CREATE'
        )
        restore_flags.append(lifecycle)
        backup_files = [options['backup_file'], *(
            options.get('additional_backup_files') or []
        )]
        if any(isinstance(options.get(field), str) and
               not options[field].isascii() for field in (
                   'key_holder', 'key_name', 'crypt_plugin')):
            flags = _flag_value(module, 'SrvRestoreFlag', restore_flags)
            lines, truncated = _service_lines(
                lambda output: start_logical_restore(
                    server, options, flags, module, output))
        else:
            lines, truncated = _service_lines(lambda output: service.restore(
                backup=(
                    backup_files[0] if len(backup_files) == 1 else backup_files
                ),
                database=(
                    database_files[0]
                    if len(database_files) == 1 else database_files
                ),
                db_file_pages=database_file_pages or (), role=role,
                flags=_flag_value(module, 'SrvRestoreFlag', restore_flags),
                verbose=bool(options.get('verbose', True)),
                stats=options.get('statistics') or None,
                verbint=options.get('verbose_interval'),
                skip_data=options.get('skip_data') or None,
                include_data=options.get('include_data') or None,
                keyhoder=options.get('key_holder') or None,
                keyname=options.get('key_name') or None,
                crypt=options.get('crypt_plugin') or None,
                replica_mode=(
                    module.ReplicaMode[options['replica_mode']]
                    if options.get('replica_mode') else None
                ),
                page_size=(
                    int(options['page_size'])
                    if options.get('page_size') else None
                ),
                buffers=options.get('page_buffers'),
                access_mode=module.DbAccessMode[
                    options.get('access_mode', 'READ_WRITE')
                ],
                parallel_workers=options.get('parallel_workers'),
                callback=output,
            ))
        result.update(
            database=options['restore_database'], output=lines,
            output_truncated=truncated,
        )
    elif operation_id == 'backup_physical' and options.get('clean_history'):
        _service_backup_with_history(server, database, options, module)
        result['history_retention_requested'] = {
            'unit': options['history_keep_unit'],
            'value': options['history_keep_value'],
            'backup_files_deleted': False,
        }
    elif operation_id == 'backup_physical':
        service.nbackup(
            database=database, backup=options['backup_file'], role=role,
            level=options.get('backup_level', 0),
            direct=options.get('direct_io'),
            flags=_flag_value(
                module, 'SrvNBackupFlag', options.get('backup_flags'),
                allowed={'NO_TRIGGERS'},
            ),
            guid=options.get('database_guid') or None,
        )
    elif operation_id == 'restore_physical':
        service.nrestore(
            backups=options['backup_files'],
            database=options['restore_database'], role=role,
            direct=None,
            flags=_flag_value(
                module, 'SrvNBackupFlag', options.get('restore_flags'),
                allowed={'IN_PLACE', 'SEQUENCE'},
            ),
        )
        result['database'] = options['restore_database']
    elif operation_id == 'validate_database':
        lines, truncated = _service_lines(lambda output: service.validate(
            database=database, role=role,
            include_table=options.get('include_table') or None,
            exclude_table=options.get('exclude_table') or None,
            include_index=options.get('include_index') or None,
            exclude_index=options.get('exclude_index') or None,
            lock_timeout=options.get('lock_timeout'), callback=output,
        ))
        result.update(output=lines, output_truncated=truncated)
    elif operation_id == 'repair_database':
        flags = _flag_value(module, 'SrvRepairFlag',
                            [name for name in repair_selection['native_flags']
                             if name != 'NOLINGER'])
        if repair_selection['no_linger_requested']:
            # The native gfix bitmask includes this property flag, although
            # firebird-driver declares it outside SrvRepairFlag.
            flags |= module.core.SrvPropertiesFlag.NOLINGER
        workers = repair_selection['parallel_workers_requested']
        if workers is not None:
            _service_parallel_repair(
                service, database, workers, role, module, flags)
        else:
            service.repair(database=database, role=role, flags=flags)
        result['repair_selection_requested'] = repair_selection
    elif operation_id == 'sweep_database':
        parallel_workers = options.get('parallel_workers')
        if parallel_workers is not None and hasattr(service, '_srv'):
            _service_parallel_sweep(
                service, database, parallel_workers, role, module
            )
        else:
            service.sweep(
                database=database, role=role,
                parallel_workers=parallel_workers,
            )
    elif operation_id == 'database_statistics':
        lines, truncated = _service_lines(
            lambda output: service.get_statistics(
                database=database, role=role,
                flags=_flag_value(
                    module, 'SrvStatFlag', options.get('statistics_flags')
                ),
                tables=options.get('tables') or None, callback=output,
            )
        )
        result.update(output=lines, output_truncated=truncated)
    elif operation_id == 'shutdown_database':
        service.shutdown(
            database=database, role=role,
            mode=module.ShutdownMode[options.get('mode', 'FULL')],
            method=module.ShutdownMethod[
                options.get('method', 'DENY_ATTACHMENTS')
            ],
            timeout=options.get('shutdown_timeout', 0),
        )
    elif operation_id == 'bring_online':
        service.bring_online(
            database=database, role=role,
            mode=module.OnlineMode[options.get('mode', 'NORMAL')],
        )
    elif operation_id == 'set_page_cache_size':
        service.set_default_cache_size(
            database=database, size=options['page_buffers'], role=role,
        )
    elif operation_id == 'set_sweep_interval':
        service.set_sweep_interval(
            database=database, interval=options['sweep_interval'],
            role=role,
        )
    elif operation_id == 'set_space_reservation':
        service.set_space_reservation(
            database=database,
            mode=module.DbSpaceReservation[options['mode']], role=role,
        )
    elif operation_id == 'set_write_mode':
        service.set_write_mode(
            database=database, mode=module.DbWriteMode[options['mode']],
            role=role,
        )
    elif operation_id == 'set_access_mode':
        service.set_access_mode(
            database=database, mode=module.DbAccessMode[options['mode']],
            role=role,
        )
    elif operation_id == 'set_sql_dialect':
        service.set_sql_dialect(
            database=database, dialect=int(options['sql_dialect']),
            role=role,
        )
    elif operation_id == 'activate_shadow':
        activation = shadow_activation.validate(options)
        if database != activation['shadow_filename']:
            raise RelationalClientError(
                'The compiled shadow recovery target changed')
        # HDR_PAGES reads physical headers without a database attachment.
        # Firebird 5.0.4 rejects SQL_ROLE_NAME in a DB_STATS start SPB.
        # The owned Services API attachment carries the reviewed task role.
        lines, truncated = _service_lines(
            lambda output: service.get_statistics(
                database=database,
                flags=module.SrvStatFlag.HDR_PAGES, callback=output))
        result['shadow_header_verification'] = shadow_activation.verify_header(
            lines, truncated=truncated)
        service.activate_shadow(database=database, role=activation['role'])
    elif operation_id == 'remove_linger':
        service.no_linger(database=database, role=role)
    elif operation_id == 'fixup_database':
        flags = _flag_value(
            module, 'SrvNBackupFlag', options.get('fixup_flags'),
            allowed={'SEQUENCE'},
        )
        if hasattr(service, '_srv'):
            _service_nfix_database(
                service, database, flags, role, module
            )
        else:
            # Lightweight contract doubles do not expose the private service
            # handle; retain the public method path for unit isolation.
            service.nfix_database(
                database=database, role=role, flags=flags,
            )
    elif operation_id == 'set_replica_mode':
        mode = module.ReplicaMode[options['mode']]
        service.set_replica_mode(
            database=database, mode=mode, role=role,
        )
    elif operation_id == 'upgrade_database':
        service.upgrade(database=database)
    else:
        raise RelationalClientError(
            'Firebird database service operation is unavailable'
        )
    result['server_completed'] = True
    return result


def _version(row):
    value = str(row[0]).strip() if row else ''
    match = re.search(r'(\d+\.\d+\.\d+)', value)
    if match is None:
        raise RelationalClientError('Firebird profile version is unavailable')
    return match.group(1)


def _initialize_connection(connection, route, module):
    _client_library_identity(module)
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
        not -1 <= lock_timeout <= 32767
    ):
        raise RelationalClientError(
            'Firebird transaction lock timeout must be an integer from '
            '-1 through 32767 seconds'
        )
    advanced = {}
    for option in ('no_auto_undo', 'auto_commit', 'ignore_limbo'):
        flag = route.get('transaction_' + option, False)
        if not isinstance(flag, bool):
            raise RelationalClientError(
                'Firebird transaction ' + option + ' must be a boolean')
        advanced[option] = flag
    arguments = dict(isolation=isolation, lock_timeout=lock_timeout,
                     access_mode=access)
    value = (module.TPB(**arguments, **advanced).get_buffer()
             if any(advanced.values()) else module.tpb(**arguments))
    connection.default_tpb = value
    connection.main_transaction.default_tpb = value
    initialize_timeouts(connection, route, module)


def _materialize_catalog_value(value):
    """Read and close native BLOBs before the catalog cursor is reused."""
    if callable(getattr(value, 'read', None)):
        reader = value
        try:
            value = reader.read()
        finally:
            if callable(getattr(reader, 'close', None)):
                reader.close()
    return value


def _catalog_detail(field, value):
    """Preserve native source text, including significant whitespace."""
    value = _materialize_catalog_value(value)
    if value is None:
        return None
    text = str(value)
    return text if field in {
        'description', 'expression_source', 'condition_source',
        'metadata_source', 'header_source', 'body_source', 'default_source',
        'validation_source', 'computed_source', 'definition',
    } else text.rstrip(' ')


def _role_privileges(value):
    """Decode Firebird's byte-indexed privilege bitmap (bit zero reserved)."""
    if value is None:
        value = b''
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise RelationalClientError(
            'Firebird role privilege bitmap is invalid')
    raw = bytes(value)
    bits = {index * 8 + bit for index, byte in enumerate(raw)
            for bit in range(8) if byte & (1 << bit)}
    return {
        'system_privileges': [
            name for index, name in enumerate(FIREBIRD_SYSTEM_PRIVILEGES, 1)
            if index in bits],
        'system_privileges_hex': raw.hex(),
        'unknown_system_privilege_bits': sorted(
            bits - set(range(1, len(FIREBIRD_SYSTEM_PRIVILEGES) + 1))),
    }


def _resources(connection, request):
    cursor = connection.cursor()
    rendering = ExitStack()
    try:
        generation = str(request.get('capability_generation') or 'current')
        resources = {}

        def add(kind, path, name, native=None, *, native_identity=None):
            path = [str(item).rstrip(' ') for item in path]
            name = (str(name) if kind == 'storage-file'
                    else str(name).rstrip(' '))
            resource_id = _catalog_resource_id(kind, path, name)
            if native_identity is not None:
                resource_id = _catalog_resource_id(kind, [], json.dumps(
                    native_identity, ensure_ascii=False,
                    separators=(',', ':')))
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

        catalog_reader = CatalogReader(cursor, _materialize_catalog_value)
        catalog_rows = catalog_reader.rows

        mapping_catalog = {}
        info = connection.info
        information_observations = {}

        def info_value(name):
            try:
                value = getattr(info, name)
            except Exception as error:
                information_observations[name] = {
                    'available': False, 'error_type': type(error).__name__}
                return None
            information_observations[name] = {'available': True}
            if value is None:
                return None
            if isinstance(value, Enum):
                return value.name
            if isinstance(value, (str, int, float, bool)):
                return value
            return str(value)

        engine_version = info_value('engine_version')
        add('server', [], 'Firebird', {
            'scope': 'database-attachment',
            'version': info_value('version'),
            'engine_version': (str(engine_version)
                               if engine_version is not None else None),
            'server_version': info_value('server_version'),
            'site': info_value('site'),
            'provider': info_value('provider'),
            'implementation': info_value('implementation'),
        })
        database_native = {
            'scope': 'database',
            'mapping_catalog': mapping_catalog,
            **{
                name: info_value(name) for name in (
                    'name', 'creation_date', 'ods', 'page_cache_size',
                    'sql_dialect',
                    'size_in_pages', 'pages_allocated', 'pages_used',
                    'pages_free', 'current_memory', 'max_memory',
                    'cache_hit_ratio', 'oit', 'oat', 'ost',
                    'next_transaction', 'fetches', 'reads', 'writes',
                    'marks', 'idle_timeout', 'statement_timeout',
                    'db_class', 'provider',
                )
            },
        }
        try:
            database_native['stored_page_buffers'] = stored_page_buffers(info)
            information_observations['stored_page_buffers'] = {
                'available': True}
        except Exception as error:
            database_native['stored_page_buffers'] = None
            information_observations['stored_page_buffers'] = {
                'available': False, 'error_type': type(error).__name__,
                'native_status_codes': list(status_codes(error)),
            }
        database_rows = catalog_rows(
            'SELECT MON$DATABASE_NAME, MON$PAGE_SIZE, MON$ODS_MAJOR, '
            'MON$ODS_MINOR, MON$SQL_DIALECT, '
            'CAST(MON$CREATION_DATE AS VARCHAR(64)), MON$PAGES, '
            'MON$BACKUP_STATE, MON$CRYPT_STATE, MON$OWNER, MON$GUID, '
            'MON$READ_ONLY, MON$FORCED_WRITES, MON$RESERVE_SPACE, '
            'MON$SWEEP_INTERVAL, MON$OLDEST_TRANSACTION, '
            'MON$OLDEST_ACTIVE, MON$OLDEST_SNAPSHOT, '
            'MON$NEXT_TRANSACTION, MON$PAGE_BUFFERS, MON$SHUTDOWN_MODE, '
            'MON$CRYPT_PAGE, MON$SEC_DATABASE, MON$FILE_ID, '
            'MON$NEXT_ATTACHMENT, MON$NEXT_STATEMENT FROM MON$DATABASE',
            'database monitoring', required=False,
        )
        if database_rows:
            row = database_rows[0]
            names = (
                'database_name', 'page_size', 'ods_major', 'ods_minor',
                'sql_dialect', 'creation_date', 'allocated_pages',
                'backup_state', 'encryption_state', 'owner', 'guid',
                'read_only', 'forced_writes', 'reserve_space',
                'sweep_interval', 'oldest_transaction', 'oldest_active',
                'oldest_snapshot', 'next_transaction', 'page_buffers',
                'shutdown_mode', 'encryption_page', 'security_database',
                'file_id', 'next_attachment', 'next_statement',
            )
            database_native.update({
                name: None if value is None else str(value).strip()
                for name, value in zip(names, row)
            })
        # Every generated metadata helper (not just table/view rendering)
        # must use this catalog's observed stored dialect. The scope is local
        # to this call and is released even on a failed read or interruption.
        from .ddl_dialect import generated_dialect
        rendering.enter_context(generated_dialect(
            1 if str(database_native.get('sql_dialect')) == '1' else 3))
        for kind in mappings.KINDS:
            try:
                rows = list(mappings.catalog_rows(
                    cursor, global_scope=kind == mappings.KINDS[1]))
                for row in rows:
                    add(kind, [], row[0], mappings.metadata(kind, row))
                mapping_catalog[kind] = {'available': True, 'count': len(rows)}
            except Exception as error:
                mapping_catalog[kind] = {
                    'available': False, 'error_type': type(error).__name__}
        database_catalog_rows = catalog_rows(
            'SELECT TRIM(TRAILING FROM D.RDB$CHARACTER_SET_NAME), '
            'TRIM(TRAILING FROM C.RDB$DEFAULT_COLLATE_NAME), D.RDB$LINGER, '
            'D.RDB$SQL_SECURITY FROM RDB$DATABASE D '
            'LEFT JOIN RDB$CHARACTER_SETS C ON '
            'C.RDB$CHARACTER_SET_NAME = D.RDB$CHARACTER_SET_NAME',
            'database defaults',
        )
        if database_catalog_rows:
            charset, collation, linger, sql_security = database_catalog_rows[0]
            database_native.update({
                'default_character_set': charset,
                'default_collation': collation,
                'linger_seconds': linger,
                'default_sql_security': (
                    None if sql_security is None else
                    'DEFINER' if bool(sql_security) else 'INVOKER'
                ),
            })
        timezone_rows = catalog_rows(
            "SELECT RDB$GET_CONTEXT('SYSTEM', 'SESSION_TIMEZONE') "
            'FROM RDB$DATABASE', 'session time zone', required=False,
        )
        if timezone_rows:
            database_native['session_time_zone'] = timezone_rows[0][0]
        replica_rows = catalog_rows(
            "SELECT RDB$GET_CONTEXT('SYSTEM', 'REPLICA_MODE') "
            'FROM RDB$DATABASE', 'database replica mode', required=False,
        )
        if replica_rows:
            database_native['replica_mode'] = replica_rows[0][0]
        database_native['backup_state_name'] = {
            '0': 'NORMAL', '1': 'STALLED', '2': 'MERGE',
        }.get(database_native.get('backup_state'))
        database_native['encryption_state_name'] = {
            '0': 'NOT_ENCRYPTED', '1': 'ENCRYPTED',
            '2': 'DECRYPT_IN_PROGRESS', '3': 'ENCRYPT_IN_PROGRESS',
        }.get(database_native.get('encryption_state'))
        for name in ('encryption_key_name', 'encryption_plugin'):
            try:
                database_native[name] = read_encryption_text(info, name)
                information_observations[name] = {'available': True}
            except Exception as error:
                database_native[name] = None
                information_observations[name] = {
                    'available': False, 'error_type': type(error).__name__,
                    'native_status_codes': list(status_codes(error)),
                }
        database_native['shutdown_mode_name'] = {
            '0': 'ONLINE', '1': 'MULTI_USER_SHUTDOWN',
            '2': 'SINGLE_USER_SHUTDOWN', '3': 'FULL_SHUTDOWN',
        }.get(database_native.get('shutdown_mode'))
        database_name = str(
            database_native.get('database_name') or 'current'
        ).rsplit(':', 1)[-1].rsplit('/', 1)[-1]
        add('database', [], database_name, database_native)
        storage_rows = catalog_rows(
            'SELECT RDB$SHADOW_NUMBER, RDB$FILE_NAME, '
            'RDB$FILE_SEQUENCE, RDB$FILE_START, RDB$FILE_LENGTH, '
            'RDB$FILE_FLAGS FROM RDB$FILES ORDER BY '
            'RDB$SHADOW_NUMBER, RDB$FILE_SEQUENCE', 'storage files')
        shadow_rows = {}
        database_files = []
        for number, filename, sequence, start, length, flags in storage_rows:
            if number is not None and number > 0:
                shadow_rows.setdefault(number, []).append(
                    (filename, sequence, start, length, flags))
            else:
                native_flags = shadows.file_flags(flags or 0, shadow=False)
                metadata = {
                    'filename': filename, 'sequence': sequence,
                    'start': start, 'length': length, 'flags_raw': flags,
                    **native_flags,
                    'file_kind': ('difference' if native_flags[
                        'difference_file'] else 'secondary-database'),
                    'catalog_authority': 'RDB$FILES',
                }
                display_name = filename
                if filename is None and native_flags['difference_file']:
                    display_name = 'Default difference file (server-selected)'
                    metadata['filename_source'] = 'Firebird default'
                database_files.append(metadata)
                add('storage-file', [], display_name, metadata)
        primary = database_native.get('database_name')
        if primary:
            primary_file = {
                'filename': primary, 'file_kind': 'primary-database',
                'catalog_authority': 'MON$DATABASE.MON$DATABASE_NAME'}
            database_files.insert(0, primary_file)
            add('storage-file', [], primary, primary_file)
        database_native['files'] = database_files
        for number, rows in shadow_rows.items():
            metadata = shadows.metadata(number, rows)
            name = str(number)
            add('shadow', [], name, metadata)
            owner_id = _catalog_resource_id('shadow', [], name)
            for file in metadata['files']:
                add('storage-file', [name], file['filename'], {
                    **file, 'file_kind': 'shadow', 'shadow_number': number,
                    'catalog_authority': 'RDB$FILES',
                    'navigator_parent_resource_id': owner_id,
                })
        cursor.execute(
            'SELECT TRIM(TRAILING FROM RDB$RELATION_NAME), RDB$VIEW_BLR, '
            'RDB$VIEW_SOURCE, '
            'RDB$DESCRIPTION, RDB$RELATION_ID, '
            'RDB$SYSTEM_FLAG, RDB$RELATION_TYPE, '
            'TRIM(TRAILING FROM RDB$SECURITY_CLASS), TRIM(TRAILING FROM '
            'RDB$EXTERNAL_FILE), '
            'TRIM(TRAILING FROM RDB$OWNER_NAME), TRIM(TRAILING FROM '
            'RDB$DEFAULT_CLASS), RDB$FLAGS, '
            'RDB$SQL_SECURITY, CASE WHEN EXISTS('
            'SELECT 1 FROM RDB$PUBLICATION_TABLES P WHERE '
            "P.RDB$PUBLICATION_NAME = 'RDB$DEFAULT' AND "
            'P.RDB$TABLE_NAME = R.RDB$RELATION_NAME) THEN 1 ELSE 0 END '
            'FROM RDB$RELATIONS R '
            'ORDER BY RDB$RELATION_NAME'
        )
        relation_types = {
            0: 'persistent',
            1: 'view',
            2: 'external',
            3: 'virtual',
            4: 'global-temporary-preserve-rows',
            5: 'global-temporary-delete-rows',
        }
        for row in [tuple(_materialize_catalog_value(value) for value in row)
                    for row in cursor.fetchall()]:
            (
                name, view_blr, view_source, description, relation_id,
                system_flag, relation_type, security_class, external_file,
                owner, default_class, flags, sql_security, publication_enabled,
            ) = row
            name = str(name).rstrip(' ')
            kind = 'view' if view_blr is not None else 'table'
            native = {
                'relation_id': relation_id,
                'relation_type': relation_type,
                'relation_type_name': relation_types.get(
                    relation_type, 'unknown'
                ),
                'system_flag': system_flag,
                'system_object': bool(system_flag),
                'owner': None if owner is None else str(owner).rstrip(' '),
                'security_class': (
                    None if security_class is None else
                    str(security_class).rstrip(' ')
                ),
                'default_security_class': (
                    None if default_class is None else
                    str(default_class).rstrip(' ')
                ),
                'external_file': (
                    None if external_file is None else
                    str(external_file).strip()
                ),
                'flags': flags,
                'sql_security': sql_security,
                'publication_enabled': bool(publication_enabled),
                'description': description,
            }
            if kind == 'view':
                native['definition'] = view_source
            add(kind, [], name, native)
        queries = (
            ('column', 'SELECT TRIM(TRAILING FROM RF.RDB$RELATION_NAME), '
             'TRIM(TRAILING FROM RF.RDB$FIELD_NAME), TRIM(TRAILING FROM '
             'RF.RDB$FIELD_SOURCE), '
             'RF.RDB$NULL_FLAG, '
             'RF.RDB$DEFAULT_SOURCE, '
             'F.RDB$FIELD_TYPE, F.RDB$FIELD_SUB_TYPE, '
             'F.RDB$FIELD_LENGTH, F.RDB$FIELD_SCALE, '
             'F.RDB$FIELD_PRECISION, F.RDB$CHARACTER_LENGTH, '
             'F.RDB$SEGMENT_LENGTH, TRIM(TRAILING FROM '
             'CS.RDB$CHARACTER_SET_NAME), '
             'TRIM(TRAILING FROM CO.RDB$COLLATION_NAME), '
             'RF.RDB$IDENTITY_TYPE, '
             'TRIM(TRAILING FROM RF.RDB$GENERATOR_NAME), '
             'F.RDB$COMPUTED_SOURCE, '
             'RF.RDB$FIELD_POSITION, '
             'RF.RDB$DESCRIPTION, G.RDB$INITIAL_VALUE, '
             'G.RDB$GENERATOR_INCREMENT '
             'FROM RDB$RELATION_FIELDS RF JOIN RDB$RELATIONS R ON '
             'R.RDB$RELATION_NAME = RF.RDB$RELATION_NAME JOIN RDB$FIELDS F '
             'ON F.RDB$FIELD_NAME = RF.RDB$FIELD_SOURCE LEFT JOIN '
             'RDB$CHARACTER_SETS CS ON CS.RDB$CHARACTER_SET_ID = '
             'F.RDB$CHARACTER_SET_ID LEFT JOIN RDB$COLLATIONS CO ON '
             'CO.RDB$CHARACTER_SET_ID = F.RDB$CHARACTER_SET_ID AND '
             'CO.RDB$COLLATION_ID = COALESCE(RF.RDB$COLLATION_ID, '
             'F.RDB$COLLATION_ID) LEFT JOIN RDB$GENERATORS G ON '
             'G.RDB$GENERATOR_NAME = RF.RDB$GENERATOR_NAME ORDER BY 1, '
             'RF.RDB$FIELD_POSITION'),
            ('index', 'SELECT TRIM(TRAILING FROM RDB$RELATION_NAME), '
             'TRIM(TRAILING FROM RDB$INDEX_NAME), RDB$UNIQUE_FLAG, '
             'RDB$INDEX_INACTIVE, '
             'RDB$INDEX_TYPE, RDB$STATISTICS, '
             'RDB$EXPRESSION_SOURCE, RDB$DESCRIPTION, RDB$CONDITION_SOURCE '
             'FROM RDB$INDICES WHERE COALESCE(RDB$SYSTEM_FLAG, 0) = 0 '
             'ORDER BY 1, 2'),
            ('constraint', 'SELECT TRIM(TRAILING FROM C.RDB$RELATION_NAME), '
             'TRIM(TRAILING FROM C.RDB$CONSTRAINT_NAME), TRIM(TRAILING '
             'FROM C.RDB$CONSTRAINT_TYPE), '
             'TRIM(TRAILING FROM C.RDB$INDEX_NAME) '
             'FROM RDB$RELATION_CONSTRAINTS C JOIN RDB$RELATIONS R ON '
             'R.RDB$RELATION_NAME = C.RDB$RELATION_NAME WHERE '
             'COALESCE(R.RDB$SYSTEM_FLAG, 0) = 0 ORDER BY 1, 2'),
        )
        query_detail_names = {
            'column': (
                'domain', 'not_null', 'default_source', 'field_type',
                'field_sub_type', 'field_length', 'field_scale',
                'field_precision', 'character_length', 'segment_length',
                'character_set', 'collation', 'identity_type',
                'generator_name', 'computed_source', 'position',
                'description', 'identity_initial_value', 'identity_increment',
            ),
            'index': (
                'unique', 'inactive', 'index_type', 'statistics',
                'expression_source', 'description', 'condition_source',
            ),
            'constraint': ('constraint_type', 'index_name'),
        }
        for kind, source in queries:
            for row in catalog_rows(source, kind + ' objects'):
                parent, name, *details = row
                add(kind, [parent], name, {
                    field: _catalog_detail(field, detail)
                    for field, detail in zip(query_detail_names[kind], details)
                })
            if 'SYSTEM_FLAG, 0) = 0' in source:
                for row in catalog_rows(source.replace(
                        'SYSTEM_FLAG, 0) = 0', 'SYSTEM_FLAG, 0) > 0'),
                        'system ' + kind + ' objects'):
                    parent, name, *details = row
                    add(kind, [parent], name, {
                        **{
                            field: _catalog_detail(field, detail)
                            for field, detail in zip(
                                query_detail_names[kind], details)
                        },
                        'system_object': True,
                    })
        simple_queries = (
            ('domain', 'SELECT TRIM(TRAILING FROM F.RDB$FIELD_NAME), '
             'F.RDB$FIELD_TYPE, '
             'F.RDB$FIELD_SUB_TYPE, F.RDB$FIELD_LENGTH, F.RDB$FIELD_SCALE, '
             'F.RDB$FIELD_PRECISION, F.RDB$CHARACTER_LENGTH, '
             'F.RDB$SEGMENT_LENGTH, TRIM(TRAILING FROM '
             'CS.RDB$CHARACTER_SET_NAME), '
             'TRIM(TRAILING FROM CO.RDB$COLLATION_NAME), F.RDB$NULL_FLAG, '
             'F.RDB$DEFAULT_SOURCE, '
             'F.RDB$VALIDATION_SOURCE, '
             'F.RDB$DESCRIPTION, F.RDB$DIMENSIONS '
             'FROM RDB$FIELDS F LEFT JOIN RDB$CHARACTER_SETS CS ON '
             'CS.RDB$CHARACTER_SET_ID = F.RDB$CHARACTER_SET_ID LEFT JOIN '
             'RDB$COLLATIONS CO ON CO.RDB$CHARACTER_SET_ID = '
             'F.RDB$CHARACTER_SET_ID AND CO.RDB$COLLATION_ID = '
             'F.RDB$COLLATION_ID WHERE COALESCE(F.RDB$SYSTEM_FLAG, 0) = 0 '
             "AND RDB$FIELD_NAME NOT STARTING WITH 'RDB$' ORDER BY 1"),
            ('sequence', 'SELECT TRIM(TRAILING FROM RDB$GENERATOR_NAME), '
             'RDB$INITIAL_VALUE, RDB$GENERATOR_INCREMENT, '
             'TRIM(TRAILING FROM RDB$OWNER_NAME), '
             'RDB$DESCRIPTION FROM '
             'RDB$GENERATORS WHERE '
             'COALESCE(RDB$SYSTEM_FLAG, 0) = 0 ORDER BY 1'),
            ('trigger', 'SELECT TRIM(TRAILING FROM RDB$TRIGGER_NAME), '
             'TRIM(TRAILING FROM RDB$RELATION_NAME), RDB$TRIGGER_TYPE, '
             'RDB$TRIGGER_INACTIVE, RDB$TRIGGER_SEQUENCE, '
             'RDB$TRIGGER_SOURCE, '
             'RDB$DESCRIPTION, RDB$SQL_SECURITY, '
             'TRIM(TRAILING FROM RDB$ENTRYPOINT), TRIM(TRAILING FROM '
             'RDB$ENGINE_NAME) '
             'FROM RDB$TRIGGERS WHERE '
             'COALESCE(RDB$SYSTEM_FLAG, 0) = 0 ORDER BY 1'),
            ('procedure', 'SELECT TRIM(TRAILING FROM RDB$PROCEDURE_NAME), '
             'TRIM(TRAILING FROM RDB$PACKAGE_NAME), '
             'RDB$PROCEDURE_SOURCE, '
             'RDB$DESCRIPTION, RDB$PROCEDURE_TYPE, '
             'RDB$VALID_BLR, RDB$SQL_SECURITY, TRIM(TRAILING FROM '
             'RDB$ENTRYPOINT), '
             'TRIM(TRAILING FROM RDB$ENGINE_NAME) FROM RDB$PROCEDURES WHERE '
             'COALESCE(RDB$SYSTEM_FLAG, 0) = 0 ORDER BY 1'),
            ('function', 'SELECT TRIM(TRAILING FROM RDB$FUNCTION_NAME), '
             'TRIM(TRAILING FROM RDB$PACKAGE_NAME), '
             'RDB$FUNCTION_SOURCE, '
             'RDB$DESCRIPTION, RDB$FUNCTION_TYPE, '
             'RDB$VALID_BLR, RDB$SQL_SECURITY, TRIM(TRAILING FROM '
             'RDB$ENTRYPOINT), '
             'TRIM(TRAILING FROM RDB$ENGINE_NAME), RDB$DETERMINISTIC_FLAG, '
             'RDB$RETURN_ARGUMENT, RDB$LEGACY_FLAG FROM RDB$FUNCTIONS WHERE '
             'COALESCE(RDB$SYSTEM_FLAG, 0) = 0 AND '
             'RDB$MODULE_NAME IS NULL ORDER BY 1'),
            ('external-function', 'SELECT TRIM(TRAILING FROM '
             'RDB$FUNCTION_NAME), '
             'TRIM(TRAILING FROM RDB$MODULE_NAME), TRIM(TRAILING FROM '
             'RDB$ENTRYPOINT), '
             'TRIM(TRAILING FROM RDB$ENGINE_NAME), TRIM(TRAILING FROM '
             'RDB$PACKAGE_NAME), '
             'RDB$DESCRIPTION, '
             'RDB$RETURN_ARGUMENT, RDB$LEGACY_FLAG FROM RDB$FUNCTIONS '
             'WHERE '
             'COALESCE(RDB$SYSTEM_FLAG, 0) = 0 AND '
             'RDB$MODULE_NAME IS NOT NULL ORDER BY 1'),
            ('blob-filter', 'SELECT TRIM(TRAILING FROM RDB$FUNCTION_NAME), '
             'RDB$INPUT_SUB_TYPE, RDB$OUTPUT_SUB_TYPE, '
             'TRIM(TRAILING FROM RDB$ENTRYPOINT), '
             'TRIM(TRAILING FROM RDB$MODULE_NAME), '
             'TRIM(TRAILING FROM RDB$OWNER_NAME), RDB$DESCRIPTION, '
             'TRIM(TRAILING FROM RDB$SECURITY_CLASS), RDB$SYSTEM_FLAG '
             'FROM RDB$FILTERS ORDER BY 1'),
            ('package', 'SELECT TRIM(TRAILING FROM RDB$PACKAGE_NAME), '
             'RDB$PACKAGE_HEADER_SOURCE, '
             'RDB$PACKAGE_BODY_SOURCE, '
             'RDB$DESCRIPTION, RDB$VALID_BODY_FLAG, '
             'RDB$SQL_SECURITY FROM RDB$PACKAGES WHERE '
             'COALESCE(RDB$SYSTEM_FLAG, 0) = 0 ORDER BY 1'),
            ('exception', 'SELECT TRIM(TRAILING FROM RDB$EXCEPTION_NAME), '
             'RDB$MESSAGE, RDB$DESCRIPTION '
             'FROM RDB$EXCEPTIONS WHERE '
             'COALESCE(RDB$SYSTEM_FLAG, 0) = 0 ORDER BY 1'),
            ('role', 'SELECT TRIM(TRAILING FROM RDB$ROLE_NAME), '
             'RDB$SYSTEM_PRIVILEGES, '
             'TRIM(TRAILING FROM RDB$OWNER_NAME), RDB$DESCRIPTION '
             'FROM RDB$ROLES WHERE COALESCE(RDB$SYSTEM_FLAG, 0) = 0 '
             'ORDER BY 1'),
            ('character-set', 'SELECT TRIM(TRAILING FROM '
             'RDB$CHARACTER_SET_NAME), '
             'RDB$BYTES_PER_CHARACTER, TRIM(TRAILING FROM '
             'RDB$DEFAULT_COLLATE_NAME), '
             'TRIM(TRAILING FROM RDB$FORM_OF_USE), RDB$SYSTEM_FLAG, '
             'TRIM(TRAILING FROM RDB$OWNER_NAME), RDB$DESCRIPTION, '
             'RDB$CHARACTER_SET_ID '
             'FROM RDB$CHARACTER_SETS ORDER BY 1'),
            ('collation', 'SELECT TRIM(TRAILING FROM RDB$COLLATION_NAME), '
             'RDB$CHARACTER_SET_ID, RDB$COLLATION_ATTRIBUTES, '
             'TRIM(TRAILING FROM RDB$BASE_COLLATION_NAME), '
             'RDB$SPECIFIC_ATTRIBUTES, '
             'RDB$SYSTEM_FLAG, TRIM(TRAILING FROM RDB$OWNER_NAME), '
             'RDB$DESCRIPTION, (SELECT TRIM(TRAILING FROM '
             'CS.RDB$CHARACTER_SET_NAME) FROM RDB$CHARACTER_SETS CS '
             'WHERE CS.RDB$CHARACTER_SET_ID = C.RDB$CHARACTER_SET_ID) '
             'FROM RDB$COLLATIONS C ORDER BY 1'),
            ('user', 'SELECT TRIM(TRAILING FROM SEC$USER_NAME), '
             'TRIM(TRAILING FROM SEC$PLUGIN) '
             'FROM SEC$USERS ORDER BY 1'),
            ('plugin', 'SELECT TRIM(TRAILING FROM RDB$CONFIG_NAME), '
             'RDB$CONFIG_VALUE FROM RDB$CONFIG WHERE '
             "UPPER(RDB$CONFIG_NAME) LIKE '%PLUGIN%' ORDER BY 1"),
            ('publication', 'SELECT TRIM(TRAILING FROM RDB$PUBLICATION_NAME), '
             'RDB$ACTIVE_FLAG FROM RDB$PUBLICATIONS ORDER BY 1'),
        )
        simple_detail_names = {
            'domain': (
                'field_type', 'field_sub_type', 'field_length',
                'field_scale', 'field_precision', 'character_length',
                'segment_length', 'character_set', 'collation', 'not_null',
                'default_source', 'validation_source', 'description',
                'dimensions',
            ),
            'sequence': ('initial_value', 'increment', 'owner', 'description'),
            'trigger': (
                'relation', 'trigger_type', 'inactive', 'position',
                'metadata_source', 'description', 'sql_security',
                'entrypoint', 'engine_name',
            ),
            'procedure': (
                'package', 'metadata_source', 'description',
                'procedure_type', 'valid_blr', 'sql_security', 'entrypoint',
                'engine_name',
            ),
            'function': (
                'package', 'metadata_source', 'description',
                'function_type', 'valid_blr', 'sql_security', 'entrypoint',
                'engine_name', 'deterministic', 'return_argument',
                'legacy',
            ),
            'external-function': (
                'module_name', 'entrypoint', 'engine_name', 'package',
                'description', 'return_argument', 'legacy',
            ),
            'blob-filter': ('input_subtype', 'output_subtype', 'entrypoint',
                            'module_name', 'owner', 'description',
                            'security_class', 'system_flag'),
            'package': (
                'header_source', 'body_source', 'description',
                'valid_body', 'sql_security',
            ),
            'exception': ('message', 'description'),
            'role': ('system_privileges', 'owner', 'description'),
            'character-set': (
                'bytes_per_character', 'default_collation', 'form_of_use',
                'system_flag', 'owner', 'description', 'character_set_id',
            ),
            'collation': (
                'character_set_id', 'attributes', 'base_collation',
                'specific_attributes',
                'system_flag', 'owner', 'description', 'character_set',
            ),
            'user': ('plugin',),
            'plugin': ('value',),
            'publication': ('active',),
        }

        def detail_value(field, detail):
            if detail is None:
                return None
            if field == 'description':
                return _catalog_detail(field, detail)
            return str(detail).rstrip(' ')

        for kind, source in simple_queries:
            for row in catalog_rows(source, kind + ' objects',
                                    required=kind not in {'user', 'plugin'}):
                native = {
                    field: detail_value(field, detail)
                    for field, detail in zip(
                        simple_detail_names[kind], row[1:]
                    )
                }
                if native.get('system_flag') not in (None, '0'):
                    native['system_object'] = True
                if kind == 'role':
                    native.update(_role_privileges(row[1]))
                if kind == 'blob-filter':
                    try:
                        statements = blob_filters.compile_operation('create', {
                            'name': row[0], **{key: native[key] for key in (
                                'input_subtype', 'output_subtype',
                                'entrypoint',
                                'module_name', 'description')}})
                        native['recreation_statements'] = statements
                        native['ddl'] = ';\n'.join(statements) + ';'
                        native['recreation_prerequisite'] = (
                            blob_filters.WARNING)
                    except RelationalClientError as error:
                        native['ddl_unavailable_reason'] = str(error)
                if kind in character_metadata.OPERATIONS:
                    try:
                        statements = character_metadata.recreation(
                            kind, row[0], native)
                        native['recreation_statements'] = statements
                        native['ddl'] = ';\n'.join(statements) + ';'
                        if kind == 'character-set':
                            native['recreation_prerequisite'] = (
                                'The character set must already exist; '
                                'this script restores its database settings.')
                    except RelationalClientError as error:
                        native['ddl_unavailable_reason'] = str(error)
                package = native.get('package') if kind in {
                    'procedure', 'function', 'external-function'} else None
                add(kind, [package] if package else [], row[0], native)

            # System objects are real catalog objects; "sys" is solely a
            # navigator folder. Keep identities and authority paths intact.
            if 'SYSTEM_FLAG, 0) = 0' not in source:
                continue
            system_source = source.replace(
                'SYSTEM_FLAG, 0) = 0', 'SYSTEM_FLAG, 0) > 0'
            ).replace("AND RDB$FIELD_NAME NOT STARTING WITH 'RDB$' ", '')
            for row in catalog_rows(system_source,
                                    'system ' + kind + ' objects'):
                native = {
                    field: detail_value(field, detail)
                    for field, detail in zip(
                        simple_detail_names[kind], row[1:]
                    )
                }
                native['system_object'] = True
                if kind == 'role':
                    native.update(_role_privileges(row[1]))
                package = native.get('package') if kind in {
                    'procedure', 'function', 'external-function'} else None
                add(kind, [package] if package else [], row[0], native)

        def routine_named(kind, name, package):
            name = str(name or '').rstrip(' ')
            package = str(package or '').rstrip(' ')
            return [
                item for item in resources.values()
                if item['resource_kind'] == kind and
                item['display_name'] == name and
                str(item.get('native', {}).get('package') or '') == package
            ]

        procedure_parameters = catalog_rows(
            'SELECT TRIM(TRAILING FROM RDB$PROCEDURE_NAME), '
            'TRIM(TRAILING FROM RDB$PACKAGE_NAME), '
            'TRIM(TRAILING FROM RDB$PARAMETER_NAME), RDB$PARAMETER_TYPE, '
            'RDB$PARAMETER_NUMBER, TRIM(TRAILING FROM P.RDB$FIELD_SOURCE), '
            'P.RDB$NULL_FLAG, '
            'P.RDB$DEFAULT_SOURCE, '
            'P.RDB$DESCRIPTION, '
            'P.RDB$PARAMETER_MECHANISM, F.RDB$FIELD_TYPE, '
            'F.RDB$FIELD_SUB_TYPE, F.RDB$FIELD_LENGTH, F.RDB$FIELD_SCALE, '
            'F.RDB$FIELD_PRECISION, F.RDB$CHARACTER_LENGTH, '
            'F.RDB$SEGMENT_LENGTH, TRIM(TRAILING FROM '
            'CS.RDB$CHARACTER_SET_NAME), '
            'TRIM(TRAILING FROM CO.RDB$COLLATION_NAME), TRIM(TRAILING '
            'FROM P.RDB$RELATION_NAME), '
            'TRIM(TRAILING FROM P.RDB$FIELD_NAME) FROM '
            'RDB$PROCEDURE_PARAMETERS P '
            'JOIN RDB$FIELDS F ON F.RDB$FIELD_NAME = P.RDB$FIELD_SOURCE '
            'LEFT JOIN RDB$CHARACTER_SETS CS ON CS.RDB$CHARACTER_SET_ID = '
            'F.RDB$CHARACTER_SET_ID LEFT JOIN RDB$COLLATIONS CO ON '
            'CO.RDB$CHARACTER_SET_ID = F.RDB$CHARACTER_SET_ID AND '
            'CO.RDB$COLLATION_ID = COALESCE(P.RDB$COLLATION_ID, '
            'F.RDB$COLLATION_ID) '
            'ORDER BY 1, 2, 4, 5', 'procedure parameters',
        )
        for row in procedure_parameters:
            procedure, package, name, mode, position, domain, not_null, \
                default_source, description, mechanism, field_type, \
                field_sub_type, field_length, field_scale, field_precision, \
                character_length, segment_length, character_set, collation, \
                relation_name, field_name = row
            parameter = {
                'name': str(name or '').rstrip(' '),
                'mode': 'input' if mode == 0 else 'output',
                'position': position,
                'domain': str(domain or '').rstrip(' ') or None,
                'not_null': not_null,
                'default_source': default_source,
                'description': description,
                'mechanism': mechanism,
                'field_type': field_type,
                'field_sub_type': field_sub_type,
                'field_length': field_length,
                'field_scale': field_scale,
                'field_precision': field_precision,
                'character_length': character_length,
                'segment_length': segment_length,
                'character_set': character_set,
                'collation': collation,
                'relation_name': relation_name,
                'field_name': field_name,
            }
            for routine in routine_named(
                    'procedure', procedure, package):
                routine.setdefault('native', {}).setdefault(
                    'parameters', []
                ).append(parameter)

        function_arguments = catalog_rows(
            'SELECT TRIM(TRAILING FROM A.RDB$FUNCTION_NAME), '
            'TRIM(TRAILING FROM A.RDB$PACKAGE_NAME), '
            'TRIM(TRAILING FROM A.RDB$ARGUMENT_NAME), '
            'A.RDB$ARGUMENT_POSITION, '
            'TRIM(TRAILING FROM A.RDB$FIELD_SOURCE), A.RDB$NULL_FLAG, '
            'A.RDB$DEFAULT_SOURCE, A.RDB$MECHANISM, '
            'A.RDB$ARGUMENT_MECHANISM, '
            'COALESCE(F.RDB$FIELD_TYPE, A.RDB$FIELD_TYPE), '
            'COALESCE(F.RDB$FIELD_SUB_TYPE, A.RDB$FIELD_SUB_TYPE), '
            'COALESCE(F.RDB$FIELD_LENGTH, A.RDB$FIELD_LENGTH), '
            'COALESCE(F.RDB$FIELD_SCALE, A.RDB$FIELD_SCALE), '
            'COALESCE(F.RDB$FIELD_PRECISION, A.RDB$FIELD_PRECISION), '
            'COALESCE(F.RDB$CHARACTER_LENGTH, A.RDB$CHARACTER_LENGTH), '
            'F.RDB$SEGMENT_LENGTH, '
            'TRIM(TRAILING FROM CS.RDB$CHARACTER_SET_NAME), '
            'TRIM(TRAILING FROM CO.RDB$COLLATION_NAME), TRIM(TRAILING '
            'FROM A.RDB$RELATION_NAME), '
            'TRIM(TRAILING FROM A.RDB$FIELD_NAME) FROM '
            'RDB$FUNCTION_ARGUMENTS A '
            'LEFT JOIN RDB$FIELDS F ON F.RDB$FIELD_NAME = '
            'A.RDB$FIELD_SOURCE LEFT JOIN RDB$CHARACTER_SETS CS ON '
            'CS.RDB$CHARACTER_SET_ID = COALESCE(F.RDB$CHARACTER_SET_ID, '
            'A.RDB$CHARACTER_SET_ID) LEFT JOIN RDB$COLLATIONS CO ON '
            'CO.RDB$CHARACTER_SET_ID = COALESCE(F.RDB$CHARACTER_SET_ID, '
            'A.RDB$CHARACTER_SET_ID) AND CO.RDB$COLLATION_ID = '
            'COALESCE(A.RDB$COLLATION_ID, F.RDB$COLLATION_ID) '
            'ORDER BY 1, 2, 4', 'function arguments',
        )
        for row in function_arguments:
            function, package, name, position, domain, not_null, \
                default_source, mechanism, argument_mechanism, field_type, \
                field_sub_type, field_length, field_scale, field_precision, \
                character_length, segment_length, character_set, collation, \
                relation_name, field_name = row
            argument = {
                'name': str(name or '').rstrip(' ') or None,
                'position': position,
                'domain': str(domain or '').rstrip(' ') or None,
                'not_null': not_null,
                'default_source': default_source,
                'mechanism': mechanism,
                'argument_mechanism': argument_mechanism,
                'field_type': field_type,
                'field_sub_type': field_sub_type,
                'field_length': field_length,
                'field_scale': field_scale,
                'field_precision': field_precision,
                'character_length': character_length,
                'segment_length': segment_length,
                'character_set': character_set,
                'collation': collation,
                'relation_name': relation_name,
                'field_name': field_name,
            }
            for kind in ('function', 'external-function'):
                for routine in routine_named(kind, function, package):
                    argument['return_value'] = str(position) == str(
                        routine.get('native', {}).get('return_argument')
                    )
                    routine.setdefault('native', {}).setdefault(
                        'parameters', []
                    ).append(argument)
        grantable_kinds = {
            'database', 'table', 'view', 'domain', 'sequence', 'procedure',
            'function', 'external-function', 'package', 'exception', 'role',
            'publication',
        }
        for (
                grantee, relation, field, privilege, grantor, grant_option,
                user_type, object_type) in catalog_rows(
            'SELECT TRIM(TRAILING FROM RDB$USER), TRIM(TRAILING FROM '
            'RDB$RELATION_NAME), '
            'TRIM(TRAILING FROM RDB$FIELD_NAME), TRIM(TRAILING FROM '
            'RDB$PRIVILEGE), '
            'TRIM(TRAILING FROM RDB$GRANTOR), RDB$GRANT_OPTION, '
            'RDB$USER_TYPE, '
            'RDB$OBJECT_TYPE FROM RDB$USER_PRIVILEGES '
            'ORDER BY 1, 2, 3, 4, 5, 7, 8', 'object privileges',
        ):
            relation = str(relation or '').rstrip(' ') or 'database'
            grantee = str(grantee).rstrip(' ')
            privilege = str(privilege).strip()
            field = str(field or '').rstrip(' ')
            membership = privilege == 'M' and object_type == 13
            default_role = bool(field) if membership else False
            if membership:
                field = ''  # Native default-role marker, not a column.
            granted_object = relation + (f'.{field}' if field else '')
            name = f'{grantee}:{privilege} on {granted_object}'
            name += f' [{user_type}; grantor {str(grantor).rstrip(' ')}]'
            # A granted object is metadata of a Firebird grant, not its
            # navigator parent.  Keeping it in display_path incorrectly
            # nested grants beneath tables, views and sequences and could
            # also make security entries disappear among object children.
            add('privilege', [], name, {
                'grantee': grantee,
                'privilege': privilege,
                'granted_object': granted_object,
                'object_name': relation,
                'default_role': default_role,
                'field': field or None,
                'grantor': str(grantor or '').rstrip(' '),
                'grant_option': grant_option,
                'user_type': user_type,
                'object_type': object_type,
            }, native_identity=[grantee, relation, field, privilege, grantor,
                                grant_option, user_type, object_type,
                                default_role])
        creation_authority = {
            'scope': 'server-security-database',
            'source': 'SEC$DB_CREATORS', 'effective_access_verified': False,
        }
        database_native['database_creation_authority'] = creation_authority
        try:
            cursor.execute('SELECT TRIM(TRAILING FROM SEC$USER), '
                           'SEC$USER_TYPE FROM SEC$DB_CREATORS ORDER BY 1, 2')
            creators = list(cursor.fetchall())
            creation_authority.update(available=True, count=len(creators))
            for grantee, user_type in creators:
                grantee = str(grantee).rstrip(' ')
                name = f'{grantee}:CREATE DATABASE [{user_type}; '
                name += 'security database]'
                add('privilege', [], name, {
                    'grantee': grantee, 'user_type': user_type,
                    'privilege': 'C', 'object_type': 21,
                    'object_name': 'SQL$DATABASE',
                    'granted_object': 'CREATE DATABASE', 'field': None,
                    'grantor': None, 'grant_option': None,
                    'catalog_source': 'SEC$DB_CREATORS',
                    'scope': 'server-security-database',
                    'grant_option_supported': False,
                    'explicit_grantor_supported': False,
                }, native_identity=['security-database-create', grantee,
                                    user_type])
        except Exception as error:
            creation_authority.update(available=False,
                                      error_type=type(error).__name__)
        dependency_rows = catalog_rows(
            'SELECT TRIM(TRAILING FROM RDB$DEPENDENT_NAME), '
            'RDB$DEPENDENT_TYPE, '
            'TRIM(TRAILING FROM RDB$DEPENDED_ON_NAME), RDB$DEPENDED_ON_TYPE, '
            'TRIM(TRAILING FROM RDB$FIELD_NAME), TRIM(TRAILING FROM '
            'RDB$PACKAGE_NAME) '
            'FROM RDB$DEPENDENCIES ORDER BY 1, 3, 5', 'object dependencies',
        )

        dependency_kinds = {
            0: {'table', 'view'}, 1: {'view'}, 2: {'trigger'},
            3: set(), 4: {'constraint'}, 5: {'procedure'},
            6: {'index'}, 7: {'exception'}, 8: {'user'},
            9: {'domain'}, 10: {'index'}, 11: {'character-set'},
            12: set(), 13: {'role'}, 14: {'sequence'},
            15: {'function', 'external-function'}, 16: {'blob-filter'},
            17: {'collation'}, 18: {'package'}, 19: {'package'},
            20: {'privilege'}, 21: {'database'},
        }
        dependency_type_names = {
            0: 'relation', 1: 'view', 2: 'trigger', 3: 'computed field',
            4: 'validation', 5: 'procedure', 6: 'expression index',
            7: 'exception', 8: 'user', 9: 'domain', 10: 'index',
            11: 'character set', 12: 'user group', 13: 'SQL role',
            14: 'sequence', 15: 'function', 16: 'BLOB filter',
            17: 'collation', 18: 'package header', 19: 'package body',
            20: 'privilege', 21: 'database', 34: 'job',
            35: 'tablespace', 37: 'partial index condition',
        }

        def objects_named(name, object_type=None, package=None):
            normalized = str(name or '').rstrip(' ')
            kinds = dependency_kinds.get(object_type)
            if object_type is not None and kinds is None:
                return []
            return [
                item for item in resources.values()
                if item['display_name'] == normalized and (
                    kinds is None or item['resource_kind'] in kinds
                ) and (object_type not in {5, 15} or
                       str(item.get('native', {}).get('package') or '') ==
                       str(package or '').rstrip(' '))
            ]

        for (
                dependent_name, dependent_type, depended_name,
                depended_type, field_name, package_name) in dependency_rows:
            dependency = {
                'object_name': str(depended_name or '').rstrip(' '),
                'object_type_code': depended_type,
                'object_type': dependency_type_names.get(
                    depended_type, f'object type {depended_type}'
                ),
                'field_name': str(field_name or '').rstrip(' ') or None,
                'package_name': str(package_name or '').rstrip(' ') or None,
            }
            dependent = {
                'object_name': str(dependent_name or '').rstrip(' '),
                'object_type_code': dependent_type,
                'object_type': dependency_type_names.get(
                    dependent_type, f'object type {dependent_type}'
                ),
                'field_name': str(field_name or '').rstrip(' ') or None,
                'package_name': str(package_name or '').rstrip(' ') or None,
            }
            dependent_targets = objects_named(dependent_name, dependent_type)
            if dependent_type == 3:
                # Computed view/table expressions depend through an implicit
                # RDB$FIELDS record, not the relation's display name. Preserve
                # that native record and resolve its real column/owner links.
                computed_columns = [item for item in resources.values() if
                                    item['resource_kind'] == 'column' and
                                    item.get('native', {}).get('domain') ==
                                    str(dependent_name or '').rstrip(' ')]
                dependent_targets = list(computed_columns)
                for column in computed_columns:
                    for owner in resources.values():
                        if (owner['resource_kind'] in {'table', 'view'} and
                                owner['display_path'] ==
                                column['display_path'][:-1] and
                                owner not in dependent_targets):
                            dependent_targets.append(owner)
                dependent['dependent_resolution'] = {
                    'state': 'resolved' if computed_columns else 'unresolved',
                    'authority': 'RDB$RELATION_FIELDS.RDB$FIELD_SOURCE',
                    'resources': [{key: item[key] for key in (
                        'resource_id', 'resource_kind', 'display_name',
                        'display_path')} for item in dependent_targets],
                }
                dependency['via_computed_field'] = str(
                    dependent_name or '').rstrip(' ')
            for item in dependent_targets:
                item.setdefault('native', {}).setdefault(
                    'dependencies', []
                ).append(dependency)
            for item in objects_named(depended_name, depended_type,
                                      package_name):
                item.setdefault('native', {}).setdefault(
                    'dependents', []
                ).append(dependent)

        for index_name, field_name, position in catalog_rows(
            'SELECT TRIM(TRAILING FROM RDB$INDEX_NAME), TRIM(TRAILING '
            'FROM RDB$FIELD_NAME), '
            'RDB$FIELD_POSITION FROM RDB$INDEX_SEGMENTS ORDER BY 1, 3',
            'index segments',
        ):
            for index in objects_named(index_name):
                if index['resource_kind'] == 'index':
                    index.setdefault('native', {}).setdefault(
                        'segments', []
                    ).append({
                        'field_name': str(field_name).rstrip(' '),
                        'position': position,
                    })

        for (
                constraint_name, referenced_relation, update_rule,
                delete_rule, referenced_index) in catalog_rows(
                    'SELECT TRIM(TRAILING FROM RC.RDB$CONSTRAINT_NAME), '
                    'TRIM(TRAILING FROM UQ.RDB$RELATION_NAME), '
                    'TRIM(TRAILING FROM RC.RDB$UPDATE_RULE), '
                    'TRIM(TRAILING FROM RC.RDB$DELETE_RULE), '
                    'TRIM(TRAILING FROM UQ.RDB$INDEX_NAME) '
                    'FROM RDB$REF_CONSTRAINTS RC JOIN '
                    'RDB$RELATION_CONSTRAINTS UQ ON '
                    'UQ.RDB$CONSTRAINT_NAME = RC.RDB$CONST_NAME_UQ '
                    'ORDER BY 1', 'referential constraints'):
            for constraint in objects_named(constraint_name):
                if constraint['resource_kind'] == 'constraint':
                    constraint.setdefault('native', {}).update({
                        'referenced_relation': str(
                            referenced_relation or ''
                        ).rstrip(' ') or None,
                        'referenced_index': str(
                            referenced_index or ''
                        ).rstrip(' ') or None,
                        'update_rule': str(update_rule or '').strip() or None,
                        'delete_rule': str(delete_rule or '').strip() or None,
                    })

        for constraint_name, check_source in catalog_rows(
            'SELECT TRIM(TRAILING FROM CC.RDB$CONSTRAINT_NAME), '
            'T.RDB$TRIGGER_SOURCE '
            'FROM RDB$CHECK_CONSTRAINTS CC JOIN RDB$TRIGGERS T ON '
            'T.RDB$TRIGGER_NAME = CC.RDB$TRIGGER_NAME ORDER BY 1',
            'check constraint definitions',
        ):
            for constraint in objects_named(constraint_name):
                if constraint['resource_kind'] == 'constraint':
                    constraint.setdefault('native', {})['check_source'] = (
                        None if check_source is None else
                        str(check_source).strip()
                    )

        for constraint_name, column_name in catalog_rows(
            'SELECT TRIM(TRAILING FROM CC.RDB$CONSTRAINT_NAME), '
            'TRIM(TRAILING FROM CC.RDB$TRIGGER_NAME) '
            'FROM RDB$CHECK_CONSTRAINTS CC JOIN RDB$RELATION_CONSTRAINTS RC '
            'ON RC.RDB$CONSTRAINT_NAME = CC.RDB$CONSTRAINT_NAME '
            "WHERE RC.RDB$CONSTRAINT_TYPE = 'NOT NULL' ORDER BY 1",
            'not-null constraint columns',
        ):
            for constraint in objects_named(constraint_name):
                if constraint['resource_kind'] == 'constraint':
                    constraint.setdefault('native', {})['not_null_column'] = (
                        str(column_name).rstrip(' '))

        for item in tuple(resources.values()):
            if item['resource_kind'] != 'privilege':
                continue
            grant = dict(item.get('native', {}))
            granted_object = grant.get('granted_object') or ''
            object_name, _, field_name = granted_object.partition('.')
            object_name = grant.get('object_name') or object_name
            field_name = grant.get('field') or ''
            privilege_target_kinds = {
                0: {'table', 'view'}, 5: {'procedure'}, 7: {'exception'},
                9: {'domain'}, 13: {'role'}, 14: {'sequence'},
                15: {'function', 'external-function'}, 16: {'blob-filter'},
                17: {'collation'},
                18: {'package'}, 21: {'database'},
            }
            target_kinds = privilege_target_kinds.get(
                grant.get('object_type'), set()
            )
            targets = (
                [target for target in resources.values()
                 if target['resource_kind'] == 'database']
                if grant.get('object_type') == 21 else
                [target for target in objects_named(object_name)
                 if target['resource_kind'] in target_kinds and
                 not (grant.get('object_type') in {5, 15} and
                      target.get('native', {}).get('package'))]
            )
            server_creation = grant.get('catalog_source') == 'SEC$DB_CREATORS'
            if server_creation:
                targets = []
            field_targets = [
                target for target in objects_named(field_name)
                if target['resource_kind'] == 'column' and
                target['display_path'][-2] == object_name
            ] if field_name and grant.get('object_type') == 0 else []
            resolved = field_targets if field_name else targets
            ddl_class = {
                22: 'TABLE', 23: 'VIEW', 24: 'PROCEDURE', 25: 'FUNCTION',
                26: 'PACKAGE', 27: 'SEQUENCE', 28: 'DOMAIN', 29: 'EXCEPTION',
                30: 'ROLE', 31: 'CHARACTER SET', 32: 'COLLATION', 33: 'FILTER',
            }.get(grant.get('object_type'))
            resolution = {
                'state': 'resolved' if len(resolved) == 1 else
                'ambiguous' if resolved else 'unresolved',
                'resource_ids': [target['resource_id'] for target in resolved],
                'authority': 'native-catalog-name-matching',
                'effective_access_verified': False,
            }
            if server_creation:
                resolution.update(state='server-scope',
                                  scope='database-creation')
            elif ddl_class and not field_name:
                resolution.update(state='class-scope', ddl_class=ddl_class)
            elif resolution['state'] != 'resolved':
                resolution['warning'] = (
                    'The native grant target does not resolve uniquely in '
                    'the visible catalog. This is not evidence that access '
                    'is absent. A renamed Firebird column can retain its '
                    'old grant name; verify effective access before changing '
                    'security.')
            grant['target_resolution'] = resolution
            item.setdefault('native', {})['target_resolution'] = resolution
            if resolution.get('warning'):
                item['native']['catalog_warnings'] = [resolution['warning']]
            for target in targets:
                if resolution.get('warning'):
                    messages = target.setdefault('native', {}).setdefault(
                        'catalog_warnings', [])
                    if resolution['warning'] not in messages:
                        messages.append(resolution['warning'])
                target.setdefault('native', {}).setdefault(
                    'privileges', []
                ).append(grant)
            for target in field_targets:
                target.setdefault('native', {}).setdefault(
                    'privileges', []
                ).append(grant)
            principal_kind = {
                8: 'user', 13: 'role',
            }.get(grant.get('user_type'))
            for principal in objects_named(grant.get('grantee')):
                if principal['resource_kind'] == principal_kind:
                    principal.setdefault('native', {}).setdefault(
                        'privileges', []
                    ).append(grant)

        for item in resources.values():
            native = item.setdefault('native', {})
            package = native.get('package')
            if item['resource_kind'] not in {'procedure', 'function'} or (
                    not package):
                continue
            owners = [value for value in objects_named(package)
                      if value['resource_kind'] == 'package']
            if len(owners) == 1:
                native['package_privileges'] = {
                    'package': package,
                    'scope': 'Entire package, not an individual routine',
                    'privileges': list(owners[0].get('native', {}).get(
                        'privileges', [])),
                    'effective_access_verified': False,
                }

        def identifier(value):
            from .ddl_dialect import identifier_sql
            return identifier_sql(str(value))

        def numeric(value, default=None):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        field_dimensions = {}
        for field_name, position, lower, upper in catalog_rows(
            'SELECT TRIM(TRAILING FROM RDB$FIELD_NAME), RDB$DIMENSION, '
            'RDB$LOWER_BOUND, RDB$UPPER_BOUND FROM RDB$FIELD_DIMENSIONS '
            'ORDER BY 1, 2', 'array dimensions',
        ):
            field_dimensions.setdefault(
                str(field_name).rstrip(' '), []
            ).append({
                'position': position,
                'lower_bound': lower,
                'upper_bound': upper,
            })

        def data_type(field, include_domain=True):
            """Render an exact Firebird field type or fail closed."""
            domain = str(field.get('domain') or '').rstrip(' ')
            if include_domain and domain and not domain.startswith(
                    'RDB$'):
                return identifier(domain)
            field_type = numeric(field.get('field_type'))
            subtype = numeric(field.get('field_sub_type'), 0)
            raw_scale = numeric(field.get('field_scale'), 0)
            scale = abs(raw_scale)
            precision = numeric(field.get('field_precision'))
            length = numeric(field.get('character_length'))
            if length is None:
                length = numeric(field.get('field_length'))

            if field_type == 27 and raw_scale < 0:
                # Legacy scaled-double metadata does not retain a declared
                # precision. Recreating a guessed NUMERIC under dialect 3
                # would even change its physical storage type.
                return None
            elif field_type in {7, 8, 16, 26}:
                if raw_scale == 0 and subtype == 0:
                    value = {
                        7: 'SMALLINT', 8: 'INTEGER', 16: 'BIGINT',
                        26: 'INT128',
                    }[field_type]
                else:
                    maximum = {7: 4, 8: 9, 16: 18, 26: 38}[field_type]
                    if (subtype not in {1, 2} or precision is None or
                            not 1 <= precision <= maximum or raw_scale > 0 or
                            scale > precision):
                        return None
                    keyword = 'DECIMAL' if subtype == 2 else 'NUMERIC'
                    value = f'{keyword}({precision},{scale})'
            elif field_type in {14, 37, 40}:
                if length is None:
                    return None
                keyword = {
                    14: 'BINARY' if subtype == 1 else 'CHAR',
                    37: 'VARBINARY' if subtype == 1 else 'VARCHAR',
                    40: 'CSTRING',
                }[field_type]
                value = f'{keyword}({length})'
            elif field_type == 261:
                blob_subtype = {
                    0: 'BINARY', 1: 'TEXT',
                }.get(subtype, str(subtype))
                value = f'BLOB SUB_TYPE {blob_subtype}'
                segment = numeric(field.get('segment_length'))
                if segment:
                    value += f' SEGMENT SIZE {segment}'
            else:
                value = {
                    9: 'QUAD', 10: 'FLOAT', 11: 'D_FLOAT', 12: 'DATE',
                    13: 'TIME', 23: 'BOOLEAN', 24: 'DECFLOAT(16)',
                    25: 'DECFLOAT(34)', 27: 'DOUBLE PRECISION',
                    28: 'TIME WITH TIME ZONE',
                    29: 'TIMESTAMP WITH TIME ZONE', 35: 'TIMESTAMP',
                }.get(field_type)
                if value is None:
                    return None

            charset = str(field.get('character_set') or '').rstrip(' ')
            dimensions = field_dimensions.get(domain, [])
            if dimensions and not (
                    include_domain and domain and
                    not domain.startswith('RDB$')):
                value += '[' + ', '.join(
                    f'{dimension["lower_bound"]}:'
                    f'{dimension["upper_bound"]}'
                    for dimension in dimensions
                ) + ']'
            if (charset and field_type in {14, 37, 40, 261} and
                    not (field_type in {14, 37} and subtype == 1)):
                value += f' CHARACTER SET {identifier(charset)}'
            return value

        def parameter_definition(parameter, include_name=True):
            relation = str(parameter.get('relation_name') or '').rstrip(' ')
            field_name = str(parameter.get('field_name') or '').rstrip(' ')
            mechanism = numeric(
                parameter.get('argument_mechanism'),
                numeric(parameter.get('mechanism'), 0),
            )
            if relation and field_name:
                value = (
                    f'COLUMN {identifier(relation)}.{identifier(field_name)}'
                )
                if mechanism == 1:
                    value = f'TYPE OF {value}'
            else:
                value = data_type(parameter)
                if value is None:
                    return None
                if mechanism == 1:
                    value = f'TYPE OF {value}'
            if include_name:
                name = str(parameter.get('name') or '').rstrip(' ')
                if not name:
                    return None
                value = f'{identifier(name)} {value}'
            if str(parameter.get('not_null')) == '1':
                value += ' NOT NULL'
            collation = str(parameter.get('collation') or '').rstrip(' ')
            if collation:
                value += f' COLLATE {identifier(collation)}'
            default = str(parameter.get('default_source') or '').strip()
            if include_name and default:
                value += f' {default}'
            return value

        def sql_security(value):
            # Native BOOLEAN catalog values become text in simple metadata.
            # Do not discard True/False as unknown or treat "False" as truthy.
            flag = (value.upper() == 'TRUE' if isinstance(value, str) and
                    value.upper() in {'TRUE', 'FALSE'} else numeric(value))
            if flag is None:
                return None
            return 'SQL SECURITY DEFINER' if flag else 'SQL SECURITY INVOKER'

        def trigger_action(value):
            trigger_type = numeric(value)
            if trigger_type is None:
                return None
            trigger_mask = 3 << 13
            trigger_family = trigger_type & trigger_mask
            if trigger_family == 0:
                prefix = 'AFTER' if (trigger_type + 1) & 1 else 'BEFORE'
                suffixes = []
                names = ('', 'INSERT', 'UPDATE', 'DELETE')
                for slot in range(1, 4):
                    suffix = ((trigger_type + 1) >> (slot * 2 - 1)) & 3
                    if suffix:
                        suffixes.append(names[suffix])
                return None if not suffixes else (
                    f'{prefix} ' + ' OR '.join(suffixes)
                )
            if trigger_family == 1 << 13:
                event = trigger_type & ~(1 << 13)
                events = (
                    'CONNECT', 'DISCONNECT', 'TRANSACTION START',
                    'TRANSACTION COMMIT', 'TRANSACTION ROLLBACK',
                )
                return f'ON {events[event]}' if event < len(events) else None
            if trigger_family != 2 << 13:
                return None
            unsigned = trigger_type & ((1 << 64) - 1)
            prefix = 'AFTER' if unsigned & 1 else 'BEFORE'
            any_mask = ((1 << 63) - 1) & ~trigger_mask & ~1
            if unsigned & any_mask == any_mask:
                return f'{prefix} ANY DDL STATEMENT'
            action_names = {
                1: 'CREATE TABLE', 2: 'ALTER TABLE', 3: 'DROP TABLE',
                4: 'CREATE PROCEDURE', 5: 'ALTER PROCEDURE',
                6: 'DROP PROCEDURE', 7: 'CREATE FUNCTION',
                8: 'ALTER FUNCTION', 9: 'DROP FUNCTION',
                10: 'CREATE TRIGGER', 11: 'ALTER TRIGGER',
                12: 'DROP TRIGGER', 16: 'CREATE EXCEPTION',
                17: 'ALTER EXCEPTION', 18: 'DROP EXCEPTION',
                19: 'CREATE VIEW', 20: 'ALTER VIEW', 21: 'DROP VIEW',
                22: 'CREATE DOMAIN', 23: 'ALTER DOMAIN',
                24: 'DROP DOMAIN', 25: 'CREATE ROLE', 26: 'ALTER ROLE',
                27: 'DROP ROLE', 28: 'CREATE INDEX', 29: 'ALTER INDEX',
                30: 'DROP INDEX', 31: 'CREATE SEQUENCE',
                32: 'ALTER SEQUENCE', 33: 'DROP SEQUENCE',
                34: 'CREATE USER', 35: 'ALTER USER', 36: 'DROP USER',
                37: 'CREATE COLLATION', 38: 'DROP COLLATION',
                39: 'ALTER CHARACTER SET', 40: 'CREATE PACKAGE',
                41: 'ALTER PACKAGE', 42: 'DROP PACKAGE',
                43: 'CREATE PACKAGE BODY', 44: 'DROP PACKAGE BODY',
                45: 'CREATE MAPPING', 46: 'ALTER MAPPING',
                47: 'DROP MAPPING',
            }
            actions = [
                action_names[position] for position in range(1, 64)
                if unsigned & (1 << position) and
                position not in {13, 14, 15} and
                position in action_names
            ]
            return None if not actions else f'{prefix} ' + ' OR '.join(actions)

        def index_segments(index_name):
            index = next((
                candidate for candidate in objects_named(index_name)
                if candidate['resource_kind'] == 'index'
            ), None)
            return [] if index is None else [
                segment['field_name']
                for segment in index.get('native', {}).get('segments', [])
            ]

        def constraint_clause(item):
            native = item.get('native', {})
            kind = str(native.get('constraint_type') or '').strip().upper()
            raw_name = str(item['display_name']).rstrip(' ')
            prefix = f'CONSTRAINT {identifier(raw_name)} '
            fields = index_segments(native.get('index_name'))
            field_list = ', '.join(identifier(field) for field in fields)
            backing = ''
            if kind in {'PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY'}:
                index = next((candidate.get('native', {}) for candidate in
                              objects_named(native.get('index_name')) if
                              candidate['resource_kind'] == 'index'), None)
                if index is None or not fields:
                    return None
                raw_direction = index.get('index_type')
                direction = (0 if raw_direction is None else
                             numeric(raw_direction))
                if direction not in (0, 1):
                    return None
                backing = (' USING ' + ('DESCENDING' if direction else
                                        'ASCENDING') + ' INDEX ' +
                           identifier(native['index_name']))
            if kind == 'PRIMARY KEY':
                return f'{prefix}PRIMARY KEY ({field_list}){backing}'
            if kind == 'UNIQUE':
                return f'{prefix}UNIQUE ({field_list}){backing}'
            if kind == 'FOREIGN KEY':
                referenced = index_segments(native.get('referenced_index'))
                referenced_list = ', '.join(
                    identifier(field) for field in referenced
                )
                value = (
                    f'{prefix}FOREIGN KEY ({field_list}) '
                    f'REFERENCES {identifier(native["referenced_relation"])} '
                    f'({referenced_list})'
                )
                if native.get('update_rule') and str(
                        native['update_rule']).upper() != 'RESTRICT':
                    value += f' ON UPDATE {native["update_rule"]}'
                if native.get('delete_rule') and str(
                        native['delete_rule']).upper() != 'RESTRICT':
                    value += f' ON DELETE {native["delete_rule"]}'
                return value + backing
            if kind == 'CHECK' and native.get('check_source'):
                source = str(native['check_source']).strip()
                if not source.upper().startswith('CHECK'):
                    source = f'CHECK ({source})'
                return f'{prefix}{source}'
            if kind == 'NOT NULL':
                return None
            return None

        def routine_parts(native, mode=None):
            parameters = sorted(
                native.get('parameters', []),
                key=lambda value: numeric(value.get('position'), 0),
            )
            if mode is not None:
                parameters = [
                    parameter for parameter in parameters
                    if parameter.get('mode') == mode
                ]
            rendered = [
                parameter_definition(parameter) for parameter in parameters
            ]
            return None if any(value is None for value in rendered) else (
                rendered
            )

        def udf_parameter(parameter):
            # Legacy UDF grammar accepts only bare BLOB, not the table-field
            # SUB_TYPE / SEGMENT SIZE / CHARACTER SET clauses.
            rendered_type = ('BLOB' if numeric(parameter.get('field_type')) ==
                             261 else data_type(parameter))
            if rendered_type is None:
                return None
            mechanism = numeric(parameter.get('mechanism'))
            if mechanism not in {-2, -1, 0, 1, 2, 3, 4, 5}:
                return None
            absolute = abs(mechanism)
            suffix = {
                0: ' BY VALUE', 1: '', 2: ' BY DESCRIPTOR',
                3: '', 4: ' BY SCALAR_ARRAY', 5: ' NULL',
            }.get(absolute)
            if suffix is None:
                return None
            if mechanism < 0:
                suffix += ' FREE_IT'
            return rendered_type + suffix

        constraint_indexes = {
            str(item.get('native', {}).get('index_name') or '').rstrip(' ')
            for item in resources.values()
            if item['resource_kind'] == 'constraint'
        }

        for item in resources.values():
            kind = item['resource_kind']
            name = item['display_name']
            native = item.setdefault('native', {})
            if kind == 'role':
                if name == 'RDB$ADMIN':
                    native['auto_admin_mapping'] = _admin_mapping_state(cursor)
                memberships = [grant for grant in native.get('privileges', [])
                               if grant.get('privilege') == 'M']
                native['memberships'] = memberships
                native['membership_recreation_statements'] = []
                for grant in memberships:
                    member_kind = {8: 'USER', 13: 'ROLE'}.get(
                        grant.get('user_type'))
                    if member_kind is None or not grant.get('grantor'):
                        native['membership_recreation_unavailable_reason'] = (
                            'Membership principal type or grantor is unknown.')
                        native['membership_recreation_statements'] = []
                        break
                    statement = ADMINISTRATION._compile_firebird_role({
                        'operation_id': 'grant',
                        'target_resource': {'display_name': name},
                        'draft': {'member': grant['grantee'],
                                  'member_kind': member_kind,
                                  'default_role': grant['default_role'],
                                  'admin_option': grant['grant_option'] == 2,
                                  'grantor': grant['grantor']}})
                    native['membership_recreation_statements'].append(
                        statement['source'])
                native['membership_recreation_requirements'] = (
                    'Create roles and principals first. Replay as a user '
                    'authorized for GRANTED BY; each recorded grantor must '
                    'already have authority to grant the role. These '
                    'statements are separate from object creation DDL.')
                native['recreation_requirements'] = {
                    'execute_as_user': native.get('owner'),
                    'requires_privileged_role_creation_authority': bool(
                        native.get('system_privileges')),
                    'ownership_transfer_supported': False,
                    'explanation': 'Firebird assigns role ownership to the '
                    'creating user. Recreate through that user to preserve '
                    'ownership; ALTER ROLE has no OWNER TO clause.',
                }
                if native.get('system_object'):
                    native['ddl_unavailable_reason'] = (
                        'Firebird creates this built-in role with the '
                        'database; it cannot be recreated with CREATE ROLE.')
                elif native.get('unknown_system_privilege_bits'):
                    native['ddl_unavailable_reason'] = (
                        'Unknown native system privilege bits; recreation '
                        'would lose privileges.')
                else:
                    privileges = native.get('system_privileges', [])
                    suffix = (' SET SYSTEM PRIVILEGES TO ' +
                              ', '.join(privileges)) if privileges else ''
                    statements = [f'CREATE ROLE {identifier(name)}{suffix}']
                    if native.get('description') is not None:
                        comment = native['description'].replace("'", "''")
                        statements.append(
                            f'COMMENT ON ROLE {identifier(name)} '
                            f"IS '{comment}'")
                    native['recreation_statements'] = statements
                    native['ddl'] = ';\n'.join(statements) + ';'
            elif kind == 'sequence':
                initial = native.get('initial_value')
                increment = native.get('increment')
                if increment is not None:
                    native['increment'] = int(increment)
                if initial is not None:
                    native['initial_value'] = str(initial)
                clauses = []
                if initial not in (None, ''):
                    clauses.append(f'START WITH {initial}')
                if increment not in (None, ''):
                    clauses.append(f'INCREMENT BY {increment}')
                statements = [' '.join([
                    'CREATE SEQUENCE', identifier(name), *clauses,
                ])]
                if native.get('description') is not None:
                    comment = str(native['description']).replace("'", "''")
                    statements.append(
                        f'COMMENT ON SEQUENCE {identifier(name)} '
                        f"IS '{comment}'")
                native['recreation_statements'] = statements
                native['ddl'] = ';\n'.join(statements) + ';'
                # Only inspect the selected sequence, never consume a value
                # or query every generator while expanding a catalog branch.
                if request.get('resource_id') == item['resource_id']:
                    native['state'] = _sequence_state(cursor, name)
            elif kind == 'exception' and native.get('message') is not None:
                message = str(native['message']).replace("'", "''")
                native['ddl'] = (
                    f"CREATE EXCEPTION {identifier(name)} '{message}';"
                )
            elif kind == 'package' and native.get('header_source'):
                security = sql_security(native.get('sql_security'))
                native['package_sql_security'] = (
                    security.rsplit(' ', 1)[-1] if security else 'INHERIT')
                native['child_definition_tasks'] = {
                    member: {'operation_id': 'alter',
                             'label': f'New {member} in package header'}
                    for member in ('function', 'procedure')}
                header = f'CREATE PACKAGE {identifier(name)}'
                if security:
                    header += f' {security}'
                header += (
                    f' AS\n{str(native["header_source"]).strip()}'
                )
                statements = [header]
                body = str(native.get('body_source') or '').strip()
                if body:
                    statements.append(
                        f'CREATE PACKAGE BODY {identifier(name)} AS\n{body}')
                if native.get('description') is not None:
                    comment = str(native['description']).replace("'", "''")
                    statements.append(
                        f'COMMENT ON PACKAGE {identifier(name)} '
                        f"IS '{comment}'")
                native['recreation_statements'] = statements
                native['ddl'] = ';\n\n'.join(statements) + ';'
            elif kind == 'domain':
                rendered_type = data_type(native, include_domain=False)
                if rendered_type is not None:
                    definition = (
                        f'CREATE DOMAIN {identifier(name)} AS {rendered_type}'
                    )
                    default = str(
                        native.get('default_source') or ''
                    ).strip()
                    if default:
                        definition += f' {default}'
                    if str(native.get('not_null')) == '1':
                        definition += ' NOT NULL'
                    validation = str(
                        native.get('validation_source') or ''
                    ).strip()
                    if validation:
                        definition += f' {validation}'
                    collation = str(native.get('collation') or '').rstrip(' ')
                    if collation:
                        definition += f' COLLATE {identifier(collation)}'
                    native['ddl'] = definition + ';'
            elif kind == 'trigger' and native.get('metadata_source'):
                action = trigger_action(native.get('trigger_type'))
                if action:
                    definition = f'CREATE TRIGGER {identifier(name)}'
                    relation = str(native.get('relation') or '').rstrip(' ')
                    if relation:
                        definition += f' FOR {identifier(relation)}'
                    definition += (
                        '\n' + (
                            'INACTIVE' if str(native.get('inactive')) == '1'
                            else 'ACTIVE'
                        ) + f' {action} POSITION '
                        f'{numeric(native.get("position"), 0)}'
                    )
                    security = sql_security(native.get('sql_security'))
                    if security:
                        definition += f'\n{security}'
                    entrypoint = str(native.get('entrypoint') or '').strip()
                    engine = str(native.get('engine_name') or '').rstrip(' ')
                    if entrypoint:
                        entrypoint = entrypoint.replace("'", "''")
                        definition += f"\nEXTERNAL NAME '{entrypoint}'"
                    if engine:
                        definition += f'\nENGINE {identifier(engine)}'
                    source = str(native['metadata_source']).strip()
                    definition += (
                        f'\n{source};' if source.upper().startswith('AS ') else
                        f'\nAS\n{source};'
                    )
                    native['ddl'] = definition
            elif kind == 'procedure' and not native.get('package'):
                inputs = routine_parts(native, 'input')
                outputs = routine_parts(native, 'output')
                source = str(native.get('metadata_source') or '').strip()
                if inputs is not None and outputs is not None and source:
                    definition = f'CREATE PROCEDURE {identifier(name)}'
                    if inputs:
                        definition += ' (' + ', '.join(inputs) + ')'
                    if outputs:
                        definition += ' RETURNS (' + ', '.join(outputs) + ')'
                    entrypoint = str(native.get('entrypoint') or '').strip()
                    engine = str(native.get('engine_name') or '').rstrip(' ')
                    if entrypoint:
                        entrypoint = entrypoint.replace("'", "''")
                        definition += f"\nEXTERNAL NAME '{entrypoint}'"
                    security = sql_security(native.get('sql_security'))
                    if security:
                        definition += f'\n{security}'
                    if engine:
                        definition += f'\nENGINE {identifier(engine)}'
                    definition += f'\nAS\n{source};'
                    native['ddl'] = definition
            elif kind == 'function' and not native.get('package'):
                parameters = sorted(
                    native.get('parameters', []),
                    key=lambda value: numeric(value.get('position'), 0),
                )
                inputs = [
                    parameter_definition(parameter)
                    for parameter in parameters
                    if not parameter.get('return_value')
                ]
                return_values = [
                    parameter_definition(parameter, include_name=False)
                    for parameter in parameters
                    if parameter.get('return_value')
                ]
                source = str(native.get('metadata_source') or '').strip()
                if not any(value is None for value in inputs) and len(
                        return_values) == 1 and return_values[0] and source:
                    definition = (
                        f'CREATE FUNCTION {identifier(name)} (' +
                        ', '.join(inputs) + ') RETURNS ' + return_values[0]
                    )
                    if str(native.get('deterministic')) == '1':
                        definition += '\nDETERMINISTIC'
                    entrypoint = str(native.get('entrypoint') or '').strip()
                    engine = str(native.get('engine_name') or '').rstrip(' ')
                    if entrypoint:
                        entrypoint = entrypoint.replace("'", "''")
                        definition += f"\nEXTERNAL NAME '{entrypoint}'"
                    security = sql_security(native.get('sql_security'))
                    if security:
                        definition += f'\n{security}'
                    if engine:
                        definition += f'\nENGINE {identifier(engine)}'
                    definition += f'\nAS\n{source};'
                    native['ddl'] = definition
            elif kind == 'external-function':
                parameters = sorted(
                    native.get('parameters', []),
                    key=lambda value: numeric(value.get('position'), 0),
                )
                return_argument = numeric(native.get('return_argument'), 0)
                arguments = []
                return_definition = None
                for parameter in parameters:
                    rendered = udf_parameter(parameter)
                    if rendered is None:
                        arguments = None
                        break
                    position = numeric(parameter.get('position'), 0)
                    if position == return_argument:
                        return_definition = (
                            rendered if position == 0 else
                            f'PARAMETER {position}'
                        )
                        if position == 0:
                            continue
                    arguments.append(rendered)
                if arguments is not None and return_definition:
                    module = str(native.get('module_name') or '').replace(
                        "'", "''"
                    )
                    entrypoint = str(native.get('entrypoint') or '').replace(
                        "'", "''"
                    )
                    if module and entrypoint:
                        declaration = (
                            f'DECLARE EXTERNAL FUNCTION {identifier(name)} '
                            f'{", ".join(arguments)} RETURNS '
                            f'{return_definition}\nENTRY_POINT '
                            f"'{entrypoint}' MODULE_NAME '{module}'"
                        )
                        statements = [declaration]
                        if native.get('description') is not None:
                            statements.extend(
                                external_functions.compile_operation(
                                    'comment', {
                                        'description': native['description']},
                                    {'display_name': name}))
                        native['recreation_statements'] = statements
                        native['ddl'] = ';\n'.join(statements) + ';'
            elif kind == 'index':
                relation = item['display_path'][-2] if len(
                    item['display_path']
                ) > 1 else None
                if relation:
                    native['state'] = {
                        'active': native.get('inactive') in {
                            None, '0', 0,
                        },
                    }
                    components = ['CREATE']
                    if str(native.get('unique')) == '1':
                        components.append('UNIQUE')
                    if str(native.get('index_type')) == '1':
                        components.append('DESCENDING')
                    components.extend([
                        'INDEX', identifier(name), 'ON', identifier(relation),
                    ])
                    expression = str(
                        native.get('expression_source') or ''
                    ).strip()
                    if expression:
                        if expression.upper().startswith('COMPUTED BY'):
                            target = expression
                        elif expression.startswith('('):
                            target = f'COMPUTED BY {expression}'
                        else:
                            target = f'COMPUTED BY ({expression})'
                    else:
                        segments = native.get('segments', [])
                        target = '(' + ', '.join(
                            identifier(segment['field_name'])
                            for segment in segments
                        ) + ')'
                    if (expression or native.get('segments')) and \
                            name not in constraint_indexes:
                        condition = str(
                            native.get('condition_source') or '').strip()
                        if condition:
                            target += '\n' + (
                                condition if re.match(r'WHERE\b', condition,
                                                      re.IGNORECASE)
                                else 'WHERE ' + condition)
                        statements = [' '.join(components) + f' {target}']
                        if not native['state']['active']:
                            statements.append(
                                f'ALTER INDEX {identifier(name)} INACTIVE')
                        if native.get('description') is not None:
                            comment = native['description'].replace("'", "''")
                            statements.append(
                                f'COMMENT ON INDEX {identifier(name)} '
                                f"IS '{comment}'")
                        native['recreation_statements'] = statements
                        native['ddl'] = ';\n'.join(statements) + ';'
            elif kind == 'constraint' and len(item['display_path']) > 1:
                clause = constraint_clause(item)
                if clause:
                    native['ddl'] = (
                        f'ALTER TABLE {identifier(item["display_path"][-2])} '
                        f'ADD {clause};'
                    )
        primary_key_columns = {}
        for candidate in resources.values():
            detail = candidate.get('native', {})
            if (candidate['resource_kind'] == 'constraint' and
                    detail.get('constraint_type') == 'PRIMARY KEY'):
                primary_key_columns.setdefault(
                    tuple(candidate['display_path'][:-1]), set()).update(
                        index_segments(detail.get('index_name')))
        for item in resources.values():
            if item['resource_kind'] != 'column':
                continue
            path = item['display_path']
            native = item.setdefault('native', {})
            parents = [parent for parent in objects_named(path[-2])
                       if parent['resource_kind'] in {'table', 'view'}]
            relation = (parents[0].get('native', {})
                        if len(parents) == 1 else {})
            primary_key = item['display_name'] in primary_key_columns.get(
                tuple(path[:-1]), set())
            native['alteration'] = columns.alteration_context(
                native, relation, primary_key=primary_key,
                array=bool(field_dimensions.get(native.get('domain'))))
            native['type_editor'] = type_editor_values(native)
        plural = {
            'column': 'columns', 'index': 'indexes',
            'constraint': 'constraints',
        }
        for item in tuple(resources.values()):
            kind = item['resource_kind']
            native = item.setdefault('native', {})
            path = item['display_path']
            if kind in plural and len(path) > 1:
                parent_name = path[-2]
                child = {'name': item['display_name'], **native}
                for parent in objects_named(parent_name):
                    if parent['resource_kind'] in {'table', 'view'}:
                        parent.setdefault('native', {}).setdefault(
                            plural[kind], []
                        ).append(child)
            if kind == 'trigger' and native.get('relation'):
                child = {'name': item['display_name'], **native}
                for parent in objects_named(native['relation']):
                    if parent['resource_kind'] in {'table', 'view'}:
                        parent.setdefault('native', {}).setdefault(
                            'triggers', []
                        ).append(child)
        for item in resources.values():
            if item['resource_kind'] != 'table':
                continue
            native = item.setdefault('native', {})
            if native.get('system_object'):
                continue
            lines = []
            for column in native.get('columns', []):
                computed = str(column.get('computed_source') or '').strip()
                rendered_type = data_type(column)
                if rendered_type is None:
                    lines = None
                    break
                if computed:
                    expression = computed if computed.startswith('(') else (
                        f'({computed})'
                    )
                    definition = (
                        f'{identifier(column["name"])} {rendered_type} '
                        'COMPUTED BY '
                        f'{expression}'
                    )
                else:
                    definition = (
                        f'{identifier(column["name"])} {rendered_type}'
                    )
                    identity_type = numeric(column.get('identity_type'))
                    if identity_type is not None:
                        initial = numeric(column.get('identity_initial_value'))
                        increment = numeric(column.get('identity_increment'))
                        if identity_type not in (0, 1) or initial is None or (
                                increment in (None, 0)):
                            lines = None
                            native['ddl_unavailable_reason'] = (
                                'Exact identity generator metadata is missing '
                                'or invalid.')
                            break
                        definition += (
                            ' GENERATED ALWAYS AS IDENTITY' if
                            identity_type == 0 else
                            ' GENERATED BY DEFAULT AS IDENTITY'
                        )
                        definition += (f' (START WITH {initial} '
                                       f'INCREMENT BY {increment})')
                    default = str(column.get('default_source') or '').strip()
                    if default:
                        definition += f' {default}'
                    if str(column.get('not_null')) == '1':
                        not_null = [constraint for constraint in
                                    native.get('constraints', []) if
                                    constraint.get('constraint_type') ==
                                    'NOT NULL' and
                                    constraint.get('not_null_column') ==
                                    column['name']]
                        primary_key_member = any(
                            constraint.get('constraint_type') == 'PRIMARY KEY'
                            and column['name'] in index_segments(
                                constraint.get('index_name'))
                            for constraint in native.get('constraints', []))
                        if not not_null and (primary_key_member or
                                             identity_type in (0, 1)):
                            # Primary-key and identity fields are implicitly
                            # NOT NULL. Adding a separate constraint would
                            # invent an extra catalog object.
                            pass
                        elif len(not_null) != 1:
                            lines = None
                            native['ddl_unavailable_reason'] = (
                                'Exact NOT NULL constraint identity is '
                                'missing or ambiguous.')
                            break
                        else:
                            definition += (
                                ' CONSTRAINT ' +
                                identifier(not_null[0]['name']) + ' NOT NULL')
                    collation = str(
                        column.get('collation') or ''
                    ).rstrip(' ')
                    if collation:
                        definition += f' COLLATE {identifier(collation)}'
                lines.append(definition)
            if lines is None:
                native['ddl_available'] = False
                native.setdefault('ddl_unavailable_reason', (
                    'The exact Firebird field type could not be rendered.'
                ))
                continue
            for constraint in native.get('constraints', []):
                if constraint.get('constraint_type') == 'NOT NULL':
                    continue
                clause = constraint_clause({
                    'display_name': constraint['name'],
                    'native': constraint,
                })
                if clause is None:
                    native['ddl_available'] = False
                    native['ddl_unavailable_reason'] = (
                        'Exact constraint definition or backing-index '
                        'metadata is missing.')
                    break
                lines.append(clause)
            if native.get('ddl_available') is False:
                continue
            relation_type = numeric(native.get('relation_type'), 0)
            prefix = 'CREATE TABLE'
            suffix = ''
            if relation_type in {4, 5}:
                prefix = 'CREATE GLOBAL TEMPORARY TABLE'
                suffix = (
                    ' ON COMMIT PRESERVE ROWS' if relation_type == 4 else
                    ' ON COMMIT DELETE ROWS'
                )
            security = numeric(native.get('sql_security'))
            if security is not None:
                suffix += (', ' if relation_type in {4, 5} else ' ') + (
                    'SQL SECURITY DEFINER' if security else
                    'SQL SECURITY INVOKER')
            if relation_type in {0, 2}:
                suffix += (' ENABLE PUBLICATION' if
                           native['publication_enabled'] else
                           ' DISABLE PUBLICATION')
            if relation_type == 2 and native.get('external_file'):
                external = str(native['external_file']).replace("'", "''")
                prefix += (
                    f" {identifier(item['display_name'])} EXTERNAL FILE "
                    f"'{external}'"
                )
            else:
                prefix += ' ' + identifier(item['display_name'])
            statements = [prefix + ' (\n  ' + ',\n  '.join(lines) +
                          f'\n){suffix}']
            if relation_type in {4, 5}:
                # CREATE GTT has no publication clause. Preserve membership
                # explicitly even when replay uses a different database policy.
                publication = ('ENABLE' if native['publication_enabled'] else
                               'DISABLE')
                statements.append(
                    f'ALTER TABLE {identifier(item["display_name"])} '
                    f'{publication} PUBLICATION')
            if native.get('description') is not None:
                comment = native['description'].replace("'", "''")
                statements.append(
                    f'COMMENT ON TABLE {identifier(item["display_name"])} '
                    f"IS '{comment}'")
            for column in native.get('columns', []):
                if column.get('description') is not None:
                    comment = column['description'].replace("'", "''")
                    statements.append(
                        f'COMMENT ON COLUMN '
                        f'{identifier(item["display_name"])}.'
                        f'{identifier(column["name"])} IS \'{comment}\'')
            native['recreation_statements'] = statements
            native['ddl'] = ';\n'.join(statements) + ';'
        dependency_capable = {
            'database', 'table', 'view', 'column', 'index', 'constraint',
            'domain', 'sequence', 'trigger', 'procedure', 'function',
            'package', 'exception', 'role', 'character-set', 'collation',
            'external-function', 'blob-filter', 'publication',
        }
        # Native ownership/privilege rows are inspectable even where Firebird
        # 5 does not expose SQL GRANT USAGE ON this object class. Do not add
        # these kinds to grantable_kinds or fabricate executable grants.
        privilege_capable = grantable_kinds | {
            'user', 'column', 'character-set', 'collation', 'blob-filter'}
        for item in resources.values():
            kind = item['resource_kind']
            native = item.setdefault('native', {})
            sections = ['properties']
            if native.get('definition') is not None or native.get(
                    'metadata_source') is not None:
                sections.append('definition')
            if native.get('ddl') is not None:
                sections.append('ddl')
            if kind in dependency_capable:
                sections.extend(['dependencies', 'dependents'])
            if kind in privilege_capable:
                sections.append('privileges')
            if kind in {'table', 'view'}:
                sections.extend([
                    'columns', 'constraints', 'indexes', 'triggers', 'data',
                ])
            if kind in {'procedure', 'function', 'external-function'}:
                sections.append('parameters')
            if 'statistics' in native:
                sections.append('statistics')
            if 'state' in native:
                sections.append('state')
            if 'files' in native:
                sections.append('files')
            sections.append('operations')
            native['property_sections'] = sections
        for name in PROFILE.admin_tools:
            add('service-operation', [], name)
        for metric in _metric_records():
            if not str(metric['source']).startswith('MON$'):
                continue
            add('metric', [metric['scope']], metric['native_name'], metric)
        for item in resources.values():
            native = item.get('native', {})
            if item['resource_kind'] == 'trigger' and native.get('relation'):
                item['display_path'] = [
                    native['relation'], item['display_name'],
                ]
            elif item['resource_kind'] in {
                    'procedure', 'function', 'external-function'
            } and native.get('package'):
                item['display_path'] = [
                    native['package'], item['display_name'],
                ]
        system_paths = [tuple(item['display_path'])
                        for item in resources.values()
                        if item.get('native', {}).get('system_object')]
        for item in resources.values():
            path = tuple(item['display_path'])
            if len(path) > 1 and item['resource_kind'] in {
                    'column', 'index', 'constraint', 'trigger',
                    'procedure', 'function', 'external-function'}:
                owner_kinds = {'table', 'view'} if item['resource_kind'] in {
                    'column', 'index', 'constraint', 'trigger'
                } else {'package'}
                owners = [parent for parent in resources.values()
                          if parent['resource_kind'] in owner_kinds and
                          parent['display_path'] == list(path[:-1])]
                if len(owners) == 1:
                    item.setdefault('native', {})[
                        'navigator_parent_resource_id'] = owners[0][
                            'resource_id']
                    if owner_kinds == {'package'}:
                        item['native']['administration'] = {
                            'allowed_operations': [
                                'inspect', 'comment', 'grant', 'revoke'],
                            'definition_owner': {
                                'resource_id': owners[0]['resource_id'],
                                'resource_kind': 'package',
                                'display_name': owners[0]['display_name'],
                            },
                            'reason': (
                                'Package members are defined in their '
                                'owning package header and body.'),
                        }
            if any(path[:len(parent)] == parent for parent in system_paths):
                item.setdefault('native', {})['system_object'] = True
        for item in resources.values():
            if item['resource_kind'] == 'database':
                native = item.setdefault('native', {})
                native['catalog_observations'] = catalog_reader.observations
                native['information_observations'] = information_observations
                native['catalog_visibility'] = 'current_attachment'
                native.setdefault('catalog_warnings', []).extend(
                    catalog_reader.warnings)
                native['catalog_warnings'].extend(
                    f'Firebird driver information lookup for {name} failed.'
                    for name, observed in information_observations.items()
                    if not observed['available'])
            elif item['resource_kind'] == 'view':
                native = item.setdefault('native', {})
                native.setdefault('catalog_warnings', []).extend(
                    views.metadata_warnings(native.get('columns')))
                try:
                    native['view_columns'] = [
                        {'name': column['name']} for column in
                        views.catalog_columns(native.get('columns'))]
                except RelationalClientError as error:
                    native['view_columns'] = []
                    native['view_columns_unavailable_reason'] = str(error)
                try:
                    native['ddl'] = views.recreation_sql(
                        item['display_name'], native.get('definition'),
                        native.get('columns'))
                except RelationalClientError as error:
                    native.pop('ddl', None)
                    native['ddl_unavailable_reason'] = str(error)
        return list(resources.values())
    finally:
        try:
            cursor.close()
        finally:
            rendering.close()


def _admin_mapping_state(cursor):
    """Inspect the database-local compatibility mapping, not auth success."""
    try:
        cursor.execute(
            'SELECT TRIM(TRAILING FROM RDB$MAP_USING), TRIM(TRAILING '
            'FROM RDB$MAP_PLUGIN), '
            'TRIM(TRAILING FROM RDB$MAP_DB), TRIM(TRAILING FROM '
            'RDB$MAP_FROM_TYPE), '
            'TRIM(TRAILING FROM RDB$MAP_FROM), RDB$MAP_TO_TYPE, '
            'TRIM(TRAILING FROM RDB$MAP_TO) '
            'FROM RDB$AUTH_MAPPING WHERE RDB$MAP_NAME = '
            "'AutoAdminImplementationMapping'")
        row = cursor.fetchone()
    except Exception as error:
        return {'available': False, 'error_type': type(error).__name__}
    expected = ('P', 'Win_Sspi', None, 'Predefined_Group',
                'DOMAIN_ANY_RID_ADMINS', 1, 'RDB$ADMIN')
    return {
        'available': True, 'present': row is not None,
        'canonical': tuple(row) == expected if row is not None else False,
        'definition': dict(zip(('using', 'plugin', 'database', 'from_type',
                                'from', 'to_type', 'to'), row or ())),
        'scope': 'database', 'windows_authentication_verified': False,
    }


def _security(connection, request):
    cursor = connection.cursor()
    try:
        cursor.execute(
            'SELECT CURRENT_USER, CURRENT_ROLE FROM RDB$DATABASE'
        )
        current_user, current_role = cursor.fetchone()
        current_user = str(current_user).rstrip(' ')
        return {
            'resource_id': f'authorization:{current_user}',
            'display_name': current_user,
            'authority_path': ['authorization', current_user],
            'generation': str(
                request.get('capability_generation') or 'current'
            ),
            'native': {
                'current_user': current_user,
                'current_role': str(current_role or '').rstrip(' '),
            },
        }
    finally:
        cursor.close()


def _sequence_state(cursor, name):
    quoted = '"' + str(name).replace('"', '""') + '"'
    try:
        cursor.execute(f'SELECT GEN_ID({quoted}, 0) FROM RDB$DATABASE')
        row = cursor.fetchone()
        if not row or row[0] is None:
            return {'available': False,
                    'reason': 'No generator value returned'}
        return {
            'available': True, 'current_value': str(row[0]),
            'observation': 'GEN_ID(sequence, 0); does not advance sequence',
            'concurrency_note': 'Other sessions may advance this value.',
        }
    except Exception as exc:
        return {'available': False, 'error_type': type(exc).__name__,
                'reason': 'Generator value could not be read by this account'}


def _create_client(permissions):
    module = load_optional_module('firebird.driver')
    core = (load_optional_module('firebird.driver.core')
            if module is not None else None)
    if module is not None:
        _configure_client_library(module)
    return FirebirdQueryClient(RelationalClientConfig(
        profile=PROFILE,
        module_name='firebird.driver',
        version_query=(
            "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
            'FROM RDB$DATABASE'
        ),
        version_parser=_version,
        connect_arguments=lambda route: _route_arguments(route, module),
        metadata_reader=_resources,
        query_parameter_normalizer=normalize_parameters,
        query_value_normalizer=lambda value: normalize_value(
            value, getattr(core, 'BlobReader', ())),
        query_columns_reader=describe_columns,
        session_rollback_needed=lambda connection: (
            connection.main_transaction.is_active()),
        transaction_observer=observe_transaction,
        session_releaser=release_session,
        security_reader=_security,
        credential_argument='password',
        secret_acquirer=permissions.acquire_secret,
        connection_initializer=lambda connection, route: (
            _initialize_connection(connection, route, module)
        ),
        failed_session_releaser=discard_failed_session,
        database_create_arguments=lambda route, database, options: (
            _database_create_arguments(route, database, options, module)
        ),
        database_creator=(
            (lambda **kwargs: create_owned_database(module, core, **kwargs))
            if module is not None else None),
        administration=ADMINISTRATION,
        server_route=_server_route,
        server_connector_name='connect_server',
        server_connect_arguments=lambda route: _server_arguments(
            route, module
        ),
        server_identity_reader=lambda server, request: _server_identity(
            server, request, module
        ),
        server_metadata_reader=_server_resources,
        server_operation_runner=lambda server, operation, database, options: (
            _firebird_service_operation(
                server, operation, database, options, module
            )
        ),
    ), module, service_connector=(
        (lambda **kwargs: connect_service(module, core, **kwargs))
        if module is not None else None), service_attached=(
        (lambda connection: notify_attached(core, connection))
        if core is not None else None))


def create_provider(context, permissions, client=None):
    return FirebirdProvider(
        context, permissions, client or _create_client(permissions)
    )
