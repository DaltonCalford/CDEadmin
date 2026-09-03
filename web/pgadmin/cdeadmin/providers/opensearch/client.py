##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""OpenSearch 3.6 HTTP/JSON query and administration adapter."""

from __future__ import annotations

import copy
import hashlib
import json
import re
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


REFERENCE_VERSION = '3.6.0'
MAX_RECORDS = 10000
MAX_PAGE_SIZE = 1000
_NAME = re.compile(r'^[a-z0-9][a-z0-9._+-]{0,254}$')


class OpenSearchClientError(AnalyticHTTPError):
    """OpenSearch operation failed or violated its admitted contract."""


class OpenSearchUnknownOutcomeError(OpenSearchClientError):
    """An OpenSearch mutation requires target-state observation."""

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
    hits: list[dict[str, Any]]
    schema: dict[str, Any]
    native: dict[str, Any]


def _mapping(value, label='request'):
    if not isinstance(value, Mapping):
        raise OpenSearchClientError(f'{label} must be an object')
    return copy.deepcopy(dict(value))


def _name(value, label='name'):
    value = required_text(value, label, 255).casefold()
    if value in {'.', '..'} or not _NAME.fullmatch(value):
        raise OpenSearchClientError(f'{label} is invalid')
    return value


def _path_part(value, label='name'):
    return urllib.parse.quote(_name(value, label), safe='._+-')


def _opaque_path_part(value, label='name'):
    value = required_text(value, label, 255)
    if any(character in value for character in ('/', '\\', '\x00')):
        raise OpenSearchClientError(f'{label} is invalid')
    return urllib.parse.quote(value, safe='._+-')


class OpenSearchClient:
    """Provider-owned OpenSearch REST adapter with native outcomes."""

    ADMIN_OPERATIONS = {
        'cluster': frozenset({'inspect', 'alter', 'execute'}),
        'node': frozenset({'inspect'}),
        'index': frozenset({
            'inspect', 'create', 'alter', 'insert', 'update', 'delete', 'drop'
        }),
        'index-template': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'component-template': frozenset({
            'inspect', 'create', 'alter', 'drop'
        }),
        'data-stream': frozenset({'inspect', 'create', 'drop'}),
        'document': frozenset({'inspect', 'insert', 'update', 'delete'}),
        'mapping': frozenset({'inspect', 'create', 'alter'}),
        'field': frozenset({'inspect', 'create', 'alter'}),
        'analyzer': frozenset({'inspect', 'create', 'alter'}),
        'normalizer': frozenset({'inspect', 'create', 'alter'}),
        'tokenizer': frozenset({'inspect', 'create', 'alter'}),
        'ingest-pipeline': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'script': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'alias': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'repository': frozenset({'inspect', 'execute'}),
        'snapshot': frozenset({'inspect', 'execute'}),
        'user': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'role': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'role-mapping': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'tenant': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'policy': frozenset({'inspect', 'create', 'alter', 'drop'}),
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
                request, default_port=9200, default_auth='none',
                extra_fields=('database', 'index'),
            )
        except AnalyticHTTPError as exc:
            raise OpenSearchClientError(str(exc)) from exc
        if route.get('index'):
            route['index'] = _name(route['index'], 'index')
        return route

    def _request(self, route, path, **options):
        try:
            return self.transport.request(route, path, **options)
        except OpenSearchClientError:
            raise
        except AnalyticHTTPUnknownOutcomeError as exc:
            raise OpenSearchUnknownOutcomeError(str(exc)) from exc
        except AnalyticHTTPError as exc:
            raise OpenSearchClientError(
                str(exc), status=exc.status,
                native_payload=exc.native_payload,
            ) from exc

    def runtime_identity(self, request, handle=None):
        route = handle.route if isinstance(handle, _Session) else self._route(
            request
        )
        document = self._request(route, '/').json()
        if not isinstance(document, Mapping) or not isinstance(
            document.get('version'), Mapping
        ):
            raise OpenSearchClientError('OpenSearch identity is invalid')
        version = str(document['version'].get('number', ''))
        distribution = str(
            document['version'].get('distribution', 'opensearch')
        ).casefold()
        if version != REFERENCE_VERSION or distribution != 'opensearch':
            raise OpenSearchClientError(
                'runtime did not prove exact OpenSearch 3.6.0 identity'
            )
        build = (document['version'].get('build_hash') or
                 document.get('cluster_uuid') or version)
        return {
            'engine_id': 'opensearch', 'version': version,
            'build_id': f'OpenSearch {version} build {build}',
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
            raise OpenSearchClientError('OpenSearch session is unavailable')
        return {
            'native_observation': handle.last_observation,
            'request_and_bulk_item_outcomes': True,
            'multi_request_transaction_supported': False,
            'automatic_replay': False,
            'finality_interpreted_by_common_code': False,
        }

    @staticmethod
    def _source(value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise OpenSearchClientError(
                    'OpenSearch Query DSL must be a JSON object'
                ) from exc
        return _mapping(value, 'OpenSearch Query DSL')

    def execute(self, handle, request):
        if not isinstance(handle, _Session) or handle.closed:
            raise OpenSearchClientError('OpenSearch session is unavailable')
        source = self._source(request.get('source'))
        index = request.get('index') or handle.route.get('index')
        path = '/_search' if not index else f'/{
            _path_part(
                index, "index")}/_search'
        document = self._request(
            handle.route, path, method='POST', json_body=source
        ).json()
        if not isinstance(document, Mapping):
            raise OpenSearchClientError('OpenSearch result is invalid')
        hits_block = document.get('hits', {})
        hits = hits_block.get(
            'hits',
            []) if isinstance(
            hits_block,
            Mapping) else []
        if not isinstance(hits, list):
            raise OpenSearchClientError('OpenSearch hits are invalid')
        handle.last_observation = 'native-search-response-observed'
        return _Result(
            copy.deepcopy(hits[:MAX_RECORDS]),
            {'total': copy.deepcopy(hits_block.get('total')),
             'max_score': hits_block.get('max_score'),
             'took_ms': document.get('took'),
             'timed_out': document.get('timed_out')},
            copy.deepcopy(dict(document)),
        )

    @staticmethod
    def cancel(_token):
        return False

    @staticmethod
    def describe_result(token):
        if not isinstance(token, _Result):
            raise OpenSearchClientError('OpenSearch result token is invalid')
        return {
            'result_kind': 'search', 'schema': copy.deepcopy(token.schema),
            'complete': True, 'stream_reference': None,
            'payload': {'hits': copy.deepcopy(token.hits),
                        'native': copy.deepcopy(token.native)},
        }

    @staticmethod
    def _generation(value):
        return hashlib.sha256(json.dumps(
            value, sort_keys=True, default=str, separators=(',', ':')
        ).encode('utf-8')).hexdigest()[:24]

    def _resource(self, kind, name, native, path=None):
        path = path or [kind, name]
        return {
            'resource_id': 'opensearch:' + ':'.join(map(str, path)),
            'resource_kind': kind, 'display_name': str(name),
            'authority_path': ['opensearch', *map(str, path)],
            'display_path': list(map(str, path)),
            'generation': self._generation(native),
            'native': copy.deepcopy(native),
        }

    def _optional_json(self, route, path, **options):
        try:
            return self._request(route, path, **options).json()
        except OpenSearchClientError as exc:
            if exc.status in {400, 403, 404}:
                return None
            raise

    def list_resources(self, request):
        route = self._route(request)
        identity = self._request(route, '/').json()
        health = self._request(route, '/_cluster/health').json()
        resources = [self._resource(
            'cluster', identity.get('cluster_name', route['host']),
            {**identity, 'health': health},
            ['cluster', identity.get('cluster_uuid', route['host'])],
        )]
        nodes = self._optional_json(route, '/_nodes') or {}
        for node_id, node in (nodes.get('nodes', {}) or {}).items():
            resources.append(self._resource(
                'node', node.get('name', node_id),
                {'node_id': node_id, **node}, ['node', node_id]
            ))
        indices = self._optional_json(
            route,
            '/_cat/indices',
            query={
                'format': 'json',
                'expand_wildcards': 'all'}) or []
        for item in indices if isinstance(indices, list) else []:
            name = item.get('index')
            if name:
                resources.append(self._resource(
                    'index', name, item, ['index', name]
                ))
        mappings = self._optional_json(route, '/_mapping') or {}
        mapping_items = (
            mappings.items() if isinstance(mappings, Mapping) else []
        )
        for index, value in mapping_items:
            mapping = value.get(
                'mappings',
                {}) if isinstance(
                value,
                Mapping) else {}
            resources.append(self._resource(
                'mapping', index, {'index': index, 'mapping': mapping},
                ['mapping', index]
            ))
            properties = mapping.get(
                'properties', {}) if isinstance(
                mapping, Mapping) else {}
            for field, definition in properties.items():
                resources.append(self._resource(
                    'field', field,
                    {'index': index, 'name': field, 'definition': definition},
                    ['field', index, field]
                ))
        self._append_named(resources, route, 'index-template',
                           '/_index_template', 'index_templates', 'name')
        self._append_named(
            resources,
            route,
            'component-template',
            '/_component_template',
            'component_templates',
            'name')
        self._append_named(resources, route, 'data-stream',
                           '/_data_stream', 'data_streams', 'name')
        self._append_mapping(resources, route, 'ingest-pipeline',
                             '/_ingest/pipeline')
        aliases = self._optional_json(route, '/_alias') or {}
        if isinstance(aliases, Mapping):
            for index, value in aliases.items():
                definitions = value.get('aliases', {}) if isinstance(
                    value, Mapping
                ) else {}
                for alias, definition in definitions.items():
                    resources.append(self._resource(
                        'alias', alias,
                        {'name': alias, 'index': index,
                         'definition': definition},
                        ['alias', index, alias],
                    ))
        repositories = self._optional_json(route, '/_snapshot/_all') or {}
        if isinstance(repositories, Mapping):
            for repository, definition in repositories.items():
                resources.append(self._resource(
                    'repository', repository,
                    {'name': repository, 'definition': definition},
                    ['repository', repository],
                ))
                snapshots = self._optional_json(
                    route,
                    f'/_snapshot/{
                        _opaque_path_part(
                            repository,
                            "repository")}/_all',
                ) or {}
                for snapshot in snapshots.get('snapshots', []) if isinstance(
                    snapshots, Mapping
                ) else []:
                    if not isinstance(snapshot, Mapping):
                        continue
                    name = snapshot.get('snapshot')
                    if name:
                        resources.append(self._resource(
                            'snapshot', name,
                            {'name': name, 'repository': repository,
                             'definition': snapshot},
                            ['snapshot', repository, name],
                        ))
        self._append_mapping(resources, route, 'user',
                             '/_plugins/_security/api/internalusers')
        self._append_mapping(resources, route, 'role',
                             '/_plugins/_security/api/roles')
        self._append_mapping(resources, route, 'role-mapping',
                             '/_plugins/_security/api/rolesmapping')
        self._append_mapping(resources, route, 'tenant',
                             '/_plugins/_security/api/tenants')
        policies = self._optional_json(route, '/_plugins/_ism/policies') or {}
        for item in policies.get('policies', []) if isinstance(
                policies, Mapping) else []:
            policy = item.get('_id') or item.get('policy', {}).get('policy_id')
            if policy:
                resources.append(self._resource(
                    'policy', policy, item, ['policy', policy]
                ))
        return resources

    def _append_named(self, resources, route, kind, path, array_key, name_key):
        document = self._optional_json(route, path) or {}
        for item in document.get(array_key, []) if isinstance(
                document, Mapping) else []:
            name = item.get(name_key)
            if name:
                resources.append(
                    self._resource(
                        kind, name, item, [
                            kind, name]))

    def _append_mapping(self, resources, route, kind, path):
        document = self._optional_json(route, path) or {}
        if isinstance(document, Mapping):
            for name, value in document.items():
                resources.append(self._resource(
                    kind, name, {'name': name, 'definition': value},
                    [kind, name]
                ))

    def inspect_resource(self, request):
        native = self._native_target(request)
        kind = request.get('resource_kind') or native.get('resource_kind')
        name = native.get('name') or native.get('index') or kind
        if not kind or not name:
            raise OpenSearchClientError(
                'OpenSearch resource identity is absent')
        return self._resource(kind, name, native)

    def describe_security(self, request):
        route = self._route(request)
        account = self._optional_json(
            route, '/_plugins/_security/api/account'
        )
        native = {
            'authorization_model': 'opensearch-security-plugin',
            'current_account': account,
            'credential_material_exposed': False,
            'security_plugin_observable': account is not None,
        }
        return {
            'resource_id': 'opensearch:security:current',
            'display_name': 'OpenSearch security',
            'authority_path': ['opensearch', 'security', 'current'],
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
            return {'form_id': 'opensearch-inspect', 'title': 'Inspect',
                    'fields': []}
        if kind in {
                'index',
                'index-template',
                'component-template',
                'data-stream',
                'ingest-pipeline',
                'script',
                'alias',
                'user',
                'role',
                'role-mapping',
                'tenant',
                'policy'} and operation in {
                'create',
                'alter'}:
            return {'form_id': f'opensearch-{kind}-{operation}',
                    'title': f'{operation.title()} {kind}', 'fields': [
                        f('name', 'Name', required=operation == 'create'),
                        f('definition', 'Native JSON definition', 'json', True,
                          default={}),
            ]}
        if kind in {'mapping', 'field', 'analyzer', 'normalizer',
                    'tokenizer'} and operation in {'create', 'alter'}:
            return {'form_id': f'opensearch-{kind}-{operation}',
                    'title': f'{operation.title()} {kind}', 'fields': [
                        f('index', 'Index', required=True),
                        f('name', 'Name', required=kind != 'mapping'),
                        f('definition', 'Native JSON definition', 'json', True,
                          default={}),
            ]}
        if kind in {'index', 'document'} and operation in {'insert', 'update'}:
            return {'form_id': f'opensearch-document-{operation}',
                    'title': f'{operation.title()} document', 'fields': [
                        f('index', 'Index', required=True),
                        f('document_id', 'Document ID', required=False),
                        f('document', 'Document', 'json', True, default={}),
                        f('if_seq_no', 'Expected sequence number', 'number'),
                        f('if_primary_term', 'Expected primary term',
                          'number'),
            ]}
        if kind in {'index', 'document'} and operation == 'delete':
            return {'form_id': 'opensearch-document-delete',
                    'title': 'Delete document', 'fields': [
                        f('index', 'Index', required=True),
                        f('document_id', 'Document ID', required=True),
                        f('if_seq_no', 'Expected sequence number', 'number'),
                        f('if_primary_term', 'Expected primary term',
                          'number'),
                        f('acknowledge_delete', 'Confirm delete', 'boolean',
                          True, default=False),
                    ]}
        if kind == 'cluster' and operation == 'execute':
            return {'form_id': f'opensearch-{kind}-execute',
                    'title': f'Execute {kind} operation', 'fields': [
                        f('action', 'Action', 'select', True,
                          default='reroute', options=[
                              {'value': 'reroute', 'label': 'Reroute'},
                          ]),
                        f('definition', 'Native JSON definition', 'json', True,
                          default={}),
                        f('acknowledge_operation', 'Confirm operation',
                          'boolean', True, default=False),
            ]}
        if kind == 'repository' and operation == 'execute':
            return {'form_id': 'opensearch-repository-execute',
                    'title': 'Manage snapshot repository', 'fields': [
                        f('action', 'Action', 'select', True,
                          default='verify', options=[
                              {'value': 'register',
                               'label': 'Register/update'},
                              {'value': 'verify', 'label': 'Verify'},
                              {'value': 'cleanup', 'label': 'Clean up'},
                              {'value': 'delete', 'label': 'Delete'},
                          ]),
                        f('name', 'Repository name', required=True),
                        f('definition', 'Repository definition', 'json', True,
                          default={}),
                        f('acknowledge_operation', 'Confirm operation',
                          'boolean', True, default=False),
                    ]}
        if kind == 'snapshot' and operation == 'execute':
            return {'form_id': 'opensearch-snapshot-execute',
                    'title': 'Manage snapshot', 'fields': [
                        f('action', 'Action', 'select', True,
                          default='create', options=[
                              {'value': 'create', 'label': 'Create'},
                              {'value': 'restore', 'label': 'Restore'},
                              {'value': 'delete', 'label': 'Delete'},
                          ]),
                        f('repository', 'Repository', required=True),
                        f('name', 'Snapshot name', required=True),
                        f('definition', 'Snapshot options', 'json', True,
                          default={}),
                        f('acknowledge_operation', 'Confirm operation',
                          'boolean', True, default=False),
                    ]}
        if operation == 'drop':
            return {'form_id': 'opensearch-drop', 'title': 'Drop', 'fields': [
                f('acknowledge_drop', 'Confirm drop', 'boolean', True,
                  default=False),
            ]}
        return {'form_id': f'opensearch-{kind}-{operation}',
                'title': operation.title(), 'fields': [
                    f('definition', 'Native JSON definition', 'json', True,
                      default={}),
        ]}

    def visual_admin_catalog(self, catalog):
        catalog['native_planner'] = 'opensearch-rest-structured-planner'
        catalog['query_language'] = 'OpenSearch Query DSL'
        catalog['transaction_authority'] = 'opensearch-request-outcome'
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
                operation['form'] = self._form(kind, operation['operation_id'])
                if operation['operation_id'] in {'drop', 'delete', 'execute'}:
                    operation['confirmation_required'] = True
        return catalog

    def validate_admin_operation(self, request):
        errors = []
        try:
            kind, operation = request.get(
                'resource_kind'), request.get('operation_id')
            if not self.supports_admin_operation(kind, operation):
                raise OpenSearchClientError('operation is unavailable')
            draft = _mapping(request.get('draft', {}), 'draft')
            if operation == 'drop' and not draft.get('acknowledge_drop'):
                raise OpenSearchClientError('drop acknowledgement is required')
            if operation == 'delete' and not draft.get('acknowledge_delete'):
                raise OpenSearchClientError(
                    'delete acknowledgement is required')
            if operation == 'execute' and not draft.get(
                    'acknowledge_operation'):
                raise OpenSearchClientError(
                    'operation acknowledgement is required')
        except OpenSearchClientError as exc:
            errors.append({'field_id': None,
                           'code': 'opensearch_native_validation',
                           'message': str(exc)})
        return {'errors': errors, 'warnings': []}

    def plan_admin_operation(self, request):
        validation = self.validate_admin_operation(request)
        if validation['errors']:
            raise OpenSearchClientError(validation['errors'][0]['message'])
        kind, operation = request['resource_kind'], request['operation_id']
        return {
            'command_preview': {
                'provider': 'opensearch', 'resource_kind': kind,
                'operation': operation,
                'target': self._safe_target(self._native_target(
                    request.get('target_resource')
                )),
                'native_rest_request_generated_at_execution': True,
            },
            'warnings': [],
            'provider_payload': {
                'kind': kind, 'operation': operation,
                'target': self._native_target(request.get('target_resource')),
                'draft': copy.deepcopy(request.get('draft', {})),
                'route': copy.deepcopy(request.get('_provider_route')),
            },
            'receipt': {'provider': 'opensearch', 'automatic_retry': False,
                        'transaction_finality_interpreted': False},
        }

    def apply_admin_operation(self, request):
        payload = _mapping(request.get('provider_payload'), 'provider payload')
        route = self._route({'route': payload.get('route')})
        if route['read_only'] and payload.get('operation') != 'inspect':
            raise OpenSearchClientError('read-only route refused mutation')
        response = self._apply_admin(route, payload)
        return {
            'native_response_observed': True,
            'native_response': response.json(), 'http_status': response.status,
            'automatic_retry': False,
            'transaction_finality_interpreted_by_common_code': False,
        }

    def _apply_admin(self, route, payload):
        kind, operation = payload['kind'], payload['operation']
        draft, target = payload.get('draft', {}), payload.get('target', {})
        name = target.get('name') or target.get('index') or draft.get('name')
        definition = draft.get('definition', {})
        if not isinstance(definition, Mapping):
            raise OpenSearchClientError('native definition must be an object')
        if operation == 'inspect':
            return self._inspect_admin(route, kind, target)
        if kind == 'cluster' and operation == 'alter':
            return self._request(
                route, '/_cluster/settings', method='PUT',
                json_body=dict(definition), mutating=True,
            )
        prefixes = {
            'index': '',
            'index-template': '/_index_template',
            'component-template': '/_component_template',
            'data-stream': '/_data_stream',
            'ingest-pipeline': '/_ingest/pipeline',
            'script': '/_scripts',
            'user': '/_plugins/_security/api/internalusers',
            'role': '/_plugins/_security/api/roles',
            'role-mapping': '/_plugins/_security/api/rolesmapping',
            'tenant': '/_plugins/_security/api/tenants',
            'policy': '/_plugins/_ism/policies',
        }
        if kind in prefixes and operation in {'create', 'alter', 'drop'}:
            path_name = (
                _path_part(name, kind) if kind == 'index'
                else _opaque_path_part(name, kind)
            )
            path = prefixes[kind] + '/' + path_name
            if operation == 'drop':
                return self._request(
                    route, path, method='DELETE', mutating=True)
            if kind == 'index' and operation == 'alter':
                path += '/_settings'
            return self._request(
                route, path, method='PUT', json_body=dict(definition),
                mutating=True,
            )
        if kind in {'mapping', 'field', 'analyzer', 'normalizer', 'tokenizer'}:
            index = draft.get('index') or target.get('index')
            body = dict(definition)
            if kind == 'field':
                body = {
                    'properties': {
                        _name(
                            draft.get('name') or name,
                            'field'): body}}
            elif kind in {'analyzer', 'normalizer', 'tokenizer'}:
                body = {
                    'analysis': {
                        kind: {
                            _name(
                                draft.get('name') or name,
                                kind): body}}}
                return self._request(
                    route, f'/{_path_part(index, "index")}/_settings',
                    method='PUT', json_body=body, mutating=True,
                )
            return self._request(
                route, f'/{_path_part(index, "index")}/_mapping',
                method='PUT', json_body=body, mutating=True,
            )
        if kind in {'index', 'document'} and operation in {
                'insert', 'update', 'delete'}:
            index = draft.get('index') or target.get(
                'index') or route.get('index')
            document_id = draft.get('document_id') or target.get('_id')
            query = {}
            if draft.get('if_seq_no') is not None:
                query['if_seq_no'] = bounded_integer(
                    draft['if_seq_no'], 'sequence number', 0, 0, 2**63 - 1
                )
            if draft.get('if_primary_term') is not None:
                query['if_primary_term'] = bounded_integer(
                    draft['if_primary_term'], 'primary term', 1, 1, 2**63 - 1
                )
            index_path = f'/{_path_part(index, "index")}'
            quoted_document_id = (
                urllib.parse.quote(
                    required_text(document_id, 'document ID', 512), safe=''
                ) if document_id else None
            )
            base = index_path + '/_doc'
            if quoted_document_id:
                base += '/' + quoted_document_id
            if operation == 'delete':
                return self._request(
                    route, base, method='DELETE', query=query, mutating=True
                )
            document = _mapping(draft.get('document'), 'document')
            if operation == 'update':
                if not quoted_document_id:
                    raise OpenSearchClientError(
                        'document update requires a document ID'
                    )
                document = {'doc': document}
                base = index_path + '/_update/' + quoted_document_id
                method = 'POST'
            else:
                method = 'PUT' if document_id else 'POST'
            return self._request(
                route, base, method=method, query=query, json_body=document,
                mutating=True,
            )
        if kind == 'alias' and operation in {'create', 'alter', 'drop'}:
            action = ('remove' if operation == 'drop' else 'add')
            body = {'actions': [{action: dict(definition)}]}
            return self._request(
                route, '/_aliases', method='POST', json_body=body,
                mutating=True,
            )
        if kind in {'cluster', 'repository',
                    'snapshot'} and operation == 'execute':
            action = required_text(draft.get('action'), 'action', 128)
            if kind == 'cluster':
                if action != 'reroute':
                    raise OpenSearchClientError('cluster action is invalid')
                path, method = '/_cluster/reroute', 'POST'
            elif kind == 'repository':
                if action not in {'register', 'verify', 'cleanup', 'delete'}:
                    raise OpenSearchClientError('repository action is invalid')
                repository = _opaque_path_part(
                    draft.get('name') or name, 'repository'
                )
                path = '/_snapshot/' + repository
                method = 'PUT' if action == 'register' else 'POST'
                if action in {'verify', 'cleanup'}:
                    path += '/_' + action
                elif action == 'delete':
                    method = 'DELETE'
            else:
                if action not in {'create', 'restore', 'delete'}:
                    raise OpenSearchClientError('snapshot action is invalid')
                repository = _opaque_path_part(
                    draft.get('repository') or target.get('repository'),
                    'repository',
                )
                snapshot = _opaque_path_part(
                    draft.get('name') or name, 'snapshot'
                )
                path = f'/_snapshot/{repository}/{snapshot}'
                method = 'PUT' if action == 'create' else 'POST'
                if action == 'restore':
                    path += '/_restore'
                elif action == 'delete':
                    method = 'DELETE'
            return self._request(
                route, path, method=method,
                json_body=(None if method == 'DELETE' else dict(definition)),
                mutating=True,
            )
        raise OpenSearchClientError('administration operation is unavailable')

    def _inspect_admin(self, route, kind, target):
        name = target.get('name') or target.get('index')
        if kind == 'cluster':
            path = '/_cluster/health'
        elif kind == 'node':
            path = '/_nodes/' + _opaque_path_part(
                target.get('node_id') or name, 'node'
            )
        elif kind == 'index':
            path = '/' + _path_part(name, 'index')
        elif kind == 'mapping':
            path = f'/{_path_part(target.get("index") or name,
                                  "index")}/_mapping'
        elif kind in {'index-template', 'component-template', 'data-stream'}:
            prefix = {
                'index-template': '/_index_template/',
                'component-template': '/_component_template/',
                'data-stream': '/_data_stream/',
            }[kind]
            path = prefix + _opaque_path_part(name, kind)
        elif kind == 'ingest-pipeline':
            path = '/_ingest/pipeline/' + _opaque_path_part(
                name, 'ingest pipeline'
            )
        elif kind == 'repository':
            path = '/_snapshot/' + _opaque_path_part(name, 'repository')
        elif kind == 'snapshot':
            repository = _opaque_path_part(
                target.get('repository'), 'repository'
            )
            path = f'/_snapshot/{repository}/' + _opaque_path_part(
                name, 'snapshot'
            )
        elif kind in {'field', 'analyzer', 'normalizer', 'tokenizer'}:
            index = _path_part(target.get('index'), 'index')
            suffix = '_mapping' if kind == 'field' else '_settings'
            path = f'/{index}/{suffix}'
        elif kind == 'alias':
            path = '/_alias/' + _opaque_path_part(name, 'alias')
        elif kind == 'script':
            path = '/_scripts/' + _opaque_path_part(name, 'script')
        elif kind in {'user', 'role', 'role-mapping', 'tenant'}:
            prefix = {
                'user': '/_plugins/_security/api/internalusers/',
                'role': '/_plugins/_security/api/roles/',
                'role-mapping': '/_plugins/_security/api/rolesmapping/',
                'tenant': '/_plugins/_security/api/tenants/',
            }[kind]
            path = prefix + _opaque_path_part(name, kind)
        elif kind == 'policy':
            path = '/_plugins/_ism/policies/' + _opaque_path_part(
                name, 'policy'
            )
        else:
            raise OpenSearchClientError('inspection is unavailable')
        return self._request(route, path)

    def read_admin_rows(self, request):
        route = self._route({'route': request.get('_provider_route')})
        target = self._native_target(request.get('target_resource'))
        index = target.get('index') or target.get('name')
        if not index:
            raise OpenSearchClientError('index target is invalid')
        limit = bounded_integer(
            request.get('limit'), 'limit', 200, 1, MAX_PAGE_SIZE
        )
        filter_value = request.get('filter') or {'match_all': {}}
        if not isinstance(filter_value, Mapping):
            raise OpenSearchClientError('search filter must be an object')
        body = {'query': copy.deepcopy(dict(filter_value)), 'size': limit}
        sort = request.get('sort')
        if sort:
            if not isinstance(sort, list):
                raise OpenSearchClientError('search sort must be an array')
            body['sort'] = copy.deepcopy(sort)
        continuation = request.get('continuation')
        if continuation is not None:
            if not isinstance(continuation, Mapping) or not isinstance(
                continuation.get('search_after'), list
            ):
                raise OpenSearchClientError('search continuation is invalid')
            body['search_after'] = copy.deepcopy(
                continuation['search_after']
            )
        document = self._request(
            route, f'/{_path_part(index, "index")}/_search',
            method='POST', json_body=body,
        ).json()
        hits_block = document.get('hits', {}) if isinstance(
            document, Mapping
        ) else {}
        hits = hits_block.get('hits', []) if isinstance(
            hits_block, Mapping
        ) else []
        if not isinstance(hits, list):
            raise OpenSearchClientError('OpenSearch hits are invalid')
        records = []
        for hit in hits:
            if not isinstance(hit, Mapping):
                continue
            records.append({
                '_id': hit.get('_id'), '_index': hit.get('_index'),
                '_score': hit.get('_score'), '_seq_no': hit.get('_seq_no'),
                '_primary_term': hit.get('_primary_term'),
                '_source': copy.deepcopy(hit.get('_source', {})),
                'highlight': copy.deepcopy(hit.get('highlight')),
            })
        next_value = None
        if len(hits) == limit and isinstance(hits[-1], Mapping) and isinstance(
            hits[-1].get('sort'), list
        ):
            next_value = {'search_after': copy.deepcopy(hits[-1]['sort'])}
        return {
            'records': records, 'documents': records,
            'editable': not route['read_only'],
            'insertable': not route['read_only'],
            'continuation': next_value,
            'limits': {'maximum_page_size': MAX_PAGE_SIZE},
            'provider_owned_identity': True,
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
            provider = extensions.get('opensearch')
            if isinstance(provider, Mapping) and isinstance(
                provider.get('native'), Mapping
            ):
                return copy.deepcopy(dict(provider['native']))
        native = target.get('native')
        return copy.deepcopy(dict(native)) if isinstance(
            native, Mapping) else {}

    @staticmethod
    def _safe_target(target):
        return {key: copy.deepcopy(value) for key, value in target.items()
                if all(marker not in key.casefold()
                       for marker in ('password', 'secret', 'credential'))}

    def close(self):
        with self._lock:
            sessions, self._sessions = self._sessions, []
        for session in sessions:
            session.close()
