##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Bounded ClickHouse 25.12 HTTP/JSON client and administration adapter."""

from __future__ import annotations

import copy
import datetime
import decimal
import hashlib
import json
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pgadmin.cdeadmin.sdk import PilotProviderError
from pgadmin.cdeadmin.transports.analytics_http import (
    AnalyticHTTPError,
    AnalyticHTTPUnknownOutcomeError,
    BoundedJSONHTTPTransport,
    normalize_http_route,
)


REFERENCE_VERSION = '25.12.10.7-stable'
RUNTIME_VERSION = '25.12.10.7'
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 10000
MAX_PAGE_SIZE = 500
MAX_IDENTIFIER_BYTES = 255
ROW_IDENTITY_TTL_SECONDS = 600
_IDENTIFIER = re.compile(r'^[^\x00-\x1f\x7f]{1,255}$')
_HOST = re.compile(
    r'^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?'
    r'|\[[0-9A-Fa-f:.]+\])$'
)
_TYPE = re.compile(r'^[A-Za-z][A-Za-z0-9_]*(?:\([^;\x00]{1,512}\))?$')
_READ_PREFIXES = frozenset({
    'SELECT', 'SHOW', 'DESCRIBE', 'DESC', 'EXISTS', 'EXPLAIN', 'WITH',
})
_MUTATION_PREFIXES = frozenset({
    'INSERT', 'ALTER', 'CREATE', 'DROP', 'RENAME', 'TRUNCATE', 'OPTIMIZE',
    'SYSTEM', 'GRANT', 'REVOKE', 'KILL',
})


class ClickHouseClientError(PilotProviderError):
    """A ClickHouse operation failed before a safe outcome was available."""


class ClickHouseServerError(ClickHouseClientError):
    """ClickHouse returned a native server diagnostic."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.native_code = code


class ClickHouseUnknownOutcomeError(ClickHouseClientError):
    """A mutation may have reached ClickHouse before transport failure."""

    outcome = 'unknown_requires_observation'
    retryable = False


@dataclass
class _Session:
    route: dict[str, Any]
    last_observation: str = 'no-statement-observed'

    def close(self):
        return None


@dataclass
class _Result:
    rows: list[dict[str, Any]]
    columns: list[dict[str, Any]]
    rowcount: int
    command: str
    query_id: str
    route: dict[str, Any]
    statistics: dict[str, Any]
    cancelled: bool = False


@dataclass(frozen=True)
class _RowIdentity:
    route_fingerprint: str
    database: str
    table: str
    key_values: tuple[tuple[str, str, Any], ...]
    digest: str
    expires_at: float


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def _mapping(value, label='request'):
    if not isinstance(value, Mapping):
        raise ClickHouseClientError(f'{label} must be an object')
    return copy.deepcopy(dict(value))


def _text(value, label, maximum=4096, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise ClickHouseClientError(f'{label} must not be empty')
    value = value.strip()
    if len(value.encode('utf-8')) > maximum or any(
        ord(character) < 32 for character in value
    ):
        raise ClickHouseClientError(f'{label} is invalid')
    return value


def _integer(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ClickHouseClientError(f'{label} must be an integer')
    if not minimum <= value <= maximum:
        raise ClickHouseClientError(f'{label} is outside the admitted range')
    return value


def _identifier(value, label='identifier'):
    value = _text(value, label, MAX_IDENTIFIER_BYTES)
    if not _IDENTIFIER.fullmatch(value):
        raise ClickHouseClientError(f'{label} is invalid')
    return value


def _quote_identifier(value):
    return '`' + _identifier(value).replace('`', '``') + '`'


def _qualified(database, name):
    return f'{_quote_identifier(database)}.{_quote_identifier(name)}'


def _sql_string(value):
    value = _text(value, 'SQL value', 1024 * 1024, required=False) or ''
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


def _clickhouse_type(value):
    value = _text(value, 'ClickHouse type', 512)
    if not _TYPE.fullmatch(value):
        raise ClickHouseClientError('ClickHouse type is invalid')
    return value


def _expression(value, label, maximum=16384):
    """Admit one ClickHouse expression/clause, never a second statement."""
    value = _text(value, label, maximum)
    if any(marker in value for marker in (';', '--', '/*', '*/')):
        raise ClickHouseClientError(f'{label} must contain one expression')
    return value


class ClickHouseClient:
    """Native ClickHouse HTTP provider with fail-closed operation outcomes."""

    ROUTE_FIELDS = frozenset({
        'route_id', 'host', 'port', 'database', 'username',
        'credential_reference_id', 'principal_reference', 'tls_mode',
        'credential_references', 'credential_kinds', 'credential_kind',
        'auth_kind',
        'tls_ca_file', 'tls_certificate_file', 'tls_key_file',
        'connect_timeout', 'statement_timeout', 'read_only',
        'session_id', 'quota_key', 'role', 'http_compression',
        'pool_max_size', 'pool_block',
    })
    ADMIN_OPERATIONS = {
        'server': frozenset({'inspect', 'execute'}),
        'cluster': frozenset({'inspect', 'execute'}),
        'replica': frozenset({'inspect', 'execute'}),
        'database': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop'
        }),
        'table': frozenset({
            'inspect', 'create', 'alter', 'rename', 'insert', 'update',
            'delete', 'drop',
        }),
        'column': frozenset({'inspect', 'create', 'alter', 'rename', 'drop'}),
        'view': frozenset({'inspect', 'create', 'alter', 'rename', 'drop'}),
        'materialized-view': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
        }),
        'dictionary': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'function': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'projection': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'data-skipping-index': frozenset({
            'inspect', 'create', 'alter', 'drop',
        }),
        'partition': frozenset({'inspect', 'execute'}),
        'user': frozenset({
            'inspect', 'create', 'alter', 'rename', 'grant', 'revoke', 'drop',
        }),
        'role': frozenset({
            'inspect', 'create', 'alter', 'rename', 'grant', 'revoke', 'drop',
        }),
        'quota': frozenset({'inspect', 'create', 'alter', 'rename', 'drop'}),
        'settings-profile': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
        }),
        'row-policy': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
        }),
    }

    def __init__(self, secret_acquirer=None, urlopen=None, clock=None):
        self.secret_acquirer = secret_acquirer
        self._urlopen = urlopen
        self.transport = BoundedJSONHTTPTransport(
            secret_acquirer, urlopen=urlopen,
            max_response_bytes=MAX_RESPONSE_BYTES,
        )
        self._clock = clock or time.time
        self._sessions = []
        self._row_identities = {}
        self._lock = threading.RLock()

    def _route(self, request):
        route = _mapping(request.get('route'), 'route')
        forbidden = {'password', 'secret', 'token', 'credential'}
        if forbidden.intersection(key.casefold() for key in route):
            raise ClickHouseClientError('inline credentials are forbidden')
        unknown = sorted(set(route).difference(self.ROUTE_FIELDS))
        if unknown:
            raise ClickHouseClientError(
                f'ClickHouse route contains unknown fields: {unknown}'
            )
        if route.get('auth_kind') is None:
            has_password = bool(route.get('credential_reference_id')) or (
                'database_password' in dict(
                    route.get('credential_references') or {}
                )
            )
            route['auth_kind'] = (
                'clickhouse-basic' if has_password else 'none'
            )
        route.setdefault('credential_kind', (
            'database_password'
            if route['auth_kind'] == 'clickhouse-basic' else None
        ))
        route.setdefault('username', 'default')
        route.setdefault('database', 'default')
        try:
            result = normalize_http_route(
                {'route': route}, default_port=8123,
                extra_fields=('database', 'session_id', 'quota_key', 'role'),
            )
        except AnalyticHTTPError as exc:
            raise ClickHouseClientError(str(exc)) from exc
        result['database'] = _identifier(result['database'], 'database')
        result['username'] = _text(result['username'], 'username')
        for field in ('session_id', 'quota_key', 'role'):
            if result.get(field) is not None:
                result[field] = _text(result[field], field)
        return result

    @staticmethod
    def _ssl_context(route):
        if route['tls_mode'] == 'disable':
            return None
        if route['tls_mode'] == 'require':
            context = ssl._create_unverified_context()
        else:
            context = ssl.create_default_context(cafile=route['tls_ca_file'])
            context.check_hostname = route['tls_mode'] == 'verify-full'
        if route.get('tls_certificate_file'):
            context.load_cert_chain(
                route['tls_certificate_file'], route['tls_key_file']
            )
        return context

    def _with_password(
        self, route, callback, purpose='connect',
        expected_kind='database_password',
    ):
        reference = route.get('credential_reference_id')
        if not reference:
            return callback(None)
        if self.secret_acquirer is None:
            raise ClickHouseClientError('secret acquisition is unavailable')
        principal = route.get('principal_reference')
        if not principal:
            raise ClickHouseClientError(
                'credential reference requires a principal reference'
            )
        lease = self.secret_acquirer(
            reference, principal, purpose, expected_kind
        )
        with lease:
            return lease.use(
                lambda view: callback(bytes(view).decode('utf-8'))
            )

    @staticmethod
    def _json_value(value, depth=0):
        if depth > 24:
            raise ClickHouseClientError('ClickHouse value nesting is too deep')
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (
            datetime.date, datetime.time, datetime.datetime
        )):
            return value.isoformat()
        if isinstance(value, decimal.Decimal):
            return str(value)
        if isinstance(value, bytes):
            return value.hex()
        if isinstance(value, Mapping):
            return {
                str(key): ClickHouseClient._json_value(item, depth + 1)
                for key, item in value.items()
            }
        if isinstance(value, Sequence) and not isinstance(value, str):
            return [ClickHouseClient._json_value(item, depth + 1)
                    for item in value]
        return str(value)

    def _stdlib_open(self, request, timeout, context):
        handlers = [_NoRedirect()]
        if context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=context))
        return urllib.request.build_opener(*handlers).open(
            request, timeout=timeout
        )

    def _request(self, route, source, parameters=None, mutating=False,
                 query_id=None, rows=None):
        source = _text(source, 'ClickHouse query', MAX_SOURCE_BYTES)
        query_id = query_id or str(uuid.uuid4())
        args = {
            'database': route['database'],
            'query_id': query_id,
            'wait_end_of_query': '1',
            'default_format': 'JSON',
            'max_execution_time': str(route['statement_timeout']),
            'max_result_rows': str(MAX_RECORDS),
            'result_overflow_mode': 'break',
        }
        if route['read_only']:
            args['readonly'] = '1'
        if route.get('session_id'):
            args['session_id'] = route['session_id']
        if route.get('quota_key'):
            args['quota_key'] = route['quota_key']
        if route.get('role'):
            args['role'] = route['role']
        if mutating:
            # Observe mutation/ALTER completion at the engine boundary. A
            # transport failure still remains an unknown outcome and is never
            # automatically replayed.
            args['mutations_sync'] = '2'
            args['alter_sync'] = '2'
        if parameters:
            if not isinstance(parameters, Mapping):
                raise ClickHouseClientError(
                    'ClickHouse parameters must be a named object'
                )
            for name, value in parameters.items():
                name = _identifier(name, 'parameter name')
                args[f'param_{name}'] = self._parameter_text(value)
        body = source.encode('utf-8')
        if rows is not None:
            encoded = '\n'.join(json.dumps(
                self._json_value(row), separators=(',', ':'),
                ensure_ascii=False
            ) for row in rows)
            body += b'\n' + encoded.encode('utf-8') + b'\n'
        scheme = 'http' if route['tls_mode'] == 'disable' else 'https'
        url = f'{scheme}://{route["host"]}:{route["port"]}/?'
        url += urllib.parse.urlencode(args)
        headers = {
            'Content-Type': 'text/plain; charset=utf-8',
            'Accept': 'application/json',
            'X-ClickHouse-User': route['username'],
        }

        try:
            response = self.transport.request(
                route, '/', method='POST', query=args, body=body,
                headers=headers, mutating=mutating,
            )
            return response.status, response.body, response.headers, query_id
        except AnalyticHTTPUnknownOutcomeError as exc:
            raise ClickHouseUnknownOutcomeError(str(exc)) from exc
        except AnalyticHTTPError as exc:
            code = None
            if isinstance(exc.native_payload, bytes):
                message = exc.native_payload.decode(
                    'utf-8', 'replace'
                ).splitlines()[0][:1024]
            else:
                message = str(exc)
            if exc.status is not None:
                raise ClickHouseServerError(message, code) from exc
            raise ClickHouseClientError(message) from exc

    @staticmethod
    def _parameter_text(value):
        if value is None:
            return '\\N'
        if isinstance(value, bool):
            return '1' if value else '0'
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, separators=(',', ':'), ensure_ascii=False)
        if isinstance(value, (
            datetime.date, datetime.time, datetime.datetime
        )):
            return value.isoformat()
        return str(value)

    def _query(self, route, source, parameters=None, mutating=False,
               rows=None):
        status, payload, headers, query_id = self._request(
            route, source, parameters, mutating, rows=rows
        )
        if status < 200 or status >= 300:
            raise ClickHouseClientError(
                f'ClickHouse returned unexpected HTTP status {status}'
            )
        if not payload.strip():
            return {
                'meta': [], 'data': [], 'rows': 0, 'statistics': {},
                'query_id': query_id, 'headers': dict(headers),
            }
        try:
            document = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ClickHouseClientError(
                'ClickHouse returned a malformed JSON result'
            ) from exc
        if not isinstance(document, Mapping):
            raise ClickHouseClientError('ClickHouse JSON result is invalid')
        data = document.get('data', [])
        meta = document.get('meta', [])
        if not isinstance(data, list) or not isinstance(meta, list):
            raise ClickHouseClientError('ClickHouse JSON rows are invalid')
        if len(data) > MAX_RECORDS:
            raise ClickHouseClientError('ClickHouse result row limit exceeded')
        return {
            'meta': copy.deepcopy(meta),
            'data': [self._json_value(row) for row in data],
            'rows': int(document.get('rows', len(data))),
            'statistics': self._json_value(document.get('statistics', {})),
            'query_id': query_id, 'headers': dict(headers),
        }

    def runtime_identity(self, request, handle=None):
        route = handle.route if isinstance(handle, _Session) else self._route(
            request
        )
        result = self._query(
            route,
            'SELECT version() AS version, revision() AS revision',
        )
        if len(result['data']) != 1:
            raise ClickHouseClientError('ClickHouse identity was not returned')
        row = result['data'][0]
        observed = str(row.get('version', ''))
        if observed != RUNTIME_VERSION:
            raise ClickHouseClientError(
                'runtime did not prove exact ClickHouse 25.12.10.7 identity'
            )
        return {
            'engine_id': 'clickhouse', 'version': REFERENCE_VERSION,
            'build_id': (
                f'ClickHouse {observed} revision {row.get("revision")}'
            ),
            'protocol_id': 'http_json',
        }

    def open_session(self, request):
        session = _Session(self._route(request))
        with self._lock:
            self._sessions.append(session)
        return session

    @staticmethod
    def describe_transaction(handle):
        return {
            'native_observation': handle.last_observation,
            'statement_atomicity_only': True,
            'multi_statement_transaction_supported': False,
            'automatic_replay': False,
            'driver_observation_only': True,
            'finality_interpreted_by_common_code': False,
        }

    @staticmethod
    def _command(source):
        match = re.search(r'[A-Za-z]+', source)
        return match.group(0).upper() if match else 'UNKNOWN'

    def execute(self, handle, request):
        source = _text(request.get('source'), 'ClickHouse query',
                       MAX_SOURCE_BYTES)
        command = self._command(source)
        mutating = command in _MUTATION_PREFIXES
        if handle.route['read_only'] and mutating:
            raise ClickHouseClientError('read-only route refused mutation')
        result = self._query(
            handle.route, source, request.get('parameters'), mutating
        )
        handle.last_observation = (
            'native-response-observed' if mutating else 'read-observed'
        )
        meta = result['meta']
        columns = [{
            'name': str(item.get('name')),
            'type': str(item.get('type', 'Unknown')),
        } for item in meta if isinstance(item, Mapping)]
        return _Result(
            rows=result['data'], columns=columns,
            rowcount=result['rows'], command=command,
            query_id=result['query_id'], route=copy.deepcopy(handle.route),
            statistics=result['statistics'],
        )

    def cancel(self, token):
        # HTTP requests are synchronous and a _Result is only issued after the
        # server response. There is no still-running operation to cancel.
        return False

    @staticmethod
    def describe_result(token):
        if not isinstance(token, _Result):
            raise ClickHouseClientError('ClickHouse result token is invalid')
        return {
            'result_kind': 'columnar',
            'schema': {
                'columns': copy.deepcopy(token.columns),
                'statistics': copy.deepcopy(token.statistics),
            },
            'complete': True,
            'stream_reference': None,
            'payload': {
                'rows': copy.deepcopy(token.rows),
                'rowcount': token.rowcount,
                'command': token.command,
                'query_id': token.query_id,
                'statistics': copy.deepcopy(token.statistics),
                'cancelled': token.cancelled,
            },
        }

    @staticmethod
    def _generation(value):
        return hashlib.sha256(json.dumps(
            value, sort_keys=True, default=str, separators=(',', ':')
        ).encode('utf-8')).hexdigest()[:24]

    def _resource(self, kind, name, native, path=None):
        path = path or [kind, name]
        return {
            'resource_id': (
                'clickhouse:' + ':'.join(str(item) for item in path)
            ),
            'resource_kind': kind, 'display_name': str(name),
            'authority_path': ['clickhouse', *[str(item) for item in path]],
            'display_path': [str(item) for item in path],
            'generation': self._generation(native),
            'native': self._json_value(native),
        }

    def _rows(self, route, source, parameters=None):
        return self._query(route, source, parameters)['data']

    def list_resources(self, request):
        route = self._route(request)
        resources = [self._resource(
            'server', route['host'], {
                'host': route['host'], 'port': route['port'],
                'database': route['database'],
            }, ['server', route['host']],
        )]
        queries = (
            ('database',
             'SELECT name, engine, comment FROM system.databases'),
            ('table',
             'SELECT database, name, engine, comment, partition_key, '
             'sorting_key, primary_key, create_table_query FROM '
             'system.tables WHERE is_temporary = 0'),
            ('column',
             'SELECT database, table, name, type, position, default_kind, '
             'default_expression, is_in_partition_key, is_in_sorting_key, '
             'is_in_primary_key FROM system.columns'),
            ('partition',
             'SELECT database, table, partition_id, sum(rows) AS rows, '
             'sum(bytes_on_disk) AS bytes_on_disk, count() AS part_count '
             'FROM system.parts WHERE active GROUP BY database, table, '
             'partition_id'),
            ('projection',
             'SELECT database, table, name, type, sorting_key '
             'FROM system.projections'),
            ('data-skipping-index',
             'SELECT database, table, name, type, expr, granularity '
             'FROM system.data_skipping_indices'),
            ('dictionary',
             'SELECT database, name, status, origin, type, key, '
             'attribute.names AS attributes FROM system.dictionaries'),
            ('function',
             "SELECT name, is_aggregate, origin FROM system.functions "
             "WHERE origin != 'System'"),
            ('cluster',
             'SELECT cluster, shard_num, replica_num, host_name, '
             'host_address, port, is_local FROM system.clusters'),
            ('replica',
             'SELECT database, table, is_leader, is_readonly, '
             'is_session_expired, queue_size, absolute_delay '
             'FROM system.replicas'),
            ('user', 'SELECT name, id, storage FROM system.users'),
            ('role', 'SELECT name, id, storage FROM system.roles'),
            ('quota', 'SELECT name, id, storage FROM system.quotas'),
            ('settings-profile',
             'SELECT name, id, storage FROM system.settings_profiles'),
            ('row-policy',
             'SELECT name, id, storage FROM system.row_policies'),
        )
        for kind, source in queries:
            try:
                rows = self._rows(route, source)
            except ClickHouseServerError:
                continue
            for row in rows:
                actual_kind = kind
                if kind == 'table':
                    if row.get('engine') == 'View':
                        actual_kind = 'view'
                    elif row.get('engine') in {
                        'MaterializedView', 'LiveView', 'WindowView'
                    }:
                        actual_kind = 'materialized-view'
                name = row.get('name') or row.get('cluster')
                if kind == 'partition':
                    name = row.get('partition_id')
                database = row.get('database')
                table = row.get('table')
                path = [actual_kind]
                if database:
                    path.append(database)
                if table:
                    path.append(table)
                path.append(name)
                resources.append(self._resource(
                    actual_kind, name, row, path
                ))
        return resources

    def inspect_resource(self, request):
        native = self._native_target(request)
        kind = request.get('resource_kind') or native.get('resource_kind')
        if not kind:
            raise ClickHouseClientError('ClickHouse resource kind is absent')
        name = native.get('name') or native.get('table') or native.get(
            'database'
        ) or kind
        return self._resource(kind, name, native)

    def describe_security(self, request):
        route = self._route(request)
        users = self._rows(
            route, 'SELECT name, id, storage, auth_type FROM system.users'
        )
        roles = self._rows(
            route, 'SELECT name, id, storage FROM system.roles'
        )
        policies = self._rows(
            route, 'SELECT name, id, storage FROM system.row_policies'
        )
        safe = {'users': users, 'roles': roles, 'row_policies': policies}
        return {
            'resource_id': 'clickhouse:security:access-control',
            'display_name': 'ClickHouse access control',
            'authority_path': ['clickhouse', 'security', 'access-control'],
            'generation': self._generation(safe),
            'native': {
                **safe, 'passwords_exposed': False,
                'authorization_model': 'clickhouse-native-access-control',
            },
        }

    def supports_admin_operation(self, resource_kind, operation_id):
        return operation_id in self.ADMIN_OPERATIONS.get(
            resource_kind, frozenset()
        )

    @staticmethod
    def _form(form_id, title, fields):
        return {'form_id': form_id, 'title': title, 'fields': fields}

    @staticmethod
    def _field(field_id, label, control='text', required=False, **values):
        return {
            'field_id': field_id, 'label': label, 'control': control,
            'required': required, **values,
        }

    def visual_admin_catalog(self, catalog):
        catalog['native_planner'] = 'clickhouse-sql-structured-planner'
        catalog['query_language'] = 'ClickHouse SQL over HTTP/JSON'
        catalog['transaction_authority'] = 'clickhouse-statement-outcome'
        catalog['statement_atomicity_only'] = True
        catalog['common_finality_interpretation'] = False
        for resource in catalog.get('objects', []):
            kind = resource['resource_kind']
            resource['operations'] = [
                operation for operation in resource.get('operations', [])
                if self.supports_admin_operation(
                    kind, operation['operation_id']
                )
            ]
            for operation in resource['operations']:
                operation['form'] = self._admin_form(
                    kind, operation['operation_id']
                )
                if operation['operation_id'] in {'drop', 'delete', 'execute'}:
                    operation['confirmation_required'] = True
        return catalog

    def _admin_form(self, kind, operation):
        f = self._field
        if operation == 'inspect':
            fields = []
            if kind in {'table', 'view', 'materialized-view'}:
                fields = [f('limit', 'Maximum rows', 'number', False,
                            default=200)]
            return self._form('clickhouse-inspect', 'Inspect', fields)
        if kind == 'database' and operation == 'create':
            return self._form(
                'clickhouse-database-create', 'Create database', [
                    f('name', 'Database name', required=True),
                    f('engine', 'Database engine', 'select', True,
                      default='Atomic', options=[
                          {'value': 'Atomic', 'label': 'Atomic'},
                          {'value': 'Memory', 'label': 'Memory'},
                          {'value': 'Lazy', 'label': 'Lazy'},
                      ]),
                    f('comment', 'Comment'),
                ]
            )
        if kind == 'table' and operation == 'create':
            return self._form('clickhouse-table-create', 'Create table', [
                f('database', 'Database', required=True, default='default'),
                f('name', 'Table name', required=True),
                f('columns', 'Columns', 'json', True, default=[
                    {'name': 'id', 'type': 'UInt64'},
                ]),
                f('engine', 'Table engine', 'select', True,
                  default='MergeTree', options=[
                      {'value': 'MergeTree', 'label': 'MergeTree'},
                      {'value': 'ReplacingMergeTree',
                       'label': 'ReplacingMergeTree'},
                      {'value': 'AggregatingMergeTree',
                       'label': 'AggregatingMergeTree'},
                      {'value': 'Memory', 'label': 'Memory'},
                      {'value': 'Log', 'label': 'Log'},
                  ]),
                f('order_by', 'Ordering key expression', 'code', False,
                  default='tuple()'),
                f('partition_by', 'Partition expression', 'code'),
                f('primary_key', 'Primary-key expression', 'code'),
                f('ttl', 'TTL expression', 'code'),
            ])
        if kind == 'column' and operation in {'create', 'alter'}:
            return self._form(f'clickhouse-column-{operation}',
                              f'{operation.title()} column', [
                f('name', 'Column name', required=operation == 'create'),
                f('type', 'ClickHouse type', required=True),
                f('default_expression', 'Default expression', 'code'),
            ])
        if kind in {'view', 'materialized-view'} and operation in {
            'create', 'alter'
        }:
            fields = [
                f('database', 'Database', required=operation == 'create',
                  default='default'),
                f('name', 'View name', required=operation == 'create'),
                f('select', 'SELECT expression', 'code', True),
            ]
            if kind == 'materialized-view':
                fields.extend([
                    f('destination_table', 'Destination table'),
                    f('engine', 'Storage engine', 'select', False,
                      default='MergeTree', options=[
                          {'value': 'MergeTree', 'label': 'MergeTree'},
                          {'value': 'ReplacingMergeTree',
                           'label': 'ReplacingMergeTree'},
                          {'value': 'AggregatingMergeTree',
                           'label': 'AggregatingMergeTree'},
                          {'value': 'Memory', 'label': 'Memory'},
                          {'value': 'Log', 'label': 'Log'},
                      ]),
                    f('order_by', 'Ordering key expression', 'code', False,
                      default='tuple()'),
                ])
            return self._form(
                f'clickhouse-{kind}-{operation}',
                f'{operation.title()} {kind}', fields,
            )
        if kind == 'dictionary' and operation in {'create', 'alter'}:
            return self._form(f'clickhouse-dictionary-{operation}',
                              f'{operation.title()} dictionary', [
                f('database', 'Database', required=operation == 'create',
                  default='default'),
                f('name', 'Dictionary name', required=operation == 'create'),
                f('attributes', 'Attributes', 'json', True, default=[]),
                f('primary_key', 'Primary key', required=True),
                f('source', 'SOURCE clause contents', 'code', True),
                f('layout', 'Layout', required=True, default='HASHED()'),
                f('lifetime', 'Lifetime', required=True, default='300'),
            ])
        if kind == 'function' and operation in {'create', 'alter'}:
            return self._form(f'clickhouse-function-{operation}',
                              f'{operation.title()} function', [
                f('name', 'Function name', required=operation == 'create'),
                f('lambda', 'Lambda expression', 'code', True),
            ])
        if kind in {'projection', 'data-skipping-index'} and operation in {
            'create', 'alter'
        }:
            fields = [
                f('name', 'Object name', required=operation == 'create'),
                f('expression', 'Expression', 'code', True),
            ]
            if kind == 'data-skipping-index':
                fields.extend([
                    f('type', 'Index type', required=True, default='minmax'),
                    f('granularity', 'Granularity', 'number', True, default=1),
                ])
            return self._form(f'clickhouse-{kind}-{operation}',
                              f'{operation.title()} {kind}', fields)
        if kind == 'partition' and operation == 'execute':
            return self._form(
                'clickhouse-partition-execute', 'Partition operation', [
                    f('action', 'Action', 'select', True,
                      default='optimize', options=[
                          {'value': 'optimize', 'label': 'Optimize'},
                          {'value': 'freeze', 'label': 'Freeze'},
                          {'value': 'detach', 'label': 'Detach'},
                          {'value': 'drop', 'label': 'Drop'},
                      ]),
                    f('acknowledge_operation', 'Confirm operation',
                      'boolean', True, default=False),
                ]
            )
        if kind in {'user', 'role', 'quota', 'settings-profile',
                    'row-policy'} and operation in {
            'create', 'alter', 'grant', 'revoke'
        }:
            fields = []
            if operation == 'create':
                fields.append(f('name', 'Name', required=True))
            if kind == 'user' and operation in {'create', 'alter'}:
                fields.append(f(
                    'password_reference', 'Password secret reference',
                    'secret-reference', True, sensitive=True,
                ))
            elif operation in {'grant', 'revoke'}:
                fields.extend([
                    f('privileges', 'Privileges', required=True),
                    f('scope', 'Scope', required=True, default='*.*'),
                ])
            else:
                fields.append(f('definition', 'Native definition', 'code'))
            return self._form(f'clickhouse-{kind}-{operation}',
                              f'{operation.title()} {kind}', fields)
        if operation == 'rename':
            return self._form('clickhouse-rename', 'Rename', [
                f('new_name', 'New name', required=True),
            ])
        if operation == 'drop':
            return self._form('clickhouse-drop', 'Drop object', [
                f('acknowledge_drop', 'Confirm drop', 'boolean', True,
                  default=False),
            ])
        if kind in {'server', 'cluster', 'replica'} and operation == 'execute':
            return self._form(
                'clickhouse-system-operation', 'System operation', [
                    f('action', 'Action', 'select', True,
                      default='reload-config', options=[
                          {'value': 'reload-config',
                           'label': 'Reload config'},
                          {'value': 'flush-logs', 'label': 'Flush logs'},
                          {'value': 'sync-replica', 'label': 'Sync replica'},
                      ]),
                    f('acknowledge_operation', 'Confirm operation',
                      'boolean', True, default=False),
                ]
            )
        if kind == 'table' and operation == 'insert':
            return self._form('clickhouse-row-insert', 'Insert row', [
                f('values', 'Column values', 'json', True, default={}),
            ])
        if kind == 'table' and operation in {'update', 'delete'}:
            fields = [
                f('selector', 'Provider row selector', 'json', True,
                  default={}),
                f('concurrency_token', 'Provider row identity', required=True),
            ]
            if operation == 'update':
                fields.append(f('changes', 'Changed values', 'json', True,
                                default={}))
            else:
                fields.append(f('acknowledge_delete', 'Confirm row mutation',
                                'boolean', True, default=False))
            return self._form(f'clickhouse-row-{operation}',
                              f'{operation.title()} row', fields)
        return self._form(
            'clickhouse-native-alter', f'{operation.title()} {kind}', [
                f('definition', 'Native definition', 'code'),
            ]
        )

    def validate_admin_operation(self, request):
        kind = request['resource_kind']
        operation = request['operation_id']
        draft = request.get('draft', {})
        errors = []
        try:
            if not self.supports_admin_operation(kind, operation):
                raise ClickHouseClientError('operation is not supported')
            if operation == 'create':
                _identifier(draft.get('name'), f'{kind} name')
            if operation == 'rename':
                _identifier(draft.get('new_name'), 'new name')
            if operation == 'drop' and not draft.get('acknowledge_drop'):
                raise ClickHouseClientError('drop must be acknowledged')
            if kind == 'table' and operation == 'create':
                self._validate_columns(draft.get('columns'))
            if kind == 'table' and operation == 'insert':
                if not isinstance(draft.get('values'), Mapping):
                    raise ClickHouseClientError('row values must be an object')
            if kind == 'table' and operation in {'update', 'delete'}:
                _text(draft.get('concurrency_token'), 'row identity', 1024)
                if not isinstance(draft.get('selector'), Mapping):
                    raise ClickHouseClientError('row selector is invalid')
                if operation == 'update' and not isinstance(
                    draft.get('changes'), Mapping
                ):
                    raise ClickHouseClientError('row changes are invalid')
                if operation == 'delete' and not draft.get(
                    'acknowledge_delete'
                ):
                    raise ClickHouseClientError(
                        'row delete must be acknowledged'
                    )
            if operation == 'execute' and not draft.get(
                'acknowledge_operation'
            ):
                raise ClickHouseClientError(
                    'system operation must be acknowledged'
                )
            if kind == 'user' and operation in {'create', 'alter'}:
                _text(draft.get('password_reference'),
                      'password secret reference')
        except ClickHouseClientError as exc:
            errors.append({
                'field_id': None, 'code': 'clickhouse_native_validation',
                'message': str(exc),
            })
        return {'errors': errors}

    @staticmethod
    def _validate_columns(columns):
        if not isinstance(columns, list) or not columns:
            raise ClickHouseClientError('columns must be a non-empty array')
        names = []
        for column in columns:
            column = _mapping(column, 'column')
            names.append(_identifier(column.get('name'), 'column name'))
            _clickhouse_type(column.get('type'))
        if len(names) != len(set(names)):
            raise ClickHouseClientError('column names must be unique')

    def plan_admin_operation(self, request):
        validation = self.validate_admin_operation(request)
        if validation['errors']:
            raise ClickHouseClientError(validation['errors'][0]['message'])
        kind = request['resource_kind']
        operation = request['operation_id']
        target = self._native_target(request.get('target_resource'))
        sensitive = kind == 'user' and operation in {'create', 'alter'}
        return {
            'command_preview': {
                'kind': kind, 'operation': operation,
                'target': self._safe_target(target),
                'statement': (
                    f'{operation.upper()} {kind.upper()} WITH <redacted>'
                    if sensitive else f'{operation.upper()} {kind.upper()}'
                ),
                'parameters_bound_at_execution': not sensitive,
            },
            'warnings': ([
                'ClickHouse mutations may complete asynchronously; this '
                'provider requests synchronous mutation observation.'
            ] if operation in {'update', 'delete'} else []),
            'provider_payload': {
                'kind': kind, 'operation': operation,
                'target': target, 'draft': copy.deepcopy(request.get(
                    'draft', {}
                )), 'route': copy.deepcopy(request.get('_provider_route')),
            },
            'receipt': {
                'provider': 'clickhouse', 'automatic_retry': False,
                'transaction_finality_interpreted': False,
            },
        }

    def apply_admin_operation(self, request):
        payload = _mapping(request.get('provider_payload'), 'provider payload')
        route = self._route({'route': payload.get('route')})
        source, parameters, rows = self._compile_admin(payload, route)
        mutation = payload['operation'] != 'inspect'
        result = self._query(
            route, source, parameters, mutation, rows=rows
        )
        return {
            'native_response_observed': True,
            'rows': result['data'], 'rowcount': result['rows'],
            'query_id': result['query_id'],
            'automatic_retry': False,
            'statement_atomicity_only': True,
            'transaction_finality_interpreted_by_common_code': False,
        }

    def _compile_admin(self, payload, route):
        kind, operation = payload['kind'], payload['operation']
        draft, target = payload.get('draft', {}), payload.get('target', {})
        database = target.get('database') or draft.get('database') or (
            route['database']
        )
        name = target.get('name') or target.get('table') or draft.get('name')
        qualified = _qualified(database, name) if name else None
        if operation == 'inspect':
            if kind in {'table', 'view', 'materialized-view'}:
                limit = draft.get('limit', 200)
                _integer(limit, 'limit', 1, MAX_PAGE_SIZE)
                return f'SELECT * FROM {qualified} LIMIT {limit}', None, None
            return 'SELECT 1 AS inspected', None, None
        if kind == 'database':
            if operation == 'create':
                engine = draft.get('engine', 'Atomic')
                if engine not in {'Atomic', 'Memory', 'Lazy'}:
                    raise ClickHouseClientError('database engine is invalid')
                suffix = f' ENGINE = {engine}'
                if engine == 'Lazy':
                    suffix += '(60)'
                comment = draft.get('comment')
                if comment:
                    suffix += f' COMMENT {_sql_string(comment)}'
                return (
                    f'CREATE DATABASE {_quote_identifier(name)}{suffix}',
                    None, None,
                )
            if operation == 'alter':
                return (
                    f'ALTER DATABASE {_quote_identifier(name)} MODIFY '
                    f'COMMENT {_sql_string(draft.get("definition", ""))}',
                    None, None,
                )
            if operation == 'rename':
                return (f'RENAME DATABASE {_quote_identifier(name)} TO '
                        f'{_quote_identifier(draft["new_name"])}', None, None)
            if operation == 'drop':
                return (
                    f'DROP DATABASE {_quote_identifier(name)}', None, None
                )
        if kind == 'table':
            if operation == 'create':
                columns = []
                for column in draft['columns']:
                    item = '{} {}'.format(
                        _quote_identifier(column['name']),
                        _clickhouse_type(column['type']),
                    )
                    if column.get('default_expression'):
                        item += ' DEFAULT ' + _expression(
                            column['default_expression'], 'default expression',
                            8192
                        )
                    columns.append(item)
                engine = draft.get('engine', 'MergeTree')
                allowed = {
                    'MergeTree', 'ReplacingMergeTree',
                    'AggregatingMergeTree', 'Memory', 'Log',
                }
                if engine not in allowed:
                    raise ClickHouseClientError('table engine is invalid')
                source = '{}{}{}'.format(
                    f'CREATE TABLE {_qualified(database, name)} (',
                    ', '.join(columns), f') ENGINE = {engine}()',
                )
                if engine not in {'Memory', 'Log'}:
                    if draft.get('partition_by'):
                        source += ' PARTITION BY ' + _expression(
                            draft['partition_by'], 'partition expression', 8192
                        )
                    if draft.get('primary_key'):
                        source += ' PRIMARY KEY ' + _expression(
                            draft['primary_key'], 'primary key', 8192
                        )
                    source += ' ORDER BY ' + _expression(
                        draft.get('order_by', 'tuple()'), 'ordering key', 8192
                    )
                    if draft.get('ttl'):
                        source += ' TTL ' + _expression(
                            draft['ttl'], 'TTL', 8192
                        )
                return source, None, None
            if operation == 'insert':
                values = _mapping(draft['values'], 'row values')
                return (
                    f'INSERT INTO {qualified} FORMAT JSONEachRow',
                    None, [values],
                )
            if operation in {'update', 'delete'}:
                identity = self._resolve_row_identity(
                    draft['concurrency_token'], route, target
                )
                predicate, parameters = self._identity_predicate(identity)
                if operation == 'delete':
                    return (
                        f'ALTER TABLE {qualified} DELETE WHERE {predicate}',
                        parameters, None,
                    )
                changes = _mapping(draft['changes'], 'row changes')
                assignments = []
                key_names = {item[0] for item in identity.key_values}
                for index, (column, value) in enumerate(changes.items()):
                    _identifier(column, 'changed column')
                    if column in key_names:
                        raise ClickHouseClientError(
                            'primary-key columns cannot be changed in the grid'
                        )
                    parameter = f'cde_change_{index}'
                    column_type = self._column_type(
                        route, database, name, column
                    )
                    assignments.append(
                        f'{_quote_identifier(column)} = '
                        f'{{{parameter}:{column_type}}}'
                    )
                    parameters[parameter] = value
                return (
                    f'ALTER TABLE {qualified} UPDATE '
                    f'{", ".join(assignments)} WHERE {predicate}',
                    parameters, None,
                )
            if operation == 'rename':
                return (
                    f'RENAME TABLE {qualified} TO '
                    f'{_qualified(database, draft["new_name"])}',
                    None, None,
                )
            if operation == 'alter':
                return (
                    f'ALTER TABLE {qualified} {self._definition(draft)}',
                    None, None,
                )
            if operation == 'drop':
                return f'DROP TABLE {qualified}', None, None
        if kind == 'column':
            table = target.get('table')
            qualified = _qualified(database, table)
            column = target.get('name') or draft.get('name')
            if operation == 'create':
                clause = (f'ADD COLUMN {_quote_identifier(column)} '
                          f'{_clickhouse_type(draft["type"])}')
                if draft.get('default_expression'):
                    clause += ' DEFAULT ' + _expression(
                        draft['default_expression'], 'default expression', 8192
                    )
            elif operation == 'alter':
                clause = (f'MODIFY COLUMN {_quote_identifier(column)} '
                          f'{_clickhouse_type(draft["type"])}')
            elif operation == 'rename':
                clause = (f'RENAME COLUMN {_quote_identifier(column)} TO '
                          f'{_quote_identifier(draft["new_name"])}')
            else:
                clause = f'DROP COLUMN {_quote_identifier(column)}'
            return f'ALTER TABLE {qualified} {clause}', None, None
        if kind in {'view', 'materialized-view'}:
            prefix = (
                'MATERIALIZED VIEW'
                if kind == 'materialized-view' else 'VIEW'
            )
            if operation == 'create':
                destination = ''
                storage = ''
                has_destination = draft.get('destination_table')
                if kind == 'materialized-view' and has_destination:
                    destination = ' TO ' + _qualified(
                        database, draft['destination_table']
                    )
                elif kind == 'materialized-view':
                    engine = draft.get('engine', 'MergeTree')
                    allowed = {
                        'MergeTree', 'ReplacingMergeTree',
                        'AggregatingMergeTree', 'Memory', 'Log',
                    }
                    if engine not in allowed:
                        raise ClickHouseClientError(
                            'materialized-view engine is invalid'
                        )
                    storage = f' ENGINE = {engine}()'
                    if engine not in {'Memory', 'Log'}:
                        storage += ' ORDER BY ' + _expression(
                            draft.get('order_by', 'tuple()'),
                            'ordering key', 8192,
                        )
                source = (
                    f'CREATE {prefix} {qualified}{destination}{storage} AS '
                    f'{self._select(draft["select"])}'
                )
                return source, None, None
            if operation == 'alter':
                return (f'ALTER TABLE {qualified} MODIFY QUERY '
                        f'{self._select(draft["select"])}', None, None)
            if operation == 'rename':
                return (
                    f'RENAME TABLE {qualified} TO '
                    f'{_qualified(database, draft["new_name"])}',
                    None, None,
                )
            return f'DROP VIEW {qualified}', None, None
        if kind == 'dictionary':
            if operation in {'create', 'alter'}:
                attributes = []
                for item in draft['attributes']:
                    item = _mapping(item, 'dictionary attribute')
                    attributes.append(
                        f'{_quote_identifier(item["name"])} '
                        f'{_clickhouse_type(item["type"])}'
                    )
                verb = (
                    'CREATE' if operation == 'create'
                    else 'CREATE OR REPLACE'
                )
                source = '{}{}{}{}{}{}{}'.format(
                    f'{verb} DICTIONARY {qualified} (',
                    ', '.join(attributes),
                    ') PRIMARY KEY '
                    f'{_quote_identifier(draft["primary_key"])} ',
                    'SOURCE(' + _expression(
                        draft['source'], 'dictionary source', 16384
                    ) + ') ',
                    'LAYOUT(' + _expression(
                        draft['layout'], 'dictionary layout', 1024
                    ) + ') ',
                    'LIFETIME(',
                    _expression(
                        draft['lifetime'], 'dictionary lifetime', 1024
                    ) + ')',
                )
                return source, None, None
            return f'DROP DICTIONARY {qualified}', None, None
        if kind == 'function':
            if operation in {'create', 'alter'}:
                verb = (
                    'CREATE FUNCTION' if operation == 'create'
                    else 'CREATE OR REPLACE FUNCTION'
                )
                return (
                    f'{verb} {_quote_identifier(name)} AS '
                    f'{_expression(draft["lambda"], "lambda expression")}',
                    None, None,
                )
            return f'DROP FUNCTION {_quote_identifier(name)}', None, None
        if kind in {'projection', 'data-skipping-index'}:
            table = target.get('table')
            qualified = _qualified(database, table)
            if operation in {'create', 'alter'}:
                object_name = target.get('name') or draft.get('name')
                if kind == 'projection':
                    clause = (
                        f'ADD PROJECTION {_quote_identifier(object_name)} '
                        f'({self._select(draft["expression"])})'
                    )
                else:
                    granularity = draft.get('granularity', 1)
                    _integer(granularity, 'granularity', 1, 1000000)
                    index_expression = _expression(
                        draft['expression'], 'index expression', 8192
                    )
                    index_type = _expression(
                        draft['type'], 'index type', 1024
                    )
                    clause = (
                        f'ADD INDEX {_quote_identifier(object_name)} '
                        f'{index_expression} TYPE {index_type} '
                        f'GRANULARITY {granularity}'
                    )
                if operation == 'alter':
                    drop = (
                        'DROP PROJECTION'
                        if kind == 'projection' else 'DROP INDEX'
                    )
                    return (
                        f'ALTER TABLE {qualified} {drop} '
                        f'{_quote_identifier(object_name)}, {clause}',
                        None, None,
                    )
                return f'ALTER TABLE {qualified} {clause}', None, None
            clause = (
                'DROP PROJECTION' if kind == 'projection' else 'DROP INDEX'
            )
            return (f'ALTER TABLE {qualified} {clause} '
                    f'{_quote_identifier(target["name"])}', None, None)
        if kind == 'partition' and operation == 'execute':
            table = target['table']
            partition = _sql_string(
                target.get('partition_id') or target['name']
            )
            action = draft['action']
            if action == 'optimize':
                return (f'OPTIMIZE TABLE {_qualified(database, table)} '
                        f'PARTITION ID {partition} FINAL', None, None)
            if action not in {'freeze', 'detach', 'drop'}:
                raise ClickHouseClientError('partition action is invalid')
            return (f'ALTER TABLE {_qualified(database, table)} '
                    f'{action.upper()} PARTITION ID {partition}', None, None)
        if kind in {'server', 'cluster', 'replica'} and operation == 'execute':
            action = draft['action']
            if action == 'reload-config':
                return 'SYSTEM RELOAD CONFIG', None, None
            if action == 'flush-logs':
                return 'SYSTEM FLUSH LOGS', None, None
            if action == 'sync-replica' and kind == 'replica':
                return (
                    'SYSTEM SYNC REPLICA '
                    f'{_qualified(target["database"], target["table"])}',
                    None, None,
                )
            raise ClickHouseClientError('system action is invalid')
        if kind in {'user', 'role', 'quota', 'settings-profile', 'row-policy'}:
            return self._compile_security(payload, route)
        raise ClickHouseClientError(
            'ClickHouse administration operation is unavailable'
        )

    def _compile_security(self, payload, route):
        kind, operation = payload['kind'], payload['operation']
        draft, target = payload.get('draft', {}), payload.get('target', {})
        name = target.get('name') or draft.get('name')
        keyword = {
            'user': 'USER', 'role': 'ROLE', 'quota': 'QUOTA',
            'settings-profile': 'SETTINGS PROFILE',
            'row-policy': 'ROW POLICY',
        }[kind]
        if kind == 'user' and operation in {'create', 'alter'}:
            password = self._admin_password(draft['password_reference'], route)
            verb = 'CREATE' if operation == 'create' else 'ALTER'
            return (f'{verb} USER {_quote_identifier(name)} IDENTIFIED WITH '
                    f'sha256_password BY {_sql_string(password)}', None, None)
        if operation == 'create':
            definition = draft.get('definition')
            suffix = (
                ' ' + _text(definition, 'definition', 16384)
                if definition else ''
            )
            return (
                f'CREATE {keyword} {_quote_identifier(name)}{suffix}',
                None, None,
            )
        if operation == 'alter':
            return (f'ALTER {keyword} {_quote_identifier(name)} '
                    f'{self._definition(draft)}', None, None)
        if operation == 'rename':
            return (f'ALTER {keyword} {_quote_identifier(name)} RENAME TO '
                    f'{_quote_identifier(draft["new_name"])}', None, None)
        if operation in {'grant', 'revoke'} and kind in {'user', 'role'}:
            privileges = _text(draft['privileges'], 'privileges', 4096)
            scope = _text(draft['scope'], 'scope', 1024)
            privileges_valid = re.fullmatch(
                r'[A-Za-z_, ]+', privileges
            )
            scope_valid = re.fullmatch(
                r'(?:\*|`[^`]+`)(?:\.(?:\*|`[^`]+`))?', scope
            )
            if not privileges_valid or not scope_valid:
                raise ClickHouseClientError('grant definition is invalid')
            verb = operation.upper()
            return (f'{verb} {privileges} ON {scope} '
                    f'{"TO" if verb == "GRANT" else "FROM"} '
                    f'{_quote_identifier(name)}', None, None)
        if operation == 'drop':
            return f'DROP {keyword} {_quote_identifier(name)}', None, None
        raise ClickHouseClientError('security operation is unavailable')

    def _admin_password(self, reference, route):
        admin_route = copy.deepcopy(route)
        admin_route['credential_reference_id'] = _text(
            reference, 'password secret reference'
        )
        return self._with_password(
            admin_route, lambda password: password or '',
            purpose='administer', expected_kind='database_password',
        )

    @staticmethod
    def _definition(draft):
        return _expression(
            draft.get('definition'), 'native definition', 16384
        )

    @staticmethod
    def _select(source):
        source = _expression(source, 'SELECT expression', 64 * 1024)
        if not re.match(r'^SELECT\b', source, re.I):
            raise ClickHouseClientError(
                'view/projection expression must start with SELECT'
            )
        return source

    @staticmethod
    def _native_target(target):
        if target is None:
            return {}
        target = _mapping(target, 'target resource')
        extensions = target.get('extensions')
        if isinstance(extensions, Mapping):
            provider = extensions.get('clickhouse')
            if isinstance(provider, Mapping) and isinstance(
                provider.get('native'), Mapping
            ):
                return copy.deepcopy(dict(provider['native']))
        native = target.get('native')
        if isinstance(native, Mapping):
            return copy.deepcopy(dict(native))
        return {
            key: copy.deepcopy(value) for key, value in target.items()
            if key in {
                'database', 'table', 'name', 'partition_id', 'engine',
                'primary_key',
            }
        }

    @staticmethod
    def _safe_target(target):
        return {
            key: copy.deepcopy(value) for key, value in target.items()
            if key not in {'password', 'secret', 'token'}
        }

    def _route_fingerprint(self, route):
        return hashlib.sha256(json.dumps({
            key: route.get(key) for key in (
                'route_id', 'host', 'port', 'database', 'username', 'tls_mode'
            )
        }, sort_keys=True).encode('utf-8')).hexdigest()

    def _column_type(self, route, database, table, column):
        result = self._rows(
            route,
            'SELECT type FROM system.columns WHERE database = '
            f'{_sql_string(database)} AND table = {_sql_string(table)} AND '
            f'name = {_sql_string(column)} LIMIT 1',
        )
        if len(result) != 1:
            raise ClickHouseClientError('column type is unavailable')
        return _clickhouse_type(result[0]['type'])

    def _primary_key_columns(self, route, database, table):
        return self._rows(
            route,
            'SELECT name, type FROM system.columns WHERE database = '
            f'{_sql_string(database)} AND table = {_sql_string(table)} AND '
            'is_in_primary_key = 1 ORDER BY position',
        )

    def _issue_row_identity(self, route, database, table, columns, row):
        values = tuple(
            (item['name'], item['type'], copy.deepcopy(row.get(item['name'])))
            for item in columns
        )
        digest = self._generation({'keys': values, 'row': row})
        token = uuid.uuid4().hex
        identity = _RowIdentity(
            self._route_fingerprint(route), database, table, values, digest,
            self._clock() + ROW_IDENTITY_TTL_SECONDS,
        )
        with self._lock:
            self._row_identities[token] = identity
        return token, digest

    def _resolve_row_identity(self, token, route, target):
        with self._lock:
            identity = self._row_identities.pop(token, None)
        if identity is None or identity.expires_at < self._clock():
            raise ClickHouseClientError('row identity is absent or stale')
        if identity.route_fingerprint != self._route_fingerprint(route):
            raise ClickHouseClientError(
                'row identity belongs to another endpoint'
            )
        if (identity.database, identity.table) != (
            target.get('database') or route['database'], target.get('table')
        ):
            raise ClickHouseClientError(
                'row identity belongs to another table'
            )
        return identity

    @staticmethod
    def _identity_predicate(identity):
        parameters = {}
        predicates = []
        for index, (name, item_type, value) in enumerate(identity.key_values):
            parameter = f'cde_key_{index}'
            predicates.append(
                f'{_quote_identifier(name)} = {{{parameter}:{item_type}}}'
            )
            parameters[parameter] = value
        if not predicates:
            raise ClickHouseClientError(
                'table has no primary-key row identity'
            )
        return ' AND '.join(predicates), parameters

    def read_admin_rows(self, request):
        route = self._route({'route': request.get('_provider_route')})
        target = self._native_target(request.get('target_resource'))
        database = target.get('database') or route['database']
        table = target.get('table') or target.get('name')
        if not table:
            raise ClickHouseClientError('table target is invalid')
        limit = request.get('limit', 200)
        offset = request.get('offset', 0)
        _integer(limit, 'limit', 1, MAX_PAGE_SIZE)
        _integer(offset, 'offset', 0, 1000000000)
        keys = self._primary_key_columns(route, database, table)
        result = self._query(
            route,
            f'SELECT * FROM {_qualified(database, table)} LIMIT {limit} '
            f'OFFSET {offset}',
        )
        rows = []
        for row in result['data']:
            token = digest = None
            if keys:
                token, digest = self._issue_row_identity(
                    route, database, table, keys, row
                )
            rows.append({
                'values': row, 'identity_token': token,
                'concurrency_token': token,
                'provider_generation': digest,
            })
        return {
            'rows': rows, 'columns': result['meta'],
            'editable': bool(keys) and not route['read_only'],
            'insertable': not route['read_only'],
            'row_identity_columns': [item['name'] for item in keys],
            'continuation': (
                {'offset': offset + len(rows)} if len(rows) == limit else None
            ),
            'limits': {'maximum_page_size': MAX_PAGE_SIZE},
            'provider_owned_identity': True,
        }

    def cancel_admin_rows(self, request):
        token = request.get('cursor_token')
        return {'cancelled': bool(token), 'provider_owned_cursor': True}

    def close(self):
        with self._lock:
            self._sessions.clear()
            self._row_identities.clear()
        self.transport.close()
