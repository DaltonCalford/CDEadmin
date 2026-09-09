"""Firebird 5.0.4 semantic provider."""

import ctypes
import hashlib
import json
import os
import re
import threading
from importlib import resources as package_resources

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
        'database': frozenset({
            'inspect', 'create', 'alter', 'drop',
            'backup_logical', 'restore_logical', 'backup_physical',
            'restore_physical', 'validate_database', 'repair_database',
            'sweep_database', 'database_statistics', 'shutdown_database',
            'bring_online', 'set_page_cache_size', 'set_sweep_interval',
            'set_space_reservation', 'set_write_mode', 'set_access_mode',
            'set_sql_dialect', 'activate_shadow', 'remove_linger',
            'fixup_database', 'set_replica_mode', 'upgrade_database',
        }),
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
    _configure_client_library(module)
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
        spb.insert_int(core.SPBItem.OPTIONS, module.SrvRepairFlag.SWEEP_DB)
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


def _firebird_service_operation(
        server, operation_id, database, options, module):
    """Dispatch one exact Firebird 5 database service-manager task."""
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
    if operation_id == 'backup_logical':
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
        database_files = [options['restore_database'], *(
            options.get('additional_database_files') or []
        )]
        lines, truncated = _service_lines(lambda output: service.restore(
            backup=(
                backup_files[0] if len(backup_files) == 1 else backup_files
            ),
            database=(
                database_files[0]
                if len(database_files) == 1 else database_files
            ),
            db_file_pages=options.get('database_file_pages') or (), role=role,
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
            direct=bool(options.get('direct_io', False)),
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
        service.repair(
            database=database, role=role,
            flags=module.SrvRepairFlag[options['repair_action']],
        )
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
            mode=module.ShutdownMode[options.get('mode', 'NORMAL')],
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
        service.activate_shadow(database=database, role=role)
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
        database_native = {'scope': 'database'}
        database_rows = optional(
            'SELECT MON$DATABASE_NAME, MON$PAGE_SIZE, MON$ODS_MAJOR, '
            'MON$ODS_MINOR, MON$SQL_DIALECT, '
            'CAST(MON$CREATION_DATE AS VARCHAR(64)), MON$PAGES, '
            'MON$BACKUP_STATE, MON$CRYPT_STATE, MON$OWNER, MON$GUID, '
            'MON$READ_ONLY, MON$FORCED_WRITES, MON$RESERVE_SPACE, '
            'MON$SWEEP_INTERVAL FROM MON$DATABASE'
        )
        if database_rows:
            row = database_rows[0]
            names = (
                'database_name', 'page_size', 'ods_major', 'ods_minor',
                'sql_dialect', 'creation_date', 'allocated_pages',
                'backup_state', 'encryption_state', 'owner', 'guid',
                'read_only', 'forced_writes', 'reserve_space',
                'sweep_interval',
            )
            database_native.update({
                name: None if value is None else str(value).strip()
                for name, value in zip(names, row)
            })
        charset_rows = optional(
            'SELECT TRIM(RDB$CHARACTER_SET_NAME) FROM RDB$DATABASE'
        )
        if charset_rows:
            database_native['default_character_set'] = charset_rows[0][0]
        timezone_rows = optional(
            "SELECT RDB$GET_CONTEXT('SYSTEM', 'SESSION_TIMEZONE') "
            'FROM RDB$DATABASE'
        )
        if timezone_rows:
            database_native['session_time_zone'] = timezone_rows[0][0]
        database_name = str(
            database_native.get('database_name') or 'current'
        ).rsplit(':', 1)[-1].rsplit('/', 1)[-1]
        add('database', [], database_name, database_native)
        cursor.execute(
            'SELECT TRIM(RDB$RELATION_NAME), RDB$VIEW_BLR, '
            'CAST(RDB$VIEW_SOURCE AS VARCHAR(8191)), '
            'CAST(RDB$DESCRIPTION AS VARCHAR(8191)), RDB$RELATION_ID, '
            'RDB$SYSTEM_FLAG, RDB$RELATION_TYPE, '
            'TRIM(RDB$SECURITY_CLASS), TRIM(RDB$EXTERNAL_FILE), '
            'TRIM(RDB$OWNER_NAME), TRIM(RDB$DEFAULT_CLASS), RDB$FLAGS, '
            'RDB$SQL_SECURITY FROM RDB$RELATIONS WHERE '
            'COALESCE(RDB$SYSTEM_FLAG, 0) = 0 ORDER BY RDB$RELATION_NAME'
        )
        relation_types = {
            0: 'persistent',
            1: 'view',
            2: 'external',
            3: 'virtual',
            4: 'global-temporary-preserve-rows',
            5: 'global-temporary-delete-rows',
        }
        for row in cursor.fetchall():
            (
                name, view_blr, view_source, description, relation_id,
                system_flag, relation_type, security_class, external_file,
                owner, default_class, flags, sql_security,
            ) = row
            name = str(name).strip()
            kind = 'view' if view_blr is not None else 'table'
            native = {
                'relation_id': relation_id,
                'relation_type': relation_type,
                'relation_type_name': relation_types.get(
                    relation_type, 'unknown'
                ),
                'system_flag': system_flag,
                'owner': None if owner is None else str(owner).strip(),
                'security_class': (
                    None if security_class is None else
                    str(security_class).strip()
                ),
                'default_security_class': (
                    None if default_class is None else
                    str(default_class).strip()
                ),
                'external_file': (
                    None if external_file is None else
                    str(external_file).strip()
                ),
                'flags': flags,
                'sql_security': sql_security,
                'description': description,
            }
            if kind == 'view':
                native['definition'] = view_source
            add(kind, [], name, native)
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
        grantable_kinds = {
            'database', 'table', 'view', 'domain', 'sequence', 'procedure',
            'function', 'external-function', 'package', 'exception', 'role',
            'publication',
        }
        grantable_names = {
            item['display_name']
            for item in resources.values()
            if item['resource_kind'] in grantable_kinds
        }
        grantable_names.add('database')
        for (
                grantee, relation, field, privilege, grantor, grant_option,
                user_type, object_type) in optional(
            'SELECT TRIM(RDB$USER), TRIM(RDB$RELATION_NAME), '
            'TRIM(RDB$FIELD_NAME), TRIM(RDB$PRIVILEGE), '
            'TRIM(RDB$GRANTOR), RDB$GRANT_OPTION, RDB$USER_TYPE, '
            'RDB$OBJECT_TYPE FROM RDB$USER_PRIVILEGES '
            'ORDER BY 1, 2, 3, 4'
        ):
            relation = str(relation or '').strip() or 'database'
            if relation not in grantable_names:
                continue
            grantee = str(grantee).strip()
            privilege = str(privilege).strip()
            field = str(field or '').strip()
            granted_object = relation + (f'.{field}' if field else '')
            name = f'{grantee}:{privilege} on {granted_object}'
            # A granted object is metadata of a Firebird grant, not its
            # navigator parent.  Keeping it in display_path incorrectly
            # nested grants beneath tables, views and sequences and could
            # also make security entries disappear among object children.
            add('privilege', [], name, {
                'grantee': grantee,
                'privilege': privilege,
                'granted_object': granted_object,
                'field': field or None,
                'grantor': str(grantor or '').strip(),
                'grant_option': grant_option,
                'user_type': user_type,
                'object_type': object_type,
            })
        for name in PROFILE.admin_tools:
            add('service-operation', [], name)
        for metric in _metric_records():
            if not str(metric['source']).startswith('MON$'):
                continue
            add('metric', [metric['scope']], metric['native_name'], metric)
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
    if module is not None:
        _configure_client_library(module)
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
        server_identity_reader=lambda server, request: _server_identity(
            server, request, module
        ),
        server_metadata_reader=_server_resources,
        server_operation_runner=lambda server, operation, database, options: (
            _firebird_service_operation(
                server, operation, database, options, module
            )
        ),
    ), module)


def create_provider(context, permissions, client=None):
    return FirebirdProvider(
        context, permissions, client or _create_client(permissions)
    )
