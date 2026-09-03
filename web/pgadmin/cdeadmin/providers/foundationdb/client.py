"""FoundationDB API 730 client boundary."""

import copy
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from urllib.parse import urlsplit
from collections.abc import Mapping

from ..distributed_sql import resource
from ..native_key_value import (
    KeyIdentityStore, decode_value, display_value, key_value_catalog,
    validate_key_value_request,
)
from ..native_distributed import (
    NativeDistributedError, NativeResult,
)
from pgadmin.cdeadmin.visual_admin import (
    ControlPlaneCatalog,
    ControlPlaneOperation,
    control_plane_field as cp_field,
)


def _choice(field_id, label, values, **options):
    return cp_field(
        field_id, label, 'select', options=[
            {'value': value, 'label': title} for value, title in values
        ], **options
    )


CONTROL_OPERATIONS = (
    ControlPlaneOperation(
        'tenant', 'create', 'Create tenant', 'admin', 'topology_admin', (
            cp_field('name', 'Tenant name', 'text', True, max_length=255,
                     pattern=r'[A-Za-z0-9_.:-]+'),
            cp_field('tenant_group', 'Tenant group', 'text', False,
                     max_length=255, pattern=r'[A-Za-z0-9_.:-]+'),
        ), target_required=False, impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'tenant', 'drop', 'Delete tenant', 'destructive',
        'topology_admin', impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'process', 'exclude', 'Exclude process', 'admin',
        'maintenance_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'process', 'include', 'Include process', 'admin',
        'maintenance_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'process', 'set_class', 'Set process class', 'admin',
        'topology_admin', (
            _choice('process_class', 'Process class', (
                ('storage', 'Storage'), ('transaction', 'Transaction'),
                ('stateless', 'Stateless'), ('log', 'Log'),
                ('commit_proxy', 'Commit proxy'),
                ('grv_proxy', 'GRV proxy'), ('resolver', 'Resolver'),
                ('coordinator', 'Coordinator'),
                ('unset', 'Unset'), ('exclude', 'Exclude'),
            ), required=True),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'cluster', 'change_coordinators', 'Change coordinators',
        'destructive', 'topology_admin', (
            cp_field('addresses', 'Coordinator addresses', 'json', True,
                     json_type='array'),
            cp_field('description', 'Cluster description', 'text', False,
                     max_length=128, pattern=r'[A-Za-z0-9_.:-]+'),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'cluster', 'maintenance_on', 'Enable zone maintenance', 'admin',
        'maintenance_admin', (
            cp_field('zone_id', 'Zone ID', 'text', True, max_length=255,
                     pattern=r'[A-Za-z0-9_.:-]+'),
            cp_field('seconds', 'Duration in seconds', 'number', True,
                     minimum=1, maximum=86400),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'cluster', 'maintenance_off', 'Disable maintenance mode', 'admin',
        'maintenance_admin', impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'configuration', 'configure', 'Configure redundancy and storage',
        'destructive', 'topology_admin', (
            _choice('redundancy', 'Redundancy mode', (
                ('single', 'Single'), ('double', 'Double'),
                ('triple', 'Triple'),
                ('three_data_hall', 'Three data hall'),
            ), required=True),
            _choice('storage_engine', 'Storage engine', (
                ('ssd', 'SSD'), ('memory', 'Memory'),
                ('ssd-redwood-1-experimental', 'Redwood experimental'),
            ), required=True),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'configuration', 'data_distribution', 'Set data distribution',
        'admin', 'maintenance_admin', (
            _choice('state', 'Data distribution', (
                ('on', 'Enabled'), ('off', 'Disabled'),
            ), required=True),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'backup', 'start', 'Start continuous backup', 'admin',
        'backup_admin', (
            cp_field('destination_url', 'Approved backup container URL',
                     'text', True, max_length=4096, sensitive=True),
            cp_field('tag_name', 'Backup tag', 'text', True,
                     max_length=255, pattern=r'[A-Za-z0-9_.:-]+'),
            cp_field('snapshot_interval_seconds',
                     'Snapshot interval (seconds)', 'number', False,
                     minimum=1, maximum=31536000),
            cp_field('stop_when_restorable', 'Stop when restorable',
                     'boolean', False, default=True),
        ), target_required=False, impact_scope='cluster', long_running=True,
        cancellable=True
    ),
    ControlPlaneOperation(
        'backup', 'status', 'Inspect continuous backup', 'read',
        'backup_admin', confirmation_required=False, impact_scope='cluster',
        post_state_required=False
    ),
    ControlPlaneOperation(
        'backup', 'pause', 'Pause continuous backup', 'admin',
        'backup_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'backup', 'resume', 'Resume continuous backup', 'admin',
        'backup_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'backup', 'discontinue', 'Discontinue continuous backup',
        'destructive', 'backup_admin', impact_scope='cluster',
        long_running=True
    ),
    ControlPlaneOperation(
        'backup', 'abort', 'Abort continuous backup', 'destructive',
        'backup_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'backup', 'delete', 'Delete backup container data', 'destructive',
        'backup_admin', (
            cp_field('destination_url', 'Approved backup container URL',
                     'text', True, max_length=4096, sensitive=True),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'restore', 'start', 'Start database restore', 'destructive',
        'restore_admin', (
            cp_field('source_url', 'Approved backup container URL', 'text',
                     True, max_length=4096, sensitive=True),
            cp_field('tag_name', 'Restore tag', 'text', True,
                     max_length=255, pattern=r'[A-Za-z0-9_.:-]+'),
            cp_field('wait_for_done', 'Wait for completion', 'boolean',
                     False, default=False),
        ), target_required=False, impact_scope='cluster', long_running=True,
        cancellable=True
    ),
    ControlPlaneOperation(
        'restore', 'status', 'Inspect database restore', 'read',
        'restore_admin', confirmation_required=False, impact_scope='cluster',
        post_state_required=False
    ),
    ControlPlaneOperation(
        'restore', 'abort', 'Abort database restore', 'destructive',
        'restore_admin', impact_scope='cluster', long_running=True
    ),
)

CONTROL_CATALOG = ControlPlaneCatalog('foundationdb', CONTROL_OPERATIONS)


class FoundationDBBackend:
    MAX_CLI_BYTES = 4 * 1024 * 1024
    _network_lock = threading.RLock()
    _network_fingerprint = None

    def __init__(self, secret_acquirer=None, module=None):
        try:
            self.fdb = module or importlib.import_module('fdb')
        except ImportError as exc:
            raise NativeDistributedError(
                'FoundationDB requires its official Python binding and '
                'client library'
            ) from exc
        if not self.fdb.is_api_version_selected():
            self.fdb.api_version(730)
        elif self.fdb.get_api_version() != 730:
            raise NativeDistributedError(
                'FoundationDB API version must be 730')
        self._databases = []
        self._database_routes = {}
        self._row_identities = KeyIdentityStore()
        self.secret_acquirer = secret_acquirer

    @staticmethod
    def _route(request):
        route = request.get('route') or request.get('_provider_route')
        if not isinstance(route, dict) or not route.get('cluster_file'):
            raise NativeDistributedError(
                'FoundationDB cluster file is required')
        route = copy.deepcopy(route)
        route.setdefault('tls_mode', 'disabled')
        route.setdefault('tls_verify_peers', 'Check.Valid=1')
        route.setdefault('transaction_timeout', 5000)
        route.setdefault('transaction_retry_limit', 5)
        route.setdefault('transaction_max_retry_delay', 1000)
        if route['tls_mode'] not in {'disabled', 'verify-peers'}:
            raise NativeDistributedError('FoundationDB TLS mode is invalid')
        if route['tls_mode'] == 'verify-peers' and not route.get(
                'tls_ca_file'):
            raise NativeDistributedError(
                'FoundationDB verified TLS requires a CA file')
        if bool(route.get('tls_certificate_file')) != bool(
                route.get('tls_key_file')):
            raise NativeDistributedError(
                'FoundationDB TLS certificate and key must be supplied '
                'together')
        references = dict(route.get('credential_references') or {})
        if route.get('credential_reference_id'):
            references.setdefault('tls_private_key_password',
                                  route['credential_reference_id'])
        if set(references) - {'tls_private_key_password'}:
            raise NativeDistributedError(
                'FoundationDB credential kind is invalid')
        if references and not route.get('principal_reference'):
            raise NativeDistributedError(
                'FoundationDB credentials require a principal reference')
        route['credential_references'] = references
        for field in ('transaction_timeout', 'transaction_retry_limit',
                      'transaction_max_retry_delay'):
            value = route[field]
            if isinstance(value, bool) or not isinstance(value, int) or not (
                    0 <= value <= 86400000):
                raise NativeDistributedError(
                    f'FoundationDB {field} is invalid')
        return route

    def _with_tls_password(self, route, callback):
        reference = route.get('credential_references', {}).get(
            'tls_private_key_password')
        if not reference:
            return callback(None)
        if not callable(self.secret_acquirer):
            raise NativeDistributedError(
                'FoundationDB secret binding is unavailable')
        lease = self.secret_acquirer(
            reference, route['principal_reference'], 'connect',
            'tls_private_key_password')
        with lease:
            return lease.use(
                lambda view: callback(bytes(view).decode('utf-8')))

    def _configure_network(self, route, password):
        values = tuple(route.get(field) for field in (
            'tls_mode', 'tls_ca_file', 'tls_certificate_file', 'tls_key_file',
            'tls_verify_peers', 'external_client_directory',
            'external_client_library', 'client_threads_per_version',
        ))
        fingerprint = (values, bool(password))
        with self._network_lock:
            owner = type(self)
            if owner._network_fingerprint not in {None, fingerprint}:
                raise NativeDistributedError(
                    'FoundationDB process already has a different network '
                    'security profile; retire its provider generation first')
            if owner._network_fingerprint is not None:
                return
            options = self.fdb.options
            if route['tls_mode'] == 'verify-peers':
                options.set_tls_ca_path(route['tls_ca_file'])
                if route.get('tls_certificate_file'):
                    options.set_tls_cert_path(route['tls_certificate_file'])
                    options.set_tls_key_path(route['tls_key_file'])
                if password:
                    options.set_tls_password(password)
                options.set_tls_verify_peers(route['tls_verify_peers'])
            if route.get('external_client_directory'):
                options.set_external_client_directory(
                    route['external_client_directory'])
            if route.get('external_client_library'):
                options.set_external_client_library(
                    route['external_client_library'])
            if route.get('client_threads_per_version') is not None:
                options.set_client_threads_per_version(
                    route['client_threads_per_version'])
            owner._network_fingerprint = fingerprint

    @staticmethod
    def _trusted_file(value, label, executable=False):
        if not isinstance(value, str) or not value:
            raise NativeDistributedError(f'FoundationDB {label} is required')
        path = Path(value).expanduser().resolve()
        if not path.is_file() or (executable and not os.access(path, os.X_OK)):
            raise NativeDistributedError(
                f'FoundationDB {label} is unavailable')
        return str(path)

    def _run_cli(self, route, command, timeout=30):
        executable = self._trusted_file(
            route.get('fdbcli_path'), 'fdbcli executable', executable=True
        )
        cluster_file = self._trusted_file(
            route.get('cluster_file'), 'cluster file'
        )
        if not isinstance(command, str) or not command or len(
                command.encode('utf-8')) > 65536 or any(
                    character in command for character in '\x00\r\n;'):
            raise NativeDistributedError(
                'FoundationDB control command is invalid')
        arguments = [executable, '-C', cluster_file]
        if route.get('tls_mode') == 'verify-peers':
            arguments.extend(['--tls-ca-file', route['tls_ca_file']])
            if route.get('tls_certificate_file'):
                arguments.extend([
                    '--tls-certificate-file', route['tls_certificate_file'],
                    '--tls-key-file', route['tls_key_file'],
                ])
            arguments.extend([
                '--tls-verify-peers', route['tls_verify_peers']])
        arguments.extend(['--exec', command])

        def run(password):
            environment = os.environ.copy()
            if password:
                environment['FDB_TLS_PASSWORD'] = password
            return subprocess.run(
                arguments, check=False, capture_output=True, text=True,
                timeout=timeout, env=environment,
            )
        try:
            result = self._with_tls_password(route, run)
        except (OSError, subprocess.SubprocessError) as exc:
            raise NativeDistributedError(
                'FoundationDB control request failed') from exc
        output = (result.stdout or '') + (result.stderr or '')
        if len(output.encode('utf-8')) > self.MAX_CLI_BYTES:
            raise NativeDistributedError(
                'FoundationDB control response exceeds size limit')
        if result.returncode != 0:
            raise NativeDistributedError(
                'FoundationDB control request was rejected')
        return {
            'exit_code': result.returncode,
            'stdout': result.stdout or '',
            'stderr': result.stderr or '',
            'provider_response_observed': True,
            'provider_finality_only': True,
            'automatic_mutation_retry_by_cdeadmin': False,
        }

    def runtime_identity(self, request, _handle=None):
        route = self._route(request)
        result = self._run_cli(route, 'status json', timeout=15)
        status = json.loads(result['stdout'])
        versions = {
            process.get('version') for process in
            status.get('cluster', {}).get('processes', {}).values()
            if process.get('version')
        }
        if len(versions) != 1:
            raise NativeDistributedError(
                'FoundationDB cluster version is ambiguous')
        version = versions.pop()
        return {'engine_id': 'foundationdb', 'version': version,
                'build_id': f'foundationdb:{version}',
                'protocol_id': 'foundationdb_native'}

    def open_session(self, request):
        route = self._route(request)

        def connect(password):
            self._configure_network(route, password)
            return self.fdb.open(route['cluster_file'])
        database = self._with_tls_password(route, connect)
        self._databases.append(database)
        self._database_routes[id(database)] = route
        return database

    def list_resources(self, request):
        database = self.open_session(request)
        generation = str(request.get('capability_generation') or 'current')
        values = [resource('cluster', [], 'FoundationDB', generation)]
        values.append(resource(
            'key-range', [], 'bounded-key-browser', generation, {
                'start_key_base64': '', 'end_key_base64': '/w==',
                'maximum_page_size': 500,
            },
        ))
        try:
            for name in self.fdb.directory.list(database):
                values.append(resource('directory', [], name, generation))
        except Exception:
            pass
        route = self._route(request)
        try:
            status = json.loads(self._run_cli(
                route, 'status json', timeout=15
            )['stdout'])
            cluster = status.get('cluster', {})
            for address in cluster.get('coordinators', {}).get(
                    'coordinators', []):
                values.append(resource(
                    'coordinator', [], address, generation
                ))
            for process_id, process in cluster.get(
                    'processes', {}).items():
                if isinstance(process, dict):
                    values.append(resource(
                        'process', [], process_id, generation, process
                    ))
            configuration = cluster.get('configuration')
            if isinstance(configuration, dict):
                values.append(resource(
                    'configuration', [], 'cluster-configuration',
                    generation, configuration,
                ))
        except Exception:
            pass
        return values

    def describe_transaction(self, handle):
        route = self._database_routes.get(id(handle), {})
        return {
            'native_state': 'foundationdb-database-handle',
            'isolation': 'strict-serializable',
            'transaction_timeout_ms': route.get('transaction_timeout'),
            'transaction_retry_limit': route.get('transaction_retry_limit'),
            'transaction_max_retry_delay_ms': route.get(
                'transaction_max_retry_delay'),
            'provider_owned_finality': True,
        }

    def _transaction(self, database):
        transaction = database.create_transaction()
        route = self._database_routes.get(id(database), {})
        options = transaction.options
        options.set_timeout(route.get('transaction_timeout', 5000))
        options.set_retry_limit(route.get('transaction_retry_limit', 5))
        options.set_max_retry_delay(
            route.get('transaction_max_retry_delay', 1000))
        return transaction

    def execute(self, handle, command, _parameters):
        if not isinstance(command, dict):
            raise NativeDistributedError('FoundationDB command must be JSON')
        operation = command.get('operation')
        key = str(command.get('key', '')).encode()
        if operation == 'get':
            value = handle[key]
            rows = [{'key': key.hex(),
                     'value': None if value is None else bytes(value).hex()}]
        elif operation in {'set', 'clear'}:
            transaction = self._transaction(handle)
            if operation == 'set':
                transaction[key] = str(command.get('value', '')).encode()
            else:
                del transaction[key]
            transaction.commit().wait()
            rows = [{'key': key.hex(), 'accepted': True}]
        else:
            raise NativeDistributedError(
                'FoundationDB operation is unsupported')
        return NativeResult('key_value', 'entries', rows,
                            {'fields': ['key', 'value']},
                            {'operation': operation})

    @staticmethod
    def cancel(_token): return False

    def describe_security(self, request):
        return resource('configuration', [], 'cluster-security',
                        str(request.get('capability_generation') or 'current'),
                        {'authorization_model':
                         'foundationdb-cluster-file-and-tls'})

    def visual_admin_catalog(self, catalog):
        value = key_value_catalog(
            catalog, {'key', 'key-range'}, 'foundationdb'
        )
        value = CONTROL_CATALOG.apply(value)
        value['transaction_authority'] = 'foundationdb-native-transaction'
        return value

    @staticmethod
    def validate_admin_operation(request):
        if CONTROL_CATALOG.supports(
                request.get('resource_kind'), request.get('operation_id')):
            result = CONTROL_CATALOG.validate(request)
            if result['errors']:
                return result
            try:
                FoundationDBBackend._control_command(request)
            except NativeDistributedError as exc:
                return {'errors': [{
                    'field_id': None, 'code': 'invalid_control_request',
                    'message': str(exc),
                }]}
            return {'errors': []}
        return validate_key_value_request(request, {'key', 'key-range'})

    @staticmethod
    def plan_admin_operation(request):
        if CONTROL_CATALOG.supports(
                request.get('resource_kind'), request.get('operation_id')):
            command = FoundationDBBackend._control_command(request)
            return {
                'command_preview': {
                    'operation': request['operation_id'],
                    'resource_kind': request['resource_kind'],
                    'provider_constructed': True,
                    'tool': command.get('tool', 'fdbcli'),
                },
                'provider_payload': dict(request),
                'warnings': [],
                'impact': command['impact'],
                'receipt': {
                    'planner': 'foundationdb-control-plane.v1',
                    'provider_finality_authority': True,
                    'automatic_mutation_retry': False,
                },
            }
        return {'command_preview': {'operation': request['operation_id'],
                                    'resource_kind': request['resource_kind'],
                                    'provider_constructed': True},
                'provider_payload': dict(request), 'warnings': [],
                'receipt': {'planner': 'foundationdb-native'}}

    def apply_admin_operation(self, request):
        payload = request['provider_payload']
        operation = payload['operation_id']
        if CONTROL_CATALOG.supports(
                payload.get('resource_kind'), operation):
            compiled = self._control_command(payload)
            route = self._route(payload)
            if compiled.get('tool') in {'fdbbackup', 'fdbrestore'}:
                response = self._run_backup_tool(
                    route, compiled['tool'], compiled['arguments']
                )
            else:
                response = self._run_cli(route, compiled['command'])
            return {
                'accepted': True,
                'provider_response': response,
                'impact': copy.deepcopy(compiled['impact']),
                'provider_finality_only': True,
                'automatic_mutation_retry_by_cdeadmin': False,
            }
        if payload['resource_kind'] == 'directory' and operation in {
                'create', 'drop'}:
            database = self.open_session({'route': payload['_provider_route']})
            name = (payload.get('draft', {}).get('name') or
                    payload.get('target_resource', {}).get('display_name'))
            if operation == 'create':
                self.fdb.directory.create(database, (name,))
            else:
                self.fdb.directory.remove(database, (name,))
            return {'accepted': True, 'native_operation': operation}
        if payload['resource_kind'] in {'key', 'key-range'} and operation in {
                'insert', 'update', 'delete'}:
            route = payload['_provider_route']
            target = payload.get('target_resource') or {
                'resource_kind': payload['resource_kind'],
                'display_path': ['bounded-key-browser'],
            }
            draft = payload.get('draft') or {}
            database = self.open_session({'route': route})
            if operation == 'insert':
                key = decode_value(draft, 'key')
                original = None
            else:
                key, original = self._row_identities.consume(
                    route, target, draft.get('selector')
                )
            transaction = self._transaction(database)
            future = transaction[key]
            future.wait()
            observed = future.value if future.present() else None
            if operation == 'insert':
                if observed is not None:
                    raise NativeDistributedError(
                        'FoundationDB key already exists'
                    )
                transaction[key] = decode_value(draft, 'value')
            else:
                if observed is None or bytes(observed) != original:
                    raise NativeDistributedError(
                        'FoundationDB row changed after it was displayed'
                    )
                if operation == 'update':
                    transaction[key] = decode_value(draft, 'value')
                else:
                    del transaction[key]
            try:
                transaction.commit().wait()
            except Exception as exc:
                raise NativeDistributedError(
                    'FoundationDB commit returned an error; outcome remains '
                    'provider-owned'
                ) from exc
            return {
                'accepted': True, 'native_operation': operation,
                'commit_requested': True, 'commit_returned': True,
                'provider_finality_only': True,
                'automatic_mutation_retry_by_cdeadmin': False,
            }
        if operation == 'inspect':
            return {'accepted': True,
                    'resource': payload.get('target_resource')}
        raise NativeDistributedError(
            'FoundationDB admin operation unsupported')

    @staticmethod
    def _safe_token(value, label, pattern=r'[A-Za-z0-9_.:-]+', limit=255):
        if not isinstance(value, str) or not value or len(value) > limit or (
                re.fullmatch(pattern, value) is None):
            raise NativeDistributedError(f'FoundationDB {label} is invalid')
        return value

    @staticmethod
    def _target(request):
        target = request.get('target_resource')
        if not isinstance(target, Mapping):
            raise NativeDistributedError(
                'FoundationDB control target is required')
        name = target.get('display_name')
        return FoundationDBBackend._safe_token(name, 'target')

    @staticmethod
    def _backup_url(request, field_id):
        value = (request.get('draft') or {}).get(field_id)
        if not isinstance(value, str) or not value or len(value) > 4096 or (
                any(character in value for character in '\x00\r\n')):
            raise NativeDistributedError(
                'FoundationDB backup container URL is invalid')
        if urlsplit(value).scheme.lower() not in {
                'file', 'blobstore', 's3', 'azure', 'gs'}:
            raise NativeDistributedError(
                'FoundationDB backup container scheme is not admitted')
        route = request.get('_provider_route') or {}
        prefixes = route.get('backup_container_allowlist')
        if isinstance(prefixes, str):
            prefixes = [
                item.strip() for item in prefixes.split(',') if item.strip()
            ]
        if not isinstance(prefixes, list) or not prefixes or not any(
                isinstance(prefix, str) and value.startswith(prefix)
                for prefix in prefixes):
            raise NativeDistributedError(
                'FoundationDB backup URL is outside the endpoint allowlist')
        return value

    @staticmethod
    def _backup_tag(request):
        operation = request.get('operation_id')
        draft = request.get('draft') or {}
        value = draft.get('tag_name') if operation == 'start' else None
        if value is None:
            value = FoundationDBBackend._target(request)
        return FoundationDBBackend._safe_token(value, 'backup tag')

    @staticmethod
    def _process_address(request):
        target = request.get('target_resource')
        native = target.get('native') if isinstance(target, Mapping) else None
        value = native.get('address') if isinstance(native, Mapping) else None
        if value is None:
            value = target.get('display_name') if isinstance(
                target, Mapping) else None
        return FoundationDBBackend._safe_token(
            value, 'process address', r'[A-Za-z0-9_.:\-\[\]]+', 512
        )

    @staticmethod
    def _control_command(request):
        kind = request.get('resource_kind')
        operation = request.get('operation_id')
        draft = request.get('draft') or {}
        command = None
        if kind == 'tenant':
            name = (
                FoundationDBBackend._safe_token(
                    draft.get('name'), 'tenant name'
                ) if operation == 'create' else
                FoundationDBBackend._target(request)
            )
            if operation == 'create':
                command = f'tenant create {name}'
                group = draft.get('tenant_group')
                if group:
                    command += ' tenant_group=' + (
                        FoundationDBBackend._safe_token(
                            group, 'tenant group'
                        )
                    )
            elif operation == 'drop':
                command = f'tenant delete {name}'
        elif kind == 'process':
            address = FoundationDBBackend._process_address(request)
            if operation in {'exclude', 'include'}:
                command = f'{operation} {address}'
            elif operation == 'set_class':
                process_class = draft.get('process_class')
                admitted = {
                    'storage', 'transaction', 'stateless', 'log',
                    'commit_proxy', 'grv_proxy', 'resolver', 'coordinator',
                    'unset', 'exclude',
                }
                if process_class not in admitted:
                    raise NativeDistributedError(
                        'FoundationDB process class is invalid')
                command = f'setclass {address} {process_class}'
        elif kind == 'cluster' and operation == 'change_coordinators':
            addresses = draft.get('addresses')
            if not isinstance(addresses, list) or not (
                    1 <= len(addresses) <= 9):
                raise NativeDistributedError(
                    'FoundationDB coordinators require 1 to 9 addresses')
            addresses = [
                FoundationDBBackend._safe_token(
                    value, 'coordinator address',
                    r'[A-Za-z0-9_.:\-\[\]]+', 512
                ) for value in addresses
            ]
            if len(addresses) != len(set(addresses)):
                raise NativeDistributedError(
                    'FoundationDB coordinator addresses must be unique')
            command = 'coordinators ' + ' '.join(addresses)
            description = draft.get('description')
            if description:
                command += ' description=' + (
                    FoundationDBBackend._safe_token(
                        description, 'cluster description', limit=128
                    )
                )
        elif kind == 'cluster' and operation == 'maintenance_on':
            zone = FoundationDBBackend._safe_token(
                draft.get('zone_id'), 'maintenance zone'
            )
            seconds = draft.get('seconds')
            if isinstance(seconds, bool) or not isinstance(
                    seconds, int) or not 1 <= seconds <= 86400:
                raise NativeDistributedError(
                    'FoundationDB maintenance duration is invalid')
            command = f'maintenance on {zone} {seconds}'
        elif kind == 'cluster' and operation == 'maintenance_off':
            command = 'maintenance off'
        elif kind == 'configuration' and operation == 'configure':
            redundancy = draft.get('redundancy')
            storage = draft.get('storage_engine')
            if redundancy not in {
                    'single', 'double', 'triple', 'three_data_hall'}:
                raise NativeDistributedError(
                    'FoundationDB redundancy mode is invalid')
            if storage not in {
                    'ssd', 'memory', 'ssd-redwood-1-experimental'}:
                raise NativeDistributedError(
                    'FoundationDB storage engine is invalid')
            command = f'configure {redundancy} {storage}'
        elif kind == 'configuration' and operation == 'data_distribution':
            state = draft.get('state')
            if state not in {'on', 'off'}:
                raise NativeDistributedError(
                    'FoundationDB data distribution state is invalid')
            command = f'datadistribution {state}'
        elif kind == 'backup':
            tag = FoundationDBBackend._backup_tag(request)
            arguments = [operation, '-C', '__cluster_file__', '-t', tag]
            if operation == 'start':
                arguments.extend([
                    '-d', FoundationDBBackend._backup_url(
                        request, 'destination_url'),
                ])
                interval = draft.get('snapshot_interval_seconds')
                if interval is not None:
                    if isinstance(interval, bool) or not isinstance(
                            interval, int) or not 1 <= interval <= 31536000:
                        raise NativeDistributedError(
                            'FoundationDB snapshot interval is invalid')
                    arguments.extend(['-s', str(interval)])
                if not draft.get('stop_when_restorable', True):
                    arguments.append('--no-stop-when-done')
            elif operation == 'delete':
                arguments.extend([
                    '-d', FoundationDBBackend._backup_url(
                        request, 'destination_url'),
                ])
            return FoundationDBBackend._tool_plan(
                request, 'fdbbackup', arguments)
        elif kind == 'restore':
            tag = FoundationDBBackend._backup_tag(request)
            arguments = [
                operation, '--dest-cluster-file', '__cluster_file__',
                '-t', tag,
            ]
            if operation == 'start':
                arguments.extend([
                    '-r', FoundationDBBackend._backup_url(
                        request, 'source_url'),
                ])
                if draft.get('wait_for_done'):
                    arguments.append('--waitfordone')
            return FoundationDBBackend._tool_plan(
                request, 'fdbrestore', arguments)
        if command is None:
            raise NativeDistributedError(
                'FoundationDB control-plane operation is unavailable')
        return {
            'command': command,
            'impact': {
                'scope': 'cluster',
                'target_resource_id': (
                    request.get('target_resource') or {}
                ).get('resource_id'),
                'availability_risk': (
                    'high' if operation in {
                        'drop', 'change_coordinators', 'configure'
                    } else 'medium'
                ),
                'data_movement_possible': operation in {
                    'exclude', 'include', 'set_class',
                    'change_coordinators', 'configure', 'data_distribution',
                },
            },
        }

    @staticmethod
    def _tool_plan(request, tool, arguments):
        return {
            'tool': tool,
            'arguments': arguments,
            'impact': {
                'scope': 'cluster',
                'target_resource_id': (
                    request.get('target_resource') or {}
                ).get('resource_id'),
                'availability_risk': (
                    'high' if request.get('resource_kind') == 'restore'
                    else 'medium'
                ),
                'data_movement_possible': True,
            },
        }

    def _run_backup_tool(self, route, tool, arguments, timeout=120):
        key = 'fdbbackup_path' if tool == 'fdbbackup' else 'fdbrestore_path'
        executable = self._trusted_file(
            route.get(key), f'{tool} executable', executable=True
        )
        cluster_file = self._trusted_file(
            route.get('cluster_file'), 'cluster file')
        if not isinstance(arguments, list) or not arguments or len(
                arguments) > 64 or any(
                    not isinstance(value, str) or not value or
                    len(value) > 8192 or '\x00' in value
                    for value in arguments):
            raise NativeDistributedError(
                'FoundationDB backup arguments are invalid')
        command = [
            executable,
            *(cluster_file if value == '__cluster_file__' else value
              for value in arguments),
        ]
        try:
            result = subprocess.run(
                command, check=False, capture_output=True, text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise NativeDistributedError(
                'FoundationDB backup request failed') from exc
        output = (result.stdout or '') + (result.stderr or '')
        if len(output.encode('utf-8')) > self.MAX_CLI_BYTES:
            raise NativeDistributedError(
                'FoundationDB backup response exceeds size limit')
        if result.returncode != 0:
            raise NativeDistributedError(
                'FoundationDB backup request was rejected')
        return {
            'exit_code': 0, 'stdout': result.stdout or '',
            'stderr': result.stderr or '',
            'provider_response_observed': True,
            'provider_finality_only': True,
            'automatic_mutation_retry_by_cdeadmin': False,
        }

    @staticmethod
    def _operation_parts(request):
        plan = request.get('plan')
        payload = request.get('provider_payload')
        if not isinstance(plan, Mapping) or not isinstance(payload, Mapping):
            raise NativeDistributedError(
                'FoundationDB provider operation handle is invalid')
        return plan, payload

    def inspect_admin_operation(self, request):
        plan, payload = self._operation_parts(request)
        route = self._route(payload)
        if plan['resource_kind'] in {'backup', 'restore'}:
            kind = plan['resource_kind']
            tag = self._backup_tag(payload)
            arguments = [
                'status',
                '-C' if kind == 'backup' else '--dest-cluster-file',
                '__cluster_file__', '-t', tag,
            ]
            response = self._run_backup_tool(
                route, 'fdbbackup' if kind == 'backup' else 'fdbrestore',
                arguments, timeout=30,
            )
        elif plan['resource_kind'] == 'tenant':
            name = (
                (payload.get('draft') or {}).get('name')
                if plan['operation_id'] == 'create'
                else self._target(payload)
            )
            command = 'tenant get ' + self._safe_token(
                name, 'tenant name'
            )
            try:
                response = self._run_cli(route, command, timeout=15)
            except NativeDistributedError:
                response = {'tenant_present': False}
            else:
                response['tenant_present'] = True
        else:
            response = self._run_cli(route, 'status json', timeout=15)
            try:
                response['status'] = json.loads(response['stdout'])
            except (TypeError, ValueError) as exc:
                raise NativeDistributedError(
                    'FoundationDB status response is invalid') from exc
        return {
            'provider_observation': response,
            'provider_observation_only': True,
            'provider_finality_authority': True,
        }

    def cancel_admin_operation(self, request):
        plan, payload = self._operation_parts(request)
        kind = plan.get('resource_kind')
        if kind not in {'backup', 'restore'} or plan.get(
                'operation_id') != 'start':
            raise NativeDistributedError(
                'FoundationDB operation is not declared cancellable')
        tag = self._backup_tag(payload)
        arguments = [
            'abort',
            '-C' if kind == 'backup' else '--dest-cluster-file',
            '__cluster_file__', '-t', tag,
        ]
        response = self._run_backup_tool(
            self._route(payload),
            'fdbbackup' if kind == 'backup' else 'fdbrestore',
            arguments,
        )
        return {
            **response,
            'cancel_request_dispatched': True,
            'provider_finality_authority': True,
        }

    def validate_admin_post_state(self, request):
        plan, payload = self._operation_parts(request)
        observed = self.inspect_admin_operation(request)
        detail = observed['provider_observation']
        confirmed = False
        if plan['resource_kind'] == 'tenant':
            present = detail.get('tenant_present') is True
            confirmed = present == (plan['operation_id'] == 'create')
        elif plan['resource_kind'] == 'configuration':
            configuration = detail.get('status', {}).get(
                'cluster', {}).get('configuration', {})
            draft = payload.get('draft') or {}
            if plan['operation_id'] == 'configure':
                confirmed = (
                    configuration.get('redundancy_mode') ==
                    draft.get('redundancy') and
                    configuration.get('storage_engine') ==
                    draft.get('storage_engine')
                )
        return {
            'confirmed': confirmed,
            'reason': None if confirmed else (
                'provider_state_does_not_match_requested_state'
            ),
            'observation': observed,
            'provider_finality_authority': True,
        }

    def read_admin_rows(self, request):
        route = self._route(request)
        target = request.get('target_resource') or {
            'resource_kind': 'key-range',
            'display_path': ['bounded-key-browser'],
        }
        if not isinstance(target, Mapping) or target.get(
                'resource_kind') not in {'key', 'key-range'}:
            raise NativeDistributedError(
                'FoundationDB key paging requires a key range'
            )
        limit = request.get('limit', 200)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise NativeDistributedError('key page limit must be an integer')
        limit = max(1, min(limit, 500))
        start = request.get('start_key', '')
        end = request.get('end_key')
        if not isinstance(start, str) or (
            end is not None and not isinstance(end, str)
        ):
            raise NativeDistributedError('key range bounds must be text')
        begin = start.encode('utf-8')
        finish = b'\xff' if end is None else end.encode('utf-8')
        if finish <= begin:
            raise NativeDistributedError(
                'key range end must sort after its start'
            )
        database = self.open_session({'route': route})
        transaction = self._transaction(database)
        try:
            values = list(transaction.get_range(
                begin, finish, limit=limit + 1
            ))
        finally:
            transaction.reset()
        rows = []
        for item in values[:limit]:
            key = bytes(item.key)
            value = bytes(item.value)
            key_text, key_base64 = display_value(key)
            value_text, value_base64 = display_value(value)
            rows.append({
                'values': {
                    'key': key_text, 'key_base64': key_base64,
                    'value': value_text, 'value_base64': value_base64,
                },
                'identity_token': self._row_identities.issue(
                    route, target, key, value
                ),
            })
        return {
            'schema': 'cdeadmin.native-row-page.v1',
            'columns': [
                {'name': 'key', 'key': True, 'editable': False},
                {'name': 'key_base64', 'key': False, 'editable': False},
                {'name': 'value', 'key': False, 'editable': True},
                {'name': 'value_base64', 'key': False, 'editable': True},
            ],
            'rows': rows, 'editable': True,
            'complete': len(values) <= limit, 'limit': limit,
            'identity_policy': 'provider-key-and-original-value',
            'transaction_finality_interpreted_by_common_code': False,
        }

    @staticmethod
    def complete(_request): return []

    def close(self):
        self._databases.clear()
        self._database_routes.clear()
        self._row_identities.clear()
