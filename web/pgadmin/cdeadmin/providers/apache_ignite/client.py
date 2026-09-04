"""Qualified pyignite boundary for Apache Ignite 2.17.0."""

import copy
import hashlib
import importlib
import json
import re
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
        'control_port', 'tool_workspace', 'control_ssl_protocols',
        'control_ssl_ciphers', 'control_ssl_key_algorithm',
        'control_ssl_factory', 'control_keystore_type',
        'control_keystore_path', 'control_truststore_type',
        'control_truststore_path',
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
        self._sql_row_identities = {}
        self._known_snapshots = {}
        self._identity_lock = threading.RLock()

    @staticmethod
    def _identifier(value, label='identifier'):
        if not isinstance(value, str) or not value or len(value) > 256 or (
                '\x00' in value):
            raise NativeDistributedError(f'Ignite {label} is invalid')
        return '"' + value.replace('"', '""') + '"'

    @staticmethod
    def _sql_type(value):
        if not isinstance(value, str):
            raise NativeDistributedError('Ignite SQL type is invalid')
        admitted = re.fullmatch(
            r'\s*(BOOLEAN|TINYINT|SMALLINT|INT|INTEGER|BIGINT|REAL|FLOAT|'
            r'DOUBLE|DECIMAL(?:\(\d{1,4}(?:,\d{1,4})?\))?|NUMERIC(?:\('
            r'\d{1,4}(?:,\d{1,4})?\))?|DATE|TIME|TIMESTAMP|UUID|CHAR(?:'
            r'\(\d{1,9}\))?|VARCHAR(?:\(\d{1,9}\))?|BINARY(?:\(\d{1,9}'
            r'\))?|VARBINARY(?:\(\d{1,9}\))?|GEOMETRY)\s*',
            value, re.IGNORECASE,
        )
        if admitted is None:
            raise NativeDistributedError('Ignite SQL type is invalid')
        return admitted.group(1).upper()

    @staticmethod
    def _sql_rows(client, source, parameters=(), schema='PUBLIC'):
        try:
            cursor = client.sql(
                source, query_args=list(parameters), schema=schema,
                include_field_names=True,
            )
            records = list(cursor)
        except Exception as exc:
            raise NativeDistributedError(
                'Ignite catalog query failed'
            ) from exc
        if not records:
            return []
        fields = [str(item) for item in records[0]]
        if len(set(fields)) != len(fields):
            raise NativeDistributedError(
                'Ignite catalog returned duplicate fields')
        return [dict(zip(fields, row)) for row in records[1:]]

    @staticmethod
    def _target_parts(target, expected):
        if not isinstance(target, Mapping) or target.get(
                'resource_kind') != expected:
            raise NativeDistributedError(
                f'Ignite {expected} target is required')
        path = target.get('display_path')
        if not isinstance(path, list) or not path or any(
                not isinstance(item, str) or not item for item in path):
            raise NativeDistributedError(
                f'Ignite {expected} target path is invalid')
        return path

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
        allowed = {
            'database_password', 'tls_private_key_password',
            'control_keystore_password', 'control_truststore_password',
        }
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
        values = {}

        def add(kind, path, name, native=None):
            item = resource(kind, path, name, generation, native)
            previous = values.get(item['resource_id'])
            if previous and native:
                item['native'] = {
                    **copy.deepcopy(previous.get('native') or {}),
                    **copy.deepcopy(dict(native)),
                }
            values[item['resource_id']] = item

        cluster_state = client.get_cluster().get_state()
        add('cluster', [], 'Apache Ignite', {
            'state': {0: 'INACTIVE', 1: 'ACTIVE', 2: 'ACTIVE_READ_ONLY'}.get(
                cluster_state, f'UNKNOWN({cluster_state})'),
        })
        add('baseline-topology', [], 'Baseline topology')
        topology = self._rest(route, 'top')
        if not isinstance(topology, list):
            raise NativeDistributedError(
                'Ignite topology response is invalid')
        for node in topology:
            if not isinstance(node, Mapping):
                raise NativeDistributedError(
                    'Ignite topology node is invalid')
            node_id = str(
                node.get('consistentId') or node.get('nodeId') or '')
            if node_id:
                add('node', [], node_id, {
                    'node_id': str(node.get('nodeId') or ''),
                    'tcp_addresses': node.get('tcpAddresses', []),
                    'tcp_port': node.get('tcpPort'),
                    'client': bool(node.get('clientMode', False)),
                })
        system_nodes = self._sql_rows(
            client, 'SELECT * FROM SYS.NODES ORDER BY NODE_ORDER')
        for row in system_nodes:
            node_id = str(row['CONSISTENT_ID'])
            add('node', [], node_id, row)
        for template in ('PARTITIONED', 'REPLICATED'):
            add('cache-template', [], template, {
                'built_in': True, 'cache_mode': template,
            })
        caches = self._sql_rows(
            client, 'SELECT * FROM SYS.CACHES ORDER BY CACHE_NAME')
        for row in caches:
            name = str(row['CACHE_NAME'])
            add('cache', [], name, row)
            region = str(row.get('DATA_REGION_NAME') or 'default')
            add('data-region', [], region, {
                'observed_from_cache': name,
            })
            add('ttl', [name], 'expiry-policy', {
                'expiry_policy_factory': row.get('EXPIRY_POLICY_FACTORY'),
                'has_expiring_entries': row.get('HAS_EXPIRING_ENTRIES'),
                'eager_ttl': row.get('IS_EAGER_TTL'),
                'remaining_ttl_available': False,
            })
        schemas = self._sql_rows(
            client, 'SELECT SCHEMA_NAME, PREDEFINED FROM SYS.SCHEMAS '
            'ORDER BY SCHEMA_NAME')
        for row in schemas:
            add('sql-schema', [], str(row['SCHEMA_NAME']), row)
        tables = self._sql_rows(
            client, 'SELECT * FROM SYS.TABLES ORDER BY SCHEMA_NAME, '
            'TABLE_NAME')
        for row in tables:
            add('table', [str(row['SCHEMA_NAME'])],
                str(row['TABLE_NAME']), row)
        columns = self._sql_rows(
            client, "SELECT * FROM SYS.TABLE_COLUMNS WHERE COLUMN_NAME NOT "
            "IN ('_KEY', '_VAL') ORDER BY SCHEMA_NAME, TABLE_NAME, "
            'COLUMN_NAME')
        primary_keys = {}
        for row in columns:
            schema = str(row['SCHEMA_NAME'])
            table = str(row['TABLE_NAME'])
            add('column', [schema, table], str(row['COLUMN_NAME']), row)
            if bool(row.get('PK')):
                primary_keys.setdefault((schema, table), []).append(
                    str(row['COLUMN_NAME']))
        for (schema, table), names in primary_keys.items():
            add('constraint', [schema, table], 'PRIMARY_KEY', {
                'constraint_type': 'PRIMARY KEY', 'columns': names,
            })
        indexes = self._sql_rows(
            client, 'SELECT * FROM SYS.INDEXES ORDER BY SCHEMA_NAME, '
            'TABLE_NAME, INDEX_NAME')
        for row in indexes:
            add('index', [str(row['SCHEMA_NAME']), str(row['TABLE_NAME'])],
                str(row['INDEX_NAME']), row)
        views = self._sql_rows(
            client, 'SELECT SCHEMA, NAME, SQL, DESCRIPTION FROM SYS.VIEWS '
            "WHERE SCHEMA <> 'SYS' AND SQL IS NOT NULL ORDER BY SCHEMA, NAME"
        )
        for row in views:
            add('view', [str(row['SCHEMA'])], str(row['NAME']), row)
        services = self._sql_rows(
            client, 'SELECT * FROM SYS.SERVICES ORDER BY NAME')
        for row in services:
            add('service', [], str(row['NAME']), row)
        tasks = self._sql_rows(
            client, 'SELECT * FROM SYS.TASKS ORDER BY START_TIME, ID')
        for row in tasks:
            add('compute-task', [], str(row['SESSION_ID']), row)
        replicas = self._sql_rows(
            client, 'SELECT CACHE_GROUP_ID, NODE_ID, STATE, IS_PRIMARY, '
            'COUNT(*) AS PARTITION_COUNT FROM SYS.PARTITION_STATES GROUP BY '
            'CACHE_GROUP_ID, NODE_ID, STATE, IS_PRIMARY ORDER BY '
            'CACHE_GROUP_ID, NODE_ID, STATE, IS_PRIMARY')
        for row in replicas:
            group = str(row['CACHE_GROUP_ID'])
            node = str(row['NODE_ID'])
            role = 'primary' if bool(row['IS_PRIMARY']) else 'backup'
            add('replica', [group], f'{node}:{role}:{row["STATE"]}', row)
        if route.get('username'):
            add('user', [], str(route['username']), {
                'current_user': True,
                'enumeration_available': False,
            })
        with self._identity_lock:
            snapshots = copy.deepcopy(self._known_snapshots.get(
                self._route_fingerprint(route), {}))
        for name, native in snapshots.items():
            add('snapshot', [], name, native)
        return list(values.values())

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

    @classmethod
    def _table_fields(cls, operation):
        if operation == 'create':
            return [
                {**cls._field('schema', 'Schema', required=True),
                 'default': 'PUBLIC'},
                cls._field('name', 'Table name', required=True),
                cls._field('columns', 'Column definitions', 'json', True),
                {**cls._field('template', 'Cache template', 'select', True),
                 'default': 'PARTITIONED', 'options': [
                     {'value': 'PARTITIONED', 'label': 'Partitioned'},
                     {'value': 'REPLICATED', 'label': 'Replicated'},
                 ]},
                {**cls._field('backups', 'Backup copies', 'number'),
                 'default': 1, 'minimum': 0, 'maximum': 32},
                {**cls._field('atomicity', 'Atomicity', 'select', True),
                 'default': 'ATOMIC', 'options': [
                     {'value': 'ATOMIC', 'label': 'Atomic'},
                     {'value': 'TRANSACTIONAL', 'label': 'Transactional'},
                 ]},
                cls._field('cache_name', 'Underlying cache name'),
                cls._field('cache_group', 'Cache group'),
                cls._field('affinity_key', 'Affinity-key column'),
                cls._field('data_region', 'Data region'),
            ]
        if operation == 'alter':
            return [
                cls._field('add_columns', 'Columns to add', 'json'),
                cls._field('drop_columns', 'Columns to drop', 'json'),
                {**cls._field('logging', 'Persistence logging', 'select'),
                 'options': [
                     {'value': 'unchanged', 'label': 'Unchanged'},
                     {'value': 'enabled', 'label': 'Enabled'},
                     {'value': 'disabled', 'label': 'Disabled'},
                 ], 'default': 'unchanged'},
            ]
        if operation == 'insert':
            return [cls._field('values', 'Column values', 'json', True)]
        if operation == 'update':
            return [
                cls._field('selector', 'Provider row identity', 'json', True),
                cls._field('changes', 'Changed column values', 'json', True),
            ]
        if operation == 'delete':
            return [
                cls._field('selector', 'Provider row identity', 'json', True),
                cls._field('confirmation', 'Confirmation', required=True),
            ]
        return None

    @classmethod
    def _view_fields(cls, operation):
        if operation in {'create', 'alter'}:
            fields = []
            if operation == 'create':
                fields.extend([
                    {**cls._field('schema', 'Schema', required=True),
                     'default': 'PUBLIC'},
                    cls._field('name', 'View name', required=True),
                ])
            fields.append(cls._field(
                'query', 'View SELECT query', 'multiline', True))
            return fields
        return None

    @classmethod
    def _column_fields(cls, operation):
        if operation == 'create':
            return [
                {**cls._field('schema', 'Schema', required=True),
                 'default': 'PUBLIC'},
                cls._field('table', 'Table name', required=True),
                cls._field('name', 'Column name', required=True),
                cls._field('data_type', 'Data type', required=True),
                {**cls._field('nullable', 'Nullable', 'boolean'),
                 'default': True},
            ]
        return None

    @classmethod
    def _index_fields(cls, operation):
        if operation == 'create':
            return [
                {**cls._field('schema', 'Schema', required=True),
                 'default': 'PUBLIC'},
                cls._field('table', 'Table name', required=True),
                cls._field('name', 'Index name', required=True),
                cls._field('columns', 'Ordered columns', 'json', True),
                {**cls._field('spatial', 'Spatial index', 'boolean'),
                 'default': False},
                {**cls._field('inline_size', 'Inline size', 'number'),
                 'minimum': 0, 'maximum': 1048576},
                {**cls._field('parallel', 'Build threads', 'number'),
                 'minimum': 1, 'maximum': 1024},
            ]
        return None

    @classmethod
    def _user_fields(cls, operation):
        if operation == 'create':
            return [
                cls._field('name', 'User name', required=True),
                cls._field(
                    'password_reference', 'Password secret reference',
                    'secret-reference', True),
            ]
        if operation == 'alter':
            return [cls._field(
                'password_reference', 'New password secret reference',
                'secret-reference', True)]
        if operation == 'drop':
            return [
                cls._field(
                    'password_reference', 'Current password secret reference',
                    'secret-reference', True),
                cls._field('confirmation', 'Confirmation', required=True),
            ]
        return None

    @classmethod
    def _ttl_fields(cls, operation):
        if operation in {'create', 'alter', 'drop'}:
            fields = [cls._field(
                'selector', 'Provider cache-row identity', 'json', True)]
            if operation != 'drop':
                fields.append({
                    **cls._field(
                        'ttl_milliseconds', 'Expiration (milliseconds)',
                        'number', True),
                    'minimum': 1, 'maximum': 315360000000,
                })
            if operation == 'drop':
                fields.append(cls._field(
                    'confirmation', 'Confirmation', required=True))
            return fields
        return None

    def visual_admin_catalog(self, catalog):
        value = copy.deepcopy(dict(catalog))
        value['key_identity'] = 'cache-plus-provider-issued-key-token'
        value['transaction_authority'] = 'ignite-server-owned'
        value['automatic_mutation_retry'] = False
        value['experience_families'] = ['relational', 'key_value']

        def declaration(status, *kinds, reason, obligations=None,
                        external_surface=None):
            result = {'status': status, 'reason': reason}
            if kinds:
                result['resource_kinds'] = list(kinds)
            if obligations:
                result['operation_obligations'] = copy.deepcopy(obligations)
            if external_surface:
                result['external_surface'] = external_surface
            result['evidence'] = [
                'apache-ignite-2.17.0-system-views-and-native-thin-api'
            ]
            return result

        def na(reason):
            return declaration('not_applicable', reason=reason)
        value['concept_declarations'] = {
            'relational': {
                'servers': declaration(
                    'supported', 'cluster', 'node',
                    reason='Cluster and node topology are native resources.',
                    obligations={
                        'cluster': ['inspect', 'set_state'],
                        'node': ['inspect'],
                    }),
                'databases': na(
                    'Ignite 2.17 has one cluster-wide SQL catalog and no '
                    'database object.'),
                'schemas': declaration(
                    'read_only', 'sql-schema',
                    reason='SYS.SCHEMAS exposes cache-derived SQL schemas.',
                    obligations={'sql-schema': ['inspect']}),
                'tables': declaration(
                    'supported', 'table',
                    reason='Ignite DDL and DML provide typed table editing.',
                    obligations={'table': [
                        'inspect', 'create', 'alter', 'insert', 'update',
                        'delete', 'drop']}),
                'views': declaration(
                    'supported', 'view',
                    reason='Ignite supports CREATE OR REPLACE/DROP VIEW.',
                    obligations={'view': [
                        'inspect', 'create', 'alter', 'drop']}),
                'materialized_views': na(
                    'Ignite 2.17 does not implement materialized views.'),
                'columns': declaration(
                    'supported', 'column',
                    reason='Columns support inspect, ADD and DROP.',
                    obligations={'column': ['inspect', 'create', 'drop']}),
                'domains': na('Ignite 2.17 has no SQL domain objects.'),
                'types': na(
                    'Ignite binary types are application metadata, not '
                    'administrable SQL type objects.'),
                'sequences': na('Ignite 2.17 has no SQL sequence objects.'),
                'functions': na(
                    'User SQL functions require server class deployment and '
                    'are not runtime DDL objects.'),
                'procedures': na('Ignite 2.17 has no SQL procedures.'),
                'triggers': na('Ignite 2.17 has no SQL triggers.'),
                'indexes': declaration(
                    'supported', 'index',
                    reason='SYS.INDEXES and typed index DDL are exposed.',
                    obligations={'index': ['inspect', 'create', 'drop']}),
                'constraints': declaration(
                    'read_only', 'constraint',
                    reason=(
                        'Primary-key membership is exposed by SYS metadata.'),
                    obligations={'constraint': ['inspect']}),
                'roles_and_grants': na(
                    'Ignite authentication has users but no role or grant '
                    'object model.'),
                'extensions_and_plugins': na(
                    'Server libraries are deployment configuration, not '
                    'runtime SQL plugin objects.'),
                'partitions': na(
                    'Ignite partitions are cache replicas, not SQL table '
                    'partition objects.'),
                'tablespaces_and_filespaces': na(
                    'Data regions are cache memory policies, not SQL '
                    'tablespaces or filespaces.'),
                'replication_objects': declaration(
                    'read_only', 'replica',
                    reason='SYS.PARTITION_STATES exposes primary and backup '
                    'partition placement.',
                    obligations={'replica': ['inspect']}),
                'jobs_and_events': declaration(
                    'supported', 'compute-task', 'service',
                    reason='Running tasks and services are inspectable and '
                    'cancellable through provider-owned controls.',
                    obligations={
                        'compute-task': ['inspect', 'cancel'],
                        'service': ['inspect', 'cancel'],
                    }),
            },
            'key_value': {
                'key_browsing': declaration(
                    'supported', reason=(
                        'The bounded cache row grid scans keys and issues '
                        'route-bound identities.'),
                    obligations={'cache': ['inspect']},
                    external_surface='ignite-cache-row-grid'),
                'data_type_editing': declaration(
                    'supported', reason='The cache row grid preserves native '
                    'key/value values and uses compare-and-set updates.',
                    obligations={'cache': ['insert', 'update', 'delete']},
                    external_surface='ignite-cache-row-grid'),
                'ttl_inspection': na(
                    'Ignite thin protocol 1.7 does not expose remaining '
                    'per-entry TTL; the UI reports that limit explicitly.'),
                'expiration_management': declaration(
                    'supported', 'ttl', reason='ExpiryPolicy-decorated CAS '
                    'updates set or remove expiration without an unsafe '
                    'blind rewrite.', obligations={
                        'ttl': ['create', 'alter', 'drop']}),
                'streams': na(
                    'Ignite data streaming is an ingestion mode, not a '
                    'persistent stream object.'),
                'pubsub': na('Ignite 2.17 has no Pub/Sub channel objects.'),
                'consumer_groups': na(
                    'Ignite 2.17 has no stream consumer-group objects.'),
                'modules': na(
                    'Server modules are deployment configuration and cannot '
                    'be administered through the thin protocol.'),
                'acls': na(
                    'Ignite 2.17 authentication is user/password only and '
                    'does not expose ACL objects.'),
                'replication': declaration(
                    'read_only', 'replica',
                    reason='Partition primary/backup placement is exposed.',
                    obligations={'replica': ['inspect']}),
                'sentinel_or_cluster_state': declaration(
                    'supported', 'cluster', 'node', 'baseline-topology',
                    reason='Cluster state, topology and baseline controls are '
                    'native Ignite administration.', obligations={
                        'cluster': ['inspect', 'set_state'],
                        'node': ['inspect'],
                        'baseline-topology': [
                            'inspect', 'add_nodes', 'remove_nodes',
                            'set_nodes', 'set_version',
                            'configure_auto_adjust'],
                    }),
            },
        }
        allowed = {
            'cluster': {'inspect'}, 'node': {'inspect'},
            'baseline-topology': {'inspect'},
            'sql-schema': {'inspect'},
            'table': {'inspect', 'create', 'alter', 'insert', 'update',
                      'delete', 'drop'},
            'view': {'inspect', 'create', 'alter', 'drop'},
            'column': {'inspect', 'create', 'drop'},
            'index': {'inspect', 'create', 'drop'},
            'constraint': {'inspect'},
            'cache': {'inspect', 'create', 'insert', 'update', 'delete',
                      'drop'},
            'cache-template': {'inspect'}, 'data-region': {'inspect'},
            'replica': {'inspect'},
            'ttl': {'inspect', 'create', 'alter', 'drop'},
            'compute-task': {'inspect'}, 'service': {'inspect'},
            'user': {'inspect', 'create', 'alter', 'drop'},
            'snapshot': {'inspect'},
        }
        for resource in value.get('objects', []):
            kind = resource['resource_kind']
            resource['operations'] = [
                item for item in resource.get('operations', [])
                if item['operation_id'] in allowed.get(kind, set())
            ]
            for operation in resource.get('operations', []):
                operation_id = operation['operation_id']
                fields = None
                title = f'{operation_id.title()} {kind}'
                if kind == 'cache' and operation_id == 'create':
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
                elif kind == 'cache' and operation_id == 'insert':
                    fields = [
                        self._field('key', 'Key', required=True),
                        self._field('value', 'Value', required=True),
                    ]
                elif kind == 'cache' and operation_id == 'update':
                    fields = [
                        self._field(
                            'selector', 'Provider row identity', 'json', True
                        ),
                        self._field(
                            'value', 'Replacement value', required=True
                        ),
                    ]
                elif kind == 'cache' and operation_id == 'delete':
                    fields = [
                        self._field(
                            'selector', 'Provider row identity', 'json', True
                        ),
                        self._field(
                            'confirmation', 'Confirmation', required=True
                        ),
                    ]
                elif kind == 'table':
                    fields = self._table_fields(operation_id)
                elif kind == 'view':
                    fields = self._view_fields(operation_id)
                elif kind == 'column':
                    fields = self._column_fields(operation_id)
                elif kind == 'index':
                    fields = self._index_fields(operation_id)
                elif kind == 'user':
                    fields = self._user_fields(operation_id)
                elif kind == 'ttl':
                    fields = self._ttl_fields(operation_id)
                if fields is not None:
                    operation['form'] = {
                        'form_id': f'ignite-{kind}-{operation_id}',
                        'title': title, 'fields': fields,
                    }
        return apply_control_catalog(value)

    @classmethod
    def _column_specs(cls, value, required=True):
        if not isinstance(value, list) or (required and not value) or len(
                value) > 1024:
            raise NativeDistributedError(
                'Ignite column definitions must be a bounded array')
        names = []
        result = []
        for item in value:
            if not isinstance(item, Mapping):
                raise NativeDistributedError(
                    'Ignite column definition is invalid')
            name = item.get('name')
            cls._identifier(name, 'column name')
            names.append(name)
            result.append({
                'name': name,
                'data_type': cls._sql_type(item.get('data_type')),
                'nullable': item.get('nullable', True),
                'primary_key': item.get('primary_key', False),
            })
            if not isinstance(result[-1]['nullable'], bool) or not isinstance(
                    result[-1]['primary_key'], bool):
                raise NativeDistributedError(
                    'Ignite column flags must be boolean')
        if len(names) != len(set(names)):
            raise NativeDistributedError(
                'Ignite column names must be unique')
        return result

    @classmethod
    def _ordered_columns(cls, value):
        if not isinstance(value, list) or not value or len(value) > 64:
            raise NativeDistributedError(
                'Ignite index columns must be a bounded array')
        result = []
        for item in value:
            if isinstance(item, str):
                item = {'name': item, 'direction': 'ASC'}
            if not isinstance(item, Mapping):
                raise NativeDistributedError(
                    'Ignite index column is invalid')
            cls._identifier(item.get('name'), 'index column')
            direction = item.get('direction', 'ASC')
            if direction not in {'ASC', 'DESC'}:
                raise NativeDistributedError(
                    'Ignite index direction is invalid')
            result.append((item['name'], direction))
        if len({item[0] for item in result}) != len(result):
            raise NativeDistributedError(
                'Ignite index columns must be unique')
        return result

    @staticmethod
    def _select_query(value):
        if not isinstance(value, str) or not value.strip() or len(
                value.encode('utf-8')) > 1024 * 1024:
            raise NativeDistributedError('Ignite view query is invalid')
        source = value.strip()
        if not re.match(r'(?is)^SELECT\b', source) or re.search(
                r';|--|/\*|\*/', source):
            raise NativeDistributedError(
                'Ignite view query must be one SELECT expression')
        return source

    @classmethod
    def validate_admin_operation(cls, request):
        kind = request.get('resource_kind')
        operation = request.get('operation_id')
        draft = request.get('draft') or {}
        if CONTROL_CATALOG.supports(kind, operation):
            return CONTROL_CATALOG.validate(request)
        errors = []
        allowed = {
            'cluster': {'inspect'}, 'node': {'inspect'},
            'baseline-topology': {'inspect'}, 'sql-schema': {'inspect'},
            'table': {'inspect', 'create', 'alter', 'insert', 'update',
                      'delete', 'drop'},
            'view': {'inspect', 'create', 'alter', 'drop'},
            'column': {'inspect', 'create', 'drop'},
            'index': {'inspect', 'create', 'drop'},
            'constraint': {'inspect'}, 'cache-template': {'inspect'},
            'data-region': {'inspect'}, 'replica': {'inspect'},
            'cache': {'inspect', 'create', 'insert', 'update', 'delete',
                      'drop'},
            'ttl': {'inspect', 'create', 'alter', 'drop'},
            'compute-task': {'inspect'}, 'service': {'inspect'},
            'user': {'inspect', 'create', 'alter', 'drop'},
            'snapshot': {'inspect'},
        }
        if operation not in allowed.get(kind, set()):
            return {'errors': [{
                'field_id': None, 'code': 'unsupported',
                'message': 'Ignite operation is not supported.',
            }]}
        try:
            if kind == 'table' and operation == 'create':
                cls._identifier(draft.get('schema'), 'schema')
                cls._identifier(draft.get('name'), 'table name')
                columns = cls._column_specs(draft.get('columns'))
                if not any(item['primary_key'] for item in columns):
                    raise NativeDistributedError(
                        'Ignite table requires at least one primary key')
                if draft.get('template', 'PARTITIONED') not in {
                        'PARTITIONED', 'REPLICATED'}:
                    raise NativeDistributedError(
                        'Ignite table template is invalid')
                if draft.get('atomicity', 'ATOMIC') not in {
                        'ATOMIC', 'TRANSACTIONAL'}:
                    raise NativeDistributedError(
                        'Ignite table atomicity is invalid')
                backups = draft.get('backups', 1)
                if isinstance(backups, bool) or not isinstance(
                        backups, int) or not 0 <= backups <= 32:
                    raise NativeDistributedError(
                        'Ignite table backups are invalid')
                for field in ('cache_name', 'cache_group', 'affinity_key',
                              'data_region'):
                    if draft.get(field):
                        cls._identifier(draft[field], field)
            elif kind == 'table' and operation == 'alter':
                additions = cls._column_specs(
                    draft.get('add_columns') or [], required=False)
                drops = draft.get('drop_columns') or []
                if not isinstance(drops, list) or len(drops) > 1024:
                    raise NativeDistributedError(
                        'Ignite dropped columns are invalid')
                for name in drops:
                    cls._identifier(name, 'column name')
                logging = draft.get('logging', 'unchanged')
                if logging not in {'unchanged', 'enabled', 'disabled'}:
                    raise NativeDistributedError(
                        'Ignite logging choice is invalid')
                if not additions and not drops and logging == 'unchanged':
                    raise NativeDistributedError(
                        'Ignite table alteration has no changes')
            elif kind == 'table' and operation == 'insert':
                if not isinstance(draft.get('values'), Mapping) or not (
                        draft['values']):
                    raise NativeDistributedError(
                        'Ignite inserted values are required')
                for name in draft['values']:
                    cls._identifier(name, 'column name')
            elif kind == 'table' and operation in {'update', 'delete'}:
                if not isinstance(draft.get('selector'), Mapping) or not (
                        isinstance(draft['selector'].get(
                            'identity_token'), str)):
                    raise NativeDistributedError(
                        'A provider-issued SQL row identity is required')
                if operation == 'update':
                    if not isinstance(draft.get('changes'), Mapping) or not (
                            draft['changes']):
                        raise NativeDistributedError(
                            'Ignite changed values are required')
                    for name in draft['changes']:
                        cls._identifier(name, 'column name')
            elif kind == 'view' and operation in {'create', 'alter'}:
                if operation == 'create':
                    cls._identifier(draft.get('schema'), 'schema')
                    cls._identifier(draft.get('name'), 'view name')
                cls._select_query(draft.get('query'))
            elif kind == 'column' and operation == 'create':
                cls._column_specs([draft])
                cls._identifier(draft.get('schema'), 'schema')
                cls._identifier(draft.get('table'), 'table name')
            elif kind == 'index' and operation == 'create':
                for field in ('schema', 'table', 'name'):
                    cls._identifier(draft.get(field), field)
                cls._ordered_columns(draft.get('columns'))
                for field, minimum, maximum in (
                        ('inline_size', 0, 1048576),
                        ('parallel', 1, 1024)):
                    number = draft.get(field)
                    if number is not None and (
                            isinstance(number, bool) or not isinstance(
                            number, int) or not minimum <= number <=
                            maximum):
                        raise NativeDistributedError(
                            f'Ignite {field} is invalid')
                if not isinstance(draft.get('spatial', False), bool):
                    raise NativeDistributedError(
                        'Ignite spatial choice is invalid')
            elif kind == 'user' and operation in {'create', 'alter', 'drop'}:
                if operation == 'create':
                    cls._identifier(draft.get('name'), 'user name')
                if not isinstance(draft.get('password_reference'), str) or (
                        not draft['password_reference']):
                    raise NativeDistributedError(
                        'Ignite password secret reference is required')
                route = request.get('_provider_route') or {}
                if not route.get('principal_reference'):
                    raise NativeDistributedError(
                        'Ignite user administration requires a principal '
                        'reference')
            elif kind == 'ttl' and operation in {'create', 'alter', 'drop'}:
                selector = draft.get('selector')
                if not isinstance(selector, Mapping) or not isinstance(
                        selector.get('identity_token'), str):
                    raise NativeDistributedError(
                        'A provider-issued cache row identity is required')
                ttl = draft.get('ttl_milliseconds')
                if operation != 'drop' and (
                        isinstance(ttl, bool) or not isinstance(ttl, int) or
                        not 1 <= ttl <= 315360000000):
                    raise NativeDistributedError(
                        'Ignite expiration is invalid')
        except NativeDistributedError as exc:
            errors.append({
                'field_id': None, 'code': 'invalid_native_request',
                'message': str(exc),
            })
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
                    'resource_kind': request['resource_kind'],
                    'operation_id': request['operation_id'],
                    'draft': copy.deepcopy(request.get('draft') or {}),
                    'target_resource': copy.deepcopy(
                        request.get('target_resource')),
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
            'provider_payload': copy.deepcopy(dict(request)), 'warnings': [],
            'receipt': {
                'planner': 'apache-ignite-native-structured.v2',
                'provider_finality_authority': True,
                'automatic_mutation_retry': False,
            },
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

    def _consume_sql_identity(self, route, schema, table, selector):
        token = selector.get('identity_token') if isinstance(
            selector, Mapping) else None
        if not isinstance(token, str) or not token:
            raise NativeDistributedError(
                'provider-issued SQL row identity is required')
        with self._identity_lock:
            identity = self._sql_row_identities.pop(token, None)
        if identity is None:
            raise NativeDistributedError(
                'SQL row identity is stale or invalid')
        (fingerprint, identity_schema, identity_table, keys, original,
         issued) = identity
        if time.monotonic() - issued > 600:
            raise NativeDistributedError('SQL row identity has expired')
        if fingerprint != self._route_fingerprint(route) or (
                identity_schema != schema or identity_table != table):
            raise NativeDistributedError(
                'SQL row identity belongs to another target')
        return keys, original

    @staticmethod
    def _execute_sql(client, source, parameters=(), schema='PUBLIC'):
        try:
            return list(client.sql(
                source, query_args=list(parameters), schema=schema,
            ))
        except Exception as exc:
            raise NativeDistributedError(
                'Ignite structured SQL operation failed') from exc

    @classmethod
    def _qualified(cls, schema, name):
        return cls._identifier(schema, 'schema') + '.' + cls._identifier(
            name, 'object name')

    @classmethod
    def _column_sql(cls, spec):
        value = cls._identifier(spec['name'], 'column name') + ' ' + (
            cls._sql_type(spec['data_type']))
        if not spec.get('nullable', True):
            value += ' NOT NULL'
        if spec.get('primary_key'):
            value += ' PRIMARY KEY'
        return value

    @classmethod
    def _resource_target(cls, payload, kind):
        target = payload.get('target_resource')
        path = cls._target_parts(target, kind)
        return target, path

    def _admin_password(self, route, reference, callback):
        if not callable(self.secret_acquirer):
            raise NativeDistributedError(
                'Ignite secret binding is unavailable')
        principal = route.get('principal_reference')
        if not isinstance(principal, str) or not principal:
            raise NativeDistributedError(
                'Ignite user administration requires a principal reference')
        lease = self.secret_acquirer(
            reference, principal, 'admin', 'database_password')
        with lease:
            return lease.use(
                lambda value: callback(bytes(value).decode('utf-8')))

    def _user_authenticates(self, route, name, reference):
        probe_route = copy.deepcopy(route)
        probe_route['auth_mode'] = 'username-password'
        probe_route['username'] = name
        probe_route['credential_reference_id'] = reference
        references = dict(probe_route.get('credential_references') or {})
        references['database_password'] = reference
        probe_route['credential_references'] = references
        probe = self.open_session({'route': probe_route})
        try:
            probe.get_cluster().get_state()
        finally:
            probe.close()
        return True

    def _apply_sql_admin(self, client, route, payload):
        kind = payload['resource_kind']
        operation = payload['operation_id']
        draft = payload.get('draft') or {}
        target = payload.get('target_resource') or {}
        statements = []
        outcome = {}
        if kind == 'table' and operation == 'create':
            schema = draft['schema']
            if schema.upper() != 'PUBLIC':
                raise NativeDistributedError(
                    'Ignite CREATE TABLE is restricted to PUBLIC schema')
            columns = self._column_specs(draft['columns'])
            options = [
                'TEMPLATE=' + draft.get('template', 'PARTITIONED'),
                'BACKUPS=' + str(draft.get('backups', 1)),
                'ATOMICITY=' + draft.get('atomicity', 'ATOMIC'),
            ]
            for field, native in (
                    ('cache_name', 'CACHE_NAME'),
                    ('cache_group', 'CACHE_GROUP'),
                    ('affinity_key', 'AFFINITY_KEY'),
                    ('data_region', 'DATA_REGION')):
                if draft.get(field):
                    options.append(native + '=' + draft[field])
            statements.append((
                'CREATE TABLE ' + self._qualified(schema, draft['name']) +
                ' (' + ', '.join(
                    self._column_sql(item) for item in columns) +
                ') WITH "' +
                ','.join(options).replace('"', '""') + '"', (), schema,
            ))
        elif kind == 'table' and operation == 'alter':
            _, path = self._resource_target(payload, 'table')
            schema, table = path[-2:]
            qualified = self._qualified(schema, table)
            additions = self._column_specs(
                draft.get('add_columns') or [], required=False)
            if additions:
                statements.append((
                    'ALTER TABLE ' + qualified + ' ADD COLUMN (' +
                    ', '.join(self._column_sql(item) for item in additions) +
                    ')', (), schema))
            drops = draft.get('drop_columns') or []
            if drops:
                statements.append((
                    'ALTER TABLE ' + qualified + ' DROP COLUMN (' +
                    ', '.join(self._identifier(item, 'column name')
                              for item in drops) + ')', (), schema))
            logging = draft.get('logging', 'unchanged')
            if logging != 'unchanged':
                statements.append((
                    'ALTER TABLE ' + qualified +
                    (' LOGGING' if logging == 'enabled' else ' NOLOGGING'),
                    (), schema))
        elif kind == 'table' and operation == 'drop':
            _, path = self._resource_target(payload, 'table')
            statements.append((
                'DROP TABLE ' + self._qualified(*path[-2:]), (), path[-2]))
        elif kind == 'table' and operation == 'insert':
            _, path = self._resource_target(payload, 'table')
            values = draft['values']
            names = list(values)
            statements.append((
                'INSERT INTO ' + self._qualified(*path[-2:]) + ' (' +
                ', '.join(self._identifier(item, 'column name')
                          for item in names) + ') VALUES (' +
                ', '.join('?' for _item in names) + ')',
                tuple(values[item] for item in names), path[-2]))
        elif kind == 'table' and operation in {'update', 'delete'}:
            _, path = self._resource_target(payload, 'table')
            schema, table = path[-2:]
            keys, original = self._consume_sql_identity(
                route, schema, table, draft['selector'])
            outcome['row_keys'] = copy.deepcopy(keys)
            predicate = []
            parameters = []
            for name, value in original.items():
                quoted = self._identifier(name, 'column name')
                predicate.append(
                    f'({quoted} = ? OR ({quoted} IS NULL AND ? IS NULL))')
                parameters.extend([value, value])
            for name, value in keys.items():
                if name not in original:
                    predicate.append(self._identifier(
                        name, 'key column') + ' = ?')
                    parameters.append(value)
            if operation == 'update':
                changes = draft['changes']
                if set(changes).intersection(keys):
                    raise NativeDistributedError(
                        'Ignite primary-key columns cannot be edited in grid')
                prefix = 'UPDATE ' + self._qualified(schema, table) + ' SET '
                prefix += ', '.join(
                    self._identifier(name, 'column name') + ' = ?'
                    for name in changes)
                parameters = list(changes.values()) + parameters
            else:
                prefix = 'DELETE FROM ' + self._qualified(schema, table)
            statements.append((
                prefix + ' WHERE ' + ' AND '.join(predicate),
                tuple(parameters), schema))
        elif kind == 'column' and operation in {'create', 'drop'}:
            if operation == 'create':
                schema, table = draft['schema'], draft['table']
                spec = self._column_specs([draft])[0]
                source = 'ALTER TABLE ' + self._qualified(schema, table) + (
                    ' ADD COLUMN ' + self._column_sql(spec))
            else:
                _, path = self._resource_target(payload, 'column')
                schema, table = path[-3:-1]
                source = 'ALTER TABLE ' + self._qualified(schema, table) + (
                    ' DROP COLUMN ' + self._identifier(
                        path[-1], 'column name'))
            statements.append((source, (), schema))
        elif kind == 'index' and operation == 'create':
            columns = self._ordered_columns(draft['columns'])
            source = ('CREATE ' + (
                'SPATIAL ' if draft.get('spatial') else '') +
                      'INDEX ' + self._identifier(draft['name'], 'index') +
                      ' ON ' + self._qualified(
                          draft['schema'], draft['table']) + ' (' +
                      ', '.join(self._identifier(name, 'index column') +
                                ' ' + direction
                                for name, direction in columns) + ')')
            if draft.get('inline_size') is not None:
                source += ' INLINE_SIZE ' + str(draft['inline_size'])
            if draft.get('parallel') is not None:
                source += ' PARALLEL ' + str(draft['parallel'])
            statements.append((source, (), draft['schema']))
        elif kind == 'index' and operation == 'drop':
            _, path = self._resource_target(payload, 'index')
            statements.append((
                'DROP INDEX ' + self._identifier(path[-1], 'index'), (),
                path[-3]))
        elif kind == 'view' and operation in {'create', 'alter'}:
            if operation == 'create':
                schema, name = draft['schema'], draft['name']
            else:
                _, path = self._resource_target(payload, 'view')
                schema, name = path[-2:]
            statements.append((
                'CREATE ' + ('OR REPLACE ' if operation == 'alter' else '') +
                'VIEW ' + self._qualified(schema, name) + ' AS ' +
                self._select_query(draft['query']), (), schema))
        elif kind == 'view' and operation == 'drop':
            _, path = self._resource_target(payload, 'view')
            statements.append((
                'DROP VIEW ' + self._qualified(*path[-2:]), (), path[-2]))
        elif kind == 'user' and operation in {'create', 'alter'}:
            name = draft.get('name') if operation == 'create' else (
                self._target_parts(target, 'user')[-1])
            verb = 'CREATE USER' if operation == 'create' else 'ALTER USER'
            reference = draft['password_reference']

            def apply_password(password):
                escaped = password.replace("'", "''")
                return self._execute_sql(
                    client, verb + ' ' + self._identifier(name, 'user') +
                    " WITH PASSWORD '" + escaped + "'")
            self._admin_password(route, reference, apply_password)
            authenticated = self._user_authenticates(
                route, name, reference)
            return {
                'affected_rows': 1,
                'user_authentication_checked': authenticated,
                'user_name': name,
            }
        elif kind == 'user' and operation == 'drop':
            _, path = self._resource_target(payload, 'user')
            name = path[-1]
            statements.append((
                'DROP USER ' + self._identifier(name, 'user'), (),
                'PUBLIC'))
            outcome['dropped_user_name'] = name
            outcome['dropped_password_reference'] = draft[
                'password_reference']
        else:
            raise NativeDistributedError(
                'Ignite structured SQL operation is unsupported')
        affected = None
        for source, parameters, schema in statements:
            rows = self._execute_sql(client, source, parameters, schema)
            if rows and len(rows[0]) == 1 and isinstance(rows[0][0], int):
                affected = rows[0][0]
        if kind == 'user' and operation == 'drop':
            try:
                self._user_authenticates(
                    route, outcome['dropped_user_name'],
                    outcome['dropped_password_reference'])
                outcome['user_authentication_rejected'] = False
            except Exception:
                # Confirm the target rejected its former credentials, then
                # prove the cluster is still reachable through the already
                # authenticated administrator session.
                client.get_cluster().get_state()
                outcome['user_authentication_rejected'] = True
        if kind == 'table' and operation in {'update', 'delete'} and (
                affected != 1):
            raise NativeDistributedError(
                'Ignite SQL row changed after it was displayed')
        outcome['affected_rows'] = affected
        return outcome

    @classmethod
    def _matching_row_count(cls, client, schema, table, values):
        predicates = []
        parameters = []
        for name, value in values.items():
            quoted = cls._identifier(name, 'column name')
            predicates.append(
                f'({quoted} = ? OR ({quoted} IS NULL AND ? IS NULL))')
            parameters.extend([value, value])
        rows = cls._sql_rows(
            client, 'SELECT COUNT(*) AS ROW_COUNT FROM ' +
            cls._qualified(schema, table) + ' WHERE ' +
            ' AND '.join(predicates), parameters, schema)
        if len(rows) != 1 or not isinstance(rows[0].get('ROW_COUNT'), int):
            raise NativeDistributedError(
                'Ignite SQL row post-state is invalid')
        return rows[0]['ROW_COUNT']

    def apply_admin_operation(self, request):
        payload = request['provider_payload']
        plan = request.get('plan') or {}
        if CONTROL_CATALOG.supports(
                plan.get('resource_kind'), plan.get('operation_id')):
            route = payload['_provider_route']
            result = self._with_secrets(
                route, lambda secrets: execute_action(request, secrets))
            if plan.get('resource_kind') == 'snapshot':
                operation = plan.get('operation_id')
                draft = payload.get('draft') or {}
                target = payload.get('target_resource') or {}
                name = draft.get('name') or target.get('display_name')
                if name:
                    with self._identity_lock:
                        snapshots = self._known_snapshots.setdefault(
                            self._route_fingerprint(route), {})
                        snapshots[name] = {
                            'last_operation': operation,
                            'provider_response_observed': True,
                        }
            return result
        route = payload['_provider_route']
        client = self.open_session({'route': route})
        kind = payload['resource_kind']
        operation = payload['operation_id']
        draft = payload.get('draft', {})
        target = payload.get('target_resource') or {}
        if operation == 'inspect':
            resource_id = target.get('resource_id')
            matches = [
                item for item in self.list_resources({'route': route})
                if item.get('resource_id') == resource_id
            ]
            if len(matches) != 1:
                raise NativeDistributedError(
                    'Ignite native resource observation is unavailable')
            return {
                'accepted': True, 'resource': matches[0],
                'provider_observation_only': True,
                'provider_finality_authority': True,
            }
        if kind == 'ttl':
            _, path = self._resource_target(payload, 'ttl')
            cache_name = path[-2]
            key, original = self._consume_identity(
                route, cache_name, draft.get('selector'))
            from pyignite.datatypes.expiry_policy import ExpiryPolicy
            ttl = (
                ExpiryPolicy.ETERNAL if operation == 'drop'
                else draft['ttl_milliseconds'])
            cache = client.get_cache(cache_name).with_expire_policy(update=ttl)
            if not cache.replace_if_equals(key, original, original):
                raise NativeDistributedError(
                    'Ignite cache row changed after it was displayed')
            return {
                'accepted': True, 'native_operation': operation,
                'provider_native_outcome': True,
                'expiration_milliseconds': ttl,
            }
        if kind != 'cache':
            outcome = self._apply_sql_admin(client, route, payload)
            return {
                'accepted': True, 'native_operation': operation,
                'provider_native_outcome': True,
                **outcome,
                'requested_values': copy.deepcopy(
                    draft.get('values') or draft.get('changes')),
            }
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
        else:
            raise NativeDistributedError('Ignite cache operation unsupported')
        result = {
            'accepted': True, 'native_operation': operation,
            'provider_native_outcome': True,
        }
        if operation == 'insert':
            result['cache_key'] = draft['key']
            result['expected_value'] = draft['value']
        elif operation == 'update':
            result['cache_key'] = key
            result['expected_value'] = draft['value']
        elif operation == 'delete':
            result['cache_key'] = key
        return result

    def inspect_admin_operation(self, request):
        plan = request.get('plan') or {}
        payload = request.get('provider_payload') or {}
        if CONTROL_CATALOG.supports(
                plan.get('resource_kind'), plan.get('operation_id')):
            return inspect_action(request)
        route = payload.get('_provider_route')
        target = payload.get('target_resource') or plan.get(
            'target_resource')
        if not isinstance(route, Mapping):
            raise NativeDistributedError(
                'Ignite observation route is unavailable')
        if not isinstance(target, Mapping):
            return {
                'provider_observation_only': True,
                'resource': None,
            }
        matches = [
            item for item in self.list_resources({'route': route})
            if item.get('resource_id') == target.get('resource_id')
        ]
        return {
            'provider_observation_only': True,
            'resource': matches[0] if len(matches) == 1 else None,
        }

    def cancel_admin_operation(self, request):
        route = (request.get('provider_payload') or {}).get(
            '_provider_route')
        if not isinstance(route, Mapping):
            raise NativeDistributedError(
                'Ignite cancellation route is unavailable')
        return self._with_secrets(
            route, lambda secrets: cancel_action(request, secrets))

    def validate_admin_post_state(self, request):
        plan = request.get('plan') or {}
        payload = request.get('provider_payload') or {}
        result = request.get('provider_result') or {}
        route = payload.get('_provider_route')
        kind = plan.get('resource_kind')
        operation = plan.get('operation_id')
        if CONTROL_CATALOG.supports(kind, operation):
            if kind == 'cache' and operation == 'clear':
                target = payload.get('target_resource') or {}
                name = target.get('display_name')
                client = self.open_session({'route': route})
                rows = []
                for index, item in enumerate(client.get_cache(name).scan()):
                    rows.append(item)
                    if index == 0:
                        break
                return {
                    'confirmed': not rows,
                    'reason': (
                        'ignite_cache_clear_confirmed' if not rows else
                        'ignite_cache_clear_not_confirmed'),
                    'observation': {'remaining_entry_sample': rows},
                    'provider_finality_authority': True,
                }
            if kind in {'service', 'compute-task'} and operation == 'cancel':
                target = payload.get('target_resource') or {}
                resources = self.list_resources({'route': route})
                matching = [
                    item for item in resources
                    if item.get('resource_id') == target.get('resource_id')
                ]
                return {
                    'confirmed': not matching,
                    'reason': (
                        'ignite_cancelled_resource_absent' if not matching
                        else 'ignite_cancelled_resource_still_present'),
                    'observation': {
                        'target_resource_id': target.get('resource_id'),
                        'matching_resource_count': len(matching),
                    },
                    'provider_finality_authority': True,
                }
            observation = self._with_secrets(
                route, lambda secrets: inspect_action(request, secrets))
            output = observation.get('provider_observation', {}).get(
                'stdout', '')
            confirmed = False
            draft = payload.get('draft') or {}
            if kind == 'cluster' and operation == 'set_state':
                expected = str(draft.get('state'))
                match = re.search(
                    r'^Cluster state:\s*([A-Z_]+)\s*$', output,
                    re.MULTILINE)
                confirmed = bool(match and match.group(1) == expected)
            elif kind == 'baseline-topology':
                baseline = output.split('Baseline nodes:', 1)[-1].split(
                    '-' * 20, 1)[0]
                observed_ids = set(re.findall(
                    r'^\s*ConsistentId=(.*?), Address=', baseline,
                    re.MULTILINE))
                requested_ids = set(draft.get('consistent_ids') or [])
                if operation in {'add_nodes', 'set_nodes'}:
                    confirmed = requested_ids.issubset(observed_ids)
                    if operation == 'set_nodes':
                        confirmed = confirmed and observed_ids == requested_ids
                elif operation == 'remove_nodes':
                    confirmed = not requested_ids.intersection(observed_ids)
                elif operation == 'set_version':
                    versions = re.findall(
                        r'^Current topology version:\s*(\d+)', output,
                        re.MULTILINE)
                    confirmed = bool(versions and int(versions[0]) ==
                                     draft.get('topology_version'))
                elif operation == 'configure_auto_adjust':
                    enabled = draft.get('enabled')
                    state = re.search(
                        r'^Baseline auto adjustment (enabled|disabled)',
                        output, re.MULTILINE)
                    confirmed = bool(
                        state and (state.group(1) == 'enabled') == enabled)
                    timeout = draft.get('timeout_ms')
                    if confirmed and enabled and timeout is not None:
                        confirmed = f'softTimeout={timeout}' in output
            elif kind == 'snapshot' and operation == 'create':
                native = observation.get('provider_observation') or {}
                confirmed = native.get('exit_code') == 0 and (
                    native.get('provider_response_observed') is True)
            elif kind in {'cache', 'service', 'compute-task', 'snapshot'}:
                response = result.get('provider_response') or {}
                confirmed = (
                    response.get('exit_code') == 0 and
                    response.get('provider_response_observed') is True)
            return {
                'confirmed': confirmed,
                'reason': (
                    'provider_control_state_observed' if confirmed else
                    'provider_control_state_not_observed'),
                'observation': observation,
                'provider_finality_authority': True,
            }
        if not isinstance(route, Mapping):
            raise NativeDistributedError(
                'Ignite post-state route is unavailable')
        client = self.open_session({'route': route})
        target = payload.get('target_resource') or {}
        draft = payload.get('draft') or {}
        confirmed = False
        observation = None
        if kind == 'cache':
            name = draft.get('name') or target.get('display_name')
            names = set(client.get_cache_names())
            if operation in {'create', 'inspect'}:
                confirmed = name in names
            elif operation == 'drop':
                confirmed = name not in names
            elif operation in {'insert', 'update'}:
                key = result.get('cache_key')
                observation = client.get_cache(name).get(key)
                confirmed = observation == result.get('expected_value')
            elif operation == 'delete':
                key = result.get('cache_key')
                observation = client.get_cache(name).get(key)
                confirmed = observation is None
        elif kind == 'ttl':
            # Ignite 2.17 cannot report remaining per-entry TTL. A successful
            # expiry-policy CAS is the strongest native acknowledgement; it
            # is deliberately not presented as a remaining-TTL observation.
            confirmed = result.get('accepted') is True and result.get(
                'provider_native_outcome') is True
            observation = {
                'remaining_ttl_available': False,
                'expiry_policy_cas_acknowledged': confirmed,
            }
        elif kind == 'user' and operation in {'create', 'alter'}:
            confirmed = result.get('user_authentication_checked') is True
            observation = {
                'user_name': result.get('user_name'),
                'new_credentials_authenticated': confirmed,
            }
        elif kind == 'user' and operation == 'drop':
            confirmed = result.get('user_authentication_rejected') is True
            observation = {
                'user_name': result.get('dropped_user_name'),
                'former_credentials_rejected': confirmed,
            }
        else:
            resources = self.list_resources({'route': route})
            expected_id = target.get('resource_id')
            if operation == 'create' and not expected_id:
                if kind in {'table', 'view'}:
                    expected_id = ':'.join([
                        kind, draft['schema'], draft['name']])
                elif kind == 'index':
                    expected_id = ':'.join([
                        kind, draft['schema'], draft['table'], draft['name']])
                elif kind == 'column':
                    expected_id = ':'.join([
                        kind, draft['schema'], draft['table'], draft['name']])
                elif kind == 'user':
                    expected_id = f'user:{draft["name"]}'
            matching = [
                item for item in resources
                if item.get('resource_id') == expected_id
            ]
            observation = matching[0] if len(matching) == 1 else None
            if operation == 'drop':
                confirmed = not matching
            elif operation in {'create', 'inspect'}:
                confirmed = len(matching) == 1
            elif kind == 'table' and operation == 'alter':
                _, path = self._resource_target(payload, 'table')
                observed = {
                    item['display_name'] for item in resources
                    if item['resource_kind'] == 'column' and
                    item['display_path'][-3:-1] == path[-2:]
                }
                confirmed = all(
                    item['name'] in observed
                    for item in self._column_specs(
                        draft.get('add_columns') or [], required=False)
                ) and not set(draft.get('drop_columns') or []).intersection(
                    observed)
            elif kind == 'table' and operation in {
                    'insert', 'update', 'delete'}:
                _, path = self._resource_target(payload, 'table')
                schema, table = path[-2:]
                values = copy.deepcopy(result.get('row_keys') or {})
                if operation in {'insert', 'update'}:
                    values.update(result.get('requested_values') or {})
                count = self._matching_row_count(
                    client, schema, table, values)
                observation = {'matching_row_count': count}
                confirmed = count == (0 if operation == 'delete' else 1)
            else:
                confirmed = len(matching) == 1
        return {
            'confirmed': bool(confirmed),
            'reason': (
                'ignite_semantic_post_state_confirmed' if confirmed else
                'ignite_semantic_post_state_not_confirmed'),
            'observation': copy.deepcopy(observation),
            'provider_finality_authority': True,
        }

    def read_admin_rows(self, request):
        target = request['target_resource']
        client = self.open_session({'route': request['_provider_route']})
        if target.get('resource_kind') == 'table':
            path = self._target_parts(target, 'table')
            schema, table = path[-2:]
            metadata = self._sql_rows(
                client, 'SELECT COLUMN_NAME, TYPE, NULLABLE, PK FROM '
                'SYS.TABLE_COLUMNS WHERE SCHEMA_NAME = ? AND TABLE_NAME = ? '
                "AND COLUMN_NAME NOT IN ('_KEY', '_VAL') ORDER BY "
                'COLUMN_NAME', (schema, table))
            if not metadata:
                raise NativeDistributedError(
                    'Ignite table metadata is unavailable')
            columns = [str(item['COLUMN_NAME']) for item in metadata]
            key_columns = [
                str(item['COLUMN_NAME']) for item in metadata
                if bool(item['PK'])]
            limit = min(max(int(request.get('limit', 200)), 1), 500)
            records = self._sql_rows(
                client, 'SELECT ' + ', '.join(
                    self._identifier(item, 'column name')
                    for item in columns) + ' FROM ' +
                self._qualified(schema, table) + ' LIMIT ' + str(limit + 1),
                schema=schema)
            complete = len(records) <= limit
            records = records[:limit]
            fingerprint = self._route_fingerprint(request['_provider_route'])
            rows = []
            for record in records:
                token = str(uuid.uuid4())
                original = {name: record.get(name) for name in columns}
                keys = {name: record.get(name) for name in key_columns}
                with self._identity_lock:
                    while len(self._sql_row_identities) >= 5000:
                        oldest = next(iter(self._sql_row_identities))
                        self._sql_row_identities.pop(oldest, None)
                    self._sql_row_identities[token] = (
                        fingerprint, schema, table, keys, original,
                        time.monotonic())
                rows.append({
                    'values': original, 'identity_token': token,
                })
            return {
                'schema': 'cdeadmin.native-row-page.v1',
                'columns': [{
                    'name': str(item['COLUMN_NAME']),
                    'native_type': str(item['TYPE']),
                    'nullable': bool(item['NULLABLE']),
                    'key': bool(item['PK']),
                    'editable': not bool(item['PK']),
                } for item in metadata],
                'rows': rows, 'editable': True, 'complete': complete,
                'identity_policy': (
                    'provider-primary-key-and-original-row-cas'),
                'limit': limit,
                'transaction_finality_interpreted_by_common_code': False,
            }
        if target.get('resource_kind') != 'cache':
            raise NativeDistributedError(
                'Ignite row editor is available for tables and caches only')
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
            self._sql_row_identities.clear()
            self._known_snapshots.clear()
