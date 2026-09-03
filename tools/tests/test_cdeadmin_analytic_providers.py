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
            if 'information_schema.tables' in source:
                return Response([{
                    'table_schema': 'metrics', 'table_name': 'cpu',
                    'table_type': 'BASE TABLE',
                }])
            if 'information_schema.columns' in source:
                return Response([{
                    'table_schema': 'metrics', 'table_name': 'cpu',
                    'column_name': 'host', 'data_type': 'dictionary',
                }])
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

    def list_collections(self):
        return ['vectors']

    def describe_collection(self, collection_name):
        return {
            'collection_name': collection_name,
            'fields': [{'name': 'id'}, {'name': 'embedding'}],
        }

    def list_partitions(self, collection_name):
        return ['_default']

    def list_indexes(self, collection_name):
        return ['embedding_idx']

    def describe_index(self, collection_name, index_name):
        return {'collection_name': collection_name, 'index_name': index_name}

    def get_load_state(self, collection_name):
        return {'state': 'Loaded'}

    def list_aliases(self, collection_name):
        self._record('list_aliases', collection_name=collection_name)
        return ['vectors_alias']

    def list_users(self):
        return ['root']

    def list_roles(self):
        return ['admin']

    def list_resource_groups(self):
        return ['default']

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
                         'partition', 'vector-index', 'load-state'} <= kinds)
        page = client.read_admin_rows({
            '_provider_route': {'host': '127.0.0.1', 'port': 19530},
            'target_resource': {'native': {'collection_name': 'vectors'}},
            'filter': {'expression': ''}, 'limit': 100,
        })
        self.assertEqual('one', page['records'][0]['title'])
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


if __name__ == '__main__':
    unittest.main()
