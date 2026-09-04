##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""OpenSearch 3.6 SQL/PPL plugin client and datasource administration."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import urllib.parse
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
from pgadmin.cdeadmin.search.opensearch import OpenSearchClient


SERVER_VERSION = '3.6.0'
PLUGIN_VERSION = '3.6.0.0'
PROFILE_VERSION = '3.6.0-sql-ppl'
MAX_RECORDS = 10000


class OpenSearchSQLPPLClientError(AnalyticHTTPError):
    """SQL/PPL request or identity validation failed."""


class OpenSearchSQLPPLUnknownOutcomeError(OpenSearchSQLPPLClientError):
    """A SQL/PPL administrative mutation has an unknown outcome."""

    outcome = 'unknown_requires_observation'
    retryable = False


@dataclass
class _Session:
    route: dict[str, Any]
    last_observation: str = 'no-query-observed'
    closed: bool = False

    def close(self):
        self.closed = True


@dataclass
class _Result:
    rows: list[dict[str, Any]]
    columns: list[dict[str, Any]]
    language: str
    native: dict[str, Any]


def _mapping(value, label='request'):
    if not isinstance(value, Mapping):
        raise OpenSearchSQLPPLClientError(f'{label} must be an object')
    return copy.deepcopy(dict(value))


def _name(value, label='name'):
    value = required_text(value, label, 255)
    if any(character in value for character in ('/', '\\', '\x00')):
        raise OpenSearchSQLPPLClientError(f'{label} is invalid')
    return value


class OpenSearchSQLPPLClient:
    """Separate SQL/PPL semantic profile over OpenSearch's HTTP boundary."""

    SEARCH_RESOURCE_KINDS = frozenset({
        'index', 'mapping', 'settings', 'alias', 'index-template',
        'component-template', 'ingest-pipeline', 'shard',
        'reindex-operation', 'repository', 'snapshot', 'ingest-processor',
        'query-profile',
    })

    ADMIN_OPERATIONS = {
        'catalog': frozenset({'inspect'}),
        'data-source': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'query': frozenset({'inspect', 'execute'}),
        'prepared-query': frozenset({'inspect', 'execute'}),
        'language-settings': frozenset({'inspect', 'alter'}),
        **{
            kind: OpenSearchClient.ADMIN_OPERATIONS[kind]
            for kind in SEARCH_RESOURCE_KINDS
        },
    }

    def __init__(self, secret_acquirer=None, urlopen=None):
        self.transport = BoundedJSONHTTPTransport(
            secret_acquirer, urlopen=urlopen
        )
        self._sessions = []
        self._lock = threading.RLock()
        self.search_admin = OpenSearchClient(secret_acquirer, urlopen=urlopen)

    @staticmethod
    def _route(request):
        try:
            route = normalize_http_route(
                request, default_port=9200, default_auth='none',
                extra_fields=(
                    'database', 'data_source', 'query_language', 'fetch_size'
                ),
            )
        except AnalyticHTTPError as exc:
            raise OpenSearchSQLPPLClientError(str(exc)) from exc
        route['query_language'] = str(
            route.get('query_language', 'sql')
        ).casefold()
        if route['query_language'] not in {'sql', 'ppl'}:
            raise OpenSearchSQLPPLClientError('query language is invalid')
        if route.get('data_source'):
            route['data_source'] = _name(route['data_source'], 'data source')
        if route.get('fetch_size') is not None:
            route['fetch_size'] = bounded_integer(
                int(route['fetch_size']), 'fetch size', 1000, 1, 10000
            )
        else:
            route['fetch_size'] = 1000
        return route

    def _request(self, route, path, **options):
        try:
            return self.transport.request(route, path, **options)
        except OpenSearchSQLPPLClientError:
            raise
        except AnalyticHTTPUnknownOutcomeError as exc:
            raise OpenSearchSQLPPLUnknownOutcomeError(str(exc)) from exc
        except AnalyticHTTPError as exc:
            raise OpenSearchSQLPPLClientError(
                str(exc), status=exc.status,
                native_payload=exc.native_payload,
            ) from exc

    def runtime_identity(self, request, handle=None):
        route = handle.route if isinstance(handle, _Session) else self._route(
            request
        )
        root = self._request(route, '/').json()
        plugins = self._request(
            route, '/_cat/plugins', query={'format': 'json'}
        ).json()
        if not isinstance(root, Mapping) or not isinstance(
            root.get('version'), Mapping
        ):
            raise OpenSearchSQLPPLClientError(
                'OpenSearch SQL/PPL server identity is invalid'
            )
        version = str((root.get('version') or {}).get('number', ''))
        distribution = str(
            (root.get('version') or {}).get('distribution', 'opensearch')
        ).casefold()
        matches = [
            item for item in plugins if isinstance(item, Mapping) and
            (item.get('component') or item.get('name')) == 'opensearch-sql'
        ] if isinstance(plugins, list) else []
        plugin_versions = {
            str(item.get('version')) for item in matches
        }
        if (version != SERVER_VERSION or distribution != 'opensearch' or
                PLUGIN_VERSION not in plugin_versions):
            raise OpenSearchSQLPPLClientError(
                'runtime did not prove exact OpenSearch 3.6.0 and SQL/PPL '
                '3.6.0.0 identity'
            )
        build = (root.get('version') or {}).get('build_hash') or version
        return {
            'engine_id': 'opensearch_sql_ppl',
            'version': PROFILE_VERSION,
            'build_id': (
                f'OpenSearch {version} build {build}; SQL {PLUGIN_VERSION}'
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
        if not isinstance(handle, _Session) or handle.closed:
            raise OpenSearchSQLPPLClientError('SQL/PPL session is unavailable')
        return {
            'native_observation': handle.last_observation,
            'query_request_atomicity_only': True,
            'multi_request_transaction_supported': False,
            'automatic_replay': False,
            'finality_interpreted_by_common_code': False,
        }

    @staticmethod
    def _language(value, default):
        if not value:
            return default
        value = str(value).casefold()
        if value in {'sql', 'ppl'}:
            return value
        if 'ppl' in value and 'sql' not in value:
            return 'ppl'
        if 'sql' in value:
            return default
        raise OpenSearchSQLPPLClientError('query language is invalid')

    def _query(self, route, source, language=None, parameters=None):
        source = required_text(source, 'SQL/PPL query', 2 * 1024 * 1024)
        language = self._language(language, route['query_language'])
        if parameters is not None and not isinstance(parameters, Mapping):
            raise OpenSearchSQLPPLClientError('parameters must be an object')
        body = {'query': source, 'fetch_size': route['fetch_size']}
        if route.get('data_source'):
            body['datasource'] = route['data_source']
        if parameters:
            body['parameters'] = copy.deepcopy(dict(parameters))
        endpoint = '/_plugins/_sql' if language == 'sql' else '/_plugins/_ppl'
        document = self._request(
            route, endpoint, method='POST', query={'format': 'jdbc'},
            json_body=body,
        ).json()
        if not isinstance(document, Mapping):
            raise OpenSearchSQLPPLClientError('SQL/PPL response is invalid')
        columns = []
        for item in document.get('schema', []) or []:
            if isinstance(item, Mapping):
                columns.append({'name': str(item.get('name')),
                                'type': str(item.get('type', 'unknown'))})
        native_rows = document.get('datarows', document.get('rows', [])) or []
        if not isinstance(native_rows, list):
            raise OpenSearchSQLPPLClientError('SQL/PPL rows are invalid')
        names = [item['name'] for item in columns]
        rows = []
        for row in native_rows[:MAX_RECORDS]:
            if isinstance(row, Mapping):
                rows.append(copy.deepcopy(dict(row)))
            elif isinstance(row, list) and len(row) == len(names):
                rows.append(dict(zip(names, copy.deepcopy(row))))
            else:
                rows.append({'value': copy.deepcopy(row)})
        if not columns and rows:
            columns = [{'name': name, 'type': 'unknown'} for name in rows[0]]
        return _Result(rows, columns, language, copy.deepcopy(dict(document)))

    def execute(self, handle, request):
        if not isinstance(handle, _Session) or handle.closed:
            raise OpenSearchSQLPPLClientError('SQL/PPL session is unavailable')
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
            raise OpenSearchSQLPPLClientError(
                'SQL/PPL result token is invalid')
        return {
            'result_kind': 'columnar',
            'schema': {'columns': copy.deepcopy(token.columns),
                       'language': token.language},
            'complete': True, 'stream_reference': None,
            'payload': {'rows': copy.deepcopy(token.rows),
                        'native': copy.deepcopy(token.native)},
        }

    @staticmethod
    def _generation(value):
        return hashlib.sha256(json.dumps(
            value, sort_keys=True, default=str, separators=(',', ':')
        ).encode('utf-8')).hexdigest()[:24]

    def _resource(self, kind, name, native):
        return {
            'resource_id': f'opensearch_sql_ppl:{kind}:{name}',
            'resource_kind': kind, 'display_name': str(name),
            'authority_path': ['opensearch_sql_ppl', kind, str(name)],
            'display_path': [kind, str(name)],
            'generation': self._generation(native),
            'native': copy.deepcopy(native),
        }

    def list_resources(self, request):
        route = self._route(request)
        resources = [
            self._resource('catalog', 'OpenSearch', {
                'server_version': SERVER_VERSION,
                'plugin_version': PLUGIN_VERSION,
            }),
            self._resource('query', 'SQL', {'language': 'sql'}),
            self._resource('query', 'PPL', {'language': 'ppl'}),
            self._resource('prepared-query', 'SQL prepared query', {
                'language': 'sql', 'parameters_supported': True
            }),
            self._resource('language-settings', 'SQL/PPL settings', {
                'endpoint': '/_cluster/settings'
            }),
        ]
        try:
            document = self._request(
                route, '/_plugins/_query/_datasources'
            ).json()
        except OpenSearchSQLPPLClientError as exc:
            if exc.status not in {400, 403, 404}:
                raise
            document = None
        values = (document.get('datasources', document.get('dataSources', []))
                  if isinstance(document, Mapping) else [])
        for item in values if isinstance(values, list) else []:
            native = item if isinstance(item, Mapping) else {'name': item}
            name = native.get('name') or native.get('dataSourceName')
            if name:
                resources.append(self._resource('data-source', name, native))
        for item in self.search_admin.list_resources(request):
            if item.get('resource_kind') not in self.SEARCH_RESOURCE_KINDS:
                continue
            shared = copy.deepcopy(item)
            shared['resource_id'] = (
                'opensearch_sql_ppl:search:' + item['resource_id']
            )
            shared['authority_path'] = [
                'opensearch_sql_ppl', 'search',
                *item.get('authority_path', [])[1:],
            ]
            shared['display_path'] = [
                'search', *item.get('display_path', []),
            ]
            resources.append(shared)
        return resources

    def inspect_resource(self, request):
        native = self._native_target(request)
        kind = request.get('resource_kind') or native.get('resource_kind')
        name = native.get('name') or native.get('dataSourceName') or kind
        if not kind or not name:
            raise OpenSearchSQLPPLClientError('resource identity is absent')
        if kind in self.SEARCH_RESOURCE_KINDS:
            return self.search_admin.inspect_resource({
                'resource_kind': kind, 'native': native,
            })
        return self._resource(kind, name, native)

    def describe_security(self, request):
        route = self._route(request)
        native = {
            'authorization_model': 'opensearch-security-plugin',
            'sql_plugin_permissions_enforced_by_server': True,
            'credential_material_exposed': False,
            'authentication_kind': route['auth_kind'],
        }
        return {
            'resource_id': 'opensearch_sql_ppl:security:current',
            'display_name': 'OpenSearch SQL/PPL security',
            'authority_path': ['opensearch_sql_ppl', 'security', 'current'],
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
            return {'form_id': 'opensearch-sql-ppl-inspect',
                    'title': 'Inspect', 'fields': []}
        if kind == 'data-source' and operation in {'create', 'alter'}:
            return {'form_id': f'opensearch-data-source-{operation}',
                    'title': f'{operation.title()} data source', 'fields': [
                        f('name', 'Data source name',
                          required=operation == 'create'),
                        f('definition', 'Data source JSON', 'json', True,
                          default={}),
            ]}
        if kind in {'query', 'prepared-query'} and operation == 'execute':
            return {'form_id': 'opensearch-query-execute',
                    'title': 'Execute query', 'fields': [
                        f('language', 'Language', 'select', True,
                          default='sql', options=[
                              {'value': 'sql', 'label': 'SQL'},
                              {'value': 'ppl', 'label': 'PPL'},
                          ]),
                        f('source', 'Query', 'code', True),
                        f('parameters', 'Parameters', 'json', True,
                          default={}),
                    ]}
        if kind == 'language-settings' and operation == 'alter':
            return {'form_id': 'opensearch-language-settings-alter',
                    'title': 'Alter language settings', 'fields': [
                        f('definition', 'Cluster settings JSON', 'json', True,
                          default={}),
                    ]}
        if operation == 'drop':
            return {'form_id': 'opensearch-data-source-drop',
                    'title': 'Drop data source', 'fields': [
                        f('acknowledge_drop', 'Confirm drop', 'boolean', True,
                          default=False),
                    ]}
        return {'form_id': f'opensearch-sql-ppl-{kind}-{operation}',
                'title': operation.title(), 'fields': []}

    def visual_admin_catalog(self, catalog):
        catalog['native_planner'] = 'opensearch-sql-ppl-rest-planner'
        catalog['query_languages'] = ['sql', 'ppl']
        catalog['transaction_authority'] = 'opensearch-query-request-outcome'
        catalog['common_finality_interpretation'] = False
        catalog['experience_families'] = ['search']
        declarations = self.search_admin.search_concept_declarations()
        for declaration in declarations.values():
            declaration['reason'] = (
                'The SQL/PPL provider composes the same exact OpenSearch '
                '3.6 REST administration surface alongside its query '
                'language endpoints.'
            )
            declaration['evidence'] = [
                'opensearch-sql-ppl-3.6-composed-rest-catalog'
            ]
        catalog['concept_declarations'] = {'search': declarations}
        for resource in catalog.get('objects', []):
            kind = resource['resource_kind']
            resource['operations'] = [
                operation for operation in resource.get('operations', [])
                if self.supports_admin_operation(
                    kind, operation['operation_id']
                )
            ]
            for operation in resource['operations']:
                if kind in self.SEARCH_RESOURCE_KINDS:
                    operation['form'] = self.search_admin._form(
                        kind, operation['operation_id']
                    )
                else:
                    operation['form'] = self._form(
                        kind, operation['operation_id']
                    )
                if operation['operation_id'] in {'drop', 'execute'}:
                    operation['confirmation_required'] = True
        return catalog

    def validate_admin_operation(self, request):
        errors = []
        try:
            kind, operation = request.get(
                'resource_kind'), request.get('operation_id')
            if kind in self.SEARCH_RESOURCE_KINDS:
                adapted = copy.deepcopy(request)
                adapted['target_resource'] = {
                    'native': self._native_target(
                        request.get('target_resource')
                    )
                }
                return self.search_admin.validate_admin_operation(adapted)
            if not self.supports_admin_operation(kind, operation):
                raise OpenSearchSQLPPLClientError('operation is unavailable')
            draft = _mapping(request.get('draft', {}), 'draft')
            if operation == 'drop' and not draft.get('acknowledge_drop'):
                raise OpenSearchSQLPPLClientError(
                    'drop acknowledgement is required')
        except OpenSearchSQLPPLClientError as exc:
            errors.append({'field_id': None,
                           'code': 'opensearch_sql_ppl_validation',
                           'message': str(exc)})
        return {'errors': errors, 'warnings': []}

    def plan_admin_operation(self, request):
        validation = self.validate_admin_operation(request)
        if validation['errors']:
            raise OpenSearchSQLPPLClientError(
                validation['errors'][0]['message'])
        return {
            'command_preview': {
                'provider': 'opensearch_sql_ppl',
                'resource_kind': request['resource_kind'],
                'operation': request['operation_id'],
                'native_request_generated_at_execution': True,
            },
            'warnings': [],
            'provider_payload': {
                'kind': request['resource_kind'],
                'operation': request['operation_id'],
                'target': self._native_target(request.get('target_resource')),
                'draft': copy.deepcopy(request.get('draft', {})),
                'route': copy.deepcopy(request.get('_provider_route')),
            },
            'receipt': {'provider': 'opensearch_sql_ppl',
                        'automatic_retry': False,
                        'transaction_finality_interpreted': False},
        }

    def apply_admin_operation(self, request):
        payload = _mapping(request.get('provider_payload'), 'provider payload')
        if payload.get('kind') in self.SEARCH_RESOURCE_KINDS:
            return self.search_admin.apply_admin_operation({
                'provider_payload': payload,
            })
        route = self._route({'route': payload.get('route')})
        kind, operation = payload['kind'], payload['operation']
        if route['read_only'] and operation not in {'inspect', 'execute'}:
            raise OpenSearchSQLPPLClientError(
                'read-only route refused mutation'
            )
        draft, target = payload.get('draft', {}), payload.get('target', {})
        if operation == 'inspect':
            response = self._request(route, '/_cat/plugins',
                                     query={'format': 'json'})
            native = response.json()
        elif kind in {'query', 'prepared-query'} and operation == 'execute':
            result = self._query(route, draft['source'], draft['language'],
                                 draft.get('parameters'))
            native = {'rows': result.rows, 'columns': result.columns,
                      'language': result.language}
        elif kind == 'data-source':
            name = target.get('name') or target.get(
                'dataSourceName') or draft.get('name')
            path = '/_plugins/_query/_datasources'
            if operation == 'drop':
                path += '/' + urllib.parse.quote(_name(name), safe='')
                response = self._request(
                    route, path, method='DELETE', mutating=True)
            else:
                definition = _mapping(
                    draft['definition'], 'data source definition')
                definition.setdefault('name', _name(name))
                response = self._request(
                    route,
                    path,
                    method='POST' if operation == 'create' else 'PUT',
                    json_body=definition,
                    mutating=True,
                )
            native = response.json()
        elif kind == 'language-settings' and operation == 'alter':
            response = self._request(
                route, '/_cluster/settings', method='PUT',
                json_body=_mapping(draft['definition'], 'settings'),
                mutating=True,
            )
            native = response.json()
        else:
            raise OpenSearchSQLPPLClientError(
                'administration operation is unavailable')
        return {
            'native_response_observed': True, 'native_response': native,
            'automatic_retry': False,
            'transaction_finality_interpreted_by_common_code': False,
        }

    @staticmethod
    def _native_target(target):
        if not isinstance(target, Mapping):
            return {}
        extensions = target.get('extensions')
        if isinstance(extensions, Mapping):
            provider = extensions.get('opensearch_sql_ppl')
            if isinstance(provider, Mapping) and isinstance(
                provider.get('native'), Mapping
            ):
                return copy.deepcopy(dict(provider['native']))
        native = target.get('native')
        return copy.deepcopy(dict(native)) if isinstance(
            native, Mapping) else {}

    def close(self):
        with self._lock:
            sessions, self._sessions = self._sessions, []
        for session in sessions:
            session.close()
        self.transport.close()
        self.search_admin.close()
