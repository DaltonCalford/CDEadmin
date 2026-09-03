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
            self._resource('node', route['host'], ping,
                           ['node', route['host']]),
            self._resource('processing-engine', 'processing-engine', {
                'endpoint': '/api/v3/configure/processing_engine_plugin'
            }),
            self._resource('compaction', 'compaction', {
                'engine_owned': True
            }),
        ]
        databases = self._request(
            route, '/api/v3/configure/database',
            query={'format': 'json', 'show_deleted': 'false'},
        ).json()
        if isinstance(databases, Mapping):
            databases = databases.get('databases', databases.get('data', []))
        for item in databases if isinstance(databases, list) else []:
            native = item if isinstance(item, Mapping) else {'name': item}
            name = native.get('name') or native.get('db') or item
            resources.append(self._resource(
                'database', name, native, ['database', name]
            ))
        queries = (
            ('table', 'SELECT table_schema, table_name, table_type FROM '
             'information_schema.tables'),
            ('column', 'SELECT table_schema, table_name, column_name, '
             'data_type FROM information_schema.columns'),
        )
        for kind, source in queries:
            try:
                rows = self._query_rows(route, source)
            except InfluxDBClientError:
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                table = row.get('table_name')
                name = row.get('column_name') if kind == 'column' else table
                schema = row.get('table_schema') or route['database']
                resources.append(self._resource(
                    kind, name, row, [kind, schema, table, name]
                ))
                if kind == 'column':
                    role = str(row.get('data_type', '')).casefold()
                    derived = 'tag' if role == 'dictionary' else 'field'
                    resources.append(self._resource(
                        derived, name, row, [derived, schema, table, name]
                    ))
        system_queries = (
            ('last-cache', 'SELECT * FROM system.last_caches', 'name', None),
            ('distinct-cache', 'SELECT * FROM system.distinct_caches',
             'name', None),
            ('trigger', 'SELECT * FROM system.processing_engine_triggers',
             'trigger_name', None),
            ('plugin', 'SELECT * FROM system.plugin_files', 'plugin_name',
             '_internal'),
            ('token', 'SELECT * FROM system.tokens', 'name', '_internal'),
        )
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
                resources.append(self._resource(
                    kind, name, native,
                    [kind, route['database'], table, name]
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
                        f('tags', 'Tag columns', 'json', True, default=[]),
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
        if kind in {'trigger', 'plugin',
                    'processing-engine'} and operation == 'execute':
            return {'form_id': f'influxdb-{kind}-execute',
                    'title': f'Configure {kind}', 'fields': [
                        f('action', 'Action', 'select', True, default='create',
                          options=[{'value': value, 'label': value.title()}
                                   for value in ('create', 'delete', 'enable',
                                                 'disable')]),
                        f('definition', 'Native API document', 'json', True,
                          default={}),
            ]}
        if operation == 'drop':
            return {'form_id': 'influxdb-drop', 'title': 'Drop', 'fields': [
                f('acknowledge_drop', 'Confirm drop', 'boolean', True,
                  default=False),
            ]}
        return {'form_id': f'influxdb-{kind}-{operation}',
                'title': operation.title(), 'fields': [
                    f('definition', 'Native API document', 'json', True,
                      default={}),
        ]}

    def visual_admin_catalog(self, catalog):
        catalog['native_planner'] = 'influxdb3-http-api-planner'
        catalog['query_languages'] = ['sql', 'influxql']
        catalog['transaction_authority'] = 'influxdb-request-outcome'
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
            if request.get('operation_id') == 'drop' and not draft.get(
                'acknowledge_drop'
            ):
                raise InfluxDBClientError('drop acknowledgement is required')
            if request.get('resource_kind') == 'table' and request.get(
                'operation_id'
            ) == 'create':
                if not isinstance(draft.get('tags'), list):
                    raise InfluxDBClientError('tags must be an array')
                if not isinstance(draft.get('fields'),
                                  list) or not draft['fields']:
                    raise InfluxDBClientError(
                        'fields must be a non-empty array')
            if request.get('resource_kind') in {
                'last-cache', 'distinct-cache'
            } and request.get('operation_id') == 'create':
                field = ('key_columns' if request['resource_kind'] ==
                         'last-cache' else 'columns')
                if not isinstance(draft.get(field), list) or not draft[field]:
                    raise InfluxDBClientError(
                        f'{field} must be a non-empty array'
                    )
            if request.get('resource_kind') in {
                'trigger', 'plugin', 'processing-engine'
            } and request.get('operation_id') == 'execute':
                if draft.get('action') not in {
                    'create', 'delete', 'enable', 'disable'
                }:
                    raise InfluxDBClientError(
                        'processing action is invalid'
                    )
        except (InfluxDBClientError, KeyError) as exc:
            errors.append({'field_id': None,
                           'code': 'influxdb_native_validation',
                           'message': str(exc)})
        return {'errors': errors, 'warnings': []}

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

    def _apply_admin(self, route, payload):
        kind, operation = payload['kind'], payload['operation']
        draft, target = payload.get('draft', {}), payload.get('target', {})
        database = target.get('db') or target.get('database') or draft.get(
            'database'
        ) or route['database']
        name = target.get('name') or target.get(
            'table_name') or draft.get('name')
        if operation == 'inspect':
            return self._request(route, '/ping')
        if kind == 'database':
            if operation in {'create', 'alter'}:
                body = {'db': _name(name, 'database')}
                if draft.get('retention_period'):
                    body['retention_period'] = required_text(
                        draft['retention_period'], 'retention period', 128
                    )
                return self._request(
                    route, '/api/v3/configure/database',
                    method='POST' if operation == 'create' else 'PUT',
                    json_body=body, mutating=True,
                )
            if operation == 'drop':
                return self._request(
                    route, '/api/v3/configure/database', method='DELETE',
                    query={'db': _name(name, 'database')}, mutating=True,
                )
        if kind == 'retention-policy' and operation == 'alter':
            return self._request(
                route, '/api/v3/configure/database', method='PUT',
                json_body={'db': _name(database, 'database'),
                           'retention_period': required_text(
                               draft.get('definition'), 'retention period', 128
                )}, mutating=True,
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
                    body=required_text(
                        draft.get('line_protocol'), 'line protocol',
                        2 * 1024 * 1024
                    ), headers={'Content-Type': 'text/plain; charset=utf-8'},
                    mutating=True,
                )
            if operation == 'drop':
                return self._request(
                    route, '/api/v3/configure/table', method='DELETE',
                    query={'db': database, 'table': _name(name, 'table')},
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
        if kind in {'trigger', 'plugin',
                    'processing-engine'} and operation == 'execute':
            action = draft['action']
            definition = _mapping(draft['definition'], 'definition')
            endpoint = {
                'trigger': '/api/v3/configure/processing_engine_trigger',
                'plugin': '/api/v3/configure/processing_engine_plugin',
                'processing-engine': (
                    '/api/v3/configure/plugin_environment/install_packages'
                ),
            }[kind]
            if kind == 'trigger' and action in {'enable', 'disable'}:
                endpoint += '/' + action
            method = 'DELETE' if action == 'delete' else 'POST'
            return self._request(
                route, endpoint, method=method, json_body=definition,
                mutating=True,
            )
        raise InfluxDBClientError('administration operation is unavailable')

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
