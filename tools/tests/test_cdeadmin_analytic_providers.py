##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Contract tests for the live-qualified analytic providers."""

from __future__ import annotations

import json
import gzip
import sys
import unittest
import urllib.parse
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.influxdb.client import (  # noqa: E402
    InfluxDBClient,
    InfluxDBClientError,
    InfluxDBUnknownOutcomeError,
)
from pgadmin.cdeadmin.providers.influxdb.provider import (  # noqa: E402
    PROFILE as INFLUXDB,
)
from pgadmin.cdeadmin.providers.milvus.client import (  # noqa: E402
    MilvusClientAdapter,
    MilvusDependencyError,
)
from pgadmin.cdeadmin.providers.milvus.provider import (  # noqa: E402
    PROFILE as MILVUS,
)
from pgadmin.cdeadmin.providers.opensearch.client import (  # noqa: E402
    OpenSearchClient,
    OpenSearchClientError,
    OpenSearchUnknownOutcomeError,
)
from pgadmin.cdeadmin.providers.opensearch.provider import (  # noqa: E402
    PROFILE as OPENSEARCH,
)
from pgadmin.cdeadmin.providers.opensearch_sql_ppl.client import (  # noqa: E402
    OpenSearchSQLPPLClient,
    OpenSearchSQLPPLClientError,
)
from pgadmin.cdeadmin.providers.opensearch_sql_ppl.provider import (  # noqa: E402
    PROFILE as OPENSEARCH_SQL_PPL,
)
from pgadmin.cdeadmin.results.renderers import builtin_renderers  # noqa: E402
from pgadmin.cdeadmin.transports.analytics_http import (  # noqa: E402
    BoundedJSONHTTPTransport,
    normalize_http_route,
)
from pgadmin.cdeadmin.visual_admin.catalog import (  # noqa: E402
    catalog_for_engine,
)


class Response:
    status = 200
    headers = {}

    def __init__(self, document):
        self.payload = (
            document if isinstance(document, bytes)
            else json.dumps(document).encode('utf-8')
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _maximum):
        return self.payload


class AnalyticHTTPFactory:
    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout, context):
        split = urllib.parse.urlsplit(request.full_url)
        body = json.loads(request.data) if (
            request.data and request.headers.get('Content-type') ==
            'application/json'
        ) else request.data
        self.requests.append({
            'method': request.method, 'path': split.path,
            'query': urllib.parse.parse_qs(split.query), 'body': body,
            'headers': dict(request.header_items()),
            'timeout': timeout, 'context': context,
        })
        path = split.path
        if path == '/ping':
            return Response({
                'product_name': 'InfluxDB 3 Core', 'version': '3.9.0',
                'revision': 'influx-revision',
                'process_id': '00000000-0000-0000-0000-000000000001',
            })
        if path == '/api/v3/configure/database' and request.method == 'GET':
            return Response([{'name': 'metrics', 'retention_period': '30d'}])
        if path == '/api/v3/query_sql':
            source = body['q']
            if 'FROM system.databases' in source:
                return Response([{
                    'database_name': 'metrics',
                    'retention_period_ns': 2592000000000000,
                }])
            if 'FROM system.nodes' in source:
                return Response([{
                    'node_id': 'node-one', 'state': 'running',
                }])
            if 'FROM system.tables' in source:
                return Response([{
                    'database_name': 'metrics', 'table_name': 'cpu',
                    'column_count': 3, 'series_key_columns': 'host',
                    'last_cache_count': 0, 'distinct_cache_count': 0,
                }])
            if 'information_schema.tables' in source:
                return Response([{
                    'table_schema': 'metrics', 'table_name': 'cpu',
                    'table_type': 'BASE TABLE',
                }])
            if 'information_schema.columns' in source:
                return Response([
                    {
                        'table_schema': 'iox', 'table_name': 'cpu',
                        'column_name': 'host',
                        'data_type': 'Dictionary(Int32, Utf8)',
                    },
                    {
                        'table_schema': 'iox', 'table_name': 'cpu',
                        'column_name': 'usage', 'data_type': 'Float64',
                    },
                    {
                        'table_schema': 'iox', 'table_name': 'cpu',
                        'column_name': 'time',
                        'data_type': 'Timestamp(Nanosecond, None)',
                    },
                ])
            if 'FROM system.' in source:
                return Response([])
            return Response([{
                'time': '2026-09-02T12:00:00Z', 'host': 'one',
                'usage': 0.5,
            }])
        if path == '/api/v3/query_influxql':
            return Response([{'time': '2026-09-02T12:00:00Z', 'value': 1}])
        if path == '/api/v3/configure/token/named_admin':
            return Response({'token': 'SECRET-TOKEN-CANARY', 'name': 'new'})
        if path == '/':
            return Response({
                'name': 'node-one', 'cluster_name': 'qualification',
                'cluster_uuid': 'cluster-one',
                'version': {
                    'distribution': 'opensearch', 'number': '3.6.0',
                    'build_hash': 'open-revision',
                },
            })
        if path == '/_cat/plugins':
            return Response([{
                'name': 'node-one', 'component': 'opensearch-sql',
                'version': '3.6.0.0',
            }])
        if path == '/_cluster/health':
            return Response(
                {'status': 'green', 'cluster_name': 'qualification'})
        if path == '/_nodes':
            return Response({'nodes': {'node-id': {'name': 'node-one'}}})
        if path == '/_cat/indices':
            return Response([{'index': 'documents', 'health': 'green'}])
        if path == '/_mapping':
            return Response({'documents': {'mappings': {'properties': {
                'title': {'type': 'text'},
            }}}})
        if path in {
            '/_index_template', '/_component_template', '/_data_stream',
        }:
            key = {
                '/_index_template': 'index_templates',
                '/_component_template': 'component_templates',
                '/_data_stream': 'data_streams',
            }[path]
            return Response({key: []})
        if path in {
            '/_ingest/pipeline', '/_alias', '/_snapshot/_all',
            '/_plugins/_security/api/internalusers',
            '/_plugins/_security/api/roles',
            '/_plugins/_security/api/rolesmapping',
            '/_plugins/_security/api/tenants',
        }:
            return Response({})
        if path == '/_plugins/_ism/policies':
            return Response({'policies': []})
        if path.endswith('/_search'):
            return Response({
                'took': 3, 'timed_out': False,
                'hits': {'total': {'value': 1}, 'max_score': 1.0,
                         'hits': [{'_index': 'documents', '_id': '1',
                                   '_score': 1.0,
                                   '_source': {'title': 'one'}}]},
            })
        if path in {'/_plugins/_sql', '/_plugins/_ppl'}:
            return Response({
                'schema': [{'name': 'answer', 'type': 'integer'}],
                'datarows': [[42]], 'total': 1,
            })
        if path == '/_plugins/_query/_datasources':
            return Response({'datasources': [{'name': 'prometheus'}]})
        return Response({'acknowledged': True})


def influx_route(**changes):
    route = {
        'host': '127.0.0.1', 'port': 8181, 'database': 'metrics',
        'auth_kind': 'none', 'tls_mode': 'disable',
    }
    route.update(changes)
    return route


def open_route(**changes):
    route = {
        'host': '127.0.0.1', 'port': 9200,
        'auth_kind': 'none', 'tls_mode': 'disable',
    }
    route.update(changes)
    return route


class FakeMilvusClient:
    def __init__(self, **arguments):
        self.arguments = arguments
        self.calls = []
        self.closed = False

    def _record(self, name, **values):
        self.calls.append((name, values))
        return {'operation': name, **values}

    def close(self):
        self.closed = True

    def get_server_version(self):
        return 'v2.6.5'

    def list_databases(self):
        return ['default']

    def describe_database(self, db_name):
        return {'name': db_name}

    def use_database(self, db_name):
        self.arguments['db_name'] = db_name

    def list_collections(self):
        return ['vectors']

    def describe_collection(self, collection_name):
        return {
            'collection_name': collection_name,
            'fields': [{'name': 'id'}, {'name': 'embedding'}],
        }

    def get_collection_stats(self, collection_name):
        return {'collection_name': collection_name, 'row_count': 1}

    def list_partitions(self, collection_name):
        return ['_default']

    def get_partition_stats(self, collection_name, partition_name):
        return {
            'collection_name': collection_name,
            'partition_name': partition_name, 'row_count': 1,
        }

    def list_indexes(self, collection_name):
        return ['embedding_idx']

    def describe_index(self, collection_name, index_name):
        return {'collection_name': collection_name, 'index_name': index_name}

    def get_load_state(self, collection_name):
        return {'state': 'Loaded'}

    def list_aliases(self, collection_name):
        self._record('list_aliases', collection_name=collection_name)
        return {
            'aliases': ['vectors_alias'],
            'collection_name': collection_name, 'db_name': 'default',
        }

    def describe_alias(self, alias):
        return {'alias': alias, 'collection_name': 'vectors'}

    def list_users(self):
        return ['root']

    def describe_user(self, user_name):
        return {'user_name': user_name, 'roles': ['admin']}

    def list_roles(self):
        return ['admin']

    def describe_role(self, role_name):
        return {
            'role_name': role_name,
            'privileges': [{
                'object_type': 'Collection', 'object_name': '*',
                'privilege': 'DescribeCollection',
            }],
        }

    def list_resource_groups(self):
        return ['default']

    def describe_resource_group(self, name):
        return {'name': name, 'capacity': 1}

    def search(self, **arguments):
        self._record('search', **arguments)
        return [[{'id': 1, 'distance': 0.1,
                  'entity': {'title': 'one'}}]]

    def query(self, **arguments):
        self._record('query', **arguments)
        return [{'id': 1, 'title': 'one'}]

    def create_collection(self, **arguments):
        return self._record('create_collection', **arguments)

    def create_schema(self, **arguments):
        return FakeMilvusSchema(**arguments)

    def insert(self, **arguments):
        return self._record('insert', **arguments)


class FakeMilvusSchema:
    def __init__(self, **arguments):
        self.arguments = arguments
        self.fields = []

    def add_field(self, **arguments):
        self.fields.append(arguments)


class FakeDataType:
    INT64 = 'INT64'
    FLOAT_VECTOR = 'FLOAT_VECTOR'


class Lease:
    def __init__(self, value):
        self.value = bytearray(value.encode('utf-8'))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.value[:] = b'\x00' * len(self.value)

    def use(self, callback):
        return callback(memoryview(self.value))


class AnalyticProviderTests(unittest.TestCase):

    def test_influxdb_combined_profile_uses_route_default_language(self):
        self.assertEqual(
            'sql', InfluxDBClient._language(
                INFLUXDB.language_profile, 'sql'
            )
        )
        self.assertEqual(
            'influxql', InfluxDBClient._language(
                INFLUXDB.language_profile, 'influxql'
            )
        )

    def test_profiles_and_manifests_are_distinct_and_activated(self):
        profiles = [INFLUXDB, MILVUS, OPENSEARCH, OPENSEARCH_SQL_PPL]
        self.assertEqual(4, len({profile.provider_id for profile in profiles}))
        self.assertEqual(4, len({profile.profile_id for profile in profiles}))
        self.assertEqual(
            {'time_series', 'vector', 'search', 'columnar'},
            {profile.result_kind for profile in profiles},
        )
        for profile, folder in zip(profiles, (
            'influxdb', 'milvus', 'opensearch', 'opensearch_sql_ppl'
        )):
            manifest = json.loads((
                WEB / 'pgadmin/cdeadmin/providers' / folder /
                'provider_manifest.json'
            ).read_text(encoding='utf-8'))
            self.assertEqual(profile.provider_id,
                             manifest['identity']['provider_id'])
            self.assertTrue(manifest['enabled'])
            self.assertTrue(manifest['production_registration'])
            self.assertEqual('passed',
                             manifest['provenance']['activation_gate'])

    def test_production_renderers_exist_for_all_analytic_result_families(self):
        renderers = {item.renderer_id: item for item in builtin_renderers()}
        for profile in (INFLUXDB, MILVUS, OPENSEARCH, OPENSEARCH_SQL_PPL):
            renderer = renderers[profile.result_renderer_id]
            self.assertFalse(renderer.fixture_safe)
            self.assertIn(profile.result_kind, renderer.result_kinds)

    def test_influxdb_identity_queries_resources_and_native_admin(self):
        http = AnalyticHTTPFactory()
        client = InfluxDBClient(urlopen=http)
        identity = client.runtime_identity({'route': influx_route()})
        self.assertEqual('3.9.0', identity['version'])
        session = client.open_session({'route': influx_route()})
        token = client.execute(session, {
            'source': 'SELECT * FROM cpu', 'language': 'sql'
        })
        result = client.describe_result(token)
        self.assertEqual('time_series', result['result_kind'])
        self.assertEqual(0.5, result['payload']['points'][0]['usage'])
        kinds = {item['resource_kind'] for item in client.list_resources({
            'route': influx_route()
        })}
        self.assertTrue({'cluster', 'node', 'database', 'table', 'column',
                         'tag'}.issubset(kinds))
        catalog = client.visual_admin_catalog(catalog_for_engine('influxdb'))
        table = next(item for item in catalog['objects']
                     if item['resource_kind'] == 'table')
        self.assertEqual({'inspect', 'create', 'insert', 'drop'}, {
            item['operation_id'] for item in table['operations']
        })
        plan = client.plan_admin_operation({
            'resource_kind': 'database', 'operation_id': 'create',
            'draft': {'name': 'newdb'}, '_provider_route': influx_route(),
        })
        applied = client.apply_admin_operation({
            'provider_payload': plan['provider_payload']
        })
        self.assertTrue(applied['native_response_observed'])
        self.assertEqual('/api/v3/configure/database',
                         http.requests[-1]['path'])
        page = client.read_admin_rows({
            '_provider_route': influx_route(),
            'target_resource': {'native': {'table_name': 'cpu'}},
            'limit': 100,
        })
        self.assertFalse(page['editable'])
        self.assertEqual(0.5, page['records'][0]['usage'])
        token = next(item for item in catalog['objects']
                     if item['resource_kind'] == 'token')
        self.assertNotIn('create', {
            item['operation_id'] for item in token['operations']
        })
        cache_plan = client.plan_admin_operation({
            'resource_kind': 'last-cache', 'operation_id': 'create',
            'draft': {
                'database': 'metrics', 'table': 'cpu',
                'name': 'cpu_last', 'key_columns': ['host'],
                'count': 2, 'ttl': 300,
            },
            '_provider_route': influx_route(),
        })
        client.apply_admin_operation({
            'provider_payload': cache_plan['provider_payload']
        })
        self.assertEqual(
            '/api/v3/configure/last_cache', http.requests[-1]['path']
        )

    def test_influxdb_time_series_forms_and_native_operations_are_typed(self):
        http = AnalyticHTTPFactory()
        client = InfluxDBClient(urlopen=http)
        resources = client.list_resources({'route': influx_route()})
        self.assertTrue({
            'node', 'database', 'retention-policy', 'table', 'column',
            'tag', 'field', 'processing-engine',
        } <= {item['resource_kind'] for item in resources})

        catalog = client.visual_admin_catalog(catalog_for_engine('influxdb'))
        self.assertEqual(
            ['time_series', 'semantic'], catalog['experience_families']
        )
        self.assertEqual(
            6, len(catalog['concept_declarations']['time_series'])
        )
        self.assertEqual(
            6, len(catalog['concept_declarations']['semantic'])
        )
        typed_kinds = {
            'table', 'retention-policy', 'last-cache', 'distinct-cache',
            'trigger', 'plugin', 'processing-engine',
        }
        for item in catalog['objects']:
            if item['resource_kind'] not in typed_kinds:
                continue
            for operation in item['operations']:
                fields = operation['form']['fields']
                self.assertNotIn(
                    'definition', {field['field_id'] for field in fields}
                )

        inspect = client.plan_admin_operation({
            'resource_kind': 'field', 'operation_id': 'inspect',
            'target_resource': {'native': {
                'database': 'metrics', 'table': 'cpu', 'name': 'usage',
            }},
            '_provider_route': influx_route(),
        })
        client.apply_admin_operation({
            'provider_payload': inspect['provider_payload']
        })
        self.assertIn(
            "column_name = 'usage'", http.requests[-1]['body']['q']
        )

        retention = client.plan_admin_operation({
            'resource_kind': 'retention-policy', 'operation_id': 'alter',
            'target_resource': {'native': {'database': 'metrics'}},
            'draft': {'retention_period': '7d', 'retain_forever': False},
            '_provider_route': influx_route(),
        })
        client.apply_admin_operation({
            'provider_payload': retention['provider_payload']
        })
        self.assertEqual('PUT', http.requests[-1]['method'])
        self.assertEqual(
            {'db': 'metrics', 'retention_period': '7d'},
            http.requests[-1]['body'],
        )

        source = (
            'def process_writes(influxdb3_local, table_batches, args=None):\n'
            '    influxdb3_local.info("qualified")\n'
        )
        upload = client.plan_admin_operation({
            'resource_kind': 'plugin', 'operation_id': 'execute',
            'draft': {
                'action': 'upload-file', 'database': 'metrics',
                'plugin_name': 'qualified.py', 'content': source,
                'acknowledge_operation': True,
            },
            '_provider_route': influx_route(),
        })
        client.apply_admin_operation({
            'provider_payload': upload['provider_payload']
        })
        self.assertEqual('/api/v3/plugins/files', http.requests[-1]['path'])
        self.assertEqual(source.strip(), http.requests[-1]['body']['content'])

        trigger = client.plan_admin_operation({
            'resource_kind': 'trigger', 'operation_id': 'execute',
            'draft': {
                'action': 'create', 'database': 'metrics',
                'trigger_name': 'qualified',
                'plugin_filename': 'qualified.py',
                'trigger_kind': 'table', 'trigger_value': 'cpu',
                'arguments': {'mode': 'test'}, 'run_async': False,
                'error_behavior': 'log', 'disabled': True,
                'acknowledge_operation': True,
            },
            '_provider_route': influx_route(),
        })
        client.apply_admin_operation({
            'provider_payload': trigger['provider_payload']
        })
        self.assertEqual(
            'table:cpu',
            http.requests[-1]['body']['trigger_specification'],
        )

        processing = client.plan_admin_operation({
            'resource_kind': 'processing-engine', 'operation_id': 'execute',
            'draft': {
                'action': 'test-wal', 'database': 'metrics',
                'plugin_filename': 'qualified.py',
                'input_line_protocol': 'cpu,host=one usage=0.5',
                'arguments': {}, 'acknowledge_operation': True,
            },
            '_provider_route': influx_route(),
        })
        client.apply_admin_operation({
            'provider_payload': processing['provider_payload']
        })
        self.assertEqual('/api/v3/plugin_test/wal', http.requests[-1]['path'])

        invalid_table = client.validate_admin_operation({
            'resource_kind': 'table', 'operation_id': 'create',
            'draft': {
                'database': 'metrics', 'name': 'bad_table',
                'tags': ['value'],
                'fields': [{'name': 'value', 'type': 'float64'}],
            },
        })
        self.assertRegex(
            invalid_table['errors'][0]['message'], 'must not overlap'
        )

    def test_influxdb_wrong_version_and_inline_secret_fail_closed(self):
        http = AnalyticHTTPFactory()
        client = InfluxDBClient(urlopen=http)
        with self.assertRaisesRegex(InfluxDBClientError, 'inline'):
            client.open_session({'route': influx_route(token='unsafe')})
        original = http.__call__

        class Wrong:
            def __call__(self, request, timeout, context):
                if urllib.parse.urlsplit(request.full_url).path == '/ping':
                    return Response({'version': '3.9.1', 'revision': 'wrong'})
                return original(request, timeout, context)

        with self.assertRaisesRegex(InfluxDBClientError, 'exact'):
            InfluxDBClient(urlopen=Wrong()).runtime_identity({
                'route': influx_route()
            })

    def test_http_auth_read_only_and_unknown_mutation_outcome_are_safe(self):
        observed = []

        def acquire(_reference, _principal, _purpose, expected_kind):
            observed.append(expected_kind)
            return Lease('secret-canary')

        client = InfluxDBClient(
            secret_acquirer=acquire,
            urlopen=lambda *_args: (_ for _ in ()).throw(OSError('lost')),
        )
        bearer = influx_route(
            auth_kind='bearer', credential_reference_id='token-reference',
            principal_reference='user:1',
        )
        with self.assertRaises(InfluxDBClientError):
            client.runtime_identity({'route': bearer})
        self.assertEqual(['api_token'], observed)
        read_only_plan = client.plan_admin_operation({
            'resource_kind': 'database', 'operation_id': 'create',
            'draft': {'name': 'blocked'},
            '_provider_route': influx_route(read_only=True),
        })
        with self.assertRaisesRegex(InfluxDBClientError, 'read-only'):
            client.apply_admin_operation({
                'provider_payload': read_only_plan['provider_payload']
            })
        plan = client.plan_admin_operation({
            'resource_kind': 'database', 'operation_id': 'create',
            'draft': {'name': 'uncertain'},
            '_provider_route': influx_route(),
        })
        with self.assertRaises(InfluxDBUnknownOutcomeError) as raised:
            client.apply_admin_operation({
                'provider_payload': plan['provider_payload']
            })
        self.assertEqual(
            'unknown_requires_observation',
            raised.exception.outcome)
        self.assertFalse(raised.exception.retryable)

    def test_http_transport_supports_typed_api_key_and_aws_credentials(self):
        values = {
            'api': 'api-key-canary', 'aws-secret': 'aws-secret-canary',
            'aws-session': 'aws-session-canary',
        }
        observed = []

        def acquire(reference, _principal, _purpose, expected_kind):
            observed.append(expected_kind)
            return Lease(values[reference])

        requests = []

        def opener(request, _timeout, _context):
            requests.append(dict(request.header_items()))
            return Response({})

        transport = BoundedJSONHTTPTransport(acquire, urlopen=opener)
        api_route = normalize_http_route({'route': open_route(
            auth_kind='api-key', credential_reference_id='api',
            credential_kind='api_key', principal_reference='user:1',
        )}, default_port=9200)
        transport.request(api_route, '/')
        self.assertEqual(
            'ApiKey api-key-canary', requests[-1]['Authorization']
        )

        aws_route = normalize_http_route({'route': open_route(
            auth_kind='aws-sigv4', username=None,
            aws_access_key_id='AKIAEXAMPLE', aws_region='ca-central-1',
            credential_kind='cloud_secret_access_key',
            credential_reference_id='aws-secret',
            credential_references={
                'cloud_secret_access_key': 'aws-secret',
                'cloud_session_token': 'aws-session',
            },
            principal_reference='user:1',
        )}, default_port=9200)
        transport.request(aws_route, '/')
        self.assertTrue(
            requests[-1]['Authorization'].startswith('AWS4-HMAC-SHA256 ')
        )
        self.assertEqual(
            {'api_key', 'cloud_secret_access_key', 'cloud_session_token'},
            set(observed),
        )

    def test_http_transport_negotiates_bounded_gzip(self):
        payload = gzip.compress(json.dumps({'ok': True}).encode('utf-8'))

        class GzipResponse(Response):
            headers = {'Content-Encoding': 'gzip'}

        captured = {}

        def opener(request, _timeout, _context):
            captured['headers'] = dict(request.header_items())
            captured['body'] = request.data
            return GzipResponse(payload)

        route = normalize_http_route({'route': influx_route(
            http_compression='gzip'
        )}, default_port=8181, extra_fields=('database',))
        response = BoundedJSONHTTPTransport(
            urlopen=opener
        ).request(route, '/write', method='POST', json_body={'value': 42})
        self.assertEqual({'ok': True}, response.json())
        self.assertEqual('gzip', captured['headers']['Content-encoding'])
        self.assertEqual(
            {'value': 42}, json.loads(gzip.decompress(captured['body']))
        )

    def test_opensearch_identity_search_resources_and_document_admin(self):
        http = AnalyticHTTPFactory()
        client = OpenSearchClient(urlopen=http)
        identity = client.runtime_identity({'route': open_route()})
        self.assertEqual('3.6.0', identity['version'])
        session = client.open_session({'route': open_route(index='documents')})
        token = client.execute(
            session, {
                'source': {
                    'query': {
                        'match_all': {}}}})
        result = client.describe_result(token)
        self.assertEqual('search', result['result_kind'])
        self.assertEqual('1', result['payload']['hits'][0]['_id'])
        kinds = {item['resource_kind'] for item in client.list_resources({
            'route': open_route()
        })}
        self.assertTrue({'cluster', 'node', 'index',
                        'mapping', 'field'} <= kinds)
        plan = client.plan_admin_operation({
            'resource_kind': 'document', 'operation_id': 'insert',
            'draft': {'index': 'documents', 'document_id': 'two',
                      'document': {'title': 'two'}},
            '_provider_route': open_route(),
        })
        applied = client.apply_admin_operation({
            'provider_payload': plan['provider_payload']
        })
        self.assertTrue(applied['native_response_observed'])
        self.assertEqual('/documents/_doc/two', http.requests[-1]['path'])
        update = client.plan_admin_operation({
            'resource_kind': 'document', 'operation_id': 'update',
            'draft': {'index': 'documents', 'document_id': 'two',
                      'document': {'title': 'updated'}},
            '_provider_route': open_route(),
        })
        client.apply_admin_operation({
            'provider_payload': update['provider_payload']
        })
        self.assertEqual('/documents/_update/two', http.requests[-1]['path'])
        alter_index = client.plan_admin_operation({
            'resource_kind': 'index', 'operation_id': 'alter',
            'draft': {'definition': {
                'index': {'number_of_replicas': 0}
            }},
            'target_resource': {'native': {
                'name': 'documents', 'index': 'documents'
            }},
            '_provider_route': open_route(),
        })
        client.apply_admin_operation({
            'provider_payload': alter_index['provider_payload']
        })
        self.assertEqual(
            '/documents/_settings', http.requests[-1]['path']
        )
        repository = client.plan_admin_operation({
            'resource_kind': 'repository', 'operation_id': 'execute',
            'draft': {
                'action': 'register', 'name': 'Backups',
                'definition': {'type': 'fs', 'settings': {
                    'location': '/snapshots'
                }},
                'acknowledge_operation': True,
            },
            '_provider_route': open_route(),
        })
        client.apply_admin_operation({
            'provider_payload': repository['provider_payload']
        })
        self.assertEqual('/_snapshot/Backups', http.requests[-1]['path'])
        snapshot = client.plan_admin_operation({
            'resource_kind': 'snapshot', 'operation_id': 'execute',
            'draft': {
                'action': 'restore', 'repository': 'Backups',
                'name': 'Nightly', 'definition': {},
                'acknowledge_operation': True,
            },
            '_provider_route': open_route(),
        })
        client.apply_admin_operation({
            'provider_payload': snapshot['provider_payload']
        })
        self.assertEqual(
            '/_snapshot/Backups/Nightly/_restore',
            http.requests[-1]['path'],
        )
        page = client.read_admin_rows({
            '_provider_route': open_route(),
            'target_resource': {'native': {'index': 'documents'}},
            'filter': {'match_all': {}}, 'limit': 100,
        })
        self.assertEqual('1', page['records'][0]['_id'])

        failing = OpenSearchClient(
            urlopen=lambda *_args: (_ for _ in ()).throw(OSError('lost'))
        )
        with self.assertRaises(OpenSearchUnknownOutcomeError):
            failing.apply_admin_operation({
                'provider_payload': update['provider_payload']
            })

    def test_opensearch_rejects_non_json_dsl_and_wrong_distribution(self):
        client = OpenSearchClient(urlopen=AnalyticHTTPFactory())
        session = client.open_session({'route': open_route()})
        with self.assertRaisesRegex(OpenSearchClientError, 'JSON object'):
            client.execute(session, {'source': 'not-json'})

    def test_sql_ppl_requires_plugin_identity_and_normalizes_rows(self):
        http = AnalyticHTTPFactory()
        client = OpenSearchSQLPPLClient(urlopen=http)
        identity = client.runtime_identity({'route': open_route()})
        self.assertEqual('3.6.0-sql-ppl', identity['version'])
        session = client.open_session({'route': open_route(
            query_language='ppl', fetch_size=200
        )})
        token = client.execute(session, {'source': 'source=documents'})
        result = client.describe_result(token)
        self.assertEqual('columnar', result['result_kind'])
        self.assertEqual([{'answer': 42}], result['payload']['rows'])
        resources = client.list_resources({'route': open_route()})
        self.assertIn('prometheus', {
            item['display_name'] for item in resources
        })
        self.assertTrue({
            'index', 'mapping', 'reindex-operation', 'query-profile',
        } <= {item['resource_kind'] for item in resources})
        catalog = client.visual_admin_catalog(
            catalog_for_engine('opensearch_sql_ppl')
        )
        self.assertEqual(['search'], catalog['experience_families'])
        self.assertEqual(11, len(catalog['concept_declarations']['search']))

        class MissingPlugin(AnalyticHTTPFactory):
            def __call__(self, request, timeout, context):
                if urllib.parse.urlsplit(
                        request.full_url).path == '/_cat/plugins':
                    return Response([])
                return super().__call__(request, timeout, context)

        with self.assertRaisesRegex(OpenSearchSQLPPLClientError, 'exact'):
            OpenSearchSQLPPLClient(urlopen=MissingPlugin()).runtime_identity({
                'route': open_route()
            })

    def test_milvus_optional_dependency_identity_search_and_admin(self):
        created = []

        def connect(**arguments):
            client = FakeMilvusClient(**arguments)
            created.append(client)
            return client

        module = SimpleNamespace(
            MilvusClient=connect, DataType=FakeDataType
        )
        client = MilvusClientAdapter(module=module)
        identity = client.runtime_identity({'route': {
            'host': '127.0.0.1', 'port': 19530,
        }})
        self.assertEqual('2.6.5', identity['version'])
        session = client.open_session({'route': {
            'host': '127.0.0.1', 'port': 19530,
        }})
        token = client.execute(session, {'source': {
            'operation': 'search', 'collection_name': 'vectors',
            'data': [[0.1, 0.2]], 'limit': 10,
        }})
        result = client.describe_result(token)
        self.assertEqual('vector', result['result_kind'])
        self.assertEqual(0.1, result['payload']['matches'][0]['distance'])
        kinds = {item['resource_kind'] for item in client.list_resources({
            'route': {'host': '127.0.0.1', 'port': 19530}
        })}
        self.assertTrue({'cluster', 'database', 'collection', 'field',
                         'partition', 'vector-index', 'load-state', 'alias',
                         'credential', 'privilege'} <= kinds)
        page = client.read_admin_rows({
            '_provider_route': {'host': '127.0.0.1', 'port': 19530},
            'target_resource': {'native': {'collection_name': 'vectors'}},
            'filter': {'expression': ''}, 'limit': 100,
        })
        self.assertEqual('one', page['records'][0]['title'])
        client.read_admin_rows({
            '_provider_route': {'host': '127.0.0.1', 'port': 19530},
            'target_resource': {
                'resource_kind': 'partition',
                'native': {
                    'collection_name': 'vectors', 'name': '_default',
                },
            },
            'filter': {'expression': ''}, 'limit': 100,
        })
        self.assertEqual(
            ['_default'], created[-1].calls[-1][1]['partition_names']
        )
        plan = client.plan_admin_operation({
            'resource_kind': 'collection', 'operation_id': 'create',
            'draft': {'name': 'new_vectors', 'dimension': 16,
                      'primary_field_name': 'id',
                      'vector_field_name': 'vector',
                      'metric_type': 'COSINE', 'auto_id': False,
                      'enable_dynamic_field': True},
            '_provider_route': {'host': '127.0.0.1', 'port': 19530},
        })
        applied = client.apply_admin_operation({
            'provider_payload': plan['provider_payload']
        })
        self.assertEqual('create_collection',
                         applied['native_response']['operation'])
        advanced = client.plan_admin_operation({
            'resource_kind': 'collection', 'operation_id': 'create',
            'draft': {
                'name': 'advanced_vectors',
                'schema': {'fields': [
                    {'name': 'id', 'datatype': 'INT64',
                     'is_primary': True},
                    {'name': 'embedding', 'datatype': 'FLOAT_VECTOR',
                     'dim': 8},
                ]},
            },
            '_provider_route': {'host': '127.0.0.1', 'port': 19530},
        })
        client.apply_admin_operation({
            'provider_payload': advanced['provider_payload']
        })
        advanced_schema = created[-1].calls[-1][1]['schema']
        self.assertEqual(
            ['INT64', 'FLOAT_VECTOR'],
            [field['datatype'] for field in advanced_schema.fields],
        )
        with self.assertRaises(MilvusDependencyError):
            MilvusClientAdapter(module=SimpleNamespace()).open_session({
                'route': {'host': '127.0.0.1'}
            })

    def test_milvus_vector_forms_and_schema_validation_are_native(self):
        client = MilvusClientAdapter(module=SimpleNamespace(
            MilvusClient=FakeMilvusClient, DataType=FakeDataType,
        ))
        catalog = client.visual_admin_catalog(catalog_for_engine('milvus'))
        self.assertEqual(['vector'], catalog['experience_families'])
        self.assertEqual(6, len(catalog['concept_declarations']['vector']))
        for resource in catalog['objects']:
            for operation in resource['operations']:
                self.assertNotIn(
                    'properties',
                    {field['field_id']
                     for field in operation['form']['fields']},
                    f"{resource['resource_kind']}."
                    f"{operation['operation_id']} used a generic form",
                )
        create = next(
            operation for resource in catalog['objects']
            if resource['resource_kind'] == 'collection'
            for operation in resource['operations']
            if operation['operation_id'] == 'create'
        )
        self.assertIn('primary_id_type', {
            field['field_id'] for field in create['form']['fields']
        })
        field_create = next(
            operation for resource in catalog['objects']
            if resource['resource_kind'] == 'field'
            for operation in resource['operations']
            if operation['operation_id'] == 'create'
        )
        self.assertIn('default_value_json', {
            field['field_id'] for field in field_create['form']['fields']
        })

        base = {
            'resource_kind': 'collection', 'operation_id': 'create',
            '_provider_route': {'host': '127.0.0.1', 'port': 19530},
        }
        for schema in (
            {'fields': [
                {'name': 'id', 'datatype': 'INT64', 'is_primary': True},
                {'name': 'id', 'datatype': 'FLOAT_VECTOR', 'dim': 4},
            ]},
            {'fields': [
                {'name': 'id', 'datatype': 'INT64', 'is_primary': True},
            ]},
            {'fields': [
                {'name': 'id', 'datatype': 'INT64', 'is_primary': True},
                {'name': 'bits', 'datatype': 'BINARY_VECTOR', 'dim': 7},
            ]},
        ):
            result = client.validate_admin_operation({
                **base, 'draft': {'name': 'vectors', 'schema': schema},
            })
            self.assertTrue(result['errors'])


if __name__ == '__main__':
    unittest.main()
