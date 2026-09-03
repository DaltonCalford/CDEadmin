"""Qualified pyignite boundary for Apache Ignite 2.17.0."""

import copy
import hashlib
import importlib
import json
import ssl
import threading
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping

from .control_plane import (
    CATALOG as CONTROL_CATALOG,
    apply_catalog as apply_control_catalog,
    cancel_action,
    compile_action,
    execute_action,
    inspect_action,
)
from ..distributed_sql import resource
from ..native_distributed import (
    NativeDistributedError,
    NativeResult,
)


class IgniteBackend:
    ROUTE_FIELDS = frozenset({
        'route_id', 'host', 'port', 'contact_points', 'rest_port',
        'rest_scheme', 'username', 'principal_reference',
        'credential_reference_id', 'credential_references',
        'credential_kinds', 'credential_kind', 'auth_mode', 'tls_mode',
        'tls_ca_file', 'tls_certificate_file', 'tls_key_file',
        'tls_check_hostname', 'tls_min_version', 'tls_ciphers', 'timeout',
        'handshake_timeout', 'rest_timeout', 'partition_aware',
        'compact_footer', 'transaction_concurrency',
        'transaction_isolation', 'transaction_timeout',
        'transaction_label', 'control_sh_path', 'control_host',
        'control_port', 'tool_workspace',
    })

    def __init__(self, secret_acquirer=None, module=None):
        try:
            self.module = module or importlib.import_module('pyignite')
        except ImportError as exc:
            raise NativeDistributedError(
                'Apache Ignite requires the pyignite package'
            ) from exc
        self.secret_acquirer = secret_acquirer
        self._clients = []
        self._row_identities = {}
        self._identity_lock = threading.RLock()

    @staticmethod
    def _route(request):
        route = request.get('route') or request.get('_provider_route')
        if not isinstance(route, dict):
            raise NativeDistributedError('Ignite route is required')
        route = copy.deepcopy(route)
        unknown = sorted(set(route) - IgniteBackend.ROUTE_FIELDS)
        if unknown:
            raise NativeDistributedError(
                'Ignite route contains unknown fields: ' + ', '.join(unknown)
            )
        route.setdefault('host', '127.0.0.1')
        route.setdefault('port', 10800)
        route.setdefault('rest_port', 8080)
        route.setdefault('rest_scheme', 'http')
        route.setdefault('auth_mode', 'none')
        route.setdefault('tls_mode', 'disabled')
        route.setdefault('timeout', 10)
        route.setdefault('handshake_timeout', 10)
        route.setdefault('rest_timeout', 10)
        route.setdefault('partition_aware', True)
        route.setdefault('compact_footer', True)
        route.setdefault('transaction_concurrency', 'pessimistic')
        route.setdefault('transaction_isolation', 'repeatable-read')
        route.setdefault('transaction_timeout', 0)
        if route['auth_mode'] not in {'none', 'username-password'}:
            raise NativeDistributedError('Ignite authentication is invalid')
        if route['tls_mode'] not in {
            'disabled', 'required', 'verify-ca'
        }:
            raise NativeDistributedError('Ignite TLS mode is invalid')
        if route['rest_scheme'] not in {'http', 'https'}:
            raise NativeDistributedError('Ignite REST scheme is invalid')
        for field in ('port', 'rest_port'):
            if isinstance(route[field], bool) or not isinstance(
                    route[field], int) or not 1 <= route[field] <= 65535:
                raise NativeDistributedError(f'Ignite {field} is invalid')
        for field in ('timeout', 'handshake_timeout', 'rest_timeout'):
            value = route[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not 0.1 <= value <= 3600:
                raise NativeDistributedError(f'Ignite {field} is invalid')
        if isinstance(route['transaction_timeout'], bool) or not isinstance(
                route['transaction_timeout'], int) or not (
                0 <= route['transaction_timeout'] <= 86400000):
            raise NativeDistributedError(
                'Ignite transaction timeout is invalid')
        for field in ('partition_aware', 'compact_footer'):
            if not isinstance(route[field], bool):
                raise NativeDistributedError(f'Ignite {field} is invalid')
        if route['transaction_concurrency'] not in {
            'optimistic', 'pessimistic'
        }:
            raise NativeDistributedError(
                'Ignite transaction concurrency is invalid')
        if route['transaction_isolation'] not in {
            'read-committed', 'repeatable-read', 'serializable'
        }:
            raise NativeDistributedError(
                'Ignite transaction isolation is invalid')
        references = dict(route.get('credential_references') or {})
        if route.get('credential_reference_id'):
            references.setdefault('database_password',
                                  route['credential_reference_id'])
        allowed = {'database_password', 'tls_private_key_password'}
        if set(references) - allowed:
            raise NativeDistributedError(
                'Ignite credential kinds are invalid')
        if route['auth_mode'] == 'username-password' and (
                not route.get('username') or
                'database_password' not in references):
            raise NativeDistributedError(
                'Ignite username/password authentication requires both '
                'username and a password reference')
        if references and not route.get('principal_reference'):
            raise NativeDistributedError(
                'Ignite credentials require a principal reference')
        if bool(route.get('tls_certificate_file')) != bool(
                route.get('tls_key_file')):
            raise NativeDistributedError(
                'Ignite client certificate and key must be supplied together')
        if route['tls_mode'] == 'verify-ca' and not route.get('tls_ca_file'):
            raise NativeDistributedError(
                'verified Ignite TLS requires a CA file')
        route['credential_references'] = references
        points = route.get('contact_points') or []
        if isinstance(points, str):
            points = [
                item.strip() for item in points.split(',') if item.strip()
            ]
        parsed = [(route['host'], route['port'])]
        for point in points:
            if isinstance(point, str):
                host, separator, port = point.rpartition(':')
                if not separator:
                    host, port = point, route['port']
                try:
                    point = (host, int(port))
                except (TypeError, ValueError):
                    raise NativeDistributedError(
                        'Ignite contact point is invalid') from None
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise NativeDistributedError(
                    'Ignite contact point is invalid')
            parsed.append((str(point[0]), int(point[1])))
        route['contact_points'] = list(dict.fromkeys(parsed))
        return route

    def _with_secrets(self, route, callback):
        bindings = sorted(route.get('credential_references', {}).items())
        if not bindings:
            return callback({})
        if not callable(self.secret_acquirer):
            raise NativeDistributedError(
                'Ignite secret binding is unavailable')
        principal = route['principal_reference']

        def acquire(index, values):
            if index == len(bindings):
                return callback(values)
            kind, reference = bindings[index]
            lease = self.secret_acquirer(reference, principal, 'connect', kind)
            with lease:
                return lease.use(lambda view: acquire(
                    index + 1, {**values, kind: bytes(view).decode('utf-8')}
                ))
        return acquire(0, {})

    def _rest(self, route, command):
        url = (
            f"{route['rest_scheme']}://{route['host']}:"
            f"{int(route.get('rest_port', 8080))}/ignite"
        )

        def perform(secrets):
            parameters = {'cmd': command}
            if route['auth_mode'] == 'username-password':
                parameters['user'] = route['username']
                parameters['password'] = secrets['database_password']
            body = urllib.parse.urlencode(parameters).encode('utf-8')
            request = urllib.request.Request(
                url, data=body,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                method='POST',
            )
            context = None
            if route['rest_scheme'] == 'https':
                context = ssl.create_default_context(
                    cafile=route.get('tls_ca_file'))
                if route['tls_mode'] == 'required':
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                if route.get('tls_certificate_file'):
                    context.load_cert_chain(
                        route['tls_certificate_file'], route['tls_key_file'],
                        secrets.get('tls_private_key_password'))
            with urllib.request.urlopen(
                    request, timeout=route['rest_timeout'], context=context
            ) as response:
                return json.loads(response.read(1024 * 1024))
        value = self._with_secrets(route, perform)
        if value.get('successStatus') != 0:
            raise NativeDistributedError('Ignite REST operation failed')
        return value.get('response')

    def runtime_identity(self, request, _handle=None):
        route = self._route(request)
        version = str(self._rest(route, 'version'))
        return {
            'engine_id': 'apache_ignite', 'version': version,
            'build_id': f'apache_ignite:{version}',
            'protocol_id': 'ignite_thin',
        }

    def open_session(self, request):
        route = self._route(request)

        def connect(secrets):
            options = {
                'timeout': float(route['timeout']),
                'handshake_timeout': float(route['handshake_timeout']),
                'partition_aware': route['partition_aware'],
                'compact_footer': route['compact_footer'],
                'use_ssl': route['tls_mode'] != 'disabled',
            }
            if route['tls_mode'] != 'disabled':
                options['ssl_cert_reqs'] = (
                    ssl.CERT_NONE if route['tls_mode'] == 'required'
                    else ssl.CERT_REQUIRED
                )
                options['ssl_ca_certfile'] = route.get('tls_ca_file')
                options['ssl_certfile'] = route.get('tls_certificate_file')
                options['ssl_keyfile'] = route.get('tls_key_file')
                options['ssl_keyfile_password'] = secrets.get(
                    'tls_private_key_password')
                options['ssl_version'] = getattr(
                    ssl, 'PROTOCOL_TLS_CLIENT', ssl.PROTOCOL_TLS)
                if route.get('tls_ciphers'):
                    options['ssl_ciphers'] = route['tls_ciphers']
            if route['auth_mode'] == 'username-password':
                options['username'] = route['username']
                options['password'] = secrets['database_password']
            value = self.module.Client(**options)
            value.connect(route['contact_points'])
            value._cdeadmin_route = route
            return value
        client = self._with_secrets(route, connect)
        self._clients.append(client)
        return client

    def list_resources(self, request):
        route = self._route(request)
        client = self.open_session(request)
        generation = str(request.get('capability_generation') or 'current')
        values = [
            resource('cluster', [], 'Apache Ignite', generation),
            resource(
                'baseline-topology', [], 'Baseline topology', generation
            ),
        ]
        try:
            for node in self._rest(route, 'top') or []:
                node_id = str(
                    node.get('consistentId') or node.get('nodeId') or ''
                )
                if node_id:
                    values.append(resource(
                        'node', [], node_id, generation, {
                            'tcp_addresses': node.get('tcpAddresses', []),
                            'tcp_port': node.get('tcpPort'),
                            'client': bool(node.get('clientMode', False)),
                        }
                    ))
        except Exception:
            pass
        for name in client.get_cache_names():
            values.append(resource('cache', [], name, generation))
        try:
            cursor = client.sql(
                'SELECT SCHEMA_NAME, TABLE_NAME FROM SYS.TABLES '
                'ORDER BY SCHEMA_NAME, TABLE_NAME'
            )
            for schema, table in cursor:
                values.append(resource('sql-schema', [], schema, generation))
                values.append(resource(
                    'table', [schema], table, generation
                ))
        except Exception:
            pass
        return values

    @staticmethod
    def describe_transaction(handle):
        route = getattr(handle, '_cdeadmin_route', {})
        return {
            'native_state': 'ignite-thin-session',
            'configured_concurrency': route.get('transaction_concurrency'),
            'configured_isolation': route.get('transaction_isolation'),
            'configured_timeout': route.get('transaction_timeout'),
            'provider_owned_finality': True,
            'automatic_mutation_retry_by_cdeadmin': False,
        }

    def execute(self, handle, command, _parameters):
        if not isinstance(command, dict):
            command = {'operation': 'sql', 'source': command}
        operation = command.get('operation')
        if operation == 'sql':
            cursor = handle.sql(str(command.get('source', '')))
            rows = [list(row) for row in cursor]
            return NativeResult(
                'key_value', 'entries', rows,
                {'columns': list(getattr(cursor, 'field_names', ()) or ())},
                {'operation': 'sql'},
            )
        cache = handle.get_cache(str(command.get('cache')))
        key = command.get('key')
        if operation == 'get':
            rows = [{'key': key, 'value': cache.get(key)}]
        elif operation == 'put':
            cache.put(key, command.get('value'))
            rows = [{'key': key, 'accepted': True}]
        elif operation == 'remove':
            rows = [{'key': key, 'removed': bool(cache.remove_key(key))}]
        else:
            raise NativeDistributedError('Ignite operation is unsupported')
        return NativeResult(
            'key_value', 'entries', rows, {'fields': ['key', 'value']},
            {'operation': operation},
        )

    @staticmethod
    def cancel(_token):
        return False

    def describe_security(self, request):
        generation = str(request.get('capability_generation') or 'current')
        return resource('user', [], 'current-user', generation, {
            'authorization_model': 'ignite-native-authentication',
        })

    @staticmethod
    def _field(field_id, label, control='text', required=False):
        return {
            'field_id': field_id, 'label': label, 'control': control,
            'required': required,
        }

    def visual_admin_catalog(self, catalog):
        value = copy.deepcopy(dict(catalog))
        value['key_identity'] = 'cache-plus-provider-issued-key-token'
        value['transaction_authority'] = 'ignite-server-owned'
        value['automatic_mutation_retry'] = False
        for resource in value.get('objects', []):
            if resource['resource_kind'] != 'cache':
                continue
            for operation in resource.get('operations', []):
                operation_id = operation['operation_id']
                if operation_id == 'create':
                    fields = [
                        self._field('name', 'Name', required=True),
                        {
                            **self._field(
                                'cache_mode', 'Cache mode', 'select', True,
                            ),
                            'default': 'PARTITIONED',
                            'options': [
                                {'value': 'LOCAL', 'label': 'Local'},
                                {'value': 'REPLICATED',
                                 'label': 'Replicated'},
                                {'value': 'PARTITIONED',
                                 'label': 'Partitioned'},
                            ],
                        },
                        {
                            **self._field(
                                'atomicity_mode', 'Atomicity mode',
                                'select', True,
                            ),
                            'default': 'ATOMIC',
                            'options': [
                                {'value': 'ATOMIC', 'label': 'Atomic'},
                                {'value': 'TRANSACTIONAL',
                                 'label': 'Transactional'},
                            ],
                        },
                        {
                            **self._field(
                                'backups_number', 'Synchronous backups',
                                'number', False,
                            ),
                            'default': 1, 'minimum': 0, 'maximum': 32,
                        },
                        {
                            **self._field(
                                'write_synchronization',
                                'Write synchronization', 'select', True,
                            ),
                            'default': 'FULL_SYNC',
                            'options': [
                                {'value': 'FULL_SYNC', 'label': 'Full sync'},
                                {'value': 'PRIMARY_SYNC',
                                 'label': 'Primary sync'},
                                {'value': 'FULL_ASYNC',
                                 'label': 'Full async'},
                            ],
                        },
                        self._field(
                            'data_region_name', 'Data region', 'text', False
                        ),
                        {
                            **self._field(
                                'read_from_backup', 'Read from backups',
                                'boolean', False,
                            ), 'default': True,
                        },
                        {
                            **self._field(
                                'copy_on_read', 'Copy on read', 'boolean',
                                False,
                            ), 'default': True,
                        },
                        {
                            **self._field(
                                'onheap_cache', 'On-heap cache', 'boolean',
                                False,
                            ), 'default': False,
                        },
                        {
                            **self._field(
                                'statistics_enabled', 'Statistics enabled',
                                'boolean', False,
                            ), 'default': False,
                        },
                        self._field(
                            'cache_group', 'Cache group', 'text', False
                        ),
                    ]
                elif operation_id == 'insert':
                    fields = [
                        self._field('key', 'Key', required=True),
                        self._field('value', 'Value', required=True),
                    ]
                elif operation_id == 'update':
                    fields = [
                        self._field(
                            'selector', 'Provider row identity', 'json', True
                        ),
                        self._field(
                            'value', 'Replacement value', required=True
                        ),
                    ]
                elif operation_id == 'delete':
                    fields = [
                        self._field(
                            'selector', 'Provider row identity', 'json', True
                        ),
                        self._field(
                            'confirmation', 'Confirmation', required=True
                        ),
                    ]
                else:
                    continue
                operation['form'] = {
                    'form_id': f'ignite-cache-{operation_id}',
                    'title': f'{operation_id.title()} cache entry',
                    'fields': fields,
                }
        return apply_control_catalog(value)

    @staticmethod
    def validate_admin_operation(request):
        kind = request.get('resource_kind')
        operation = request.get('operation_id')
        draft = request.get('draft') or {}
        if CONTROL_CATALOG.supports(kind, operation):
            return CONTROL_CATALOG.validate(request)
        errors = []
        if kind == 'cache' and operation == 'create':
            backups = draft.get('backups_number', 1)
            if isinstance(backups, bool) or not isinstance(backups, int) or (
                    not 0 <= backups <= 32):
                errors.append({
                    'field_id': 'backups_number', 'code': 'range',
                    'message': 'Ignite backups must be between 0 and 32.',
                })
            admitted = {
                'cache_mode': {'LOCAL', 'REPLICATED', 'PARTITIONED'},
                'atomicity_mode': {'ATOMIC', 'TRANSACTIONAL'},
                'write_synchronization': {
                    'FULL_SYNC', 'PRIMARY_SYNC', 'FULL_ASYNC',
                },
            }
            for field, values in admitted.items():
                value = draft.get(field)
                if value is not None and value not in values:
                    errors.append({
                        'field_id': field, 'code': 'choice',
                        'message': f'Ignite {field} is invalid.',
                    })
            for field in (
                    'read_from_backup', 'copy_on_read', 'onheap_cache',
                    'statistics_enabled'):
                if field in draft and not isinstance(draft[field], bool):
                    errors.append({
                        'field_id': field, 'code': 'type',
                        'message': f'Ignite {field} must be boolean.',
                    })
        if kind == 'cache' and operation in {'insert', 'update', 'delete'}:
            if operation == 'insert':
                for field in ('key', 'value'):
                    if not isinstance(draft.get(field), str):
                        errors.append({
                            'field_id': field, 'code': 'type',
                            'message': f'Ignite cache {field} must be text.',
                        })
            else:
                selector = draft.get('selector')
                if not isinstance(selector, Mapping) or not isinstance(
                        selector.get('identity_token'), str):
                    errors.append({
                        'field_id': 'selector',
                        'code': 'provider_identity_required',
                        'message': (
                            'A provider-issued cache row identity is required.'
                        ),
                    })
        return {'errors': errors}

    @staticmethod
    def plan_admin_operation(request):
        if CONTROL_CATALOG.supports(
                request.get('resource_kind'), request.get('operation_id')):
            route = request.get('_provider_route')
            if not isinstance(route, Mapping) or not route:
                raise NativeDistributedError(
                    'Ignite administration requires a trusted route')
            checked = CONTROL_CATALOG.validate(request)
            if checked['errors']:
                raise NativeDistributedError(
                    'Ignite control-plane request is invalid')
            compiled = compile_action(copy.deepcopy(dict(request)))
            return {
                'command_preview': {
                    'engine_id': 'apache_ignite',
                    'operation': request['operation_id'],
                    'statements': [copy.deepcopy(
                        compiled['action_preview'])],
                    'provider_constructed': True,
                },
                'provider_payload': {
                    '_provider_route': copy.deepcopy(dict(route)),
                    'compiled': compiled,
                },
                'warnings': [],
                'impact': copy.deepcopy(compiled['impact']),
                'receipt': {
                    'planner': 'cdeadmin.apache-ignite-control-plane.v1',
                    'provider_finality_authority': True,
                    'automatic_mutation_retry': False,
                },
            }
        return {
            'command_preview': {
                'operation': request['operation_id'],
                'resource_kind': request['resource_kind'],
                'provider_constructed': True,
            },
            'provider_payload': dict(request), 'warnings': [],
            'receipt': {'planner': 'apache-ignite-native'},
        }

    @staticmethod
    def _route_fingerprint(route):
        value = {
            key: route.get(key) for key in (
                'host', 'port', 'contact_points', 'partition_aware'
            ) if key in route
        }
        return hashlib.sha256(json.dumps(
            value, sort_keys=True, default=str,
            separators=(',', ':'),
        ).encode('utf-8')).hexdigest()

    def _consume_identity(self, route, cache_name, selector):
        token = selector.get('identity_token') if isinstance(
            selector, Mapping
        ) else None
        if not isinstance(token, str) or not token:
            raise NativeDistributedError(
                'provider-issued cache row identity is required'
            )
        with self._identity_lock:
            identity = self._row_identities.pop(token, None)
        if identity is None:
            raise NativeDistributedError(
                'cache row identity is stale or invalid'
            )
        fingerprint, identity_cache, key, original, issued = identity
        if time.monotonic() - issued > 600:
            raise NativeDistributedError('cache row identity has expired')
        if fingerprint != self._route_fingerprint(route) or (
                identity_cache != cache_name):
            raise NativeDistributedError(
                'cache row identity belongs to another target'
            )
        return key, original

    def apply_admin_operation(self, request):
        payload = request['provider_payload']
        plan = request.get('plan') or {}
        if CONTROL_CATALOG.supports(
                plan.get('resource_kind'), plan.get('operation_id')):
            return execute_action(request)
        route = payload['_provider_route']
        client = self.open_session({'route': route})
        kind = payload['resource_kind']
        operation = payload['operation_id']
        draft = payload.get('draft', {})
        target = payload.get('target_resource') or {}
        if kind != 'cache':
            if operation == 'inspect':
                return {'accepted': True, 'resource': target}
            raise NativeDistributedError('Ignite admin operation unsupported')
        name = draft.get('name') or target.get('display_name')
        if operation == 'create':
            modes = {
                'cache_mode': {
                    'LOCAL': 0, 'REPLICATED': 1, 'PARTITIONED': 2,
                },
                'atomicity_mode': {'TRANSACTIONAL': 0, 'ATOMIC': 1},
                'write_synchronization': {
                    'FULL_SYNC': 0, 'FULL_ASYNC': 1, 'PRIMARY_SYNC': 2,
                },
            }
            settings = {
                0: str(name),
                1: modes['cache_mode'][draft.get(
                    'cache_mode', 'PARTITIONED')],
                2: modes['atomicity_mode'][draft.get(
                    'atomicity_mode', 'ATOMIC')],
                3: int(draft.get('backups_number', 1)),
                4: modes['write_synchronization'][draft.get(
                    'write_synchronization', 'FULL_SYNC')],
                5: bool(draft.get('copy_on_read', True)),
                6: bool(draft.get('read_from_backup', True)),
                101: bool(draft.get('onheap_cache', False)),
                406: bool(draft.get('statistics_enabled', False)),
            }
            if draft.get('data_region_name'):
                settings[100] = str(draft['data_region_name'])
            if draft.get('cache_group'):
                settings[400] = str(draft['cache_group'])
            client.create_cache(settings)
        elif operation == 'drop':
            client.get_cache(str(name)).destroy()
        elif operation == 'insert':
            cache = client.get_cache(str(name))
            accepted = cache.put_if_absent(draft['key'], draft['value'])
            if not accepted:
                raise NativeDistributedError('Ignite cache key already exists')
        elif operation in {'update', 'delete'}:
            cache = client.get_cache(str(name))
            key, original = self._consume_identity(
                route, str(name), draft.get('selector')
            )
            if operation == 'update':
                accepted = cache.replace_if_equals(
                    key, original, draft['value']
                )
            else:
                accepted = cache.remove_if_equals(key, original)
            if not accepted:
                raise NativeDistributedError(
                    'Ignite cache row changed after it was displayed'
                )
        elif operation == 'inspect':
            return {'accepted': True, 'resource': target}
        else:
            raise NativeDistributedError('Ignite cache operation unsupported')
        return {
            'accepted': True, 'native_operation': operation,
            'provider_native_outcome': True,
        }

    @staticmethod
    def inspect_admin_operation(request):
        return inspect_action(request)

    @staticmethod
    def cancel_admin_operation(request):
        return cancel_action(request)

    @staticmethod
    def validate_admin_post_state(request):
        observation = inspect_action(request)
        return {
            'confirmed': False,
            'reason': 'ignite_provider_state_requires_semantic_review',
            'observation': observation,
            'provider_finality_authority': True,
        }

    def read_admin_rows(self, request):
        target = request['target_resource']
        client = self.open_session({'route': request['_provider_route']})
        cache = client.get_cache(target['display_name'])
        rows = []
        limit = min(max(int(request.get('limit', 200)), 1), 500)
        fingerprint = self._route_fingerprint(request['_provider_route'])
        complete = True
        for index, (key, value) in enumerate(cache.scan()):
            if index >= limit:
                complete = False
                break
            token = str(uuid.uuid4())
            with self._identity_lock:
                while len(self._row_identities) >= 5000:
                    oldest = next(iter(self._row_identities))
                    self._row_identities.pop(oldest, None)
                self._row_identities[token] = (
                    fingerprint, target['display_name'], key, value,
                    time.monotonic(),
                )
            rows.append({
                'values': {'key': key, 'value': value},
                'identity_token': token,
            })
        return {
            'schema': 'cdeadmin.native-row-page.v1',
            'columns': [
                {'name': 'key', 'key': True, 'editable': False},
                {'name': 'value', 'key': False, 'editable': True},
            ],
            'rows': rows, 'editable': True, 'complete': complete,
            'identity_policy': 'provider-cache-key-and-original-value',
            'limit': limit,
            'transaction_finality_interpreted_by_common_code': False,
        }

    @staticmethod
    def complete(_request):
        return []

    def close(self):
        for client in self._clients:
            client.close()
        self._clients.clear()
        with self._identity_lock:
            self._row_identities.clear()
