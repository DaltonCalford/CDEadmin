##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Bounded psycopg adapter for the XTDB 2.1 PostgreSQL-wire surface.

The adapter intentionally treats PostgreSQL wire as transport, not identity.
Only ``xt.version()`` admits a runtime. Native transaction observations are
opaque and uncertain mutations are never replayed.
"""

from __future__ import annotations

import base64
import copy
import datetime
import decimal
import hashlib
import importlib
import json
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pgadmin.cdeadmin.sdk import PilotProviderError


QUALIFIED_DRIVER_VERSION = '3.3.4'
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_RESULT_RECORDS = 10000
MAX_PAGE_SIZE = 500
MAX_VALUE_DEPTH = 24
MAX_IDENT_LENGTH = 255
ROW_IDENTITY_TTL_SECONDS = 600

_XTDB_VERSION = re.compile(
    r'^XTDB\s*@\s*(?P<version>\d+\.\d+\.\d+)(?:[-+ ].*)?$', re.I
)
_XTDB_USER_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_$]*$')
_READ_PREFIXES = frozenset({'SELECT', 'SHOW', 'EXPLAIN', 'VALUES', 'WITH'})
_TX_PREFIXES = frozenset({'BEGIN', 'START', 'COMMIT', 'ROLLBACK', 'SET'})
_MUTATION_PREFIXES = frozenset({
    'INSERT', 'UPDATE', 'PATCH', 'DELETE', 'ERASE', 'ASSERT', 'COPY',
    'CREATE', 'ALTER', 'ATTACH', 'DETACH', 'EXECUTE',
})


class XTDBClientError(PilotProviderError):
    """An XTDB request failed before a safe native result was available."""


class XTDBDependencyError(XTDBClientError):
    """The qualified psycopg dependency is unavailable or has drifted."""


class XTDBUnknownOutcomeError(XTDBClientError):
    """A mutating request may have reached XTDB before transport failure."""

    outcome = 'unknown_requires_observation'
    retryable = False


@dataclass
class _Session:
    connection: object
    route: dict[str, Any]
    transaction_observation: str = 'idle-or-server-owned'
    last_basis: dict[str, Any] | None = None

    def close(self):
        close = getattr(self.connection, 'close', None)
        if callable(close):
            close()


@dataclass
class _Result:
    rows: list[dict[str, Any]]
    columns: list[dict[str, Any]]
    rowcount: int | None
    command: str
    connection: object
    cancelled: bool = False


@dataclass(frozen=True)
class _RowIdentity:
    route_fingerprint: str
    database: str
    schema: str
    table: str
    document_id: object
    system_from: object
    digest: str
    expires_at: float


@dataclass
class _AdminCursor:
    route_fingerprint: str
    database: str
    schema: str
    table: str
    offset: int
    expires_at: float


def _mapping(value, label):
    if not isinstance(value, Mapping):
        raise XTDBClientError(f'{label} must be an object')
    return copy.deepcopy(dict(value))


def _text(value, label, maximum=1024, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise XTDBClientError(f'{label} must not be empty')
    value = value.strip()
    if len(value) > maximum or any(ord(ch) < 32 for ch in value):
        raise XTDBClientError(f'{label} is invalid')
    return value


def _integer(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise XTDBClientError(f'{label} must be an integer')
    if not minimum <= value <= maximum:
        raise XTDBClientError(f'{label} is outside the admitted range')
    return value


def _identifier(value, label):
    value = _text(value, label, MAX_IDENT_LENGTH)
    if '\x00' in value:
        raise XTDBClientError(f'{label} contains a forbidden character')
    return value


def _quote_identifier(value):
    return '"' + _identifier(value, 'identifier').replace('"', '""') + '"'


def _user_identifier(value):
    """Return the exact unquoted form required by XTDB's user parser.

    XTDB 2.1 obtains user names with ``identifier.getText()`` rather than the
    normal identifier decoder, so a delimited SQL identifier would persist
    its quote characters as part of the principal name.
    """
    value = _identifier(value, 'username')
    if not _XTDB_USER_IDENTIFIER.fullmatch(value):
        raise XTDBClientError(
            'username must be an unquoted XTDB regular identifier'
        )
    return value


def _qualified(schema, table):
    return f'{_quote_identifier(schema)}.{_quote_identifier(table)}'


def _sql_string(value):
    value = _text(value, 'SQL literal', 1024 * 1024, required=False)
    return "'" + (value or '').replace("'", "''") + "'"


class XTDBClient:
    """XTDB 2.1 client with native, fail-closed administration contracts."""

    ADMIN_OPERATIONS = {
        'cluster': frozenset({'inspect'}),
        'node': frozenset({'inspect'}),
        'database': frozenset({'inspect', 'create', 'drop'}),
        'table': frozenset({
            'inspect', 'create', 'insert', 'update', 'delete', 'erase'
        }),
        'column': frozenset({'inspect'}),
        'document': frozenset(
            {'inspect', 'insert', 'update', 'delete', 'erase'}
        ),
        'entity': frozenset(
            {'inspect', 'insert', 'update', 'delete', 'erase'}
        ),
        'valid-time': frozenset({'inspect'}),
        'system-time': frozenset({'inspect'}),
        'transaction': frozenset({'inspect'}),
        'transaction-log': frozenset({'inspect'}),
        'health': frozenset({'inspect', 'execute'}),
        'user': frozenset({'inspect', 'create', 'alter'}),
    }

    ROUTE_FIELDS = frozenset({
        'route_id', 'host', 'port', 'database', 'username',
        'credential_reference_id', 'principal_reference', 'tls_mode',
        'tls_ca_file', 'tls_certificate_file', 'tls_key_file',
        'connect_timeout', 'statement_timeout', 'application_name',
        'read_only', 'healthz_url', 'tool_workspace',
    })

    def __init__(self, secret_acquirer=None, module=None, clock=None):
        self.secret_acquirer = secret_acquirer
        try:
            self.module = module or importlib.import_module('psycopg')
        except (ImportError, ModuleNotFoundError) as exc:
            raise XTDBDependencyError(
                'qualified psycopg dependency is unavailable'
            ) from exc
        version = str(getattr(self.module, '__version__', ''))
        if version != QUALIFIED_DRIVER_VERSION:
            raise XTDBDependencyError(
                f'psycopg {QUALIFIED_DRIVER_VERSION} is required'
            )
        if not callable(getattr(self.module, 'connect', None)):
            raise XTDBDependencyError('psycopg connector is unavailable')
        self._clock = clock or time.monotonic
        self._connections = []
        self._results = []
        self._row_identities: dict[str, _RowIdentity] = {}
        self._admin_cursors: dict[str, _AdminCursor] = {}
        self._lock = threading.RLock()

    @classmethod
    def _route(cls, request):
        route = request.get('route')
        if route is None:
            route = request.get('_provider_route')
        route = _mapping(route, 'XTDB route')
        unknown = sorted(set(route).difference(cls.ROUTE_FIELDS))
        if unknown:
            raise XTDBClientError(
                'XTDB route contains unknown fields: ' + ', '.join(unknown)
            )
        route.setdefault('host', '127.0.0.1')
        route.setdefault('port', 5432)
        route.setdefault('database', 'xtdb')
        route.setdefault('username', 'xtdb')
        route.setdefault('tls_mode', 'disable')
        route.setdefault('connect_timeout', 10)
        route.setdefault('statement_timeout', 30)
        route.setdefault('application_name', 'CDEadmin')
        route.setdefault('read_only', False)
        route['host'] = _text(route['host'], 'host', 255)
        route['port'] = _integer(route['port'], 'port', 1, 65535)
        route['database'] = _identifier(route['database'], 'database')
        route['username'] = _identifier(route['username'], 'username')
        route['connect_timeout'] = _integer(
            route['connect_timeout'], 'connect_timeout', 1, 120
        )
        route['statement_timeout'] = _integer(
            route['statement_timeout'], 'statement_timeout', 1, 3600
        )
        route['application_name'] = _text(
            route['application_name'], 'application_name', 63
        )
        if not isinstance(route['read_only'], bool):
            raise XTDBClientError('read_only must be true or false')
        if route['tls_mode'] not in {
            'disable', 'require', 'verify-ca', 'verify-full'
        }:
            raise XTDBClientError('XTDB TLS mode is invalid')
        for field in (
            'tls_ca_file', 'tls_certificate_file', 'tls_key_file',
            'healthz_url', 'tool_workspace', 'route_id',
            'credential_reference_id', 'principal_reference',
        ):
            if field in route:
                route[field] = _text(route[field], field, 4096)
        if route['tls_mode'] in {'verify-ca', 'verify-full'} and not route.get(
            'tls_ca_file'
        ):
            raise XTDBClientError(
                'verified XTDB TLS requires a certificate authority file'
            )
        if bool(route.get('tls_certificate_file')) != bool(
            route.get('tls_key_file')
        ):
            raise XTDBClientError(
                'XTDB client certificate and key must be supplied together'
            )
        return route

    @staticmethod
    def _connection_arguments(route):
        values = {
            'host': route['host'], 'port': route['port'],
            'dbname': route['database'], 'user': route['username'],
            'connect_timeout': route['connect_timeout'],
            'application_name': route['application_name'],
            'autocommit': True, 'sslmode': route['tls_mode'],
            'options': (
                f"-c statement_timeout={route['statement_timeout'] * 1000}"
            ),
        }
        mapping = {
            'tls_ca_file': 'sslrootcert',
            'tls_certificate_file': 'sslcert',
            'tls_key_file': 'sslkey',
        }
        for source, target in mapping.items():
            if route.get(source):
                values[target] = route[source]
        return values

    def _connect(self, request):
        route = self._route(request)
        kwargs = self._connection_arguments(route)
        reference = route.get('credential_reference_id')
        try:
            if reference is None:
                connection = self.module.connect(**kwargs)
            else:
                principal = route.get('principal_reference')
                if not principal or not callable(self.secret_acquirer):
                    raise XTDBClientError(
                        'XTDB credential binding is unavailable'
                    )
                lease = self.secret_acquirer(
                    reference, principal, 'connect', 'database_password'
                )
                with lease:
                    connection = lease.use(lambda view: self.module.connect(
                        **kwargs, password=bytes(view).decode('utf-8')
                    ))
            self._register_string_dumper(connection)
            session = _Session(connection, route)
            self._connections.append(session)
            return session
        except XTDBClientError:
            raise
        except Exception as exc:
            raise XTDBClientError(
                f'XTDB connection failed ({type(exc).__name__})'
            ) from None

    def _register_string_dumper(self, connection):
        adapters = getattr(connection, 'adapters', None)
        register = getattr(adapters, 'register_dumper', None)
        types = getattr(self.module, 'types', None)
        strings = getattr(types, 'string', None)
        dumper = getattr(strings, 'StrDumperVarchar', None)
        if callable(register) and dumper is not None:
            register(str, dumper)

    def runtime_identity(self, request, handle=None):
        temporary = handle is None
        session = handle or self._connect(request)
        if not isinstance(session, _Session):
            raise XTDBClientError('XTDB session is invalid')
        cursor = None
        try:
            cursor = session.connection.cursor()
            cursor.execute('SELECT xt.version() AS xtdb_version')
            row = cursor.fetchone()
            raw = self._first_value(row)
            match = _XTDB_VERSION.match(str(raw).strip())
            if match is None:
                raise XTDBClientError(
                    'PostgreSQL-wire endpoint did not prove XTDB identity'
                )
            version = match.group('version')
            return {
                'engine_id': 'xtdb', 'version': version,
                'build_id': str(raw).strip(),
                'protocol_id': 'postgresql_wire',
            }
        except XTDBClientError:
            raise
        except Exception as exc:
            raise XTDBClientError(
                f'XTDB identity verification failed ({type(exc).__name__})'
            ) from None
        finally:
            self._safe_close(cursor)
            if temporary:
                self._forget_and_close(session)

    @staticmethod
    def _first_value(row):
        if isinstance(row, Mapping):
            values = list(row.values())
            return values[0] if values else None
        if isinstance(row, Sequence) and not isinstance(
            row, (str, bytes, bytearray)
        ):
            return row[0] if row else None
        return row

    def open_session(self, request):
        return self._connect(request)

    @staticmethod
    def describe_transaction(handle):
        if not isinstance(handle, _Session):
            raise XTDBClientError('XTDB session is invalid')
        return {
            'native_observation': handle.transaction_observation,
            'basis': copy.deepcopy(handle.last_basis),
            'driver_autocommit': getattr(
                handle.connection, 'autocommit', None
            ),
            'driver_observation_only': True,
            'finality_interpreted_by_common_code': False,
            'automatic_replay': False,
        }

    @staticmethod
    def _command(source):
        source = source.lstrip()
        while source.startswith('--'):
            _, separator, source = source.partition('\n')
            if not separator:
                return ''
            source = source.lstrip()
        match = re.match(r'([A-Za-z]+)', source)
        return match.group(1).upper() if match else ''

    @classmethod
    def _is_mutation(cls, source):
        command = cls._command(source)
        return command in _MUTATION_PREFIXES or command not in (
            _READ_PREFIXES | _TX_PREFIXES
        )

    def _outcome_uncertain(self, exc):
        uncertain_types = [ConnectionError, TimeoutError, OSError]
        for name in ('OperationalError', 'InterfaceError'):
            value = getattr(self.module, name, None)
            if isinstance(value, type):
                uncertain_types.append(value)
        return isinstance(exc, tuple(uncertain_types))

    def execute(self, handle, request):
        if not isinstance(handle, _Session):
            raise XTDBClientError('XTDB session is invalid')
        source = request.get('source')
        if not isinstance(source, str) or not source.strip():
            raise XTDBClientError('XTDB SQL source must not be empty')
        if len(source.encode('utf-8')) > MAX_SOURCE_BYTES:
            raise XTDBClientError('XTDB SQL source exceeds the safety limit')
        parameters = request.get('parameters', ())
        if not isinstance(parameters, (Mapping, list, tuple)):
            raise XTDBClientError(
                'XTDB SQL parameters must be an object or array'
            )
        command = self._command(source)
        if handle.route.get('read_only') and self._is_mutation(source):
            raise XTDBClientError('mutations are refused on a read-only route')
        cursor = None
        try:
            cursor = handle.connection.cursor()
            cursor.execute(source, self._adapt_parameters(parameters) or None)
            result = self._result_from_cursor(cursor, handle.connection,
                                              command)
            self._results.append(result)
            if command in {'BEGIN', 'START'}:
                handle.transaction_observation = 'server-begin-observed'
            elif command in {'COMMIT', 'ROLLBACK'}:
                handle.transaction_observation = (
                    f'server-{command.lower()}-response-observed'
                )
            elif command == 'SET' and any(
                key in source.upper()
                for key in ('AWAIT_TOKEN', 'TIME ZONE', 'TIMEZONE')
            ):
                handle.last_basis = {'statement_observed': command}
            return result
        except XTDBClientError:
            self._safe_close(cursor)
            raise
        except Exception as exc:
            self._safe_close(cursor)
            if self._is_mutation(source) and self._outcome_uncertain(exc):
                raise XTDBUnknownOutcomeError(
                    'XTDB mutation outcome is unknown; observe server state '
                    f'before any retry ({type(exc).__name__})'
                ) from None
            raise XTDBClientError(
                f'XTDB execution failed ({type(exc).__name__})'
            ) from None

    def _adapt_parameters(self, parameters):
        if isinstance(parameters, Mapping):
            return {
                key: self._adapt_value(value)
                for key, value in parameters.items()
            }
        return tuple(self._adapt_value(value) for value in parameters)

    def _result_from_cursor(self, cursor, connection, command):
        description = getattr(cursor, 'description', None) or ()
        names = [str(item[0]) for item in description]
        columns = [{
            'name': name,
            'native_type': (
                None if len(description[index]) < 2
                else str(description[index][1])
            ),
        } for index, name in enumerate(names)]
        rows = []
        size = 0
        if description:
            for raw in cursor.fetchmany(MAX_RESULT_RECORDS + 1):
                if len(rows) >= MAX_RESULT_RECORDS:
                    raise XTDBClientError(
                        'XTDB result exceeds the record safety limit'
                    )
                if isinstance(raw, Mapping):
                    record = {
                        str(key): self._json_value(value)
                        for key, value in raw.items()
                    }
                else:
                    record = {
                        name: self._json_value(value)
                        for name, value in zip(names, raw)
                    }
                size += len(json.dumps(
                    record, default=str, ensure_ascii=True
                ).encode('utf-8'))
                if size > MAX_RESULT_BYTES:
                    raise XTDBClientError(
                        'XTDB result exceeds the byte safety limit'
                    )
                rows.append(record)
        rowcount = getattr(cursor, 'rowcount', None)
        return _Result(
            rows, columns, rowcount if isinstance(rowcount, int) else None,
            command, connection,
        )

    @classmethod
    def _json_value(cls, value, depth=0):
        if depth > MAX_VALUE_DEPTH:
            raise XTDBClientError('XTDB value nesting exceeds safety limit')
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, decimal.Decimal):
            return {'$xtdb_type': 'decimal', 'value': str(value)}
        if isinstance(value, (datetime.date, datetime.time,
                              datetime.datetime, datetime.timedelta)):
            return {
                '$xtdb_type': type(value).__name__, 'value': str(value)
            }
        if isinstance(value, uuid.UUID):
            return {'$xtdb_type': 'uuid', 'value': str(value)}
        if isinstance(value, (bytes, bytearray, memoryview)):
            return {
                '$xtdb_type': 'varbinary',
                'base64': base64.b64encode(bytes(value)).decode('ascii'),
            }
        if isinstance(value, Mapping):
            return {
                str(key): cls._json_value(item, depth + 1)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [cls._json_value(item, depth + 1) for item in value]
        return {'$xtdb_type': type(value).__name__, 'value': str(value)}

    def describe_result(self, token):
        if not isinstance(token, _Result) or token not in self._results:
            raise XTDBClientError('XTDB result token is invalid')
        return {
            'result_kind': 'document',
            'schema': {'columns': copy.deepcopy(token.columns)},
            'payload': {
                'rows': copy.deepcopy(token.rows),
                'rowcount': token.rowcount,
                'command': token.command,
                'cancelled': token.cancelled,
            },
            'stream_reference': None,
            'complete': True,
        }

    def cancel(self, token):
        if not isinstance(token, _Result) or token not in self._results:
            raise XTDBClientError('XTDB result token is invalid')
        cancel = getattr(token.connection, 'cancel', None)
        if callable(cancel):
            cancel()
            token.cancelled = True
            return True
        return False

    @staticmethod
    def _generation(*values):
        return hashlib.sha256(json.dumps(
            values, sort_keys=True, default=str
        ).encode('utf-8')).hexdigest()[:20]

    @classmethod
    def _resource(cls, kind, name, native, parent=None):
        path = ['xtdb', kind, str(name)]
        if parent:
            path.insert(2, str(parent))
        return {
            'resource_id': ':'.join(path), 'resource_kind': kind,
            'display_name': str(name), 'authority_path': path,
            'display_path': path[1:], 'generation': cls._generation(native),
            'native': copy.deepcopy(native),
        }

    def _query_rows(self, session, source, parameters=()):
        cursor = session.connection.cursor()
        try:
            cursor.execute(source, parameters or None)
            description = getattr(cursor, 'description', None) or ()
            names = [str(item[0]) for item in description]
            return [
                dict(raw) if isinstance(raw, Mapping)
                else dict(zip(names, raw))
                for raw in cursor.fetchall()
            ]
        finally:
            self._safe_close(cursor)

    def list_resources(self, request):
        session = self._connect(request)
        try:
            identity = self.runtime_identity(request, session)
            database = session.route['database']
            resources = [
                self._resource('cluster', session.route['host'], {
                    'host': session.route['host'], 'protocol': 'pgwire'
                }),
                self._resource('node', identity['build_id'], {
                    'version': identity['version'],
                    'build_id': identity['build_id'],
                }),
                self._resource('database', database, {
                    'database': database, 'attached': database != 'xtdb'
                }),
            ]
            tables = self._query_rows(session, (
                'SELECT table_schema, table_name, table_type '
                'FROM information_schema.tables '
                'ORDER BY table_schema, table_name'
            ))
            columns = self._query_rows(session, (
                'SELECT table_schema, table_name, column_name, data_type '
                'FROM information_schema.columns '
                'ORDER BY table_schema, table_name, column_name'
            ))
            schemas = sorted({
                str(row.get('table_schema')) for row in tables
                if row.get('table_schema')
            })
            for schema in schemas:
                resources.append(self._resource(
                    'schema', schema, {'database': database, 'schema': schema},
                    database,
                ))
            for row in tables:
                schema = str(row.get('table_schema'))
                table = str(row.get('table_name'))
                native = {
                    'database': database, 'schema': schema, 'table': table,
                    'table_type': row.get('table_type'),
                }
                if schema == 'xt' and table == 'txs':
                    resources.extend((
                        self._resource(
                            'transaction-log', 'xt.txs', native, database
                        ),
                        self._resource(
                            'transaction', 'XTDB transactions', native,
                            database,
                        ),
                    ))
                    continue
                resources.append(self._resource(
                    'table', f'{schema}.{table}', native, database
                ))
                if schema not in {'information_schema', 'pg_catalog', 'xt'}:
                    resources.extend((
                        self._resource(
                            'document', f'{schema}.{table}', native, database
                        ),
                        self._resource(
                            'valid-time', f'{schema}.{table}', native, database
                        ),
                        self._resource(
                            'system-time', f'{schema}.{table}', native,
                            database,
                        ),
                    ))
            for row in columns:
                schema = str(row.get('table_schema'))
                table = str(row.get('table_name'))
                column = str(row.get('column_name'))
                resources.append(self._resource(
                    'column', f'{schema}.{table}.{column}', {
                        'database': database, 'schema': schema,
                        'table': table, 'column': column,
                        'data_type': row.get('data_type'),
                    }, database,
                ))
            try:
                users = self._query_rows(
                    session,
                    'SELECT username, usesuper FROM pg_catalog.pg_user '
                    'ORDER BY username',
                )
            except Exception:
                users = []
            for row in users:
                resources.append(self._resource(
                    'user', row.get('username'), {
                        'username': row.get('username'),
                        'usesuper': bool(row.get('usesuper')),
                    }, database,
                ))
            for metric_table in (
                'metrics_timers', 'metrics_gauges', 'metrics_counters'
            ):
                resources.append(self._resource(
                    'metric', f'xt.{metric_table}', {
                        'database': database, 'schema': 'xt',
                        'table': metric_table,
                    }, database,
                ))
            if session.route.get('healthz_url'):
                resources.append(self._resource(
                    'health', 'XTDB health', {
                        'healthz_url': session.route['healthz_url'],
                        'checks': ['started', 'alive', 'ready'],
                    }, database,
                ))
            return resources
        finally:
            self._forget_and_close(session)

    def inspect_resource(self, request):
        resource_id = request.get('resource_id')
        for resource in self.list_resources(request):
            if resource['resource_id'] == resource_id:
                return resource
        raise XTDBClientError('XTDB resource is unavailable')

    def describe_security(self, request):
        session = self._connect(request)
        try:
            users = self._query_rows(
                session,
                'SELECT username, usesuper FROM pg_catalog.pg_user '
                'ORDER BY username',
            )
            safe_users = [{
                'username': row.get('username'),
                'usesuper': bool(row.get('usesuper')),
            } for row in users]
            return {
                'resource_id': 'xtdb:security:users',
                'display_name': 'XTDB user-table authentication',
                'authority_path': ['xtdb', 'security', 'users'],
                'generation': self._generation(safe_users),
                'native': {
                    'authorization_model': 'xtdb-user-table-authentication',
                    'users': safe_users,
                    'passwords_exposed': False,
                    'auth_rules_configuration_owned': True,
                    'roles_and_grants_supported': False,
                },
            }
        finally:
            self._forget_and_close(session)

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
        catalog['native_planner'] = 'xtdb-sql-structured-planner'
        catalog['query_language'] = 'XTDB SQL 2.1 over PostgreSQL wire'
        catalog['document_identity'] = '_id'
        catalog['temporal_axes'] = ['valid_time', 'system_time']
        catalog['transaction_authority'] = 'xtdb-server-and-driver'
        catalog['native_outcomes_are_opaque'] = True
        catalog['xtql_transport_qualified'] = False
        catalog['common_finality_interpretation'] = False
        for resource in catalog.get('objects', []):
            kind = resource['resource_kind']
            if kind in {'table', 'document', 'entity'}:
                operations = resource.get('operations', [])
                if not any(op['operation_id'] == 'erase' for op in operations):
                    operations.append({
                        'operation_id': 'erase',
                        'title': 'Irreversibly erase all history',
                        'mutation_class': 'destructive',
                        'target_required': True,
                        'confirmation_required': True,
                        'required_permissions': [],
                        'form_id': 'xtdb-document-erase',
                        'form': self._document_form('erase'),
                    })
            for operation in resource.get('operations', []):
                form = self._admin_form(kind, operation['operation_id'])
                if form is not None:
                    operation['form'] = form
                if operation['operation_id'] in {'delete', 'erase', 'drop'}:
                    operation['confirmation_required'] = True
        return catalog

    def _document_form(self, operation):
        f = self._field
        if operation == 'insert':
            return self._form('xtdb-document-insert', 'Insert document', [
                f('values', 'Document fields (must contain _id)', 'json',
                  True, default={'_id': ''}),
                f('options', 'Temporal options', 'json', False, default={}),
            ])
        if operation == 'update':
            return self._form('xtdb-document-update', 'Update document', [
                f('selector', 'Provider row selector', 'json', True,
                  default={}),
                f('changes', 'Changed fields', 'json', True, default={}),
                f('concurrency_token', 'Provider row identity', required=True),
                f('options', 'Valid-time options', 'json', False, default={}),
            ])
        if operation == 'delete':
            return self._form('xtdb-document-delete', 'Delete document', [
                f('selector', 'Provider row selector', 'json', True,
                  default={}),
                f('concurrency_token', 'Provider row identity', required=True),
                f('confirmation', 'Confirmation phrase', required=True),
                f('options', 'Valid-time options', 'json', False, default={}),
            ])
        if operation == 'erase':
            return self._form(
                'xtdb-document-erase', 'Irreversibly erase history', [
                    f('row_identity', 'Provider row identity', required=True),
                    f('acknowledge_irreversible',
                      'I understand all bitemporal history is destroyed',
                      'boolean', True, default=False),
                ]
            )
        return None

    def _admin_form(self, kind, operation):
        f = self._field
        if operation == 'inspect':
            temporal = []
            if kind in {'table', 'document', 'entity', 'valid-time',
                        'system-time'}:
                temporal = [
                    f('valid_time_mode', 'Valid-time basis', 'select', False,
                      default='current', options=[
                          {'value': 'current', 'label': 'Current'},
                          {'value': 'all', 'label': 'All history'},
                          {'value': 'as_of', 'label': 'As of'},
                      ]),
                    f('valid_time', 'Valid-time instant', required=False),
                    f('system_time_mode', 'System-time basis', 'select', False,
                      default='current', options=[
                          {'value': 'current', 'label': 'Current'},
                          {'value': 'all', 'label': 'All history'},
                          {'value': 'as_of', 'label': 'As of'},
                      ]),
                    f('system_time', 'System-time instant', required=False),
                    f('limit', 'Maximum rows', 'number', False, default=200),
                ]
            return self._form('xtdb-inspect', 'Inspect', temporal)
        if kind == 'database' and operation == 'create':
            return self._form('xtdb-database-attach', 'Attach database', [
                f('name', 'Database name', required=True),
                f('config_yaml', 'Log and storage YAML', 'code', True,
                  max_length=1024 * 1024),
            ])
        if kind == 'database' and operation == 'drop':
            return self._form('xtdb-database-detach', 'Detach database', [
                f('acknowledge_detach', 'Confirm database detach',
                  'boolean', True, default=False),
            ])
        if kind in {'table', 'document', 'entity'} and operation in {
            'insert', 'update', 'delete', 'erase'
        }:
            return self._document_form(operation)
        if kind == 'table' and operation == 'create':
            return self._form(
                'xtdb-table-create-by-document',
                'Create table with its first document', [
                    f('schema', 'Schema', required=True, default='public'),
                    f('name', 'Table name', required=True),
                    f('values', 'Initial document (must contain _id)', 'json',
                      True, default={'_id': ''}),
                    f('options', 'Temporal options', 'json', False,
                      default={}),
                ]
            )
        if kind == 'user' and operation in {'create', 'alter'}:
            fields = [] if operation == 'alter' else [
                f('username', 'Username', required=True)
            ]
            fields.append(f(
                'password_reference', 'Password secret reference',
                'secret-reference', True, sensitive=True,
            ))
            return self._form(
                f'xtdb-user-{operation}', f'{operation.title()} user', fields
            )
        if kind == 'health' and operation == 'inspect':
            return self._form('xtdb-health-inspect', 'Inspect health', [
                f('check', 'Health check', 'select', True, default='ready',
                  options=[
                      {'value': 'started', 'label': 'Started'},
                      {'value': 'alive', 'label': 'Alive'},
                      {'value': 'ready', 'label': 'Ready'},
                  ]),
            ])
        if kind == 'health' and operation == 'execute':
            return self._form(
                'xtdb-finish-block', 'Finish the current transaction block', [
                    f('action', 'Action', 'select', True,
                      default='finish-block', options=[{
                          'value': 'finish-block',
                          'label': 'Finish transaction block',
                      }]),
                    f('acknowledge_operation', 'Confirm block maintenance',
                      'boolean', True, default=False),
                ]
            )
        return None

    def validate_admin_operation(self, request):
        kind = request['resource_kind']
        operation = request['operation_id']
        draft = request.get('draft', {})
        errors = []
        try:
            if kind == 'database' and operation == 'create':
                _identifier(draft.get('name'), 'database name')
                _text(draft.get('config_yaml'), 'database config YAML',
                      1024 * 1024)
            if kind == 'database' and operation == 'drop' and not draft.get(
                'acknowledge_detach'
            ):
                raise XTDBClientError('database detach must be acknowledged')
            if kind in {'table', 'document', 'entity'}:
                self._validate_document_draft(operation, draft)
            if kind == 'table' and operation == 'create':
                _identifier(draft.get('schema'), 'schema')
                _identifier(draft.get('name'), 'table name')
            if kind == 'user' and operation in {'create', 'alter'}:
                if operation == 'create':
                    _user_identifier(draft.get('username'))
                else:
                    _user_identifier(self._native_target(
                        request.get('target_resource')
                    ).get('username'))
                _text(draft.get('password_reference'),
                      'password secret reference', 1024)
            if kind == 'health':
                if operation == 'inspect' and draft.get(
                    'check', 'ready'
                ) not in {'started', 'alive', 'ready'}:
                    raise XTDBClientError('XTDB health check is invalid')
                if operation == 'execute' and (
                    draft.get('action') != 'finish-block' or
                    not draft.get('acknowledge_operation')
                ):
                    raise XTDBClientError(
                        'XTDB finish-block operation must be acknowledged'
                    )
        except XTDBClientError as exc:
            errors.append({
                'field_id': None, 'code': 'xtdb_native_validation',
                'message': str(exc),
            })
        return {'errors': errors}

    @staticmethod
    def _validate_document_draft(operation, draft):
        if operation == 'insert':
            document = draft.get('values')
            if not isinstance(document, Mapping) or '_id' not in document:
                raise XTDBClientError('document must contain _id')
            if document.get('_id') is None:
                raise XTDBClientError('document _id must not be null')
            for key in document:
                _identifier(key, 'document field')
                if key.startswith('_') and key not in {'_id'}:
                    raise XTDBClientError(
                        'system-time fields are provider controlled'
                    )
        if operation == 'create':
            document = draft.get('values')
            if not isinstance(document, Mapping) or '_id' not in document:
                raise XTDBClientError('initial document must contain _id')
            if document.get('_id') is None:
                raise XTDBClientError('initial document _id must not be null')
            for key in document:
                _identifier(key, 'document field')
                if key.startswith('_') and key != '_id':
                    raise XTDBClientError(
                        'system-time fields are provider controlled'
                    )
        if operation == 'update':
            changes = draft.get('changes')
            if not isinstance(changes, Mapping) or not changes:
                raise XTDBClientError('document changes must not be empty')
            for key in changes:
                _identifier(key, 'changed field')
                if key.startswith('_'):
                    raise XTDBClientError(
                        'document identity and temporal fields are immutable'
                    )
        if operation in {'update', 'delete'}:
            _text(draft.get('concurrency_token'), 'row identity', 1024)
            selector = draft.get('selector')
            if not isinstance(selector, Mapping) or selector.get(
                'identity_token'
            ) != draft.get('concurrency_token'):
                raise XTDBClientError(
                    'row selector and concurrency identity must match'
                )
            options = draft.get('options') or {}
            if not isinstance(options, Mapping):
                raise XTDBClientError('temporal options must be an object')
            mode = options.get('valid_time_mode', 'current')
            if mode not in {'current', 'all', 'portion'}:
                raise XTDBClientError('valid-time mode is invalid')
            if mode == 'portion' and not (
                options.get('valid_from') and options.get('valid_to')
            ):
                raise XTDBClientError(
                    'valid-time portion requires start and end values'
                )
        if operation == 'erase':
            _text(draft.get('row_identity'), 'row identity', 1024)
            if not draft.get('acknowledge_irreversible'):
                raise XTDBClientError(
                    'irreversible history erase must be acknowledged'
                )

    def plan_admin_operation(self, request):
        kind = request['resource_kind']
        operation = request['operation_id']
        draft = request.get('draft', {})
        target = request.get('target_resource')
        native = self._native_target(target)
        preview = {
            'kind': kind, 'operation': operation,
            'target': self._safe_target(native),
            'parameters_bound_at_execution': True,
        }
        if kind == 'database' and operation == 'create':
            preview['statement'] = 'ATTACH DATABASE <identifier> WITH <yaml>'
        elif kind == 'database' and operation == 'drop':
            preview['statement'] = 'DETACH DATABASE <identifier>'
        elif kind == 'user' and operation in {'create', 'alter'}:
            preview['statement'] = (
                f'{operation.upper()} USER <identifier> WITH PASSWORD '
                '<redacted>'
            )
        elif kind in {'table', 'document', 'entity'}:
            preview['statement'] = operation.upper()
            if operation == 'erase':
                preview['irreversible'] = True
        elif kind == 'health' and operation == 'execute':
            preview['statement'] = 'POST /system/finish-block'
        else:
            preview['statement'] = 'SELECT provider-owned metadata'
        return {
            'command_preview': preview,
            'warnings': ([
                'ERASE permanently destroys all valid-time and system-time '
                'history for the selected document.'
            ] if operation == 'erase' else []),
            'provider_payload': {
                'kind': kind, 'operation': operation,
                'draft': copy.deepcopy(draft), 'target': native,
                'route': copy.deepcopy(request.get('_provider_route')),
            },
            'receipt': {
                'provider': 'xtdb', 'automatic_retry': False,
                'transaction_finality_interpreted': False,
            },
        }

    def apply_admin_operation(self, request):
        payload = _mapping(request.get('provider_payload'), 'provider payload')
        route = _mapping(payload.get('route'), 'provider route')
        if payload['kind'] == 'health':
            return self._apply_health(payload, route)
        session = self._connect({'route': route})
        mutation = payload['operation'] not in {'inspect'}
        try:
            source, parameters = self._compile_admin(payload, route)
            cursor = session.connection.cursor()
            try:
                cursor.execute(source, parameters or None)
                rowcount = getattr(cursor, 'rowcount', None)
                if (
                    payload['operation'] in {'update', 'delete', 'erase'}
                    and rowcount == 0
                ):
                    raise XTDBClientError(
                        'XTDB document changed or no longer exists'
                    )
                rows = []
                if getattr(cursor, 'description', None):
                    names = [str(item[0]) for item in cursor.description]
                    rows = [
                        {name: self._json_value(value)
                         for name, value in zip(names, row)}
                        for row in cursor.fetchmany(MAX_PAGE_SIZE)
                    ]
                return {
                    'native_response_observed': True,
                    'rowcount': rowcount,
                    'rows': rows,
                    'automatic_retry': False,
                    'transaction_finality_interpreted_by_common_code': False,
                }
            finally:
                self._safe_close(cursor)
        except XTDBClientError:
            raise
        except Exception as exc:
            if mutation and self._outcome_uncertain(exc):
                raise XTDBUnknownOutcomeError(
                    'XTDB administration outcome is unknown; observe server '
                    f'state before any retry ({type(exc).__name__})'
                ) from None
            raise XTDBClientError(
                f'XTDB administration failed ({type(exc).__name__})'
            ) from None
        finally:
            self._forget_and_close(session)

    def _apply_health(self, payload, route):
        route = self._route({'route': route})
        operation = payload['operation']
        draft = payload.get('draft', {})
        if operation == 'inspect':
            check = draft.get('check', 'ready')
            return self._http_request(
                route, f'/healthz/{check}', method='GET', mutating=False
            )
        if operation == 'execute' and draft.get('action') == 'finish-block':
            return self._http_request(
                route, '/system/finish-block', method='POST', mutating=True
            )
        raise XTDBClientError('XTDB health operation is unavailable')

    def _http_request(self, route, path, method, mutating):
        base = route.get('healthz_url')
        if not base:
            raise XTDBClientError('XTDB health endpoint is not configured')
        parsed = urllib.parse.urlsplit(base)
        if (
            parsed.scheme not in {'http', 'https'} or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or
            parsed.fragment
        ):
            raise XTDBClientError('XTDB health endpoint URL is invalid')
        url = base.rstrip('/') + path
        request = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(
                request, timeout=route['connect_timeout']
            ) as response:
                body = response.read(4097)
                if len(body) > 4096:
                    raise XTDBClientError(
                        'XTDB health response exceeds the safety limit'
                    )
                return {
                    'native_response_observed': True,
                    'http_status': response.status,
                    'body': body.decode('utf-8', 'replace'),
                    'automatic_retry': False,
                    'transaction_finality_interpreted_by_common_code': False,
                }
        except XTDBClientError:
            raise
        except Exception as exc:
            if mutating and self._outcome_uncertain(exc):
                raise XTDBUnknownOutcomeError(
                    'XTDB finish-block outcome is unknown; observe node state '
                    f'before any retry ({type(exc).__name__})'
                ) from None
            raise XTDBClientError(
                f'XTDB health request failed ({type(exc).__name__})'
            ) from None

    def _compile_admin(self, payload, route):
        kind = payload['kind']
        operation = payload['operation']
        draft = payload.get('draft', {})
        native = payload.get('target', {})
        if kind == 'database':
            if operation == 'create':
                if route['database'] != 'xtdb':
                    raise XTDBClientError(
                        'databases must be attached through primary xtdb'
                    )
                name = _quote_identifier(draft['name'])
                yaml = draft['config_yaml']
                delimiter = '$cdeadmin_xtdb$'
                if delimiter in yaml:
                    delimiter = '$cdeadmin_xtdb_' + hashlib.sha256(
                        yaml.encode('utf-8')
                    ).hexdigest()[:16] + '$'
                return (
                    f'ATTACH DATABASE {name} WITH '
                    f'{delimiter}{yaml}{delimiter}',
                    (),
                )
            if operation == 'drop':
                name = native.get('database')
                if not name or name == 'xtdb':
                    raise XTDBClientError(
                        'the primary xtdb database cannot be detached'
                    )
                if route['database'] != 'xtdb':
                    raise XTDBClientError(
                        'databases must be detached through primary xtdb'
                    )
                return f'DETACH DATABASE {_quote_identifier(name)}', ()
        if kind == 'user' and operation in {'create', 'alter'}:
            username = (
                draft.get('username') if operation == 'create'
                else native.get('username')
            )
            password = self._acquire_admin_password(
                draft['password_reference'], route
            )
            verb = 'CREATE' if operation == 'create' else 'ALTER'
            return (
                f'{verb} USER {_user_identifier(username)} WITH PASSWORD '
                f'{_sql_string(password)}',
                (),
            )
        if kind in {'table', 'document', 'entity'}:
            if kind == 'table' and operation == 'create':
                target = _qualified(draft['schema'], draft['name'])
                document = dict(draft['values'])
                options = draft.get('options') or {}
                if options.get('valid_from'):
                    document['_valid_from'] = options['valid_from']
                if options.get('valid_to'):
                    document['_valid_to'] = options['valid_to']
                columns = ', '.join(
                    _quote_identifier(key) for key in document
                )
                placeholders = ', '.join(['%s'] * len(document))
                return (
                    f'INSERT INTO {target} ({columns}) '
                    f'VALUES ({placeholders})',
                    tuple(self._adapt_value(value)
                          for value in document.values()),
                )
            schema = native.get('schema', 'public')
            table = native.get('table')
            if not table:
                raise XTDBClientError('XTDB document target is invalid')
            target = _qualified(schema, table)
            if operation == 'inspect':
                temporal, parameters = self._temporal_read(draft)
                limit = int(draft.get('limit', 200))
                _integer(limit, 'limit', 1, MAX_PAGE_SIZE)
                return (
                    f'SELECT * FROM {target}{temporal} LIMIT %s',
                    (*parameters, limit),
                )
            if operation == 'insert':
                document = dict(draft['values'])
                options = draft.get('options') or {}
                if options.get('valid_from'):
                    document['_valid_from'] = options['valid_from']
                if options.get('valid_to'):
                    document['_valid_to'] = options['valid_to']
                columns = ', '.join(
                    _quote_identifier(key) for key in document
                )
                placeholders = ', '.join(['%s'] * len(document))
                return (
                    f'INSERT INTO {target} ({columns}) '
                    f'VALUES ({placeholders})',
                    tuple(self._adapt_value(value)
                          for value in document.values()),
                )
            identity = self._resolve_row_identity(
                draft.get('row_identity') or draft.get('concurrency_token'),
                route, native, consume=True
            )
            if operation == 'erase':
                return f'ERASE FROM {target} WHERE _id = %s', (
                    self._adapt_value(identity.document_id),
                )
            temporal, temporal_parameters = self._temporal_mutation(draft)
            if operation == 'delete':
                predicate = '_id = %s'
                predicate_parameters = [
                    self._adapt_value(identity.document_id)
                ]
                if (
                    draft.get('options', {}).get(
                        'valid_time_mode', 'current'
                    ) == 'current' and identity.system_from is not None
                ):
                    predicate += ' AND _system_from = %s'
                    predicate_parameters.append(identity.system_from)
                return (
                    f'DELETE FROM {target}{temporal} WHERE {predicate}',
                    (*temporal_parameters, *predicate_parameters),
                )
            if operation == 'update':
                changes = draft['changes']
                assignments = ', '.join(
                    f'{_quote_identifier(key)} = %s' for key in changes
                )
                values = tuple(
                    self._adapt_value(value) for value in changes.values()
                )
                predicate = '_id = %s'
                predicate_parameters = [
                    self._adapt_value(identity.document_id)
                ]
                if (
                    draft.get('options', {}).get(
                        'valid_time_mode', 'current'
                    ) == 'current' and identity.system_from is not None
                ):
                    predicate += ' AND _system_from = %s'
                    predicate_parameters.append(identity.system_from)
                return (
                    f'UPDATE {target}{temporal} SET {assignments} '
                    f'WHERE {predicate}',
                    (*temporal_parameters, *values,
                     *predicate_parameters),
                )
        if kind in {'valid-time', 'system-time'} and operation == 'inspect':
            schema = native.get('schema', 'public')
            table = native.get('table')
            if not table:
                raise XTDBClientError('XTDB history target is invalid')
            options = dict(draft)
            if kind == 'valid-time':
                options.setdefault('valid_time_mode', 'all')
            else:
                options.setdefault('system_time_mode', 'all')
            temporal, parameters = self._temporal_read(options)
            limit = int(options.get('limit', 200))
            _integer(limit, 'limit', 1, MAX_PAGE_SIZE)
            return (
                f'SELECT * FROM {_qualified(schema, table)}{temporal} '
                'LIMIT %s',
                (*parameters, limit),
            )
        if kind in {'transaction', 'transaction-log'} and (
            operation == 'inspect'
        ):
            return (
                'SELECT * FROM xt.txs FOR ALL VALID_TIME '
                'ORDER BY _id DESC LIMIT %s',
                (MAX_PAGE_SIZE,),
            )
        if kind == 'user' and operation == 'inspect':
            username = native.get('username')
            if username:
                return (
                    'SELECT username, usesuper FROM pg_catalog.pg_user '
                    'WHERE username = %s',
                    (username,),
                )
            return (
                'SELECT username, usesuper FROM pg_catalog.pg_user '
                'ORDER BY username',
                (),
            )
        if kind == 'database' and operation == 'inspect':
            return (
                'SELECT oid, datname, datallowconn, datistemplate '
                'FROM pg_catalog.pg_database',
                (),
            )
        if operation == 'inspect':
            return 'SELECT xt.version() AS xtdb_version', ()
        raise XTDBClientError('XTDB administration operation is unavailable')

    def _acquire_admin_password(self, reference, route):
        principal = route.get('principal_reference')
        if not principal or not callable(self.secret_acquirer):
            raise XTDBClientError(
                'XTDB password secret binding is unavailable'
            )
        lease = self.secret_acquirer(
            reference, principal, 'administer', 'new_database_password'
        )
        with lease:
            return lease.use(lambda view: bytes(view).decode('utf-8'))

    def _adapt_value(self, value):
        if isinstance(value, (Mapping, list)):
            types = getattr(self.module, 'types', None)
            json_types = getattr(types, 'json', None)
            jsonb = getattr(json_types, 'Jsonb', None)
            if callable(jsonb):
                return jsonb(value)
        return value

    @staticmethod
    def _temporal_mutation(draft):
        options = draft.get('options') or {}
        mode = options.get('valid_time_mode', 'current')
        if mode == 'current':
            return '', ()
        if mode == 'all':
            return ' FOR ALL VALID_TIME', ()
        if mode == 'portion':
            return ' FOR PORTION OF VALID_TIME FROM %s TO %s', (
                options['valid_from'], options['valid_to'],
            )
        raise XTDBClientError('valid-time mode is invalid')

    @staticmethod
    def _temporal_read(draft):
        clauses = []
        parameters = []
        for field, axis in (
            ('valid', 'VALID_TIME'), ('system', 'SYSTEM_TIME')
        ):
            mode = draft.get(f'{field}_time_mode', 'current')
            if mode == 'all':
                clauses.append(f' FOR ALL {axis}')
            elif mode == 'as_of':
                value = draft.get(f'{field}_time')
                if not value:
                    raise XTDBClientError(
                        f'{field}-time as-of requires an instant'
                    )
                clauses.append(f' FOR {axis} AS OF %s')
                parameters.append(value)
            elif mode != 'current':
                raise XTDBClientError(f'{field}-time mode is invalid')
        return ''.join(clauses), tuple(parameters)

    @staticmethod
    def _native_target(target):
        if not isinstance(target, Mapping):
            return {}
        extensions = target.get('extensions')
        if isinstance(extensions, Mapping):
            extension = extensions.get('xtdb')
            if isinstance(extension, Mapping):
                native = extension.get('native')
                if isinstance(native, Mapping):
                    return copy.deepcopy(dict(native))
        native = target.get('native')
        if isinstance(native, Mapping):
            return copy.deepcopy(dict(native))
        return {
            key: copy.deepcopy(target[key]) for key in (
                'database', 'schema', 'table', 'column', 'username'
            ) if key in target
        }

    @staticmethod
    def _safe_target(native):
        return {
            key: value for key, value in native.items()
            if key not in {'password', 'secret', 'token'}
        }

    @staticmethod
    def _route_fingerprint(route):
        safe = {
            key: route.get(key) for key in (
                'host', 'port', 'database', 'username', 'tls_mode', 'route_id'
            )
        }
        return hashlib.sha256(json.dumps(
            safe, sort_keys=True, default=str
        ).encode('utf-8')).hexdigest()

    def _issue_row_identity(self, route, native, row):
        document_id = row.get('_id')
        if document_id is None:
            return None
        token = str(uuid.uuid4())
        digest = hashlib.sha256(json.dumps(
            row, sort_keys=True, default=str
        ).encode('utf-8')).hexdigest()
        identity = _RowIdentity(
            self._route_fingerprint(route), route['database'],
            native.get('schema', 'public'), native['table'], document_id,
            row.get('_system_from'), digest,
            self._clock() + ROW_IDENTITY_TTL_SECONDS,
        )
        with self._lock:
            self._row_identities[token] = identity
        return token

    def _resolve_row_identity(self, token, route, native, consume=False):
        with self._lock:
            identity = self._row_identities.get(token)
            if consume and identity is not None:
                self._row_identities.pop(token, None)
        if identity is None or identity.expires_at < self._clock():
            raise XTDBClientError('XTDB row identity is absent or stale')
        expected = (
            self._route_fingerprint(route), route['database'],
            native.get('schema', 'public'), native.get('table'),
        )
        observed = (
            identity.route_fingerprint, identity.database,
            identity.schema, identity.table,
        )
        if observed != expected:
            raise XTDBClientError(
                'XTDB row identity belongs to another endpoint or table'
            )
        return identity

    def read_admin_rows(self, request):
        route = self._route({'route': request.get('_provider_route')})
        native = self._native_target(request.get('target_resource'))
        table = native.get('table')
        schema = native.get('schema', 'public')
        if not table:
            raise XTDBClientError('XTDB editable document target is invalid')
        limit = request.get('limit', 200)
        if not isinstance(limit, int):
            raise XTDBClientError('XTDB page limit must be an integer')
        limit = _integer(limit, 'page limit', 1, MAX_PAGE_SIZE)
        continuation = request.get('continuation')
        offset = 0
        if continuation:
            with self._lock:
                cursor_state = self._admin_cursors.pop(continuation, None)
            if cursor_state is None or cursor_state.expires_at < self._clock():
                raise XTDBClientError('XTDB page continuation is stale')
            if (
                cursor_state.route_fingerprint != self._route_fingerprint(
                    route
                )
                or cursor_state.database != route['database']
                or cursor_state.schema != schema
                or cursor_state.table != table
            ):
                raise XTDBClientError(
                    'XTDB page continuation belongs to another target'
                )
            offset = cursor_state.offset
        filters = request.get('filter') or {}
        temporal, parameters = self._temporal_read(filters)
        target = _qualified(schema, table)
        source = (
            f'SELECT *, _valid_from AS __cde_valid_from, '
            f'_valid_to AS __cde_valid_to, '
            f'_system_from AS __cde_system_from, '
            f'_system_to AS __cde_system_to FROM {target}{temporal} '
            'ORDER BY _id LIMIT %s OFFSET %s'
        )
        session = self._connect({'route': route})
        try:
            rows = self._query_rows(
                session, source, (*parameters, limit + 1, offset)
            )
        finally:
            self._forget_and_close(session)
        has_more = len(rows) > limit
        rows = rows[:limit]
        records = []
        for row in rows:
            row['_valid_from'] = row.pop(
                '__cde_valid_from', row.get('_valid_from')
            )
            row['_valid_to'] = row.pop(
                '__cde_valid_to', row.get('_valid_to')
            )
            row['_system_from'] = row.pop(
                '__cde_system_from', row.get('_system_from')
            )
            row['_system_to'] = row.pop(
                '__cde_system_to', row.get('_system_to')
            )
            identity = self._issue_row_identity(route, native, row)
            records.append({
                'identity_token': identity,
                'values': self._json_value(row),
            })
        next_token = None
        if has_more:
            next_token = str(uuid.uuid4())
            with self._lock:
                self._admin_cursors[next_token] = _AdminCursor(
                    self._route_fingerprint(route), route['database'],
                    schema, table, offset + limit,
                    self._clock() + ROW_IDENTITY_TTL_SECONDS,
                )
        column_names = []
        for record in records:
            for name in record['values']:
                if name not in column_names:
                    column_names.append(name)
        columns = [{
            'name': name, 'key': name == '_id',
            'editable': not name.startswith('_') or name == '_id',
        } for name in column_names]
        return {
            'schema': 'cdeadmin.visual-admin.row-page.v1',
            'engine_id': 'xtdb', 'resource_kind': 'document',
            'columns': columns, 'rows': records,
            'editable': bool(records) and all(
                row['identity_token'] for row in records
            ),
            'continuation': next_token,
            'complete': not has_more,
            'identity_kind': 'provider-issued-bitemporal-document-identity',
            'transaction_finality_interpreted_by_common_code': False,
        }

    def cancel_admin_cursor(self, request):
        token = request.get('continuation')
        with self._lock:
            existed = self._admin_cursors.pop(token, None) is not None
        return {'cancelled': existed, 'continuation': token}

    def close(self):
        for session in tuple(self._connections):
            self._forget_and_close(session)
        self._results.clear()
        with self._lock:
            self._row_identities.clear()
            self._admin_cursors.clear()

    def _forget_and_close(self, session):
        try:
            self._connections.remove(session)
        except ValueError:
            pass
        self._safe_close(session)

    @staticmethod
    def _safe_close(value):
        if value is None:
            return
        close = getattr(value, 'close', None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
