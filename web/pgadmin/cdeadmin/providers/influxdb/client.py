##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""InfluxDB 3.9 HTTP API, query, discovery, and visual administration."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from dataclasses import dataclass
from typing import Any, Mapping

from pgadmin.cdeadmin.transports.analytics_http import (
    AnalyticHTTPError,
    AnalyticHTTPUnknownOutcomeError,
    BoundedJSONHTTPTransport,
    bounded_integer,
    normalize_http_route,
    required_text,
)


REFERENCE_VERSION = '3.9.0'
MAX_RECORDS = 10000
MAX_PAGE_SIZE = 1000
_NAME = re.compile(r'^[^\x00-\x1f\x7f]{1,255}$')
_DURATION = re.compile(
    r'^(?:0|[1-9][0-9]*(?:ns|us|ms|s|m|h|d|w|month|year))$'
)


class InfluxDBClientError(AnalyticHTTPError):
    """InfluxDB request or native contract validation failed."""


class InfluxDBUnknownOutcomeError(InfluxDBClientError):
    """An InfluxDB mutation requires target-state observation."""

    outcome = 'unknown_requires_observation'
    retryable = False


@dataclass
class _Session:
    route: dict[str, Any]
    last_observation: str = 'no-request-observed'
    closed: bool = False

    def close(self):
        self.closed = True


@dataclass
class _Result:
    points: list[Any]
    columns: list[dict[str, Any]]
    language: str
    request_kind: str
    complete: bool = True


def _name(value, label='name'):
    value = required_text(value, label, 255)
    if not _NAME.fullmatch(value):
        raise InfluxDBClientError(f'{label} is invalid')
    return value


def _mapping(value, label='request'):
    if not isinstance(value, Mapping):
        raise InfluxDBClientError(f'{label} must be an object')
    return copy.deepcopy(dict(value))


def _duration(value, label='duration'):
    value = required_text(value, label, 128)
    if not _DURATION.fullmatch(value):
        raise InfluxDBClientError(
            f'{label} must be a positive InfluxDB duration'
        )
    return value


def _string_list(value, label, required=False):
    if not isinstance(value, list) or (required and not value):
        raise InfluxDBClientError(f'{label} must be an array')
    result = [_name(item, f'{label} item') for item in value]
    if len(result) != len(set(result)):
        raise InfluxDBClientError(f'{label} must not contain duplicates')
    return result


def _string_map(value, label):
    value = _mapping(value, label)
    result = {}
    for key, item in value.items():
        result[_name(key, f'{label} key')] = required_text(
            item, f'{label} value', 4096
        )
    return result


def _sql_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def _code_text(value, label, maximum):
    if not isinstance(value, str) or not value.strip():
        raise InfluxDBClientError(f'{label} must not be empty')
    value = value.strip()
    if len(value.encode('utf-8')) > maximum or any(
        ord(character) < 32 and character not in '\t\n\r'
        for character in value
    ):
        raise InfluxDBClientError(f'{label} is invalid')
    return value


class InfluxDBClient:
    """Provider-owned adapter for InfluxDB 3 Core/Enterprise HTTP APIs."""

    ADMIN_OPERATIONS = {
        'cluster': frozenset({'inspect'}),
        'node': frozenset({'inspect'}),
        'database': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'table': frozenset({'inspect', 'create', 'insert', 'drop'}),
        'column': frozenset({'inspect'}),
        'tag': frozenset({'inspect'}),
        'field': frozenset({'inspect'}),
        'retention-policy': frozenset({'inspect', 'alter'}),
        'last-cache': frozenset({'inspect', 'create', 'drop'}),
        'distinct-cache': frozenset({'inspect', 'create', 'drop'}),
        'token': frozenset({'inspect', 'drop'}),
        'trigger': frozenset({'inspect', 'execute'}),
        'plugin': frozenset({'inspect', 'execute'}),
        'processing-engine': frozenset({'inspect', 'execute'}),
        'compaction': frozenset({'inspect'}),
    }

    def __init__(self, secret_acquirer=None, urlopen=None):
        self.transport = BoundedJSONHTTPTransport(
            secret_acquirer, urlopen=urlopen
        )
        self._sessions = []
        self._lock = threading.RLock()

    @staticmethod
    def _route(request):
        try:
            route = normalize_http_route(
                request, default_port=8181, default_auth='none',
                extra_fields=('database', 'query_language'),
            )
        except AnalyticHTTPError as exc:
            raise InfluxDBClientError(str(exc)) from exc
        route['database'] = _name(route.get('database', 'default'), 'database')
        language = route.get('query_language', 'sql').casefold()
        if language not in {'sql', 'influxql'}:
            raise InfluxDBClientError('query language is invalid')
        route['query_language'] = language
        return route

    def _request(self, route, path, **options):
        try:
            return self.transport.request(route, path, **options)
        except InfluxDBClientError:
            raise
        except AnalyticHTTPUnknownOutcomeError as exc:
            raise InfluxDBUnknownOutcomeError(str(exc)) from exc
        except AnalyticHTTPError as exc:
            raise InfluxDBClientError(
                str(exc), status=exc.status,
                native_payload=exc.native_payload,
            ) from exc

    def runtime_identity(self, request, handle=None):
        route = handle.route if isinstance(handle, _Session) else self._route(
            request
        )
        document = self._request(route, '/ping').json()
        if not isinstance(document, Mapping):
            raise InfluxDBClientError('InfluxDB ping identity is invalid')
        version = str(document.get('version', '')).removeprefix('v')
        if version != REFERENCE_VERSION:
            raise InfluxDBClientError(
                'runtime did not prove exact InfluxDB 3.9.0 identity'
            )
        revision = required_text(
            str(document.get('revision') or 'unknown'), 'revision'
        )
        return {
            'engine_id': 'influxdb', 'version': version,
            'build_id': f'InfluxDB {version} revision {revision}',
            'protocol_id': 'http_json',
        }

    def open_session(self, request):
        session = _Session(self._route(request))
        with self._lock:
            self._sessions.append(session)
        return session

    @staticmethod
    def describe_transaction(handle):
        if not isinstance(handle, _Session) or handle.closed:
            raise InfluxDBClientError('InfluxDB session is unavailable')
        return {
            'native_observation': handle.last_observation,
            'request_atomicity_only': True,
            'multi_request_transaction_supported': False,
            'automatic_replay': False,
            'finality_interpreted_by_common_code': False,
        }

    def _query(self, route, source, language=None, parameters=None):
        source = required_text(source, 'InfluxDB query', 2 * 1024 * 1024)
        language = self._language(language, route['query_language'])
        if language not in {'sql', 'influxql'}:
            raise InfluxDBClientError('query language is invalid')
        if parameters is not None and not isinstance(parameters, Mapping):
            raise InfluxDBClientError('query parameters must be an object')
        body = {
            'db': route['database'], 'q': source, 'format': 'json',
            'params': copy.deepcopy(parameters) if parameters else None,
        }
        document = self._request(
            route, f'/api/v3/query_{language}', method='POST',
            json_body=body,
        ).json()
        if document is None:
            rows = []
        elif isinstance(document, list):
            rows = copy.deepcopy(document)
        elif isinstance(document, Mapping):
            candidate = document.get('data', document.get('results'))
            rows = candidate if isinstance(candidate, list) else [document]
        else:
            raise InfluxDBClientError('InfluxDB query response is invalid')
        if len(rows) > MAX_RECORDS:
            rows = rows[:MAX_RECORDS]
        columns = []
        if rows and isinstance(rows[0], Mapping):
            columns = [
                {'name': str(name), 'type': type(value).__name__}
                for name, value in rows[0].items()
            ]
        return _Result(rows, columns, language, 'query')

    @staticmethod
    def _language(value, default):
        if not value:
            return default
        value = str(value).casefold()
        if value in {'sql', 'influxql'}:
            return value
        if 'influxql' in value:
            return 'influxql'
        if 'sql' in value:
            return default
        raise InfluxDBClientError('query language is invalid')

    def execute(self, handle, request):
        if not isinstance(handle, _Session) or handle.closed:
            raise InfluxDBClientError('InfluxDB session is unavailable')
        result = self._query(
            handle.route, request.get('source'),
            request.get('language') or request.get('language_profile'),
            request.get('parameters'),
        )
        handle.last_observation = 'native-query-response-observed'
        return result

    @staticmethod
    def cancel(_token):
        return False

    @staticmethod
    def describe_result(token):
        if not isinstance(token, _Result):
            raise InfluxDBClientError('InfluxDB result token is invalid')
        return {
            'result_kind': 'time_series',
            'schema': {
                'columns': copy.deepcopy(token.columns),
                'time_field': 'time',
                'language': token.language,
            },
            'complete': token.complete,
            'stream_reference': None,
            'payload': {
                'points': copy.deepcopy(token.points),
                'language': token.language,
                'request_kind': token.request_kind,
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
            'resource_id': 'influxdb:' + ':'.join(map(str, path)),
            'resource_kind': kind, 'display_name': str(name),
            'authority_path': ['influxdb', *map(str, path)],
            'display_path': list(map(str, path)),
            'generation': self._generation(native),
            'native': copy.deepcopy(native),
        }

    def _query_rows(self, route, source, database=None):
        selected = route
        if database is not None:
            selected = copy.deepcopy(route)
            selected['database'] = database
        return self._query(selected, source, 'sql').points

    @staticmethod
    def _quote_identifier(value):
        return '"' + _name(value, 'table').replace('"', '""') + '"'

    def list_resources(self, request):
        route = self._route(request)
        ping = self._request(route, '/ping').json()
        resources = [
            self._resource('cluster', route['host'], ping,
                           ['cluster', route['host']]),
            self._resource('processing-engine', 'processing-engine', {
                'plugin_endpoint': '/api/v3/plugins/files',
                'trigger_endpoint': (
                    '/api/v3/configure/processing_engine_trigger'
                ),
            }),
            self._resource('compaction', 'compaction', {
                'engine_owned': True
            }),
        ]
        nodes = self._query_rows(
            route, 'SELECT * FROM system.nodes', '_internal'
        )
        for item in nodes:
            name = item.get('node_id')
            if name:
                resources.append(self._resource(
                    'node', name, item, ['node', name]
                ))
        databases = self._query_rows(
            route,
            'SELECT database_name, retention_period_ns FROM '
            'system.databases WHERE deleted = false',
            '_internal',
        )
        database_names = []
        for item in databases:
            name = item.get('database_name')
            if not name:
                continue
            database_names.append(name)
            native = {**item, 'name': name, 'database': name}
            resources.append(self._resource(
                'database', name, native, ['database', name]
            ))
            retention = {
                'name': 'retention', 'database': name,
                'retention_period_ns': item.get('retention_period_ns'),
            }
            resources.append(self._resource(
                'retention-policy', 'retention', retention,
                ['database', name, 'retention-policy', 'retention'],
            ))
        tables = self._query_rows(
            route,
            'SELECT database_name, table_name, column_count, '
            'series_key_columns, last_cache_count, distinct_cache_count '
            'FROM system.tables WHERE deleted = false',
            '_internal',
        )
        for item in tables:
            database = item.get('database_name')
            table = item.get('table_name')
            if not database or not table:
                continue
            native = {**item, 'name': table, 'database': database}
            resources.append(self._resource(
                'table', table, native, ['database', database, 'table', table]
            ))
        for database in database_names:
            try:
                rows = self._query_rows(
                    route,
                    'SELECT table_schema, table_name, column_name, data_type '
                    'FROM information_schema.columns',
                    database,
                )
            except InfluxDBClientError:
                continue
            for row in rows:
                if not isinstance(row, Mapping) or (
                    row.get('table_schema') != 'iox'
                ):
                    continue
                table = row.get('table_name')
                name = row.get('column_name')
                native = {
                    **row, 'name': name, 'database': database,
                    'table': table,
                }
                resources.append(self._resource(
                    'column', name, native,
                    ['database', database, 'table', table, 'column', name],
                ))
                data_type = str(row.get('data_type', '')).casefold()
                if name != 'time':
                    derived = (
                        'tag' if data_type.startswith('dictionary(')
                        else 'field'
                    )
                    resources.append(self._resource(
                        derived, name, native,
                        ['database', database, 'table', table, derived, name],
                    ))
        system_queries = []
        for database in database_names:
            system_queries.extend((
                ('last-cache', 'SELECT * FROM system.last_caches', 'name',
                 database),
                ('distinct-cache', 'SELECT * FROM system.distinct_caches',
                 'name', database),
                ('trigger',
                 'SELECT * FROM system.processing_engine_triggers',
                 'trigger_name', database),
            ))
        system_queries.extend((
            ('plugin', 'SELECT * FROM system.plugin_files', 'plugin_name',
             '_internal'),
            ('token', 'SELECT * FROM system.tokens', 'name', '_internal'),
        ))
        for kind, source, name_field, database in system_queries:
            try:
                rows = self._query_rows(route, source, database)
            except InfluxDBClientError:
                continue
            for row in rows:
                if not isinstance(row, Mapping) or not row.get(name_field):
                    continue
                native = copy.deepcopy(dict(row))
                if kind == 'token':
                    native.pop('hash', None)
                    native['token_material_exposed'] = False
                name = native[name_field]
                table = native.get('table')
                native.update({
                    'name': name, 'database': database,
                })
                resources.append(self._resource(
                    kind, name, native,
                    [kind, database, table, name]
                    if table else [kind, name],
                ))
        return resources

    def inspect_resource(self, request):
        native = self._native_target(request)
        kind = request.get('resource_kind') or native.get('resource_kind')
        name = (native.get('name') or native.get('db') or
                native.get('table_name') or native.get('column_name') or kind)
        if not kind or not name:
            raise InfluxDBClientError('InfluxDB resource identity is absent')
        return self._resource(kind, name, native)

    def describe_security(self, request):
        route = self._route(request)
        native = {
            'authorization_model': 'influxdb3-bearer-token',
            'authentication_enabled': route['auth_kind'] != 'none',
            'token_material_exposed': False,
            'credential_reference_bound': bool(
                route.get('credential_reference_id')
            ),
        }
        return {
            'resource_id': 'influxdb:security:tokens',
            'display_name': 'InfluxDB token security',
            'authority_path': ['influxdb', 'security', 'tokens'],
            'generation': self._generation(native), 'native': native,
        }

    def supports_admin_operation(self, resource_kind, operation_id):
        return operation_id in self.ADMIN_OPERATIONS.get(
            resource_kind, frozenset()
        )

    @staticmethod
    def _field(field_id, label, control='text', required=False, **values):
        return {'field_id': field_id, 'label': label, 'control': control,
                'required': required, **values}

    @classmethod
    def _form(cls, kind, operation):
        f = cls._field
        if operation == 'inspect':
            return {'form_id': 'influxdb-inspect', 'title': 'Inspect',
                    'fields': []}
        if kind == 'database' and operation in {'create', 'alter'}:
            return {'form_id': f'influxdb-database-{operation}',
                    'title': f'{operation.title()} database', 'fields': [
                        f('name', 'Database name',
                          required=operation == 'create'),
                        f('retention_period', 'Retention period',
                          required=False,
                          help='InfluxDB duration, for example 30d'),
            ]}
        if kind == 'table' and operation == 'create':
            return {'form_id': 'influxdb-table-create',
                    'title': 'Create table', 'fields': [
                        f('database', 'Database', required=True),
                        f('name', 'Table name', required=True),
                        f('tags', 'Tag columns', 'json', False, default=[]),
                        f('fields', 'Field columns', 'json', True, default=[
                            {'name': 'value', 'type': 'float64'}
                        ]),
                    ]}
        if kind == 'table' and operation == 'insert':
            return {'form_id': 'influxdb-line-protocol-write',
                    'title': 'Write line protocol', 'fields': [
                        f('line_protocol', 'Line protocol', 'code', True),
                        f('precision', 'Timestamp precision', 'select', True,
                          default='nanosecond', options=[
                              {'value': value, 'label': value}
                              for value in ('nanosecond', 'microsecond',
                                            'millisecond', 'second')
                          ]),
                        f('accept_partial', 'Accept partial writes',
                          'boolean', True, default=False),
                    ]}
        if kind == 'retention-policy' and operation == 'alter':
            return {
                'form_id': 'influxdb-retention-alter',
                'title': 'Set database retention',
                'fields': [
                    f('retention_period', 'Retention period', required=False,
                      help='For example 30d; leave empty to retain forever'),
                    f('retain_forever', 'Clear retention period', 'boolean',
                      True, default=False),
                ],
            }
        if kind in {'last-cache', 'distinct-cache'} and operation == 'create':
            fields = [
                f('database', 'Database', required=True),
                f('table', 'Table', required=True),
                f('name', 'Cache name'),
            ]
            if kind == 'last-cache':
                fields.extend([
                    f('key_columns', 'Key columns', 'json', True, default=[]),
                    f('value_columns', 'Value columns', 'json', False),
                    f('count', 'Cached values per key', 'number', True,
                      default=1),
                    f('ttl', 'Time to live (seconds)', 'number', True,
                      default=14400),
                ])
            else:
                fields.extend([
                    f('columns', 'Columns', 'json', True, default=[]),
                    f('max_cardinality', 'Maximum cardinality', 'number',
                      True, default=100000),
                    f('max_age_seconds', 'Maximum age (seconds)', 'number',
                      True, default=3600),
                ])
            return {'form_id': f'influxdb-{kind}-create',
                    'title': f'Create {kind}', 'fields': fields}
        if kind == 'trigger' and operation == 'execute':
            return {
                'form_id': 'influxdb-trigger-execute',
                'title': 'Manage processing trigger',
                'fields': [
                    f('action', 'Action', 'select', True, default='create',
                      options=[{'value': value, 'label': value.title()}
                               for value in (
                                   'create', 'delete', 'enable', 'disable'
                               )]),
                    f('database', 'Database'),
                    f('trigger_name', 'Trigger name'),
                    f('plugin_filename', 'Plugin filename'),
                    f('trigger_kind', 'Trigger kind', 'select', False,
                      default='table', options=[
                          {'value': 'table', 'label': 'Table writes'},
                          {'value': 'all_tables', 'label': 'All writes'},
                          {'value': 'cron', 'label': 'Cron schedule'},
                          {'value': 'every', 'label': 'Fixed interval'},
                          {'value': 'request', 'label': 'HTTP request path'},
                      ]),
                    f('trigger_value', 'Table, schedule, interval, or path'),
                    f('arguments', 'Trigger arguments', 'json', False,
                      default={}),
                    f('run_async', 'Run asynchronously', 'boolean', True,
                      default=False),
                    f('error_behavior', 'On error', 'select', True,
                      default='log', options=[
                          {'value': 'log', 'label': 'Log'},
                          {'value': 'retry', 'label': 'Retry'},
                          {'value': 'disable', 'label': 'Disable trigger'},
                      ]),
                    f('disabled', 'Create disabled', 'boolean', True,
                      default=False),
                    f('force', 'Force deletion', 'boolean', True,
                      default=False),
                    f('acknowledge_operation', 'Confirm action', 'boolean',
                      True, default=False),
                ],
            }
        if kind == 'plugin' and operation == 'execute':
            return {
                'form_id': 'influxdb-plugin-execute',
                'title': 'Manage processing plugin',
                'fields': [
                    f('action', 'Action', 'select', True,
                      default='upload-file', options=[
                          {'value': 'upload-file', 'label': 'Upload file'},
                          {'value': 'update-file', 'label': 'Update file'},
                          {'value': 'replace-directory',
                           'label': 'Replace directory'},
                      ]),
                    f('database', 'Database'),
                    f(
                        'plugin_name', 'Plugin name or filename',
                        help=(
                            'Use a filename when uploading; use the trigger '
                            'name when updating an installed plugin.'
                        ),
                    ),
                    f('content', 'Python source', 'code'),
                    f('files', 'Directory files', 'json', False, default=[]),
                    f('acknowledge_operation', 'Confirm action', 'boolean',
                      True, default=False),
                ],
            }
        if kind == 'processing-engine' and operation == 'execute':
            return {
                'form_id': 'influxdb-processing-engine-execute',
                'title': 'Run processing-engine operation',
                'fields': [
                    f('action', 'Action', 'select', True,
                      default='test-wal', options=[
                          {'value': 'test-wal', 'label': 'Test WAL plugin'},
                          {'value': 'test-schedule',
                           'label': 'Test scheduled plugin'},
                          {'value': 'install-packages',
                           'label': 'Install packages'},
                          {'value': 'install-requirements',
                           'label': 'Install requirements'},
                      ]),
                    f('database', 'Database'),
                    f('plugin_filename', 'Plugin filename'),
                    f('input_line_protocol', 'Test line protocol', 'code'),
                    f('schedule', 'Optional schedule'),
                    f('cache_name', 'Optional cache name'),
                    f('arguments', 'Plugin arguments', 'json', False,
                      default={}),
                    f('packages', 'Package names', 'json', False, default=[]),
                    f('requirements_location', 'Requirements file location'),
                    f('acknowledge_operation', 'Confirm action', 'boolean',
                      True, default=False),
                ],
            }
        if operation == 'drop':
            fields = [
                f('acknowledge_drop', 'Confirm drop', 'boolean', True,
                  default=False),
            ]
            if kind in {'database', 'table'}:
                fields.extend([
                    f('hard_delete_mode', 'Hard deletion', 'select', True,
                      default='default', options=[
                          {'value': 'default', 'label': 'Server default'},
                          {'value': 'now', 'label': 'Immediately'},
                          {'value': 'never', 'label': 'Never'},
                          {'value': 'timestamp',
                           'label': 'At RFC 3339 timestamp'},
                      ]),
                    f('hard_delete_at', 'Hard-delete timestamp'),
                ])
            return {
                'form_id': f'influxdb-{kind}-drop',
                'title': f'Drop {kind}', 'fields': fields,
            }
        return {'form_id': f'influxdb-{kind}-{operation}',
                'title': operation.title(), 'fields': []}

    def visual_admin_catalog(self, catalog):
        catalog['native_planner'] = 'influxdb3-http-api-planner'
        catalog['query_languages'] = ['sql', 'influxql']
        catalog['transaction_authority'] = 'influxdb-request-outcome'
        catalog['common_finality_interpretation'] = False
        catalog['experience_families'] = ['time_series', 'semantic']

        def declaration(resource_kinds, operations, reason, evidence):
            return {
                'status': 'supported', 'resource_kinds': resource_kinds,
                'operation_obligations': {
                    kind: sorted(self.ADMIN_OPERATIONS[kind])
                    for kind in operations
                },
                'reason': reason, 'evidence': [evidence],
            }

        catalog['concept_declarations'] = {
            'time_series': {
                'measurements_or_tables': declaration(
                    ['table'], ('table',),
                    'InfluxDB native tables are discovered from the system '
                    'catalog and use typed schema and line-protocol editors.',
                    'influxdb-3.9-native-tables',
                ),
                'tags': declaration(
                    ['tag'], ('tag',),
                    'Series-key tag columns are classified from exact Arrow '
                    'dictionary types and exposed as immutable metadata.',
                    'influxdb-3.9-tags',
                ),
                'fields': declaration(
                    ['field'], ('field',),
                    'Native field columns and their exact Arrow types are '
                    'individually navigable and inspectable.',
                    'influxdb-3.9-fields',
                ),
                'retention': declaration(
                    ['retention-policy'], ('retention-policy',),
                    'Database retention is a first-class child with typed set '
                    'and retain-forever actions.',
                    'influxdb-3.9-retention',
                ),
                'processing': declaration(
                    ['processing-engine', 'trigger', 'plugin'],
                    ('processing-engine', 'trigger', 'plugin'),
                    'Plugin files, registrations, trigger lifecycles and '
                    'bounded test/install actions are provider-owned.',
                    'influxdb-3.9-processing-engine',
                ),
                'caches': declaration(
                    ['last-cache', 'distinct-cache'],
                    ('last-cache', 'distinct-cache'),
                    'Last-value and distinct-value caches have native typed '
                    'configuration and lifecycle operations.',
                    'influxdb-3.9-caches',
                ),
            },
            'semantic': {
                concept: {
                    'status': 'supported', 'resource_kinds': [],
                    'operation_obligations': {},
                    'external_surface': 'cdeadmin.semantic-model-workspace.v1',
                    'reason': (
                        'The revisioned semantic workspace designs, '
                        'validates, compiles, executes and renders this '
                        'concept through InfluxDB 3 SQL.'
                    ),
                    'evidence': ['influxdb-3.9-semantic-workspace'],
                }
                for concept in (
                    'cubes', 'dimensions', 'hierarchies', 'levels',
                    'measures', 'materializations',
                )
            },
        }
        for resource in catalog.get('objects', []):
            kind = resource['resource_kind']
            resource['operations'] = [
                operation for operation in resource.get('operations', [])
                if self.supports_admin_operation(
                    kind, operation['operation_id']
                )
            ]
            for operation in resource['operations']:
                operation['form'] = self._form(
                    kind, operation['operation_id']
                )
                if operation['operation_id'] in {'drop', 'execute'}:
                    operation['confirmation_required'] = True
        return catalog

    def validate_admin_operation(self, request):
        errors = []
        try:
            if not self.supports_admin_operation(
                request.get('resource_kind'), request.get('operation_id')
            ):
                raise InfluxDBClientError('operation is unavailable')
            draft = _mapping(request.get('draft', {}), 'draft')
            kind = request.get('resource_kind')
            operation = request.get('operation_id')
            if request.get('operation_id') == 'drop' and not draft.get(
                'acknowledge_drop'
            ):
                raise InfluxDBClientError('drop acknowledgement is required')
            if kind in {'database', 'table'} and operation == 'drop':
                mode = draft.get('hard_delete_mode', 'default')
                if mode not in {'default', 'now', 'never', 'timestamp'}:
                    raise InfluxDBClientError(
                        'hard deletion mode is invalid'
                    )
                if mode == 'timestamp':
                    timestamp = required_text(
                        draft.get('hard_delete_at'),
                        'hard deletion timestamp', 64,
                    )
                    if not re.fullmatch(
                        r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z',
                        timestamp,
                    ):
                        raise InfluxDBClientError(
                            'hard deletion timestamp must be RFC 3339 UTC'
                        )
            if kind == 'database' and operation in {'create', 'alter'}:
                if operation == 'create':
                    _name(draft.get('name'), 'database')
                if draft.get('retention_period'):
                    _duration(draft['retention_period'], 'retention period')
            if kind == 'table' and operation == 'create':
                _name(draft.get('database'), 'database')
                _name(draft.get('name'), 'table')
                tags = _string_list(draft.get('tags', []), 'tags')
                if not isinstance(draft.get('fields'),
                                  list) or not draft['fields']:
                    raise InfluxDBClientError(
                        'fields must be a non-empty array')
                field_names = []
                for value in draft['fields']:
                    item = _mapping(value, 'field')
                    field_names.append(_name(item.get('name'), 'field'))
                    if str(item.get('type', '')).casefold() not in {
                        'utf8', 'int64', 'uint64', 'float64', 'bool'
                    }:
                        raise InfluxDBClientError('field type is invalid')
                if len(field_names) != len(set(field_names)):
                    raise InfluxDBClientError(
                        'field names must not contain duplicates'
                    )
                if set(tags).intersection(field_names):
                    raise InfluxDBClientError(
                        'tag and field names must not overlap'
                    )
            if kind == 'table' and operation == 'insert':
                _code_text(
                    draft.get('line_protocol'), 'line protocol',
                    2 * 1024 * 1024,
                )
                if draft.get('precision', 'nanosecond') not in {
                    'nanosecond', 'microsecond', 'millisecond', 'second'
                }:
                    raise InfluxDBClientError('precision is invalid')
            if kind == 'retention-policy' and operation == 'alter':
                if draft.get('retain_forever'):
                    if draft.get('retention_period'):
                        raise InfluxDBClientError(
                            'retain forever cannot include a retention period'
                        )
                else:
                    _duration(
                        draft.get('retention_period'), 'retention period'
                    )
            if kind in {
                'last-cache', 'distinct-cache'
            } and operation == 'create':
                field = ('key_columns' if request['resource_kind'] ==
                         'last-cache' else 'columns')
                _name(draft.get('database'), 'database')
                _name(draft.get('table'), 'table')
                _string_list(draft.get(field), field, True)
                if kind == 'last-cache':
                    if draft.get('value_columns') is not None:
                        _string_list(
                            draft.get('value_columns'), 'value_columns'
                        )
                    bounded_integer(
                        draft.get('count'), 'count', 1, 1, 10
                    )
                    bounded_integer(
                        draft.get('ttl'), 'ttl', 14400, 1, 315360000
                    )
                else:
                    bounded_integer(
                        draft.get('max_cardinality'),
                        'maximum cardinality', 100000, 1, 2**63 - 1,
                    )
                    bounded_integer(
                        draft.get('max_age_seconds'),
                        'maximum age', 3600, 1, 315360000,
                    )
            if kind == 'trigger' and operation == 'execute':
                self._validate_trigger_action(draft)
            if kind == 'plugin' and operation == 'execute':
                self._validate_plugin_action(draft)
            if kind == 'processing-engine' and operation == 'execute':
                self._validate_processing_action(draft)
        except (InfluxDBClientError, KeyError) as exc:
            errors.append({'field_id': None,
                           'code': 'influxdb_native_validation',
                           'message': str(exc)})
        return {'errors': errors, 'warnings': []}

    @staticmethod
    def _require_acknowledgement(draft):
        if not draft.get('acknowledge_operation'):
            raise InfluxDBClientError(
                'processing operation acknowledgement is required'
            )

    @classmethod
    def _validate_trigger_action(cls, draft):
        cls._require_acknowledgement(draft)
        action = draft.get('action')
        if action not in {'create', 'delete', 'enable', 'disable'}:
            raise InfluxDBClientError('trigger action is invalid')
        if action == 'create':
            _name(draft.get('database'), 'database')
            _name(draft.get('trigger_name'), 'trigger name')
            _name(draft.get('plugin_filename'), 'plugin filename')
            kind = draft.get('trigger_kind')
            if kind not in {
                'table', 'all_tables', 'cron', 'every', 'request'
            }:
                raise InfluxDBClientError('trigger kind is invalid')
            if kind != 'all_tables':
                required_text(
                    draft.get('trigger_value'), 'trigger value', 4096
                )
            _string_map(draft.get('arguments', {}), 'trigger arguments')
            if draft.get('error_behavior', 'log') not in {
                'log', 'retry', 'disable'
            }:
                raise InfluxDBClientError('trigger error behavior is invalid')
        else:
            _name(draft.get('database'), 'database')
            _name(draft.get('trigger_name'), 'trigger name')

    @classmethod
    def _validate_plugin_action(cls, draft):
        cls._require_acknowledgement(draft)
        action = draft.get('action')
        if action not in {
            'upload-file', 'update-file', 'replace-directory',
        }:
            raise InfluxDBClientError('plugin action is invalid')
        _name(draft.get('database'), 'database')
        _name(draft.get('plugin_name'), 'plugin name')
        if action in {'upload-file', 'update-file'}:
            _code_text(draft.get('content'), 'plugin source', 2 * 1024**2)
        elif action == 'replace-directory':
            files = draft.get('files')
            if not isinstance(files, list) or not files:
                raise InfluxDBClientError(
                    'plugin directory files must be a non-empty array'
                )
            for value in files:
                item = _mapping(value, 'plugin directory file')
                _name(item.get('relative_path'), 'relative path')
                _code_text(
                    item.get('content'), 'plugin source', 2 * 1024**2
                )
            paths = [item['relative_path'] for item in files]
            if len(paths) != len(set(paths)):
                raise InfluxDBClientError(
                    'plugin directory paths must not contain duplicates'
                )

    @classmethod
    def _validate_processing_action(cls, draft):
        cls._require_acknowledgement(draft)
        action = draft.get('action')
        if action not in {
            'test-wal', 'test-schedule', 'install-packages',
            'install-requirements',
        }:
            raise InfluxDBClientError('processing action is invalid')
        if action in {'test-wal', 'test-schedule'}:
            _name(draft.get('database'), 'database')
            _name(draft.get('plugin_filename'), 'plugin filename')
            _string_map(draft.get('arguments', {}), 'plugin arguments')
            if draft.get('cache_name'):
                _name(draft.get('cache_name'), 'cache name')
        if action == 'test-wal':
            _code_text(
                draft.get('input_line_protocol'), 'test line protocol',
                2 * 1024 * 1024,
            )
        elif action == 'install-packages':
            _string_list(draft.get('packages'), 'packages', True)
        elif action == 'install-requirements':
            required_text(
                draft.get('requirements_location'),
                'requirements location', 4096,
            )
        elif action == 'test-schedule' and draft.get('schedule'):
            required_text(draft.get('schedule'), 'schedule', 4096)

    def plan_admin_operation(self, request):
        validation = self.validate_admin_operation(request)
        if validation['errors']:
            raise InfluxDBClientError(validation['errors'][0]['message'])
        kind, operation = request['resource_kind'], request['operation_id']
        return {
            'command_preview': {
                'provider': 'influxdb', 'resource_kind': kind,
                'operation': operation,
                'target': self._safe_target(self._native_target(
                    request.get('target_resource')
                )),
                'native_api_request_generated_at_execution': True,
            },
            'warnings': [],
            'provider_payload': {
                'kind': kind, 'operation': operation,
                'target': self._native_target(request.get('target_resource')),
                'draft': copy.deepcopy(request.get('draft', {})),
                'route': copy.deepcopy(request.get('_provider_route')),
            },
            'receipt': {
                'provider': 'influxdb', 'automatic_retry': False,
                'transaction_finality_interpreted': False,
            },
        }

    def apply_admin_operation(self, request):
        payload = _mapping(request.get('provider_payload'), 'provider payload')
        route = self._route({'route': payload.get('route')})
        if route['read_only'] and payload.get('operation') != 'inspect':
            raise InfluxDBClientError('read-only route refused mutation')
        if payload.get('operation') == 'inspect':
            document = self._inspect_admin(route, payload)
            return {
                'native_response_observed': True,
                'native_response': document,
                'http_status': 200,
                'automatic_retry': False,
                'request_atomicity_only': True,
                'transaction_finality_interpreted_by_common_code': False,
            }
        response = self._apply_admin(route, payload)
        document = response.json()
        return {
            'native_response_observed': True,
            'native_response': document,
            'http_status': response.status,
            'automatic_retry': False,
            'request_atomicity_only': True,
            'transaction_finality_interpreted_by_common_code': False,
        }

    def _inspect_admin(self, route, payload):
        kind = payload['kind']
        target = payload.get('target', {})
        database = (
            target.get('db') or target.get('database') or
            target.get('database_name') or route['database']
        )
        name = (
            target.get('name') or target.get('table_name') or
            target.get('column_name') or target.get('trigger_name') or
            target.get('plugin_name')
        )
        if kind == 'cluster':
            return self._request(route, '/ping').json()
        if kind == 'node':
            source = 'SELECT * FROM system.nodes'
            if name:
                source += ' WHERE node_id = ' + _sql_string(name)
            return {'rows': self._query_rows(route, source, '_internal')}
        if kind in {'database', 'retention-policy'}:
            source = (
                'SELECT * FROM system.databases WHERE database_name = ' +
                _sql_string(database if kind == 'retention-policy' else name)
            )
            return {'rows': self._query_rows(route, source, '_internal')}
        if kind == 'table':
            source = (
                'SELECT * FROM system.tables WHERE database_name = ' +
                _sql_string(database) + ' AND table_name = ' +
                _sql_string(name)
            )
            return {'rows': self._query_rows(route, source, '_internal')}
        if kind in {'column', 'tag', 'field'}:
            source = (
                'SELECT * FROM information_schema.columns WHERE table_name = '
                + _sql_string(target.get('table')) +
                ' AND column_name = ' + _sql_string(name)
            )
            return {'rows': self._query_rows(route, source, database)}
        if kind in {'last-cache', 'distinct-cache'}:
            table = target.get('table')
            source = (
                'SELECT * FROM system.' + kind.replace('-', '_') + 's '
                'WHERE table = ' + _sql_string(table) + ' AND name = ' +
                _sql_string(name)
            )
            return {'rows': self._query_rows(route, source, database)}
        if kind == 'trigger':
            source = (
                'SELECT * FROM system.processing_engine_triggers WHERE '
                'trigger_name = ' + _sql_string(name)
            )
            return {'rows': self._query_rows(route, source, database)}
        if kind == 'plugin':
            source = (
                'SELECT * FROM system.plugin_files WHERE plugin_name = ' +
                _sql_string(name)
            )
            return {'rows': self._query_rows(route, source, '_internal')}
        if kind == 'token':
            source = (
                'SELECT * EXCLUDE(hash) FROM system.tokens WHERE name = ' +
                _sql_string(name)
            )
            return {'rows': self._query_rows(route, source, '_internal')}
        if kind == 'processing-engine':
            return {
                'triggers': self._query_rows(
                    route, 'SELECT * FROM system.processing_engine_triggers',
                    database,
                ),
                'plugins': self._query_rows(
                    route, 'SELECT * FROM system.plugin_files', '_internal'
                ),
            }
        if kind == 'compaction':
            return {'parquet_files': self._query_rows(
                route, 'SELECT * FROM system.parquet_files', database
            )}
        raise InfluxDBClientError('resource inspection is unavailable')

    def _apply_admin(self, route, payload):
        kind, operation = payload['kind'], payload['operation']
        draft, target = payload.get('draft', {}), payload.get('target', {})
        database = (
            target.get('db') or target.get('database') or
            target.get('database_name') or draft.get('database') or
            route['database']
        )
        name = target.get('name') or target.get(
            'table_name') or target.get('trigger_name') or target.get(
                'plugin_name') or draft.get('name')
        if kind == 'database':
            if operation in {'create', 'alter'}:
                body = {'db': _name(name, 'database')}
                if draft.get('retention_period'):
                    body['retention_period'] = _duration(
                        draft['retention_period'], 'retention period'
                    )
                return self._request(
                    route, '/api/v3/configure/database',
                    method='POST' if operation == 'create' else 'PUT',
                    json_body=body, mutating=True,
                )
            if operation == 'drop':
                hard_delete = self._hard_delete_at(draft)
                return self._request(
                    route, '/api/v3/configure/database', method='DELETE',
                    query={
                        'db': _name(name, 'database'),
                        'hard_delete_at': hard_delete,
                    }, mutating=True,
                )
        if kind == 'retention-policy' and operation == 'alter':
            return self._request(
                route, '/api/v3/configure/database', method='PUT',
                json_body={
                    'db': _name(database, 'database'),
                    'retention_period': (
                        None if draft.get('retain_forever') else _duration(
                            draft.get('retention_period'), 'retention period'
                        )
                    ),
                },
                mutating=True,
            )
        if kind == 'table':
            if operation == 'create':
                fields = []
                for item in draft['fields']:
                    item = _mapping(item, 'field')
                    field_type = str(item.get('type', '')).casefold()
                    if field_type not in {
                            'utf8', 'int64', 'uint64', 'float64', 'bool'}:
                        raise InfluxDBClientError('field type is invalid')
                    fields.append({'name': _name(item.get('name'), 'field'),
                                   'type': field_type})
                return self._request(
                    route,
                    '/api/v3/configure/table',
                    method='POST',
                    json_body={
                        'db': _name(
                            database,
                            'database'),
                        'table': _name(
                            name,
                            'table'),
                        'tags': [
                            _name(
                                item,
                                'tag') for item in draft['tags']],
                        'fields': fields},
                    mutating=True,
                )
            if operation == 'insert':
                precision = draft.get('precision', 'nanosecond')
                if precision not in {'nanosecond',
                                     'microsecond', 'millisecond', 'second'}:
                    raise InfluxDBClientError('precision is invalid')
                return self._request(
                    route, '/api/v3/write_lp', method='POST',
                    query={'db': database, 'precision': precision,
                           'accept_partial': str(bool(draft.get(
                               'accept_partial'
                           ))).lower()},
                    body=_code_text(
                        draft.get('line_protocol'), 'line protocol',
                        2 * 1024 * 1024
                    ), headers={'Content-Type': 'text/plain; charset=utf-8'},
                    mutating=True,
                )
            if operation == 'drop':
                hard_delete = self._hard_delete_at(draft)
                return self._request(
                    route, '/api/v3/configure/table', method='DELETE',
                    query={
                        'db': database, 'table': _name(name, 'table'),
                        'hard_delete_at': hard_delete,
                    },
                    mutating=True,
                )
        if kind in {'last-cache', 'distinct-cache'}:
            endpoint = '/api/v3/configure/' + kind.replace('-', '_')
            table = target.get('table') or draft.get('table')
            if operation == 'create':
                body = {
                    'db': _name(database, 'database'),
                    'table': _name(table, 'table'),
                }
                if draft.get('name'):
                    body['name'] = _name(draft['name'], 'cache name')
                if kind == 'last-cache':
                    body.update({
                        'key_columns': [
                            _name(item, 'key column')
                            for item in draft['key_columns']
                        ],
                        'count': bounded_integer(
                            draft.get('count'), 'count', 1, 1, 10
                        ),
                        'ttl': bounded_integer(
                            draft.get('ttl'), 'ttl', 14400, 1, 315360000
                        ),
                    })
                    if draft.get('value_columns') is not None:
                        if not isinstance(draft['value_columns'], list):
                            raise InfluxDBClientError(
                                'value_columns must be an array'
                            )
                        body['value_columns'] = [
                            _name(item, 'value column')
                            for item in draft['value_columns']
                        ]
                else:
                    body.update({
                        'columns': [
                            _name(item, 'cache column')
                            for item in draft['columns']
                        ],
                        'max_cardinality': bounded_integer(
                            draft.get('max_cardinality'),
                            'maximum cardinality', 100000, 1, 2**63 - 1,
                        ),
                        'max_age': bounded_integer(
                            draft.get('max_age_seconds'),
                            'maximum age', 3600, 1, 315360000,
                        ),
                    })
                return self._request(
                    route, endpoint, method='POST', json_body=body,
                    mutating=True,
                )
            if operation == 'drop':
                return self._request(
                    route, endpoint, method='DELETE', query={
                        'db': _name(database, 'database'),
                        'table': _name(table, 'table'),
                        'name': _name(name, 'cache name'),
                    }, mutating=True,
                )
        if kind == 'token':
            if operation == 'drop':
                return self._request(
                    route, '/api/v3/configure/token', method='DELETE',
                    query={'token_name': _name(name, 'token name')},
                    mutating=True,
                )
        if kind == 'trigger' and operation == 'execute':
            return self._apply_trigger_action(route, draft)
        if kind == 'plugin' and operation == 'execute':
            return self._apply_plugin_action(route, draft)
        if kind == 'processing-engine' and operation == 'execute':
            return self._apply_processing_action(route, draft)
        raise InfluxDBClientError('administration operation is unavailable')

    @staticmethod
    def _hard_delete_at(draft):
        mode = draft.get('hard_delete_mode', 'default')
        if mode == 'timestamp':
            return draft['hard_delete_at']
        return mode

    def _apply_trigger_action(self, route, draft):
        action = draft['action']
        database = _name(draft.get('database'), 'database')
        trigger_name = _name(draft.get('trigger_name'), 'trigger name')
        endpoint = '/api/v3/configure/processing_engine_trigger'
        if action in {'enable', 'disable'}:
            return self._request(
                route, endpoint + '/' + action, method='POST',
                query={'db': database, 'trigger_name': trigger_name},
                mutating=True,
            )
        if action == 'delete':
            return self._request(
                route, endpoint, method='DELETE', json_body={
                    'db': database, 'trigger_name': trigger_name,
                    'force': bool(draft.get('force')),
                }, mutating=True,
            )
        trigger_kind = draft['trigger_kind']
        specification = {
            'all_tables': 'all_tables',
            'table': 'table:' + draft.get('trigger_value', ''),
            'cron': 'cron:' + draft.get('trigger_value', ''),
            'every': 'every:' + draft.get('trigger_value', ''),
            'request': 'request:' + draft.get('trigger_value', ''),
        }[trigger_kind]
        return self._request(
            route, endpoint, method='POST', json_body={
                'db': database,
                'plugin_filename': _name(
                    draft.get('plugin_filename'), 'plugin filename'
                ),
                'trigger_name': trigger_name,
                'trigger_settings': {
                    'run_async': bool(draft.get('run_async')),
                    'error_behavior': draft.get('error_behavior', 'log'),
                },
                'trigger_specification': specification,
                'trigger_arguments': _string_map(
                    draft.get('arguments', {}), 'trigger arguments'
                ) or None,
                'disabled': bool(draft.get('disabled')),
            }, mutating=True,
        )

    def _apply_plugin_action(self, route, draft):
        action = draft['action']
        database = _name(draft.get('database'), 'database')
        plugin_name = _name(draft.get('plugin_name'), 'plugin name')
        if action in {'upload-file', 'update-file'}:
            return self._request(
                route, '/api/v3/plugins/files',
                method='POST' if action == 'upload-file' else 'PUT',
                query={'db': database}, json_body={
                    'plugin_name': plugin_name,
                    'content': _code_text(
                        draft.get('content'), 'plugin source', 2 * 1024**2
                    ),
                }, mutating=True,
            )
        if action == 'replace-directory':
            return self._request(
                route, '/api/v3/plugins/directory', method='PUT',
                query={'db': database}, json_body={
                    'plugin_name': plugin_name,
                    'files': [{
                        'relative_path': _name(
                            item['relative_path'], 'relative path'
                        ),
                        'content': _code_text(
                            item['content'], 'plugin source', 2 * 1024**2
                        ),
                    } for item in draft['files']],
                }, mutating=True,
            )
        raise InfluxDBClientError('plugin action is unavailable')

    def _apply_processing_action(self, route, draft):
        action = draft['action']
        if action == 'install-packages':
            return self._request(
                route,
                '/api/v3/configure/plugin_environment/install_packages',
                method='POST', json_body={'packages': _string_list(
                    draft.get('packages'), 'packages', True
                )}, mutating=True,
            )
        if action == 'install-requirements':
            return self._request(
                route,
                '/api/v3/configure/plugin_environment/install_requirements',
                method='POST', json_body={
                    'requirements_location': required_text(
                        draft.get('requirements_location'),
                        'requirements location', 4096,
                    ),
                }, mutating=True,
            )
        body = {
            'filename': _name(
                draft.get('plugin_filename'), 'plugin filename'
            ),
            'database': _name(draft.get('database'), 'database'),
            'cache_name': draft.get('cache_name') or None,
            'input_arguments': _string_map(
                draft.get('arguments', {}), 'plugin arguments'
            ) or None,
        }
        if action == 'test-wal':
            body['input_lp'] = _code_text(
                draft.get('input_line_protocol'), 'test line protocol',
                2 * 1024 * 1024,
            )
            endpoint = '/api/v3/plugin_test/wal'
        else:
            body['schedule'] = draft.get('schedule') or None
            endpoint = '/api/v3/plugin_test/schedule'
        return self._request(
            route, endpoint, method='POST', json_body=body, mutating=True
        )

    def read_admin_rows(self, request):
        route = self._route({'route': request.get('_provider_route')})
        target = self._native_target(request.get('target_resource'))
        table = target.get('table_name') or target.get('table') or target.get(
            'name'
        )
        if not table:
            raise InfluxDBClientError('table target is invalid')
        limit = bounded_integer(
            request.get('limit'), 'limit', 200, 1, MAX_PAGE_SIZE
        )
        continuation = request.get('continuation') or {}
        if not isinstance(continuation, Mapping):
            raise InfluxDBClientError('continuation is invalid')
        offset = bounded_integer(
            continuation.get('offset'), 'offset', 0, 0, 1000000000
        )
        result = self._query(
            route,
            f'SELECT * FROM {self._quote_identifier(table)} '
            f'ORDER BY time DESC LIMIT {limit} OFFSET {offset}',
            'sql',
        )
        return {
            'records': copy.deepcopy(result.points),
            'columns': copy.deepcopy(result.columns),
            'editable': False,
            'insertable': not route['read_only'],
            'continuation': (
                {'offset': offset + len(result.points)}
                if len(result.points) == limit else None
            ),
            'limits': {'maximum_page_size': MAX_PAGE_SIZE},
            'provider_owned_identity': False,
            'native_write_surface': 'line_protocol',
        }

    @staticmethod
    def cancel_admin_cursor(request):
        return {
            'cancelled': bool(request.get('continuation')),
            'provider_owned_cursor': False,
        }

    @staticmethod
    def _native_target(target):
        if not isinstance(target, Mapping):
            return {}
        extensions = target.get('extensions')
        if isinstance(extensions, Mapping):
            provider = extensions.get('influxdb')
            if isinstance(provider, Mapping) and isinstance(
                provider.get('native'), Mapping
            ):
                return copy.deepcopy(dict(provider['native']))
        native = target.get('native')
        return copy.deepcopy(dict(native)) if isinstance(
            native, Mapping) else {}

    @staticmethod
    def _safe_target(target):
        return {key: copy.deepcopy(value) for key, value in target.items(
        ) if 'token' not in key.casefold() and 'secret' not in key.casefold()}

    def close(self):
        with self._lock:
            sessions, self._sessions = self._sessions, []
        for session in sessions:
            session.close()
        self.transport.close()
