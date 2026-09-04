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
        'settings': frozenset({'inspect', 'alter'}),
        'shard': frozenset({'inspect'}),
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
        'ingest-processor': frozenset({
            'inspect', 'create', 'alter', 'drop'
        }),
        'reindex-operation': frozenset({'execute'}),
        'query-profile': frozenset({'execute'}),
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
        parameters = request.get('parameters') or {}
        if not isinstance(parameters, Mapping):
            raise OpenSearchClientError(
                'OpenSearch query parameters must be an object'
            )
        index = (
            request.get('index') or parameters.get('index') or
            handle.route.get('index')
        )
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
        source_aggregations = source.get('aggs', {})
        if isinstance(source_aggregations, Mapping) and (
                'semantic_rows' in source_aggregations):
            hits = self._semantic_aggregation_rows(
                document, parameters.get('semantic_axes', []),
                parameters.get('semantic_count_measures', []),
            )
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
    def _semantic_aggregation_rows(document, axes, count_measures):
        """Flatten only CDEadmin's reserved semantic aggregation envelope."""
        if not isinstance(axes, list) or not all(
                isinstance(item, str) for item in axes):
            raise OpenSearchClientError('semantic axes are invalid')
        if not isinstance(count_measures, list) or not all(
                isinstance(item, str) for item in count_measures):
            raise OpenSearchClientError('semantic count measures are invalid')
        aggregations = document.get('aggregations', {})
        if not isinstance(aggregations, Mapping) or (
                'semantic_rows' not in aggregations):
            raise OpenSearchClientError(
                'OpenSearch semantic aggregation response is missing'
            )
        semantic = aggregations['semantic_rows']
        if not isinstance(semantic, Mapping):
            raise OpenSearchClientError(
                'OpenSearch semantic aggregation response is invalid'
            )

        def row(value, keys):
            result = copy.deepcopy(keys)
            for name, metric in value.items():
                if name in {'key', 'key_as_string', 'doc_count', 'buckets',
                            'after_key'}:
                    continue
                if isinstance(metric, Mapping) and 'value' in metric:
                    result[str(name)] = copy.deepcopy(metric['value'])
            for name in count_measures:
                result[name] = value.get('doc_count', 0)
            return result

        buckets = semantic.get('buckets')
        if isinstance(buckets, list):
            rows = []
            for bucket in buckets:
                if not isinstance(bucket, Mapping):
                    raise OpenSearchClientError(
                        'OpenSearch semantic bucket is invalid'
                    )
                key = bucket.get('key', {})
                if not isinstance(key, Mapping):
                    key = {axes[0]: key} if len(axes) == 1 else {}
                rows.append(row(bucket, dict(key)))
            return rows
        return [row(semantic, {})]

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
        settings = self._optional_json(route, '/_settings') or {}
        if isinstance(settings, Mapping):
            for index, value in settings.items():
                if not isinstance(value, Mapping):
                    continue
                resources.append(self._resource(
                    'settings', index, {
                        'name': index, 'index': index,
                        'settings': copy.deepcopy(
                            value.get('settings', value)
                        ),
                    }, ['settings', index],
                ))
        shard_rows = self._optional_json(
            route, '/_cat/shards', query={
                'format': 'json', 'bytes': 'b',
            },
        ) or []
        shard_groups = {}
        for shard in shard_rows if isinstance(shard_rows, list) else []:
            if not isinstance(shard, Mapping) or not shard.get('index'):
                continue
            key = (str(shard['index']), str(shard.get('shard', '0')))
            shard_groups.setdefault(key, []).append(copy.deepcopy(dict(
                shard
            )))
        for (index, shard_id), copies in shard_groups.items():
            resources.append(self._resource(
                'shard', shard_id, {
                    'name': shard_id, 'index': index,
                    'shard': shard_id, 'copies': copies,
                    'primary': next((
                        item for item in copies
                        if str(item.get('prirep')).casefold() == 'p'
                    ), None),
                    'replicas': [
                        item for item in copies
                        if str(item.get('prirep')).casefold() == 'r'
                    ],
                }, ['shard', index, shard_id],
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
        pipelines = self._optional_json(route, '/_ingest/pipeline') or {}
        if isinstance(pipelines, Mapping):
            for pipeline, definition in pipelines.items():
                if not isinstance(definition, Mapping):
                    continue
                native = {
                    'name': pipeline,
                    'definition': copy.deepcopy(dict(definition)),
                }
                resources.append(self._resource(
                    'ingest-pipeline', pipeline, native,
                    ['ingest-pipeline', pipeline],
                ))
                for position, processor in enumerate(
                        definition.get('processors', [])):
                    if not isinstance(processor, Mapping) or not processor:
                        continue
                    processor_type = next(iter(processor))
                    configuration = processor.get(processor_type)
                    tag = configuration.get('tag') if isinstance(
                        configuration, Mapping
                    ) else None
                    name = str(tag or f'{position}:{processor_type}')
                    resources.append(self._resource(
                        'ingest-processor', name, {
                            'name': name, 'pipeline': pipeline,
                            'position': position,
                            'processor_type': processor_type,
                            'configuration': copy.deepcopy(configuration),
                            'pipeline_version': definition.get('version'),
                        }, ['ingest-processor', pipeline, str(position)],
                    ))
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
        resources.append(self._resource(
            'reindex-operation', 'reindex', {
                'name': 'reindex', 'workspace': True,
            }, ['reindex-operation', 'reindex'],
        ))
        resources.append(self._resource(
            'query-profile', 'query profile', {
                'name': 'query-profile', 'workspace': True,
            }, ['query-profile', 'query-profile'],
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
        if kind == 'index' and operation in {'create', 'alter'}:
            fields = [f('name', 'Index name', required=operation == 'create')]
            if operation == 'create':
                fields.extend([
                    f('number_of_shards', 'Primary shards', 'number', True,
                      default=1),
                    f('number_of_replicas', 'Replica copies', 'number', True,
                      default=1),
                    f('mappings', 'Mappings', 'json', True, default={}),
                    f('aliases', 'Aliases', 'json', True, default={}),
                ])
            else:
                fields.append(f(
                    'number_of_replicas', 'Replica copies', 'number', True,
                    default=1,
                ))
            fields.extend([
                f('refresh_interval', 'Refresh interval'),
                f('advanced_settings', 'Additional settings', 'json', True,
                  default={}),
            ])
            return {'form_id': f'opensearch-index-{operation}',
                    'title': f'{operation.title()} index', 'fields': fields}
        if kind == 'settings' and operation == 'alter':
            return {'form_id': 'opensearch-settings-alter',
                    'title': 'Alter index settings', 'fields': [
                        f('index', 'Index', required=True),
                        f('number_of_replicas', 'Replica copies', 'number'),
                        f('refresh_interval', 'Refresh interval'),
                        f('blocks_read_only', 'Read-only block', 'boolean'),
                        f('advanced_settings', 'Additional settings', 'json',
                          True, default={}),
                    ]}
        if kind == 'mapping' and operation in {'create', 'alter'}:
            return {'form_id': f'opensearch-{kind}-{operation}',
                    'title': f'{operation.title()} {kind}', 'fields': [
                        f('index', 'Index', required=True),
                        f('dynamic', 'Dynamic mapping', 'select', False,
                          options=[
                              {'value': 'true', 'label': 'Enabled'},
                              {'value': 'false', 'label': 'Disabled'},
                              {'value': 'strict', 'label': 'Strict'},
                          ]),
                        f('date_detection', 'Date detection', 'boolean'),
                        f('numeric_detection', 'Numeric detection',
                          'boolean'),
                        f('properties', 'Field definitions', 'json', True,
                          default={}),
                        f('dynamic_templates', 'Dynamic templates', 'json',
                          True, default=[]),
            ]}
        if kind == 'field' and operation in {'create', 'alter'}:
            return {'form_id': f'opensearch-field-{operation}',
                    'title': f'{operation.title()} field', 'fields': [
                        f('index', 'Index', required=True),
                        f('name', 'Field name', required=True),
                        f('field_type', 'Field type', 'select', True,
                          default='keyword', options=[
                              {'value': 'keyword', 'label': 'Keyword'},
                              {'value': 'text', 'label': 'Text'},
                              {'value': 'integer', 'label': 'Integer'},
                              {'value': 'long', 'label': 'Long'},
                              {'value': 'float', 'label': 'Float'},
                              {'value': 'double', 'label': 'Double'},
                              {'value': 'boolean', 'label': 'Boolean'},
                              {'value': 'date', 'label': 'Date'},
                              {'value': 'object', 'label': 'Object'},
                              {'value': 'nested', 'label': 'Nested'},
                              {'value': 'knn_vector', 'label': 'k-NN vector'},
                          ]),
                        f('analyzer', 'Analyzer'),
                        f('search_analyzer', 'Search analyzer'),
                        f('format', 'Date format'),
                        f('dimension', 'Vector dimension', 'number'),
                        f('indexed', 'Indexed', 'boolean', False,
                          default=True),
                        f('stored', 'Stored', 'boolean', False,
                          default=False),
                        f('advanced_definition', 'Additional field options',
                          'json', True, default={}),
                    ]}
        if kind in {'analyzer', 'normalizer', 'tokenizer'} and operation in {
                'create', 'alter'}:
            return {'form_id': f'opensearch-{kind}-{operation}',
                    'title': f'{operation.title()} {kind}', 'fields': [
                        f('index', 'Index', required=True),
                        f('name', 'Name', required=True),
                        f('type', 'Implementation type', required=True),
                        f('tokenizer', 'Tokenizer'),
                        f('filter', 'Filters', 'json', True, default=[]),
                        f('char_filter', 'Character filters', 'json', True,
                          default=[]),
                        f('advanced_definition', 'Additional options', 'json',
                          True, default={}),
                    ]}
        if kind in {'index-template', 'component-template'} and operation in {
                'create', 'alter'}:
            fields = [
                f('name', 'Template name', required=operation == 'create'),
            ]
            if kind == 'index-template':
                fields.extend([
                    f('index_patterns', 'Index patterns', 'json', True,
                      default=[]),
                    f('priority', 'Priority', 'number'),
                    f('composed_of', 'Component templates', 'json', True,
                      default=[]),
                    f('data_stream', 'Data stream template', 'boolean', False,
                      default=False),
                ])
            fields.extend([
                f('version', 'Version', 'number'),
                f('settings', 'Index settings', 'json', True, default={}),
                f('mappings', 'Mappings', 'json', True, default={}),
                f('aliases', 'Aliases', 'json', True, default={}),
                f('metadata', 'Metadata', 'json', True, default={}),
            ])
            return {'form_id': f'opensearch-{kind}-{operation}',
                    'title': f'{operation.title()} {kind}', 'fields': fields}
        if kind == 'ingest-pipeline' and operation in {'create', 'alter'}:
            return {'form_id': f'opensearch-pipeline-{operation}',
                    'title': f'{operation.title()} ingest pipeline',
                    'fields': [
                        f('name', 'Pipeline name',
                          required=operation == 'create'),
                        f('description', 'Description', 'multiline'),
                        f('version', 'Version', 'number'),
                        f('processors', 'Processors', 'json', True,
                          default=[]),
                        f('on_failure', 'Failure processors', 'json', True,
                          default=[]),
                    ]}
        if kind == 'ingest-processor' and operation in {'create', 'alter'}:
            return {'form_id': f'opensearch-processor-{operation}',
                    'title': f'{operation.title()} ingest processor',
                    'fields': [
                        f('pipeline', 'Pipeline', required=True),
                        f('position', 'Processor position', 'number',
                          required=operation == 'alter'),
                        f('processor_type', 'Processor type', required=True),
                        f('tag', 'Processor tag'),
                        f('description', 'Description'),
                        f('if_expression', 'Conditional expression', 'code'),
                        f('ignore_failure', 'Ignore failure', 'boolean', False,
                          default=False),
                        f('configuration', 'Processor configuration', 'json',
                          True, default={}),
                        f('expected_pipeline_version',
                          'Expected pipeline version', 'number'),
                    ]}
        if kind == 'alias' and operation in {'create', 'alter'}:
            return {'form_id': f'opensearch-alias-{operation}',
                    'title': f'{operation.title()} alias', 'fields': [
                        f('name', 'Alias name',
                          required=operation == 'create'),
                        f('index', 'Index or pattern', required=True),
                        f('filter', 'Filter', 'json', True, default={}),
                        f('routing', 'Routing'),
                        f('index_routing', 'Index routing'),
                        f('search_routing', 'Search routing'),
                        f('is_write_index', 'Write index', 'boolean'),
                        f('is_hidden', 'Hidden alias', 'boolean'),
                    ]}
        if kind == 'reindex-operation' and operation == 'execute':
            return {'form_id': 'opensearch-reindex-execute',
                    'title': 'Reindex documents', 'fields': [
                        f('source_index', 'Source index', required=True),
                        f('destination_index', 'Destination index',
                          required=True),
                        f('query', 'Source query', 'json', True,
                          default={'match_all': {}}),
                        f('script', 'Transform script', 'json', False,
                          default={}),
                        f('conflicts', 'Version conflicts', 'select', True,
                          default='abort', options=[
                              {'value': 'abort', 'label': 'Abort'},
                              {'value': 'proceed', 'label': 'Proceed'},
                          ]),
                        f('refresh', 'Refresh destination', 'boolean', False,
                          default=False),
                        f('wait_for_completion', 'Wait for completion',
                          'boolean', False, default=True),
                        f('slices', 'Parallel slices', 'number'),
                        f('acknowledge_operation', 'Confirm reindex',
                          'boolean', True, default=False),
                    ]}
        if kind == 'query-profile' and operation == 'execute':
            return {'form_id': 'opensearch-query-profile-execute',
                    'title': 'Profile search query', 'fields': [
                        f('index', 'Index', required=True),
                        f('query', 'Query DSL', 'json', True,
                          default={'match_all': {}}),
                        f('aggregations', 'Aggregations', 'json', False,
                          default={}),
                        f('size', 'Result size', 'number', True, default=0),
                        f('explain', 'Explain hits', 'boolean', False,
                          default=False),
                        f('acknowledge_operation', 'Run profile', 'boolean',
                          True, default=False),
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
                        f('repository_type', 'Repository type', required=True,
                          default='fs'),
                        f('location', 'Repository location'),
                        f('compress', 'Compress metadata', 'boolean', False,
                          default=True),
                        f('settings', 'Additional repository settings',
                          'json', True, default={}),
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
                        f('indices', 'Indices', 'text', False, default='*'),
                        f('include_global_state', 'Include global state',
                          'boolean', False, default=True),
                        f('ignore_unavailable', 'Ignore unavailable indices',
                          'boolean', False, default=False),
                        f('partial', 'Allow partial snapshot', 'boolean',
                          False, default=False),
                        f('rename_pattern', 'Restore rename pattern'),
                        f('rename_replacement', 'Restore rename replacement'),
                        f('wait_for_completion', 'Wait for completion',
                          'boolean', False, default=True),
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

    def search_concept_declarations(self):
        def declaration(*resource_kinds):
            return {
                'status': 'supported',
                'resource_kinds': list(resource_kinds),
                'operation_obligations': {
                    kind: sorted(self.ADMIN_OPERATIONS[kind])
                    for kind in resource_kinds
                },
                'reason': (
                    'OpenSearch search objects and operations are '
                    'provider-owned through the exact REST administration '
                    'surface.'
                ),
                'evidence': ['opensearch-3.6-native-rest-catalog'],
            }
        return {
            'indices': declaration('index'),
            'mappings': declaration('mapping'),
            'settings': declaration('settings'),
            'aliases': declaration('alias'),
            'templates': declaration(
                'index-template', 'component-template'),
            'pipelines': declaration('ingest-pipeline'),
            'shards_and_replicas': declaration('shard'),
            'reindex_operations': declaration('reindex-operation'),
            'snapshots': declaration('snapshot'),
            'ingest_processors': declaration('ingest-processor'),
            'query_profiling': declaration('query-profile'),
        }

    def visual_admin_catalog(self, catalog):
        catalog['native_planner'] = 'opensearch-rest-structured-planner'
        catalog['query_language'] = 'OpenSearch Query DSL'
        catalog['transaction_authority'] = 'opensearch-request-outcome'
        catalog['common_finality_interpretation'] = False
        catalog['experience_families'] = ['search']
        catalog['concept_declarations'] = {
            'search': self.search_concept_declarations(),
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
            if kind == 'ingest-processor' and operation == 'drop' and not (
                    draft.get('acknowledge_drop')):
                raise OpenSearchClientError('drop acknowledgement is required')
            if kind == 'reindex-operation' and operation == 'execute':
                _name(draft.get('source_index'), 'source index')
                _name(draft.get('destination_index'), 'destination index')
                if draft['source_index'].casefold() == draft[
                        'destination_index'].casefold():
                    raise OpenSearchClientError(
                        'reindex source and destination must differ'
                    )
            if kind == 'query-profile' and operation == 'execute':
                _name(draft.get('index'), 'index')
                _mapping(draft.get('query'), 'query')
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
        if route['read_only'] and not (
            payload.get('operation') == 'inspect' or
            (payload.get('kind') == 'query-profile' and
             payload.get('operation') == 'execute')
        ):
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
        definition = self._admin_definition(
            kind, operation, {**target, **draft}
        )
        if not isinstance(definition, Mapping):
            raise OpenSearchClientError('native definition must be an object')
        if operation == 'inspect':
            return self._inspect_admin(route, kind, target)
        if kind == 'cluster' and operation == 'alter':
            return self._request(
                route, '/_cluster/settings', method='PUT',
                json_body=dict(definition), mutating=True,
            )
        if kind == 'settings' and operation == 'alter':
            index = draft.get('index') or target.get('index') or name
            return self._request(
                route, f'/{_path_part(index, "index")}/_settings',
                method='PUT', json_body=dict(definition), mutating=True,
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
        if kind == 'ingest-processor' and operation in {
                'create', 'alter', 'drop'}:
            return self._mutate_ingest_processor(
                route, operation, draft, target
            )
        if kind == 'alias' and operation in {'create', 'alter', 'drop'}:
            action = ('remove' if operation == 'drop' else 'add')
            alias_definition = dict(definition)
            if operation == 'drop':
                alias_definition = {
                    'index': required_text(
                        target.get('index') or draft.get('index'),
                        'index or pattern', 255,
                    ),
                    'alias': required_text(
                        target.get('name') or draft.get('name'),
                        'alias name', 255,
                    ),
                }
            body = {'actions': [{action: alias_definition}]}
            return self._request(
                route, '/_aliases', method='POST', json_body=body,
                mutating=True,
            )
        if kind == 'reindex-operation' and operation == 'execute':
            query = {
                'refresh': str(bool(
                    draft.get('refresh', False)
                )).casefold(),
                'wait_for_completion': str(bool(
                    draft.get('wait_for_completion', True)
                )).casefold(),
            }
            if draft.get('slices') is not None:
                query['slices'] = bounded_integer(
                    draft['slices'], 'slices', 1, 1, 1024
                )
            body = {
                'source': {
                    'index': _name(draft.get('source_index'), 'source index'),
                    'query': _mapping(
                        draft.get('query') or {'match_all': {}}, 'query'
                    ),
                },
                'dest': {
                    'index': _name(
                        draft.get('destination_index'), 'destination index'
                    ),
                },
                'conflicts': draft.get('conflicts', 'abort'),
            }
            if body['conflicts'] not in {'abort', 'proceed'}:
                raise OpenSearchClientError(
                    'reindex conflicts mode is invalid'
                )
            if draft.get('script'):
                body['script'] = _mapping(draft['script'], 'script')
            return self._request(
                route, '/_reindex', method='POST', query=query,
                json_body=body, mutating=True,
            )
        if kind == 'query-profile' and operation == 'execute':
            index = _name(draft.get('index'), 'index')
            size = bounded_integer(
                draft.get('size'), 'result size', 0, 0, MAX_PAGE_SIZE
            )
            body = {
                'profile': True,
                'query': _mapping(draft.get('query'), 'query'),
                'size': size,
                'explain': bool(draft.get('explain', False)),
            }
            if draft.get('aggregations'):
                body['aggs'] = _mapping(
                    draft['aggregations'], 'aggregations'
                )
            return self._request(
                route, f'/{_path_part(index, "index")}/_search',
                method='POST', json_body=body,
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
                query=(
                    {'wait_for_completion': str(bool(
                        draft.get('wait_for_completion', True)
                    )).casefold()}
                    if kind == 'snapshot' and action in {'create', 'restore'}
                    else None
                ),
                json_body=(None if method == 'DELETE' else dict(definition)),
                mutating=True,
            )
        raise OpenSearchClientError('administration operation is unavailable')

    @staticmethod
    def _copy_present(source, target, *fields):
        for field in fields:
            if field in source and source[field] is not None:
                target[field] = copy.deepcopy(source[field])

    @classmethod
    def _admin_definition(cls, kind, operation, draft):
        if operation in {'inspect', 'drop'}:
            return {}
        legacy = draft.get('definition')
        if legacy is not None:
            return _mapping(legacy, 'native definition')
        if kind in {'index', 'settings'}:
            settings = _mapping(
                draft.get('advanced_settings', {}), 'additional settings'
            )
            cls._copy_present(
                draft, settings, 'number_of_shards', 'number_of_replicas',
                'refresh_interval',
            )
            if 'blocks_read_only' in draft and draft[
                    'blocks_read_only'] is not None:
                settings['blocks.read_only'] = bool(
                    draft['blocks_read_only']
                )
            if kind == 'settings' or operation == 'alter':
                return {'index': settings}
            definition = {'settings': settings}
            mappings = draft.get('mappings')
            aliases = draft.get('aliases')
            if mappings:
                definition['mappings'] = _mapping(mappings, 'mappings')
            if aliases:
                definition['aliases'] = _mapping(aliases, 'aliases')
            return definition
        if kind == 'mapping':
            definition = {'properties': _mapping(
                draft.get('properties', {}), 'field definitions'
            )}
            cls._copy_present(
                draft, definition, 'dynamic', 'date_detection',
                'numeric_detection', 'dynamic_templates',
            )
            if definition.get('dynamic') in {'true', 'false'}:
                definition['dynamic'] = definition['dynamic'] == 'true'
            return definition
        if kind == 'field':
            definition = _mapping(
                draft.get('advanced_definition', {}),
                'additional field options',
            )
            definition['type'] = required_text(
                draft.get('field_type'), 'field type', 128
            )
            cls._copy_present(
                draft, definition, 'analyzer', 'search_analyzer', 'format',
            )
            if 'indexed' in draft:
                definition['index'] = bool(draft['indexed'])
            if 'stored' in draft:
                definition['store'] = bool(draft['stored'])
            if draft.get('dimension') is not None:
                definition['dimension'] = bounded_integer(
                    draft['dimension'], 'vector dimension', 1, 1, 16000
                )
            return definition
        if kind in {'analyzer', 'normalizer', 'tokenizer'}:
            definition = _mapping(
                draft.get('advanced_definition', {}), 'additional options'
            )
            cls._copy_present(
                draft, definition, 'type', 'tokenizer', 'filter',
                'char_filter',
            )
            return definition
        if kind in {'index-template', 'component-template'}:
            template = {
                'settings': _mapping(draft.get('settings', {}), 'settings'),
                'mappings': _mapping(draft.get('mappings', {}), 'mappings'),
                'aliases': _mapping(draft.get('aliases', {}), 'aliases'),
            }
            definition = {'template': template}
            if kind == 'index-template':
                patterns = draft.get('index_patterns')
                if not isinstance(patterns, list) or not patterns:
                    raise OpenSearchClientError(
                        'index template requires index patterns'
                    )
                definition['index_patterns'] = copy.deepcopy(patterns)
                cls._copy_present(
                    draft, definition, 'priority', 'composed_of'
                )
                if draft.get('data_stream'):
                    definition['data_stream'] = {}
            cls._copy_present(draft, definition, 'version')
            if draft.get('metadata'):
                definition['_meta'] = _mapping(draft['metadata'], 'metadata')
            return definition
        if kind == 'ingest-pipeline':
            definition = {
                'processors': copy.deepcopy(draft.get('processors', [])),
            }
            if not isinstance(definition['processors'], list):
                raise OpenSearchClientError('processors must be an array')
            cls._copy_present(
                draft, definition, 'description', 'version'
            )
            if draft.get('on_failure'):
                definition['on_failure'] = copy.deepcopy(draft['on_failure'])
            return definition
        if kind == 'alias':
            definition = {
                'index': required_text(
                    draft.get('index'), 'index or pattern', 255
                ),
                'alias': required_text(
                    draft.get('name'), 'alias name', 255
                ),
            }
            cls._copy_present(
                draft, definition, 'filter', 'routing', 'index_routing',
                'search_routing', 'is_write_index', 'is_hidden',
            )
            return definition
        if kind == 'repository':
            settings = _mapping(draft.get('settings', {}), 'settings')
            cls._copy_present(draft, settings, 'location', 'compress')
            return {
                'type': required_text(
                    draft.get('repository_type', 'fs'), 'repository type', 128
                ),
                'settings': settings,
            }
        if kind == 'snapshot':
            definition = {}
            cls._copy_present(
                draft, definition, 'indices', 'include_global_state',
                'ignore_unavailable', 'partial', 'rename_pattern',
                'rename_replacement',
            )
            return definition
        return _mapping(draft.get('definition', {}), 'native definition')

    def _mutate_ingest_processor(self, route, operation, draft, target):
        pipeline = draft.get('pipeline') or target.get('pipeline')
        pipeline = _opaque_path_part(pipeline, 'pipeline')
        document = self._request(
            route, '/_ingest/pipeline/' + pipeline
        ).json()
        decoded_name = urllib.parse.unquote(pipeline)
        if not isinstance(document, Mapping) or not isinstance(
                document.get(decoded_name), Mapping):
            raise OpenSearchClientError('ingest pipeline was not observed')
        definition = copy.deepcopy(dict(document[decoded_name]))
        processors = definition.get('processors', [])
        if not isinstance(processors, list):
            raise OpenSearchClientError('pipeline processors are invalid')
        expected = draft.get('expected_pipeline_version')
        current = definition.get('version')
        if expected is not None and current != int(expected):
            raise OpenSearchClientError('pipeline version changed')
        position_value = draft.get('position', target.get('position'))
        position = None if position_value is None else bounded_integer(
            position_value, 'processor position', 0, 0,
            max(len(processors), 1),
        )
        if operation in {'alter', 'drop'} and (
                position is None or position >= len(processors)):
            raise OpenSearchClientError('processor position is invalid')
        if operation != 'drop':
            processor_type = required_text(
                draft.get('processor_type') or target.get('processor_type'),
                'processor type', 128,
            )
            configuration = _mapping(
                draft.get('configuration', {}), 'processor configuration'
            )
            self._copy_present(
                draft, configuration, 'tag', 'description', 'ignore_failure'
            )
            if draft.get('if_expression'):
                configuration['if'] = draft['if_expression']
            processor = {processor_type: configuration}
            if operation == 'create':
                if position is None:
                    processors.append(processor)
                else:
                    processors.insert(position, processor)
            else:
                processors[position] = processor
        else:
            processors.pop(position)
        definition['processors'] = processors
        if isinstance(current, int):
            definition['version'] = current + 1
        return self._request(
            route, '/_ingest/pipeline/' + pipeline, method='PUT',
            json_body=definition, mutating=True,
        )

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
        elif kind == 'settings':
            path = f'/{_path_part(target.get("index") or name,
                                  "index")}/_settings'
        elif kind == 'shard':
            path = '/_cat/shards/' + _path_part(
                target.get('index'), 'index'
            )
            return self._request(
                route, path, query={'format': 'json', 'bytes': 'b'}
            )
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
        elif kind == 'ingest-processor':
            path = '/_ingest/pipeline/' + _opaque_path_part(
                target.get('pipeline'), 'ingest pipeline'
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
        self.transport.close()
